"""Тесты глубины истории и warm-up индикаторов Telegram Panel."""

import asyncio
import json
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
