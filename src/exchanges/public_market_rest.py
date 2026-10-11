"""Только публичные REST klines: без credentials, private client и рекурсии WS-кэша."""

from __future__ import annotations

import logging
import math
import time

import requests

from .market_data import StreamKey, interval_start, next_start, timestamp_ms, market_data_settings, normalize_candle

logger = logging.getLogger(__name__)
MEXC_INTERVALS = {
    "1m": "Min1", "5m": "Min5", "15m": "Min15", "30m": "Min30", "1h": "Min60",
    "4h": "Hour4", "8h": "Hour8", "1d": "Day1", "1w": "Week1", "1M": "Month1",
}
BINGX_WS_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "12h", "1d", "1w", "1M"}
# REST и WS имеют разные enum: не подменяем 2h/6h/8h ближайшим WS-интервалом.
BINGX_INTERVALS = BINGX_WS_INTERVALS | {"2h", "6h", "8h"}
_RETRY_HTTP = {429, 500, 502, 503, 504}
# https://bingx-api.github.io/docs/: FREQUENCY_LIMIT, INTERNAL_SERVER_ERROR, SERVER_BUSY.
# Остальные коды повторяют явный allowlist текущего impl/bingx_client.py, без импорта private client.
_BINGX_RETRY_BODY = _RETRY_HTTP | {100410, 100500, 100503, 109429, 109500}
# https://mexcdevelop.github.io/apidocs/{spot_v3_en,contract_v1_en}/: только rate-коды.
_MEXC_RATE_BODY = {"spot": {429}, "perpetual": {510}}
MEXC_SPOT_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "4h": "4h", "1d": "1d", "1w": "1W", "1M": "1M"}


def exchange_symbol(key):
    key = StreamKey.from_value(key)
    for quote in ("USDT", "USDC", "BTC", "ETH", "USD"):
        if key.symbol.endswith(quote) and len(key.symbol) > len(quote):
            separator = "_" if key.exchange == "mexc" else "-"
            return key.symbol[:-len(quote)] + separator + quote
    raise ValueError(f"Неизвестный quote asset: {key.symbol}")


class PublicRESTFetcher:
    def __init__(self, *, settings=None, rate_limiters=None, request=None, sleep=time.sleep):
        self.settings = market_data_settings(settings)
        self.rate_limiters = dict(rate_limiters or {})
        self.request = request or requests.get
        self.sleep = sleep

    @staticmethod
    def supports(key):
        key = StreamKey.from_value(key)
        if key.exchange == "bingx":
            return key.market_type == "perpetual" and key.interval in BINGX_INTERVALS
        intervals = MEXC_INTERVALS if key.market_type == "perpetual" else MEXC_SPOT_INTERVALS
        # Spot WS Hour8 имеет точный backfill из двух документированных REST 4h свечей.
        return key.interval in intervals or (key.market_type == "spot" and key.interval == "8h")

    def _limiter(self, exchange):
        configured = self.rate_limiters.get(exchange) or self.rate_limiters.get((exchange, "public"))
        if configured is not None:
            return configured
        from .rate_limit import get_exchange_rate_limiters
        budgets = get_exchange_rate_limiters(exchange, data_dir=self.settings.get("data_dir"))
        # Без account_id все аккаунты консервативно делят namespace; IP budget общий всегда.
        limiter = budgets[exchange]
        self.rate_limiters[exchange] = limiter
        return limiter

    @staticmethod
    def _retryable_body(key, payload):
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            return False
        codes = _BINGX_RETRY_BODY if key.exchange == "bingx" else _MEXC_RATE_BODY.get(key.market_type, set())
        return payload["code"] in codes

    @staticmethod
    def _cooldown_delay(response, attempt):
        fallback = 60.0 if response.status_code == 418 else float(2 ** attempt)
        try:
            delay = float(response.headers.get("Retry-After", fallback))
        except (TypeError, ValueError, OverflowError):
            return fallback
        # Не передаём NaN/inf/отрицательный срок в durable limiter и не сокращаем бан.
        return max(1.0, delay) if math.isfinite(delay) and 0 <= delay <= 31_536_000 else fallback

    def _get(self, key, url, params):
        limiter = self._limiter(key.exchange)
        retries = self.settings["rest_retries"]
        for attempt in range(retries):
            limiter.acquire(weight=1)
            try:
                response = self.request(url, params=params, timeout=self.settings["rest_timeout_seconds"])
                if response.status_code in _RETRY_HTTP | {418}:
                    limiter.cooldown(self._cooldown_delay(response, attempt))
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                logger.warning("⚠️ Публичный REST %s, попытка %s/%s: %s", key.id, attempt + 1, retries, exc)
                response = getattr(exc, "response", None)
                non_retryable = isinstance(exc, requests.HTTPError) and response is not None and response.status_code not in _RETRY_HTTP
                if non_retryable or attempt + 1 == retries:
                    raise
                self.sleep(2 ** attempt)
                continue
            if not self._retryable_body(key, payload):
                return payload
            # HTTP 200 с временной body-ошибкой расходует тот же бюджет попыток.
            # Cooldown сохраняется и при исчерпании попыток, для других reader/process.
            limiter.cooldown(self._cooldown_delay(response, attempt))
            exc = ValueError(f"Временная ошибка публичного REST {key.id}: {payload!r}")
            logger.warning("⚠️ Публичный REST %s, попытка %s/%s: %s", key.id, attempt + 1, retries, exc)
            if attempt + 1 == retries:
                raise exc
            self.sleep(2 ** attempt)

    def _page(self, key, start_ms, end_ms, limit):
        if key.exchange == "bingx":
            payload = self._get(key, "https://open-api.bingx.com/openApi/swap/v3/quote/klines", {
                "symbol": exchange_symbol(key), "interval": key.interval, "limit": limit, "timeZone": 0,
                "startTime": start_ms, "endTime": end_ms - 1,
            })
            if (not isinstance(payload, dict) or type(payload.get("code")) is not int
                                or payload["code"] != 0 or not isinstance(payload.get("data"), list)):
                raise ValueError(f"Ошибка публичного BingX klines: {payload!r}")
            rows = payload["data"]
        elif key.market_type == "spot":
            rows = self._get(key, "https://api.mexc.com/api/v3/klines", {
                "symbol": key.symbol, "interval": MEXC_SPOT_INTERVALS[key.interval],
                "startTime": start_ms, "endTime": end_ms - 1, "limit": limit,
            })
            if not isinstance(rows, list):
                raise ValueError("Некорректный публичный MEXC Spot klines")
        else:
            payload = self._get(key, f"https://api.mexc.com/api/v1/contract/kline/{exchange_symbol(key)}", {
                "interval": MEXC_INTERVALS[key.interval], "start": start_ms // 1000,
                "end": (end_ms - 1) // 1000,
            })
            if (not isinstance(payload, dict) or payload.get("success") is not True
                    or type(payload.get("code", 0)) is not int or payload.get("code", 0) != 0
                    or not isinstance(payload.get("data"), dict)):
                raise ValueError(f"Ошибка публичного MEXC Futures klines: {payload!r}")
            data = payload["data"]
            if any(not isinstance(data.get(name), list) for name in ("time", "open", "high", "low", "close", "vol")):
                raise ValueError("Некорректные массивы MEXC OHLCV")
            times = data["time"]
            if any(len(data[name]) != len(times) for name in ("open", "high", "low", "close", "vol")):
                raise ValueError("Массивы MEXC OHLCV имеют разную длину")
            rows = [{"timestamp": ts, "open": data["open"][i], "high": data["high"][i],
                     "low": data["low"][i], "close": data["close"][i], "volume": data["vol"][i]}
                    for i, ts in enumerate(times)]
        converted = []
        for item in rows:
            if isinstance(item, list):
                if len(item) < 6:
                    raise ValueError("Неполная REST свеча")
                item = dict(zip(("timestamp", "open", "high", "low", "close", "volume"), item[:6]))
            converted.append(item)
        return converted

    def __call__(self, key, *, start_ms=None, end_ms=None, limit=None):
        key = StreamKey.from_value(key)
        if not self.supports(key):
            raise ValueError(f"Нет проверенного публичного REST adapter для {key.id}; передайте rest_fetcher")
        limit = self.settings["history_limit"] if limit is None else max(1, int(limit))
        end_ms = int(time.time() * 1000) + 1 if end_ms is None else int(end_ms)
        start_ms = next_start(interval_start(end_ms - 1, key.interval), key.interval, -limit + 1) if start_ms is None else int(start_ms)
        if (key.exchange, key.market_type, key.interval) == ("mexc", "spot", "8h"):
            return self._spot_eight_hour(key, start_ms, end_ms, limit)
        page_size = min(limit, 1000 if key.exchange == "bingx" else (500 if key.market_type == "spot" else 2000))
        cursor, indexed = start_ms, {}
        while cursor < end_ms:
            page_end = min(end_ms, next_start(cursor, key.interval, page_size))
            rows = self._page(key, cursor, page_end, min(page_size, limit))
            for row in rows:
                ts = timestamp_ms(row.get("timestamp", row.get("time", row.get("t", row.get("snapshotTimeUTC")))))
                if start_ms <= ts < end_ms:
                    indexed[ts] = row
            # Продвигаем границу запроса, не полагаясь на направление сортировки ответа.
            cursor = page_end
        return [indexed[ts] for ts in sorted(indexed)[-limit:]]

    def _spot_eight_hour(self, key, start_ms, end_ms, limit):
        observed = time.time()
        base = StreamKey(key.exchange, key.market_type, key.symbol, "4h")
        start = interval_start(start_ms, "8h")
        rows = self(base, start_ms=start, end_ms=end_ms, limit=limit * 2 + 2)
        indexed = {timestamp_ms(row["timestamp"]): normalize_candle(row, base, now=observed, received_at=observed)
                   for row in rows}
        result = []
        while start < end_ms:
            middle, end = next_start(start, "4h"), next_start(start, "8h")
            expected = [start, middle] if middle <= int(observed * 1000) else [start]
            if start >= start_ms and all(ts in indexed for ts in expected):
                parts = [indexed[ts] for ts in expected]
                result.append({"timestamp": start, "open": parts[0]["openPrice"],
                               "close": parts[-1]["closePrice"], "high": max(p["highPrice"] for p in parts),
                               "low": min(p["lowPrice"] for p in parts), "volume": sum(p["volume"] for p in parts),
                               "is_closed": end <= int(observed * 1000) and all(p["is_closed"] for p in parts)})
            # Неполный исторический bucket не превращается в closed свечу; gap не скрываем.
            start = end
        return result[-limit:]
