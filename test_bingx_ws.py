import websocket
import json
import gzip
import time

def on_message(ws, message):
    if isinstance(message, bytes):
        try: message = gzip.decompress(message).decode('utf-8')
        except: message = message.decode('utf-8')
    data = json.loads(message)
    print('Raw msg:', json.dumps(data, indent=2))
    if 'dataType' in data and 'kline' in data['dataType']:
        ws.close()

def on_open(ws):
    print("Opened")
    sub = {'id': 'id1', 'reqType': 'sub', 'dataType': 'btcusdt@kline_1m'}
    ws.send(json.dumps(sub))

def on_error(ws, error):
    print("Error:", error)

print("Starting ws...")
ws = websocket.WebSocketApp('wss://open-api-ws.bingx.com/market', on_open=on_open, on_message=on_message, on_error=on_error)
ws.run_forever()
