"""Tests for src.core.intraday_signal — INTRADAY signal generator."""

import pytest
from unittest.mock import patch


INTRADAY_CONFIG = {
    "INTRADAY_SETTINGS": {
        "signal_scoring": {
            "weights": {
                "htf_trend": 3, "ema_alignment": 2, "rsi_zone": 2,
                "sr_proximity": 2, "macd": 1, "volume": 1,
                "session_quality": 1, "momentum": 1
            },
            "min_score_for_signal": 5,
            "tier1_required": True,
            "conflict_friction_threshold": 4,
            "min_volume_ratio": 0.4,
            "min_atr_ratio": 0.4,
            "rsi_long_zone": [30, 55],
            "rsi_short_zone": [45, 70],
            "sr_proximity_pct": 2.5,
        },
        "sessions": {
            "enabled": True,
            "definitions": {
                "ASIAN": {"start_utc": 0, "end_utc": 8},
                "EUROPEAN": {"start_utc": 7, "end_utc": 15},
                "US": {"start_utc": 13, "end_utc": 21},
            },
            "overlap_bonus": 1,
            "dead_zone_hours": [21, 22, 23],
            "dead_zone_penalty": -2,
        },
        "multi_timeframe": {"enabled": True},
        "pre_filter": {
            "enabled": True,
            "skip_dead_market_volume": 0.25,
            "skip_rsi_neutral_zone": [46, 54],
            "skip_no_htf_trend": True,
            "skip_dead_session": True,
        },
        "ai_filter": {
            "enabled": True,
            "auto_approve_quality": 0.75,
            "invoke_on_borderline": True,
            "invoke_on_conflicting": True,
        },
        "interaction_rules": {
            "htf_ltf_confluence_bonus": 2,
            "session_overlap_momentum_bonus": 1,
            "counter_htf_trend_penalty": -3,
            "rsi_divergence_penalty": -2,
        },
    }
}


@pytest.fixture(autouse=True)
def mock_config():
    with patch("src.core.intraday_signal.BOT_CONFIG", INTRADAY_CONFIG):
        # Reset singleton
        import src.core.intraday_signal as mod
        mod._generator = None
        yield


def _base_analysis(**overrides):
    """Create a base analysis dict with sensible defaults."""
    data = {
        "global_trend": "UP",
        "local_trend": "BULLISH",
        "rsi": 42,
        "volume_ratio": 1.2,
        "current_price": 100.0,
        "support": 98.0,
        "resistance": 103.0,
        "ema9": 100.5,
        "ema21": 99.8,
        "last_5_direction": "UP",
        "atr": 0.8,
        "atr_ratio": 1.0,
        "macd_line": 0.1,
        "macd_signal": 0.05,
        "macd_hist": 0.05,
        "bb_upper": 102.0,
        "bb_lower": 98.0,
        "bb_width": 4.0,
        "close_prices": [99 + i * 0.05 for i in range(50)],
        "rsi_values": [45 + i * 0.2 for i in range(50)],
    }
    data.update(overrides)
    return data


def _htf_data(**overrides):
    data = {
        "htf_trend": "BULLISH",
        "htf_ema_fast": 100.3,
        "htf_ema_slow": 99.5,
        "htf_rsi": 55.0,
        "daily_bias": "LONG",
        "daily_change_pct": 1.2,
    }
    data.update(overrides)
    return data


def _session_data(**overrides):
    data = {
        "current_hour_utc": 14,
        "active_sessions": ["EUROPEAN", "US"],
        "is_overlap": True,
        "is_dead_zone": False,
        "session_quality": "HIGH",
        "quality_score_adj": 1,
    }
    data.update(overrides)
    return data


def _regime(**overrides):
    data = {
        "regime": "TRENDING",
        "recommended_min_score": 4,
        "sl_multiplier": 1.5,
        "tp_multiplier": 3.5,
        "position_size_factor": 1.2,
    }
    data.update(overrides)
    return data


class TestPreFilter:
    """Test pre-filter logic."""

    def test_dead_market_volume(self):
        from src.core.intraday_signal import intraday_pre_filter
        analysis = _base_analysis(volume_ratio=0.1)
        proceed, reason = intraday_pre_filter(analysis, _htf_data(), _session_data())
        assert proceed is False
        assert "Dead market" in reason

    def test_rsi_neutral_no_htf(self):
        from src.core.intraday_signal import intraday_pre_filter
        analysis = _base_analysis(rsi=50)
        htf = _htf_data(htf_trend="NEUTRAL")
        proceed, reason = intraday_pre_filter(analysis, htf, _session_data())
        assert proceed is False
        assert "RSI neutral" in reason

    def test_dead_session(self):
        from src.core.intraday_signal import intraday_pre_filter
        session = _session_data(session_quality="DEAD", is_dead_zone=True)
        proceed, reason = intraday_pre_filter(_base_analysis(), _htf_data(), session)
        assert proceed is False
        assert "Dead trading session" in reason

    def test_passes_good_conditions(self):
        from src.core.intraday_signal import intraday_pre_filter
        proceed, reason = intraday_pre_filter(_base_analysis(), _htf_data(), _session_data())
        assert proceed is True
        assert reason == "Passed"


class TestGenerateSignal:
    """Test signal generation logic."""

    def test_strong_buy_signal(self):
        """All indicators align for BUY — should produce a signal."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis(rsi=42, ema9=100.5, ema21=99.8, volume_ratio=1.5)
        htf = _htf_data(htf_trend="BULLISH")
        session = _session_data(quality_score_adj=1)
        regime = _regime(recommended_min_score=4)

        result = generate_intraday_signal(analysis, htf, session, regime)

        assert result["signal"] == "BUY"
        assert result["score"] >= 5
        assert result["quality"] > 0
        assert result["confidence"] > 0

    def test_strong_sell_signal(self):
        """All indicators align for SELL."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis(
            rsi=62, ema9=99.0, ema21=100.0, volume_ratio=1.5,
            last_5_direction="DOWN",
            macd_line=-0.1, macd_signal=-0.05, macd_hist=-0.05,
            current_price=100.0, resistance=102.0, support=97.5,
        )
        htf = _htf_data(htf_trend="BEARISH")
        session = _session_data(quality_score_adj=1)
        regime = _regime(recommended_min_score=4)

        result = generate_intraday_signal(analysis, htf, session, regime)

        assert result["signal"] == "SELL"
        assert result["score"] >= 5

    def test_hold_low_volume(self):
        """Low volume should produce HOLD."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis(volume_ratio=0.2)

        result = generate_intraday_signal(analysis, _htf_data(), _session_data())

        assert result["signal"] == "HOLD"
        assert result["quality"] == 0.0

    def test_hold_low_atr(self):
        """Low ATR should produce HOLD."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis(atr_ratio=0.1)

        result = generate_intraday_signal(analysis, _htf_data(), _session_data())

        assert result["signal"] == "HOLD"

    def test_no_tier1_produces_hold(self):
        """Without any Tier1 direction (neutral HTF + flat EMA), should HOLD even with good Tier2."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis(
            ema9=100.0, ema21=100.0,  # flat EMA
            rsi=42, volume_ratio=1.0,
        )
        htf = _htf_data(htf_trend="NEUTRAL")  # no HTF direction

        result = generate_intraday_signal(analysis, htf, _session_data())

        # Should be HOLD because no Tier1 is satisfied
        assert result["signal"] == "HOLD"

    def test_htf_ltf_confluence_bonus(self):
        """HTF BULLISH + EMA LONG should get confluence bonus."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis(ema9=101.0, ema21=99.5, rsi=42)
        htf = _htf_data(htf_trend="BULLISH")
        regime = _regime(recommended_min_score=4)

        result = generate_intraday_signal(analysis, htf, _session_data(), regime)

        assert result["signal"] == "BUY"
        # Score should include HTF(3) + EMA(2) + HTF+LTF confluence(2) = 7 minimum
        assert result["score"] >= 7

    def test_dead_zone_penalty(self):
        """Dead zone should penalize scores."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis()
        session = _session_data(
            session_quality="DEAD",
            quality_score_adj=-2,
            is_dead_zone=True,
            is_overlap=False,
            active_sessions=[],
        )

        result_dead = generate_intraday_signal(analysis, _htf_data(), session, _regime())

        # Compare with normal session
        session_normal = _session_data(quality_score_adj=0, is_overlap=False, active_sessions=["US"])
        result_normal = generate_intraday_signal(analysis, _htf_data(), session_normal, _regime())

        # Dead zone score should be lower than normal
        assert result_dead["score"] < result_normal["score"]

    def test_max_score_is_13(self):
        """Max base score = 13."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis()

        result = generate_intraday_signal(analysis, _htf_data(), _session_data())

        assert result["max_score"] == 13

    def test_regime_adaptive_min_score(self):
        """Regime should override default min_score."""
        from src.core.intraday_signal import generate_intraday_signal
        analysis = _base_analysis()
        regime = _regime(recommended_min_score=8)

        result = generate_intraday_signal(analysis, _htf_data(), _session_data(), regime)

        # With min_score=8, most signals won't fire
        details = result.get("details", {})
        assert details.get("min_score_required") == 8

    def test_result_structure(self):
        """Signal result has all expected keys."""
        from src.core.intraday_signal import generate_intraday_signal
        result = generate_intraday_signal(_base_analysis(), _htf_data(), _session_data())

        expected_keys = {
            "signal", "score", "max_score", "quality", "confidence",
            "reasons", "filters_passed", "details", "regime"
        }
        assert expected_keys == set(result.keys())


class TestShouldClose:
    """Test close position logic."""

    def test_rsi_extreme_long(self):
        from src.core.intraday_signal import intraday_should_close
        analysis = _base_analysis(rsi=85, current_price=105.0)
        position = {"type": "BUY", "entry": "100.0"}

        result = intraday_should_close(analysis, position)

        assert result["should_close"] is True
        assert "RSI" in result["reason"]

    def test_htf_reversal_against_long(self):
        from src.core.intraday_signal import intraday_should_close
        analysis = _base_analysis(current_price=99.0)
        position = {"type": "BUY", "entry": "100.0"}
        htf = _htf_data(htf_trend="BEARISH")

        result = intraday_should_close(analysis, position, htf)

        assert result["should_close"] is True
        assert "HTF" in result["reason"]

    def test_no_close_when_profitable(self):
        from src.core.intraday_signal import intraday_should_close
        analysis = _base_analysis(rsi=55, current_price=101.0, macd_hist=0.1)
        position = {"type": "BUY", "entry": "100.0"}
        htf = _htf_data(htf_trend="BULLISH")

        result = intraday_should_close(analysis, position, htf)

        assert result["should_close"] is False

    def test_macd_reversal_with_loss(self):
        from src.core.intraday_signal import intraday_should_close
        analysis = _base_analysis(current_price=98.0, macd_hist=-0.1)
        position = {"type": "BUY", "entry": "100.0"}

        result = intraday_should_close(analysis, position)

        assert result["should_close"] is True
        assert "MACD" in result["reason"]

    def test_no_position(self):
        from src.core.intraday_signal import intraday_should_close
        result = intraday_should_close(_base_analysis(), None)
        assert result["should_close"] is False

    def test_trailing_profit_lock(self):
        """Lock profit when >= 3% and momentum fading."""
        from src.core.intraday_signal import intraday_should_close
        analysis = _base_analysis(current_price=104.0, rsi=68, macd_hist=-0.01)
        position = {"type": "BUY", "entry": "100.0"}

        result = intraday_should_close(analysis, position)

        assert result["should_close"] is True
        assert "Trail" in result["reason"]
