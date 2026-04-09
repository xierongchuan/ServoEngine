import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import DATA_DIR
from ..services.auth import get_current_user

# Путь к base.json для получения plotter_ranges
import os
BASE_CONFIG_PATH = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'config', 'base.json'))

router = APIRouter(prefix="/api/chart-data", tags=["chart-data"])


def _read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
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


@router.get("/{symbol}")
async def get_chart_data(
    symbol: str,
    time_range: str = Query(default="1D", alias="range"),
    _user: dict = Depends(get_current_user),
) -> dict:
    """OHLCV + индикаторы + позиция для интерактивного графика."""

    safe_symbol = symbol.replace("/", "_")
    price_file = DATA_DIR / "prices" / f"{safe_symbol}.json"

    if not price_file.is_file():
        raise HTTPException(status_code=404, detail=f"No price data for {symbol}")

    raw_candles = _read_json(price_file, default=[])
    if not raw_candles:
        raise HTTPException(status_code=404, detail="Empty price data")

    # time_range параметр теперь используется только для определения временного диапазона
    range_seconds = 86400  # По умолчанию 1 день (86400 секунд)
    if time_range:
        range_str = time_range.upper()
        if range_str.endswith("D"):
            range_seconds = int(range_str[:-1]) * 86400
        elif range_str.endswith("W"):
            range_seconds = int(range_str[:-1]) * 7 * 86400
        elif range_str.endswith("H"):
            range_seconds = int(range_str[:-1]) * 3600
        elif range_str.endswith("M"):
            range_seconds = int(range_str[:-1]) * 60

    # Парсинг свечей (тот же формат что в plotter.py)
    candles = []
    for c in raw_candles:
        ts_str = c.get("snapshotTimeUTC", "")
        try:
            if ts_str.endswith("Z"):
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            ts = int(dt.timestamp())
        except (ValueError, AttributeError):
            continue

        # Поддержка обоих форматов цен (dict и float)
        if isinstance(c.get("closePrice"), dict):
            candles.append({
                "time": ts,
                "open": float(c["openPrice"]["bid"]),
                "high": float(c["highPrice"]["bid"]),
                "low": float(c["lowPrice"]["bid"]),
                "close": float(c["closePrice"]["bid"]),
                "volume": float(c.get("lastTradedVolume", 0)),
            })
        else:
            candles.append({
                "time": ts,
                "open": float(c.get("openPrice", 0)),
                "high": float(c.get("highPrice", 0)),
                "low": float(c.get("lowPrice", 0)),
                "close": float(c.get("closePrice", 0)),
                "volume": float(c.get("volume", 0)),
            })

    if not candles:
        raise HTTPException(status_code=404, detail="No parseable candle data")

    candles.sort(key=lambda x: x["time"])

    # === Определение фактического таймфрейма из свечей ===
    def _detect_timeframe(candles_list: list[dict]) -> int:
        """Определяет таймфрейм в минутах на основе разницы между свечами."""
        if len(candles_list) < 2:
            return 1  # По умолчанию 1 минута

        # Вычисляем разницы между соседними свечами
        diffs = []
        for i in range(1, min(len(candles_list), 20)):  # Берем первые 20 для стабильности
            diff = candles_list[i]["time"] - candles_list[i-1]["time"]
            diffs.append(diff)

        if not diffs:
            return 1

        # Медианная разница
        diffs.sort()
        median_diff = diffs[len(diffs) // 2]

        # Конвертируем в минуты
        minutes = median_diff // 60

        # Округляем до ближайшего стандартного таймфрейма
        standard_timeframes = [1, 5, 15, 30, 60, 120, 240, 360, 720, 1440]
        for tf in standard_timeframes:
            if tf * 60 >= median_diff:
                return tf

        return minutes if minutes > 0 else 1

    # Определяем фактический таймфрейм из загруженных свечей
    detected_tf_minutes = _detect_timeframe(candles)

    # Используем фактический таймфрейм свечей - никакого ресемплинга!
    # Графики показывают ровно то, что есть в данных

    # Фильтр по временному диапазону (отталкиваемся от последней свечи)
    latest_ts = candles[-1]["time"]
    cutoff_ts = latest_ts - range_seconds
    candles = [c for c in candles if c["time"] >= cutoff_ts]

    if not candles:
        raise HTTPException(status_code=404, detail=f"No data for range {time_range}")

    # Индикаторы
    closes = [c["close"] for c in candles]

    indicators: dict = {}

    # EMA 12
    ema12_values = _calculate_ema(closes, 12)
    indicators["ema12"] = [
        {"time": candles[i]["time"], "value": v}
        for i, v in enumerate(ema12_values)
        if v is not None
    ]

    # SMA 26
    sma26_values = _calculate_sma(closes, 26)
    indicators["sma26"] = [
        {"time": candles[i]["time"], "value": v}
        for i, v in enumerate(sma26_values)
        if v is not None
    ]

    rsi_values = _calculate_rsi(closes, 14)
    indicators["rsi"] = [
        {"time": candles[i]["time"], "value": v}
        for i, v in enumerate(rsi_values)
        if v is not None
    ]

    # MACD (12, 26, 9)
    macd_line, signal_line, histogram = _calculate_macd(closes)
    indicators["macd"] = [
        {"time": candles[i]["time"], "value": v}
        for i, v in enumerate(macd_line)
        if v is not None
    ]
    indicators["macd_signal"] = [
        {"time": candles[i]["time"], "value": v}
        for i, v in enumerate(signal_line)
        if v is not None
    ]
    indicators["macd_histogram"] = [
        {"time": candles[i]["time"], "value": v}
        for i, v in enumerate(histogram)
        if v is not None
    ]

    # Позиция
    active_trades = _read_json(DATA_DIR / "active_trades.json", default={})
    position = None
    # Handle both dict and list formats for active_trades
    trade = None
    if isinstance(active_trades, dict):
        trade = active_trades.get(symbol)
    elif isinstance(active_trades, list):
        # Find trade by symbol in list format
        trade = next((t for t in active_trades if isinstance(t, dict) and t.get("symbol") == symbol), None)
    if trade and trade.get("status") == "OPEN":
        position = {
            "side": trade.get("side", "UNKNOWN"),
            "entry_price": float(trade.get("entry_price", 0)),
            "sl": float(trade.get("sl", 0) or 0),
            "tp": float(trade.get("tp", 0) or 0),
            "pnl": float(trade.get("last_pnl", 0) or 0),
            "leverage": trade.get("leverage"),
        }

    # available_ranges теперь формируется на основе фактических данных
    # Просто возвращаем стандартные периоды для UI
    available_ranges = ["15m", "30m", "1h", "2h", "4h", "6h", "12h", "1D", "2D", "3D", "1W", "14D"]

    # interval показываем фактический (определённый из свечей)
    tf_minutes_to_str = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h", 360: "6h", 720: "12h", 1440: "1d"}
    display_interval = tf_minutes_to_str.get(detected_tf_minutes, f"{detected_tf_minutes}m")

    return {
        "symbol": symbol,
        "range": time_range,
        "interval": display_interval,
        "candles": candles,
        "indicators": indicators,
        "position": position,
        "available_ranges": available_ranges,
    }
