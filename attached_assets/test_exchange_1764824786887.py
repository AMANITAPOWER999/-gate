import os
import sys
import ccxt
import pandas as pd
from ta.trend import PSARIndicator

# Test AscendEX API connection
print("🔍 Testing AscendEX API Connection...\n")

try:
    exchange = ccxt.ascendex({
        "sandbox": False,
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
        }
    })
    
    print("✅ Connected to AscendEX\n")
    
    # Get current price
    ticker = exchange.fetch_ticker("ETH/USDT:USDT")
    current_price = ticker['last']
    print(f"💰 Current ETH/USDT Price: ${current_price:.2f}\n")
    
    # Fetch 5m OHLCV data
    print("📊 Fetching 5m OHLCV data (last 50 candles)...")
    ohlcv = exchange.fetch_ohlcv("ETH/USDT:USDT", timeframe="5m", limit=50)
    
    df = pd.DataFrame(ohlcv)
    df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    
    print(f"✅ Got {len(df)} candles\n")
    
    # Calculate SAR
    print("📈 Calculating Parabolic SAR...")
    high_series = pd.Series(df["high"].values)
    low_series = pd.Series(df["low"].values)
    close_series = pd.Series(df["close"].values)
    psar_ind = PSARIndicator(high=high_series, low=low_series, close=close_series, step=0.05, max_step=0.5)
    psar = psar_ind.psar()
    
    last_close = df["close"].iloc[-1]
    last_sar = psar.iloc[-1]
    
    direction = "🟢 LONG" if last_close > last_sar else "🔴 SHORT"
    
    print(f"✅ Last Close: ${last_close:.2f}")
    print(f"✅ Last SAR:   ${last_sar:.2f}")
    print(f"✅ Direction:  {direction}\n")
    
    # Show last 5 candles
    print("📋 Last 5 Candles:")
    print(df[["datetime", "open", "high", "low", "close"]].tail(5).to_string())
    
    print("\n✅ API TEST SUCCESSFUL - All real data working!")
    
except Exception as e:
    print(f"❌ API Error: {e}")
    sys.exit(1)
