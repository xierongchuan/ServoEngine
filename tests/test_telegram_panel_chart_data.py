"""Тесты глубины истории и warm-up индикаторов Telegram Panel."""

import asyncio
import json
import math

import pytest
from fastapi import HTTPException
from datetime import datetime, timedelta, timezone

from src.telegram_panel.backend.routes import chart_data


def _write_daily_candles(path, count=180):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for index in range(count):
        price = 100 + index * 0.2
        candles.append({
            "snapshotTimeUTC": (start + timedelta(days=index)).isoformat(),
            "openPrice": price - 0.1,
            "highPrice": price + 0.5,
            "lowPrice": price - 0.5,
            "closePrice": price,
            "volume": 1000 + index,
        })
    path.write_text(json.dumps(candles), encoding="utf-8")


def test_daily_chart_auto_range_shows_six_months(tmp_path, monkeypatch):
    prices_dir = tmp_path / "prices"
    prices_dir.mkdir()
    _write_daily_candles(prices_dir / "BTCUSDT.json")
    (tmp_path / "active_trades.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)

    result = asyncio.run(chart_data.get_chart_data("BTCUSDT", "AUTO", {}))

    assert result["interval"] == "1d"
    assert result["range"] == "180D"
    assert len(result["candles"]) == 180
    assert len(result["indicators"]["macd_histogram"]) > 100


def test_short_daily_zoom_keeps_macd_warmup(tmp_path, monkeypatch):
    prices_dir = tmp_path / "prices"
    prices_dir.mkdir()
    _write_daily_candles(prices_dir / "BTCUSDT.json")
    (tmp_path / "active_trades.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)

    result = asyncio.run(chart_data.get_chart_data("BTCUSDT", "1D", {}))

    assert len(result["candles"]) == 2
    assert len(result["indicators"]["macd_histogram"]) == 2


def test_candles_deduplicate_sort_and_reject_nonfinite(tmp_path, monkeypatch):
    directory = tmp_path / "prices"
    directory.mkdir()
    path = directory / "BTC-USDT.json"
    _write_daily_candles(path, count=40)
    rows = json.loads(path.read_text())
    changed = {**rows[10], "volume": 9999}
    invalid = {**rows[11], "closePrice": float("nan")}
    rows = list(reversed(rows)) + [changed, invalid, None, {"snapshotTimeUTC": "invalid"},
                                               {**rows[0], "openPrice": "broken"}]
    path.write_text(json.dumps(rows))
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)
    result = asyncio.run(chart_data.get_chart_data("BTC-USDT", "AUTO", {}))
    assert len(result["candles"]) == 40
    times = [row["time"] for row in result["candles"]]
    assert times == sorted(set(times))
    assert result["candles"][10]["volume"] == 9999
    assert all(math.isfinite(value) for row in result["candles"] for value in row.values())
    assert result["source"]["kind"] == "legacy_file"
    assert result["source"]["interval_inferred"] is True
    assert result["source"]["snapshot_updated_at"] > 0


@pytest.mark.parametrize("symbol", ["..", "../secret", "x\\y", "a.b"])
def test_symbol_path_validation(tmp_path, monkeypatch, symbol):
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(chart_data.get_chart_data(symbol, "AUTO", {}))
    assert caught.value.status_code == 400


@pytest.mark.parametrize("time_range", ["0D", "-1D", "unknown"])
def test_range_validation(tmp_path, monkeypatch, time_range):
    directory = tmp_path / "prices"
    directory.mkdir()
    _write_daily_candles(directory / "BTCUSDT.json")
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(chart_data.get_chart_data("BTCUSDT", time_range, {}))
    assert caught.value.status_code == 400


def _write_market_snapshot(tmp_path, exchange="mexc", interval="1d"):
    path = tmp_path / "market_data" / exchange / "perpetual" / "BTCUSDT" / f"{interval}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = tmp_path / f"rows-{exchange}.json"
    _write_daily_candles(legacy, 40)
    rows = json.loads(legacy.read_text())
    for row in rows:
        row["timestamp"] = int(datetime.fromisoformat(row["snapshotTimeUTC"]).timestamp() * 1000)
    snapshot = {"schema_version": 1, "stream": {"exchange": exchange, "market_type": "perpetual", "symbol": "BTCUSDT", "interval": interval},
                "candles": rows, "status": "rest", "error": None, "gaps": [],
                "last_valid_update": 1, "version": 7, "min_history": 1}
    path.write_text(json.dumps(snapshot))
    return snapshot


def test_market_snapshot_explicit_interval_and_stale_metadata(tmp_path, monkeypatch):
    snapshot = _write_market_snapshot(tmp_path)
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)
    result = asyncio.run(chart_data.get_chart_data("BTC-USDT", "AUTO", {}, "mexc", "perpetual", "1d"))
    assert result["interval"] == "1d"
    assert result["source"]["kind"] == "market_snapshot"
    assert result["source"]["interval_inferred"] is False
    assert result["source"]["stream"] == snapshot["stream"]
    assert result["source"]["version"] == 7
    assert result["source"]["stale"] is True
    assert result["source"]["ready"] is False
    assert len(result["candles"]) == 40


def test_market_multiple_sources_requires_selection_without_legacy(tmp_path, monkeypatch):
    _write_market_snapshot(tmp_path, "mexc")
    _write_market_snapshot(tmp_path, "bingx")
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)
    sources = asyncio.run(chart_data.get_chart_sources("BTC-USDT", {}))
    assert [source["exchange"] for source in sources] == ["bingx", "mexc"]
    with pytest.raises(HTTPException) as caught:
        asyncio.run(chart_data.get_chart_data("BTC-USDT", "AUTO", {}))
    assert caught.value.status_code == 409
    selected = asyncio.run(chart_data.get_chart_data("BTC-USDT", "AUTO", {}, "bingx", "perpetual", "1d"))
    assert selected["source"]["stream"]["exchange"] == "bingx"
    assert len(selected["available_sources"]) == 2


def test_partial_source_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(chart_data.get_chart_data("BTC-USDT", "AUTO", {}, "mexc"))
    assert caught.value.status_code == 400


def test_lowercase_open_position(tmp_path, monkeypatch):
    directory = tmp_path / "prices"
    directory.mkdir()
    _write_daily_candles(directory / "BTCUSDT.json")
    (tmp_path / "active_trades.json").write_text(json.dumps({"BTCUSDT": {
        "status": "open", "side": "LONG", "entry_price": 100, "sl": 95, "tp": 120, "last_pnl": 2}}))
    monkeypatch.setattr(chart_data, "DATA_DIR", tmp_path)
    result = asyncio.run(chart_data.get_chart_data("BTCUSDT", "AUTO", {}))
    assert result["position"]["sl"] == 95
