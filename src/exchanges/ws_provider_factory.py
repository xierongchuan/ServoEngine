"""Выбор WebSocket-провайдера без hardcode конкретной биржи в runtime."""


def _module():
    from src.config import EXCHANGE, MARKET_TYPE
    if EXCHANGE.lower() == "bingx":
        from src.exchanges import bingx_ws_data_provider as provider
        return provider
    if EXCHANGE.lower() == "mexc" and MARKET_TYPE == "perpetual":
        from src.exchanges import mexc_futures_ws_data_provider as provider
        return provider
    return None


def start_ws_provider(symbols, interval="5m"):
    provider = _module()
    if provider is None:
        return None, None
    return provider.start_ws_provider(symbols, interval)


def stop_ws_provider():
    provider = _module()
    if provider is not None:
        provider.stop_ws_provider()


def set_shared_cache(cache, ready):
    provider = _module()
    if provider is not None:
        provider.set_shared_cache(cache, ready)


def get_klines_from_shared_cache(symbol, limit=288):
    provider = _module()
    return provider.get_klines_from_shared_cache(symbol, limit) if provider is not None else []


def is_cache_ready(symbol):
    provider = _module()
    return provider.is_cache_ready(symbol) if provider is not None else False
