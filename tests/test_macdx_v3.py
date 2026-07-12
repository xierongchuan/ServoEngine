"""Регрессионные тесты MACDX v3: закрытые свечи, симметричные фильтры и таймфреймы."""

import json
from pathlib import Path

import pytest

from src.core.data.collector import keep_closed_candles
from src.core.signals.macdx import MacdxSignalGenerator
from src.backtest.signals import SignalGenerator


def _settings(**rule_overrides):
    rules = {
        "macd_cross_weight": 4,
        "rsi_zone_weight": 1,
        "ema_alignment_weight": 2,
        "not_sideways_weight": 2,
        "no_exhaustion_weight": 1,
        "volume_weight": 1,
        "min_score_for_signal": 7,
        "min_confirmations": 3,
        "min_volume_ratio": 0.4,
        "min_atr_ratio": 0.5,
        "min_atr_percent": 0.15,
        "rsi_long_min": 28,
        "rsi_long_max": 68,
        "rsi_short_min": 32,
        "rsi_short_max": 72,
        "rsi_block_long_above": 78,
        "rsi_block_short_below": 22,
        "bb_width_threshold": 0.8,
        "adx_threshold": 20,
        "min_adx_for_entry": 18,
        "enable_volume_filter": False,
        "enable_strengthening_trend_entry": False,
        "sideways_block_signals": True,
        "trend_alignment_filter": False,
        "consecutive_red_filter": False,
        "enable_counter_trend_filter": False,
        "chop_enabled": True,
        "chop_threshold": 61.8,
        "chop_penalty": -3,
        "chop_block_signals": True,
    }
    rules.update(rule_overrides)
    return {"preset": {"timeframe": "15m"}, "signal_rules": rules, "exit_rules": {}}


def _analysis(**overrides):
    data = {
        "current_price": 100.0,
        "rsi": 50.0,
        "volume_ratio": 1.0,
        "ema9": 101.0,
        "ema21": 100.0,
        "macd_hist": 0.2,
        "macd_hist_prev": -0.1,
        "macd_hist_2prev": -0.2,
        "bb_upper": 103.0,
        "bb_middle": 100.0,
        "bb_lower": 97.0,
        "atr": 0.5,
        "atr_ratio": 1.0,
        "atr_percent": 0.5,
        "adx": 28.0,
        "chop": 45.0,
        "last_5_direction": "MIXED",
        "close_prices": [],
        "rsi_values": [],
        "candle_time": "2026-07-12T10:00:00",
    }
    data.update(overrides)
    return data


def test_entry_requires_absolute_volatility():
    result = MacdxSignalGenerator(_settings()).generate(
        _analysis(atr_ratio=1.2, atr_percent=0.1)
    )
    assert result["signal"] == "HOLD"
    assert result["details"]["filter"] == "volatility"


def test_zero_volatility_is_not_replaced_by_default():
    result = MacdxSignalGenerator(_settings()).generate(
        _analysis(atr_ratio=0.0, atr_percent=0.0, volume_ratio=0.0, adx=0.0)
    )
    assert result["details"]["filter"] == "volatility"


def test_regime_cannot_lower_base_entry_score():
    generator = MacdxSignalGenerator(_settings(min_score_for_signal=8))
    result = generator.generate(_analysis(), {"regime": "TRENDING", "recommended_min_score": 4})
    assert result["signal"] == "BUY"
    assert result["details"]["min_score_required"] == 8


@pytest.mark.parametrize(
    ("overrides", "blocked_side"),
    [
        ({"rsi": 80.0}, "BUY"),
        (
            {
                "rsi": 20.0,
                "ema9": 99.0,
                "ema21": 100.0,
                "macd_hist": -0.2,
                "macd_hist_prev": 0.1,
                "macd_hist_2prev": 0.2,
            },
            "SELL",
        ),
    ],
)
def test_rsi_extreme_protection_is_symmetric(overrides, blocked_side):
    result = MacdxSignalGenerator(_settings()).generate(_analysis(**overrides))
    assert result["signal"] != blocked_side


def test_same_crossover_candle_is_emitted_once():
    generator = MacdxSignalGenerator(_settings())
    assert generator.generate(_analysis())["signal"] == "BUY"
    duplicate = generator.generate(_analysis())
    assert duplicate["signal"] == "HOLD"
    assert duplicate["details"]["filter"] == "duplicate_candle"


def test_exit_context_counts_candles_not_polling_cycles():
    generator = MacdxSignalGenerator(_settings())
    context = {}
    for _ in range(3):
        generator._update_exit_context(context, 101, 100, "BUY", 1, 0.2, 0.5, "same")
    assert context["candles_in_trade"] == 1
    generator._update_exit_context(context, 102, 100, "BUY", 2, 0.15, 0.5, "next")
    assert context["candles_in_trade"] == 2


def test_unclosed_candle_is_removed():
    candles = [
        {"snapshotTimeUTC": "2026-07-12T09:45:00+00:00"},
        {"snapshotTimeUTC": "2026-07-12T10:00:00+00:00"},
    ]
    now = 1_783_850_700  # 2026-07-12 10:05:00 UTC
    assert keep_closed_candles(candles, "15m", now=now) == candles[:1]


def test_all_macdx_timeframe_profiles_have_enough_history():
    root = Path(__file__).parents[1]
    base = json.loads((root / "config/base.json").read_text())
    ranges = base["chart_ranges"]
    for name in ("5m", "15m", "30m", "1h", "1d"):
        profile = json.loads((root / f"config/profiles/macdx_{name}.json").read_text())
        preset = profile["preset"]
        chart = ranges[preset["chart_period"]]
        duration_minutes = chart.get("days", 0) * 1440 + chart.get("hours", 0) * 60 + chart.get("minutes", 0)
        unit = preset["timeframe"][-1]
        value = int(preset["timeframe"][:-1])
        timeframe_minutes = value * (1440 if unit == "d" else 60 if unit == "h" else 1)
        assert duration_minutes // timeframe_minutes >= 35
        assert preset["history_candles"] == duration_minutes // timeframe_minutes
        assert preset["plotter_period"] == preset["chart_period"]

    daily = json.loads((root / "config/profiles/macdx_1d.json").read_text())
    assert daily["preset"]["history_candles"] == 180


def test_backtest_uses_same_atr_units_as_live_analysis():
    klines = []
    for index in range(50):
        price = 100 + index * 0.2
        klines.append(
            {
                "openPrice": price - 0.1,
                "highPrice": price + 0.3,
                "lowPrice": price - 0.3,
                "closePrice": price,
                "volume": 100,
                "snapshotTimeUTC": str(index),
            }
        )
    result = SignalGenerator("MACDX", _settings()).calculate_indicators(klines, 49)
    assert result["atr_ratio"] > 0.5
    assert result["atr_percent"] == pytest.approx(result["atr"] / 109.8 * 100)
