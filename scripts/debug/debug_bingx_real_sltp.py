import os
import sys
import time
import json

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exchanges.bingx_client import BingXClient
from src.utils.logger import info, error

def debug_sltp():
    client = BingXClient()
    print("🔍 Fetching positions...")

    # 1. Get Raw Positions to check positionSide
    endpoint = "/openApi/swap/v2/user/positions"
    response = client.make_request("get", endpoint)

    if not response or response.get("code") != 0:
        print(f"❌ Failed to get positions: {response}")
        return

    positions = response.get("data", [])
    if not positions:
        print("⚠️ No open positions found. Please open a small position manually or via bot to test.")
        return

    print(f"📊 Found {len(positions)} positions.")

    for pos in positions:
        symbol = pos.get("symbol")
        pos_side = pos.get("positionSide")
        amt = float(pos.get("positionAmt", 0))

        if amt == 0:
            continue

        print(f"\n👉 Analyzing {symbol}:")
        print(f"   Raw positionSide: '{pos_side}'")
        print(f"   Amount: {amt}")

        # Try to set SL/TP using the client's method
        # We need to determine what we would pass to set_sl_tp

        # Logic from executor.py:
        # pos_type = current_pos["type"].upper() # BUY/SELL
        # pos_side_arg = "LONG" if pos_type == "BUY" else "SHORT"

        # Let's try to call set_sl_tp with "LONG" or "SHORT" and see if it works
        # If raw positionSide is "BOTH", passing "LONG" might fail.

        target_side = "LONG" if amt > 0 else "SHORT"
        print(f"   Attempting set_sl_tp with position_side='{target_side}'...")

        current_price = float(pos.get("avgPrice", 0)) # Approx
        if current_price == 0:
            print("   Skipping (avgPrice 0)")
            continue

        # Set dummy SL/TP +/- 10%
        if target_side == "LONG":
            tp = current_price * 1.1
            sl = current_price * 0.9
        else:
            tp = current_price * 0.9
            sl = current_price * 1.1

        print(f"   Target TP: {tp}, SL: {sl}")

        # Call set_sl_tp directly
        print(f"   Calling client.set_sl_tp({symbol}, {target_side}, tp={tp}, sl={sl})...")
        try:
            client.set_sl_tp(symbol, target_side, tp=tp, sl=sl)
            print("   ✅ set_sl_tp executed successfully")
        except Exception as e:
            print(f"   ❌ set_sl_tp failed: {e}")

if __name__ == "__main__":
    debug_sltp()
