"""Публичный BingX WS: несколько таймфреймов, общий reducer и фоновый REST backfill."""

import gzip
import json

import websocket

from src.exchanges.market_data import StreamKey, StreamStore
from src.exchanges.public_market_rest import exchange_symbol
from src.exchanges.ws.runtime import ManagedMarketDataProvider


class BingXWebSocketDataProvider(ManagedMarketDataProvider):
    EXCHANGE = "bingx"
    WS_URL = "wss://open-api-ws.bingx.com/market"
    INTERVALS = {
        "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "60min", "4h": "4hour", "12h": "12hour",
    }

    def __init__(self, manager=None, **kwargs):
        if manager is not None and "store" not in kwargs:
            kwargs["store"] = StreamStore(manager.dict(), manager.dict(), settings=kwargs.get("settings"))
        super().__init__(**kwargs)
        self._interval_map = self.INTERVALS

    def send_subscription(self, ws, key, request_id, subscribe):
        ws.send(json.dumps({
            "id": request_id, "reqType": "sub" if subscribe else "unsub",
            "dataType": f"{exchange_symbol(key)}@kline_{self.INTERVALS[key.interval]}",
        }))

    def send_ping(self, ws):
        # RFC 6455 heartbeat не зависит от поддержки недокументированного JSON ping.
        ws.send(str(self._generation), opcode=websocket.ABNF.OPCODE_PING)

    def handle_message(self, ws, message):
        if isinstance(message, bytes):
            message = gzip.decompress(message).decode() if message[:2] == b"\x1f\x8b" else message.decode()
        if message in {"Ping", "ping"}:
            ws.send("Pong")
            return
        if message in {"Pong", "pong"}:
            self.received_pong()
            return
        payload = json.loads(message)
        if "ping" in payload:
            ws.send(json.dumps({"pong": payload["ping"]}))
            return
        if "pong" in payload or payload.get("e", payload.get("event")) == "pong":
            self.received_pong()
            return
        if payload.get("e", payload.get("event")) == "ping":
            ws.send(json.dumps({"pong": payload.get("time")}))
            return
        if "id" in payload and "code" in payload:
            self.acknowledge_request(payload["id"], error=None if payload["code"] == 0 else payload.get("msg", payload["code"]))
        self._handle_kline_update(payload, ws)

    def _handle_kline_update(self, payload, ws=None):
        ws = self._ws if ws is None else ws
        data_type = payload.get("dataType", "")
        if "@kline_" in data_type:
            symbol, interval = data_type.split("@kline_", 1)
        elif data_type.startswith("market.kline."):
            _, _, symbol, interval = data_type.split(".", 3)
        else:
            return
        key = StreamKey("bingx", "perpetual", symbol, interval)
        rows = payload.get("data")
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            if not isinstance(row, dict):
                self.accept_candle(ws, key, row)
                continue
            row = dict(row.get("K", row))
            if payload.get("E") is not None:
                row["event_time_ms"] = payload["E"]
            self.accept_candle(ws, key, row)

    def _normalize_symbol(self, symbol):
        return exchange_symbol(StreamKey("bingx", "perpetual", symbol, "1m"))


WebSocketDataProvider = BingXWebSocketDataProvider


def start_ws_provider(symbols=None, interval="5m", **kwargs):
    from src.exchanges import ws_provider_factory as factory
    return factory.start_ws_provider(symbols, interval, exchange="bingx", market_type="perpetual", **kwargs)


def stop_ws_provider():
    from src.exchanges import ws_provider_factory as factory
    factory.stop_ws_provider(exchange="bingx", market_type="perpetual")


def set_shared_cache(cache, ready):
    from src.exchanges import ws_provider_factory as factory
    factory.set_shared_cache(cache, ready)


def get_shared_cache():
    from src.exchanges import ws_provider_factory as factory
    return factory.get_shared_cache()


def get_ws_provider():
    from src.exchanges import ws_provider_factory as factory
    return factory.get_ws_provider("bingx", "perpetual")


def get_klines_from_shared_cache(symbol, limit=288, interval=None, **kwargs):
    from src.exchanges import ws_provider_factory as factory
    return factory.get_klines_from_shared_cache(symbol, limit, interval, exchange="bingx", market_type="perpetual", **kwargs)


def is_cache_ready(symbol, interval=None, **kwargs):
    from src.exchanges import ws_provider_factory as factory
    return factory.is_cache_ready(symbol, interval, exchange="bingx", market_type="perpetual", **kwargs)
