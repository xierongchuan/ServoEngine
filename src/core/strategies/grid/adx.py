"""ADX (Average Directional Index) calculation."""

from typing import Dict, List


def _wilder_smooth(values: List[float], period: int) -> List[float]:
    """Wilder's smoothing method."""
    if len(values) < period:
        return []
    smoothed = [sum(values[:period])]
    for i in range(period, len(values)):
        smoothed.append(smoothed[-1] - (smoothed[-1] / period) + values[i])
    return smoothed


def calculate_adx(klines: List[Dict], period: int = 14) -> Dict:
    """
    Рассчитывает ADX (Average Directional Index) и +DI/-DI.

    Args:
        klines: Список свечей с highPrice, lowPrice, closePrice
        period: Период для расчета (обычно 14)

    Returns:
        {"adx": float, "plus_di": float, "minus_di": float, "trend": str}
    """
    if len(klines) < period + 1:
        return {"adx": 0, "plus_di": 0, "minus_di": 0, "trend": "UNKNOWN"}

    # 1. Рассчитываем +DM, -DM и TR
    plus_dm = []
    minus_dm = []
    tr_values = []

    for i in range(1, len(klines)):
        high = klines[i]["highPrice"]
        low = klines[i]["lowPrice"]
        prev_high = klines[i - 1]["highPrice"]
        prev_low = klines[i - 1]["lowPrice"]
        prev_close = klines[i - 1]["closePrice"]

        # True Range
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_values.append(tr)

        # Directional Movement
        up_move = high - prev_high
        down_move = prev_low - low

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)

    # 2. Smoothed averages (Wilder's smoothing)
    smoothed_tr = _wilder_smooth(tr_values, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    if not smoothed_tr or smoothed_tr[-1] == 0:
        return {"adx": 0, "plus_di": 0, "minus_di": 0, "trend": "UNKNOWN"}

    # 3. +DI и -DI
    plus_di_values = []
    minus_di_values = []
    dx_values = []

    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] > 0:
            plus_di = 100 * smoothed_plus_dm[i] / smoothed_tr[i]
            minus_di = 100 * smoothed_minus_dm[i] / smoothed_tr[i]
        else:
            plus_di = 0
            minus_di = 0

        plus_di_values.append(plus_di)
        minus_di_values.append(minus_di)

        # DX
        di_sum = plus_di + minus_di
        if di_sum > 0:
            dx = 100 * abs(plus_di - minus_di) / di_sum
        else:
            dx = 0
        dx_values.append(dx)

    # 4. ADX = smoothed DX
    if len(dx_values) >= period:
        adx_values = _wilder_smooth(dx_values, period)
        adx = adx_values[-1] if adx_values else 0
    else:
        adx = sum(dx_values) / len(dx_values) if dx_values else 0

    plus_di = plus_di_values[-1] if plus_di_values else 0
    minus_di = minus_di_values[-1] if minus_di_values else 0

    # 5. Определяем тренд
    if adx < 20:
        trend = "RANGING"
    elif adx < 25:
        trend = "WEAK_TREND"
    elif adx < 40:
        trend = "TRENDING_UP" if plus_di > minus_di else "TRENDING_DOWN"
    else:
        trend = "STRONG_TREND_UP" if plus_di > minus_di else "STRONG_TREND_DOWN"

    return {
        "adx": round(adx, 2),
        "plus_di": round(plus_di, 2),
        "minus_di": round(minus_di, 2),
        "trend": trend
    }
