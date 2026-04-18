"""Индикаторы тренда: EMA, SMA, SEB, ADX."""

from typing import Dict, List, Tuple


def calculate_ema(prices: List[float], period: int) -> float:
    """Рассчитывает Exponential Moving Average."""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # Start with SMA

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 5)


def calculate_sma_series(values: List[float], period: int) -> List[float]:
    """Calculates SMA series for a list of values."""
    if len(values) < period:
        return [0] * len(values)

    sma_values = []
    pass_len = len(values)

    # Efficient rolling sum
    # First `period` are 0 or partial (we just use 0 padding for alignment simplicity)
    sma_values = [0] * (period - 1)

    # Calculate initial window
    current_sum = sum(values[:period])
    sma_values.append(current_sum / period)

    # Rolling
    for i in range(period, pass_len):
        current_sum = current_sum - values[i - period] + values[i]
        sma_values.append(current_sum / period)

    return sma_values


def calculate_ema_series(values: List[float], period: int) -> List[float]:
    """Calculates EMA series for a list of values. Returns array aligned with input."""
    n = len(values)
    if n < period:
        return [0.0] * n

    ema_values = [0.0] * (period - 1)
    # Seed with SMA
    sma_seed = sum(values[:period]) / period
    ema_values.append(sma_seed)

    multiplier = 2.0 / (period + 1)
    for i in range(period, n):
        ema_val = (values[i] - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(ema_val)

    return ema_values


def calculate_seb(prices: List[float], length: int = 50, mult: float = 2.0) -> Tuple[float, float, float, float]:
    """
    Calculates Standard Error Bands (Linear Regression + StdErr) for LAST point only.
    Use calculate_seb_series for full history.
    """
    import numpy as np

    if len(prices) < length:
        return 0, 0, 0, 0

    y = np.array(prices[-length:])
    x = np.arange(length)

    # Linear Regression (Least Squares)
    A = np.vstack([x, np.ones(len(x))]).T
    m, c = np.linalg.lstsq(A, y, rcond=None)[0]

    # Regression Line
    linreg = m * x + c

    # Standard Error
    residuals = y - linreg
    std_err = np.sqrt(np.sum(residuals**2) / (length - 2))

    # Bands
    linreg_current = linreg[-1]
    upper_seb = linreg_current + (mult * std_err)
    lower_seb = linreg_current - (mult * std_err)

    # R-Squared (Trend Quality)
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y - np.mean(y))**2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

    return linreg_current, upper_seb, lower_seb, r_squared


def calculate_seb_series(
    prices: List[float], length: int = 50, mult: float = 2.0
) -> Tuple[List[float], List[float], List[float]]:
    """
    Calculates Standard Error Bands (Linear Regression + StdErr) using rolling window.
    Returns arrays populated with the LAST regression line values for visualization.
    """
    import numpy as np

    n = len(prices)
    linreg_series = [0.0] * n
    upper_series = [0.0] * n
    lower_series = [0.0] * n

    if n < length:
        return linreg_series, upper_series, lower_series

    prices_arr = np.array(prices)
    x = np.arange(length)

    # Rolling calculation
    for i in range(length, n + 1):
        y = prices_arr[i - length: i]

        # Linreg for this window. We only need the ENDPOINT value (at x = length-1)
        m, c = np.polyfit(x, y, 1)

        # Value at the last point of window
        reg_val = m * (length - 1) + c

        # Std Err
        residuals = y - (m * x + c)
        std_err = np.sqrt(np.sum(residuals**2) / (length - 2))

        linreg_series[i - 1] = reg_val
        upper_series[i - 1] = reg_val + (mult * std_err)
        lower_series[i - 1] = reg_val - (mult * std_err)

    return linreg_series, upper_series, lower_series


def _wilder_smooth(values: List[float], period: int) -> List[float]:
    """Wilder's smoothing method (Wilders Exponential Moving Average)."""
    if len(values) < period:
        return []
    smoothed = [sum(values[:period])]
    for i in range(period, len(values)):
        smoothed.append(smoothed[-1] - (smoothed[-1] / period) + values[i])
    return smoothed


def calculate_adx(
    klines: List[Dict], period: int = 14
) -> Dict:
    """
    Рассчитывает ADX (Average Directional Index) и +DI/-DI.
    Оптимален для таймфреймов 15м, 1ч и выше.

    Args:
        klines: Список свечей с highPrice, lowPrice, closePrice
        period: Период расчёта (по умолчанию 14)

    Returns:
        {"adx": float, "plus_di": float, "minus_di": float, "trend": str}
    """
    if len(klines) < period + 1:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "trend": "UNKNOWN"}

    plus_dm, minus_dm, tr_values = [], [], []

    for i in range(1, len(klines)):
        high = klines[i]["highPrice"]
        low = klines[i]["lowPrice"]
        prev_high = klines[i - 1]["highPrice"]
        prev_low = klines[i - 1]["lowPrice"]
        prev_close = klines[i - 1]["closePrice"]

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

    smoothed_tr = _wilder_smooth(tr_values, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    if not smoothed_tr or smoothed_tr[-1] == 0:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "trend": "UNKNOWN"}

    plus_di_values, minus_di_values, dx_values = [], [], []

    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] > 0:
            plus_di = 100 * smoothed_plus_dm[i] / smoothed_tr[i]
            minus_di = 100 * smoothed_minus_dm[i] / smoothed_tr[i]
        else:
            plus_di, minus_di = 0.0, 0.0

        plus_di_values.append(plus_di)
        minus_di_values.append(minus_di)

        di_sum = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
        dx_values.append(dx)

    if len(dx_values) >= period:
        adx = sum(dx_values[-period:]) / period
    else:
        adx = sum(dx_values) / len(dx_values) if dx_values else 0.0

    plus_di = plus_di_values[-1] if plus_di_values else 0.0
    minus_di = minus_di_values[-1] if minus_di_values else 0.0

    if adx < 20:
        trend = "RANGING"
    elif adx < 25:
        trend = "WEAK_TREND"
    elif adx < 40:
        trend = "TRENDING_UP" if plus_di > minus_di else "TRENDING_DOWN"
    else:
        trend = "STRONG_TREND_UP" if plus_di > minus_di else "STRONG_TREND_DOWN"

    return {"adx": round(adx, 2), "plus_di": round(plus_di, 2), "minus_di": round(minus_di, 2), "trend": trend}


def calculate_adx_series(
    klines: List[Dict], period: int = 14
) -> Dict[str, List[float]]:
    """
    Рассчитывает полную историю ADX для визуализации.
    Возвращает массивы значений, выровненные по времени с входными данными.

    Args:
        klines: Список свечей с highPrice, lowPrice, closePrice
        period: Период расчёта (по умолчанию 14)

    Returns:
        {"adx": List[float], "plus_di": List[float], "minus_di": List[float], "trend": List[str]}
    """
    n = len(klines)
    if n < period + 1:
        return {"adx": [0.0] * n, "plus_di": [0.0] * n, "minus_di": [0.0] * n, "trend": ["UNKNOWN"] * n}

    plus_dm, minus_dm, tr_values = [], [], []

    for i in range(1, n):
        high = klines[i]["highPrice"]
        low = klines[i]["lowPrice"]
        prev_high = klines[i - 1]["highPrice"]
        prev_low = klines[i - 1]["lowPrice"]
        prev_close = klines[i - 1]["closePrice"]

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

    smoothed_tr = _wilder_smooth(tr_values, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    adx_series = [0.0] * n
    plus_di_series = [0.0] * n
    minus_di_series = [0.0] * n
    trend_series = ["UNKNOWN"] * n

    if not smoothed_tr:
        return {"adx": adx_series, "plus_di": plus_di_series, "minus_di": minus_di_series, "trend": trend_series}

    plus_di_values, minus_di_values, dx_values = [], [], []

    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] > 0:
            plus_di = 100 * smoothed_plus_dm[i] / smoothed_tr[i]
            minus_di = 100 * smoothed_minus_dm[i] / smoothed_tr[i]
        else:
            plus_di, minus_di = 0.0, 0.0

        plus_di_values.append(plus_di)
        minus_di_values.append(minus_di)

        di_sum = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
        dx_values.append(dx)

    for i in range(len(dx_values)):
        start_idx = max(0, i - period + 1)
        window = dx_values[start_idx:i + 1]
        adx_val = sum(window) / len(window)
        adx_series[i + 1] = round(adx_val, 2)
        plus_di_series[i + 1] = round(plus_di_values[i], 2)
        minus_di_series[i + 1] = round(minus_di_values[i], 2)

        if adx_val < 20:
            trend_series[i + 1] = "RANGING"
        elif adx_val < 25:
            trend_series[i + 1] = "WEAK_TREND"
        elif adx_val < 40:
            trend_series[i + 1] = "TRENDING_UP" if plus_di_values[i] > minus_di_values[i] else "TRENDING_DOWN"
        else:
            trend_series[i + 1] = "STRONG_TREND_UP" if plus_di_values[i] > minus_di_values[i] else "STRONG_TREND_DOWN"

    return {"adx": adx_series, "plus_di": plus_di_series, "minus_di": minus_di_series, "trend": trend_series}
