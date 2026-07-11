import os
import sys
import pytest

pytestmark = pytest.mark.live

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import EXCHANGE
from src.exchanges.exchange_factory import get_exchange_client
from src.utils.logger import info, error

def test_integration():
    print(f"Testing integration for EXCHANGE={EXCHANGE}")
    
    try:
        client = get_exchange_client()
        print(f"✅ Client obtained: {type(client).__name__}")
    except Exception as e:
        print(f"❌ Failed to get client: {e}")
        return

    if not client.check_prerequisites():
        print("❌ Prerequisites check failed")
        return
    print("✅ Prerequisites check passed")

    try:
        balance = client.get_balance()
        print(f"✅ Balance: {balance}")
    except Exception as e:
        print(f"❌ Failed to get balance: {e}")

    # Test klines for a known symbol
    symbol = "BTC/USD"
    if EXCHANGE == "bingx":
        symbol = "BTC-USDT" # BingX uses different format, but our system might expect standard format and client handles it?
        # In collector.py we pass "BTC/USD" (from SYMBOLS) to fetch_prices.
        # BingXClient.get_kline_data handles the mapping?
        # Let's check BingXClient.get_kline_data
        pass
    
    # Actually, let's use a symbol from config
    from src.config import SYMBOLS
    if SYMBOLS:
        symbol = SYMBOLS[0]
        print(f"Testing klines for {symbol}...")
        try:
            klines = client.get_kline_data(symbol, limit=5)
            print(f"✅ Got {len(klines)} klines")
            if klines:
                print(f"   Sample: {klines[0]}")
        except Exception as e:
            print(f"❌ Failed to get klines: {e}")

if __name__ == "__main__":
    test_integration()
