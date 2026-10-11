"""Ограниченная opt-in проверка публичного WS без ключей и торговых запросов.

Запуск: Podman, без .env. Модуль не входит в обычный offline pytest.
"""

import argparse
import json
import time

from src.exchanges.bingx_ws_data_provider import BingXWebSocketDataProvider
from src.exchanges.market_data import StreamKey
from src.exchanges.mexc_futures_ws_data_provider import MEXCFuturesWebSocketDataProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exchange", choices=("bingx", "mexc"))
    args = parser.parse_args()
    provider_type = (BingXWebSocketDataProvider if args.exchange == "bingx"
                     else MEXCFuturesWebSocketDataProvider)
    keys = [StreamKey(args.exchange, "perpetual", "BTC-USDT", tf) for tf in ("1m", "5m")]
    settings = {
        "history_limit": 5, "min_history": 2, "data_dir": "/tmp/public-perpetual-wire",
        "rest_timeout_seconds": 5, "rest_retries": 1,
        "heartbeat_interval_seconds": 3, "heartbeat_timeout_seconds": 12,
    }
    # MEXC подписывает один TF на соединение; runtime использует такие же shards.
    groups = [keys] if args.exchange == "bingx" else [[key] for key in keys]
    providers = [provider_type(settings=settings, publisher=lambda snapshot: None) for _ in groups]
    started = time.monotonic()
    seen_pending, confirmed_pong = set(), set()
    try:
        for provider, streams in zip(providers, groups):
            provider.start(streams=streams)
        while time.monotonic() - started < 35:
            values = []
            for index, (provider, streams) in enumerate(zip(providers, groups)):
                with provider._lock:
                    if provider._pending_ping is not None:
                        seen_pending.add(index)
                    elif index in seen_pending and provider._connected:
                        confirmed_pong.add(index)
                values.extend(provider.store.snapshot(key) for key in streams)
            if (all(value["ready"] and value["subscribed"] and value["candles"]
                    and value["candles"][-1]["source"] == "ws" for value in values)
                    and len(confirmed_pong) == len(providers)):
                break
            time.sleep(0.05)
        summary = {
            "exchange": args.exchange, "endpoint": provider_type.WS_URL,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "heartbeat_confirmed_shards": sorted(confirmed_pong),
            "streams": [{"stream": value["stream"], "ready": value["ready"],
                         "subscribed": value["subscribed"], "status": value["status"],
                         "error": value["error"],
                         "last_candle": value["candles"][-1] if value["candles"] else None}
                        for value in values],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if not (all(value["ready"] and value["subscribed"] and value["candles"]
                    and value["candles"][-1]["source"] == "ws" for value in values)
                and len(confirmed_pong) == len(providers)):
            raise RuntimeError("Публичный WS не подтвердил свечи обоих TF и heartbeat")
    finally:
        for provider in providers:
            provider.stop()


if __name__ == "__main__":
    main()
