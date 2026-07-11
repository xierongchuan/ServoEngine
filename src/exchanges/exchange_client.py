"""
Abstract base class for all exchange clients.
Defines the standard interface that all exchange implementations must follow.

Этот класс определяет контракт, который должны реализовать все клиенты бирж.
При добавлении новой биржи (Binance, Bybit и т.д.) нужно унаследовать этот класс.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict

from .dto.models import (
    Balance,
    Ticker,
    OrderBook,
    CommissionRate,
    FundingRate,
    OrderSide,
    OrderType,
    PositionSide,
    PositionsDict,
    OrdersList,
    KlinesList,
    ExchangeCapabilities,
    InstrumentRules,
    MarketType,
)


class ExchangeClient(ABC):
    """
    Abstract base class for all exchange clients.

    При реализации нового клиента биржи необходимо:
    1. Наследовать этот класс
    2. Реализовать все @abstractmethod методы
    3. Использовать DTO модели для возврата данных
    4. Обрабатывать ошибки и логировать их
    """

    # Имя биржи (должно быть переопределено в подклассе)
    EXCHANGE_NAME: str = "unknown"

    @property
    def capabilities(self) -> ExchangeCapabilities:
        """Возможности деривативного клиента по умолчанию."""
        return ExchangeCapabilities(
            market_type=MarketType.PERPETUAL,
            positions=True,
            shorting=True,
            leverage=True,
            funding=True,
            native_protection=True,
            automated_strategy=True,
        )

    def get_instrument_rules(self, symbol: str) -> Optional[InstrumentRules]:
        """Вернуть торговые правила, если биржа их предоставляет."""
        return None

    # =========================================================================
    # Market Data - получение рыночных данных
    # =========================================================================

    @abstractmethod
    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 288
    ) -> KlinesList:
        """
        Получить исторические данные свечей (klines).

        Args:
            symbol: Символ (напр. BTC-USDT)
            interval: Интервал свечей (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Количество свечей

        Returns:
            Список объектов Kline
        """
        pass

    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker:
        """
        Получить текущий тикер с лучшими bid/ask ценами.

        Args:
            symbol: Символ (напр. BTC-USDT)

        Returns:
            Объект Ticker
        """
        pass

    @abstractmethod
    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        """
        Получить стакан заявок (order book / depth).

        Args:
            symbol: Символ
            limit: Глубина стакана

        Returns:
            Объект OrderBook с bids и asks
        """
        pass

    # =========================================================================
    # Account - работа с аккаунтом
    # =========================================================================

    @abstractmethod
    def get_balance(self) -> Balance:
        """
        Получить баланс аккаунта.

        Returns:
            Объект Balance
        """
        pass

    @abstractmethod
    def get_commission_rate(self, symbol: str) -> Optional[CommissionRate]:
        """
        Получить ставки комиссий maker/taker для символа.

        Args:
            symbol: Символ

        Returns:
            Объект CommissionRate или None если недоступно
        """
        pass

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> Optional[FundingRate]:
        """
        Получить текущую ставку финансирования для символа.

        Args:
            symbol: Символ

        Returns:
            Объект FundingRate или None если недоступно
        """
        pass

    # =========================================================================
    # Positions - работа с позициями
    # =========================================================================

    @abstractmethod
    def get_positions(self) -> PositionsDict:
        """
        Получить все открытые позиции.

        Returns:
            Словарь {symbol: [positions]}, где positions - список объектов Position
        """
        pass

    @abstractmethod
    def close_position(
        self,
        symbol: str,
        position_id: str,
        percentage: float = 1.0
    ) -> bool:
        """
        Закрыть позицию (полностью или частично).

        Args:
            symbol: Символ
            position_id: ID позиции
            percentage: Доля закрытия (0.0 - 1.0), по умолчанию 1.0 (100%)

        Returns:
            True если успешно, False в противном случае
        """
        pass

    # =========================================================================
    # Orders - работа с ордерами
    # =========================================================================

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        position_side: Optional[PositionSide] = None,
        leverage: Optional[int] = None,
    ) -> Optional[str]:
        """
        Разместить ордер.

        Args:
            symbol: Символ
            side: Сторона (BUY/SELL)
            quantity: Количество
            order_type: Тип ордера (MARKET/LIMIT и т.д.)
            price: Цена (для лимитных ордеров)
            sl: Цена Stop Loss
            tp: Цена Take Profit
            position_side: Сторона позиции (LONG/SHORT для хедж режима)
            leverage: Плечо для ордера. Если не указано, клиент использует свой дефолт.

        Returns:
            ID ордера если успешно, None в противном случае
        """
        pass

    @abstractmethod
    def set_leverage(
        self,
        symbol: str,
        leverage: int,
        position_side: PositionSide = PositionSide.BOTH
    ) -> bool:
        """
        Установить кредитное плечо для символа.

        Args:
            symbol: Символ
            leverage: Кредитное плечо (напр. 10, 20, 50, 100)
            position_side: Сторона позиции

        Returns:
            True если успешно, False в противном случае
        """
        pass

    @abstractmethod
    def set_sl_tp(
        self,
        symbol: str,
        position_side: PositionSide,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        quantity: Optional[float] = None,
    ) -> bool:
        """
        Установить или обновить Stop Loss и Take Profit для позиции.

        Args:
            symbol: Символ
            position_side: Сторона позиции (LONG/SHORT)
            sl: Цена Stop Loss
            tp: Цена Take Profit
            quantity: Размер позиции (если None - получить с биржи)

        Returns:
            True если все успешно установлено, False в противном случае
        """
        pass

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        Отменить ордер.

        Args:
            symbol: Символ
            order_id: ID ордера

        Returns:
            True если успешно, False в противном случае
        """
        pass

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> OrdersList:
        """
        Получить открытые ордера.

        Args:
            symbol: Символ (если None - все символы)

        Returns:
            Список объектов Order
        """
        pass

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> bool:
        """
        Отменить все открытые ордера для символа.

        Args:
            symbol: Символ

        Returns:
            True если успешно, False в противном случае
        """
        pass

    @abstractmethod
    def get_recent_orders(self, symbol: str, limit: int = 10) -> OrdersList:
        """
        Получить последние ордера (все статусы) для символа.

        Args:
            symbol: Символ
            limit: Количество ордеров

        Returns:
            Список объектов Order
        """
        pass

    # =========================================================================
    # Utils - утилиты
    # =========================================================================

    @abstractmethod
    def normalize_symbol(self, symbol: str) -> str:
        """
        Нормализовать символ в формат биржи.

        BingX: BTCUSDT -> BTC-USDT
        Binance: BTCUSDT -> BTCUSDT

        Args:
            symbol: Символ в универсальном формате

        Returns:
            Символ в формате биржи
        """
        pass

    @abstractmethod
    def denormalize_symbol(self, symbol: str) -> str:
        """
        Преобразовать символ обратно в универсальный формат.

        Args:
            symbol: Символ в формате биржи

        Returns:
            Символ в универсальном формате
        """
        pass

    @abstractmethod
    def check_prerequisites(self) -> bool:
        """
        Проверить выполнение необходимых условий (API ключи и т.д.).

        Returns:
            True если всё готово к работе, False в противном случае
        """
        pass

    # =========================================================================
    # Cache Management - управление кэшем
    # =========================================================================

    def invalidate_cache(self, cache_type: Optional[str] = None) -> None:
        """
        Инвалидировать кэш.

        Args:
            cache_type: Тип кэша ('positions', 'balance', 'orders') или None для всего
        """
        # По умолчанию ничего не делает, переопределить в подклассе при необходимости
        pass

    # =========================================================================
    # Legacy methods - для обратной совместимости
    # =========================================================================

    def get_kline_data(self, symbol: str, interval: str = "5m", limit: int = 288) -> List[Dict]:
        """
        Legacy метод для обратной совместимости.
        Рекомендуется использовать get_klines().

        Returns:
            Список словарей с ключами: snapshotTimeUTC, openPrice, highPrice, lowPrice, closePrice, volume
        """
        klines = self.get_klines(symbol, interval, limit)
        result = []
        for k in klines:
            ts = k.timestamp
            if hasattr(ts, 'isoformat'):
                ts = ts.isoformat()
            result.append({
                "snapshotTimeUTC": ts if ts else "",
                "openPrice": k.open,
                "highPrice": k.high,
                "lowPrice": k.low,
                "closePrice": k.close,
                "volume": k.volume,
            })
        return result

    def get_positions_legacy(self) -> Dict[str, List[Dict]]:
        """
        Legacy метод для обратной совместимости.
        Рекомендуется использовать get_positions().

        Returns:
            Словарь в старом формате
        """
        positions = self.get_positions()
        result = {}
        for symbol, pos_list in positions.items():
            result[symbol] = [
                {
                    "type": "buy" if p.side == PositionSide.LONG else "sell",
                    "entry": p.entry_price,
                    "dealId": p.position_id,
                    "workingOrderId": p.position_id,
                    "created": p.created_at.isoformat() if p.created_at else None,
                    "size": p.size,
                    "pnl": p.unrealized_pnl,
                    "leverage": p.leverage,
                    "markPrice": p.mark_price,
                }
                for p in pos_list
            ]
        return result
