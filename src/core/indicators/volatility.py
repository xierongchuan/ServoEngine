"""Индикаторы волатильности: ATR, Bollinger Bands."""

from typing import Dict, List, Tuple


def calculate_atr(prices_data: List[Dict], period: int = 14) -> float:
    """
    Рассчитывает Average True Range
    :param prices_data: Список свечей с high, low, close
    :param period: Период для расчета ATR
    :return: ATR значение
    """
    from .levels import get_price_value

    if len(prices_data) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(prices_data)):
        high = get_price_value(prices_data[i].get("highPrice", 0))
        low = get_price_value(prices_data[i].get("lowPrice", 0))
        prev_close = get_price_value(prices_data[i - 1].get("closePrice", 0))

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # ATR = SMA of True Range
    if len(true_ranges) >= period:
        atr = sum(true_ranges[-period:]) / period
    else:
        atr = sum(true_ranges) / len(true_ranges)

    return round(atr, 5)


def calculate_bollinger_bands(
    prices: List[float], period: int = 20, std_mult: float = 2.0
) -> Tuple[float, float, float, float]:
    """
    Рассчитывает Bollinger Bands
    Returns: (upper_band, middle_band, lower_band, width_percent)
    """
    if len(prices) < period:
        return 0, 0, 0, 0

    recent = prices[-period:]
    middle = sum(recent) / period

    # Calculate standard deviation
    variance = sum((p - middle) ** 2 for p in recent) / period
    std_dev = variance ** 0.5

    upper = middle + (std_mult * std_dev)
    lower = middle - (std_mult * std_dev)

    # Width as percentage
    width_pct = ((upper - lower) / middle * 100) if middle > 0 else 0

    return round(upper, 5), round(middle, 5), round(lower, 5), round(width_pct, 2)
