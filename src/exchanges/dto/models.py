"""
Data Transfer Objects (DTO) for exchange operations.
Provides unified data structures across all exchange implementations.

Эти модели используются для стандартизации данных между разными биржами.
Каждая биржа (BingX, Binance, Bybit) должна преобразовывать свои данные в эти модели.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Union
from enum import Enum


class OrderSide(Enum):
    """Сторона ордера"""
    BUY = "BUY"
    SELL = "SELL"

    def opposite(self) -> "OrderSide":
        """Возвращает противоположную сторону"""
        return OrderSide.SELL if self == OrderSide.BUY else OrderSide.BUY


class OrderType(Enum):
    """Тип ордера"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class OrderStatus(Enum):
    """Статус ордера"""
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    PENDING_CANCEL = "PENDING_CANCEL"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PositionSide(Enum):
    """Сторона позиции (для хедж режима)"""
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"  # One-way mode


class PositionStatus(Enum):
    """Статус позиции"""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


class MarketType(Enum):
    """Тип торгового продукта."""
    PERPETUAL = "perpetual"
    SPOT = "spot"


class SubmissionStatus(Enum):
    """Результат отправки торговой команды."""
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExchangeCapabilities:
    """Явно описывает возможности клиента и запрещает опасные догадки."""
    market_type: MarketType
    market_data: bool = True
    account_balances: bool = True
    orders: bool = True
    positions: bool = False
    shorting: bool = False
    leverage: bool = False
    funding: bool = False
    native_protection: bool = False
    attached_protection: bool = False
    automated_strategy: bool = False


@dataclass(frozen=True)
class InstrumentRules:
    """Торговые ограничения инструмента в точных Decimal-единицах."""
    symbol: str
    exchange_symbol: str
    base_asset: str
    quote_asset: str
    tradable: bool
    price_step: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal = Decimal("0")
    max_notional: Decimal = Decimal("0")
    contract_size: Decimal = Decimal("1")
    min_leverage: int = 1
    max_leverage: int = 1
    leverage_tiers: tuple[Dict[str, Any], ...] = ()
    order_types: tuple[str, ...] = ()
    trade_side_type: int = 1


@dataclass(frozen=True)
class AssetBalance:
    """Баланс отдельного Spot-актива."""
    asset: str
    free: Decimal
    locked: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass(frozen=True)
class SpotOrderRequest:
    """Spot-ордер с раздельными base и quote количествами."""
    symbol: str
    side: OrderSide
    order_type: OrderType
    base_quantity: Optional[Decimal] = None
    quote_quantity: Optional[Decimal] = None
    price: Optional[Decimal] = None
    client_order_id: Optional[str] = None
    test_only: bool = False


@dataclass(frozen=True)
class OrderSubmission:
    """Результат отправки ордера с поддержкой неоднозначного состояния."""
    status: SubmissionStatus
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    message: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Position:
    """
    Представляет открытую позицию на бирже.
    frozen=True делает класс immutable для безопасности в многопоточной среде.
    """
    symbol: str
    side: PositionSide
    size: float  # Размер позиции (абсолютное значение)
    entry_price: float  # Средняя цена входа
    unrealized_pnl: float  # Нереализованный P&L
    leverage: Optional[int] = None  # Кредитное плечо
    position_id: str = ""  # ID позиции на бирже
    mark_price: Optional[float] = None  # Маркировочная цена
    liquidation_price: Optional[float] = None  # Цена ликвидации
    margin: Optional[float] = None  # Залог
    created_at: Optional[datetime] = None  # Время открытия
    updated_at: Optional[datetime] = None  # Время обновления
    exchange_quantity: Optional[float] = None  # Нативный объём биржи (контракты)
    contract_size: Optional[float] = None  # Размер одного контракта

    @property
    def is_long(self) -> bool:
        return self.side == PositionSide.LONG

    @property
    def is_short(self) -> bool:
        return self.side == PositionSide.SHORT

    @property
    def notional_value(self) -> float:
        """Номинальная стоимость позиции"""
        return self.size * self.entry_price

    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь для сериализации"""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "size": self.size,
            "entry_price": self.entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "leverage": self.leverage,
            "position_id": self.position_id,
            "mark_price": self.mark_price,
        }


@dataclass(frozen=True)
class Order:
    """
    Представляет ордер на бирже.
    """
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    price: float  # Цена ордера (для лимитных)
    quantity: float  # Запрошенное количество
    filled_quantity: float = 0.0  # Исполненное количество
    average_price: float = 0.0  # Средняя цена исполнения
    commission: float = 0.0  # Комиссия
    realized_pnl: float = 0.0  # Реализованный P&L (для закрытых ордеров)
    stop_price: Optional[float] = None  # Стоп-цена
    position_side: Optional[PositionSide] = None  # Сторона позиции
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)  # Сырые данные от API

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_canceled(self) -> bool:
        return self.status == OrderStatus.CANCELED

    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity

    @property
    def fill_percentage(self) -> float:
        if self.quantity == 0:
            return 0.0
        return (self.filled_quantity / self.quantity) * 100


@dataclass(frozen=True)
class Balance:
    """
    Представляет баланс аккаунта.
    """
    total_balance: float  # Общий баланс
    available_balance: float  # Доступный баланс
    unrealized_pnl: float = 0.0  # Нереализованный P&L
    locked_balance: float = 0.0  # Заблокированный баланс (в ордерах)
    asset: str = "USDT"  # Актив

    @property
    def used_balance(self) -> float:
        """Использованный баланс"""
        return self.total_balance - self.available_balance

    @property
    def total_with_pnl(self) -> float:
        """Общий баланс с учетом P&L"""
        return self.total_balance + self.unrealized_pnl


@dataclass(frozen=True)
class Kline:
    """
    Представляет свечу (OHLCV).
    """
    timestamp: Union[datetime, str]
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True  # Закрыта ли свеча

    @property
    def typical_price(self) -> float:
        """Типичная цена (HLC/3)"""
        return (self.high + self.low + self.close) / 3

    @property
    def range_price(self) -> float:
        """Диапазон (H-L)"""
        return self.high - self.low

    @property
    def change_percent(self) -> float:
        """Процент изменения"""
        if self.open == 0:
            return 0.0
        return ((self.close - self.open) / self.open) * 100

    @property
    def is_bullish(self) -> bool:
        """Бычья свеча"""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Медвежья свеча"""
        return self.close < self.open


@dataclass(frozen=True)
class Ticker:
    """
    Представляет тикер (текущая цена и объемы).
    """
    symbol: str
    last_price: float
    bid_price: float  # Лучшая цена покупки
    ask_price: float  # Лучшая цена продажи
    volume_24h: float  # Объем за 24ч
    quote_volume_24h: float = 0.0  # Объем в USDT за 24ч
    price_change_24h: float = 0.0  # Изменение цены за 24ч
    price_change_percent_24h: float = 0.0  # % изменение за 24ч
    high_24h: float = 0.0  # Максимум за 24ч
    low_24h: float = 0.0  # Минимум за 24ч
    mark_price: Optional[float] = None  # Маркировочная цена
    index_price: Optional[float] = None  # Индексная цена
    funding_rate: Optional[float] = None  # Ставка финансирования
    next_funding_time: Optional[datetime] = None  # Следующее финансирование

    @property
    def spread(self) -> float:
        """Спред (ask - bid)"""
        return self.ask_price - self.bid_price

    @property
    def spread_percent(self) -> float:
        """Спред в процентах"""
        if self.bid_price == 0:
            return 0.0
        return (self.spread / self.bid_price) * 100

    @property
    def mid_price(self) -> float:
        """Средняя цена"""
        return (self.bid_price + self.ask_price) / 2


@dataclass(frozen=True)
class OrderBook:
    """
    Представляет стакан заявок (order book).
    """
    symbol: str
    bids: List[List[float]]  # [[price, quantity], ...]
    asks: List[List[float]]  # [[price, quantity], ...]
    last_update_id: int = 0

    @property
    def best_bid(self) -> Optional[float]:
        """Лучшая цена покупки"""
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Лучшая цена продажи"""
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        """Средняя цена"""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    def get_bids_volume(self, depth: int = 5) -> float:
        """Объем bids в глубину depth"""
        return sum(b[1] for b in self.bids[:depth])

    def get_asks_volume(self, depth: int = 5) -> float:
        """Объем asks в глубину depth"""
        return sum(a[1] for a in self.asks[:depth])


@dataclass(frozen=True)
class CommissionRate:
    """
    Представляет ставки комиссий.
    """
    maker: float  # Комиссия мейкера (в процентах, напр. 0.02 = 0.02%)
    taker: float  # Комиссия тейкера (в процентах)

    @property
    def effective_maker(self) -> float:
        """Эффективная мейкерская комиссия"""
        return self.maker / 100

    @property
    def effective_taker(self) -> float:
        """Эффективная тейкерская комиссия"""
        return self.taker / 100


@dataclass(frozen=True)
class FundingRate:
    """
    Представляет ставку финансирования.
    """
    funding_rate: float  # Ставка финансирования
    funding_rate_pct: float  # Ставка в процентах
    next_funding_time: Optional[Union[datetime, str]] = None  # Следующее время финансирования

    @property
    def is_positive(self) -> bool:
        """Положительная ставка (платят держатели long)"""
        return self.funding_rate > 0

    @property
    def is_negative(self) -> bool:
        """Отрицательная ставка (платят держатели short)"""
        return self.funding_rate < 0


@dataclass(frozen=True)
class Trade:
    """
    Представляет сделку (исполненный ордер).
    """
    trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    commission: float
    realized_pnl: float = 0.0
    timestamp: Optional[datetime] = None


# Type aliases для удобства
PositionsDict = Dict[str, List[Position]]
OrdersList = List[Order]
KlinesList = List[Kline]
