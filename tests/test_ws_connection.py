"""
Тест WebSocket соединения с BingX.
Проверяет подключение, подписку и получение kline-сообщений.
"""

import websocket
import json
import gzip
import time
import sys
import pytest

pytestmark = pytest.mark.live


def run_ws_connection(symbol="BTC-USDT", interval="1m", duration=30):
    """Тест WebSocket соединения с BingX."""

    print(f"=== Тест WebSocket: {symbol} @ {interval} ===")
    print(f"URL: wss://open-api-ws.bingx.com/market")
    print(f"Длительность: {duration} секунд")
    print("-" * 50)

    messages_received = 0
    kline_messages = 0
    last_price = None

    def on_open(ws):
        print("[OK] WebSocket соединение установлено")

        # Подписка на kline (BingX использует: btcusdt@kline_1m, без дефиса)
        ws_symbol = symbol.replace("-", "").lower()
        sub_msg = {
            "id": f"test_kline",
            "reqType": "sub",
            "dataType": f"{ws_symbol}@kline_{interval}"
        }
        ws.send(json.dumps(sub_msg))
        print(f"[OK] Отправлена подписка: {sub_msg}")

    def on_message(ws, message):
        nonlocal messages_received, kline_messages, last_price
        messages_received += 1

        # Декомпрессия gzip
        if isinstance(message, bytes):
            try:
                message = gzip.decompress(message).decode('utf-8')
            except:
                message = message.decode('utf-8')

        try:
            data = json.loads(message)
        except:
            print(f"[WARN] Не JSON сообщение: {message[:100]}")
            return

        # Обработка ping
        if "ping" in data:
            ws.send(json.dumps({"pong": data["ping"]}))
            print(f"[PING] Получен ping, отправлен pong")
            return

        # Проверка типа сообщения
        data_type = data.get("dataType", "")

        if "@kline_" in data_type:
            kline_messages += 1
            kline_data = data.get("data", {})
            k = kline_data.get("K", kline_data)

            if k:
                close_price = float(k.get("c", 0))
                volume = float(k.get("v", 0))
                is_closed = k.get("x", False)  # Свеча закрыта?

                if last_price is None:
                    last_price = close_price
                    print(f"[KLINE #{kline_messages}] {symbol}: close={close_price}, vol={volume}, closed={is_closed}")
                elif abs(close_price - last_price) > 0.01:
                    last_price = close_price
                    print(f"[KLINE #{kline_messages}] {symbol}: close={close_price}, vol={volume}, closed={is_closed}")
        else:
            # Другие типы сообщений
            print(f"[MSG #{messages_received}] Type={data_type}, Data: {str(data)[:150]}")

    def on_error(ws, error):
        print(f"[ERROR] {error}")

    def on_close(ws, close_status_code, close_msg):
        print(f"[CLOSE] Код={close_status_code}, Сообщение={close_msg}")

    # Создаём WebSocket
    ws = websocket.WebSocketApp(
        "wss://open-api-ws.bingx.com/market",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    # Запускаем на заданное время
    import threading

    def run_ws():
        ws.run_forever()

    ws_thread = threading.Thread(target=run_ws)
    ws_thread.daemon = True
    ws_thread.start()

    time.sleep(duration)
    ws.close()

    # Результаты
    print("-" * 50)
    print("=== РЕЗУЛЬТАТЫ ТЕСТА ===")
    print(f"Всего сообщений: {messages_received}")
    print(f"Kline сообщений: {kline_messages}")
    print(f"Последняя цена: {last_price}")

    if kline_messages == 0:
        print("\n[FAIL] ❌ Kline сообщения НЕ ПРИХОДЯТ!")
        return False
    else:
        print(f"\n[OK] ✅ Получено {kline_messages} kline сообщений")
        return True


def test_ws_connection():
    assert run_ws_connection(duration=30) is True


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC-USDT"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1m"

    success = run_ws_connection(symbol, interval, duration=30)
    sys.exit(0 if success else 1)
