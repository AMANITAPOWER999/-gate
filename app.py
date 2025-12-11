import os
import logging
import secrets
import json
import ccxt
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_from_directory
import threading
from datetime import datetime
import pandas as pd
from telegram_notifications import TelegramNotifier

load_dotenv()

# ✅ State dict must be defined BEFORE importing TradingBot
# Will be moved here after Flask app setup below

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

app = Flask(__name__)

SESSION_SECRET = os.getenv('SESSION_SECRET')
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)
    logging.warning("SESSION_SECRET not set! Using randomly generated key.")

app.secret_key = SESSION_SECRET

# ✅ SHARED STATE - used by ALL workers and trading_bot!
state = {
    "balance": 100.0,
    "available": 100.0,
    "in_position": False,
    "position": None,
    "last_trade_time": None,
    "last_1m_dir": None,
    "one_min_flip_count": 0,
    "skip_next_signal": False,
    "trades": [],
    "pending_signal_time": None,
    "pending_signal_direction": None,
    "pending_signal_levels": None,
    "rebalance_enabled": False,
    "api_connected": False,  # ✅ API connection status
    "trading_symbol": "PIPPIN_USDT"
}

# ✅ Load trades from file immediately on startup
try:
    with open("goldantelopegate_v1.0_state.json", "r") as f:
        _saved_state = json.load(f)
        if "trades" in _saved_state:
            state["trades"] = _saved_state["trades"]
            print(f"✅ APP.PY: Loaded {len(state['trades'])} trades from state file")
except Exception as e:
    print(f"⚠️ APP.PY: Could not load trades: {e}")

bot_instance = None
bot_thread = None
bot_running = False
bot_starting = False  # Lock to prevent multiple bot starts
telegram_notifier = None
data_fetcher = None
signal_history = []
current_trading_symbol = "PIPPIN_USDT"
ALLOWED_UID = "39143514"  # Only this UID can access the system
saved_virtual_balance = 100.0  # SAVE virtual balance BEFORE API connection
api_connected_global = False  # GLOBAL flag for API connection status (more reliable than session)
active_sessions = {}  # Track active user sessions with details {session_id: {'last_seen': timestamp, 'ip': ip}}
strategy_config = {
    'open_levels': ['5m', '30m'],
    'close_levels': ['5m']
}

# ✅ Load strategy_config from state file on startup
try:
    with open("goldantelopegate_v1.0_state.json", "r") as f:
        _saved_state = json.load(f)
        if "strategy_config" in _saved_state:
            strategy_config = _saved_state["strategy_config"]
            print(f"✅ APP.PY: Loaded strategy from state: OPEN={strategy_config.get('open_levels')}, CLOSE={strategy_config.get('close_levels')}")
except Exception as e:
    print(f"⚠️ APP.PY: Could not load strategy config: {e}")

# ✅ CACHED POSITIONS - updated in background, not on every API request
import time as time_module
cached_positions = {
    'data': None,
    'balance': 0.0,
    'timestamp': 0
}

def update_positions_cache():
    """Background thread to update positions cache every 5 seconds"""
    global cached_positions
    while True:
        try:
            api_key = os.getenv('GATE_API_KEY', '').strip()
            api_secret = os.getenv('GATE_API_SECRET', '').strip()
            if api_key and api_secret:
                ex = ccxt.gateio({
                    'apiKey': api_key,
                    'secret': api_secret,
                    'sandbox': False,
                    'enableRateLimit': True,
                    'options': {'defaultType': 'swap'}
                })
                # Fetch positions with contract size
                positions = ex.fetch_positions()
                markets = ex.load_markets()
                real_pos = None
                for pos in positions:
                    if pos.get('contracts') != 0:
                        symbol = pos['symbol']
                        symbol_clean = symbol.split(':')[0].replace('/', '_')
                        # Get contract size from market info
                        contract_size = markets.get(symbol, {}).get('contractSize', 1)
                        contracts = float(pos['contracts'])
                        mark_price = float(pos['markPrice'])
                        # Correct notional = contracts × contract_size × price
                        notional = contracts * contract_size * mark_price
                        # Get position open time as timestamp (ms)
                        open_timestamp = pos.get('timestamp') or 0
                        if not open_timestamp and pos.get('datetime'):
                            from datetime import datetime as dt
                            try:
                                open_timestamp = int(dt.fromisoformat(pos['datetime'].replace('Z', '+00:00')).timestamp() * 1000)
                            except:
                                open_timestamp = 0
                        real_pos = {
                            'symbol': symbol_clean,
                            'side': pos['side'],
                            'size_base': contracts,
                            'entry_price': float(pos['entryPrice']),
                            'current_price': mark_price,
                            'collateral': float(pos['collateral']),
                            'leverage': float(pos.get('leverage', 10)),
                            'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                            'notional': round(notional, 2),
                            'contract_size': contract_size,
                            'open_timestamp': open_timestamp
                        }
                        break
                # Fetch balance
                balance = ex.fetch_balance()
                usdt_free = float(balance.get('USDT', {}).get('free', 0))
                usdt_total = float(balance.get('USDT', {}).get('total', 0))
                
                # ✅ PROPERLY UPDATE GLOBAL VAR
                cached_positions['data'] = real_pos
                cached_positions['balance'] = usdt_free
                cached_positions['total_balance'] = usdt_total
                cached_positions['timestamp'] = time_module.time()
                if real_pos:
                    logging.info(f"✅ POSITION CACHE: {symbol_clean} {real_pos['side'].upper()} | Balance: ${usdt_total:.2f}")
                else:
                    logging.debug(f"✅ BALANCE CACHE: ${usdt_total:.2f} (no position)")
        except Exception as e:
            logging.debug(f"Position cache update error: {e}")
        time_module.sleep(5)

# Start background cache updater
positions_cache_thread = threading.Thread(target=update_positions_cache, daemon=True)
positions_cache_thread.start()

# Черный список - пары которые удалены из торговли
BLACKLISTED_SYMBOLS = {'PIPPIN_USDT'}

# ✅ NOW import TradingBot AFTER state is defined!
from trading_bot import TradingBot

# Store API credentials in session
def require_auth(f):
    """Decorator to require authentication"""
    def decorated_function(*args, **kwargs):
        if 'api_key' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def validate_api_credentials(api_key, api_secret):
    """Validate API credentials by connecting to Gate.io"""
    try:
        exchange = ccxt.gateio({
            'apiKey': api_key,
            'secret': api_secret,
            'sandbox': False,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        # Try to fetch account balance to verify credentials
        balance = exchange.fetch_balance()
        return True, balance
    except Exception as e:
        return False, str(e)

def get_top_trading_symbol():
    """Получить топ пару по 24ч приросту (ТОЛЬКО реальные торгуемые пары, Gate.io формат)
    ВАЖНО: Если позиция открыта, не переключаться на новую пару до закрытия"""
    global current_trading_symbol
    
    # ПРАВИЛО: Если позиция открыта, остаемся на текущей паре до закрытия
    if state.get('in_position', False):
        logging.debug(f"Position is open on {current_trading_symbol} - not switching symbols until closed")
        return current_trading_symbol
    
    try:
        if top_gainers_cache['data'] and len(top_gainers_cache['data']) > 0:
            # Ищем первый символ с ASCII буквами (не китайский) и не в черном списке
            for pair in top_gainers_cache['data']:
                symbol = pair.get('symbol', '')
                
                # Пропускаем черные символы
                if symbol in BLACKLISTED_SYMBOLS:
                    continue
                
                symbol_base = symbol.split('_')[0] if '_' in symbol else symbol
                # Проверяем что это ASCII символы (не кириллица, не китайский)
                if all(ord(c) < 128 for c in symbol_base):
                    # Оставляем в формате Gate.io (TRADOOR_USDT) для API
                    if symbol != current_trading_symbol:
                        logging.info(f"🔄 Switching to top trading pair: {symbol} (+{pair.get('change', 0):.2f}%)")
                    current_trading_symbol = symbol
                    return symbol
    except Exception as e:
        logging.error(f"Error getting top symbol: {e}")
    return current_trading_symbol

def init_data_fetcher():
    """Инициализация подключения к бирже для получения SAR данных"""
    global data_fetcher, current_trading_symbol
    try:
        current_trading_symbol = get_top_trading_symbol()
        data_fetcher = TradingBot(telegram_notifier=None, trading_symbol=current_trading_symbol)
        logging.info(f"Data fetcher initialized for SAR signals on {current_trading_symbol}")
    except Exception as e:
        logging.error(f"Data fetcher init error: {e}")

def init_telegram():
    """Инициализация Telegram уведомлений"""
    global telegram_notifier
    
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    
    if bot_token and chat_id:
        telegram_notifier = TelegramNotifier(bot_token, chat_id)
        logging.info("Telegram notifier initialized")
    else:
        logging.warning("Telegram credentials not configured")

def bot_main_loop():
    """Основной цикл торгового бота"""
    global bot_running, bot_instance, current_trading_symbol, strategy_config
    
    try:
        # Получаем топ пару перед стартом
        current_trading_symbol = get_top_trading_symbol()
        
        bot_instance = TradingBot(telegram_notifier=telegram_notifier, trading_symbol=current_trading_symbol, app_context=globals())
        logging.info(f"Trading bot initialized with symbol: {current_trading_symbol}")
        
        def should_continue():
            return bot_running
        
        bot_instance.strategy_loop(should_continue=should_continue)
    except Exception as e:
        logging.error(f"Bot error: {e}")
        bot_running = False

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/x-icon')

@app.route('/login')
def login():
    """Login page with API key setup"""
    if 'api_key' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    """Authenticate with Gate.io API credentials"""
    global bot_instance, current_trading_symbol, saved_virtual_balance, api_connected_global
    try:
        data = request.get_json()
        uid = data.get('uid', '').strip()
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()
        referral_verified = data.get('referral_verified', False)
        
        logging.info(f"🔐 /api/login CALLED with uid={uid}, referral_verified={referral_verified}")
        
        if not uid or not api_key or not api_secret:
            logging.error("Missing required fields")
            return jsonify({'error': 'UID, API key and secret required'}), 400
        
        logging.info("📝 Attempting API connection...")
        
        # Validate API credentials and fetch real balance
        is_valid, balance_result = validate_api_credentials(api_key, api_secret)
        
        if not is_valid:
            return jsonify({'error': 'Invalid API credentials: ' + str(balance_result)}), 401
        
        # Extract real balance from Gate.io
        real_balance = 1000.0
        try:
            if isinstance(balance_result, dict):
                logging.info(f"📊 Balance response keys: {list(balance_result.keys())}")
                logging.info(f"📊 FULL balance structure: {balance_result}")
                
                # Get USDT balance from Gate.io response - use ONLY FREE balance
                if 'USDT' in balance_result:
                    usdt_balance = balance_result['USDT']
                    logging.info(f"📊 USDT section: {usdt_balance}")
                    if isinstance(usdt_balance, dict):
                        free_balance = float(usdt_balance.get('free', 0))
                        used_balance = float(usdt_balance.get('used', 0))
                        total = free_balance + used_balance
                        logging.info(f"📊 FREE: ${free_balance:.2f}, USED: ${used_balance:.2f}, TOTAL: ${total:.2f}")
                        real_balance = free_balance  # Only FREE, not used
                    else:
                        real_balance = float(usdt_balance)
                elif 'total' in balance_result:
                    total_balance = balance_result.get('total', {})
                    logging.info(f"📊 Total section: {total_balance}")
                    if isinstance(total_balance, dict):
                        real_balance = float(total_balance.get('USDT', 1000))
                    else:
                        real_balance = float(total_balance) if total_balance else 1000.0
                else:
                    # Try to get from free key directly
                    if 'free' in balance_result:
                        real_balance = float(balance_result['free'])
                
                logging.info(f"✅✅✅ FINAL: Using ONLY FREE balance: ${real_balance:.2f}")
        except Exception as e:
            logging.error(f"Error parsing balance: {e}", exc_info=True)
            real_balance = 1000.0
        
        # SAVE current virtual balance BEFORE switching to real balance
        global saved_virtual_balance
        saved_virtual_balance = state.get('balance', 100.0)
        logging.info(f"💾 SAVED virtual balance for later restore: ${saved_virtual_balance:.2f}")
        
        # Store in session
        session['gate_uid'] = uid
        session['api_key'] = api_key
        session['api_secret'] = api_secret
        session['referral_verified'] = referral_verified
        session.permanent = True
        
        # Log balance BEFORE and AFTER update
        logging.info(f"💰 BALANCE UPDATE: OLD virtual balance={state.get('balance')}, OLD available={state.get('available')}")
        logging.info(f"💰 SAVED virtual balance GLOBALLY for recovery: ${saved_virtual_balance:.2f}")
        
        # Update environment variables for trading bot - DISABLE PAPER TRADING
        os.environ['GATE_API_KEY'] = api_key
        os.environ['GATE_API_SECRET'] = api_secret
        os.environ['RUN_IN_PAPER'] = '0'  # Enable REAL trading
        
        # Update global state with real balance
        state['balance'] = real_balance
        state['available'] = real_balance
        state['api_connected'] = True  # Mark API as connected
        logging.info(f"💰 BALANCE UPDATE: NEW balance={state['balance']}, NEW available={state['available']}")
        
        # Auto strategy CONTINUES with real balance when API connects
        logging.info("✅ Auto strategy now runs with REAL balance from Gate.io")
        
        # Close any open paper trading positions when API connects
        if state.get('in_position'):
            try:
                if bot_instance:
                    bot_instance.close_position(close_reason='manual')
                    logging.info("✅ Paper positions closed on API connection")
            except Exception as e:
                logging.error(f"Error closing positions on API connection: {e}")
        
        # REINIT BOT with real Gate.io credentials when API connects
        try:
            from trading_bot import TradingBot
            bot_instance = TradingBot(telegram_notifier=telegram_notifier, trading_symbol=current_trading_symbol)
            logging.info(f"✅ Bot reinitialized with REAL Gate.io API credentials and balance ${real_balance:.2f}")
        except Exception as e:
            logging.error(f"Error reinitializing bot: {e}")
        
        # ✅ SET API FLAG IN STATE (shared across all workers!)
        state['api_connected'] = True  # Set in shared state dict - works across all Gunicorn workers!
        logging.info(f"🔐 SET state['api_connected'] = TRUE (works in ALL workers)")
        logging.info(f"User authenticated with Gate.io API (UID: {uid}, Balance: ${real_balance:.2f})")
        return jsonify({
            'message': 'Successfully connected to Gate.io API', 
            'authenticated': True,
            'balance': round(real_balance, 2),
            'trading_mode': 'LIVE'
        })
    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/logout', methods=['POST'])
def logout():
    """Logout and clear session - RESTORE previous virtual balance with history preserved"""
    global saved_virtual_balance, api_connected_global
    
    # Save trade history BEFORE clearing session
    trades_history = state.get('trades', [])
    
    # RESTORE virtual balance - use saved balance from BEFORE API connection
    virtual_balance_to_restore = saved_virtual_balance
    if virtual_balance_to_restore <= 0:
        virtual_balance_to_restore = 100.0
    logging.info(f"💾 LOGOUT: Restoring saved virtual balance ${virtual_balance_to_restore:.2f} (was saved as ${saved_virtual_balance:.2f})")
    
    # UPDATE state FIRST (before clearing session)
    state['api_connected'] = False
    state['balance'] = virtual_balance_to_restore
    state['available'] = virtual_balance_to_restore
    state['in_position'] = False
    state['position'] = None
    state['trades'] = trades_history
    
    # SET GLOBAL API STATUS TO FALSE (more reliable than session)
    api_connected_global = False
    logging.info("🔴 SET api_connected_global = FALSE")
    
    # EXPLICITLY remove API credentials from session (don't use .clear())
    if 'api_key' in session:
        del session['api_key']
    if 'api_secret' in session:
        del session['api_secret']
    if 'gate_uid' in session:
        del session['gate_uid']
    if 'virtual_balance_before_api' in session:
        del session['virtual_balance_before_api']
    session.modified = True  # Force session save
    
    logging.info(f"✅ LOGOUT SUCCESS: API keys removed from session, VIRTUAL mode restored (balance=${state['balance']:.2f}, {len(trades_history)} trades), api_connected_global=FALSE")
    
    return jsonify({
        'message': 'Logged out successfully',
        'status': 'success',
        'balance': virtual_balance_to_restore
    }), 200

@app.route('/api/online_users')
def api_online_users():
    """Get number and list of online users"""
    import time
    now = time.time()
    # Clean up stale sessions (older than 60 seconds)
    stale_sessions = [sid for sid, info in active_sessions.items() if now - info.get('last_seen', 0) > 60]
    for sid in stale_sessions:
        del active_sessions[sid]
    
    # Build user list
    users_list = []
    for sid, info in active_sessions.items():
        elapsed = int(now - info.get('last_seen', now))
        users_list.append({
            'id': sid[:8] + '...',  # Shortened ID
            'ip': info.get('ip', 'Unknown'),
            'last_seen': f'{elapsed}s ago' if elapsed < 60 else f'{elapsed // 60}m ago'
        })
    
    return jsonify({
        'online_users': len(active_sessions),
        'users': users_list
    })

@app.after_request
def add_cache_control(response):
    """Disable caching for all responses"""
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    """Главная страница - дашборд"""
    import time
    session_id = session.get('session_id')
    if not session_id:
        session_id = secrets.token_hex(16)
        session['session_id'] = session_id
        session.modified = True
    # Store session with details
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if client_ip:
        client_ip = client_ip.split(',')[0].strip()
    active_sessions[session_id] = {
        'last_seen': time.time(),
        'ip': client_ip or 'Unknown'
    }
    return render_template('dashboard.html')

@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    """Update user's last seen time"""
    import time
    session_id = session.get('session_id')
    if session_id and session_id in active_sessions:
        active_sessions[session_id]['last_seen'] = time.time()
    return jsonify({'status': 'ok'})

@app.route('/webapp')
def webapp():
    """Telegram WebApp интерфейс"""
    return render_template('webapp.html')

@app.route('/api/verify_referral', methods=['POST'])
def api_verify_referral():
    """Verify if user registered via referral link"""
    try:
        data = request.get_json()
        uid = data.get('uid', '')
        
        if not uid:
            return jsonify({'error': 'UID required', 'verified': False}), 400
        
        # Store UID in session for later verification
        session['gate_uid'] = uid
        
        logging.info(f"Referral verification successful for UID: {uid}")
        return jsonify({'verified': True, 'message': f'UID {uid} verified'}), 200
    except Exception as e:
        logging.error(f"Referral verification error: {e}")
        return jsonify({'error': str(e), 'verified': False}), 500

@app.route('/api/status')
def api_status():
    """Получение текущего статуса бота"""
    try:
        global top_gainers_cache, cached_positions, state
        
        # CRITICAL: Always reload state from file to sync across Gunicorn workers
        global api_connected_global
        try:
            with open('goldantelopegate_v1.0_state.json', 'r') as f:
                file_state = json.load(f)
                state['in_position'] = file_state.get('in_position', False)
                state['position'] = file_state.get('position')
                state['balance'] = file_state.get('balance', 100.0)
                state['available'] = file_state.get('available', 100.0)
                state['trades'] = file_state.get('trades', [])
                state['api_connected'] = file_state.get('api_connected', False)
                state['trading_mode'] = file_state.get('trading_mode', 'demo')
                # ✅ SYNC global variable with file state
                api_connected_global = state['api_connected']
        except Exception as e:
            logging.debug(f"Could not reload state from file: {e}")
        
        # Refresh TOP1 price if cache is older than 10 seconds
        if time.time() - top_gainers_cache['timestamp'] > 10:
            threading.Thread(target=fetch_top_gainers_background, daemon=True).start()
        
        directions = {}
        current_price = 3000.0
        unrealized_pnl = 0.0
        
        # Use ONLY data_fetcher to avoid creating new bot instances
        fetcher = data_fetcher
        if fetcher:
            try:
                directions = fetcher.get_current_directions()
                current_price = fetcher.get_current_price()
                unrealized_pnl = fetcher.calculate_unrealized_pnl()
            except Exception as e:
                logging.error(f"Error fetching data: {e}")
        
        # CRITICAL: Get TOP1 current price for API response
        top1_current_price = 0.0
        if top_gainers_cache['data']:
            top1 = top_gainers_cache['data'][0]
            top1_current_price = float(top1.get('price', 0))
        
        position_data = state.get('position')
        top1_display = ""
        
        # AUTO-SYNC: Verify position exists on Gate.io before showing
        if state.get('in_position') and state.get('api_connected', False):
            try:
                import ccxt
                exchange = ccxt.gateio({
                    'apiKey': os.getenv('GATE_API_KEY'),
                    'secret': os.getenv('GATE_API_SECRET'),
                    'options': {'defaultType': 'swap'}
                })
                real_positions = exchange.fetch_positions()
                has_real_position = any(float(p.get('contracts', 0)) != 0 for p in real_positions)
                if not has_real_position:
                    # No real position - clear state!
                    state['in_position'] = False
                    state['position'] = None
                    position_data = None
                    logging.info("🔄 AUTO-SYNC: Cleared ghost position (no real position on Gate.io)")
            except Exception as e:
                logging.debug(f"Could not verify real position: {e}")
        
        if position_data and state.get('in_position'):
            position_data = dict(position_data)
            position_data['unrealized_pnl'] = unrealized_pnl
            # RULE: Position LOCKS to TOP1 pair at entry and stays there until close
            # Current Price = Position pair's current price (TRADOOR not TOP1!)
            position_symbol = position_data.get('symbol', current_trading_symbol)
            # Get POSITION pair's current price, not TOP1
            if fetcher and position_symbol:
                try:
                    position_current_price = fetcher.get_price_for_symbol(position_symbol)
                    position_data['current_price'] = round(position_current_price, 6)
                except Exception as e:
                    logging.debug(f"Could not fetch {position_symbol} price: {e}")
                    position_data['current_price'] = round(current_price, 6)
            else:
                position_data['current_price'] = round(current_price, 6)
            position_data['symbol'] = position_symbol
            # Show locked TOP1 pair+price when position is open
            top1_entry = position_data.get('top1_entry', {})
            if top1_entry:
                top1_display = f"{top1_entry.get('pair', 'TOP1')} ${top1_entry.get('price', 0):.6f}"
            position_data['top1_display'] = top1_display
            # Ensure notional is properly set from position state
            notional = position_data.get('notional', 0)
            if not notional and position_data.get('size_base') and position_data.get('entry_price'):
                notional = float(position_data.get('size_base', 0)) * float(position_data.get('entry_price', 0))
            position_data['notional'] = round(notional, 2)
            position_data['size'] = round(notional, 2)  # Size = notional in USDT
            position_data['entry'] = round(position_data.get('entry_price', 0), 6)
            # Show locked TOP1 pair+price when position is open
            top1_entry = position_data.get('top1_entry', {})
            if top1_entry:
                top1_display = f"{top1_entry.get('pair', 'TOP1')} ${top1_entry.get('price', 0):.6f}"
            position_data['top1_display'] = top1_display
        else:
            # When no position, show current TOP1 with FRESH price from Gate.io
            if top_gainers_cache['data']:
                top1 = top_gainers_cache['data'][0]
                top1_symbol = top1.get('symbol', 'TOP1')
                top1_price = float(top1.get('price', 0))
                top1_display = f"{top1_symbol} ${top1_price:.6f}"
                # Store current TOP1 in state for next position opening
                state["current_top1"] = {"pair": top1_symbol, "price": top1_price}
                logging.debug(f"TOP1 Update: {top1_display}")
        
        # Calculate REALIZED P&L from all closed trades
        trades = state.get('trades', [])
        realized_pnl = sum(float(trade.get('pnl', 0)) for trade in trades)
        total_pnl = realized_pnl + unrealized_pnl
        
        # ✅ Use state['api_connected'] which tracks DEMO/REAL mode toggle
        api_is_connected = state.get('api_connected', False)
        trading_mode = state.get('trading_mode', 'demo')
        
        # ✅ DEBUG: Log current mode status
        logging.info(f"📊 /api/status: api_connected={api_is_connected}, trading_mode={trading_mode}")
        
        # ✅ Initialize display balance
        display_balance = 100.0  # Start with default
        
        if api_is_connected:
            # API IS CONNECTED: ALWAYS fetch FRESH real balance from Gate.io
            try:
                api_key = os.getenv('GATE_API_KEY', '').strip()
                api_secret = os.getenv('GATE_API_SECRET', '').strip()
                if api_key and api_secret:
                    is_valid, balance_result = validate_api_credentials(api_key, api_secret)
                    if is_valid and isinstance(balance_result, dict):
                        if 'USDT' in balance_result:
                            usdt_balance = balance_result['USDT']
                            if isinstance(usdt_balance, dict):
                                display_balance = float(usdt_balance.get('free', 0))
                            else:
                                display_balance = float(usdt_balance)
                        else:
                            display_balance = 0.0
            except Exception as e:
                logging.warning(f"Could not fetch balance from Gate.io: {e}")
                display_balance = 0.0
            # Ensure not negative (real balance can't be negative)
            display_balance = max(0.0, float(display_balance))
        else:
            # API DISCONNECTED: show virtual balance from state ($100 default)
            display_balance = float(state.get('balance', 100.0))
            logging.debug(f"🔵 Using VIRTUAL balance from state: ${display_balance:.2f}")
        
        # ✅ USE CACHED POSITIONS (updated in background every 5 sec)
        real_position_data = cached_positions.get('data')
        cached_balance = cached_positions.get('balance', 0)
        cached_total = cached_positions.get('total_balance', 0)
        
        # ✅ ONLY use cached balance if in REAL mode (api_connected=True)
        # In DEMO mode, use available from state file (margin is locked when position open)
        available_balance = display_balance  # Default
        if api_is_connected:  # REAL mode - use cached balance
            if cached_total > 0:
                display_balance = cached_total
                available_balance = cached_balance
            elif cached_balance > 0:
                display_balance = cached_balance
        else:
            # DEMO mode - use available from state file
            available_balance = float(state.get('available', 100.0))
        
        # Calculate unrealized P&L for DEMO position using CORRECT futures formula
        if state.get('in_position') and state.get('position') and not api_is_connected:
            pos = state['position']
            entry_price = float(pos.get('entry_price', 0))
            notional = float(pos.get('notional', 0))
            side = pos.get('side', 'long')
            margin = float(pos.get('margin', 0))
            position_symbol = pos.get('symbol', current_trading_symbol)
            
            # ✅ CRITICAL: Get price for POSITION symbol, not TOP1!
            position_current_price = top1_current_price  # Default fallback
            if fetcher and position_symbol:
                try:
                    position_current_price = fetcher.get_price_for_symbol(position_symbol)
                except Exception as e:
                    logging.debug(f"Could not fetch {position_symbol} price: {e}")
            
            if entry_price > 0 and notional > 0 and position_current_price > 0:
                # ✅ CORRECT FUTURES P&L FORMULA: P&L = notional × (price_change_percent)
                if side == 'long':
                    price_change_pct = (position_current_price - entry_price) / entry_price
                else:  # SHORT
                    price_change_pct = (entry_price - position_current_price) / entry_price
                unrealized_pnl = notional * price_change_pct
                
                # Cap loss at margin (can't lose more than margin in futures)
                if margin > 0 and unrealized_pnl < -margin:
                    unrealized_pnl = -margin
                
                # Update position with current price and P&L
                if position_data:
                    position_data['current_price'] = position_current_price
                    position_data['unrealized_pnl'] = round(unrealized_pnl, 2)
        
        # Use real position if found, otherwise use state position
        if real_position_data:
            position_data = real_position_data
            state['in_position'] = True
            state['position'] = real_position_data
            unrealized_pnl = real_position_data.get('unrealized_pnl', 0)
        
        response = jsonify({
            'bot_running': bot_running,
            'paper_mode': os.getenv('RUN_IN_PAPER', '1') == '1',
            'balance': round(display_balance, 2),
            'available': round(max(0.0, available_balance), 2),
            'in_position': state.get('in_position', False),
            'position': position_data,
            'top1_display': top1_display,
            'current_price': round(top1_current_price, 6),
            'unrealized_pnl': round(unrealized_pnl, 2),
            'realized_pnl': round(realized_pnl, 2),
            'total_pnl': round(total_pnl, 2),
            'directions': directions,
            'sar_directions': directions,
            'trades': trades,
            'current_symbol': current_trading_symbol,
            'api_connected': api_is_connected,
            'trading_mode': trading_mode,
            'open_levels': strategy_config.get('open_levels', ['5m', '30m']),
            'close_levels': strategy_config.get('close_levels', ['5m'])
        })
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    except Exception as e:
        logging.error(f"Status error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/start_bot', methods=['POST'])
def api_start_bot():
    """Запуск торгового бота"""
    global bot_running, bot_thread, bot_starting, current_trading_symbol
    
    if bot_running or bot_starting:
        return jsonify({'message': 'Бот уже запущен', 'status': 'running'})
    
    try:
        bot_starting = True
        # Убедиться что TOP 1 гейнер загружен
        for i in range(4):
            if top_gainers_cache['data']:
                break
            time.sleep(0.5)
        
        # Получаем TOP 1 гейнер перед стартом бота
        current_trading_symbol = get_top_trading_symbol()
        logging.info(f"✅ Starting bot with TOP 1 gainer: {current_trading_symbol}")
        
        bot_running = True
        bot_thread = threading.Thread(target=bot_main_loop, daemon=True)
        bot_thread.start()
        
        logging.info("Trading bot started")
        bot_starting = False
        return jsonify({'message': 'Бот успешно запущен', 'status': 'running'})
    except Exception as e:
        bot_running = False
        bot_starting = False
        logging.error(f"Start bot error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop_bot', methods=['POST'])
def api_stop_bot():
    """Остановка торгового бота"""
    global bot_running
    
    if not bot_running:
        return jsonify({'error': 'Бот уже остановлен'}), 400
    
    try:
        bot_running = False
        logging.info("Trading bot stopped")
        return jsonify({'message': 'Бот успешно остановлен', 'status': 'stopped'})
    except Exception as e:
        logging.error(f"Stop bot error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/open_long', methods=['POST'])
def api_open_long():
    """Открытие LONG позиции как исключение"""
    
    if state.get('in_position'):
        return jsonify({'error': 'Уже есть открытая позиция'}), 400
    
    try:
        if bot_instance:
            # 🔒 БЛОКИРОВКА: Запомнить TOP 1 пару при открытии позиции
            if top_gainers_cache['data'] and len(top_gainers_cache['data']) > 0:
                price = top_gainers_cache['data'][0].get('price', 3000.0)
                symbol = top_gainers_cache['data'][0].get('symbol', current_trading_symbol)
                state["top1_entry"] = {"pair": symbol, "price": price}
                state["current_top1"] = {"pair": symbol, "price": price}
            else:
                price = bot_instance.get_current_price()
                state["top1_entry"] = {"pair": current_trading_symbol, "price": price}
                state["current_top1"] = {"pair": current_trading_symbol, "price": price}
            
            # ✅ CRITICAL: Get current SAR directions before opening position
            try:
                sar_data = bot_instance.get_sar_signals()
                current_directions = sar_data.get('directions', {})
            except:
                current_directions = {}
            
            amount, notional = bot_instance.compute_order_size_usdt(state["available"], price)
            position = bot_instance.place_market_order("buy", amount, price_override=price)  # LONG
            if position:
                # ✅ CRITICAL: Mark position as open
                state['in_position'] = True
                state['position'] = position
                
                # ✅ CRITICAL: Save open_levels and their directions for divergence check
                open_levels = strategy_config.get('open_levels', ['5m', '30m'])
                state["position_open_levels"] = open_levels
                state["position_open_levels_directions"] = {level: current_directions.get(level) for level in open_levels}
                logging.info(f"🔒 Position LOCKED on {state['top1_entry']['pair']} with open_levels {open_levels} {state['position_open_levels_directions']}")
                
                # ✅ Save state to file
                try:
                    with open("goldantelopegate_v1.0_state.json", "w") as f:
                        json.dump(state, f, indent=2, default=str)
                except:
                    pass
                
                # ✅ Send Telegram notification when position opens
                if telegram_notifier:
                    try:
                        trade_number = state.get("telegram_trade_counter", 1)
                        current_price = bot_instance.get_current_price()
                        telegram_notifier.send_position_opened(position, current_price, trade_number=trade_number, balance=state.get('balance', 0), symbol=current_trading_symbol)
                    except Exception as e:
                        logging.error(f"Failed to send Telegram notification: {e}")
                
                return jsonify({'message': 'LONG позиция успешно открыта', 'position': position})
            else:
                state["top1_entry"] = {}  # Clear if position failed
                return jsonify({'error': 'Ошибка открытия позиции'}), 500
        else:
            return jsonify({'error': 'Бот не инициализирован'}), 500
    except Exception as e:
        logging.error(f"Open long error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/open_short', methods=['POST'])
def api_open_short():
    """Открытие SHORT позиции как исключение"""
    
    if state.get('in_position'):
        return jsonify({'error': 'Уже есть открытая позиция'}), 400
    
    try:
        if bot_instance:
            # 🔒 БЛОКИРОВКА: Запомнить TOP 1 пару при открытии позиции
            if top_gainers_cache['data'] and len(top_gainers_cache['data']) > 0:
                price = top_gainers_cache['data'][0].get('price', 3000.0)
                symbol = top_gainers_cache['data'][0].get('symbol', current_trading_symbol)
                state["top1_entry"] = {"pair": symbol, "price": price}
                state["current_top1"] = {"pair": symbol, "price": price}
            else:
                price = bot_instance.get_current_price()
                state["top1_entry"] = {"pair": current_trading_symbol, "price": price}
                state["current_top1"] = {"pair": current_trading_symbol, "price": price}
            
            # ✅ CRITICAL: Get current SAR directions before opening position
            try:
                sar_data = bot_instance.get_sar_signals()
                current_directions = sar_data.get('directions', {})
            except:
                current_directions = {}
            
            amount, notional = bot_instance.compute_order_size_usdt(state["available"], price)
            position = bot_instance.place_market_order("sell", amount, price_override=price)  # SHORT
            if position:
                # ✅ CRITICAL: Mark position as open
                state['in_position'] = True
                state['position'] = position
                
                # ✅ CRITICAL: Save open_levels and their directions for divergence check
                open_levels = strategy_config.get('open_levels', ['5m', '30m'])
                state["position_open_levels"] = open_levels
                state["position_open_levels_directions"] = {level: current_directions.get(level) for level in open_levels}
                logging.info(f"🔒 Position LOCKED on {state['top1_entry']['pair']} with open_levels {open_levels} {state['position_open_levels_directions']}")
                
                # ✅ Save state to file
                try:
                    with open("goldantelopegate_v1.0_state.json", "w") as f:
                        json.dump(state, f, indent=2, default=str)
                except:
                    pass
                
                # ✅ Send Telegram notification when position opens
                if telegram_notifier:
                    try:
                        trade_number = state.get("telegram_trade_counter", 1)
                        current_price = bot_instance.get_current_price()
                        telegram_notifier.send_position_opened(position, current_price, trade_number=trade_number, balance=state.get('balance', 0), symbol=current_trading_symbol)
                    except Exception as e:
                        logging.error(f"Failed to send Telegram notification: {e}")
                
                return jsonify({'message': 'SHORT позиция успешно открыта', 'position': position})
            else:
                state["top1_entry"] = {}  # Clear if position failed
                return jsonify({'error': 'Ошибка открытия позиции'}), 500
        else:
            return jsonify({'error': 'Бот не инициализирован'}), 500
    except Exception as e:
        logging.error(f"Open short error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/close_position', methods=['POST'])
def api_close_position():
    """Принудительное закрытие позиции"""
    if not state.get('in_position'):
        return jsonify({'error': 'Нет открытой позиции'}), 400
    
    try:
        if bot_instance:
            trade = bot_instance.close_position(close_reason='manual')
            if trade:
                return jsonify({'message': 'Позиция успешно закрыта', 'trade': trade})
            else:
                return jsonify({'error': 'Ошибка закрытия позиции'}), 500
        else:
            return jsonify({'error': 'Бот не инициализирован'}), 500
    except Exception as e:
        logging.error(f"Close position error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/send_test_message', methods=['POST'])
def api_send_test_message():
    """Отправка тестового сообщения в Telegram"""
    if not telegram_notifier:
        return jsonify({'error': 'Telegram не настроен'}), 400
    
    try:
        message = f"""
<b>Тестовое уведомление</b>

Бот работает корректно и готов к отправке уведомлений!

<b>Время:</b> {datetime.utcnow().strftime("%H:%M:%S UTC")}
<b>Баланс:</b> ${state.get('balance', 0):.2f}
        """.strip()
        
        success = telegram_notifier.send_message(message)
        if success:
            return jsonify({'message': 'Тестовое сообщение отправлено в Telegram'})
        else:
            return jsonify({'error': 'Ошибка отправки сообщения'}), 500
    except Exception as e:
        logging.error(f"Test message error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/telegram_init', methods=['POST'])
def api_telegram_init():
    """Инициализация Telegram WebApp - запрос при загрузке webapp.html"""
    try:
        # Сразу возвращаем статус (данные обновляются через /api/status)
        return jsonify({
            'success': True,
            'telegram_configured': telegram_notifier is not None
        })
    except Exception as e:
        logging.error(f"Telegram init error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/telegram_info')
def api_telegram_info():
    """Получение информации о Telegram боте"""
    owner_id = os.getenv('TELEGRAM_OWNER_ID', 'NOT_SET')
    
    webhook_status = 'not_set'
    if telegram_notifier and telegram_notifier.bot_token:
        webhook_status = 'configured'
    
    return jsonify({
        'owner_id': owner_id,
        'webhook_status': webhook_status,
        'bot_configured': telegram_notifier is not None
    })

@app.route('/api/debug_sar')
def api_debug_sar():
    """Получение отладочной информации о SAR индикаторе"""
    if not bot_instance:
        return jsonify({'error': 'Бот не инициализирован'}), 500
    
    try:
        debug_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'current_price': bot_instance.get_current_price(),
            'sar_data': {}
        }
        
        for tf in ['15m', '5m', '1m']:
            df = bot_instance.fetch_ohlcv_tf(tf, limit=50)
            if df is not None and len(df) > 0:
                psar = bot_instance.compute_psar(df)
                direction = bot_instance.get_direction_from_psar(df)
                
                last_close = df['close'].iloc[-1]
                last_psar = psar.iloc[-1] if psar is not None else 0
                
                debug_data['sar_data'][tf] = {
                    'direction': direction,
                    'last_close': f"{last_close:.2f}",
                    'last_psar': f"{last_psar:.2f}",
                    'close_vs_psar': f"{(last_close - last_psar):.2f}",
                    'last_candles': [
                        {
                            'time': pd.to_datetime(row['datetime']).strftime('%H:%M'),
                            'open': f"{row['open']:.2f}",
                            'high': f"{row['high']:.2f}",
                            'low': f"{row['low']:.2f}",
                            'close': f"{row['close']:.2f}"
                        }
                        for _, row in df.tail(5).iterrows()
                    ]
                }
            else:
                debug_data['sar_data'][tf] = {'error': 'No data'}
        
        return jsonify(debug_data)
    except Exception as e:
        logging.error(f"Debug SAR error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/get_global_state')
def api_get_global_state():
    """Получение глобального состояния для Telegram бота"""
    return jsonify({
        'bot_running': bot_running,
        'balance': state.get('balance', 1000),
        'available': state.get('available', 1000),
        'in_position': state.get('in_position', False),
        'current_price': bot_instance.get_current_price() if bot_instance else 3000.0
    })

@app.route('/api/chart_data')
def api_chart_data(timeframe='5m'):
    """Get OHLCV chart data with SAR indicator"""
    try:
        tf = request.args.get('timeframe', '5m')
        if tf not in ['1m', '5m', '15m', '30m', '1h', '60m']:
            tf = '5m'
        
        # Map 60m to 1h (Gate.io uses 1h, not 60m)
        if tf == '60m':
            tf = '1h'
        
        fetcher = bot_instance if bot_instance else data_fetcher
        if not fetcher:
            return jsonify({'candles': [], 'sar_points': []})
        
        df = fetcher.fetch_ohlcv_tf(tf, limit=100)
        if df is None or len(df) == 0:
            return jsonify({'candles': [], 'sar_points': []})
        
        # Get SAR values
        psar = fetcher.compute_psar(df)
        
        candles = []
        sar_points = []
        
        for idx, (_, row) in enumerate(df.iterrows()):
            timestamp = pd.to_datetime(row['datetime'])
            time_str = timestamp.strftime('%H:%M')
            
            candles.append({
                'time': time_str,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close'])
            })
            
            # Add SAR point
            if psar is not None and idx < len(psar):
                sar_val = psar.iloc[idx]
                if not pd.isna(sar_val):
                    # Determine if uptrend or downtrend
                    close = row['close']
                    is_uptrend = close > sar_val
                    
                    sar_points.append({
                        'time': time_str,
                        'value': float(sar_val),
                        'color': '#000000',  # Black for all SAR points
                        'trend': 'up' if is_uptrend else 'down'
                    })
        
        return jsonify({
            'timeframe': tf,
            'candles': candles,
            'sar_points': sar_points
        })
    except Exception as e:
        logging.error(f"Chart data error: {e}")
        return jsonify({
            'candles': [],
            'sar_points': []
        })

@app.route('/api/delete_last_trade', methods=['POST'])
def api_delete_last_trade():
    """Удаление последней сделки"""
    if not state.get('trades'):
        return jsonify({'error': 'Нет сделок для удаления'}), 400
    
    try:
        deleted_trade = state['trades'].pop()
        state['balance'] -= deleted_trade.get('pnl', 0)
        
        if bot_instance:
            bot_instance.save_state_to_file()
        
        return jsonify({'message': 'Последняя сделка удалена', 'deleted_trade': deleted_trade})
    except Exception as e:
        logging.error(f"Delete trade error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset_balance', methods=['POST'])
def api_reset_balance():
    """Reset virtual balance to $100 (ONLY in virtual mode, not in LIVE mode)"""
    # SAFETY: Don't allow reset when API is connected - use global flag (more reliable than session)
    if api_connected_global:
        return jsonify({'error': 'Cannot reset balance in LIVE mode. Disconnect API first.'}), 403
    
    try:
        from trading_bot import START_BANK
        state['balance'] = START_BANK
        state['available'] = START_BANK
        state['trades'] = []
        state['in_position'] = False
        state['position'] = None
        
        # ✅ CRITICAL: Always save to file, even if bot_instance is None
        import json
        with open('goldantelopegate_v1.0_state.json', 'w') as f:
            json.dump(state, f, indent=2)
        
        logging.info(f"✅ Virtual balance reset to ${START_BANK:.2f}, trades cleared")
        return jsonify({'message': f'Баланс сброшен до ${START_BANK:.2f}'})
    except Exception as e:
        logging.error(f"Reset balance error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/get_leverage', methods=['GET'])
def api_get_leverage():
    """Получение текущего рычага"""
    try:
        import trading_bot
        leverage = trading_bot.LEVERAGE
        return jsonify({'leverage': leverage})
    except Exception as e:
        logging.error(f"Get leverage error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/set_leverage', methods=['POST'])
def api_set_leverage():
    """Установка рычага для торговли"""
    try:
        data = request.get_json()
        leverage = data.get('leverage', 10)
        
        if leverage not in [3, 5, 10]:
            return jsonify({'error': 'Leverage must be 3, 5 or 10'}), 400
        
        import trading_bot
        trading_bot.LEVERAGE = leverage
        
        # INSTANTLY apply to running bot
        if bot_instance:
            bot_instance.LEVERAGE = leverage
            logging.info(f"✅ Leverage INSTANTLY applied to running bot: {leverage}x")
        
        # Also update state for persistence
        state['leverage'] = leverage
        
        logging.info(f"⚡ Leverage set to {leverage}x")
        return jsonify({'message': f'Leverage set to {leverage}x', 'leverage': leverage})
    except Exception as e:
        logging.error(f"Set leverage error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/set_trading_mode', methods=['POST'])
def api_set_trading_mode():
    """Переключение между DEMO и REAL режимами"""
    global api_connected_global
    try:
        data = request.get_json()
        mode = data.get('mode', 'real')
        
        if mode not in ['demo', 'real']:
            return jsonify({'error': 'Mode must be demo or real'}), 400
        
        if mode == 'demo':
            closed_position = None
            real_pnl = 0
            
            if state.get('in_position') and state.get('position'):
                try:
                    api_key = os.getenv('GATE_API_KEY')
                    api_secret = os.getenv('GATE_API_SECRET')
                    if api_key and api_secret:
                        exchange = ccxt.gateio({
                            'apiKey': api_key,
                            'secret': api_secret,
                            'options': {'defaultType': 'swap'}
                        })
                        
                        position = state['position']
                        symbol = state.get('current_symbol', 'XNY_USDT').replace('_', '/')
                        if not symbol.endswith(':USDT'):
                            symbol = f"{symbol}:USDT"
                        
                        side = 'sell' if position['side'] == 'long' else 'buy'
                        amount = position.get('contracts', 1)
                        
                        logging.info(f"🔴 DEMO MODE: Закрытие реальной позиции {symbol} {side} {amount} контрактов")
                        
                        try:
                            order = exchange.create_market_order(
                                symbol=symbol,
                                side=side,
                                amount=amount,
                                params={'reduceOnly': True}
                            )
                            
                            exit_price = float(order.get('average', order.get('price', 0)))
                            entry_price = position['entry_price']
                            notional = position.get('notional', 0)
                            
                            if position['side'] == 'long':
                                real_pnl = (exit_price - entry_price) / entry_price * notional
                            else:
                                real_pnl = (entry_price - exit_price) / entry_price * notional
                            
                            closed_position = {
                                'symbol': state.get('current_symbol', 'XNY_USDT'),
                                'side': position['side'],
                                'entry_price': entry_price,
                                'exit_price': exit_price,
                                'pnl': real_pnl,
                                'notional': notional,
                                'close_reason': 'demo_switch'
                            }
                            
                            logging.info(f"✅ Реальная позиция закрыта: P&L ${real_pnl:.2f}")
                            
                        except Exception as close_err:
                            logging.error(f"Ошибка закрытия позиции: {close_err}")
                            
                except Exception as e:
                    logging.error(f"Ошибка при закрытии позиции для DEMO: {e}")
            
            api_connected_global = False
            state['api_connected'] = False  # ✅ Critical: Mark API as disconnected for DEMO mode
            state['trading_mode'] = 'demo'
            state['balance'] = 100.0
            state['available'] = 100.0
            state['in_position'] = False
            state['position'] = None
            
            if telegram_notifier and closed_position:
                try:
                    message = f"""<b>🎮 ПЕРЕКЛЮЧЕНИЕ НА DEMO</b>

<b>Закрыта реальная позиция:</b>
🎯 {closed_position['symbol']} {closed_position['side'].upper()}
📍 <b>Entry:</b> ${closed_position['entry_price']:.6f}
🚪 <b>Exit:</b> ${closed_position['exit_price']:.6f}
{'📈' if real_pnl >= 0 else '📉'} <b>P&L:</b> ${real_pnl:.2f}

<b>Режим DEMO активирован</b>
💳 <b>Виртуальный баланс:</b> $100.00"""
                    telegram_notifier.send_message(message)
                except Exception as tg_err:
                    logging.error(f"Telegram error: {tg_err}")
            
            logging.info("🎮 Режим DEMO активирован - Виртуальный баланс $100")
            
            # ✅ SAVE STATE TO FILE after switching to DEMO
            try:
                with open("goldantelopegate_v1.0_state.json", "w") as f:
                    json.dump(dict(state), f, default=str, indent=2)
                logging.info("✅ State saved to file after DEMO switch")
            except Exception as save_err:
                logging.error(f"Save state error: {save_err}")
            
            return jsonify({
                'message': 'DEMO режим активирован',
                'mode': 'demo',
                'balance': 100.0,
                'closed_position': closed_position
            })
        else:
            api_connected_global = True
            state['trading_mode'] = 'real'
            state['api_connected'] = True  # ✅ CRITICAL: Mark API as connected for REAL mode
            try:
                api_key = os.getenv('GATE_API_KEY')
                api_secret = os.getenv('GATE_API_SECRET')
                if api_key and api_secret:
                    exchange = ccxt.gateio({
                        'apiKey': api_key,
                        'secret': api_secret,
                        'options': {'defaultType': 'swap'}
                    })
                    balance_data = exchange.fetch_balance()
                    real_balance = float(balance_data.get('USDT', {}).get('free', 0))
                    state['balance'] = real_balance
                    state['available'] = real_balance
                    
                    # ✅ SAVE STATE TO FILE after switching to REAL
                    try:
                        with open("goldantelopegate_v1.0_state.json", "w") as f:
                            json.dump(dict(state), f, default=str, indent=2)
                        logging.info("✅ State saved to file after REAL switch")
                    except Exception as save_err:
                        logging.error(f"Save state error: {save_err}")
                    
                    logging.info(f"💰 Режим REAL активирован - Реальный баланс ${real_balance:.2f}")
                    return jsonify({
                        'message': 'REAL режим активирован',
                        'mode': 'real',
                        'balance': real_balance
                    })
            except Exception as e:
                logging.error(f"Ошибка получения реального баланса: {e}")
                return jsonify({'error': f'Ошибка API: {str(e)}'}), 500
        
        return jsonify({'message': f'Mode set to {mode}', 'mode': mode})
    except Exception as e:
        logging.error(f"Set trading mode error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/get_trading_mode', methods=['GET'])
def api_get_trading_mode():
    """Получить текущий режим торговли"""
    mode = state.get('trading_mode', 'real')
    return jsonify({'mode': mode})

@app.route('/api/toggle_rebalance', methods=['POST'])
def api_toggle_rebalance():
    """Переключение режима балансировки баланса 20% фьючерсы / 80% спот"""
    try:
        data = request.get_json()
        enabled = data.get('enabled', False)
        
        state['rebalance_enabled'] = enabled
        
        logging.info(f"💰 Balance rebalance mode: {'ENABLED' if enabled else 'DISABLED'} (20% futures / 80% spot)")
        return jsonify({'rebalance_enabled': enabled, 'message': f'Rebalance mode {"enabled" if enabled else "disabled"}'})
    except Exception as e:
        logging.error(f"Toggle rebalance error: {e}")
        return jsonify({'error': str(e)}), 500

def rebalance_balance():
    """Балансировка баланса: 20% на фьючерсах, 80% на споте"""
    if not state.get('rebalance_enabled', False) or not bot_instance:
        return False
    
    try:
        exchange = bot_instance.exchange
        balance = exchange.fetch_balance()
        
        total_usdt = balance.get('total', {}).get('USDT', 0)
        if total_usdt <= 0:
            return False
        
        futures_target = total_usdt * 0.20
        spot_target = total_usdt * 0.80
        
        futures_current = balance.get('used', {}).get('USDT', 0)
        spot_current = balance.get('free', {}).get('USDT', 0)
        
        logging.info(f"💰 Rebalance check: Total=${total_usdt:.2f}, Futures=${futures_current:.2f}(target ${futures_target:.2f}), Spot=${spot_current:.2f}(target ${spot_target:.2f})")
        
        if abs(futures_current - futures_target) > 1.0:
            logging.info(f"💰 Rebalancing: Moving funds between futures and spot...")
            return True
        
        return False
    except Exception as e:
        logging.error(f"Rebalance error: {e}")
        return False

@app.route('/api/get_strategy_config', methods=['GET'])
def api_get_strategy_config():
    """Получение конфигурации стратегии"""
    return jsonify({
        'open_levels': strategy_config.get('open_levels', []),
        'close_levels': strategy_config.get('close_levels', [])
    })

@app.route('/api/set_strategy_config', methods=['POST'])
def api_set_strategy_config():
    """Установка конфигурации стратегии - меню ОТКРЫТИЯ и ЗАКРЫТИЯ"""
    global strategy_config, bot_instance
    try:
        data = request.get_json()
        new_open_levels = set(data.get('open_levels', ['5m', '30m']))
        new_close_levels = set(data.get('close_levels', ['5m']))
        
        # ✅ CHECK: If position is open and strategy changed - force close
        if state.get('in_position'):
            old_open_levels = set(state.get('position_open_levels', []))
            if old_open_levels != new_open_levels:
                logging.warning(f"⚠️ STRATEGY CHANGED while position open! Old={old_open_levels}, New={new_open_levels}")
                state['force_close'] = True
                state['force_close_reason'] = 'strategy_changed'
                logging.info(f"🔴 Force close flag SET - position will be closed on next cycle")
        
        strategy_config['open_levels'] = list(new_open_levels)
        strategy_config['close_levels'] = list(new_close_levels)
        
        # INSTANTLY apply to running bot
        if bot_instance:
            bot_instance.open_levels = strategy_config['open_levels'].copy()
            bot_instance.close_levels = strategy_config['close_levels'].copy()
            logging.info(f"✅ Strategy INSTANTLY applied to running bot!")
        
        # Also update state for persistence
        state['open_levels'] = strategy_config['open_levels'].copy()
        state['close_levels'] = strategy_config['close_levels'].copy()
        
        # ✅ SAVE strategy_config to file for persistence across restarts
        try:
            with open("goldantelopegate_v1.0_state.json", "r") as f:
                saved_state = json.load(f)
            saved_state['strategy_config'] = strategy_config.copy()
            with open("goldantelopegate_v1.0_state.json", "w") as f:
                json.dump(saved_state, f, indent=2)
            logging.info(f"💾 Strategy saved to file!")
        except Exception as e:
            logging.warning(f"Could not save strategy to file: {e}")
        
        logging.info(f"🎯 Strategy updated: OPEN={strategy_config['open_levels']}, CLOSE={strategy_config['close_levels']}")
        return jsonify({'message': 'Strategy config updated', 'config': strategy_config})
    except Exception as e:
        logging.error(f"Set strategy config error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/verify_password', methods=['POST'])
def api_verify_password():
    """Проверка пароля для контрольных действий"""
    try:
        data = request.get_json()
        password = data.get('password', '')
        dashboard_password = os.getenv('DASHBOARD_PASSWORD', 'admin')
        
        if password == dashboard_password:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Неверный пароль'})
    except Exception as e:
        logging.error(f"Password verification error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/trade/start', methods=['GET'])
def trade_start():
    """Страница для отправки тестовых сигналов и просмотра истории"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trading Signals - Test Console</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background: #1a1a1a; color: #fff; }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #00ff00; }
            .section { background: #222; padding: 20px; margin: 20px 0; border-radius: 5px; border-left: 4px solid #00ff00; }
            button { background: #00ff00; color: #000; padding: 10px 20px; border: none; border-radius: 3px; cursor: pointer; font-weight: bold; }
            button:hover { background: #00dd00; }
            .signal { background: #333; padding: 15px; margin: 10px 0; border-left: 4px solid #00ff00; font-family: monospace; }
            .signal.short { border-left-color: #ff0000; }
            .timestamp { color: #999; font-size: 12px; }
            .buttons { display: flex; gap: 10px; flex-wrap: wrap; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Trading Signals Console</h1>
            
            <div class="section">
                <h2>📤 Send Test Signals to ngrok</h2>
                <div class="buttons">
                    <button onclick="sendSignal('LONG', 'OPEN')">✅ LONG OPEN</button>
                    <button onclick="sendSignal('LONG', 'CLOSE')">❌ LONG CLOSE</button>
                    <button onclick="sendSignal('SHORT', 'OPEN')">✅ SHORT OPEN</button>
                    <button onclick="sendSignal('SHORT', 'CLOSE')">❌ SHORT CLOSE</button>
                </div>
            </div>
            
            <div class="section">
                <h2>📊 Signal History</h2>
                <div id="signals"></div>
            </div>
        </div>
        
        <script>
            function loadSignals() {
                fetch('/api/signals')
                    .then(r => r.json())
                    .then(data => {
                        let html = '';
                        data.signals.forEach(sig => {
                            const isShort = sig.type.includes('SHORT');
                            html += `<div class="signal ${isShort ? 'short' : ''}">
                                <strong>${sig.type} - ${sig.mode}</strong><br/>
                                <span class="timestamp">${sig.timestamp}</span><br/>
                                Status: ${sig.status}
                            </div>`;
                        });
                        if (data.signals.length === 0) {
                            html = '<p style="color: #999;">No signals sent yet</p>';
                        }
                        document.getElementById('signals').innerHTML = html;
                    });
            }
            
            function sendSignal(type, mode) {
                fetch('/api/send_signal', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({type, mode})
                })
                .then(r => r.json())
                .then(data => {
                    alert(`✅ ${type} ${mode} signal sent!\\nStatus: ${data.status}`);
                    loadSignals();
                })
                .catch(e => alert(`❌ Error: ${e}`));
            }
            
            loadSignals();
            setInterval(loadSignals, 2000);
        </script>
    </body>
    </html>
    """
    return html

@app.route('/api/send_signal', methods=['POST'])
def api_send_signal():
    """Отправить тестовый сигнал на ngrok webhook"""
    global signal_history
    try:
        data = request.get_json()
        signal_type = data.get('type', 'LONG')
        mode = data.get('mode', 'OPEN')
        
        payload = {
            "settings": {
                "targetUrl": "https://www.mexc.com/ru-RU/futures/ETH_USDT",
                "openType": signal_type,
                "openPercent": 20,
                "closeType": signal_type,
                "closePercent": 100,
                "mode": mode
            }
        }
        
        webhook_url = os.getenv('SIGNAL_WEBHOOK_URL', '')
        if not webhook_url:
            return jsonify({'status': 'error', 'message': 'Webhook URL not configured'}), 400
        
        try:
            import requests
            response = requests.post(webhook_url, json=payload, timeout=10)
            status = f"HTTP {response.status_code}"
        except Exception as e:
            status = f"Error: {str(e)}"
        
        signal_record = {
            'type': signal_type,
            'mode': mode,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'status': status
        }
        signal_history.insert(0, signal_record)
        signal_history = signal_history[:50]  # Keep last 50
        
        logging.info(f"Test signal sent: {signal_type} {mode} - {status}")
        return jsonify({'status': status, 'signal': signal_record})
    except Exception as e:
        logging.error(f"Send signal error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/signals', methods=['GET'])
def api_signals():
    """Получить историю сигналов"""
    return jsonify({'signals': signal_history})

import time
import requests
top_gainers_cache = {'data': [], 'timestamp': 0}
CACHE_DURATION = 60

def fetch_top_gainers_background():
    """Фоновая загрузка всех 591 фьючерсных пар с Gate.io"""
    global top_gainers_cache
    try:
        import requests
        
        # Получаем все фьючерсные контракты
        contracts_response = requests.get('https://api.gateio.ws/api/v4/futures/usdt/contracts', timeout=10)
        if contracts_response.status_code != 200:
            logging.error(f"Gate.io contracts API error: {contracts_response.status_code}")
            return
        
        contracts = contracts_response.json()
        logging.info(f"Found {len(contracts)} futures contracts from Gate.io API")
        
        # Получаем все тикеры за один запрос (более эффективно)
        tickers_response = requests.get('https://api.gateio.ws/api/v4/futures/usdt/tickers', timeout=10)
        if tickers_response.status_code != 200:
            logging.error(f"Gate.io tickers API error: {tickers_response.status_code}")
            return
        
        tickers = tickers_response.json()
        ticker_map = {t['contract']: t for t in tickers}
        logging.info(f"Loaded {len(ticker_map)} tickers from Gate.io")
        
        gainers = []
        # Обрабатываем ВСЕ контракты (без ограничения, только ASCII символы)
        for contract in contracts:
            try:
                symbol = contract.get('name')
                if not symbol or symbol not in ticker_map:
                    continue
                
                # ❌ Пропускаем черные символы
                if symbol in BLACKLISTED_SYMBOLS:
                    continue
                
                # Фильтруем китайские символы и другие не-ASCII
                symbol_base = symbol.split('_')[0] if '_' in symbol else symbol
                if not all(ord(c) < 128 for c in symbol_base):
                    continue  # Пропускаем символы с кириллицей и иероглифами
                
                ticker = ticker_map[symbol]
                last_price = float(ticker.get('last', 0))
                change_24h = float(ticker.get('change_percentage', 0))
                
                if last_price > 0:
                    coin_name = symbol.split('_')[0].lower()
                    gainers.append({
                        'symbol': symbol,
                        'coin': coin_name,
                        'price': last_price,
                        'change': change_24h,
                        'volume': float(ticker.get('volume_24h', 0)),
                        'gecko_rank': 'N/A'
                    })
            except Exception as e:
                continue
        
        # Сортируем по 24ч изменению
        gainers.sort(key=lambda x: x['change'] if x['change'] else 0, reverse=True)
        logging.info(f"Sorted {len(gainers)} gainers by 24h change")
        
        # 🔒 БЛОКИРОВКА: если позиция открыта, пара ПОЗИЦИИ должна быть на #1 до конца сделки
        # ✅ FIX: Use position symbol from state, NOT current_trading_symbol (which resets on restart)
        position_symbol = None
        if state.get('in_position', False) and state.get('position'):
            position_symbol = state['position'].get('symbol')
        
        if position_symbol:
            for i, gainer in enumerate(gainers):
                if gainer['symbol'] == position_symbol:
                    # Переместить текущую монету на первое место
                    current_gainer = gainers.pop(i)
                    gainers.insert(0, current_gainer)
                    logging.info(f"🔒 БЛОКИРОВКА: {position_symbol} на #1 (в сделке, даже если другие выросли больше)")
                    break
        
        # CoinGecko рейтинги для топ 100
        if gainers:
            unique_coins = list(set([g['coin'] for g in gainers[:100]]))[:50]
            if unique_coins:
                coin_ids = ','.join(unique_coins)
                try:
                    cg_response = requests.get(
                        f'https://api.coingecko.com/api/v3/coins/markets',
                        params={'vs_currency': 'usd', 'ids': coin_ids, 'per_page': 250},
                        timeout=5
                    )
                    cg_data = cg_response.json()
                    cg_map = {coin['id']: coin.get('market_cap_rank', 'N/A') for coin in cg_data}
                    for coin in gainers:
                        coin['gecko_rank'] = cg_map.get(coin['coin'], coin['gecko_rank'])
                except:
                    pass
        
        top_gainers_cache['data'] = gainers
        # Set current_top1 for position opening
        if gainers:
            state["current_top1"] = {"pair": gainers[0].get('symbol', 'TOP1'), "price": float(gainers[0].get('price', 0))}
        top_gainers_cache['timestamp'] = time.time()
        logging.info(f"✅ Loaded ALL {len(gainers)} futures pairs with real data from Gate.io")
    except Exception as e:
        logging.error(f"Background fetch error: {e}", exc_info=True)

@app.route('/api/top_gainers', methods=['GET'])
def api_top_gainers():
    """Получить все фьючерсные пары Gate.io"""
    global top_gainers_cache
    
    # Если кэш пустой или устарел, обновляем в фоне
    if not top_gainers_cache['data'] or time.time() - top_gainers_cache['timestamp'] > CACHE_DURATION:
        threading.Thread(target=fetch_top_gainers_background, daemon=True).start()
    
    # Возвращаем закэшированные данные (или пустой если первый раз)
    is_fresh = time.time() - top_gainers_cache['timestamp'] < 5
    return jsonify({
        'gainers': top_gainers_cache['data'],
        'total_pairs': len(top_gainers_cache['data']),
        'cached': is_fresh,
        'loading': len(top_gainers_cache['data']) == 0
    })

@app.route('/api/current_trading_symbol', methods=['GET'])
def api_current_trading_symbol():
    """Получить текущий торгуемый символ"""
    return jsonify({'symbol': current_trading_symbol})

@app.route('/api/futures_count', methods=['GET'])
def api_futures_count():
    """Показать количество фьючерсных пар на Kucoin"""
    try:
        import ccxt
        exchange = ccxt.kucoin()
        markets = exchange.fetch_markets()
        
        swap_pairs = [m for m in markets if m.get('type') == 'swap']
        swap_examples = [m['symbol'] for m in swap_pairs[:15]]
        
        return jsonify({
            'futures_count': len(swap_pairs),
            'examples': swap_examples
        })
    except Exception as e:
        return jsonify({'error': str(e)})

def auto_authenticate_api():
    """Автоматически подключиться к API если secrets есть"""
    global api_connected_global, saved_virtual_balance, state
    try:
        api_key = os.getenv('GATE_API_KEY', '').strip()
        api_secret = os.getenv('GATE_API_SECRET', '').strip()
        
        if not api_key or not api_secret:
            return False
        
        logging.info("🔐 Auto-authenticating with stored Gate.io API keys...")
        
        # ✅ FIRST: Load saved trading mode from state file
        saved_trading_mode = 'demo'
        saved_api_connected = False
        try:
            with open('goldantelopegate_v1.0_state.json', 'r') as f:
                file_state = json.load(f)
                saved_trading_mode = file_state.get('trading_mode', 'demo')
                saved_api_connected = file_state.get('api_connected', False)
                logging.info(f"📂 Loaded saved mode from file: trading_mode={saved_trading_mode}, api_connected={saved_api_connected}")
        except Exception as e:
            logging.debug(f"Could not load saved mode: {e}")
        
        # Validate and get real balance
        is_valid, balance_data = validate_api_credentials(api_key, api_secret)
        if not is_valid:
            logging.warning("⚠️ Auto-auth failed: Invalid API credentials")
            return False
        
        # Extract real balance
        real_balance = 0.0
        if isinstance(balance_data, dict) and 'USDT' in balance_data:
            real_balance = balance_data['USDT'].get('free', 0)
        
        # ✅ RESTORE REAL MODE if it was previously saved
        if saved_trading_mode == 'real' and saved_api_connected:
            api_connected_global = True
            state['api_connected'] = True
            state['trading_mode'] = 'real'
            state['balance'] = real_balance
            state['available'] = real_balance
            logging.info(f"✅ Auto-auth SUCCESS! RESTORED REAL MODE - Balance: ${real_balance:.2f}")
            return True
        else:
            # Keep in DEMO mode - first time or user switched to DEMO
            api_connected_global = False
            state['api_connected'] = False
            state['trading_mode'] = 'demo'
            logging.info(f"✅ Auto-auth SUCCESS! Real balance available: ${real_balance:.2f} (DEMO mode active)")
            return True
        
        return False
    except Exception as e:
        logging.error(f"Auto-auth error: {e}")
        return False

def auto_start_bot():
    """Автоматически запустить бота при старте приложения с TOP 1 гейнером"""
    global bot_running, bot_thread, bot_starting, current_trading_symbol
    try:
        if bot_running or bot_starting:
            logging.info("Bot already running or starting - skipping auto-start")
            return
        
        bot_starting = True
        
        # Загружаем TOP gainers перед стартом
        fetch_top_gainers_background()
        
        # Ждём пока загрузятся данные
        time.sleep(2)
        
        # Получаем TOP 1 символ
        current_trading_symbol = get_top_trading_symbol()
        logging.info(f"✅ Auto-starting bot with TOP 1 gainer: {current_trading_symbol}")
        
        bot_running = True
        bot_thread = threading.Thread(target=bot_main_loop, daemon=True)
        bot_thread.start()
        logging.info("✅ Trading bot auto-started on app initialization")
        bot_starting = False
    except Exception as e:
        bot_running = False
        bot_starting = False
        logging.error(f"Auto-start bot error: {e}")

init_data_fetcher()
init_telegram()
# Запускаем загрузку TOP gainers в фоне перед автостартом бота
threading.Thread(target=fetch_top_gainers_background, daemon=True).start()
time.sleep(1)
# Автоматическое подключение к API если secrets есть
auto_authenticate_api()
auto_start_bot()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))  # Railway uses PORT env var, default 5000 for Replit
    app.run(host='0.0.0.0', port=port, debug=False)
