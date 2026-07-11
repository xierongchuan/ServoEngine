"""Публичный MEXC Futures WebSocket-кэш свечей с REST backfill."""

import gzip
import json
import threading
import time
from multiprocessing import Manager
from typing import List, Optional

import websocket

from src.utils.logger import error, info, warning


class MEXCFuturesWebSocketDataProvider:
    WS_URL = "wss://contract.mexc.com/edge"
    CACHE_SIZE = 600

    def __init__(self):
        self._cache = None
        self._ready = None
        self._symbols: List[str] = []
        self._interval = "5m"
        self._running = False
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread = None
        self._ping_thread = None

    @staticmethod
    def _canonical(symbol: str) -> str:
        return str(symbol).upper().replace("-", "").replace("_", "").replace("/", "")

    @staticmethod
    def _exchange_symbol(symbol: str) -> str:
        canonical = MEXCFuturesWebSocketDataProvider._canonical(symbol)
        return f"{canonical[:-4]}_USDT" if canonical.endswith("USDT") else canonical

    def start(self, symbols, interval, cache, ready):
        self._symbols = list(symbols)
        self._interval = interval
        self._cache, self._ready = cache, ready
        self._running = True
        self._backfill()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws:
            self._ws.close()

    def _backfill(self):
        from src.exchanges.exchange_factory import get_market_data_client
        client = get_market_data_client("mexc", "perpetual")
        for symbol in self._symbols:
            try:
                data = client.get_kline_data(symbol, self._interval, self.CACHE_SIZE)
                key = self._canonical(symbol)
                self._cache[key] = data
                self._ready[key] = len(data) >= 100
            except Exception as exc:
                warning(f"⚠️ MEXC WS backfill {symbol} failed: {exc}")

    def _run(self):
        delay = 1
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self.WS_URL, on_open=self._on_open, on_message=self._on_message,
                    on_error=lambda _ws, exc: warning(f"⚠️ MEXC WS error: {exc}"),
                )
                self._ws.run_forever()
            except Exception as exc:
                if self._running:
                    warning(f"⚠️ MEXC WS reconnect in {delay}s: {exc}")
            if self._running:
                time.sleep(delay)
                delay = min(delay * 2, 60)

    def _on_open(self, ws):
        interval_map = {
            "1m": "Min1", "5m": "Min5", "15m": "Min15", "30m": "Min30",
            "1h": "Min60", "4h": "Hour4", "8h": "Hour8", "1d": "Day1",
            "1w": "Week1", "1M": "Month1",
        }
        exchange_interval = interval_map.get(self._interval)
        if not exchange_interval:
            raise ValueError(f"MEXC WS interval not supported: {self._interval}")
        for symbol in self._symbols:
            ws.send(json.dumps({
                "method": "sub.kline",
                "param": {"symbol": self._exchange_symbol(symbol), "interval": exchange_interval},
                "gzip": False,
            }))
        self._ping_thread = threading.Thread(target=self._ping_loop, daemon=True)
        self._ping_thread.start()
        info(f"✅ MEXC Futures WS subscribed: {len(self._symbols)} symbols")

    def _ping_loop(self):
        while self._running and self._ws:
            try:
                self._ws.send(json.dumps({"method": "ping"}))
            except Exception:
                return
            time.sleep(15)

    def _on_message(self, _ws, message):
        try:
            if isinstance(message, bytes):
                try:
                    message = gzip.decompress(message).decode()
                except OSError:
                    message = message.decode()
            payload = json.loads(message)
            if payload.get("channel") != "push.kline":
                return
            data = payload.get("data") or {}
            symbol = self._canonical(data.get("symbol") or payload.get("symbol", ""))
            timestamp = int(data.get("t", 0))
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            candle = {
                "snapshotTimeUTC": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp)),
                "timestamp": timestamp * 1000,
                "openPrice": float(data.get("o", 0)),
                "highPrice": float(data.get("h", 0)),
                "lowPrice": float(data.get("l", 0)),
                "closePrice": float(data.get("c", 0)),
                "volume": float(data.get("v", data.get("q", 0))),
            }
            cached = list(self._cache.get(symbol, []))
            if cached and cached[-1].get("timestamp") == candle["timestamp"]:
                cached[-1] = candle
            else:
                cached.append(candle)
            self._cache[symbol] = cached[-self.CACHE_SIZE:]
            self._ready[symbol] = len(cached) >= 100
        except Exception as exc:
            error(f"❌ MEXC WS message error: {exc}")


_provider = None
_manager = None
_shared_cache = None
_shared_ready = None


def start_ws_provider(symbols, interval="5m"):
    global _provider, _manager, _shared_cache, _shared_ready
    _manager = Manager()
    _shared_cache = _manager.dict()
    _shared_ready = _manager.dict()
    _provider = MEXCFuturesWebSocketDataProvider()
    _provider.start(symbols, interval, _shared_cache, _shared_ready)
    return _shared_cache, _shared_ready


def stop_ws_provider():
    global _provider
    if _provider:
        _provider.stop()
        _provider = None


def set_shared_cache(cache, ready):
    global _shared_cache, _shared_ready
    _shared_cache, _shared_ready = cache, ready


def get_klines_from_shared_cache(symbol, limit=288):
    if _shared_cache is None:
        return []
    key = MEXCFuturesWebSocketDataProvider._canonical(symbol)
    data = list(_shared_cache.get(key, []))
    return data[-limit:]


def is_cache_ready(symbol):
    if _shared_ready is None:
        return False
    return bool(_shared_ready.get(MEXCFuturesWebSocketDataProvider._canonical(symbol), False))
