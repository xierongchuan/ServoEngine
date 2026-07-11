"""Контракт Spot-клиента без фиктивных фьючерсных позиций."""

from abc import ABC, abstractmethod
from typing import List, Optional

from .dto.models import (
    AssetBalance,
    CommissionRate,
    ExchangeCapabilities,
    InstrumentRules,
    KlinesList,
    OrderBook,
    OrdersList,
    OrderSubmission,
    SpotOrderRequest,
    Ticker,
)


class SpotExchangeClient(ABC):
    EXCHANGE_NAME = "unknown"

    @property
    @abstractmethod
    def capabilities(self) -> ExchangeCapabilities:
        raise NotImplementedError

    @abstractmethod
    def get_klines(self, symbol: str, interval: str = "5m", limit: int = 288) -> KlinesList:
        raise NotImplementedError

    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker:
        raise NotImplementedError

    @abstractmethod
    def get_order_book(self, symbol: str, limit: int = 20) -> OrderBook:
        raise NotImplementedError

    @abstractmethod
    def get_instrument_rules(self, symbol: str) -> InstrumentRules:
        raise NotImplementedError

    @abstractmethod
    def get_asset_balances(self) -> List[AssetBalance]:
        raise NotImplementedError

    @abstractmethod
    def get_commission_rate(self, symbol: str) -> Optional[CommissionRate]:
        raise NotImplementedError

    @abstractmethod
    def place_spot_order(self, request: SpotOrderRequest) -> OrderSubmission:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> OrdersList:
        raise NotImplementedError

    @abstractmethod
    def get_recent_orders(self, symbol: str, limit: int = 10) -> OrdersList:
        raise NotImplementedError

    @abstractmethod
    def normalize_symbol(self, symbol: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def denormalize_symbol(self, symbol: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def check_prerequisites(self) -> bool:
        raise NotImplementedError

    def get_kline_data(self, symbol: str, interval: str = "5m", limit: int = 288):
        result = []
        for candle in self.get_klines(symbol, interval, limit):
            timestamp = candle.timestamp.isoformat() if hasattr(candle.timestamp, "isoformat") else candle.timestamp
            result.append({
                "snapshotTimeUTC": timestamp or "",
                "openPrice": candle.open,
                "highPrice": candle.high,
                "lowPrice": candle.low,
                "closePrice": candle.close,
                "volume": candle.volume,
            })
        return result
