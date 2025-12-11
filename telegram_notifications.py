import requests
import logging
import os
from datetime import datetime

class TelegramNotifier:
    def __init__(self):
        # ----------- YOUR TOKEN + CHAT ID -----------
        self.bot_token = "8254846286:AAFbb-NrJMLS9-XB3YLtrYm3U4YIXeucAeM"
        self.chat_ids = ["7373419661"]
        self.owner_id = "7373419661"
        # --------------------------------------------

        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, message):
        """Send a message to Telegram"""
        if not self.bot_token or not self.chat_ids:
            logging.warning("Telegram credentials not configured")
            return False
            
        success_count = 0
        for chat_id in self.chat_ids:
            try:
                url = f"{self.base_url}/sendMessage"

                try:
                    chat_id_int = int(str(chat_id).strip())
                except:
                    chat_id_int = chat_id
                    
                data = {
                    "chat_id": chat_id_int,
                    "text": message,
                    "parse_mode": "HTML"
                }
                
                # Telegram uses form-data, not JSON
                response = requests.post(url, data=data, timeout=10)
                response.raise_for_status()

                logging.info(f"✅ Message sent to {chat_id_int}")
                success_count += 1
            except Exception as e:
                logging.error(f"Failed to send Telegram message to {chat_id}: {e}")
                
        return success_count > 0
    
    def send_current_position(self, position, current_price, balance=0, symbol="TOP1"):
        if not position:
            message = f"""
<b>CURRENT POSITION</b>

No open position at the moment.
<b>Balance:</b> ${balance:.2f}
            """.strip()
        else:
            side_text = "LONG" if position["side"] == "long" else "SHORT"
            trade_num = position.get("trade_number", 1)

            entry_price = position["entry_price"]
            size = position["size_base"]

            if position["side"] == "long":
                pnl = (current_price - entry_price) * size
            else:
                pnl = (entry_price - current_price) * size

            pnl_sign = "+" if pnl > 0 else ""

            margin = position["notional"] / 10
            roi = (pnl / margin) * 100 if margin > 0 else 0
            symbol_display = symbol.replace("_USDT", "")

            message = f"""
<b>CURRENT POSITION #{trade_num}</b>
<b>{side_text} {symbol_display}/USDT</b>

<b>Entry:</b> ${entry_price:.2f}
<b>Current:</b> ${current_price:.2f}
<b>P&L:</b> {pnl_sign}{pnl:.2f} USDT
<b>ROI:</b> {pnl_sign}{roi:.2f}%
<b>Balance:</b> ${balance:.2f}

<b>Entry Time:</b> {datetime.fromisoformat(position["entry_time"]).strftime("%H:%M:%S")}
            """.strip()

        self.send_message(message)

    def send_position_opened(self, position, current_price, trade_number=1, balance=0, symbol="TOP1"):
        side_text = "LONG" if position["side"] == "long" else "SHORT"
        symbol_display = symbol.replace("_USDT", "")

        message = f"""
<b>✅ POSITION OPENED #{trade_number}</b>
<b>{side_text} {symbol_display}/USDT</b>

<b>Entry:</b> ${position["entry_price"]:.2f}
<b>Price now:</b> ${current_price:.2f}

<b>Balance:</b> ${balance:.2f}
        """.strip()

        self.send_message(message)

    def send_position_closed(self, trade, trade_number=1, balance=0, symbol="TOP1"):
        side_text = "LONG" if trade["side"] == "long" else "SHORT"
        symbol_display = symbol.replace("_USDT", "")

        margin = trade["notional"] / 10
        roi = (trade["pnl"] / margin) * 100 if margin > 0 else 0
        pnl_sign = "+" if trade["pnl"] > 0 else ""

        message = f"""
<b>TRADE CLOSED #{trade_number}</b>
<b>{side_text} {symbol_display}/USDT</b>

<b>Entry:</b> ${trade["entry_price"]:.6f}
<b>Exit:</b> ${trade["exit_price"]:.6f}

<b>P&L:</b> {pnl_sign}{trade["pnl"]:.2f}
<b>ROI:</b> {pnl_sign}{roi:.2f}%

<b>Balance:</b> ${balance:.2f}

Closed: {datetime.fromisoformat(trade["time"]).strftime("%H:%M:%S")}
        """.strip()

        self.send_message(message)

    def send_error(self, error_message):
        message = f"""
<b>TRADING BOT ERROR</b>

<b>Error:</b> {error_message}
<b>Time:</b> {datetime.utcnow().strftime("%H:%M:%S UTC")}
        """.strip()

        self.send_message(message)

    def send_message_to_chat(self, chat_id, message):
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            r = requests.post(url, data=data, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to send: {e}")
            return False
