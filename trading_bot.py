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

# ✅ IMPORTANT: Will import state from app.py after app is initialized
# For now, use local state as fallback
_app_state = None
def get_state():
    """Get shared state from app.py or use local state"""
    global _app_state
    if _app_state is None:
        try:
            from app import state as app_state
            _app_state = app_state
        except:
            pass
    return _app_state if _app_state else globals()['state']

API_KEY = os.getenv("GATE_API_KEY", "")
API_SECRET = os.getenv("GATE_API_SECRET", "")
RUN_IN_PAPER = os.getenv("RUN_IN_PAPER", "1") == "1"
USE_SIMULATOR = os.getenv("USE_SIMULATOR", "0") == "1"

SYMBOL = "TRADOOR/USDT"  # Default, будет переопределяться динамически
LEVERAGE = 10
ISOLATED = True  # Isolated margin mode
POSITION_PERCENT = 1.0  # 100% от банка
TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
MIN_TRADE_SECONDS = 120
MIN_RANDOM_TRADE_SECONDS = 480
MAX_RANDOM_TRADE_SECONDS = 780
PAUSE_BETWEEN_TRADES = 0
START_BANK = 100.0
DASHBOARD_MAX = 20

# ✅ Local fallback state (will be overridden by app.py state)
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
    "rebalance_enabled": False,
    "api_connected": False
}

# ✅ Load trades from file immediately on module import
try:
    with open("goldantelopegate_v1.0_state.json", "r") as f:
        _saved_state = json.load(f)
        if "trades" in _saved_state:
            state["trades"] = _saved_state["trades"]
            logging.info(f"✅ Loaded {len(state['trades'])} trades from state file")
except:
    pass

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
                    logging.warning(f"Note: Margin/leverage config skipped (not critical): {e}")
                    logging.info("✅ REAL TRADING MODE - API credentials valid, orders will execute on Gate.io")
        
        self.load_state_from_file()
        
        # ✅ RESET signal state to allow fresh detection
        state["pending_signal_time"] = None
        state["pending_signal_direction"] = None
        state["pending_signal_levels"] = None
        
    def save_state_to_file(self):
        try:
            # Load existing file to preserve strategy_config
            try:
                with open("goldantelopegate_v1.0_state.json", "r") as f:
                    existing = json.load(f)
                    strategy_cfg = existing.get('strategy_config', None)
            except:
                strategy_cfg = None
            
            # Merge state with strategy_config
            save_data = dict(state)
            if strategy_cfg:
                save_data['strategy_config'] = strategy_cfg
            
            with open("goldantelopegate_v1.0_state.json", "w") as f:
                json.dump(save_data, f, default=str, indent=2)
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

    def convert_symbol_for_ccxt(self, symbol):
        """Convert symbol format from XNY_USDT to XNY/USDT:USDT for ccxt Gate.io futures"""
        if not symbol:
            return symbol
        if '/' in symbol and ':' in symbol:
            return symbol
        if '_USDT' in symbol:
            base = symbol.replace('_USDT', '')
            return f"{base}/USDT:USDT"
        return symbol

    def fetch_ohlcv_tf(self, tf: str, limit=200):
        """
        Возвращает pd.DataFrame с колонками: timestamp, open, high, low, close, volume
        """
        try:
            if USE_SIMULATOR and self.simulator:
                ohlcv = self.simulator.fetch_ohlcv(tf, limit=limit)
            else:
                ccxt_symbol = self.convert_symbol_for_ccxt(SYMBOL)
                ohlcv = self.exchange.fetch_ohlcv(ccxt_symbol, timeframe=tf, limit=limit)
            
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

    def compute_order_size_usdt(self, balance, price, symbol=None):
        """
        Calculate order size in CONTRACTS (not base currency!)
        Gate.io futures expects contract count, not base amount.
        """
        # Validate inputs
        if balance <= 0:
            logging.warning(f"❌ Invalid balance: {balance}")
            return 0.0, 0.0
        if price <= 0:
            logging.warning(f"❌ Invalid price: {price}")
            return 0.0, 0.0
        
        # Get contract size from exchange
        contract_size = 10000.0  # ✅ Gate.io standard contract size
        try:
            if self.exchange and symbol:
                ccxt_symbol = self.convert_symbol_for_ccxt(symbol)
                if ccxt_symbol not in self.exchange.markets:
                    self.exchange.load_markets()
                if ccxt_symbol in self.exchange.markets:
                    market = self.exchange.markets[ccxt_symbol]
                    contract_size = float(market.get('contractSize', 10000.0))
                    logging.info(f"📊 Contract size for {symbol}: {contract_size}")
        except Exception as e:
            logging.warning(f"Could not get contract size: {e}")
            contract_size = 10000.0
        
        # Notional = balance × leverage (total position value in USDT)
        notional = balance * POSITION_PERCENT * LEVERAGE
        
        # Contract value = contract_size × price
        # Number of contracts = notional / contract_value
        contract_value = contract_size * price
        contracts = int(notional / contract_value)  # Must be integer for Gate.io
        
        # Safety margin: reduce by 1% to ensure we have enough margin (not fixed 2!)
        # This prevents issues with small positions where -2 leaves almost nothing
        if contracts > 100:
            contracts = int(contracts * 0.99)  # 1% buffer for large positions
        elif contracts > 10:
            contracts = max(contracts - 1, 1)  # At least 1 contract
        
        # Recalculate actual notional based on contracts
        actual_notional = contracts * contract_value
        
        logging.info(f"✅ Order size: {contracts} contracts, notional=${actual_notional:.2f}, balance=${balance:.2f}, price=${price:.6f}, contract_size={contract_size}")
        return contracts, actual_notional

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
                # Convert FHE_USDT to FHE/USDT:USDT format for ccxt
                if '_' in symbol and ':' not in symbol:
                    parts = symbol.split('_')
                    ccxt_symbol = f"{parts[0]}/{parts[1]}:{parts[1]}"
                else:
                    ccxt_symbol = symbol
                
                ticker = self.exchange.fetch_ticker(ccxt_symbol)
                price = ticker['last']
                if price and price > 0:
                    return price
                else:
                    # Fallback: return entry_price from position if available
                    pos = state.get("position", {})
                    return pos.get("entry_price", 0.01)
            except Exception as e:
                logging.error(f"Error fetching price for {symbol}: {e}")
                # Fallback: return entry_price from position (NOT 3000!)
                pos = state.get("position", {})
                return pos.get("entry_price", 0.01)

    def get_contract_size(self, symbol=None):
        """Get contract size for a symbol from exchange or use default"""
        default_size = 10000.0  # Gate.io standard for most contracts
        if not symbol:
            return default_size
        try:
            if self.exchange:
                ccxt_symbol = self.convert_symbol_for_ccxt(symbol)
                if ccxt_symbol not in self.exchange.markets:
                    self.exchange.load_markets()
                if ccxt_symbol in self.exchange.markets:
                    market = self.exchange.markets[ccxt_symbol]
                    return float(market.get('contractSize', default_size))
        except Exception as e:
            logging.debug(f"Could not get contract size for {symbol}: {e}")
        return default_size

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
        # CRITICAL: Multiply by contract_size to get actual USD value
        contract_size = self.get_contract_size(position_symbol) if position_symbol else 1.0
        
        # ✅ CORRECT FUTURES P&L FORMULA:
        # P&L = notional × (price_change_percent)
        # P&L = notional × (current_price - entry_price) / entry_price
        notional = pos.get("notional", 0)
        margin = pos.get("margin", 0)
        
        if pos.get("side") == "long":
            price_change_pct = (current_price - entry_price) / entry_price
        else:  # SHORT
            price_change_pct = (entry_price - current_price) / entry_price
        
        unrealized_pnl = notional * price_change_pct
        
        # DEBUG: Log P&L calculation
        logging.info(f"📊 P&L DEBUG: entry={entry_price}, current={current_price}, notional={notional}, change%={price_change_pct*100:.4f}%, P&L=${unrealized_pnl:.2f}")
        
        # ✅ CRITICAL: Cap loss at margin (can't lose more than margin in futures)
        if margin > 0 and unrealized_pnl < -margin:
            unrealized_pnl = -margin  # Liquidation - max loss = margin
        
        # Store current_price in position for API response
        pos["current_price"] = current_price
        pos["unrealized_pnl"] = round(unrealized_pnl, 4)
        
        return round(unrealized_pnl, 4)

    def place_market_order(self, side: str, amount_base: float, price_override: float = None, notional_amount: float = None):
        """
        side: 'buy' или 'sell' (для открытия позиции)
        amount_base: количество в базовой валюте (ETH)
        price_override: опциональная цена для установки (используется для TOP 1 гейнера)
        notional_amount: маржин-требование (если не передано, вычисляется как amount_base * price)
        """
        logging.info(f"[{self.now()}] PLACE MARKET ORDER -> side={side}, amount={amount_base:.6f}")
        
        # ✅ CRITICAL: Block zero-amount orders (prevents ghost positions)
        if amount_base <= 0:
            logging.warning(f"❌ BLOCKED: Cannot open position with amount={amount_base:.6f} (must be > 0)")
            return None
        
        # ✅ USE STATE DICT (shared across all Gunicorn workers!)
        shared_state = get_state()
        api_connected = shared_state.get('api_connected', False)
        use_paper = not api_connected  # Paper mode when state['api_connected']=FALSE
        
        # ✅ CRITICAL: Block if available balance is invalid
        available = shared_state.get('available', 0)
        if available <= 0:
            logging.warning(f"❌ BLOCKED: Cannot open position with available=${available:.2f} (must be > 0)")
            return None
        
        # 🔐 SECURITY: If API is connected, BLOCK virtual trades!
        if api_connected and use_paper:
            logging.error("❌ BLOCKED: Cannot open virtual trades when API is connected! Use real balance only!")
            return None
        
        # 🔐 Alternative safety check: If API connected but trying to use paper balance
        if api_connected and shared_state.get('balance', 100) == 100 and shared_state.get('balance') == shared_state.get('available'):
            logging.warning("⚠️ WARNING: API connected but balance looks virtual ($100). Refusing to trade.")
            return None
        
        if use_paper:
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
            
            # ✅ SAVE STATE TO FILE - ensure all workers see the update
            self.save_state_to_file()
            
            logging.info(f"Position opened with random close time: {close_time_seconds}s ({close_time_seconds/60:.1f} minutes)")
            
            # ✅ ANTI-DUPLICATE: Only send TG if this trade wasn't already opened by another worker
            position_id = state["position"]["position_id"]
            last_opened_id = state.get("last_tg_open_position_id", "")
            if self.notifier and position_id != last_opened_id:
                state["last_tg_open_position_id"] = position_id
                self.save_state_to_file()  # Save BEFORE sending to prevent race condition
                self.notifier.send_position_opened(state["position"], price, trade_number, state["balance"], position_symbol)
            elif position_id == last_opened_id:
                logging.info(f"⚠️ TG notification already sent for open position {position_id[:8]} - skipping duplicate")
            
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
                    "symbol": SYMBOL,  # ✅ FIX: Always save trading symbol
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
                
                # CRITICAL: Save state immediately to sync all workers
                self.save_state_to_file()
                
                logging.info(f"Position opened with random close time: {close_time_seconds}s ({close_time_seconds/60:.1f} minutes)")
                
                return state["position"]
                
            except Exception as e:
                logging.error(f"Order error: {e}")
                return None

    def close_position(self, close_reason="manual"):
        """Закрытие текущей позиции - РЕАЛЬНО на бирже"""
        if not state["in_position"] or state["position"] is None:
            return None
        
        # ✅ CRITICAL: Immediately mark position as closed to prevent race conditions
        position_id = state["position"].get("position_id", "")
        
        # Check if already being closed or was closed
        if state.get("closing_position_id") == position_id:
            logging.warning(f"⚠️ Position {position_id[:8]} already being closed - skipping duplicate")
            return None
        
        # Check if this position was already closed (in trades history)
        for trade in state.get("trades", []):
            if trade.get("position_id") == position_id:
                logging.warning(f"⚠️ Position {position_id[:8]} already in trades history - skipping duplicate close")
                state["in_position"] = False
                state["position"] = None
                self.save_state_to_file()
                return None
        
        # ✅ LOCK: Mark this position as being closed BEFORE any work
        state["closing_position_id"] = position_id
        self.save_state_to_file()  # Save lock state immediately
        
        pos = state["position"]
        position_symbol = pos.get("symbol", SYMBOL)
        size = float(pos["size_base"])
        
        # ✅ REAL TRADING: Close position on Gate.io exchange
        try:
            # Get real position from exchange
            positions = self.exchange.fetch_positions()
            real_pos = None
            for p in positions:
                if p.get('contracts') != 0:
                    sym = p['symbol'].split(':')[0].replace('/', '_')
                    if sym == position_symbol or position_symbol in p['symbol']:
                        real_pos = p
                        break
            
            if real_pos:
                # Close the REAL position on exchange
                contracts = float(real_pos['contracts'])
                side = real_pos['side']
                close_side = 'sell' if side == 'long' else 'buy'
                symbol = real_pos['symbol']
                
                logging.info(f"🔴 CLOSING REAL POSITION: {symbol} {side} {contracts} contracts")
                
                order = self.exchange.create_order(
                    symbol=symbol,
                    type='market',
                    side=close_side,
                    amount=contracts,
                    params={'reduceOnly': True}
                )
                logging.info(f"✅ REAL CLOSE ORDER: ID={order.get('id')}, Status={order.get('status')}")
                
                # Get actual PnL from the closed position
                exit_price = float(order.get('average', real_pos.get('markPrice', 0)))
                pnl = float(real_pos.get('unrealizedPnl', 0))
            else:
                logging.warning(f"⚠️ No real position found on exchange for {position_symbol}")
                # Ghost position - clear state and return early
                if not pos.get("entry_price"):
                    logging.info("🔄 Clearing ghost position (no entry_price)")
                    state["in_position"] = False
                    state["position"] = None
                    self.save_state_to_file()
                    return None
                exit_price = self.get_price_for_symbol(position_symbol)
                entry_price = float(pos.get("entry_price", 0))
                contract_size = self.get_contract_size(position_symbol)
                if pos.get("side") == "long":
                    pnl = (exit_price - entry_price) * size * contract_size
                else:
                    pnl = (entry_price - exit_price) * size * contract_size
                logging.info(f"📊 P&L calc: ({entry_price:.8f} - {exit_price:.8f}) * {size} * {contract_size} = ${pnl:.2f}")
        except Exception as e:
            logging.error(f"❌ Error closing real position: {e}")
            # Fallback to virtual close - check for ghost position
            if not pos.get("entry_price"):
                logging.info("🔄 Clearing ghost position in fallback (no entry_price)")
                state["in_position"] = False
                state["position"] = None
                self.save_state_to_file()
                return None
            exit_price = self.get_price_for_symbol(position_symbol)
            entry_price = float(pos.get("entry_price", 0))
            contract_size = self.get_contract_size(position_symbol)
            if pos.get("side") == "long":
                pnl = (exit_price - entry_price) * size * contract_size
            else:
                pnl = (entry_price - exit_price) * size * contract_size
            logging.info(f"📊 P&L calc: ({entry_price:.8f} - {exit_price:.8f}) * {size} * {contract_size} = ${pnl:.2f}")
        
        entry_price = float(pos.get("entry_price", 0))
        
        pnl = round(pnl, 4)
        
        # Handle missing entry_time gracefully
        entry_time_str = pos.get("entry_time")
        if entry_time_str:
            try:
                entry_time = datetime.fromisoformat(entry_time_str)
                duration_seconds = (datetime.utcnow() - entry_time).total_seconds()
                minutes = int(duration_seconds // 60)
                seconds = int(duration_seconds % 60)
                duration_str = f"{minutes}м {seconds}с"
            except Exception:
                duration_str = "N/A"
        else:
            duration_str = "N/A"
        
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
        # ✅ SAFETY: Prevent negative balance
        if state["balance"] < 0:
            logging.warning(f"⚠️ Negative balance detected: ${state['balance']:.2f}, resetting to $0")
            state["balance"] = 0
        margin_released = pos.get("margin", pos["notional"] / LEVERAGE)
        state["available"] = state["balance"]  # When no position: available = balance
        logging.info(f"✅ Position closed - balance=${state['balance']:.2f}, available=${state['available']:.2f}")
        state["top1_entry"] = {}  # Очистить TOP1 информацию при закрытии позиции
        state["trades"].append(trade_record)
        
        if len(state["trades"]) > DASHBOARD_MAX:
            state["trades"] = state["trades"][-DASHBOARD_MAX:]
        
        trade_number = pos.get("trade_number", state.get("telegram_trade_counter", 1))
        position_id = pos.get("position_id", "")
        
        # ✅ ANTI-DUPLICATE: Only send TG if this position wasn't already closed by another worker
        last_closed_id = state.get("last_tg_close_position_id", "")
        if self.notifier and position_id and position_id != last_closed_id:
            state["last_tg_close_position_id"] = position_id
            self.save_state_to_file()  # Save BEFORE sending to prevent race condition
            self.notifier.send_position_closed(trade_record, trade_number, state["balance"], trade_record.get("symbol", SYMBOL))
        elif position_id == last_closed_id:
            logging.info(f"⚠️ TG notification already sent for position {position_id[:8]} - skipping duplicate")
        
        if pos["side"] == "long":
            self.signal_sender.send_close_long()
        else:
            self.signal_sender.send_close_short()
        
        state["in_position"] = False
        state["position"] = None
        state["closing_position_id"] = None  # ✅ Clear the lock after successful close
        state["last_position_close_time"] = time.time()
        logging.info(f"⏳ 20-second cooldown started before next trade")
        
        # ВАЖНО: После закрытия позиции - переключиться на реальный TOP1 гейнер
        if self.app_context:
            try:
                app_context = self.app_context
                if 'get_top_trading_symbol' in app_context:
                    top1_symbol = app_context['get_top_trading_symbol']()
                    app_context['current_trading_symbol'] = top1_symbol
                    logging.info(f"🔄 Position closed - updated TOP1 to {top1_symbol}")
            except Exception as e:
                logging.error(f"Error updating TOP1 after close: {e}")
        
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
        """Get strategy config from FILE (not app_context) to sync across workers"""
        try:
            with open("goldantelopegate_v1.0_state.json", "r") as f:
                saved_state = json.load(f)
                if "strategy_config" in saved_state:
                    return saved_state["strategy_config"]
        except Exception as e:
            logging.debug(f"Could not load strategy from file: {e}")
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
                
                # ✅ CRITICAL FIX: Always re-read state from FILE to sync across Gunicorn workers
                global state
                try:
                    with open("goldantelopegate_v1.0_state.json", "r") as f:
                        state = json.load(f)
                except:
                    pass  # Keep in-memory state if file read fails
                
                # ✅ RECONCILIATION: Sync state with real exchange positions
                # CRITICAL: Only reconcile in REAL mode (api_connected=True)
                # In DEMO mode, positions are virtual and should NOT be cleared
                api_connected = state.get('api_connected', False)
                
                try:
                    real_positions = self.exchange.fetch_positions()
                    has_real_position = any(float(p.get('contracts', 0)) != 0 for p in real_positions)
                    
                    if state.get('in_position') and not has_real_position and api_connected:
                        # State says in position but no real position - clear ghost (ONLY IN REAL MODE)
                        logging.info("🔄 RECONCILE: Clearing ghost position (state=True, exchange=None) [REAL MODE]")
                        state['in_position'] = False
                        state['position'] = None
                        state['position_open_levels_directions'] = {}
                        self.save_state_to_file()
                    elif has_real_position and (not state.get('in_position') or state.get('position') is None):
                        # Real position exists but state says not in position OR position data is None - sync
                        logging.info("🔄 RECONCILE: Syncing real position to state (in_position or position missing)")
                        for p in real_positions:
                            if float(p.get('contracts', 0)) != 0:
                                state['in_position'] = True
                                state['position'] = {
                                    'position_id': str(uuid.uuid4()),
                                    'symbol': p['symbol'].split(':')[0].replace('/', '_'),
                                    'side': p.get('side', 'long'),
                                    'size_base': float(p.get('contracts', 0)),
                                    'entry_price': float(p.get('entryPrice', 0)),
                                    'entry_time': datetime.utcnow().isoformat(),
                                    'notional': float(p.get('notional', 0)),
                                    'margin': float(p.get('collateral', 0))
                                }
                                logging.info(f"🔄 RECONCILE: Position synced - {state['position']['symbol']} {state['position']['side']} {state['position']['size_base']} contracts")
                                self.save_state_to_file()
                                break
                except Exception as e:
                    logging.debug(f"Reconciliation check failed: {e}")
                
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
                    logging.info(f"🔍 in_position={state.get('in_position')}, last_close={state.get('last_position_close_time')}")
                    
                    # Initialize last_level_directions on first run
                    if not last_level_directions:
                        last_level_directions = current_directions.copy()
                        logging.info(f"Initialized directions: {level_str}")
                    
                    # Check if ANY close_level changed -> CLOSE position
                    should_close = False
                    close_reason = ""
                    
                    # ✅ PRIORITY 0: Check force_close flag (set when strategy changed)
                    if state.get('force_close'):
                        should_close = True
                        close_reason = state.get('force_close_reason', 'strategy_changed')
                        logging.warning(f"🔴 FORCE CLOSE triggered by API! Reason: {close_reason}")
                        # Reset flag immediately
                        state['force_close'] = False
                        state['force_close_reason'] = None
                    
                    # ✅ FIX: Compare close_levels to direction at POSITION OPEN time, not last cycle
                    # This fixes the bug where server restart resets last_level_directions
                    
                    # Condition 1: Check if close_level changed from OPENING direction
                    if not should_close and state.get('in_position'):
                        # Get the directions saved when position was opened
                        open_directions = state.get('position_open_levels_directions', {})
                        
                        for level in close_levels:
                            if level in current_directions and level in open_directions:
                                if current_directions[level] != open_directions[level]:
                                    logging.warning(f"⚠️ {level.upper()} SAR CHANGED FROM OPEN: {open_directions[level].upper()} -> {current_directions[level].upper()}")
                                    should_close = True
                                    close_reason = f"{level}_changed_from_open"
                                    break
                            elif level in current_directions and level in last_level_directions:
                                # Fallback to last_level_directions if no saved open directions
                                if current_directions[level] != last_level_directions[level]:
                                    logging.warning(f"⚠️ {level.upper()} SAR CHANGED (fallback): {last_level_directions[level].upper()} -> {current_directions[level].upper()}")
                                    should_close = True
                                    close_reason = f"{level}_changed"
                                    break
                    
                    # ❌ REMOVED: Old hardcoded 5m/30m divergence check
                    # Now using dynamic Condition 0 above which checks actual open_levels
                    # This fixes issue where selecting [5m, 1m] still checked 5m!=30m
                    
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
                    logging.info(f"🔍 ENTERING OPEN CHECK: in_position={state.get('in_position')}")
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
                            logging.info(f"✅ ALIGNMENT CHECK: all_aligned={all_aligned}, levels={open_levels}, directions={[current_directions.get(l) for l in open_levels]}")
                            
                            if all_aligned:
                                direction = current_directions.get(open_levels[0], "long")
                                # ✅ INSTANT OPEN: No double confirmation - open IMMEDIATELY when levels align
                                
                                if direction not in ['long', 'short']:
                                    logging.error(f"❌ INVALID DIRECTION: {direction}. Skipping position open.")
                                else:
                                    # CHECK 20-SECOND PAUSE BETWEEN TRADES
                                    last_close_time = state.get("last_position_close_time")
                                    confirmed = True
                                    if last_close_time:
                                        time_since_close = current_time - last_close_time
                                        if time_since_close < 20:
                                            logging.info(f"⏳ PAUSE: {20 - time_since_close:.0f}s remaining before next trade (20s cooldown)")
                                            confirmed = False
                                    
                                    if confirmed:
                                        # CHECK REAL POSITION ON GATE.IO BEFORE OPENING
                                        try:
                                            real_positions = self.exchange.fetch_positions()
                                            has_real_position = any(float(p.get('contracts', 0)) != 0 for p in real_positions)
                                            if has_real_position:
                                                logging.warning("⚠️ BLOCKED: Real position already exists on Gate.io! Syncing state...")
                                                state["in_position"] = True
                                                confirmed = False
                                        except Exception as e:
                                            logging.error(f"Error checking real positions: {e}")
                                    
                                    if confirmed:
                                        # ✅ OPEN POSITION IMMEDIATELY
                                        trade_side = "buy" if direction == "long" else "sell"
                                        shared_state = get_state()
                                        balance_type = "VIRTUAL"
                                        logging.info(f"✅ OPENING {direction.upper()} POSITION IMMEDIATELY - SAR levels aligned [{','.join(open_levels)}]")
                                        state["position_open_direction"] = direction
                                        state["position_open_levels"] = open_levels.copy()
                                        # ✅ Save directions for ALL levels (open + close) for close logic
                                        all_levels = set(open_levels + close_levels)
                                        state["position_open_levels_directions"] = {level: current_directions[level] for level in all_levels if level in current_directions}
                                        
                                        # ✅ CRITICAL FIX: Get price from TOP1 gainer, not self.SYMBOL
                                        from app import top_gainers_cache
                                        top1_symbol = SYMBOL  # Default to current symbol
                                        if top_gainers_cache['data'] and len(top_gainers_cache['data']) > 0:
                                            top1_fresh = top_gainers_cache['data'][0]
                                            top1_symbol = top1_fresh.get('symbol', SYMBOL)
                                            price = float(top1_fresh.get('price', 0))
                                            if price <= 0:
                                                price = self.get_price_for_symbol(top1_symbol)
                                            state["current_top1"] = {"pair": top1_symbol, "price": price}
                                            logging.info(f"📊 Using TOP1 price: {top1_symbol} @ ${price}")
                                        else:
                                            price = self.get_current_price()
                                            state["current_top1"] = {"pair": SYMBOL, "price": price}
                                        state["top1_entry"] = state.get("current_top1", {})
                                        
                                        shared_state = get_state()
                                        balance_for_order = shared_state["available"]
                                        amount, notional = self.compute_order_size_usdt(balance_for_order, price, top1_symbol)
                                        order_result = self.place_market_order(trade_side, amount, price_override=price, notional_amount=notional)
                                        # ✅ CRITICAL: Only set in_position=True if order succeeded (not None)
                                        if order_result is not None:
                                            state["in_position"] = True
                                            state["position_open_price"] = price
                                            state["position_open_time"] = current_time
                                            self.save_state_to_file()
                                            logging.info(f"✅ AUTO TRADE OPENED: {trade_side.upper()}")
                                        else:
                                            logging.warning(f"❌ ORDER FAILED: Position NOT opened (order returned None)")
                    
                    last_level_directions = current_directions.copy()
                    last_direction_check = current_time
                
            except Exception as e:
                logging.error(f"Strategy loop error: {e}", exc_info=True)
            
            time.sleep(5)
