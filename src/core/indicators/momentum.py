"""Индикаторы импульса: MACD, RSI, RSI divergence detection."""

from typing import List, Tuple


def calculate_macd(
    prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[float, float, float, float, float]:
    """
    Рассчитывает MACD (Moving Average Convergence Divergence)
    Returns: (macd_line, signal_line, histogram, histogram_prev, histogram_2prev)

    FIX: Now correctly calculates previous histogram for proper crossover detection.
    Добавлен histogram_2prev - значение гистограммы 2 свечи назад для проверки что пересечение было недавно.
    """
    from src.utils.logger import debug, warning

    # Debug: log prices count and sample
    min_required = slow + signal
    if len(prices) < min_required:
        warning(f"[MACD] Недостаточно данных: {len(prices)} свечей, требуется минимум {min_required} (slow={slow} + signal={signal})")
        debug(f"[MACD] Рекомендация: увеличьте количество запрашиваемых свечей или используйте более длинный таймфрейм")
        return 0, 0, 0, 0, 0

    # Import EMA from trend module to avoid duplication
    from .trend import calculate_ema

    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow

    # Debug: log EMA values
    debug(f"[MACD] Prices: {len(prices)}, EMA_fast: {ema_fast:.2f}, EMA_slow: {ema_slow:.2f}, MACD: {macd_line:.4f}")

    # Calculate MACD history for signal line
    macd_history = []
    for i in range(slow, len(prices)):
        subset = prices[:i + 1]
        ef = calculate_ema(subset, fast)
        es = calculate_ema(subset, slow)
        macd_history.append(ef - es)

    if len(macd_history) >= signal:
        # Track signal line history for proper prev_histogram calculation
        signal_history = []
        prev_signal_line = 0.0

        # Initialize signal line with SMA
        signal_line = sum(macd_history[:signal]) / signal

        # Calculate k for EMA smoothing
        k = 2.0 / (signal + 1)

        # Process remaining values and track EACH signal line value
        for i, m in enumerate(macd_history[signal:]):
            prev_signal_line = signal_line  # Save previous before updating
            signal_line = m * k + signal_line * (1 - k)
            # Store ALL signal line values for later use (to get previous signal)
            signal_history.append(signal_line)

        # Current histogram - use last value from history, not the full-array macd_line
        histogram = macd_history[-1] - signal_line

        # FIX: Use stored signal_history to get previous signal line
        # signal_history[-1] is current, signal_history[-2] is previous (n-1 period)
        if len(signal_history) >= 2:
            # Use the actual signal line value from (n-1) period that we stored
            histogram_prev = macd_history[-2] - signal_history[-2]
        elif len(macd_history) >= 2:
            # Not enough signal history, approximate with current signal
            histogram_prev = macd_history[-2] - signal_line
        else:
            histogram_prev = histogram

        # Дополнительно вычисляем гистограмму 2 свечи назад
        if len(signal_history) >= 3:
            # signal_history[-3] это значение 2 периода назад
            histogram_2prev = macd_history[-3] - signal_history[-3]
        elif len(macd_history) >= 3:
            # Приблизительная оценка
            histogram_2prev = macd_history[-3] - signal_line
        else:
            histogram_2prev = histogram_prev
    else:
        signal_line = macd_line
        histogram = 0
        histogram_prev = 0
        histogram_2prev = 0

    return round(macd_line, 6), round(signal_line, 6), round(histogram, 6), round(histogram_prev, 6), round(histogram_2prev, 6)


def calculate_rsi_series(prices: List[float], period: int = 14) -> List[float]:
    """Calculates RSI series using Wilder's smoothing method."""
    if len(prices) < period + 1:
        return [50.0] * len(prices)

    rsi_values = [50.0] * len(prices)  # Default 50

    gains = []
    losses = []

    # Calculate changes
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    # First Average Gain/Loss
    if len(deltas) < period:
        return rsi_values

    # Initial avg
    seed_gains = [d for d in deltas[:period] if d > 0]
    seed_losses = [-d for d in deltas[:period] if d < 0]

    avg_gain = sum(seed_gains) / period
    avg_loss = sum(seed_losses) / period

    # First RSI at index `period`
    if avg_loss == 0:
        rs = 100
        first_rsi = 100
    else:
        rs = avg_gain / avg_loss
        first_rsi = 100 - (100 / (1 + rs))

    rsi_values[period] = first_rsi

    # Smoothing
    for i in range(period + 1, len(prices)):
        delta = prices[i] - prices[i - 1]
        gain = delta if delta > 0 else 0
        loss = -delta if delta < 0 else 0

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        rsi_values[i] = rsi

    return rsi_values


def detect_rsi_divergence(
    prices: List[float], rsi_values: List[float], window: int = 20
) -> Tuple[bool, bool]:
    """
    Detects bullish and bearish RSI divergence.
    Returns: (bullish_divergence, bearish_divergence)
    """
    if len(prices) < window or len(rsi_values) < window:
        return False, False

    recent_prices = prices[-window:]
    recent_rsi = rsi_values[-window:]

    bullish_divergence = False
    bearish_divergence = False

    # Bullish divergence: price makes lower low, RSI makes higher low
    if len(recent_prices) >= 2:
        price_low = min(recent_prices)
        price_low_idx = recent_prices.index(price_low)
        rsi_at_low = recent_rsi[price_low_idx] if price_low_idx < len(recent_rsi) else 50

        # Check if earlier low had higher RSI
        for i in range(price_low_idx - 1, max(0, price_low_idx - 10), -1):
            if recent_prices[i] > price_low and recent_rsi[i] < rsi_at_low:
                # Price went down but RSI went up = bullish divergence
                if recent_rsi[i] < 40:  # RSI in oversold zone
                    bullish_divergence = True
                    break

    # Bearish divergence: price makes higher high, RSI makes lower high
    if len(recent_prices) >= 2:
        price_high = max(recent_prices)
        price_high_idx = recent_prices.index(price_high)
        rsi_at_high = recent_rsi[price_high_idx] if price_high_idx < len(recent_rsi) else 50

        # Check if earlier high had lower RSI
        for i in range(price_high_idx - 1, max(0, price_high_idx - 10), -1):
            if recent_prices[i] < price_high and recent_rsi[i] > rsi_at_high:
                # Price went up but RSI went down = bearish divergence
                if recent_rsi[i] > 60:  # RSI in overbought zone
                    bearish_divergence = True
                    break

    return bullish_divergence, bearish_divergence
