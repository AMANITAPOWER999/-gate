# Goldantelopegate_v1.0 - TOP 1 Gainer Trading Bot

## Overview

This is a Python-based cryptocurrency trading bot for Gate.io exchange with x10 leverage. The bot dynamically selects the #1 top gainer from 584 futures pairs and trades it using a 5m/30m SAR alignment strategy. Executes **LONG and SHORT positions** with real market data in paper trading mode. Features Flask web dashboard with real-time chart visualization and Telegram notifications.

## System Architecture

The application follows a modular architecture with clear separation of concerns:

- **Flask Web Application**: Dashboard for monitoring and controlling the trading bot
- **Trading Bot Core**: Implements SAR strategy with x10 leverage (configurable timeframes)
- **Market Simulator**: Testing environment for strategy validation
- **Telegram Integration**: Real-time notifications and WebApp interface
- **Signal Sender**: External webhook integration for automated trading

## Key Components

### Core Files

1. **app.py** - Flask web application and API endpoints
   - Dashboard routes (`/`, `/webapp`)
   - API endpoints for bot control (`/api/start_bot`, `/api/stop_bot`, `/api/open_long`, `/api/open_short`, etc.)
   - Status and monitoring endpoints
   - Balance management (virtual $100 / real balance switching)

2. **trading_bot.py** - Core trading logic
   - SAR strategy implementation (dynamic open/close levels - default 5m/30m)
   - Position management (open/close)
   - OHLCV data fetching and analysis
   - Paper trading and live trading modes
   - **INSTANT position opening** when SAR levels align (no confirmation delay)
   - **Cross-worker state synchronization** - reads state from file every cycle (Gunicorn support)

3. **market_simulator.py** - Market simulation for testing
   - Generates realistic price movements
   - Produces OHLCV data for backtesting
   - Configurable volatility

4. **telegram_notifications.py** - Telegram bot integration
   - Position opened/closed notifications
   - Balance and P&L updates
   - Subscriber management

5. **signal_sender.py** - External signal dispatch
   - Webhook integration for automated trading
   - LONG/SHORT signal sending

### Frontend Files

- **templates/dashboard.html** - Main web dashboard
- **templates/webapp.html** - Telegram WebApp interface
- **static/css/dashboard.css** - Dashboard styling
- **static/js/dashboard.js** - Dashboard JavaScript logic with strategy controls

### State Files

- **goldantelopegate_v1.0_state.json** - Trading state (balance, positions, trades)

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| GATE_API_KEY | Gate.io API key | - |
| GATE_API_SECRET | Gate.io API secret | - |
| RUN_IN_PAPER | Paper trading mode (1=on, 0=off) | 1 |
| USE_SIMULATOR | Use market simulator (1=on, 0=off) | 0 |
| TELEGRAM_BOT_TOKEN | Telegram bot token | - |
| TELEGRAM_CHAT_ID | Telegram chat ID | - |
| DASHBOARD_PASSWORD | Dashboard password | admin |
| SESSION_SECRET | Flask session secret | auto-generated |

### Trading Parameters

- **Leverage**: x10 ✅
- **Position Size**: 100% of available balance (Balance × x10 notional)
- **Symbol**: Dynamic TOP 1 gainer from Gate.io
- **Primary Strategy**: User-configurable SAR alignment (default: 5m/30m for open, 5m for close)
- **Entry Type**: **INSTANT opening** when selected timeframes align

## Strategy

### Dynamic SAR Trading Logic (Configurable Levels 1-15)

**ENTRY RULES:**
- Position opens when **ALL selected OPEN levels have SAME SAR direction**
  - Default: [5m, 30m] alignment
  - User can select strategies 1-15 via dashboard
  - Checks every 5 seconds
  - **INSTANT opening** - no confirmation delay, position opens immediately
  - Opens BOTH LONG and SHORT positions (whichever direction signals align)

**EXIT RULES:**
- Position closes when **ANY CLOSE level SAR changes direction**
  - Default: Close when 5m SAR changes
  - Automatic re-entry: When OPEN levels align again

**Manual Entry (DEMO Mode):**
- **OPEN LONG** button ✅ Active in DEMO mode (virtual $100 balance)
- **OPEN SHORT** button ✅ Active in DEMO mode (virtual $100 balance)
- Both buttons automatically disable when API is connected (REAL mode)

**Position Type:**
- **BOTH LONG and SHORT positions** enabled
- Leverage: **x10**
- Position Size: **100% of available balance (Balance × x10 notional)**
- Trading Symbol: **Dynamic TOP 1 gainer** from Gate.io (updated every 60 seconds, FROZEN during position)

## Balance Management

### Virtual vs Real Balance Logic

**Startup State (DEMO Mode):**
- balance = $100 virtual
- available = $100 virtual
- Shows virtual balance on dashboard
- OPEN LONG / OPEN SHORT buttons **ENABLED**

**After "Connect API" button (REAL Mode):**
- User stores API credentials in session
- balance = Real Gate.io futures balance
- available = Real Gate.io futures balance
- OPEN LONG / OPEN SHORT buttons **DISABLED** (only auto-trading enabled)
- Uses real margin for trades

**Auto-Authentication (Background):**
- On app startup, API credentials from environment are validated
- state['api_connected'] flag set to FALSE for background operations (safety)
- state['balance'] and state['available'] remain at $100 virtual
- Only explicit "Connect API" button click switches to real balance

## Dashboard Controls

### Strategy Configuration
- **Strategies 1-15**: User-selectable SAR level combinations
- **OPEN Levels**: Which timeframes must align to open (default: 5m, 30m)
- **CLOSE Levels**: Which timeframes must diverge to close (default: 5m)
- **Leverage**: 3x, 5x, 10x (default: 10x)

### Buttons & Modes
| Button | DEMO Mode | REAL Mode | Function |
|--------|-----------|-----------|----------|
| Start Bot | ✅ | ✅ | Start automatic trading |
| Stop Bot | ✅ | ✅ | Stop automatic trading |
| **Open Long** | ✅ **ACTIVE** | ❌ Disabled | Manual LONG entry |
| **Open Short** | ✅ **ACTIVE** | ❌ Disabled | Manual SHORT entry |
| Close Position | ✅ | ✅ | Force close position |
| Delete Trade | ✅ | ✅ | Remove last trade |
| Reset Balance | ✅ | ❌ Disabled | Reset to $100 virtual |
| DEMO / REAL | ✅ Toggle | ✅ Toggle | Switch mode |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Get bot status, balance, positions |
| `/api/start_bot` | POST | Start trading bot |
| `/api/stop_bot` | POST | Stop trading bot |
| `/api/open_long` | POST | Manual LONG position (DEMO mode) |
| `/api/open_short` | POST | Manual SHORT position (DEMO mode) |
| `/api/close_position` | POST | Force close current position |
| `/api/delete_last_trade` | POST | Delete last trade record |
| `/api/reset_balance` | POST | Reset balance to $100 |
| `/api/chart_data` | GET | Get chart data with markers |
| `/api/debug_sar` | GET | SAR indicator debug info |
| `/api/top_gainers` | GET | Get top 584 Gate.io futures gainers |

## Deployment

### Running Locally
```bash
python app.py
```

### Dashboard Access
- Web Dashboard: `http://localhost:5000/`
- Telegram WebApp: `http://localhost:5000/webapp`

## Recent Changes

- **December 7, 2025 - P&L CALCULATION FIX (v2.21):**
  - ✅ **Fixed P&L Formula**: Now uses correct futures formula: `P&L = notional × (price_change%)`
  - ✅ **Fixed Entry Price Bug**: Entry price now taken from TOP1 gainer price, not old symbol
  - ✅ **Fixed Current Price**: P&L uses position symbol's current price, not TOP1 price
  - ✅ **Fixed Reset Balance**: Now saves to file immediately (was not persisting)
  - ✅ **Removed Wrong contract_size**: Old formula multiplied by contract_size causing 100x error

- **December 6, 2025 - CLOSE LOGIC FIX + RECONCILIATION v2 (v2.14):**
  - ✅ **Close Logic Fixed**: Now compares close_levels to OPENING direction, not last cycle
  - ✅ **Survives Restart**: Close logic works even after server restart (uses saved state)
  - ✅ **Enhanced Reconciliation**: Fills position data when position=null but exchange has position
  - ✅ **Full Position Data**: Reconciliation creates complete position object with position_id
  - ✅ **Ghost Position Cleanup**: Clears position_open_levels_directions when clearing ghost

- **December 6, 2025 - GHOST POSITION FIX + RECONCILIATION (v2.13):**
  - ✅ **Ghost Position Fixed**: Bot no longer crashes with KeyError when entry_time is missing
  - ✅ **Reconciliation**: Strategy loop now syncs state with real exchange positions every cycle
  - ✅ **State Persistence**: save_state_to_file() called immediately after opening real position
  - ✅ **Automatic Cleanup**: Ghost positions (state=True, exchange=None) cleared automatically
  - ✅ **Position Sync**: Real positions on exchange synced to state if state was cleared

- **December 6, 2025 - REAL MODE PERSISTENCE FIX (v2.12):**
  - ✅ **REAL Mode Persists on Restart**: auto_authenticate_api() now checks saved mode from state file
  - ✅ **State File Restoration**: If trading_mode='real' was saved, it restores REAL mode on server restart
  - ✅ **API Status Returns trading_mode**: /api/status now includes trading_mode field for frontend sync
  - ✅ **Frontend Mode Sync**: updateDashboard() now calls updateModeButtons() with trading_mode from API
  - ✅ **Cross-Worker Sync**: All Gunicorn workers read api_connected and trading_mode from state file

- **December 6, 2025 - DEMO BUTTONS FIXED + STATE SYNC FINALIZED (v2.11):**
  - ✅ **DEMO Buttons Active**: OPEN LONG / OPEN SHORT now fully active in DEMO mode (virtual $100)
  - ✅ **Button Initialization**: Added `enableDEMOButtons()` to ensure buttons start enabled in DEMO mode
  - ✅ **State Sync Final**: File-based state re-reading in strategy loop fixes all Gunicorn worker conflicts
  - ✅ **Automatic Opening**: Bot automatically opens positions when selected SAR levels align
  - ✅ **Manual Override**: Users can click OPEN LONG/SHORT in DEMO mode anytime
  - ✅ **Result**: Full control in DEMO mode!

- **December 5, 2025 - GUNICORN WORKER STATE SYNC FIX (v2.10):**
  - ✅ **Fixed Critical State Sync Bug**: Workers now re-read `state` from file every cycle
  - ✅ **Instant Opening Restored**: Positions open immediately when SAR levels align
  - ✅ **Cross-Worker Synchronization**: All 4 Gunicorn workers see same `in_position` status
  - ✅ **File-Based State**: Added `state = json.load()` at start of strategy loop

## User Preferences

- **Exchange**: Gate.io
- **Trading Pair**: Dynamic TOP 1 gainer (changes every 60 seconds, FROZEN during position)
- **Leverage**: x10 ✅
- **Strategy**: User-configurable via dashboard (default 5m/30m for open, 5m for close)
- **Mode**: Paper Trading (DEMO: $100 virtual) / Live Trading (REAL: actual Gate.io balance)
- **Language**: Russian language preferred
- **Top Gainers**: All Gate.io ASCII futures contracts with 24h % change
- **Manual Controls**: DEMO mode allows clicking OPEN LONG / OPEN SHORT anytime
- **Security**: When API connected, manual buttons disabled - only automatic trading allowed

## Current Status (v2.14)

✅ **Working**: 
- Automatic position opening when SAR levels align
- **DEMO buttons ACTIVE** - click OPEN LONG / OPEN SHORT in virtual mode
- Instant opening (no confirmation delay)
- Cross-worker state synchronization (Gunicorn stable)
- DEMO/REAL balance toggle
- TOP 1 gainer dynamic selection
- Automatic position closing on signal divergence
- Telegram notifications
- Manual position override in DEMO mode
- **REAL mode persists after server restart**
- **Ghost position auto-cleanup** (no more stuck positions)
- **Exchange reconciliation** (state syncs with real positions)

🎯 **Ready For**:
- Live Gate.io trading (click "Connect API")
- Strategy customization (select levels 1-15)
- Real-time monitoring via dashboard
- Railway deployment (see RAILWAY_DEPLOYMENT.md)

## GitHub Repository

Repository: `manuninkirill-bot/tradingbot`  
Last Updated: December 6, 2025 v2.14
