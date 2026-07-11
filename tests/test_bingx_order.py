import os
import sys
import time
import json
import pytest

pytestmark = pytest.mark.live

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exchanges.bingx_client import BingXClient
from src.utils.logger import info, error

def test_place_order():
    client = BingXClient()

    symbol = "BTC-USDT"
    price = 100000 # Irrelevant for MARKET
    quantity = 0.0001 # Min size?

    print(f"Testing order placement for {symbol}...")

    # 1. Test Simple MARKET Order (No TP/SL)
    print("\n1. Testing Simple MARKET Order (No TP/SL)...")
    try:
        order_id = client.place_order(
            symbol=symbol,
            side="BUY",
            price=price,
            quantity=quantity,
            type="MARKET",
            sl=None,
            tp=None
        )
        if order_id:
            print(f"✅ Simple Order Placed: {order_id}")
            # Cancel it immediately
            time.sleep(1)
            client.cancel_order(symbol, order_id)
        else:
            print("❌ Simple Order Failed")
    except Exception as e:
        print(f"❌ Exception: {e}")

    # 2. Test MARKET Order WITH TP/SL (JSON with Correct Types)
    print("\n2. Testing MARKET Order WITH TP/SL (JSON Correct Types)...")
    try:
        current_price = 96000 # Approx
        tp = current_price * 1.05
        sl = current_price * 0.95

        tp_json = json.dumps({
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": tp,
            "workingType": "MARK_PRICE"
        })

        sl_json = json.dumps({
            "type": "STOP_MARKET",
            "stopPrice": sl,
            "workingType": "MARK_PRICE"
        })

        endpoint = "/openApi/swap/v2/trade/order"
        params = {
            "symbol": "BTC-USDT",
            "side": "BUY",
            "positionSide": "LONG",
            "type": "MARKET",
            "quantity": quantity,
            "takeProfit": tp_json,
            "stopLoss": sl_json
        }

        print(f"   Sending params: {params}")
        response = client.make_request("post", endpoint, params)

        if response and response.get("code") == 0:
            order_id = response.get("data", {}).get("order", {}).get("orderId") or response.get("data", {}).get("orderId")
            print(f"✅ Order with JSON TP/SL Placed: {order_id}")
             # Cancel it immediately
            time.sleep(1)
            client.cancel_order(symbol, order_id)
        else:
            print(f"❌ Order with JSON TP/SL Failed: {response}")

    except Exception as e:
        print(f"❌ Exception: {e}")

if __name__ == "__main__":
    test_place_order()
