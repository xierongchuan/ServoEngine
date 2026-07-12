import json

from src.core.data import collector


class _FakeClient:
    def __init__(self):
        self.calls = []

    def get_kline_data(self, symbol, interval="5m", limit=288):
        self.calls.append({"symbol": symbol, "interval": interval, "limit": limit})
        return [{
            "snapshotTimeUTC": "2026-06-18T10:00:00",
            "openPrice": 1.0,
            "highPrice": 2.0,
            "lowPrice": 0.5,
            "closePrice": 1.5,
            "volume": 100.0,
        }]


def test_fetch_prices_uses_runtime_instance_config(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(collector, "get_exchange_client", lambda: client)

    prices = collector.fetch_prices("BNBUSDT", {
        "STRATEGY_STYLE": "HYBRID",
        "DEFAULT_CHART_RANGE": "2H",
        "CHART_RANGES": {"2H": {"hours": 2}},
        "STYLE_PRESETS": {"HYBRID": {"timeframe": "5m"}},
    })

    assert len(prices) == 1
    assert client.calls == [{"symbol": "BNBUSDT", "interval": "5m", "limit": 24}]


def test_fetch_prices_prefers_explicit_history_candles(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(collector, "get_exchange_client", lambda: client)

    collector.fetch_prices("BTCUSDT", {
        "STRATEGY_STYLE": "MACDX",
        "DEFAULT_CHART_RANGE": "180D",
        "CHART_RANGES": {"180D": {"days": 180}},
        "STYLE_PRESETS": {"MACDX": {"timeframe": "1d", "history_candles": 180}},
    })

    assert client.calls == [{"symbol": "BTCUSDT", "interval": "1d", "limit": 180}]


def test_process_symbol_writes_to_runtime_data_dir(monkeypatch, tmp_path):
    client = _FakeClient()
    monkeypatch.setattr(collector, "get_exchange_client", lambda: client)

    ok = collector.process_symbol("BNBUSDT", {
        "DATA_DIR": str(tmp_path),
        "ENABLE_NEWS": False,
        "STRATEGY_STYLE": "HYBRID",
        "DEFAULT_CHART_RANGE": "2H",
        "CHART_RANGES": {"2H": {"hours": 2}},
        "STYLE_PRESETS": {"HYBRID": {"timeframe": "5m"}},
    })

    assert ok is True
    prices = json.loads((tmp_path / "prices" / "BNBUSDT.json").read_text())
    news = json.loads((tmp_path / "news" / "BNBUSDT.json").read_text())
    assert prices[0]["closePrice"] == 1.5
    assert news == []
