import os
import time
import json
import threading
import random
from datetime import datetime, timedelta

import ccxt
import pandas as pd
from ta.trend import PSARIndicator
import logging
from market_simulator import MarketSimulator
from signal_sender import SignalSender

API_KEY = os.getenv("GATE_API_KEY", "")
API_SECRET = os.getenv("GATE_API_SECRET", "")
RUN_IN_PAPER = os.getenv("RUN_IN_PAPER", "1") == "1"
USE_SIMULATOR = os.getenv("USE_SIMULATOR", "0") == "1"

SYMBOL = "TRADOOR/USDT"  # Default, будет переопределяться динамически
LEVERAGE = 10
ISOLATED = True
POSITION_PERCENT = 1.0
TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}
MIN_TRADE_SECONDS = 120
MIN_RANDOM_TRADE_SECONDS = 480
MAX_RANDOM_TRADE_SECONDS = 780
PAUSE_BETWEEN_TRADES = 0
START_BANK = 100.0
DASHBOARD_MAX = 20

state = {
    "balance": START_BANK,
    "available": START_BANK,
    "in_position": False,
    "position": None,
    "last_trade_time": None,
    "last_1m_dir": None,
    "one_min_flip_count": 0,
    "skip_next_signal": False,
    "trades": []
}

class TradingBot:
    def __init__(self, telegram_notifier=None, trading_symbol=None):
        global SYMBOL
        self.notifier = telegram_notifier
        self.signal_sender = SignalSender()
        
        # Устанавливаем символ для торговли
        if trading_symbol:
            SYMBOL = trading_symbol
            logging.info(f"Trading symbol set to: {SYMBOL}")
        else:
            trading_symbol = SYMBOL
        
        if USE_SIMULATOR:
            logging.info("Initializing market simulator")
            self.simulator = MarketSimulator(initial_price=3000, volatility=0.02)
            self.exchange = None
        else:
            logging.info("Initializing GATE.IO exchange connection")
            self.simulator = None
            self.exchange = ccxt.gateio({
                "apiKey": API_KEY,
                "secret": API_SECRET,
                "sandbox": False,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap",
                }
            })
            logging.info("GATE.IO configured for futures trading with leverage support")
            
            if API_KEY and API_SECRET:
                try:
                    if ISOLATED:
                        self.exchange.set_margin_mode('isolated', SYMBOL)
                        logging.info(f"Margin mode set to ISOLATED for {SYMBOL}")
                    
                    self.exchange.set_leverage(LEVERAGE, SYMBOL)
                    logging.info(f"Leverage set to {LEVERAGE}x for {SYMBOL}")
                except Exception as e:
                    logging.error(f"Failed to configure leverage/margin mode: {e}")
                    logging.error("Trading will continue in paper mode to avoid order rejections")
        
        self.load_state_from_file()
        
    def save_state_to_file(self):
        try:
            with open("goldantelopegate_v1.0_state.json", "w") as f:
                json.dump(state, f, default=str, indent=2)
        except Exception as e:
            logging.error(f"Save error: {e}")

    def load_state_from_file(self):
        try:
            with open("goldantelopegate_v1.0_state.json", "r") as f:
                data = json.load(f)
                state.update(data)
        except:
            pass

    def now(self):
        return datetime.utcnow()

    def fetch_ohlcv_tf(self, tf: str, limit=200):
        """
        Возвращает pd.DataFrame с колонками: timestamp, open, high, low, close, volume
        """
        try:
            if USE_SIMULATOR and self.simulator:
                ohlcv = self.simulator.fetch_ohlcv(tf, limit=limit)
            else:
                ohlcv = self.exchange.fetch_ohlcv(SYMBOL, timeframe=tf, limit=limit)
            
            if not ohlcv:
                return None
                
            df = pd.DataFrame(ohlcv)
            df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            logging.error(f"Error fetching {tf} ohlcv: {e}")
            return None

    def compute_psar(self, df: pd.DataFrame):
        """
        Возвращает Series с PSAR (последняя точка).
        """
        if df is None or len(df) < 5:
            return None
        try:
            high_series = pd.Series(df["high"].values)
            low_series = pd.Series(df["low"].values)
            close_series = pd.Series(df["close"].values)
            psar_ind = PSARIndicator(high=high_series, low=low_series, close=close_series, step=0.05, max_step=0.5)
            psar = psar_ind.psar()
            return psar
        except Exception as e:
            logging.error(f"PSAR compute error: {e}")
            return None

    def get_direction_from_psar(self, df: pd.DataFrame):
        """
        Возвращает направление 'long' или 'short' на основе сравнения последней close и psar
        """
        try:
            psar = self.compute_psar(df)
            if psar is None or len(psar) == 0:
                return None
            last_psar = psar.iloc[-1]
            last_close = df["close"].iloc[-1]
            
            if pd.isna(last_psar) or pd.isna(last_close):
                return None
            
            return "long" if last_close > last_psar else "short"
        except Exception as e:
            logging.error(f"Error in get_direction_from_psar: {e}")
            return None


    def get_current_directions(self):
        """Get current PSAR directions for all timeframes"""
        directions = {}
        for tf in TIMEFRAMES.keys():
            try:
                df = self.fetch_ohlcv_tf(tf, limit=50)
                if df is not None and len(df) >= 5:
                    direction = self.get_direction_from_psar(df)
                    directions[tf] = direction if direction else None
                else:
                    directions[tf] = None
            except Exception as e:
                logging.error(f"Error getting direction for {tf}: {e}")
                directions[tf] = None
        return directions

    def compute_order_size_usdt(self, balance, price):
        # Validate inputs
        if balance <= 0:
            logging.warning(f"❌ Invalid balance: {balance}")
            return 0.0, 0.0
        if price <= 0:
            logging.warning(f"❌ Invalid price: {price}")
            return 0.0, 0.0
        
        notional = balance * POSITION_PERCENT * LEVERAGE
        base_amount = notional / price
        logging.info(f"✅ Order size: amount={base_amount:.6f}, notional=${notional:.2f}, balance=${balance:.2f}, price=${price:.6f}")
        return base_amount, notional

    def get_current_price(self):
        """Get current price from exchange or simulator"""
        if USE_SIMULATOR and self.simulator:
            return self.simulator.get_current_price()
        else:
            try:
                ticker = self.exchange.fetch_ticker(SYMBOL)
                price = ticker['last']
                return price if price and price > 0 else 3000.0
            except Exception as e:
                logging.error(f"Error fetching price: {e}")
                return 3000.0

    def calculate_unrealized_pnl(self):
        """Рассчитать нереализованный P&L для открытой позиции
        Formula: P&L = (price_change) * size_base
        Для SHORT: P&L = (entry_price - current_price) * size_base
        Для LONG: P&L = (current_price - entry_price) * size_base
        """
        if not state["in_position"] or state["position"] is None:
            return 0.0
        
        pos = state["position"]
        current_price = self.get_current_price()
        
        # Safety check - ensure current_price is valid
        if current_price is None or current_price <= 0:
            current_price = pos.get("entry_price", 3000.0)
        
        entry_price = pos.get("entry_price", 0)
        size_base = pos.get("size_base", 0)
        
        # Ensure no division by zero
        if entry_price <= 0 or size_base <= 0:
            return 0.0
        
        # Calculate P&L based on position side
        # For SHORT: profit when price DOWN (entry_price - current_price > 0)
        # For LONG: profit when price UP (current_price - entry_price > 0)
        if pos.get("side") == "long":
            unrealized_pnl = (current_price - entry_price) * size_base
        else:  # SHORT
            unrealized_pnl = (entry_price - current_price) * size_base
        
        # Store current_price in position for API response
        pos["current_price"] = current_price
        
        return round(unrealized_pnl, 4)

    def place_market_order(self, side: str, amount_base: float, price_override: float = None):
        """
        side: 'buy' или 'sell' (для открытия позиции)
        amount_base: количество в базовой валюте (ETH)
        price_override: опциональная цена для установки (используется для TOP 1 гейнера)
        """
        logging.info(f"[{self.now()}] PLACE MARKET ORDER -> side={side}, amount={amount_base:.6f}")
        
        if RUN_IN_PAPER or API_KEY == "" or API_SECRET == "":
            price = price_override if price_override is not None else self.get_current_price()
            entry_price = price
            entry_time = datetime.utcnow()
            notional = amount_base * entry_price
            margin = notional / LEVERAGE
            
            state["available"] -= margin  # Deduct margin from available
            
            close_time_seconds = random.randint(MIN_RANDOM_TRADE_SECONDS, MAX_RANDOM_TRADE_SECONDS)
            
            if "telegram_trade_counter" not in state:
                state["telegram_trade_counter"] = 1
            else:
                state["telegram_trade_counter"] += 1
            trade_number = state["telegram_trade_counter"]
            
            state["in_position"] = True
            state["position"] = {
                "symbol": SYMBOL,  # Add trading symbol to position
                "side": "long" if side == "buy" else "short",
                "entry_price": entry_price,
                "size_base": amount_base,
                "notional": notional,
                "margin": margin,
                "entry_time": entry_time.isoformat(),
                "close_time_seconds": close_time_seconds,
                "trade_number": trade_number,
                "top1_entry": state.get("top1_entry", {})
            }
            state["last_trade_time"] = entry_time.isoformat()
            
            logging.info(f"Position opened with random close time: {close_time_seconds}s ({close_time_seconds/60:.1f} minutes)")
            
            if self.notifier:
                self.notifier.send_position_opened(state["position"], price, trade_number, state["balance"])
            
            if state["position"]["side"] == "long":
                self.signal_sender.send_open_long()
            else:
                self.signal_sender.send_open_short()
            
            return state["position"]
        else:
            try:
                try:
                    self.exchange.set_leverage(LEVERAGE, SYMBOL)
                except Exception as e:
                    logging.error(f"set_leverage failed: {e}")

                order = self.exchange.create_market_buy_order(SYMBOL, amount_base) if side == "buy" else self.exchange.create_market_sell_order(SYMBOL, amount_base)
                logging.info(f"Order response: {order}")
                
                entry_price = float(order.get("average", order.get("price", self.get_current_price())))
                entry_time = datetime.utcnow()
                notional = amount_base * entry_price
                margin = notional / LEVERAGE
                
                state["available"] -= margin
                
                close_time_seconds = random.randint(MIN_RANDOM_TRADE_SECONDS, MAX_RANDOM_TRADE_SECONDS)
                
                state["in_position"] = True
                state["position"] = {
                    "side": "long" if side == "buy" else "short",
                    "entry_price": entry_price,
                    "size_base": amount_base,
                    "notional": notional,
                    "margin": margin,
                    "entry_time": entry_time.isoformat(),
                    "close_time_seconds": close_time_seconds,
                    "top1_entry": state.get("top1_entry", {})
                }
                state["last_trade_time"] = entry_time.isoformat()
                
                logging.info(f"Position opened with random close time: {close_time_seconds}s ({close_time_seconds/60:.1f} minutes)")
                
                return state["position"]
                
            except Exception as e:
                logging.error(f"Order error: {e}")
                return None

    def close_position(self, close_reason="manual"):
        """Закрытие текущей позиции"""
        if not state["in_position"] or state["position"] is None:
            return None
            
        pos = state["position"]
        exit_price = self.get_current_price()
        entry_price = float(pos["entry_price"])
        size = float(pos["size_base"])
        
        if pos["side"] == "long":
            pnl = (exit_price - entry_price) * size
        else:
            pnl = (entry_price - exit_price) * size
        
        pnl = round(pnl, 4)
        
        entry_time = datetime.fromisoformat(pos["entry_time"])
        duration_seconds = (datetime.utcnow() - entry_time).total_seconds()
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        duration_str = f"{minutes}м {seconds}с"
        
        trade_record = {
            "time": datetime.utcnow().isoformat(),
            "side": pos["side"],
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size_base": size,
            "pnl": pnl,
            "notional": pos["notional"],
            "duration": duration_str,
            "close_reason": close_reason
        }
        
        state["balance"] += pnl
        margin_released = pos.get("margin", pos["notional"] / LEVERAGE)
        state["available"] = state["balance"]  # When no position: available = balance
        logging.info(f"✅ Position closed - balance=${state['balance']:.2f}, available=${state['available']:.2f}")
        state["top1_entry"] = {}  # Очистить TOP1 информацию при закрытии позиции
        state["trades"].append(trade_record)
        
        if len(state["trades"]) > DASHBOARD_MAX:
            state["trades"] = state["trades"][-DASHBOARD_MAX:]
        
        trade_number = pos.get("trade_number", state.get("telegram_trade_counter", 1))
        
        if self.notifier:
            self.notifier.send_position_closed(trade_record, trade_number, state["balance"])
        
        if pos["side"] == "long":
            self.signal_sender.send_close_long()
        else:
            self.signal_sender.send_close_short()
        
        state["in_position"] = False
        state["position"] = None
        
        self.save_state_to_file()
        
        logging.info(f"Position closed: PnL={pnl:.2f}, Reason={close_reason}")
        
        return trade_record

    def get_1m_direction(self):
        """Получить направление SAR на 1m таймфрейме"""
        try:
            df = self.fetch_ohlcv_tf("1m", limit=50)
            if df is None or len(df) < 5:
                logging.warning("Could not fetch 1m OHLCV data - using default LONG")
                return "long"
            direction = self.get_direction_from_psar(df)
            logging.debug(f"1m direction determined: {direction}")
            return direction
        except Exception as e:
            logging.error(f"Error in get_1m_direction: {e}", exc_info=True)
            return "long"

    def get_5m_direction(self):
        """Получить направление SAR на 5m таймфрейме"""
        try:
            df = self.fetch_ohlcv_tf("5m", limit=50)
            if df is None or len(df) < 5:
                logging.warning("Could not fetch 5m OHLCV data - using default LONG")
                return "long"
            direction = self.get_direction_from_psar(df)
            logging.debug(f"5m direction determined: {direction}")
            return direction
        except Exception as e:
            logging.error(f"Error in get_5m_direction: {e}", exc_info=True)
            return "long"

    def get_15m_direction(self):
        """Получить направление SAR на 15m таймфрейме"""
        try:
            df = self.fetch_ohlcv_tf("15m", limit=50)
            if df is None or len(df) < 5:
                logging.warning("Could not fetch 15m OHLCV data - using default LONG")
                return "long"
            direction = self.get_direction_from_psar(df)
            logging.debug(f"15m direction determined: {direction}")
            return direction
        except Exception as e:
            logging.error(f"Error in get_15m_direction: {e}", exc_info=True)
            return "long"

    def get_30m_direction(self):
        """Получить направление SAR на 30m таймфрейме"""
        try:
            df = self.fetch_ohlcv_tf("30m", limit=50)
            if df is None or len(df) < 5:
                logging.warning("Could not fetch 30m OHLCV data - using default LONG")
                return "long"
            direction = self.get_direction_from_psar(df)
            logging.debug(f"30m direction determined: {direction}")
            return direction
        except Exception as e:
            logging.error(f"Error in get_30m_direction: {e}", exc_info=True)
            return "long"

    def strategy_loop(self, should_continue=None):
        """Новая стратегия торговли
        ВХОД: 30m SAR и 5m SAR в ОДНОМ направлении (оба SHORT или оба LONG)
        ВЫХОД: Смена 5m SAR -> закрыть сделку
        ПОЗИЦИИ: Открываем и SHORT и LONG в зависимости от направления
        """
        logging.info("Starting trading strategy loop - 30m/5m SAR alignment mode")
        logging.info("ENTRY: 30m SAR и 5m SAR в одном направлении")
        logging.info("EXIT: Смена 5m SAR направления")
        logging.info("POSITIONS: SHORT и LONG позиции")
        
        last_5m_direction = None
        direction_check_interval = 5  # Check every 5 seconds
        last_direction_check = 0
        
        while True:
            if should_continue and not should_continue():
                logging.info("Strategy loop stopped by external signal")
                break
            
            try:
                current_time = time.time()
                
                # Check directions every 5 seconds
                if current_time - last_direction_check >= direction_check_interval:
                    current_30m = self.get_30m_direction()
                    current_5m = self.get_5m_direction()
                    
                    logging.info(f"30m SAR: {current_30m.upper()}, 5m SAR: {current_5m.upper()}")
                    
                    if last_5m_direction is None:
                        # First check - initialize
                        last_5m_direction = current_5m
                        logging.info(f"Initialized 5m direction: {current_5m.upper()}")
                        
                        # Проверяем выравнивание 30m и 5m при инициализации
                        if current_30m == current_5m and not state["in_position"]:
                            # Открываем позицию в направлении выравнивания
                            trade_side = "buy" if current_5m == "long" else "sell"
                            logging.info(f"🚀 Opening initial {current_5m.upper()} position (30m и 5m aligned)")
                            price = self.get_current_price()
                            amount, notional = self.compute_order_size_usdt(state["available"], price)
                            state["top1_entry"] = state.get("current_top1", {})
                            self.place_market_order(trade_side, amount)
                            self.save_state_to_file()
                        else:
                            logging.info(f"⏳ Waiting for 30m and 5m SAR to align")
                    
                    elif current_5m != last_5m_direction:
                        # 5m SAR changed - CLOSE position
                        logging.warning(f"⚠️ 5m SAR CHANGED: {last_5m_direction.upper()} -> {current_5m.upper()}")
                        
                        if state["in_position"]:
                            logging.info(f"CLOSING POSITION due to 5m SAR change")
                            self.close_position(close_reason="5m_sar_change_exit")
                            time.sleep(1)
                        
                        # Check if new alignment exists for opening new position
                        if current_30m == current_5m and not state["in_position"]:
                            trade_side = "buy" if current_5m == "long" else "sell"
                            logging.info(f"✅ 5m SAR CHANGED and aligned with 30m -> OPENING NEW {current_5m.upper()} POSITION")
                            price = self.get_current_price()
                            amount, notional = self.compute_order_size_usdt(state["available"], price)
                            state["top1_entry"] = state.get("current_top1", {})
                            self.place_market_order(trade_side, amount)
                            self.save_state_to_file()
                        
                        last_5m_direction = current_5m
                    else:
                        # 5m hasn't changed, but check if we need to close due to misalignment
                        if state["in_position"] and current_30m != current_5m:
                            logging.warning(f"⚠️ Position misalignment - 30m: {current_30m.upper()}, 5m: {current_5m.upper()}")
                            logging.info(f"CLOSING POSITION due to 30m/5m misalignment")
                            self.close_position(close_reason="alignment_loss")
                            time.sleep(1)
                        elif state["in_position"]:
                            logging.debug(f"Position held - 5m unchanged: {current_5m.upper()}, 30m: {current_30m.upper()}")
                    
                    last_direction_check = current_time
                
            except Exception as e:
                logging.error(f"Strategy loop error: {e}", exc_info=True)
            
            time.sleep(5)
