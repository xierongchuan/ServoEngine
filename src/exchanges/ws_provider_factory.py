"""Owner lifecycle нескольких публичных потоков; workers используют только snapshots."""

from __future__ import annotations

import copy
import logging
import os
from multiprocessing import Manager

from .market_data import StreamKey, StreamStore, candle_gaps, closed_candles, evaluate_snapshot, select_stream, market_data_settings
from .public_market_rest import PublicRESTFetcher, BINGX_INTERVALS, BINGX_WS_INTERVALS, MEXC_INTERVALS, MEXC_SPOT_INTERVALS
from .ws.mexc_spot_proto import MEXC_SPOT_WS_INTERVALS

logger = logging.getLogger(__name__)
_shared_cache = None
_shared_ready = None
_manager = None
_store = None
_providers = {}
_owner_pid = None
_options = {}
_reader_only = False


def trading_selection():
    try:
        from src.trading_settings import get_trading_settings
        settings = get_trading_settings()
        return settings["exchange"], settings["market_type"], settings.get("market_data", {})
    except ImportError:
        # Не импортируем src.config: market-data не читает .env и не требует private credentials.
        return os.getenv("EXCHANGE", "bingx").lower(), os.getenv("MARKET_TYPE", "perpetual").lower(), {}


def provider_capabilities(exchange, market_type, interval=None):
    exchange, market_type = str(exchange).lower(), str(market_type).lower()
    pair = (exchange, market_type)
    ws_intervals, rest_intervals = (), ()
    if pair == ("bingx", "perpetual"):
        ws_intervals, rest_intervals = BINGX_WS_INTERVALS, BINGX_INTERVALS
    elif pair == ("mexc", "perpetual"):
        ws_intervals = rest_intervals = MEXC_INTERVALS
    elif pair == ("mexc", "spot"):
        ws_intervals, rest_intervals = MEXC_SPOT_WS_INTERVALS, set(MEXC_SPOT_INTERVALS) | {"8h"}
    if interval is not None:
        from .market_data import canonical_interval
        interval = canonical_interval(interval)
    ws_supported = bool(ws_intervals) if interval is None else interval in ws_intervals
    rest_supported = bool(rest_intervals) if interval is None else interval in rest_intervals
    result = {"transport": "ws" if ws_supported else "rest", "ws_supported": ws_supported,
              "rest_supported": rest_supported, "ws_intervals": sorted(ws_intervals),
              "rest_intervals": sorted(rest_intervals)}
    if not ws_supported:
        result["reason"] = ("Этот TF использует точный публичный REST polling" if rest_supported
                            else "Нет проверенного публичного адаптера для этого source/TF")
    return result


def _assert_owner():
    if _reader_only or (_owner_pid is not None and _owner_pid != os.getpid()):
        raise RuntimeError("Менять подписки может только главный процесс market-data")


def start_ws_provider(symbols=None, interval="5m", *, streams=None, exchange=None, market_type=None,
                      settings=None, cache=None, ready=None, rate_limiters=None, rest_fetcher=None,
                      publisher=None, ws_factory=None):
    global _manager, _store, _shared_cache, _shared_ready, _owner_pid, _options
    _assert_owner()
    selected_exchange, selected_market, configured = trading_selection()
    exchange, market_type = exchange or selected_exchange, market_type or selected_market
    keys = [StreamKey.from_value(key) for key in streams] if streams is not None else [StreamKey(exchange, market_type, symbol, interval) for symbol in symbols or []]
    _validate_streams(keys, rest_fetcher if _store is None else _options.get("rest_fetcher"))
    market_data_settings({**configured, **(settings or {})})
    if _store is None:
        if (cache is None) != (ready is None):
            raise ValueError("Передайте оба shared proxy: cache и ready")
        if cache is None:
            _manager = Manager()
            cache, ready = _manager.dict(), _manager.dict()
        _shared_cache, _shared_ready = cache, ready
        _store = StreamStore(cache, ready, settings={**configured, **(settings or {})})
        _owner_pid = os.getpid()
        _options = {"store": _store, "rest_fetcher": rest_fetcher,
                    "rate_limiters": rate_limiters, "publisher": publisher, "ws_factory": ws_factory}
    elif settings is not None or rate_limiters is not None:
        configure_market_data(settings=settings, rate_limiters=rate_limiters)
    sync_subscriptions(keys)
    return _shared_cache, _shared_ready


def configure_market_data(*, settings=None, rate_limiters=None):
    """Owner hot-reconfigure без замены Manager proxies и потери подписок."""
    _assert_owner()
    if _store is None:
        raise RuntimeError("Сначала вызовите start_ws_provider")
    _store.reconfigure(settings)
    if rate_limiters is not None:
        _options["rate_limiters"] = rate_limiters
    for provider in _providers.values():
        provider.refresh_settings(rate_limiters=rate_limiters)


def _validate_streams(keys, rest_fetcher):
    for key in keys:
        if not rest_fetcher and not PublicRESTFetcher.supports(key):
            raise ValueError(f"Нет проверенного public adapter для {key.id}")
        capability = provider_capabilities(key.exchange, key.market_type, key.interval)
        if not capability["ws_supported"] and not capability["rest_supported"] and not rest_fetcher:
            raise ValueError(f"Нет поддерживаемого транспорта для {key.id}")


def _provider_identity(key):
    if not provider_capabilities(key.exchange, key.market_type, key.interval)["ws_supported"]:
        return (key.exchange, key.market_type, "rest", key.interval)
    # MEXC Futures unsub.kline снимает весь symbol: TF изолируем отдельными WS-соединениями.
    return (key.exchange, key.market_type, key.interval) if (key.exchange, key.market_type) == ("mexc", "perpetual") else (key.exchange, key.market_type)


def _group_streams(keys):
    grouped = {}
    spot = {key for key in keys if (key.exchange, key.market_type) == ("mexc", "spot")
            and provider_capabilities(key.exchange, key.market_type, key.interval)["ws_supported"]}
    for key in keys - spot:
        grouped.setdefault(_provider_identity(key), []).append(key)
    # Сохраняем существующие shards: добавление symbol/TF не переподписывает остальные каналы.
    for identity, provider in _providers.items():
        if identity[:2] == ("mexc", "spot") and len(identity) == 3 and isinstance(identity[2], int):
            keep = spot.intersection(provider.streams)
            if keep:
                grouped[identity] = sorted(keep)
                spot.difference_update(keep)
    for key in sorted(spot):
        shard = 0
        while len(grouped.get(("mexc", "spot", shard), [])) >= 30:
            shard += 1
        grouped.setdefault(("mexc", "spot", shard), []).append(key)
    return grouped


def sync_subscriptions(streams):
    _assert_owner()
    if _store is None:
        raise RuntimeError("Сначала вызовите start_ws_provider")
    keys = {StreamKey.from_value(key) for key in streams}
    # Валидация всего набора до изменения уже работающих подписок.
    _validate_streams(keys, _options.get("rest_fetcher"))
    grouped = _group_streams(keys)
    for identity in list(_providers):
        if identity not in grouped:
            provider = _providers.pop(identity)
            provider.stop()
            for key in provider.streams:
                _store.remove(key)
    for identity, desired in grouped.items():
        if identity not in _providers:
            if len(identity) > 2 and identity[2] == "rest":
                from .ws.runtime import RESTPollingProvider
                provider = RESTPollingProvider(*identity[:2], **_options)
                logger.warning("⚠️ %s: используется точный public REST polling", identity)
            elif identity[:2] == ("bingx", "perpetual"):
                from .bingx_ws_data_provider import BingXWebSocketDataProvider
                provider = BingXWebSocketDataProvider(**_options)
            elif identity[:2] == ("mexc", "perpetual"):
                from .mexc_futures_ws_data_provider import MEXCFuturesWebSocketDataProvider
                provider = MEXCFuturesWebSocketDataProvider(**_options)
            elif identity[:2] == ("mexc", "spot"):
                from .mexc_spot_ws_data_provider import MEXCSpotWebSocketDataProvider
                provider = MEXCSpotWebSocketDataProvider(**_options)
            else:
                from .ws.runtime import RESTPollingProvider
                provider = RESTPollingProvider(*identity, **_options)
                logger.warning("⚠️ %s/%s: публичный REST fallback, WS не поддерживается", *identity)
            _providers[identity] = provider
        _providers[identity].start(streams=desired)


def stop_ws_provider(*, exchange=None, market_type=None):
    global _store, _manager, _owner_pid, _shared_cache, _shared_ready, _options
    _assert_owner()
    for identity in list(_providers):
        if exchange and identity[0] != exchange:
            continue
        if market_type and identity[1] != market_type:
            continue
        _providers.pop(identity).stop()
    if not _providers:
        if _manager is not None:
            _manager.shutdown()
        _manager = _store = _owner_pid = _shared_cache = _shared_ready = None
        _options = {}


def set_shared_cache(cache, ready):
    global _shared_cache, _shared_ready, _reader_only
    _shared_cache, _shared_ready = cache, ready
    _reader_only = cache is not None and _owner_pid != os.getpid()


def get_shared_cache():
    return _shared_cache, _shared_ready


def get_ws_provider(exchange=None, market_type=None, interval=None):
    exchange_default, market_default, _ = trading_selection()
    identity = (exchange or exchange_default, market_type or market_default)
    if _owner_pid != os.getpid():
        return None
    if interval is not None:
        from .market_data import canonical_interval
        interval = canonical_interval(interval)
        if identity == ("mexc", "perpetual"):
            return _providers.get((*identity, interval))
    matches = [provider for key, provider in _providers.items() if key[:2] == identity
               and (interval is None or any(stream.interval == interval for stream in provider.streams))]
    return matches[0] if len(matches) == 1 else None


def request_reconcile(key, **kwargs):
    _assert_owner()
    key = StreamKey.from_value(key)
    for provider in _providers.values():
        if key in provider.streams:
            return provider.request_reconcile(key, **kwargs)
    return False


def get_market_snapshot(key, *, allow_stale=False, max_age_seconds=None):
    if _shared_cache is None:
        return None
    key = StreamKey.from_value(key)
    try:
        value = _shared_cache.get(key.id)
        if not value or StreamKey.from_value(value["stream"]) != key:
            return None
        value = evaluate_snapshot(value, max_age_seconds=max_age_seconds)
        return value if allow_stale or value["ready"] else None
    except (EOFError, BrokenPipeError, OSError):
        return None


def _selected_snapshot(symbol, interval, exchange, market_type, max_age_seconds):
    if _shared_cache is None:
        return None
    if exchange is None or market_type is None:
        default_exchange, default_market, _ = trading_selection()
        exchange, market_type = exchange or default_exchange, market_type or default_market
    try:
        value = select_stream(_shared_cache, symbol, interval, exchange=exchange, market_type=market_type)
        return evaluate_snapshot(value, max_age_seconds=max_age_seconds) if value else None
    except (EOFError, BrokenPipeError, OSError, ValueError):
        return None


def get_klines_from_shared_cache(symbol, limit=288, interval=None, *, exchange=None, market_type=None,
                                 closed_only=False, max_age_seconds=None):
    if limit <= 0:
        return []
    value = _selected_snapshot(symbol, interval, exchange, market_type, max_age_seconds)
    if not value or not value["ready"]:
        return []
    rows = closed_candles(value["candles"], value["stream"]["interval"]) if closed_only else value["candles"]
    # Нехватка запрошенной истории — cache miss, не тихое усечение требований стратегии.
    return copy.deepcopy(rows[-limit:]) if len(rows) >= limit and not candle_gaps(rows[-limit:], value["stream"]["interval"]) else []


def is_cache_ready(symbol, interval=None, *, exchange=None, market_type=None, max_age_seconds=None):
    value = _selected_snapshot(symbol, interval, exchange, market_type, max_age_seconds)
    return bool(value and value["ready"])
