from abc import ABC, abstractmethod

class ExchangeClient(ABC):
    """
    Abstract base class for all exchange clients.
    Defines the standard interface that all exchange implementations must follow.
    """

    @abstractmethod
    def check_prerequisites(self):
        """
        Checks if all necessary API keys and configurations are set.
        Returns True if ready, False otherwise.
        """
        pass

    @abstractmethod
    def get_balance(self):
        """
        Retrieves the account balance.
        Returns a dictionary or float representing the balance.
        """
        pass

    @abstractmethod
    def get_kline_data(self, symbol, interval="5m", limit=288):
        """
        Retrieves historical candle data (klines).
        Returns a list of dictionaries with keys: snapshotTimeUTC, openPrice, highPrice, lowPrice, closePrice, volume.
        """
        pass

    @abstractmethod
    def get_positions(self):
        """
        Retrieves current open positions.
        Returns a dictionary where keys are symbols and values are lists of position dictionaries.
        Position dict format: {type, entry, dealId, workingOrderId, created, size, pnl}
        """
        pass

    @abstractmethod
    def place_order(self, symbol, side, price, quantity, type="MARKET", sl=None, tp=None):
        """
        Places a new order.
        Returns the order ID if successful, None otherwise.
        """
        pass

    @abstractmethod
    def close_position(self, symbol, position_id):
        """
        Closes a specific position.
        Returns True if successful, False otherwise.
        """
        pass

    @abstractmethod
    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """
        Retrieves the order book (depth) for a symbol.
        Returns: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
        Bids sorted descending (best bid first), asks sorted ascending (best ask first).
        """
        pass

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict:
        """
        Retrieves current ticker data including best bid/ask.
        Returns: {"bid": float, "ask": float, "last": float, "volume": float}
        """
        pass

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> bool:
        """
        Cancels all open orders for a symbol.
        Returns True if successful, False otherwise.
        """
        pass

    def get_commission_rate(self, symbol: str) -> dict:
        """
        Retrieves maker/taker commission rates for a symbol.
        Returns: {"maker": float, "taker": float} in percent (e.g. 0.02 = 0.02%)
        or None if not supported / error.
        """
        return None

    def get_funding_rate(self, symbol: str) -> dict:
        """
        Retrieves current funding rate for a perpetual contract.
        Returns: {"funding_rate": float, "funding_rate_pct": float, "next_funding_time": str}
        or None if not supported / error.
        """
        return None
