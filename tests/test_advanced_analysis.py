"""Расширенный анализ реального атомарного instance snapshot без private I/O."""

from src.core.analyzer import analyze_symbol
from src.exchanges.market_data_storage import AtomicJsonStore, read_strategy_snapshot
from market_data_fixtures import FixedClock, publish_instance, runtime_config


def test_advanced_analysis(monkeypatch, tmp_path):
    clock = FixedClock()
    monkeypatch.setattr("src.exchanges.market_data.time.time", clock)
    config = runtime_config(tmp_path, style="AISCALP", history=100, instance="advanced")
    config["ENABLE_ADVANCED_ANALYSIS"] = True
    key, live = publish_instance(config, "BTCUSDT", clock)
    AtomicJsonStore(tmp_path / "prices" / "BTCUSDT.json").write([{"closePrice": 999999}])
    result = analyze_symbol("BTCUSDT", config=config)
    prompt = result["prompt"]
    # Актуальный AISCALP prompt явно включает оценку структуры/психологии рынка.
    assert "РЫНОЧНАЯ ОЦЕНКА" in prompt
    assert "Свечной паттерн и психология" in prompt
    assert result["market_data"]["stream"] == key.to_dict()
    assert result["market_data"]["instance_id"] == "advanced"
    assert result["current_price"] == live["candles"][-2]["closePrice"]
    assert "999999" not in prompt
    snapshot = read_strategy_snapshot(tmp_path, "advanced", "BTCUSDT", stream_key=key, limit=100)
    assert len(snapshot["candles"]) == 100
    assert all(row["is_closed"] for row in snapshot["candles"])
