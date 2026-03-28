"""
Debug script to log raw WebSocket messages from BingX.
This will help identify the actual format of kline messages and volume fields.
"""

import websocket
import json
import gzip
import time
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.config import BINGX_API_URL

def debug_ws_messages(symbol="BTC-USDT", interval="1m", duration=60):
    """Debug WebSocket messages to identify volume field format."""

    print(f"=== Debug WebSocket Messages: {symbol} @ {interval} ===")
    print(f"URL: wss://open-api-ws.bingx.com/market")
    print(f"Duration: {duration} seconds")
    print("-" * 80)

    messages_received = 0
    kline_messages = 0
    raw_messages = []

    def on_open(ws):
        print("[OK] WebSocket connection established")

        # Subscribe to kline stream
        # BingX format: BTC-USDT@kline_1min (with hyphen, BingX interval format)
        interval_map = {
            "1m": "1min", "3m": "3min", "5m": "5min",
            "15m": "15min", "30m": "30min",
            "1h": "1hour", "4h": "4hour", "12h": "12hour",
            "1d": "1day", "1w": "1week", "1M": "1month",
        }
        bingx_interval = interval_map.get(interval, interval)
        ws_symbol = symbol  # Keep hyphen: BTC-USDT
        sub_msg = {
            "id": f"debug_kline",
            "reqType": "sub",
            "dataType": f"{ws_symbol}@kline_{bingx_interval}"
        }
        ws.send(json.dumps(sub_msg))
        print(f"[OK] Subscription sent: {sub_msg}")

    def on_message(ws, message):
        nonlocal messages_received, kline_messages, raw_messages
        messages_received += 1

        # Decompress gzip
        if isinstance(message, bytes):
            try:
                message = gzip.decompress(message).decode('utf-8')
            except:
                message = message.decode('utf-8')

        try:
            data = json.loads(message)
        except:
            print(f"[WARN] Non-JSON message: {message[:100]}")
            return

        # Handle ping
        if "ping" in data:
            ws.send(json.dumps({"pong": data["ping"]}))
            print(f"[PING] Received ping, sent pong")
            return

        # Check message type
        data_type = data.get("dataType", "")

        if "@kline_" in data_type:
            kline_messages += 1

            # Log raw message for first 5 kline messages
            if kline_messages <= 5:
                print(f"\n[RAW KLINE #{kline_messages}]")
                print(f"  dataType: {data_type}")
                print(f"  Full message: {json.dumps(data, indent=2)}")

                # Extract kline data
                kline_data = data.get("data", {})
                if isinstance(kline_data, list):
                    if len(kline_data) > 0:
                        k = kline_data[-1]
                        print(f"\n  Kline data (list format, last item):")
                        print(f"    Type: {type(k)}")
                        print(f"    Keys/Fields: {list(k.keys()) if isinstance(k, dict) else 'N/A (list)'}")
                        if isinstance(k, dict):
                            print(f"    'v' field: {k.get('v', 'NOT FOUND')}")
                            print(f"    'volume' field: {k.get('volume', 'NOT FOUND')}")
                            print(f"    All fields: {k}")
                        else:
                            print(f"    Value: {k}")
                elif isinstance(kline_data, dict):
                    k = kline_data.get("K", kline_data)
                    print(f"\n  Kline data (dict format):")
                    print(f"    Type: {type(k)}")
                    print(f"    Keys/Fields: {list(k.keys()) if isinstance(k, dict) else 'N/A'}")
                    if isinstance(k, dict):
                        print(f"    'v' field: {k.get('v', 'NOT FOUND')}")
                        print(f"    'volume' field: {k.get('volume', 'NOT FOUND')}")
                        print(f"    All fields: {k}")

                # Store for analysis
                raw_messages.append({
                    "message_number": kline_messages,
                    "dataType": data_type,
                    "data": data
                })

            # Log summary every 10 messages
            if kline_messages % 10 == 0:
                print(f"[KLINE SUMMARY] Received {kline_messages} kline messages so far")

        else:
            # Other message types
            if messages_received <= 3:
                print(f"[MSG #{messages_received}] Type={data_type}, Data: {str(data)[:150]}")

    def on_error(ws, error):
        print(f"[ERROR] {error}")

    def on_close(ws, close_status_code, close_msg):
        print(f"[CLOSE] Code={close_status_code}, Message={close_msg}")

    # Create WebSocket
    ws = websocket.WebSocketApp(
        "wss://open-api-ws.bingx.com/market",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    # Run for specified duration
    import threading

    def run_ws():
        ws.run_forever()

    ws_thread = threading.Thread(target=run_ws)
    ws_thread.daemon = True
    ws_thread.start()

    time.sleep(duration)
    ws.close()

    # Results
    print("\n" + "=" * 80)
    print("=== DEBUG RESULTS ===")
    print(f"Total messages: {messages_received}")
    print(f"Kline messages: {kline_messages}")

    if raw_messages:
        print(f"\n=== RAW MESSAGE ANALYSIS ===")
        for msg in raw_messages[:3]:  # Show first 3
            print(f"\nMessage #{msg['message_number']}:")
            print(f"  dataType: {msg['dataType']}")
            data = msg['data']
            kline_data = data.get("data", {})

            if isinstance(kline_data, list) and len(kline_data) > 0:
                k = kline_data[-1]
                if isinstance(k, dict):
                    print(f"  Fields: {list(k.keys())}")
                    print(f"  Volume field 'v': {k.get('v', 'NOT FOUND')}")
                    print(f"  Volume field 'volume': {k.get('volume', 'NOT FOUND')}")
            elif isinstance(kline_data, dict):
                k = kline_data.get("K", kline_data)
                if isinstance(k, dict):
                    print(f"  Fields: {list(k.keys())}")
                    print(f"  Volume field 'v': {k.get('v', 'NOT FOUND')}")
                    print(f"  Volume field 'volume': {k.get('volume', 'NOT FOUND')}")

    # Save raw messages to file for further analysis
    output_file = "data/debug_ws_messages.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(raw_messages, f, indent=2)
    print(f"\n[SAVED] Raw messages saved to: {output_file}")

    return kline_messages > 0


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC-USDT"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1m"
    duration = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    success = debug_ws_messages(symbol, interval, duration)
    sys.exit(0 if success else 1)
