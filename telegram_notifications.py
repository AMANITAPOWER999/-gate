import requests
import logging
import os
from datetime import datetime

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        if isinstance(chat_id, str) and ',' in chat_id:
            self.chat_ids = [id.strip() for id in chat_id.split(',') if id.strip()]
        elif chat_id:
            self.chat_ids = [chat_id]
        else:
            self.chat_ids = []
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        
        self.owner_id = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
        if self.owner_id:
            self.owner_id = str(self.owner_id)
            
    def send_message(self, message):
        """Send a message to Telegram"""
        if not self.bot_token or not self.chat_ids:
            logging.warning("Telegram credentials not configured")
            return False
            
        success_count = 0
        for chat_id in self.chat_ids:
            try:
                url = f"{self.base_url}/sendMessage"

                # Convert chat_id
                try:
                    chat_id_int = int(str(chat_id).strip())
                except:
                    chat_id_int = chat_id
                    
                data = {
                    "chat_id": chat_id_int,
                    "text": message,
                    "parse_mode": "HTML"
                }
                
                # FIXED: Telegram does NOT allow JSON here
                response = requests.post(url, data=data, timeout=10)
                response.raise_for_status()

                logging.info(f"✅ Message sent to {chat_id_int}")
                success_count += 1
            except Exception as e:
                error_msg = str(e)
                if "bot" in error_msg and ":" in error_msg:
                    error_msg = error_msg[:error_msg.find("bot")] + "bot[REDACTED]"
                logging.error(f"Failed to send Telegram message to {chat_id}: {error_msg}")
                
        if success_count > 0:
            logging.info(f"Telegram message sent to {success_count} chats")
            return True
        return False
    
    def send_current_position(self, position, current_price, balance=0, symbol="TOP1"):
        """Send notification about current open position"""
        if not position:
            message = """
<b>CURRENT POSITION</b>

No open position at the moment.
<b>Balance:</b> ${:.2f}
            """.format(balance).strip()
        else:
            side_emoji = "UP" if position["side"] == "long" else "DOWN"
            side_text = "LONG" if position["side"] == "long" else "SHORT"
            trade_number = position.get("trade_number", 1)
            
            entry_price = position["entry_price"]
            size = position["size_base"]
            if position["side"] == "long":
                pnl = (current_price - entry_price) * size
            else:
                pnl = (entry_price - current_price) * size
            
            pnl_sign = "+" if pnl > 0 else ""
            
            margin = position["notional"] / 10
            roi = (pnl / margin) * 100 if margin > 0 else 0
            
            symbol_display = symbol.replace('_USDT', '') if '_USDT' in symbol else symbol
            
            message = f"""
<b>CURRENT POSITION #{trade_number}</b>
<b>{side_text} {symbol_display}/USDT</b> (x10 leverage)

<b>Entry Price:</b> ${entry_price:.2f}
<b>Current Price:</b> ${current_price:.2f}
<b>Size (notional):</b> ${size:.2f}
<b>Notional:</b> ${position["notional"]:.2f}
<b>Margin:</b> ${margin:.2f}
<b>Leverage:</b> x10

<b>Unrealized P&L:</b> {pnl_sign}{pnl:.2f} USDT
<b>ROI:</b> {pnl_sign}{roi:.2f}%
<b>Balance:</b> ${balance:.2f}

<b>Entry Time:</b> {datetime.fromisoformat(position["entry_time"]).strftime("%H:%M:%S")}
            """.strip()
        
        self.send_message(message)
    
    def send_position_opened(self, position, current_price, trade_number=1, balance=0, symbol="TOP1"):
        """Send notification when position is opened"""
        side_text = "LONG" if position["side"] == "long" else "SHORT"
        symbol_display = symbol.replace('_USDT', '') if '_USDT' in symbol else symbol
        
        message = f"""
<b>✅ POSITION OPENED #{trade_number}</b>
<b>🚀 {side_text} {symbol_display}/USDT</b> (x10 leverage)

💵 <b>Entry Price:</b> ${position["entry_price"]:.2f}
📊 <b>Current Price:</b> ${current_price:.2f}
📈 <b>Size (notional):</b> ${position["size_base"]:.2f}
💰 <b>Notional:</b> ${position["notional"]:.2f}
🔐 <b>Margin:</b> ${position["notional"]/10:.2f}
⚡ <b>Leverage:</b> x10
💳 <b>Balance:</b> ${balance:.2f}

⏱️ <b>Entry Time:</b> {datetime.fromisoformat(position["entry_time"]).strftime("%H:%M:%S")}
        """.strip()
        
        self.send_message(message)
    
    def send_position_closed(self, trade, trade_number=1, balance=0, symbol="TOP1"):
        """Send notification when position is closed"""
        side_text = "LONG" if trade["side"] == "long" else "SHORT"
        symbol_display = symbol.replace('_USDT', '') if '_USDT' in symbol else symbol
        
        margin = trade["notional"] / 10
        roi = (trade["pnl"] / margin) * 100 if margin > 0 else 0
        
        pnl_sign = "+" if trade["pnl"] > 0 else ""
        pnl_emoji = "✅ PROFIT" if trade["pnl"] > 0 else "❌ LOSS"
        profit_emoji = "📈" if trade["pnl"] > 0 else "📉"
        
        close_reason = trade.get("close_reason", "manual").upper()
        close_reason_display = {
            "5M_CHANGED": "5m SAR Changed",
            "5M_30M_DIVERGE": "5m & 30m Diverged",
            "5M_DIVERGED": "5m Diverged from Entry",
            "30M_DIVERGED": "30m Diverged from Entry",
            "MANUAL": "Manual Close"
        }.get(close_reason, close_reason)
        
        message = f"""<b>{pnl_emoji} TRADE #{trade_number} CLOSED</b>
<b>🎯 {side_text} {symbol_display}/USDT</b> (x10 leverage)

📍 <b>Entry:</b> ${trade["entry_price"]:.6f}
🚪 <b>Exit:</b> ${trade["exit_price"]:.6f}
💰 <b>Notional:</b> ${trade["notional"]:.2f}
🔐 <b>Margin:</b> ${margin:.2f}

{profit_emoji} <b>P&L: {pnl_sign}${abs(trade["pnl"]):.2f}</b>
📊 <b>ROI: {pnl_sign}{roi:.2f}%</b>
💳 <b>Balance: ${balance:.2f}</b>

⌛ Duration: {trade.get("duration", "N/A")}
📌 Close Reason: {close_reason_display}
⏱️ Closed: {datetime.fromisoformat(trade["time"]).strftime("%H:%M:%S")}""".strip()
        
        self.send_message(message)
    
    def send_error(self, error_message):
        """Send error notification"""
        message = f"""
<b>TRADING BOT ERROR</b>

<b>Error:</b> {error_message}
<b>Time:</b> {datetime.utcnow().strftime("%H:%M:%S UTC")}

Please check the bot status and logs.
        """.strip()
        
        self.send_message(message)
    
    def add_subscriber(self, chat_id):
        """Add a new subscriber"""
        chat_id_str = str(chat_id)
        if chat_id_str not in self.chat_ids:
            self.chat_ids.append(chat_id_str)
            logging.info(f"Added new Telegram subscriber: {chat_id_str} (Total: {len(self.chat_ids)})")
            return True
        logging.info(f"User {chat_id_str} already subscribed")
        return False
    
    def is_owner(self, user_id):
        return True  # always returns True now
    
    def handle_message(self, message):
        """Handle incoming Telegram message"""
        try:
            user_id = message.get('from', {}).get('id')
            chat_id = message.get('chat', {}).get('id')
            text = message.get('text', '').strip()
            
            if text.lower() == '/start':
                is_new = self.add_subscriber(str(chat_id))
                self.send_welcome_message(chat_id, is_new)
                return True
            
            if text.lower() == '/help':
                self.send_help_message(chat_id)
            elif text.lower() == '/status':
                self.send_bot_status_on_demand(chat_id)
            elif text.lower() == '/subscribe':
                is_new = self.add_subscriber(str(chat_id))
                if is_new:
                    self.send_message_to_chat(chat_id, "You are now subscribed to trading notifications!")
                else:
                    self.send_message_to_chat(chat_id, "You are already subscribed!")
            else:
                self.send_message_to_chat(chat_id, "Unknown command. Use /help to see available commands.")
                
            return True
            
        except Exception as e:
            logging.error(f"Error handling Telegram message: {e}")
            return False
    
    def send_welcome_message(self, chat_id, is_new_subscriber):
        """Send welcome message"""
        if is_new_subscriber:
            message = """
<b>✅ Gold Antelope Trading Bot - OPEN FOR ALL</b>

You are now subscribed to real-time trading notifications!

This bot is open for everyone - no restrictions!

<b>What you'll receive:</b>
- Position opened/closed alerts
- Profit/Loss updates
- Real-time trading signals
- Bot status updates
- Dynamic TOP 1 gainer alerts

<b>Available commands:</b>
/status - Check current trading status
/help - Show all commands
/subscribe - Subscribe to notifications

<b>Details:</b>
- Exchange: Gate.io Futures
- Leverage: x5
- Strategy: 30m Parabolic SAR
- Mode: Paper Trading

Let’s make some profits!
            """.strip()
        else:
            message = """
<b>Welcome back!</b>

You are already subscribed to trading notifications.

Use /help to see available commands.
            """.strip()
        self.send_message_to_chat(chat_id, message)
    
    def send_help_message(self, chat_id):
        """Send help message"""
        message = """
<b>Trading Bot Commands</b>

/start - Subscribe
/status - Check status
/subscribe - Subscribe
/help - Show this help message

<b>Notifications:</b>
- Position opened/closed
- Profit/Loss updates
- Trading signals
- Bot status updates

<b>Info:</b>
- Strategy: Parabolic SAR
- Pair: ETH/USDT
- Leverage: x500
- Mode: Paper Trading
        """.strip()
        self.send_message_to_chat(chat_id, message)
    
    def send_bot_status_on_demand(self, chat_id):
        """Send bot status"""
        try:
            try:
                response = requests.get('http://localhost:8080/api/get_global_state', timeout=5)
                if response.status_code == 200:
                    state = response.json()
                    bot_running = state.get('bot_running', False)
                    balance = state.get('balance', 0)
                    in_position = state.get('in_position', False)
                    current_price = state.get('current_price', 0)
                else:
                    raise Exception("API request failed")
            except:
                from trading_bot import state
                bot_running = False
                balance = state.get('balance', 0)
                in_position = state.get('in_position', False)
                current_price = 0
            
            message = f"""
<b>Bot Status:</b> {"Running" if bot_running else "Stopped"}
<b>Balance:</b> ${balance:.2f}
<b>Position:</b> {"Active" if in_position else "None"}
<b>Price:</b> ${current_price:.2f}
            """.strip()
            
            self.send_message_to_chat(chat_id, message)
            
        except Exception as e:
            error_msg = f"Error getting bot status: {str(e)}"
            self.send_message_to_chat(chat_id, error_msg)
            logging.error(error_msg)
    
    def send_message_to_chat(self, chat_id, message):
        """Send message to specific chat"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to send message to chat {chat_id}: {e}")
            return False
    
    def get_bot_info(self):
        """Get bot username"""
        if not self.bot_token:
            return None
        try:
            url = f"{self.base_url}/getMe"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            bot_info = response.json()
            return bot_info.get('result', {}).get('username')
        except Exception as e:
            logging.error(f"Failed to get bot info: {e}")
            return None
