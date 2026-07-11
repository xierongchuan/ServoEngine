"""Фабрика клиентов по паре (биржа, торговый продукт)."""

import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

_client_instances: Dict[Tuple[str, str, bool], object] = {}
# Старое имя сохранено для интеграций и тестов, которые сбрасывают singleton напрямую.
_client_instance = None


def _selection(exchange=None, market_type=None):
    from src import config as runtime_config
    selected_exchange = str(exchange or runtime_config.EXCHANGE).lower()
    default_market = "perpetual" if selected_exchange in {"bingx", "mexc"} else ""
    selected_market = str(market_type or getattr(runtime_config, "MARKET_TYPE", default_market)).lower()
    if selected_exchange == "bingx":
        selected_market = "perpetual"
    return selected_exchange, selected_market, runtime_config.MODE == "demo"


def _create_client(exchange: str, market_type: str, is_demo: bool, use_new_impl: bool = True):
    if exchange == "bingx":
        if not use_new_impl:
            from .bingx_client import BingXClient
            return BingXClient()
        from .config.base import ConfigFactory
        from .impl.bingx_client import BingXClient
        return BingXClient(ConfigFactory.create("bingx", is_demo=is_demo))

    if exchange == "mexc":
        from .config.base import ConfigFactory
        config = ConfigFactory.create("mexc", is_demo=is_demo, market_type=market_type)
        if market_type == "spot":
            from .impl.mexc_spot_client import MEXCSpotClient
            return MEXCSpotClient(config)
        if market_type == "perpetual":
            from .impl.mexc_futures_client import MEXCFuturesClient
            return MEXCFuturesClient(config)
        raise ValueError(f"MEXC market type not supported: {market_type}")

    raise ValueError(f"Unknown exchange: {exchange}")


def get_trading_client(exchange=None, market_type=None, use_new_impl: bool = True):
    selected_exchange, selected_market, is_demo = _selection(exchange, market_type)
    key = (selected_exchange, selected_market, bool(use_new_impl))
    if key not in _client_instances:
        _client_instances[key] = _create_client(
            selected_exchange, selected_market, is_demo, use_new_impl=use_new_impl
        )
    return _client_instances[key]


def get_market_data_client(exchange=None, market_type=None):
    """Публичный клиент; MEXC разрешён и при MODE=demo, без торговых мутаций."""
    return get_trading_client(exchange=exchange, market_type=market_type, use_new_impl=True)


def get_exchange_client(use_new_impl: bool = True):
    """Обратно совместимый alias торгового клиента."""
    global _client_instance
    if _client_instance is None:
        _client_instance = get_trading_client(use_new_impl=use_new_impl)
    return _client_instance


def reset_client():
    global _client_instance
    _client_instances.clear()
    _client_instance = None


__all__ = [
    "get_exchange_client", "get_market_data_client", "get_trading_client", "reset_client",
]
