import os
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, session, flash
from datetime import datetime
import json
import threading
import time
import zipfile
import tempfile

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")

# Import trading bot components
from trading_bot import TradingBot, state
from telegram_notifications import TelegramNotifier
# Google Sheets integration removed

# Global instances
bot = None
notifier = None
# sheets_reporter = None  # Google Sheets integration removed
bot_thread = None
bot_running = False

# Authentication middleware
def require_auth():
    """Check if user is authenticated"""
    if 'authenticated' not in session:
        return False
    return session['authenticated'] == True

def auth_required(f):
    """Decorator to require authentication for a route"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not require_auth():
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            else:
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def init_services():
    global bot, notifier
    
    # Initialize Telegram notifier
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    notifier = TelegramNotifier(telegram_token, telegram_chat_id)
    
    # Google Sheets integration removed
    
    # Initialize trading bot with notifier only
    bot = TradingBot(notifier)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        password = request.form.get('password', '')
        view_password = os.environ.get('VIEW_PASSWORD', '')
        
        if password and password == view_password:
            session['authenticated'] = True
            flash('Успешный вход в систему', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Неверный пароль', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout user"""
    session.pop('authenticated', None)
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))

@app.route('/')
@auth_required
def dashboard():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/status')
@auth_required
def api_status():
    """Get current bot status and trading data"""
    global bot_running
    
    try:
        current_price = bot.get_current_price() if bot else 0.0
        
        # Initialize bot if not already done
        if not bot:
            init_services()
        
        # Get PSAR directions
        directions = {"1m": "unknown", "5m": "unknown", "15m": "unknown"}
        if bot:
            try:
                bot_directions = bot.get_current_directions()
                for tf in ["1m", "5m", "15m"]:
                    dir_value = bot_directions.get(tf)
                    if isinstance(dir_value, str) and dir_value in ["long", "short"]:
                        directions[tf] = dir_value
                    elif dir_value is None:
                        directions[tf] = "loading"
                    else:
                        directions[tf] = "unknown"
            except Exception as e:
                logging.error(f"Error getting directions: {e}")
                directions = {"1m": "error", "5m": "error", "15m": "error"}
        
        # Обогащаем данные позиции расчетными полями
        position_data = state.get("position")
        if position_data and state.get("in_position"):
            # Копируем данные позиции
            enriched_position = position_data.copy()
            
            # Рассчитываем стоп лосс цену (40% от ставки)
            entry_price = position_data.get("entry_price", 0)
            margin = position_data.get("margin", abs(position_data.get("notional", 0)) / 500)
            side = position_data.get("side", "long")
            
            # Вычисляем цену стоп лосс на основе 40% от маржи
            stop_loss_amount = margin * 0.4  # 40% от маржи
            size = position_data.get("size_base", 1)
            if side == "long":
                stop_loss_price = entry_price - (stop_loss_amount / size)  # Цена, при которой убыток составит 40% от маржи
            else:
                stop_loss_price = entry_price + (stop_loss_amount / size)  # Для шорта - обратная логика
            enriched_position["stop_loss_price"] = stop_loss_price
            
            # Рассчитываем оставшееся время до закрытия
            entry_time_str = position_data.get("entry_time")
            close_time_seconds = position_data.get("close_time_seconds", 0)
            if entry_time_str and close_time_seconds:
                try:
                    entry_time = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
                    current_time = datetime.utcnow()
                    elapsed_seconds = (current_time - entry_time).total_seconds()
                    time_left = max(0, close_time_seconds - elapsed_seconds)
                    enriched_position["time_left"] = int(time_left)
                except:
                    enriched_position["time_left"] = 0
            else:
                enriched_position["time_left"] = 0
                
            position_data = enriched_position
        
        response_data = {
            "bot_running": bot_running,
            "current_time": datetime.utcnow().isoformat(),
            "balance": state.get("balance", 0.0),
            "available": state.get("available", 0.0),
            "in_position": state.get("in_position", False),
            "position": position_data,
            "current_price": current_price,
            "directions": directions,
            "trades": state.get("trades", [])[:20],  # Last 20 trades
            "paper_mode": os.getenv("RUN_IN_PAPER", "1") == "1"
        }
        
        return jsonify(response_data)
    except Exception as e:
        logging.error(f"Error in api_status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/debug_sar')
@auth_required
def api_debug_sar():
    """Debug SAR values and calculations"""
    try:
        if not bot:
            return jsonify({"error": "Bot not initialized"}), 500
            
        debug_data = {}
        
        for tf in ["1m", "5m", "15m"]:
            try:
                # Получаем данные
                df = bot.fetch_ohlcv_tf(tf)
                if df is None or len(df) < 10:
                    debug_data[tf] = {"error": "No data or insufficient data"}
                    continue
                    
                # Рассчитываем PSAR
                psar = bot.compute_psar(df)
                if psar is None:
                    debug_data[tf] = {"error": "PSAR calculation failed"}
                    continue
                
                # Последние 5 значений  
                last_candles = df.tail(5)[["datetime", "open", "high", "low", "close"]].to_dict(orient='records')
                last_psar_values = psar.tail(5).tolist()
                
                # Последние значения
                last_close = df["close"].iloc[-1]
                last_psar = psar.iloc[-1]
                direction = "long" if last_close > last_psar else "short"
                
                debug_data[tf] = {
                    "last_close": round(last_close, 2),
                    "last_psar": round(last_psar, 2),
                    "direction": direction,
                    "close_vs_psar": round(last_close - last_psar, 2),
                    "last_candles": [
                        {
                            "time": candle["datetime"].strftime("%H:%M:%S") if candle["datetime"] else "N/A",
                            "open": round(candle["open"], 2),
                            "high": round(candle["high"], 2), 
                            "low": round(candle["low"], 2),
                            "close": round(candle["close"], 2)
                        }
                        for candle in last_candles
                    ],
                    "last_psar_values": [round(p, 2) for p in last_psar_values]
                }
            except Exception as e:
                debug_data[tf] = {"error": str(e)}
                
        return jsonify({
            "timestamp": datetime.utcnow().isoformat(),
            "current_price": round(bot.get_current_price(), 2),
            "sar_data": debug_data
        })
        
    except Exception as e:
        logging.error(f"Error in debug_sar: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/start_bot', methods=['POST'])
@auth_required
def start_bot():
    """Start the trading bot"""
    global bot_running, bot_thread
    
    try:
        if bot_running:
            return jsonify({"error": "Бот уже запущен"}), 400
        
        if not bot:
            init_services()
        
        bot_running = True
        bot_thread = threading.Thread(target=bot_worker, daemon=True)
        bot_thread.start()
        
        logging.info("Trading bot started")
        return jsonify({"message": "Бот успешно запущен"})
    except Exception as e:
        logging.error(f"Error starting bot: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stop_bot', methods=['POST'])
@auth_required
def stop_bot():
    """Stop the trading bot"""
    global bot_running
    
    try:
        bot_running = False
        logging.info("Trading bot stopped")
        return jsonify({"message": "Бот остановлен"})
    except Exception as e:
        logging.error(f"Error stopping bot: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/close_position', methods=['POST'])
@auth_required
def close_position():
    """Manually close current position"""
    try:
        if not bot:
            return jsonify({"error": "Бот не инициализирован"}), 400
        
        if not state.get("in_position", False):
            return jsonify({"error": "Нет открытой позиции"}), 400
        
        trade = bot.close_position()
        if trade:
            return jsonify({"message": "Позиция закрыта", "trade": trade})
        else:
            return jsonify({"error": "Ошибка при закрытии позиции"}), 500
    except Exception as e:
        logging.error(f"Error closing position: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/telegram_info')
@auth_required
def telegram_info():
    """Get Telegram bot information and access control status"""
    try:
        if not notifier:
            init_services()
        
        bot_username = notifier.get_bot_info() if notifier else None
        owner_id = os.environ.get("TELEGRAM_OWNER_ID", "")
        
        # Check webhook status
        webhook_status = "not_configured"
        if notifier and notifier.bot_token:
            try:
                import requests
                webhook_url = f"https://api.telegram.org/bot{notifier.bot_token}/getWebhookInfo"
                response = requests.get(webhook_url, timeout=10)
                if response.status_code == 200:
                    webhook_info = response.json().get('result', {})
                    webhook_url = webhook_info.get('url', '')
                    if webhook_url:
                        webhook_status = "configured"
                    else:
                        webhook_status = "not_set"
            except:
                webhook_status = "error"
        
        response = {
            "bot_configured": bool(notifier and notifier.bot_token),
            "bot_username": bot_username,
            "owner_id": owner_id if owner_id else "NOT_SET",
            "webhook_status": webhook_status,
            "access_control_enabled": bool(owner_id),
            "subscription_instructions": {
                "step1": f"Найдите бота @{bot_username} в Telegram" if bot_username else "Бот не настроен",
                "step2": "Напишите /start боту",
                "step3": "Используйте /status для проверки состояния бота",
                "note": "Доступ ограничен только для владельца (TELEGRAM_OWNER_ID)"
            }
        }
        
        return jsonify(response)
    except Exception as e:
        logging.error(f"Error getting telegram info: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/send_test_message', methods=['POST'])
@auth_required
def send_test_message():
    """Send test message to Telegram"""
    try:
        if not notifier:
            init_services()
        
        test_message = """
🤖 <b>ТЕСТОВОЕ СООБЩЕНИЕ</b>

✅ Telegram бот работает!
📊 Торговый бот goldantilopabtc500 подключен
⚡ Уведомления настроены корректно

⏰ Время: """ + datetime.utcnow().strftime("%H:%M:%S UTC") + """
        """
        
        if notifier is None:
            return jsonify({"error": "Telegram notifier not initialized"}), 500
            
        success = notifier.send_message(test_message.strip())
        
        if success:
            return jsonify({"message": "Тестовое сообщение отправлено в Telegram"})
        else:
            return jsonify({"error": "Ошибка отправки сообщения"}), 500
    except Exception as e:
        logging.error(f"Error sending test message: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/reset_balance', methods=['POST'])
@auth_required
def reset_balance():
    """Reset balance to 1000 and clear trading history"""
    global bot_running
    
    try:
        # Stop bot if running
        bot_running = False
        time.sleep(1)  # Give time to stop
        
        # Reset state
        state.clear()
        state.update({
            "balance": 1000.0,
            "available": 1000.0,
            "in_position": False,
            "position": None,
            "last_trade_time": None,
            "last_1m_dir": None,
            "one_min_flip_count": 0,
            "trades": []
        })
        
        # Save to file
        if bot:
            bot.save_state_to_file()
        
        logging.info("Balance reset to $1000, history cleared")
        return jsonify({"message": "Баланс сброшен на $1000, история очищена"})
    except Exception as e:
        logging.error(f"Error resetting balance: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/webhook/telegram', methods=['POST'])
def telegram_webhook():
    """Handle incoming Telegram messages"""
    try:
        if not notifier:
            init_services()
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Check if it's a message update
        if 'message' in data:
            message = data['message']
            if notifier and hasattr(notifier, 'handle_message'):
                success = notifier.handle_message(message)
                
                if success:
                    return jsonify({"status": "ok"})
                else:
                    return jsonify({"error": "Message handling failed"}), 500
            else:
                return jsonify({"error": "Notifier not available"}), 500
        
        # Ignore other types of updates
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logging.error(f"Error in telegram webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_global_state')
@auth_required
def get_global_state():
    """Get global bot state for external access"""
    global bot_running
    try:
        # Add bot_running to state for telegram notifications
        current_state = state.copy()
        current_state['bot_running'] = bot_running
        return jsonify(current_state)
    except Exception as e:
        logging.error(f"Error getting global state: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/download')
def download_bot():
    """Download bot files with password protection"""
    try:
        # Check password from URL parameter
        provided_password = request.args.get('password', '')
        correct_password = os.environ.get('BOT_PASSWORD', '')
        
        if not correct_password:
            return jsonify({"error": "Password not configured"}), 500
        
        if provided_password != correct_password:
            return jsonify({"error": "Invalid password"}), 403
        
        # Create temporary zip file
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, 'goldantilopabtc500_bot.zip')
        
        # Files to include in the download
        files_to_zip = [
            'app.py',
            'main.py', 
            'trading_bot.py',
            'telegram_notifications.py',
            'market_simulator.py',
            'templates/dashboard.html',
            'static/css/dashboard.css',
            'static/js/dashboard.js',
            'pyproject.toml',
            'replit.md'
        ]
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in files_to_zip:
                if os.path.exists(file_path):
                    zipf.write(file_path, file_path)
        
        return send_file(zip_path, 
                        as_attachment=True,
                        download_name='goldantilopabtc500_bot.zip',
                        mimetype='application/zip')
                        
    except Exception as e:
        logging.error(f"Error in download: {e}")
        return jsonify({"error": str(e)}), 500

def bot_worker():
    """Background worker for trading bot"""
    global bot_running
    
    try:
        if bot:
            bot.strategy_loop(lambda: bot_running)
    except Exception as e:
        logging.error(f"Bot worker error: {e}")
        bot_running = False

if __name__ == '__main__':
    init_services()
    app.run(host='0.0.0.0', port=5000, debug=True)
