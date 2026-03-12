"""
WebSocket Data Provider for BingX.
Maintains real-time kline cache via WebSocket, reducing REST API calls.

Architecture:
- One WebSocket connection for all symbols
- In-memory cache with deque (auto-removes old candles)
- REST backfill on startup for historical data
- Automatic reconnection with exponential backoff
"""

import json
import time
import threading
import requests
import gzip
from collections import deque
from typing import Dict, List, Optional
from multiprocessing import Manager
import websocket

from src.utils.logger import info, warning, error


class WebSocketDataProvider:
    """
    Singleton WebSocket provider for kline data.
    Subscribes to kline streams for all configured symbols.
    """

    # BingX Market Data WebSocket — один URL для demo и real
    # (рыночные данные публичные, не зависят от VST/prod)
    WS_URL = "wss://open-api-ws.bingx.com/market"
    CACHE_SIZE = 600  # Keep 600 candles per symbol

    def __init__(self, manager: Optional[Manager] = None):
        # Use multiprocessing Manager for cross-process access
        if manager:
            self._kline_cache = manager.dict()
            self._ready_flags = manager.dict()
        else:
            self._kline_cache = {}
            self._ready_flags = {}

        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ping_thread: Optional[threading.Thread] = None
        self._running = False
        self._symbols: List[str] = []
        self._interval: str = "5m"
        self._ws_url = self.WS_URL
        info(f"[WS] Using endpoint: {self._ws_url}")
        # BingX Swap interval mapping (short -> BingX API format)
        self._interval_map = {
            "1m": "1min", "3m": "3min", "5m": "5min",
            "15m": "15min", "30m": "30min",
            "1h": "1hour", "4h": "4hour", "12h": "12hour",
            "1d": "1day", "1w": "1week", "1M": "1month",
        }
        self._reconnect_delay = 1
        self._max_reconnect_delay = 60
        self._last_msg_time = 0.0
        self._ping_interval = 20  # Send keepalive every 20s of silence

    def start(self, symbols: List[str], interval: str = "5m"):
        """
        Start the WebSocket provider.
        1. REST backfill historical data
        2. Connect WebSocket for real-time updates
        """
        self._symbols = symbols
        self._interval = interval
        self._running = True

        info(f"[WS] Starting WebSocket provider for {len(symbols)} symbols, interval={interval}")

        # 1. REST backfill for each symbol
        self._backfill_all_symbols()

        # 2. Start WebSocket in background thread
        self._ws_thread = threading.Thread(target=self._run_websocket, daemon=True)
        self._ws_thread.start()

        info("[WS] WebSocket provider started")

    def stop(self):
        """Stop the WebSocket provider."""
        self._running = False
        if self._ws:
            self._ws.close()
        if self._ping_thread and self._ping_thread.is_alive():
            self._ping_thread.join(timeout=5)
        info("[WS] WebSocket provider stopped")

    def get_klines(self, symbol: str, limit: int = 288) -> List[dict]:
        """
        Get klines from cache.
        Returns list of candles in unified format.
        """
        # Normalize symbol: BTCUSDT -> BTC-USDT for cache lookup
        cache_key = self._normalize_symbol(symbol)

        if cache_key not in self._kline_cache:
            return []

        # Get from cache (it's a list proxy from Manager)
        cached = list(self._kline_cache.get(cache_key, []))

        # Return last N candles
        return cached[-limit:] if len(cached) > limit else cached

    def is_ready(self, symbol: str) -> bool:
        """Check if symbol has enough cached data."""
        cache_key = self._normalize_symbol(symbol)
        return self._ready_flags.get(cache_key, False)

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to BTC-USDT format."""
        if "-" in symbol:
            return symbol
        if symbol.endswith("USDT"):
            return symbol[:-4] + "-USDT"
        return symbol

    def _backfill_all_symbols(self):
        """REST backfill historical data for all symbols."""
        info(f"[WS] Starting REST backfill for {len(self._symbols)} symbols...")

        for symbol in self._symbols:
            try:
                self._backfill_symbol(symbol)
                time.sleep(0.2)  # Small delay between requests
            except Exception as e:
                error(f"[WS] Backfill failed for {symbol}: {e}")

        info("[WS] REST backfill complete")

    def _backfill_symbol(self, symbol: str):
        """REST backfill for single symbol."""
        cache_key = self._normalize_symbol(symbol)
        formatted_symbol = cache_key  # Already in BTC-USDT format

        market_url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
        params = {
            "symbol": formatted_symbol,
            "interval": self._interval,
            "limit": self.CACHE_SIZE
        }

        try:
            response = requests.get(market_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data and data.get("code") == 0:
                klines = data.get("data", [])
                formatted = self._format_klines(klines)

                # Store in cache (as list for Manager compatibility)
                self._kline_cache[cache_key] = formatted
                self._ready_flags[cache_key] = len(formatted) >= 100

                info(f"[WS] Backfill {symbol}: {len(formatted)} candles loaded")
            else:
                warning(f"[WS] Backfill {symbol}: API error {data}")

        except Exception as e:
            error(f"[WS] Backfill {symbol} request failed: {e}")

    def _format_klines(self, klines: list) -> list:
        """Convert BingX klines to unified format."""
        formatted = []

        for k in klines:
            if isinstance(k, dict):
                ts_ms = k.get("time")
                formatted.append({
                    "snapshotTimeUTC": time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts_ms / 1000)),
                    "timestamp": ts_ms,
                    "openPrice": float(k.get("open")),
                    "highPrice": float(k.get("high")),
                    "lowPrice": float(k.get("low")),
                    "closePrice": float(k.get("close")),
                    "volume": float(k.get("volume"))
                })

        # Sort by timestamp ascending
        formatted.sort(key=lambda x: x["timestamp"])
        return formatted

    def _run_websocket(self):
        """WebSocket connection loop with auto-reconnect."""
        while self._running:
            try:
                self._connect_websocket()
            except Exception as e:
                if self._running:
                    warning(f"[WS] Connection error: {e}. Reconnecting in {self._reconnect_delay}s...")
                    time.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    def _connect_websocket(self):
        """Establish WebSocket connection and subscribe."""
        self._ws = websocket.WebSocketApp(
            self._ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        # NOTE: BingX uses custom JSON ping/pong, not WebSocket protocol pings.
        # Do NOT use ping_interval/ping_timeout - they cause disconnects.
        # BingX sends {"ping": ...} messages that we handle in _on_message.
        self._ws.run_forever()

    def _on_open(self, ws):
        """Subscribe to kline streams for all symbols."""
        info("[WS] WebSocket connected, subscribing to klines...")
        self._reconnect_delay = 1  # Reset reconnect delay on success
        self._last_msg_time = time.time()

        # Subscribe to each symbol's kline stream
        # BingX WS format: BTC-USDT@kline_1min (дефис в символе, @kline_ разделитель, 1min интервал)
        bingx_interval = self._interval_map.get(self._interval, self._interval)
        for symbol in self._symbols:
            # Нормализуем символ в формат с дефисом: BTCUSDT -> BTC-USDT
            ws_symbol = self._normalize_symbol(symbol)

            data_type = f"{ws_symbol}@kline_{bingx_interval}"
            sub_msg = {
                "id": f"kline_{ws_symbol}",
                "reqType": "sub",
                "dataType": data_type
            }
            ws.send(json.dumps(sub_msg))
            info(f"[WS] Subscribing: {data_type}")
            time.sleep(0.05)  # Небольшая задержка между подписками

        info(f"[WS] Subscribed to {len(self._symbols)} kline streams")

        # Start keepalive thread (one per connection)
        if self._ping_thread is None or not self._ping_thread.is_alive():
            self._ping_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._ping_thread.start()

    def _keepalive_loop(self):
        """Send periodic pings to prevent idle disconnects."""
        while self._running and self._ws:
            try:
                elapsed = time.time() - self._last_msg_time
                if elapsed >= self._ping_interval and self._ws and self._ws.sock:
                    ping_msg = json.dumps({"ping": int(time.time() * 1000)})
                    self._ws.send(ping_msg)
                time.sleep(5)
            except Exception:
                break

    def _on_message(self, ws, message):
        """Handle incoming WebSocket message."""
        try:
            self._last_msg_time = time.time()
            # BingX sends gzip-compressed messages
            if isinstance(message, bytes):
                try:
                    message = gzip.decompress(message).decode('utf-8')
                except:
                    message = message.decode('utf-8')

            data = json.loads(message)



            # Handle BingX ping/pong (keeps connection alive)
            if "ping" in data:
                pong_msg = json.dumps({"pong": data["ping"], "time": int(time.time() * 1000)})
                ws.send(pong_msg)
                return

            # Handle Ping message type
            if data.get("e") == "ping" or data.get("event") == "ping":
                ws.send(json.dumps({"pong": data.get("time", int(time.time() * 1000))}))
                return

            # Handle kline update
            data_type = data.get("dataType", "")
            if "@kline_" in data_type or "market.kline." in data_type:
                self._handle_kline_update(data)

        except json.JSONDecodeError:
            pass  # Ignore non-JSON messages
        except Exception as e:
            warning(f"[WS] Message handling error: {e}")

    def _handle_kline_update(self, data: dict):
        """Process kline update from WebSocket."""
        try:
            data_type = data.get("dataType", "")

            # Извлечение символа из dataType
            if "@kline_" in data_type:
                # BingX format: BTC-USDT@kline_1min -> BTC-USDT
                symbol = data_type.split("@")[0]
                # Если пришло в lowercase (btcusdt@kline_1min), нормализуем
                if "-" not in symbol and symbol.lower().endswith("usdt"):
                    symbol = symbol[:-4].upper() + "-USDT"
            elif "market.kline." in data_type:
                # Alt format: market.kline.BTC-USDT.1min -> BTC-USDT
                parts = data_type.split(".")
                symbol = parts[2] if len(parts) >= 3 else "UNKNOWN"
            else:
                return

            kline_data = data.get("data")

            # BingX Swap WebSocket может вернуть список или словарь
            if isinstance(kline_data, list):
                if len(kline_data) == 0:
                    warning(f"[WS-KLINE] {symbol}: No kline data (empty list)")
                    return
                k = kline_data[-1]
            elif isinstance(kline_data, dict):
                k = kline_data.get("K", kline_data)
            else:
                warning(f"[WS-KLINE] {symbol}: Unknown kline_data format {type(kline_data)}")
                return

            if not k:
                warning(f"[WS-KLINE] {symbol}: No kline data structure found")
                return

            # Формируем свечу — BingX Swap использует как сокращённые (o/h/l/c/v/t),
            # так и полные (open/high/low/close/volume/time) имена полей
            ts_ms = k.get("t") or k.get("time") or int(time.time() * 1000)
            ts_ms = int(ts_ms)
            new_candle = {
                "snapshotTimeUTC": time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts_ms / 1000)),
                "timestamp": ts_ms,
                "openPrice": float(k.get("o") or k.get("open", 0)),
                "highPrice": float(k.get("h") or k.get("high", 0)),
                "lowPrice": float(k.get("l") or k.get("low", 0)),
                "closePrice": float(k.get("c") or k.get("close", 0)),
                "volume": float(k.get("v") or k.get("volume", 0))
            }

            # DEBUG: Log every kline update (commented out to prevent log flooding)
            # info(f"[WS-KLINE] {symbol}: close={new_candle['closePrice']:.4f}, volume={new_candle['volume']:.2f}")

            # Update cache
            self._update_cache(symbol, new_candle)

        except Exception as e:
            warning(f"[WS] Kline update error: {e}")

    def _update_cache(self, symbol: str, new_candle: dict):
        """Update kline cache with new candle."""
        if symbol not in self._kline_cache:
            self._kline_cache[symbol] = []

        cached = list(self._kline_cache.get(symbol, []))

        # Check if this updates the last candle or adds new one
        is_new_candle = True
        if cached and cached[-1]["timestamp"] == new_candle["timestamp"]:
            # Update existing candle (same time = same candle updating)
            cached[-1] = new_candle
            is_new_candle = False
        else:
            # New candle
            cached.append(new_candle)

            # Trim to CACHE_SIZE
            if len(cached) > self.CACHE_SIZE:
                cached = cached[-self.CACHE_SIZE:]

        # Write back to manager dict
        self._kline_cache[symbol] = cached

        # DEBUG: Log cache update (every 10th update per symbol to avoid spam)
        if not hasattr(self, '_cache_update_counts'):
            self._cache_update_counts = {}
        self._cache_update_counts[symbol] = self._cache_update_counts.get(symbol, 0) + 1

        if self._cache_update_counts[symbol] % 10 == 0:
            cache_type = "NEW" if is_new_candle else "UPDATE"
            info(f"[WS-CACHE] {symbol}: {cache_type} candle, cache_size={len(cached)}, updates={self._cache_update_counts[symbol]}")

    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        warning(f"[WS] WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket close."""
        info(f"[WS] WebSocket closed: {close_status_code} - {close_msg}")


# Global instance, manager, and shared cache
_provider: Optional[WebSocketDataProvider] = None
_manager: Optional[Manager] = None
_shared_cache: Optional[dict] = None  # Manager.dict() proxy
_shared_ready: Optional[dict] = None  # Manager.dict() proxy


def get_ws_provider() -> Optional[WebSocketDataProvider]:
    """Get the global WebSocket provider instance (main process only)."""
    return _provider


def get_shared_cache():
    """Get the shared cache dict (works in worker processes)."""
    return _shared_cache, _shared_ready


def set_shared_cache(cache_dict, ready_dict):
    """Set the shared cache in worker process."""
    global _shared_cache, _shared_ready
    _shared_cache = cache_dict
    _shared_ready = ready_dict


def get_klines_from_shared_cache(symbol: str, limit: int = 288) -> List[dict]:
    """
    Get klines from shared cache.
    Works in both main and worker processes.
    """
    if _shared_cache is None:
        return []

    # Normalize symbol
    if "-" in symbol:
        cache_key = symbol
    elif symbol.endswith("USDT"):
        cache_key = symbol[:-4] + "-USDT"
    else:
        cache_key = symbol

    if cache_key not in _shared_cache:
        return []

    cached = list(_shared_cache.get(cache_key, []))
    return cached[-limit:] if len(cached) > limit else cached


def is_cache_ready(symbol: str) -> bool:
    """Check if symbol has data in shared cache."""
    if _shared_ready is None:
        return False

    if "-" in symbol:
        cache_key = symbol
    elif symbol.endswith("USDT"):
        cache_key = symbol[:-4] + "-USDT"
    else:
        cache_key = symbol

    return _shared_ready.get(cache_key, False)


def start_ws_provider(symbols: List[str], interval: str = "5m"):
    """
    Start the global WebSocket provider.
    Call this from main.py before starting workers.
    Returns (cache_dict, ready_dict) for passing to workers.
    """
    global _provider, _manager, _shared_cache, _shared_ready

    # Create multiprocessing Manager for cross-process cache sharing
    _manager = Manager()
    _shared_cache = _manager.dict()
    _shared_ready = _manager.dict()

    _provider = WebSocketDataProvider(manager=None)
    _provider._kline_cache = _shared_cache
    _provider._ready_flags = _shared_ready
    _provider.start(symbols, interval)

    return _shared_cache, _shared_ready


def stop_ws_provider():
    """Stop the global WebSocket provider."""
    global _provider
    if _provider:
        _provider.stop()
        _provider = None
