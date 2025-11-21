import os
import sys

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_API_URL, MODE, EXCHANGE

# Force BingX mode for testing if not set
if EXCHANGE != "bingx":
    print("⚠️ EXCHANGE is not set to 'bingx'. Setting it temporarily for this test.")
    os.environ["EXCHANGE"] = "bingx"
    # Re-import config to pick up changes if needed, but config is already loaded.
    # We might need to manually mock or just proceed if the client uses the variable directly.
    # Since we import variables from config, we can't easily change them after import.
    # But we can instantiate the client manually with keys.

from src.exchanges.bingx_client import BingXClient

def test_bingx_connection():
    print("🚀 Testing BingX API Connection...")
    print(f"   Mode: {MODE}")
    print(f"   URL: {BINGX_API_URL}")
    
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        print("\n❌ ОШИБКА: Не найдены API ключи BingX!")
        print("⚠️ Для получения баланса (даже демо/VST) ОБЯЗАТЕЛЬНО нужны API ключи.")
        print("\n📝 Как исправить:")
        print("1. Зайдите на https://bingx.com/en-us/account/api/")
        print("2. Создайте API ключи (разрешите 'Perpetual Futures Trading')")
        print("3. Добавьте их в файл .env:")
        print("   EXCHANGE=bingx")
        print("   BINGX_API_KEY=ваши_ключи")
        print("   BINGX_SECRET_KEY=ваши_ключи")
        print("\n⚠️ Без ключей API не может получить доступ к вашему счету.")
        return

    client = BingXClient()
    
    # 1. Test Balance
    print("\n💰 Testing Get All Balances...")
    try:
        balances = client.get_all_balances()
        print("   --- Account Balances ---")
        
        # Perpetual
        perp = balances.get("perpetual", {})
        print(f"   🔹 Perpetual Futures (VST/USDT): {perp}")
        
        # Spot
        spot = balances.get("spot", [])
        print(f"   🔹 Spot: {spot}")
        
        # Standard Futures
        std = balances.get("standard_futures", [])
        print(f"   🔹 Standard Futures: {std}")
        
        print("   ------------------------")
    except Exception as e:
        print(f"❌ Failed to get balances: {e}")

    # 2. Test Klines
    print("\n📊 Testing Get Klines (BTC-USDT)...")
    try:
        klines = client.get_kline_data("BTC/USD", limit=5)
        print(f"   Got {len(klines)} candles")
        if klines:
            print(f"   Latest candle: {klines[-1]}")
    except Exception as e:
        print(f"❌ Failed to get klines: {e}")

    # 3. Test Positions
    print("\n📈 Testing Get Positions...")
    try:
        positions = client.get_positions()
        print(f"   Positions: {positions}")
    except Exception as e:
        print(f"❌ Failed to get positions: {e}")

if __name__ == "__main__":
    test_bingx_connection()
