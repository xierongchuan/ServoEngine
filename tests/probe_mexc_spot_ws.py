"""Явный opt-in public wire probe (не входит в offline unittest discover).

Запускать только в Podman без credentials/.env и с разрешённой внешней сетью.
"""

import json
import time

import websocket

from src.exchanges.market_data import StreamKey
from src.exchanges.mexc_spot_ws_data_provider import MEXCSpotWebSocketDataProvider


def main():
    started = time.monotonic()
    deadline = started + 25
    keys = [StreamKey("mexc", "spot", "BTCUSDT", interval) for interval in ("1m", "5m")]
    key = keys[0]
    provider = MEXCSpotWebSocketDataProvider(
        settings={"history_limit": 5, "min_history": 2, "data_dir": "/tmp/public-wire",
                  "rest_timeout_seconds": 4, "rest_retries": 1,
                  "heartbeat_interval_seconds": 3, "heartbeat_timeout_seconds": 10},
        publisher=lambda snapshot: None,
    )
    provider.sync_subscriptions(keys)
    controls, binary_count, first_binary = [], 0, None
    try:
        ws = websocket.create_connection(provider.WS_URL, timeout=6)
        ws.settimeout(1)
        provider._ws = ws
        provider._on_open(ws)
        # Публичный REST шаг выполняется отдельно от callback, как в background runtime.
        for stream in keys:
            provider.reconcile_once(stream)
        while time.monotonic() < deadline:
            provider.watchdog_once()
            try:
                message = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not message:
                break
            if isinstance(message, bytes):
                binary_count += 1
                if first_binary is None:
                    first_binary = message.hex()
            else:
                controls.append(json.loads(message))
            provider._on_message(ws, message)
            snapshots = [provider.store.snapshot(stream) for stream in keys]
            valid_ws = all(value["ready"] and value["subscribed"] and value["candles"][-1]["source"] == "ws"
                           for value in snapshots)
            if valid_ws and provider._pending_ping is None and any(c.get("msg") == "PONG" for c in controls):
                break
        snapshot = provider.store.snapshot(key)
        summary = {"endpoint": provider.WS_URL, "elapsed_seconds": round(time.monotonic() - started, 2),
                   "controls": controls, "binary_count": binary_count, "first_binary_hex": first_binary,
                   "stream": snapshot["stream"], "transport": snapshot["transport"],
                   "status": snapshot["status"], "ready": snapshot["ready"],
                   "error": snapshot["error"], "heartbeat_acknowledged": provider._pending_ping is None,
                   "last_valid_update": snapshot["last_valid_update"],
                   "last_candle": snapshot["candles"][-1] if snapshot["candles"] else None,
                   "streams": [{"stream": value["stream"], "ready": value["ready"],
                                "subscribed": value["subscribed"], "status": value["status"],
                                "last_candle": value["candles"][-1] if value["candles"] else None}
                               for value in (provider.store.snapshot(stream) for stream in keys)]}
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if not (binary_count and all(value["ready"] and value["subscribed"] and value["candles"][-1]["source"] == "ws"
                                    for value in (provider.store.snapshot(stream) for stream in keys))
                and provider._pending_ping is None
                and any(c.get("msg") == "PONG" for c in controls)):
            raise RuntimeError("Public wire probe не подтвердил binary WS/ready/PONG")
    finally:
        provider.stop()


if __name__ == "__main__":
    main()
