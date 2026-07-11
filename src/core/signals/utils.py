"""Общие утилиты для генераторов сигналов — дедупликация."""

from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Tuple


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
        if position is None:
            raise ValueError("Позиция не может быть None")
        if not isinstance(position, Mapping) and not hasattr(position, "entry_price"):
            raise TypeError(f"Неподдерживаемый контракт позиции: {type(position).__name__}")
        self._position = position

    def _get(self, *keys: str, default=None):
        if isinstance(self._position, Mapping):
            for key in keys:
                value = self._position.get(key)
                if value is not None:
                    return value
        return default

    @property
    def entry_price(self) -> float:
        if hasattr(self._position, 'entry_price'):
            return float(self._position.entry_price)
        return float(self._get("entry", "avgPrice", "entry_price", default=0) or 0)

    @property
    def direction(self) -> str:
        if hasattr(self._position, 'is_long'):
            return "BUY" if self._position.is_long else "SELL"
        raw = str(self._get("type", "side", "positionSide", default="LONG")).upper()
        return "BUY" if raw in ("BUY", "LONG") else "SELL"

    @property
    def is_long(self) -> bool:
        if hasattr(self._position, 'is_long'):
            return self._position.is_long
        return self.direction == "BUY"

    @property
    def position_id(self) -> str:
        if hasattr(self._position, "position_id"):
            return str(self._position.position_id or "")
        return str(self._get("dealId", "positionId", "position_id", default="") or "")

    @property
    def size(self) -> float:
        if hasattr(self._position, "size"):
            return float(self._position.size)
        return abs(float(self._get("size", "amount", "positionAmt", default=0) or 0))

    @property
    def unrealized_pnl(self) -> float:
        if hasattr(self._position, "unrealized_pnl"):
            return float(self._position.unrealized_pnl)
        return float(self._get("pnl", "unrealizedPnl", "unrealizedProfit", default=0) or 0)

    @property
    def mark_price(self) -> float:
        if hasattr(self._position, "mark_price"):
            return float(self._position.mark_price or self.entry_price)
        return float(self._get("markPrice", "mark_price", default=self.entry_price) or self.entry_price)

    @property
    def leverage(self) -> Optional[int]:
        value = getattr(self._position, "leverage", None)
        if value is None:
            value = self._get("leverage")
        return int(float(value)) if value not in (None, "") else None

    @property
    def created_at(self) -> Optional[datetime]:
        value = getattr(self._position, "created_at", None)
        if value is None:
            value = self._get("created", "createdAt", "createTime")
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class OrderAdapter:
    """Единый read-only доступ к Order DTO и legacy-ответам API."""

    def __init__(self, order: Any):
        if not isinstance(order, Mapping) and not hasattr(order, "order_id"):
            raise TypeError(f"Неподдерживаемый контракт ордера: {type(order).__name__}")
        self._order = order

    def _get(self, *keys: str, default=None):
        if isinstance(self._order, Mapping):
            for key in keys:
                value = self._order.get(key)
                if value is not None:
                    return value
        return default

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value) or "").upper()

    @property
    def order_id(self) -> str:
        value = getattr(self._order, "order_id", None)
        return str(value if value is not None else self._get("orderId", "order_id", default=""))

    @property
    def side(self) -> str:
        value = getattr(self._order, "side", None)
        return self._enum_value(value if value is not None else self._get("side"))

    @property
    def status(self) -> str:
        value = getattr(self._order, "status", None)
        return self._enum_value(value if value is not None else self._get("status"))

    @property
    def price(self) -> float:
        value = getattr(self._order, "price", None)
        return float(value if value is not None else self._get("price", default=0) or 0)

    @property
    def average_price(self) -> float:
        value = getattr(self._order, "average_price", None)
        if value not in (None, 0, 0.0):
            return float(value)
        return float(self._get("avgPrice", "dealAvgPrice", "average_price", "price", default=self.price) or self.price)

    @property
    def realized_pnl(self) -> float:
        value = getattr(self._order, "realized_pnl", None)
        return float(value if value is not None else self._get("profit", "realizedPnl", default=0) or 0)

    @property
    def commission(self) -> float:
        value = getattr(self._order, "commission", None)
        return float(value if value is not None else self._get("commission", default=0) or 0)

    @property
    def updated_at(self) -> Optional[datetime]:
        value = getattr(self._order, "updated_at", None)
        if value is None:
            value = self._get("updateTime", "updatedAt")
        if value in (None, "", 0):
            return None
        if isinstance(value, datetime):
            return value
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, timezone.utc)
