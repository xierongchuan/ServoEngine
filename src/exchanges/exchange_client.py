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
