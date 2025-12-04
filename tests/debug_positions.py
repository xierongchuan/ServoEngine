import os
import sys
import json

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exchanges.bingx_client import BingXClient
from src.config import SYMBOLS

def debug_positions():
    client = BingXClient()
    positions = client.get_positions()

    print(f"Configured Symbols: {SYMBOLS}")
    print(f"Open Positions Keys: {list(positions.keys())}")

    for sym in SYMBOLS:
        if sym in positions:
            print(f"✅ Symbol {sym} found in positions")
        else:
            print(f"❌ Symbol {sym} NOT found in positions")
            # Check for partial matches or formatting differences
            for pos_sym in positions.keys():
                if sym.replace("/", "").replace("-", "") == pos_sym.replace("/", "").replace("-", ""):
                     print(f"   ⚠️ But found similar: {pos_sym}")

if __name__ == "__main__":
    debug_positions()
