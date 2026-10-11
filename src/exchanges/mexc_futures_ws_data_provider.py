"""Публичный MEXC Futures WS с несколькими TF и фоновым range reconciliation."""

import gzip
import json

from src.exchanges.market_data import StreamKey, canonical_symbol
from src.exchanges.public_market_rest import MEXC_INTERVALS, exchange_symbol
from src.exchanges.ws.runtime import ManagedMarketDataProvider


class MEXCFuturesWebSocketDataProvider(ManagedMarketDataProvider):
    EXCHANGE = "mexc"
    WS_URL = "wss://contract.mexc.com/edge"
    INTERVALS = MEXC_INTERVALS

    @staticmethod
    def _canonical(symbol):
        return canonical_symbol(symbol)

    @staticmethod
    def _exchange_symbol(symbol):
        return exchange_symbol(StreamKey("mexc", "perpetual", symbol, "1m"))

    def start(self, symbols=None, interval="5m", cache=None, ready=None, *, streams=None):
        # Старый прямой start(symbols, interval, cache, ready) остаётся допустимым.
        if cache is not None:
            self.store.cache = cache
        if ready is not None:
            self.store.ready = ready
        super().start(symbols, interval, streams=streams)

    def sync_subscriptions(self, streams):
        desired = [StreamKey.from_value(key) for key in streams]
        if len({key.interval for key in desired}) > 1:
            raise ValueError("MEXC несколько TF требуют WS-shards через ws_provider_factory.sync_subscriptions")
        super().sync_subscriptions(desired)

    def send_subscription(self, ws, key, request_id, subscribe):
        ws.send(json.dumps({
            "method": "sub.kline" if subscribe else "unsub.kline",
            "param": ({"symbol": exchange_symbol(key), "interval": self.INTERVALS[key.interval]}
                      if subscribe else {"symbol": exchange_symbol(key)}),
            "gzip": False,
        }))

    def send_ping(self, ws):
        ws.send(json.dumps({"method": "ping"}))

    def handle_message(self, ws, message):
        if isinstance(message, bytes):
            message = gzip.decompress(message).decode() if message[:2] == b"\x1f\x8b" else message.decode()
        payload = json.loads(message)
        channel = payload.get("channel", "")
        if channel in {"pong", "rs.ping"} or payload.get("method") == "pong":
            self.received_pong()
            return
        if channel == "rs.error":
            raise ValueError(f"MEXC отклонил публичную подписку: {payload.get('data')}")
        if channel == "rs.sub.kline":
            data = payload.get("data")
            if isinstance(data, dict) and data.get("symbol") and data.get("interval"):
                key = StreamKey("mexc", "perpetual", data["symbol"], data["interval"])
                with self._lock:
                    if key not in self._streams or ws is not self._ws:
                        return
                    if data.get("success") is False:
                        self.store.mark(key, status="error", error=str(data))
                        self._emit(key)
                    else:
                        self.acknowledge(key)
            # Ответ без идентификатора не подтверждает произвольный поток.
            # Его подписку подтвердит первый валидный kline или watchdog timeout.
            return
        if channel != "push.kline":
            return
        row = dict(payload.get("data") or {})
        symbol = canonical_symbol(row.get("symbol") or payload.get("symbol", ""))
        interval = row.get("interval") or payload.get("interval")
        if interval:
            key = StreamKey("mexc", "perpetual", symbol, interval)
        else:
            matches = [key for key in self.streams if key.symbol == symbol]
            if len(matches) != 1:
                raise ValueError("MEXC сообщение без interval неоднозначно при нескольких подписках")
            key = matches[0]
        if payload.get("ts") is not None:
            row["event_time_ms"] = payload["ts"]
        self.accept_candle(ws, key, row)


def start_ws_provider(symbols=None, interval="5m", **kwargs):
    from src.exchanges import ws_provider_factory as factory
    return factory.start_ws_provider(symbols, interval, exchange="mexc", market_type="perpetual", **kwargs)


def stop_ws_provider():
    from src.exchanges import ws_provider_factory as factory
    factory.stop_ws_provider(exchange="mexc", market_type="perpetual")


def set_shared_cache(cache, ready):
    from src.exchanges import ws_provider_factory as factory
    factory.set_shared_cache(cache, ready)


def get_klines_from_shared_cache(symbol, limit=288, interval=None, **kwargs):
    from src.exchanges import ws_provider_factory as factory
    return factory.get_klines_from_shared_cache(symbol, limit, interval, exchange="mexc", market_type="perpetual", **kwargs)


def get_ws_provider(interval=None):
    from src.exchanges import ws_provider_factory as factory
    return factory.get_ws_provider("mexc", "perpetual", interval=interval)


def is_cache_ready(symbol, interval=None, **kwargs):
    from src.exchanges import ws_provider_factory as factory
    return factory.is_cache_ready(symbol, interval, exchange="mexc", market_type="perpetual", **kwargs)
