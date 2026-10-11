"""Runtime source/TF, closed-only история и изолированная публикация collector."""

import json
from functools import partial

import pytest

from src.core.data import collector
from src.core.data.reader import build_streams_for_configs
from src.exchanges.market_data import StreamKey, StreamStore
from src.exchanges.market_data_storage import instance_prices_path, read_market_snapshot, read_strategy_snapshot
from market_data_fixtures import FixedClock, candle_rows


class _FakeClient:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def get_kline_data(self, symbol, interval="5m", limit=288):
        self.calls.append({"symbol": symbol, "interval": interval, "limit": limit})
        return candle_rows(StreamKey("mexc", "spot", symbol, interval), self.clock, limit)


@pytest.fixture
def public_client(monkeypatch):
    clock = FixedClock()
    monkeypatch.setattr("src.exchanges.market_data.time.time", clock)
    monkeypatch.setattr(collector, "StreamStore", partial(StreamStore, clock=clock))
    monkeypatch.setattr(collector, "get_market_snapshot", lambda *args, **kwargs: None)
    client = _FakeClient(clock)
    monkeypatch.setattr(collector, "get_exchange_client", lambda: client)
    return clock, client


def _config():
    return {
        "EXCHANGE": "mexc", "MARKET_TYPE": "spot", "EXECUTION_MODE": "read_only",
        "STRATEGY_INSTANCE_ID": "runtime-hybrid", "SYMBOLS": ["BNBUSDT"],
        "STRATEGY_STYLE": "HYBRID", "DEFAULT_CHART_RANGE": "2H",
        "CHART_RANGES": {"2H": {"hours": 2}},
        "STYLE_PRESETS": {"HYBRID": {"timeframe": "5m"}},
        "MARKET_DATA": {"min_history": 1, "max_age_seconds": 30},
    }


def test_fetch_prices_uses_runtime_instance_config(public_client):
    clock, client = public_client
    config = _config()
    assert build_streams_for_configs([config]) == [StreamKey("mexc", "spot", "BNBUSDT", "5m")]
    prices = collector.fetch_prices("BNBUSDT", config)
    assert len(prices) == 24
    # Дополнительная свеча нужна для live view; стратегия получает ровно 24 закрытые.
    assert client.calls == [{"symbol": "BNBUSDT", "interval": "5m", "limit": 25}]
    assert all(row["is_closed"] and row["source"] == "rest" for row in prices)
    assert prices[-1]["timestamp"] < int(clock() * 1000)


def test_fetch_prices_prefers_explicit_history_candles(public_client):
    _, client = public_client
    prices = collector.fetch_prices("BTCUSDT", {
        **_config(), "STRATEGY_STYLE": "MACDX", "DEFAULT_CHART_RANGE": "180D",
        "CHART_RANGES": {"180D": {"days": 180}},
        "STYLE_PRESETS": {"MACDX": {"timeframe": "1d", "history_candles": 180}},
    })
    assert len(prices) == 180
    assert client.calls == [{"symbol": "BTCUSDT", "interval": "1d", "limit": 181}]
    assert all(row["is_closed"] for row in prices)


def test_process_symbol_writes_to_runtime_data_dir(public_client, tmp_path):
    _, client = public_client
    config = {**_config(), "DATA_DIR": str(tmp_path), "ENABLE_NEWS": False}
    legacy = tmp_path / "prices" / "BNBUSDT.json"
    legacy.parent.mkdir()
    legacy.write_text('[{"sentinel": true}]')
    ok = collector.process_symbol("BNBUSDT", config)
    assert ok is True
    key = StreamKey("mexc", "spot", "BNBUSDT", "5m")
    live = read_market_snapshot(tmp_path, key)
    strategy = read_strategy_snapshot(tmp_path, config["STRATEGY_INSTANCE_ID"], "BNBUSDT", stream_key=key, limit=24)
    prices = json.loads(instance_prices_path(tmp_path, config["STRATEGY_INSTANCE_ID"], "BNBUSDT").read_text())
    news = json.loads((tmp_path / "news" / "BNBUSDT.json").read_text())
    assert prices[0]["closePrice"] == 1.5
    assert news == []
    assert strategy["candles"] == prices
    assert strategy["instance_id"] == config["STRATEGY_INSTANCE_ID"]
    assert strategy["stream"] == live["stream"] == key.to_dict()
    assert live["ready"] and strategy["ready"]
    assert len(live["candles"]) == 25 and not live["candles"][-1]["is_closed"]
    assert all(row["is_closed"] for row in prices)
    assert json.loads(legacy.read_text()) == [{"sentinel": True}]
    assert client.calls == [{"symbol": "BNBUSDT", "interval": "5m", "limit": 25}]
