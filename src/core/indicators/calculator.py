"""Калькулятор индикаторов — обёртка над чистыми функциями."""

from typing import Dict, List, Tuple

from src.config import AI_THRESHOLDS, TECHNICAL_ANALYSIS
from .trend import calculate_ema_series
from .momentum import calculate_rsi_series


def calculate_indicators(prices: List[Dict]) -> Tuple[float, float]:
    """Рассчитывает ключевые индикаторы (SMA, RSI)."""
    if not prices:
        raise ValueError("Нет данных о ценах")

    try:
        closes = []
        for candle in prices:
            price_data = candle["closePrice"]
            if isinstance(price_data, dict):
                closes.append(float(price_data["bid"]))
            else:
                closes.append(float(price_data))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Некорректная структура данных о ценах: {str(e)}")

    sma_period = AI_THRESHOLDS["SMA_PERIOD"]
    rsi_period = AI_THRESHOLDS["RSI_PERIOD"]

    if len(closes) >= sma_period:
        sma = sum(closes[-sma_period:]) / sma_period
    else:
        sma = sum(closes) / len(closes)

    if len(closes) < rsi_period + 1:
        return round(sma, 5), 50.0

    rsi_series = calculate_rsi_series(closes, rsi_period)
    rsi = rsi_series[-1] if rsi_series else 50.0

    return round(sma, 5), round(rsi, 2)


def calculate_indicator_series(closes: List[float]) -> Dict:
    """Рассчитывает серии индикаторов для истории свечей."""
    rsi_series = calculate_rsi_series(closes)

    ema_periods = TECHNICAL_ANALYSIS.get("ema_periods", [9, 21])
    ema9_series = calculate_ema_series(closes, ema_periods[0])
    ema21_series = calculate_ema_series(closes, ema_periods[1] if len(ema_periods) > 1 else 21)

    return {
        "rsi": rsi_series,
        "ema9": ema9_series,
        "ema21": ema21_series,
    }
