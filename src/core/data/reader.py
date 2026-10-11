"""Общий source/instance-aware reader для анализа, стратегий и live guards."""

from __future__ import annotations

import copy
import math
import os
import time

from src.exchanges.market_data import (
    StreamKey, candle_gaps, closed_candles, evaluate_snapshot, interval_start,
    market_data_settings, next_start,
)
from src.exchanges.market_data_storage import read_market_snapshot, read_strategy_snapshot
from src.exchanges.ws_provider_factory import get_shared_cache, set_shared_cache
from src.trading_settings import get_trading_settings
from src.utils.logger import warning


class MarketDataUnavailable(ValueError):
    """Недостаточные, устаревшие или чужие данные не допускаются к анализу."""


def runtime_data_config(config=None):
    if config is None:
        from src.config import BOT_CONFIG
        config = BOT_CONFIG
    result = dict(config)
    execution = {**get_trading_settings(), **result.get("execution", result.get("TRADING_SETTINGS", {}))}
    result.setdefault("EXCHANGE", execution["exchange"])
    result.setdefault("MARKET_TYPE", execution["market_type"])
    result.setdefault("EXECUTION_MODE", execution["execution_mode"])
    instance = result.get("STRATEGY_INSTANCE", {})
    if instance.get("id") and not result.get("STRATEGY_INSTANCE_ID"):
        result["STRATEGY_INSTANCE_ID"] = instance["id"]
    result["MARKET_DATA"] = market_data_settings({
        **execution.get("market_data", {}),
        **result.get("market_data", {}), **result.get("MARKET_DATA", {}),
    })
    return result


def trading_settings_for_config(config=None):
    config = runtime_data_config(config)
    settings = {**get_trading_settings(), **config.get("execution", config.get("TRADING_SETTINGS", {}))}
    settings.update(exchange=config["EXCHANGE"], market_type=config["MARKET_TYPE"],
                    execution_mode=config["EXECUTION_MODE"])
    # Внутренние heartbeat/retry defaults не являются полями execution-конфига.
    from src.trading_settings import DEFAULT_TRADING_SETTINGS
    settings["market_data"] = {key: int(config["MARKET_DATA"][key])
                               for key in DEFAULT_TRADING_SETTINGS["market_data"]}
    return settings


def is_read_only(config=None):
    # Неизвестный режим не может разрешить создание торгового клиента.
    return runtime_data_config(config)["EXECUTION_MODE"] not in {"live", "demo"}


def bind_shared_market_cache(cache=None, ready=None):
    if (cache is None) != (ready is None):
        raise ValueError("Нужно передать оба market-data proxy: cache и ready")
    if cache is not None:
        set_shared_cache(cache, ready)


def stream_key_for_config(symbol, config=None, *, interval=None):
    config = runtime_data_config(config)
    if interval is None:
        from src.core.data.collector import _request_parameters
        interval = _request_parameters(config)[0]
    return StreamKey(config["EXCHANGE"], config["MARKET_TYPE"], symbol, interval)


def build_streams_for_configs(configs, *, include_htf=True):
    """Полный набор подписок; main объединяет configs всех активных instances."""
    result = set()
    for raw_config in configs:
        config = runtime_data_config(raw_config)
        symbols = config.get("SYMBOLS")
        if symbols is None:
            instance_symbol = config.get("STRATEGY_INSTANCE", {}).get("symbol")
            symbols = [instance_symbol] if instance_symbol else []
        for symbol in symbols:
            result.add(stream_key_for_config(symbol, config))
            mtf = config.get("AISCALP_SETTINGS", {}).get("multi_timeframe", {})
            if include_htf and config.get("STRATEGY_STYLE") in {"AISCALP", "SWING"} and mtf.get("enabled", True):
                result.add(stream_key_for_config(symbol, config, interval=mtf.get("htf_timeframe", "1h")))
    return sorted(result)


def _unavailable(key, reason):
    warning(f"⚠️ Рыночные данные {key.id} отклонены: {reason}")
    raise MarketDataUnavailable(f"{key.id}: {reason}")


def _live_snapshot(key, config, cache=None):
    age = config["MARKET_DATA"]["max_age_seconds"]
    if cache is None:
        cache = get_shared_cache()[0]
    if cache is not None:
        raw = cache.get(key.id)
        if raw is not None:
            if StreamKey.from_value(raw["stream"]) != key:
                _unavailable(key, "Источник proxy не совпадает с ключом")
            value = evaluate_snapshot(raw, max_age_seconds=age)
            return value if value["ready"] else None
        return None
    from src.config import DATA_DIR
    return read_market_snapshot(config.get("DATA_DIR", os.getenv("SERVO_DATA_DIR", DATA_DIR)), key,
                                max_age_seconds=age)


def read_live_data(symbol, config=None, *, interval=None, cache=None):
    config = runtime_data_config(config)
    key = stream_key_for_config(symbol, config, interval=interval)
    try:
        value = _live_snapshot(key, config, cache)
    except (KeyError, TypeError, ValueError, OSError, EOFError) as exc:
        _unavailable(key, str(exc))
    if value is None:
        _unavailable(key, "Нет свежего ready snapshot")
    return value


def read_strategy_data(symbol, config=None, *, interval=None, limit=None):
    config = runtime_data_config(config)
    key = stream_key_for_config(symbol, config, interval=interval)
    primary = stream_key_for_config(symbol, config)
    instance_id = config.get("STRATEGY_INSTANCE_ID")
    if limit is None and key == primary:
        from src.core.data.collector import _request_parameters
        limit = _request_parameters(config)[1]
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        _unavailable(key, "Некорректная длина истории стратегии")
    try:
        if instance_id and key == primary:
            from src.config import DATA_DIR
            value = read_strategy_snapshot(
                config.get("DATA_DIR", os.getenv("SERVO_DATA_DIR", DATA_DIR)), instance_id, symbol,
                stream_key=key, limit=limit, max_age_seconds=config["MARKET_DATA"]["max_age_seconds"],
            )
        else:
            # HTF имеет собственный StreamKey: primary-файл instance никогда не подменяет его.
            value = _live_snapshot(key, config)
            if value is not None:
                rows = closed_candles(value["candles"], key.interval)
                if limit is not None:
                    rows = rows[-limit:]
                value = {**value, "candles": rows, "view": "closed", "instance_id": instance_id,
                         "min_history": limit if limit is not None else value.get("min_history", 1),
                         "gaps": candle_gaps(rows, key.interval)}
                value = evaluate_snapshot(value, max_age_seconds=config["MARKET_DATA"]["max_age_seconds"])
                if not value["ready"]:
                    value = None
    except (KeyError, TypeError, ValueError, OSError, EOFError) as exc:
        _unavailable(key, str(exc))
    if value is None:
        _unavailable(key, "Нет свежей непрерывной closed-only истории нужного instance/source/TF")
    return value


def market_data_context(snapshot):
    fields = ("stream", "instance_id", "view", "last_valid_update", "max_age_seconds", "version",
              "publisher_id", "status", "ready", "stale", "gaps", "error")
    result = {name: copy.deepcopy(snapshot.get(name)) for name in fields}
    result["last_candle_timestamp"] = snapshot["candles"][-1]["timestamp"]
    return result


def _valid_context(context, key, config):
    if not isinstance(context, dict):
        return False
    try:
        if StreamKey.from_value(context["stream"]) != key or context.get("instance_id") != config.get("STRATEGY_INSTANCE_ID"):
            return False
        last = float(context["last_valid_update"])
        age = min(config["MARKET_DATA"]["max_age_seconds"], float(context["max_age_seconds"]))
        timestamp = int(context["last_candle_timestamp"])
        now = time.time()
        shared = get_shared_cache()[0]
        if shared is not None:
            latest = shared.get(key.id)
            if latest is None or StreamKey.from_value(latest["stream"]) != key or not evaluate_snapshot(latest, max_age_seconds=age)["ready"]:
                return False
        return bool(math.isfinite(last) and 0 <= now - last <= age and context.get("ready")
                    and not context.get("stale") and not context.get("gaps") and not context.get("error")
                    and context.get("status") in {"live", "rest"} and context.get("view") == "closed"
                    and next_start(timestamp, key.interval) >= interval_start(int(now * 1000), key.interval))
    except (KeyError, TypeError, ValueError, OverflowError, OSError, EOFError):
        return False


def validate_analysis_market_data(analysis, config=None):
    """Повторная проверка всех использованных TF после AI и перед исполнением."""
    config = runtime_data_config(config)
    context = analysis.get("market_data")
    # Только старый no-instance API допускает переданный вручную анализ без provenance.
    if context is None and not config.get("STRATEGY_INSTANCE_ID"):
        return True
    try:
        key = stream_key_for_config(analysis["symbol"], config)
        if not _valid_context(context, key, config):
            return False
        htf = analysis.get("htf_data")
        if htf is not None:
            interval = config.get("AISCALP_SETTINGS", {}).get("multi_timeframe", {}).get("htf_timeframe", "1h")
            return _valid_context(htf.get("market_data"), stream_key_for_config(analysis["symbol"], config, interval=interval), config)
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
