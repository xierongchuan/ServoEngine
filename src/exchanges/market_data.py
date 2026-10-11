"""Идентичность потока, строгие свечи и атомарные snapshots рыночных данных."""

from __future__ import annotations

import copy
import math
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping


_INTERVAL_ALIASES = {
    "MINUTE_1": "1m", "MINUTE_5": "5m", "MINUTE_15": "15m", "MINUTE_30": "30m",
    "HOUR_1": "1h", "HOUR_2": "2h", "HOUR_4": "4h", "HOUR_6": "6h", "HOUR_8": "8h", "DAY_1": "1d",
    "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m",
    "1hour": "1h", "2hour": "2h", "4hour": "4h", "6hour": "6h", "8hour": "8h", "12hour": "12h",
        "1day": "1d", "1week": "1w", "1month": "1M",
    "Min1": "1m", "Min5": "5m", "Min15": "15m", "Min30": "30m", "Min60": "1h",
    "Hour4": "4h", "Hour8": "8h", "Day1": "1d", "Week1": "1w", "Month1": "1M",
}
DEFAULT_SETTINGS = {
    "max_age_seconds": 30.0, "reconcile_interval_seconds": 15.0,
    "history_limit": 600, "min_history": 100, "heartbeat_interval_seconds": 15.0,
    "heartbeat_timeout_seconds": 30.0, "subscription_timeout_seconds": 30.0,
    "reconnect_delay_seconds": 1.0, "max_reconnect_delay_seconds": 60.0,
    "rest_timeout_seconds": 10.0, "rest_retries": 3, "future_tolerance_seconds": 5.0,
}


def market_data_settings(settings=None):
    result = dict(DEFAULT_SETTINGS)
    result.update(settings or {})
    for name in DEFAULT_SETTINGS:
        value = float(result[name])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Некорректная настройка market_data: {name}")
        if name in {"history_limit", "min_history", "rest_retries"} and (value != int(value) or isinstance(result[name], bool)):
            raise ValueError(f"Настройка market_data должна быть целым числом: {name}")
        result[name] = int(value) if name in {"history_limit", "min_history", "rest_retries"} else value
    result["min_history"] = min(result["min_history"], result["history_limit"])
    return result


def canonical_symbol(symbol):
    value = re.sub(r"[-_/]", "", str(symbol).strip().upper())
    if not re.fullmatch(r"[A-Z0-9]{2,40}", value):
        raise ValueError(f"Некорректный символ: {symbol!r}")
    return value


def canonical_interval(interval):
    value = _INTERVAL_ALIASES.get(str(interval), str(interval))
    if not re.fullmatch(r"[1-9][0-9]*(?:m|h|d|w|M)", value):
        raise ValueError(f"Некорректный таймфрейм: {interval!r}")
    if value.endswith("M") and value != "1M":
        raise ValueError("Поддерживается только календарный месяц 1M")
    return value


@dataclass(frozen=True, order=True)
class StreamKey:
    exchange: str
    market_type: str
    symbol: str
    interval: str

    def __post_init__(self):
        exchange = str(self.exchange).lower()
        market = str(self.market_type).lower()
        if exchange not in {"bingx", "mexc"} or market not in {"spot", "perpetual"}:
            raise ValueError(f"Неподдерживаемый источник: {exchange}/{market}")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "market_type", market)
        object.__setattr__(self, "symbol", canonical_symbol(self.symbol))
        object.__setattr__(self, "interval", canonical_interval(self.interval))

    @property
    def id(self):
        return f"{self.exchange}:{self.market_type}:{self.symbol}:{self.interval}"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(*value.split(":"))
        return cls(**{name: value[name] for name in ("exchange", "market_type", "symbol", "interval")})


def timestamp_ms(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Отсутствует timestamp свечи")
    if isinstance(value, datetime):
        dt = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        value = dt.timestamp() * 1000
    elif isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
            value = dt.timestamp() * 1000
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Некорректный timestamp свечи")
    if number < 10_000_000_000:
        number *= 1000
    if number != int(number):
        raise ValueError("Timestamp должен иметь точность до миллисекунды")
    return int(number)


def interval_start(ts_ms, interval):
    interval = canonical_interval(interval)
    value, unit = int(interval[:-1]), interval[-1]
    if unit == "M":
        dt = datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
        return int(dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    duration = value * {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}[unit]
    # Начало недель UTC — понедельник, а не четверг Unix epoch.
    anchor = 345_600_000 if unit == "w" else 0
    return ((ts_ms - anchor) // duration) * duration + anchor


def next_start(ts_ms, interval, steps=1):
    interval = canonical_interval(interval)
    if interval == "1M":
        dt = datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
        month = dt.year * 12 + dt.month - 1 + steps
        return int(dt.replace(year=month // 12, month=month % 12 + 1, day=1).timestamp() * 1000)
    duration = int(interval[:-1]) * {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}[interval[-1]]
    return ts_ms + duration * steps


def _field(raw, names):
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    raise ValueError(f"Отсутствует поле свечи: {names[0]}")


def _number(value, name):
    if isinstance(value, Mapping):
        value = value.get("bid")
    if isinstance(value, bool):
        raise ValueError(f"Некорректное поле {name}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Нечисловое поле {name}")
    return number


def normalize_candle(raw, key, *, source="rest", now=None, received_at=None, future_tolerance=5.0):
    key = StreamKey.from_value(key)
    if not isinstance(raw, Mapping):
        raise ValueError("Свеча должна быть словарём")
    now = time.time() if now is None else now
    ts = timestamp_ms(_field(raw, ("timestamp", "open_time_ms", "t", "time", "snapshotTimeUTC")))
    if ts > (now + future_tolerance) * 1000:
        raise ValueError("Свеча из будущего")
    if interval_start(ts, key.interval) != ts:
        raise ValueError(f"Timestamp не совпадает с границей {key.interval}")
    if raw.get("symbol") and canonical_symbol(raw["symbol"]) != key.symbol:
        raise ValueError("Символ свечи не совпадает с потоком")
    if raw.get("interval") and canonical_interval(raw["interval"]) != key.interval:
        raise ValueError("Таймфрейм свечи не совпадает с потоком")
    for name in ("timestamp", "open_time_ms", "t", "time", "snapshotTimeUTC"):
        if raw.get(name) is not None and timestamp_ms(raw[name]) != ts:
            raise ValueError("Поля времени открытия свечи не совпадают")
    o = _number(_field(raw, ("openPrice", "open", "o")), "open")
    h = _number(_field(raw, ("highPrice", "high", "h")), "high")
    l = _number(_field(raw, ("lowPrice", "low", "l")), "low")
    c = _number(_field(raw, ("closePrice", "close", "c")), "close")
    v = _number(_field(raw, ("volume", "v", "vol", "q", "lastTradedVolume")), "volume")
    if min(o, h, l, c) <= 0 or v < 0 or h < max(o, c, l) or l > min(o, c, h):
        raise ValueError("Нарушены OHLCV-инварианты свечи")
    observed_at = min(now, float(received_at)) if source == "rest" and received_at is not None else now
    elapsed = next_start(ts, key.interval) <= int(observed_at * 1000)
    flag = next((raw[name] for name in ("is_closed", "closed", "x") if name in raw), None)
    if any(not isinstance(raw[name], bool) or raw[name] != flag for name in ("is_closed", "closed", "x") if name in raw):
        raise ValueError("Флаги закрытия свечи должны быть согласованными bool")
    closed = elapsed and (flag if flag is not None else source == "rest")
    received = now if received_at is None else float(received_at)
    if not math.isfinite(received) or received <= 0 or received > now + future_tolerance:
        raise ValueError("Некорректное время получения свечи")
    result = {
        "timestamp": ts, "snapshotTimeUTC": datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(),
        "openPrice": o, "highPrice": h, "lowPrice": l, "closePrice": c, "volume": v,
        "is_closed": closed, "closed": closed, "source": source, "received_at": received,
        "rest_confirmed": source == "rest" and closed,
        "volume_unit": "contracts" if key.exchange == "mexc" and key.market_type == "perpetual" else "base",
    }
    if raw.get("event_time_ms") is not None:
        result["event_time_ms"] = timestamp_ms(raw["event_time_ms"])
        if result["event_time_ms"] < ts or result["event_time_ms"] > (now + future_tolerance) * 1000:
            raise ValueError("Некорректное время события свечи")
    return result


def merge_candles(existing, incoming, *, limit=600):
    """REST подтверждает закрытие; поздний open-update не отменяет final свечу."""
    indexed = {row["timestamp"]: dict(row) for row in existing}
    for row in incoming:
        old = indexed.get(row["timestamp"])
        if old:
            if old.get("rest_confirmed") and not row.get("rest_confirmed"):
                continue
            if old.get("is_closed") and not row.get("is_closed"):
                continue
            authoritative = row.get("rest_confirmed") and not old.get("rest_confirmed")
            if not authoritative:
                if old.get("event_time_ms") and row.get("event_time_ms") and row["event_time_ms"] <= old["event_time_ms"]:
                    continue
                if row.get("received_at", 0) < old.get("received_at", 0):
                    continue
        indexed[row["timestamp"]] = dict(row)
    return [indexed[ts] for ts in sorted(indexed)[-int(limit):]]


def candle_gaps(candles, interval):
    gaps = []
    for previous, current in zip(candles, candles[1:]):
        expected = next_start(previous["timestamp"], interval)
        if expected < current["timestamp"]:
            gaps.append({"start_ms": expected, "end_ms": current["timestamp"]})
    return gaps


def evaluate_snapshot(snapshot, *, now=None, max_age_seconds=None):
    """Свежесть вычисляется и читателем: зависший publisher не оставляет вечный ready."""
    result = copy.deepcopy(snapshot)
    now = time.time() if now is None else now
    age_limit = result.get("max_age_seconds", 30) if max_age_seconds is None else max_age_seconds
    last = result.get("last_valid_update")
    age = math.inf if last is None else max(0.0, now - last)
    rows = result.get("candles", [])
    coverage = bool(rows) and next_start(rows[-1]["timestamp"], result["stream"]["interval"]) >= interval_start(int(now * 1000), result["stream"]["interval"])
    result["stale"] = age > age_limit or not coverage
    result["age_seconds"] = None if math.isinf(age) else age
    result["ready"] = bool(
        not result["stale"] and not result.get("error") and not result.get("gaps")
        and result.get("status") in {"live", "rest"} and len(rows) >= result.get("min_history", 1)
    )
    return result


class StreamStore:
    """Единственный writer; весь envelope публикуется одним присваиванием Manager.dict."""

    def __init__(self, cache=None, ready=None, settings=None, clock=time.time):
        self.cache = {} if cache is None else cache
        self.ready = {} if ready is None else ready
        self.settings = market_data_settings(settings)
        self.clock = clock
        self.lock = threading.RLock()
        self.publisher_id = uuid.uuid4().hex
        self._versions = {}

    def reconfigure(self, settings):
        options = market_data_settings({**self.settings, **(settings or {})})
        with self.lock:
            self.settings = options
            for value in list(self.cache.values()):
                key = StreamKey.from_value(value["stream"])
                value = copy.deepcopy(value)
                value["candles"] = value["candles"][-options["history_limit"]:]
                value["gaps"] = candle_gaps(value["candles"], key.interval)
                value["max_age_seconds"] = options["max_age_seconds"]
                value["min_history"] = options["min_history"]
                self._publish(key, value)

    def ensure(self, key):
        key = StreamKey.from_value(key)
        with self.lock:
            if key.id not in self.cache:
                self.cache[key.id] = {
                    "schema_version": 1, "stream": key.to_dict(), "candles": [], "status": "recovering",
                    "error": None, "gaps": [], "last_valid_update": None, "version": 0,
                    "max_age_seconds": self.settings["max_age_seconds"], "min_history": self.settings["min_history"],
                    "transport": "ws", "connected": False, "subscribed": False,
                }
                self.ready[key.id] = False
        return key

    def snapshot(self, key, **kwargs):
        key = StreamKey.from_value(key)
        value = self.cache.get(key.id)
        return evaluate_snapshot(value, now=kwargs.pop("now", self.clock()), **kwargs) if value else None

    def _publish(self, key, value):
        value["version"] = max(value["version"], self._versions.get(key.id, 0)) + 1
        self._versions[key.id] = value["version"]
        value["publisher_id"] = self.publisher_id
        value["published_at"] = self.clock()
        value = evaluate_snapshot(value, now=self.clock())
        self.cache[key.id] = value
        self.ready[key.id] = value["ready"]
        return copy.deepcopy(value)

    def mark(self, key, **changes):
        key = self.ensure(key)
        with self.lock:
            value = copy.deepcopy(self.cache[key.id])
            value.update(changes)
            return self._publish(key, value)

    def merge(self, key, rows, *, source="rest", received_at=None, status=None):
        key = self.ensure(key)
        # Вся пачка валидируется до записи, частичное принятие не скрывает ошибку API.
        normalized = [normalize_candle(row, key, source=source, now=self.clock(), received_at=received_at,
                                       future_tolerance=self.settings["future_tolerance_seconds"]) for row in rows]
        with self.lock:
            value = copy.deepcopy(self.cache[key.id])
            value["candles"] = merge_candles(value["candles"], normalized, limit=self.settings["history_limit"])
            value["gaps"] = candle_gaps(value["candles"], key.interval)
            if value["candles"]:
                tail = value["candles"][-1]
                accepted_tail = any(row["timestamp"] == tail["timestamp"] and row == tail for row in normalized)
                if accepted_tail:
                    value["last_valid_update"] = max(value.get("last_valid_update") or 0,
                                                     min(self.clock(), tail["received_at"]))
            if status is not None:
                value["status"] = status
                value["error"] = None
            elif source == "ws" and value["status"] in {"live", "rest"}:
                value["status"] = "live"
            return self._publish(key, value)

    def remove(self, key):
        key = StreamKey.from_value(key)
        with self.lock:
            self.cache.pop(key.id, None)
            self.ready.pop(key.id, None)


def select_stream(cache, symbol, interval=None, *, exchange=None, market_type=None):
    symbol = canonical_symbol(symbol)
    interval = canonical_interval(interval) if interval is not None else None
    matches = []
    for value in list(cache.values()):
        if not isinstance(value, Mapping) or "stream" not in value:
            continue
        stream = value["stream"]
        if stream["symbol"] != symbol or (interval and stream["interval"] != interval):
            continue
        if exchange and stream["exchange"] != exchange.lower():
            continue
        if market_type and stream["market_type"] != market_type.lower():
            continue
        matches.append(value)
    return matches[0] if len(matches) == 1 else None


def closed_candles(rows, interval, *, now=None):
    now_ms = int((time.time() if now is None else now) * 1000)
    return [dict(row) for row in rows if row.get("is_closed", row.get("closed", True))
            and next_start(timestamp_ms(row.get("timestamp", row.get("snapshotTimeUTC"))), interval) <= now_ms]
