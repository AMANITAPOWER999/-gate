import os
import time
import json
import threading
import random
import uuid
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
TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
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
    "trades": [],
    "pending_signal_time": None,
    "pending_signal_direction": None,
    "pending_signal_levels": None,
    "rebalance_enabled": False
}

class TradingBot:
    def __init__(self, telegram_notifier=None, trading_symbol=None, app_context=None):
        global SYMBOL
        self.notifier = telegram_notifier
        self.signal_sender = SignalSender()
        self.app_context = app_context
        
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

    def get_price_for_symbol(self, symbol):
        """Get current price for ANY symbol (not just SYMBOL)"""
        if not symbol:
            return self.get_current_price()
        if USE_SIMULATOR and self.simulator:
            return self.simulator.get_current_price()
        else:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                price = ticker['last']
                return price if price and price > 0 else 3000.0
            except Exception as e:
                logging.error(f"Error fetching price for {symbol}: {e}")
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
        # CRITICAL: Get price for the POSITION pair (TRADOOR), NOT the global SYMBOL
        position_symbol = pos.get("symbol")
        if position_symbol:
            current_price = self.get_price_for_symbol(position_symbol)
        else:
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

    def place_market_order(self, side: str, amount_base: float, price_override: float = None, notional_amount: float = None):
        """
        side: 'buy' или 'sell' (для открытия позиции)
        amount_base: количество в базовой валюте (ETH)
        price_override: опциональная цена для установки (используется для TOP 1 гейнера)
        notional_amount: маржин-требование (если не передано, вычисляется как amount_base * price)
        """
        logging.info(f"[{self.now()}] PLACE MARKET ORDER -> side={side}, amount={amount_base:.6f}")
        
        if RUN_IN_PAPER or API_KEY == "" or API_SECRET == "":
            price = price_override if price_override is not None else self.get_current_price()
            entry_price = price
            entry_time = datetime.utcnow()
            # CRITICAL: Use passed notional (from compute_order_size_usdt), not recalculated
            notional = notional_amount if notional_amount is not None else (amount_base * entry_price)
            margin = notional / LEVERAGE
            
            state["available"] -= margin  # Deduct margin from available
            
            close_time_seconds = random.randint(MIN_RANDOM_TRADE_SECONDS, MAX_RANDOM_TRADE_SECONDS)
            
            if "telegram_trade_counter" not in state:
                state["telegram_trade_counter"] = 1
            else:
                state["telegram_trade_counter"] += 1
            trade_number = state["telegram_trade_counter"]
            
            state["in_position"] = True
            # Get TOP1 pair from top1_entry, fall back to SYMBOL if not available
            position_symbol = state.get("top1_entry", {}).get("pair", SYMBOL)
            state["position"] = {
                "position_id": str(uuid.uuid4()),  # Unique position ID for timer tracking
                "symbol": position_symbol,  # Position MUST be in TOP1 pair that was current at entry
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
                self.notifier.send_position_opened(state["position"], price, trade_number, state["balance"], position_symbol)
            
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
                    "position_id": str(uuid.uuid4()),  # Unique position ID for timer tracking
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
        
        # CRITICAL: Prevent duplicate closing of the same position
        position_id = state["position"].get("position_id")
        if position_id:
            # Check if this position was already closed
            for trade in state.get("trades", []):
                if trade.get("position_id") == position_id:
                    logging.warning(f"⚠️ Position {position_id} already closed, skipping duplicate close")
                    return None
            
        pos = state["position"]
        # CRITICAL: Get price for the POSITION pair (not the global SYMBOL)
        position_symbol = pos.get("symbol", SYMBOL)
        exit_price = self.get_price_for_symbol(position_symbol)
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
            "position_id": pos.get("position_id"),  # Add position_id to prevent duplicates
            "time": datetime.utcnow().isoformat(),
            "symbol": pos.get("symbol", "N/A"),
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
            self.notifier.send_position_closed(trade_record, trade_number, state["balance"], trade_record.get("symbol", SYMBOL))
        
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

    def get_1h_direction(self):
        """Получить направление SAR на 1h таймфрейме"""
        try:
            df = self.fetch_ohlcv_tf("1h", limit=50)
            if df is None or len(df) < 5:
                logging.warning("Could not fetch 1h OHLCV data - using default LONG")
                return "long"
            direction = self.get_direction_from_psar(df)
            logging.debug(f"1h direction determined: {direction}")
            return direction
        except Exception as e:
            logging.error(f"Error in get_1h_direction: {e}", exc_info=True)
            return "long"
    
    def get_direction(self, timeframe):
        """Get direction for any timeframe (1m, 5m, 15m, 30m, 1h)"""
        timeframe_lower = timeframe.lower()
        if timeframe_lower == "1m":
            return self.get_1m_direction()
        elif timeframe_lower == "5m":
            return self.get_5m_direction()
        elif timeframe_lower == "15m":
            return self.get_15m_direction()
        elif timeframe_lower == "30m":
            return self.get_30m_direction()
        elif timeframe_lower == "1h":
            return self.get_1h_direction()
        else:
            logging.warning(f"Unknown timeframe: {timeframe}")
            return "long"
    
    def get_strategy_config(self):
        """Get strategy config from Flask app"""
        try:
            if self.app_context:
                return self.app_context.get('strategy_config', {'open_levels': ['5m', '30m'], 'close_levels': ['5m']})
        except:
            pass
        return {'open_levels': ['5m', '30m'], 'close_levels': ['5m']}

    def strategy_loop(self, should_continue=None):
        """Dynamic strategy using configured open/close levels"""
        config = self.get_strategy_config()
        open_levels = config.get('open_levels', ['5m', '30m'])
        close_levels = config.get('close_levels', ['5m'])
        
        logging.info(f"🎯 Strategy OPEN on levels: {open_levels}")
        logging.info(f"🎯 Strategy CLOSE on levels: {close_levels}")
        
        last_level_directions = {}
        direction_check_interval = 5
        last_direction_check = 0
        
        while True:
            if should_continue and not should_continue():
                logging.info("Strategy loop stopped by external signal")
                break
            
            try:
                current_time = time.time()
                
                if current_time - last_direction_check >= direction_check_interval:
                    config = self.get_strategy_config()
                    open_levels = config.get('open_levels', ['5m', '30m'])
                    close_levels = config.get('close_levels', ['5m'])
                    
                    # Get current directions for all levels
                    current_directions = {}
                    for level in set(open_levels + close_levels):
                        current_directions[level] = self.get_direction(level)
                    
                    level_str = ", ".join([f"{k}:{v.upper()}" for k, v in current_directions.items()])
                    logging.info(f"SAR Levels: {level_str}")
                    
                    # Initialize last_level_directions on first run
                    if not last_level_directions:
                        last_level_directions = current_directions.copy()
                        logging.info(f"Initialized directions: {level_str}")
                    
                    # Check if ANY close_level changed -> CLOSE position
                    should_close = False
                    close_reason = ""
                    
                    # CRITICAL: Condition 0 - If position is open, check if ANY open_level diverged from opening direction
                    if state["in_position"]:
                        position_open_levels = state.get("position_open_levels", [])
                        position_open_directions = state.get("position_open_levels_directions", {})
                        
                        if position_open_levels and position_open_directions:
                            for level in position_open_levels:
                                original_direction = position_open_directions.get(level)
                                current_direction = current_directions.get(level)
                                
                                if original_direction and current_direction and original_direction != current_direction:
                                    logging.warning(f"🔴 STRATEGY DIVERGENCE: {level.upper()} was {original_direction.upper()} at OPEN, now {current_direction.upper()} - CLOSING!")
                                    should_close = True
                                    close_reason = f"{level}_diverged"
                                    break
                    
                    # Condition 1: Check if close_level (5m) changed direction
                    if not should_close:
                        for level in close_levels:
                            if level in current_directions and level in last_level_directions:
                                if current_directions[level] != last_level_directions[level]:
                                    logging.warning(f"⚠️ {level.upper()} SAR CHANGED: {last_level_directions[level].upper()} -> {current_directions[level].upper()}")
                                    should_close = True
                                    close_reason = "5m_changed"
                                    break
                    
                    # Condition 2: Check if 5m and 30m diverge (different directions)
                    if state["in_position"] and not should_close:
                        if '5m' in current_directions and '30m' in current_directions:
                            if current_directions['5m'] != current_directions['30m']:
                                logging.warning(f"⚠️ 5m ({current_directions['5m'].upper()}) != 30m ({current_directions['30m'].upper()}) - DIVERGENCE DETECTED")
                                should_close = True
                                close_reason = "5m_30m_diverge"
                    
                    # Debug logging
                    if should_close:
                        logging.info(f"🔍 CLOSE CHECK: should_close=True, reason={close_reason}, in_position={state.get('in_position')}")
                    
                    if should_close and state["in_position"]:
                        logging.info(f"🔴 CLOSING POSITION - Reason: {close_reason}")
                        self.close_position(close_reason=close_reason)
                        # Clear position tracking data
                        state["position_open_direction"] = None
                        state["position_open_levels"] = []
                        state["position_open_levels_directions"] = {}
                        time.sleep(1)
                    elif should_close and not state["in_position"]:
                        logging.warning(f"⚠️ Close signal triggered but NO POSITION open! (in_position={state.get('in_position')})")
                    
                    # Check if ALL open_levels aligned -> OPEN position
                    # Auto strategy works both in virtual and LIVE mode when API connected
                    if not state["in_position"]:
                        # CRITICAL: Validate all levels exist and have valid direction (not None)
                        valid_levels = all(level in current_directions and current_directions[level] in ['long', 'short'] for level in open_levels)
                        
                        if not valid_levels:
                            invalid_levels = [level for level in open_levels if level not in current_directions or current_directions.get(level) not in ['long', 'short']]
                            logging.warning(f"❌ CANNOT OPEN: Invalid/missing levels {invalid_levels}. Current: {current_directions}")
                            state["pending_signal_time"] = None
                            state["pending_signal_direction"] = None
                        else:
                            # Check if ALL open_levels are aligned (same direction)
                            all_aligned = all(current_directions.get(level) == current_directions.get(open_levels[0]) for level in open_levels)
                            
                            if all_aligned:
                                direction = current_directions.get(open_levels[0], "long")
                                # VALIDATION: Ensure direction is valid
                                if direction not in ['long', 'short']:
                                    logging.error(f"❌ INVALID DIRECTION: {direction}. Skipping position open.")
                                    state["pending_signal_time"] = None
                                else:
                                    # DOUBLE CONFIRMATION: Check if this is first signal or confirmation after 8 sec
                                    if state["pending_signal_time"] is None:
                                        # First signal detected - start waiting for confirmation
                                        state["pending_signal_time"] = current_time
                                        state["pending_signal_direction"] = direction
                                        state["pending_signal_levels"] = {level: current_directions[level] for level in open_levels}
                                        logging.info(f"🔔 SIGNAL DETECTED: {direction.upper()} ({','.join(open_levels)}) - Waiting 8 seconds for confirmation...")
                                    else:
                                        # Check if 8+ seconds have passed
                                        time_elapsed = current_time - state["pending_signal_time"]
                                        if time_elapsed >= 8:
                                            # 8 seconds passed - RECONFIRM signal
                                            confirmed = True
                                            
                                            # Check if direction is still the SAME
                                            if direction != state["pending_signal_direction"]:
                                                logging.warning(f"❌ SIGNAL CHANGED: Was {state['pending_signal_direction'].upper()}, now {direction.upper()} - REJECTING")
                                                confirmed = False
                                            
                                            # Check if all levels still aligned
                                            if confirmed:
                                                for level in open_levels:
                                                    if current_directions.get(level) != state["pending_signal_levels"].get(level):
                                                        logging.warning(f"❌ LEVEL DIVERGED: {level} was {state['pending_signal_levels'].get(level)}, now {current_directions.get(level)} - REJECTING")
                                                        confirmed = False
                                                        break
                                            
                                            if confirmed:
                                                # DOUBLE CONFIRMATION PASSED - OPEN POSITION
                                                from app import api_connected_global
                                                trade_side = "buy" if direction == "long" else "sell"
                                                balance_type = "REAL Gate.io" if api_connected_global else "VIRTUAL"
                                                logging.info(f"✅ DOUBLE CONFIRMATION PASSED! OPENING {direction.upper()} POSITION ON {balance_type} BALANCE")
                                                # CRITICAL: Store opening direction and levels for position validation
                                                state["position_open_direction"] = direction
                                                state["position_open_levels"] = open_levels.copy()
                                                state["position_open_levels_directions"] = {level: current_directions[level] for level in open_levels}
                                                price = self.get_current_price()
                                                amount, notional = self.compute_order_size_usdt(state["available"], price)
                                                # CRITICAL: Get FRESH TOP1 data from top_gainers_cache BEFORE opening position
                                                from app import top_gainers_cache
                                                if top_gainers_cache['data'] and len(top_gainers_cache['data']) > 0:
                                                    top1_fresh = top_gainers_cache['data'][0]
                                                    state["current_top1"] = {"pair": top1_fresh.get('symbol', 'TOP1'), "price": float(top1_fresh.get('price', 0))}
                                                    logging.info(f"🔄 Updated TOP1 to {state['current_top1']['pair']} @ ${state['current_top1']['price']:.6f}")
                                                state["top1_entry"] = state.get("current_top1", {})
                                                self.place_market_order(trade_side, amount, notional_amount=notional)
                                                self.save_state_to_file()
                                                logging.info(f"✅ AUTO TRADE executed on {balance_type} balance: {trade_side.upper()}")
                                                # Reset pending signal
                                                state["pending_signal_time"] = None
                                                state["pending_signal_direction"] = None
                                            else:
                                                # Confirmation FAILED - reset and wait for new signal
                                                state["pending_signal_time"] = None
                                                state["pending_signal_direction"] = None
                    
                    last_level_directions = current_directions.copy()
                    last_direction_check = current_time
                
            except Exception as e:
                logging.error(f"Strategy loop error: {e}", exc_info=True)
            
            time.sleep(5)
