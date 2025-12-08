import os
import logging
import secrets
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_from_directory
import threading
from datetime import datetime
import pandas as pd
from trading_bot import TradingBot, state
from telegram_notifications import TelegramNotifier

load_dotenv()

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

bot_instance = None
bot_thread = None
bot_running = False
telegram_notifier = None
data_fetcher = None
signal_history = []  # Хранилище отправленных сигналов
current_trading_symbol = "PIPPIN_USDT"  # Динамическая торговая пара

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
            # Ищем первый символ с ASCII буквами (не китайский)
            for pair in top_gainers_cache['data']:
                symbol = pair.get('symbol', '')
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
    global bot_running, bot_instance, current_trading_symbol
    
    try:
        # Получаем топ пару перед стартом
        current_trading_symbol = get_top_trading_symbol()
        
        bot_instance = TradingBot(telegram_notifier=telegram_notifier, trading_symbol=current_trading_symbol)
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

@app.route('/')
def index():
    """Главная страница - дашборд"""
    return render_template('dashboard.html')

@app.route('/webapp')
def webapp():
    """Telegram WebApp интерфейс"""
    return render_template('webapp.html')

@app.route('/api/status')
def api_status():
    """Получение текущего статуса бота"""
    try:
        global top_gainers_cache
        
        # Refresh TOP1 price if cache is older than 10 seconds
        if time.time() - top_gainers_cache['timestamp'] > 10:
            threading.Thread(target=fetch_top_gainers_background, daemon=True).start()
        
        directions = {}
        current_price = 3000.0
        unrealized_pnl = 0.0
        
        fetcher = bot_instance if bot_instance else data_fetcher
        if fetcher:
            try:
                directions = fetcher.get_current_directions()
                current_price = fetcher.get_current_price()
                unrealized_pnl = fetcher.calculate_unrealized_pnl()
            except Exception as e:
                logging.error(f"Error fetching data: {e}")
        
        position_data = state.get('position')
        top1_display = ""
        if position_data and state.get('in_position'):
            position_data = dict(position_data)
            position_data['unrealized_pnl'] = unrealized_pnl
            # IMPORTANT: Use position's current price, not TOP1 price
            position_symbol = position_data.get('symbol', current_trading_symbol)
            try:
                if fetcher and position_symbol:
                    position_fetcher = TradingBot(telegram_notifier=None, trading_symbol=position_symbol)
                    current_price = position_fetcher.get_current_price()
            except Exception as e:
                logging.debug(f"Could not fetch position symbol price: {e}")
            position_data['current_price'] = round(current_price, 6)  # Use position's current_price
            position_data['symbol'] = position_symbol
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
                top1_display = f"{top1_entry.get('pair', 'TOP1')} ${top1_entry.get('price', 0):.4f}"
            position_data['top1_display'] = top1_display
        else:
            # When no position, show current TOP1 with FRESH price from Gate.io
            if top_gainers_cache['data']:
                top1 = top_gainers_cache['data'][0]
                top1_symbol = top1.get('symbol', 'TOP1')
                top1_price = float(top1.get('price', 0))
                top1_display = f"{top1_symbol} ${top1_price:.4f}"
                # Store current TOP1 in state for next position opening
                state["current_top1"] = {"pair": top1_symbol, "price": top1_price}
                logging.debug(f"TOP1 Update: {top1_display}")
        
        return jsonify({
            'bot_running': bot_running,
            'paper_mode': os.getenv('RUN_IN_PAPER', '1') == '1',
            'balance': round(state.get('balance', 1000), 2),
            'available': round(state.get('available', 1000), 2),
            'in_position': state.get('in_position', False),
            'position': position_data,
            'top1_display': top1_display,
            'current_price': current_price,
            'unrealized_pnl': unrealized_pnl,
            'directions': directions,
            'sar_directions': directions,
            'trades': state.get('trades', []),
            'current_symbol': current_trading_symbol
        })
    except Exception as e:
        logging.error(f"Status error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/start_bot', methods=['POST'])
def api_start_bot():
    """Запуск торгового бота"""
    global bot_running, bot_thread, current_trading_symbol
    
    if bot_running:
        return jsonify({'message': 'Бот уже запущен', 'status': 'running'})
    
    try:
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
        return jsonify({'message': 'Бот успешно запущен', 'status': 'running'})
    except Exception as e:
        bot_running = False
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

@app.route('/api/open_short', methods=['POST'])
def api_open_short():
    """Открытие SHORT позиции как исключение"""
    if state.get('in_position'):
        return jsonify({'error': 'Уже есть открытая позиция'}), 400
    
    try:
        if bot_instance:
            # Получаем реальную цену TOP 1 из top_gainers
            if top_gainers_cache['data'] and len(top_gainers_cache['data']) > 0:
                price = top_gainers_cache['data'][0].get('price', 3000.0)
            else:
                price = bot_instance.get_current_price()
            amount, notional = bot_instance.compute_order_size_usdt(state["available"], price)
            position = bot_instance.place_market_order("sell", amount, price_override=price)  # SHORT
            if position:
                return jsonify({'message': 'SHORT позиция успешно открыта', 'position': position})
            else:
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
        if tf not in ['1m', '5m', '15m']:
            tf = '5m'
        
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
                        'color': '#10b981' if is_uptrend else '#ef4444',  # Green if uptrend, red if downtrend
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
    """Сброс баланса до начального значения"""
    try:
        from trading_bot import START_BANK
        state['balance'] = START_BANK
        state['available'] = START_BANK
        state['trades'] = []
        state['in_position'] = False
        state['position'] = None
        
        if bot_instance:
            bot_instance.save_state_to_file()
        
        return jsonify({'message': f'Баланс сброшен до ${START_BANK:.2f}'})
    except Exception as e:
        logging.error(f"Reset balance error: {e}")
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

def auto_start_bot():
    """Автоматически запустить бота при старте приложения с TOP 1 гейнером"""
    global bot_running, bot_thread, current_trading_symbol
    try:
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
    except Exception as e:
        bot_running = False
        logging.error(f"Auto-start bot error: {e}")

init_data_fetcher()
init_telegram()
# Запускаем загрузку TOP gainers в фоне перед автостартом бота
threading.Thread(target=fetch_top_gainers_background, daemon=True).start()
time.sleep(1)
auto_start_bot()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))  # Railway uses PORT env var, default 5000 for Replit
    app.run(host='0.0.0.0', port=port, debug=False)
