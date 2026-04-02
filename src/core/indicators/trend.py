"""Индикаторы тренда: EMA, SMA, SEB (Standard Error Bands)."""

from typing import List, Tuple


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
