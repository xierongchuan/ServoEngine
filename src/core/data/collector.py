"""Сбор данных: идентифицированный live snapshot и отдельная closed-only strategy view."""

import math
import os
import time
from typing import Any, Dict, List, Optional

from src.config import (
    SYMBOLS, DATA_DIR, ENABLE_NEWS, CHART_RANGES, DEFAULT_CHART_RANGE,
    STYLE_PRESETS, STRATEGY_STYLE, ENABLE_PARALLEL_COLLECTION,
)
from src.exchanges.market_data import (
    StreamKey, StreamStore, candle_gaps, canonical_interval, closed_candles, interval_start,
    market_data_settings, next_start, timestamp_ms,
)
from src.exchanges.public_market_rest import PublicRESTFetcher
from src.exchanges.ws_provider_factory import get_market_snapshot, trading_selection
from src.exchanges.market_data_storage import AtomicJsonStore, publish_market_snapshot, publish_strategy_view
from src.utils.logger import info, error, warning
from src.utils.helpers import get_filename
from src.utils.news_api import get_news_for_symbol


def _runtime_config(config=None):
    if config is not None:
        return config
    import src.config as config_module
    return config_module.BOT_CONFIG


def _market_options(config):
    _, _, defaults = trading_selection()
    return market_data_settings({**defaults, **config.get("MARKET_DATA", config.get("market_data", {}))})


class PublicMarketDataClient:
    """Collector не создаёт private client и не вызывает рекурсивный WS fallback."""
    def get_kline_data(self, symbol, interval="5m", limit=288):
        exchange, market, settings = trading_selection()
        return PublicRESTFetcher(settings=settings)(StreamKey(exchange, market, symbol, interval), limit=limit)


def get_exchange_client():
    # Сохранён seam для offline adapters; default использует исключительно public REST.
    return PublicMarketDataClient()


def ensure_dirs():
    os.makedirs(os.path.join(DATA_DIR, "prices"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "news"), exist_ok=True)


def _timestamp_seconds(value: Any) -> Optional[float]:
    try:
        return timestamp_ms(value) / 1000
    except (ValueError, TypeError, OverflowError):
        return None


def keep_closed_candles(prices: List[Dict], timeframe: str, now=None) -> List[Dict]:
    """Фильтрует все незакрытые периоды, а не только последнюю запись."""
    return closed_candles(prices, canonical_interval(timeframe), now=now)


def _request_parameters(config):
    style = config.get("STRATEGY_STYLE", STRATEGY_STYLE)
    presets = config.get("STYLE_PRESETS", STYLE_PRESETS)
    preset = presets.get(style) or presets.get("AISCALP") or {"timeframe": "1m"}
    interval = canonical_interval(preset.get("timeframe", "1m"))
    ranges = config.get("CHART_RANGES", CHART_RANGES)
    selected_range = config.get("DEFAULT_CHART_RANGE", DEFAULT_CHART_RANGE)
    chart = ranges.get(selected_range, {})
    minutes = chart.get("days", 0) * 1440 + chart.get("hours", 0) * 60 + chart.get("minutes", 0)
    options = _market_options(config)
    if preset.get("history_candles") is not None:
        limit = max(1, int(preset["history_candles"]))
    elif minutes:
        end = interval_start(int(time.time() * 1000), interval)
        if interval == "1M":
            start, limit = end, 0
            while end - start < minutes * 60_000:
                start = next_start(start, interval, -1)
                limit += 1
        else:
            limit = max(1, math.ceil(minutes * 60_000 / (next_start(end, interval) - end)))
    else:
        limit = max(1, int(chart.get("candles", options["history_limit"])))
    return interval, limit, options


def fetch_price_snapshot(symbol, config=None, *, interval=None, limit=None):
    config = _runtime_config(config)
    configured_interval, configured_limit, options = _request_parameters(config)
    interval, limit = interval or configured_interval, limit or configured_limit
    exchange, market, _ = trading_selection()
    key = StreamKey(config.get("EXCHANGE", exchange), config.get("MARKET_TYPE", market), symbol, interval)
    required = int(limit)
    cached = get_market_snapshot(key, max_age_seconds=options["max_age_seconds"])
    if cached is not None and len(cached["candles"]) >= required:
        return cached
    client = get_exchange_client()
    requested_at = time.time()
    # Общий limiter может быть передан main/config integration без private клиента.
    if isinstance(client, PublicMarketDataClient):
        rows = PublicRESTFetcher(settings=options, rate_limiters=config.get("PUBLIC_RATE_LIMITERS"))(
            key, limit=required,
        )
    else:
        rows = client.get_kline_data(symbol, interval=key.interval, limit=required)
    if not rows:
        raise ValueError(f"Пустая публичная история {key.id}")
    local_options = {**options, "history_limit": max(required, options["history_limit"]),
                     "min_history": min(required, options["min_history"])}
    store = StreamStore(settings=local_options)
    value = store.merge(key, rows, source="rest", received_at=requested_at, status="rest")
    value = store.mark(key, transport="rest")
    if not value["ready"] or len(value["candles"]) < required:
        raise ValueError(f"Непригодная рыночная история {key.id}: stale={value['stale']}, gaps={value['gaps']}, error={value['error']}")
    return value


def fetch_prices(symbol: str, config=None) -> List[Dict]:
    config = _runtime_config(config)
    interval, limit, options = _request_parameters(config)
    closed_only = options.get("strategy_closed_only", True)
    snapshot = fetch_price_snapshot(symbol, config, interval=interval, limit=limit + int(closed_only))
    rows = keep_closed_candles(snapshot["candles"], interval) if closed_only else snapshot["candles"]
    if len(rows) < limit or candle_gaps(rows[-limit:], interval):
        error(f"❌ Недостаточно непрерывных пригодных свечей для стратегии {symbol}/{interval}: {len(rows)}/{limit}")
        raise ValueError("Недостаточно пригодных свечей стратегии")
    return rows[-limit:]


def fetch_news(symbol):
    news = get_news_for_symbol(symbol)
    info(f"✅ Получено {len(news)} новостей для {symbol}")
    return news


def fetch_htf_prices(symbol, config=None):
    config = _runtime_config(config)
    mtf = config.get("AISCALP_SETTINGS", {}).get("multi_timeframe", {})
    if not mtf.get("enabled", True):
        return None
    interval = canonical_interval(mtf.get("htf_timeframe", "1h"))
    limit = max(1, int(mtf.get("htf_candles", 48)))
    try:
        snapshot = fetch_price_snapshot(symbol, config, interval=interval, limit=limit + 1)
        rows = keep_closed_candles(snapshot["candles"], interval)[-limit:]
        return rows if len(rows) >= limit and not candle_gaps(rows, interval) else None
    except Exception as exc:
        error(f"❌ Ошибка публичных HTF данных {symbol}/{interval}: {exc}")
        return None


def process_symbol(symbol, config=None):
    try:
        config = _runtime_config(config)
        interval, limit, options = _request_parameters(config)
        closed_only = options.get("strategy_closed_only", True)
        snapshot = fetch_price_snapshot(symbol, config, interval=interval, limit=limit + int(closed_only))
        data_dir = config.get("DATA_DIR", os.getenv("SERVO_DATA_DIR", DATA_DIR))
        publish_market_snapshot(data_dir, snapshot)
        strategy = dict(snapshot)
        strategy["min_history"] = limit
        strategy["candles"] = (keep_closed_candles(snapshot["candles"], interval) if closed_only else snapshot["candles"])[-limit:]
        if len(strategy["candles"]) < limit or candle_gaps(strategy["candles"], interval):
            raise ValueError("Недостаточно непрерывных закрытых свечей для стратегии")
        instance_id = config.get("STRATEGY_INSTANCE_ID")
        publish_strategy_view(data_dir, strategy, instance_id=instance_id, symbol=symbol,
                              closed_only=closed_only, legacy=options.get("publish_legacy_prices", not bool(instance_id)))
        news = fetch_news(symbol) if config.get("ENABLE_NEWS", ENABLE_NEWS) else []
        filename = get_filename(symbol)
        AtomicJsonStore(os.path.join(data_dir, "news", f"{filename}.json")).write(news)
        info(f"✅ Опубликованы live данные {snapshot['stream']} и стратегия {instance_id or 'legacy'}")
        return True
    except Exception as exc:
        error(f"❌ Ошибка сбора данных {symbol}: {exc}")
        return False


def main():
    ensure_dirs()
    if ENABLE_PARALLEL_COLLECTION and SYMBOLS:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(SYMBOLS), 10)) as executor:
            list(executor.map(process_symbol, SYMBOLS))
    else:
        for symbol in SYMBOLS:
            process_symbol(symbol)


if __name__ == "__main__":
    main()
