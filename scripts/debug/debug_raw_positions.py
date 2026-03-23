import os
import sys
import json

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exchanges.bingx_client import BingXClient

def debug_raw_positions():
    client = BingXClient()
    endpoint = "/openApi/swap/v2/user/positions"
    response = client.make_request("get", endpoint)

    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    debug_raw_positions()
