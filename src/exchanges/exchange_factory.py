from src.config import EXCHANGE
from .bingx_client import BingXClient
from src.utils.logger import info

_client_instance = None

def get_exchange_client():
    """
    Factory function to get the appropriate exchange client instance.
    Returns a singleton per process to avoid unnecessary object creation.
    """
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    exchange = EXCHANGE.lower()

    if exchange == "bingx":
        _client_instance = BingXClient()
    else:
        raise ValueError(f"Unknown exchange: {EXCHANGE}")

    return _client_instance
