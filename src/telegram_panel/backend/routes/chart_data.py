import json
import math
import os
import re
from collections import Counter

from src.utils.helpers import get_filename
from src.exchanges.market_data_storage import list_market_snapshots
from src.exchanges.market_data import canonical_symbol, StreamKey
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import DATA_DIR
from ..services.auth import get_current_user


router = APIRouter(prefix="/api/chart-data", tags=["chart-data"])


def _range_to_seconds(value: str) -> Optional[int]:
    """Преобразовать UI-диапазон в секунды."""
    text = str(value or "").strip().upper()
    if not text or text == "AUTO":
        return None
    multipliers = {"M": 60, "H": 3600, "D": 86400, "W": 7 * 86400, "Y": 365 * 86400}
    unit = text[-1]
    if unit not in multipliers:
        return None
    try:
        seconds = int(text[:-1]) * multipliers[unit]
        return seconds if seconds > 0 else None
    except ValueError:
        return None


def _recommended_range(timeframe_minutes: int) -> str:
    """Диапазон, на котором MACD и тренд читаются без перегрузки графика."""
    if timeframe_minutes >= 1440:
        return "180D"
    if timeframe_minutes >= 60:
        return "30D"
    if timeframe_minutes >= 30:
        return "14D"
    if timeframe_minutes >= 15:
        return "1W"
    if timeframe_minutes >= 5:
        return "3D"
    return "6h"


def _available_ranges(timeframe_minutes: int) -> list[str]:
    if timeframe_minutes >= 1440:
        return ["30D", "90D", "180D", "1Y"]
    if timeframe_minutes >= 60:
        return ["3D", "1W", "14D", "30D", "90D", "180D"]
    if timeframe_minutes >= 30:
        return ["1D", "3D", "1W", "14D", "30D"]
    if timeframe_minutes >= 15:
        return ["12h", "1D", "3D", "1W", "14D"]
    if timeframe_minutes >= 5:
        return ["6h", "12h", "1D", "3D", "1W"]
    return ["1h", "2h", "4h", "6h", "12h", "1D", "3D"]


def _read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _calculate_sma(closes: list[float], period: int) -> list[Optional[float]]:
    """SMA с None для первых period-1 значений."""
    result: list[Optional[float]] = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            window = closes[i - period + 1 : i + 1]
            result.append(round(sum(window) / period, 6))
    return result


def _calculate_rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """RSI — тот же алгоритм что в plotter.py."""
    if len(closes) < period:
        return [None] * len(closes)

    result: list[Optional[float]] = [None] * (period - 1)

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        deltas = [window[j] - window[j - 1] for j in range(1, len(window))]
        gains = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        avg_gain = sum(gains) / len(deltas)
        avg_loss = sum(losses) / len(deltas)

        if avg_loss == 0:
            rsi = 100.0
        elif avg_gain == 0:
            rsi = 0.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        result.append(round(rsi, 2))

    return result


def _calculate_ema(values: list[float], period: int) -> list[Optional[float]]:
    """EMA с None для первых period-1 значений."""
    if len(values) < period:
        return [None] * len(values)
    result: list[Optional[float]] = [None] * (period - 1)
    multiplier = 2 / (period + 1)
    # Первое значение EMA = SMA первых period значений
    ema = sum(values[:period]) / period
    result.append(round(ema, 6))
    for i in range(period, len(values)):
        ema = (values[i] - ema) * multiplier + ema
        result.append(round(ema, 6))
    return result


def _calculate_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """MACD line, Signal line, Histogram."""
    ema_fast = _calculate_ema(closes, fast)
    ema_slow = _calculate_ema(closes, slow)

    macd_line: list[Optional[float]] = []
    for f, s in zip(ema_fast, ema_slow):
        if f is not None and s is not None:
            macd_line.append(round(f - s, 6))
        else:
            macd_line.append(None)

    # Signal = EMA(signal) от не-None значений MACD
    macd_values = [v for v in macd_line if v is not None]
    signal_line_raw = _calculate_ema(macd_values, signal) if len(macd_values) >= signal else [None] * len(macd_values)

    # Раскладываем обратно по индексам
    signal_line: list[Optional[float]] = []
    histogram: list[Optional[float]] = []
    sig_idx = 0
    for m in macd_line:
        if m is None:
            signal_line.append(None)
            histogram.append(None)
        else:
            s = signal_line_raw[sig_idx] if sig_idx < len(signal_line_raw) else None
            signal_line.append(s)
            histogram.append(round(m - s, 6) if s is not None else None)
            sig_idx += 1

    return macd_line, signal_line, histogram


def _resample_candles(candles: list[dict], target_minutes: int) -> list[dict]:
    """Ресемплинг 1-минутных свечей в целевой таймфрейм."""
    if target_minutes <= 1:
        return candles

    resampled = []
    bucket: list[dict] = []
    bucket_start: Optional[int] = None

    for c in candles:
        ts = c["time"]
        current_start = (ts // (target_minutes * 60)) * target_minutes * 60

        if bucket_start is None:
            bucket_start = current_start

        if current_start != bucket_start and bucket:
            resampled.append({
                "time": bucket_start,
                "open": bucket[0]["open"],
                "high": max(x["high"] for x in bucket),
                "low": min(x["low"] for x in bucket),
                "close": bucket[-1]["close"],
                "volume": sum(x["volume"] for x in bucket),
            })
            bucket = []
            bucket_start = current_start

        bucket.append(c)

    if bucket and bucket_start is not None:
        resampled.append({
            "time": bucket_start,
            "open": bucket[0]["open"],
            "high": max(x["high"] for x in bucket),
            "low": min(x["low"] for x in bucket),
            "close": bucket[-1]["close"],
            "volume": sum(x["volume"] for x in bucket),
        })

    return resampled


@router.get("/sources/{symbol}")
async def get_chart_sources(symbol: str, _user: dict = Depends(get_current_user)) -> list[dict]:
    try:
        normalized = canonical_symbol(symbol)
    except ValueError:
        raise HTTPException(400, "Некорректный символ")
    return sorted([value["stream"] for value in list_market_snapshots(DATA_DIR)
                   if value["stream"]["symbol"] == normalized],
                  key=lambda stream: (stream["exchange"], stream["market_type"], stream["interval"]))


@router.get("/{symbol}")
async def get_chart_data(
    symbol: str,
    time_range: str = Query(default="AUTO", alias="range"),
    _user: dict = Depends(get_current_user),
    exchange: Optional[str] = None,
    market_type: Optional[str] = None,
    interval: Optional[str] = None,
) -> dict:
    """OHLCV + индикаторы + позиция для интерактивного графика."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]{0,63}", symbol) or ".." in symbol:
        raise HTTPException(status_code=400, detail="Некорректный символ")
    safe_symbol = get_filename(symbol)
    price_file = DATA_DIR / "prices" / f"{safe_symbol}.json"

    try:
        normalized = canonical_symbol(symbol)
        snapshots = [snapshot for snapshot in list_market_snapshots(DATA_DIR)
                     if snapshot["stream"]["symbol"] == normalized]
        requested = None
        if any((exchange, market_type, interval)):
            if exchange is None or market_type is None or interval is None:
                raise HTTPException(400, "Укажите exchange, market_type и interval вместе")
            requested = StreamKey(exchange, market_type, symbol, interval).to_dict()
    except ValueError:
        raise HTTPException(400, "Некорректный источник графика")
    snapshot = next((value for value in snapshots if value["stream"] == requested), None) if requested else snapshots[0] if len(snapshots) == 1 else None
    if requested and snapshot is None:
        raise HTTPException(404, "Снимок выбранного потока пока недоступен")
    if snapshot:
        raw_candles = snapshot["candles"]
        snapshot_updated_at = snapshot.get("last_valid_update")
    else:
        if price_file.is_symlink() or not price_file.is_file():
            if snapshots:
                raise HTTPException(409, "Доступно несколько потоков. Укажите источник и интервал явно.")
            raise HTTPException(404, "Данные графика пока недоступны")
        # Метаданные и свечи читаются из одного inode, даже при atomic replace publisher.
        try:
            with price_file.open(encoding="utf-8") as stream:
                snapshot_updated_at = os.fstat(stream.fileno()).st_mtime
                raw_candles = json.load(stream)
        except (OSError, ValueError):
            raise HTTPException(503, "Снимок графика обновляется. Повторите запрос.")
    if not isinstance(raw_candles, list) or not raw_candles:
        raise HTTPException(status_code=404, detail="Empty price data")

    # Повторный timestamp заменяется последней валидной свечой из снимка.
    by_time = {}
    for c in raw_candles:
        if not isinstance(c, dict):
            continue
        ts_str = c.get("snapshotTimeUTC", "")
        try:
            if ts_str.endswith("Z"):
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(ts_str)
                dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            ts = int(dt.timestamp())
        except (ValueError, AttributeError):
            continue

        try:
            candle: dict[str, float | int] = {"time": ts}
            for field in ("open", "high", "low", "close"):
                value = c[field + "Price"]
                candle[field] = float(value["bid"] if isinstance(value, dict) else value)
            candle["volume"] = float(c.get("volume", c.get("lastTradedVolume", 0)))
            if ts <= 0 or not all(math.isfinite(value) for value in candle.values()):
                continue
            if min(candle[field] for field in ("open", "high", "low", "close")) <= 0 or candle["volume"] < 0:
                continue
            if candle["high"] < max(candle["open"], candle["close"], candle["low"]) or candle["low"] > min(candle["open"], candle["close"]):
                continue
            by_time[ts] = candle
        except (KeyError, TypeError, ValueError, OverflowError):
            continue

    candles = sorted(by_time.values(), key=lambda candle: candle["time"])
    if not candles:
        raise HTTPException(status_code=404, detail="No parseable candle data")

    # Без metadata нельзя выдавать вычисленный интервал за контракт провайдера.
    diffs = Counter(b["time"] - a["time"] for a, b in zip(candles, candles[1:]))
    interval_seconds = min(diffs, key=lambda value: (-diffs[value], value)) if diffs else None
    if snapshot:
        explicit_interval = snapshot["stream"]["interval"]
        unit_seconds = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        interval_seconds = (int(explicit_interval[:-1]) * unit_seconds[explicit_interval[-1]]
                            if explicit_interval[-1] in unit_seconds else None)
    detected_tf_minutes = (43200 if snapshot and snapshot["stream"]["interval"] == "1M"
                           else max(1, (interval_seconds or 60) // 60))

    requested_range = str(time_range or "AUTO").upper()
    resolved_range = (
        _recommended_range(detected_tf_minutes)
        if requested_range == "AUTO"
        else requested_range
    )
    range_seconds = _range_to_seconds(resolved_range)
    if range_seconds is None:
        raise HTTPException(status_code=400, detail=f"Invalid chart range: {time_range}")

    # Используем фактический таймфрейм свечей - никакого ресемплинга!
    # Графики показывают ровно то, что есть в данных

    # Индикаторы считаем по всей загруженной истории, а диапазон UI
    # применяем только после warm-up. Иначе MACD на коротком zoom исчезает.
    indicator_candles = candles
    closes = [c["close"] for c in indicator_candles]

    # Фильтр по временному диапазону (отталкиваемся от последней свечи)
    latest_ts = candles[-1]["time"]
    cutoff_ts = latest_ts - range_seconds
    candles = [c for c in candles if c["time"] >= cutoff_ts]

    if not candles:
        raise HTTPException(status_code=404, detail=f"No data for range {resolved_range}")

    indicators: dict = {}

    # EMA 12
    ema12_values = _calculate_ema(closes, 12)
    indicators["ema12"] = [
        {"time": indicator_candles[i]["time"], "value": v}
        for i, v in enumerate(ema12_values)
        if v is not None
    ]

    # SMA 26
    sma26_values = _calculate_sma(closes, 26)
    indicators["sma26"] = [
        {"time": indicator_candles[i]["time"], "value": v}
        for i, v in enumerate(sma26_values)
        if v is not None
    ]

    rsi_values = _calculate_rsi(closes, 14)
    indicators["rsi"] = [
        {"time": indicator_candles[i]["time"], "value": v}
        for i, v in enumerate(rsi_values)
        if v is not None
    ]

    # MACD (12, 26, 9)
    macd_line, signal_line, histogram = _calculate_macd(closes)
    indicators["macd"] = [
        {"time": indicator_candles[i]["time"], "value": v}
        for i, v in enumerate(macd_line)
        if v is not None
    ]
    indicators["macd_signal"] = [
        {"time": indicator_candles[i]["time"], "value": v}
        for i, v in enumerate(signal_line)
        if v is not None
    ]
    indicators["macd_histogram"] = [
        {"time": indicator_candles[i]["time"], "value": v}
        for i, v in enumerate(histogram)
        if v is not None
    ]

    # В UI отдаём только выбранное окно, но значения уже имеют полный warm-up.
    indicators = {
        name: [point for point in series if point["time"] >= cutoff_ts]
        for name, series in indicators.items()
    }

    # Позиция
    active_trades = _read_json(DATA_DIR / "active_trades.json", default={})
    position = None
    # Совместимость словаря и списка, с нормализацией обозначения символа.
    trade = None
    if isinstance(active_trades, dict):
        trade = next((value for key, value in active_trades.items()
                      if re.sub(r"[-_/]", "", str(key)).upper() == normalized), None)
    elif isinstance(active_trades, list):
        trade = next((t for t in active_trades if isinstance(t, dict)
                      and re.sub(r"[-_/]", "", str(t.get("symbol", ""))).upper() == normalized), None)
    if isinstance(trade, dict) and str(trade.get("status", "")).upper() == "OPEN":
        try:
            values = {name: float(trade.get(field, 0) or 0) for name, field in
                      (("entry_price", "entry_price"), ("sl", "sl"), ("tp", "tp"), ("pnl", "last_pnl"))}
            if all(math.isfinite(value) for value in values.values()):
                position = {"side": trade.get("side", "UNKNOWN"), **values, "leverage": trade.get("leverage")}
        except (TypeError, ValueError, OverflowError):
            pass

    available_ranges = _available_ranges(detected_tf_minutes)

    # interval показываем фактический (определённый из свечей)
    tf_minutes_to_str = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h", 360: "6h", 720: "12h", 1440: "1d"}
    display_interval = (tf_minutes_to_str.get(detected_tf_minutes, f"{detected_tf_minutes}m")
                        if interval_seconds and interval_seconds % 60 == 0
                        else f"{interval_seconds}s" if interval_seconds else "unknown")

    return {
        "symbol": symbol,
        "range": resolved_range,
        "interval": snapshot["stream"]["interval"] if snapshot else display_interval,
        "candles": candles,
        "indicators": indicators,
        "position": position,
        "available_ranges": available_ranges,
        "available_sources": [value["stream"] for value in snapshots],
        "source": {
            "kind": "market_snapshot" if snapshot else "legacy_file",
            "stream": snapshot["stream"] if snapshot else None,
            "interval_inferred": snapshot is None,
            "interval_seconds": interval_seconds,
            "snapshot_updated_at": snapshot_updated_at,
            "last_candle_at": latest_ts,
            "status": snapshot.get("status") if snapshot else None,
            "stale": snapshot.get("stale") if snapshot else None,
            "ready": snapshot.get("ready") if snapshot else None,
            "age_seconds": snapshot.get("age_seconds") if snapshot else None,
            "version": snapshot.get("version") if snapshot else None,
        },
    }
