"""Общие утилиты для генераторов сигналов — дедупликация."""

from typing import Any, Dict, List, Tuple


def detect_rsi_divergence(prices: List[float], rsi_values: List[float], window: int = 30) -> Tuple[bool, bool]:
    """
    Detect RSI divergence over the given window.
    Unified version from all signal generators.

    Returns:
        (bearish_divergence, bullish_divergence) booleans
    """
    if not prices or not rsi_values:
        return False, False

    n = min(len(prices), len(rsi_values))
    if n < 10:
        return False, False

    # Limit window for relevance
    prices = prices[-window:]
    rsi_values = rsi_values[-window:]
    n = len(prices)

    min_extrema_distance = 3

    # Find local maxima (for bearish divergence detection)
    maxima = []
    for i in range(1, n - 1):
        if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
            if not maxima or (i - maxima[-1]) >= min_extrema_distance:
                maxima.append(i)

    # Find local minima (for bullish divergence detection)
    minima = []
    for i in range(1, n - 1):
        if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
            if not minima or (i - minima[-1]) >= min_extrema_distance:
                minima.append(i)

    bearish_div = False
    bullish_div = False

    # Bearish divergence: price higher high + RSI lower high
    if len(maxima) >= 2:
        prev_max = maxima[-2]
        last_max = maxima[-1]
        if prices[last_max] > prices[prev_max] and rsi_values[last_max] < rsi_values[prev_max]:
            bearish_div = True

    # Bullish divergence: price lower low + RSI higher low
    if len(minima) >= 2:
        prev_min = minima[-2]
        last_min = minima[-1]
        if prices[last_min] < prices[prev_min] and rsi_values[last_min] > rsi_values[prev_min]:
            bullish_div = True

    return bearish_div, bullish_div


def map_quality_to_confidence(quality: float, has_signal: bool) -> float:
    """Unified quality -> confidence mapping used by all signal generators."""
    if not has_signal:
        return 0.0
    if quality >= 0.7:
        return 0.85
    elif quality >= 0.4:
        return 0.70
    else:
        return 0.55


def calculate_pnl_pct(entry_price: float, current_price: float, direction: str) -> float:
    """
    Calculate PnL percentage.
    LONG (BUY): (current - entry) / entry * 100
    SHORT (SELL): (entry - current) / entry * 100
    """
    if entry_price <= 0:
        return 0.0
    if direction.upper() in ("BUY", "LONG"):
        return (current_price - entry_price) / entry_price * 100
    else:
        return (entry_price - current_price) / entry_price * 100


class PositionAdapter:
    """Единая работа с позицией — dict или dataclass."""

    def __init__(self, position: Any):
        self._position = position

    @property
    def entry_price(self) -> float:
        if hasattr(self._position, 'entry_price'):
            return float(self._position.entry_price)
        return float(self._position.get("entry", self._position.get("avgPrice", 0)))

    @property
    def direction(self) -> str:
        if hasattr(self._position, 'is_long'):
            return "BUY" if self._position.is_long else "SELL"
        return self._position.get("type", "LONG").upper()

    @property
    def is_long(self) -> bool:
        if hasattr(self._position, 'is_long'):
            return self._position.is_long
        return self._position.get("type", "LONG").upper() in ("BUY", "LONG")
