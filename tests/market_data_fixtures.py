"""Детерминированные OHLCV и реальные source/instance snapshots для offline тестов."""

from datetime import datetime, timezone

from src.exchanges.market_data import StreamKey, StreamStore, interval_start, next_start
from src.exchanges.market_data_storage import publish_market_snapshot, publish_strategy_view


class FixedClock:
    def __init__(self):
        self.now = datetime(2026, 10, 11, 12, 37, 15, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now


def runtime_config(data_dir, *, style="HYBRID", interval="1m", history=100, instance="fixture"):
    return {
        "DATA_DIR": str(data_dir), "EXCHANGE": "bingx", "MARKET_TYPE": "perpetual",
        "EXECUTION_MODE": "read_only", "STRATEGY_INSTANCE_ID": instance,
        "STRATEGY_STYLE": style, "ENABLE_NEWS": False,
        "STYLE_PRESETS": {style: {"timeframe": interval, "history_candles": history}},
        "DEFAULT_CHART_RANGE": "1D", "CHART_RANGES": {"1D": {"days": 1, "ai_context_candles": history}},
        "SMART_SAMPLING": {"enabled": False},
        "MARKET_DATA": {"history_limit": history + 1, "min_history": 1, "max_age_seconds": 30},
        "AISCALP_SETTINGS": {"multi_timeframe": {"enabled": False}},
    }


def candle_rows(key, clock, count, *, close=1.5, varying=False):
    key = StreamKey.from_value(key)
    end = interval_start(int(clock() * 1000), key.interval)
    rows = []
    for index in range(count):
        timestamp = next_start(end, key.interval, index - count + 1)
        price = close + (index % 10) * close * 0.002 if varying else close
        rows.append({
            "timestamp": timestamp,
            "snapshotTimeUTC": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat(),
            "symbol": key.symbol, "interval": key.interval,
            "openPrice": price * 0.98, "highPrice": price * 1.05,
            "lowPrice": price * 0.95, "closePrice": price,
            "volume": 100 + (index % 5) * 50,
        })
    return rows


def make_snapshot(key, clock, count, *, close=1.5, varying=False, source="rest"):
    store = StreamStore(settings={"history_limit": count, "min_history": 1, "max_age_seconds": 30}, clock=clock)
    rows = candle_rows(key, clock, count, close=close, varying=varying)
    store.merge(key, rows, source="rest", status="rest")
    if source == "ws":
        store.merge(key, [{**rows[-1], "event_time_ms": int(clock() * 1000)}], source="ws", status="live")
    return store.mark(key, transport=source, connected=source == "ws", subscribed=source == "ws")


def publish_instance(config, symbol, clock, *, close=50000, varying=True):
    preset = config["STYLE_PRESETS"][config["STRATEGY_STYLE"]]
    key = StreamKey(config["EXCHANGE"], config["MARKET_TYPE"], symbol, preset["timeframe"])
    snapshot = make_snapshot(key, clock, preset["history_candles"] + 1, close=close, varying=varying)
    publish_market_snapshot(config["DATA_DIR"], snapshot)
    publish_strategy_view(config["DATA_DIR"], snapshot, instance_id=config["STRATEGY_INSTANCE_ID"],
                          symbol=symbol, closed_only=True, legacy=False)
    return key, snapshot
