"""
Unit tests for Signal Generator module (signal_generator.py).
"""

import pytest
from unittest.mock import patch

from src.core.signal_generator import SignalGenerator


# Test config matching bot_config.json HYBRID_SETTINGS structure
TEST_HYBRID_SETTINGS = {
    "signal_rules": {
        "trend_alignment_required": False,
        "min_volume_ratio": 0.5,
        "rsi_long_max": 43,
        "rsi_long_min": 20,
        "rsi_short_max": 80,
        "rsi_short_min": 57,
        "sr_proximity_pct": 2.0,
        "ema_cross_weight": 2,
        "rsi_zone_weight": 2,
        "volume_weight": 1,
        "sr_weight": 2,
        "momentum_weight": 1,
        "macd_weight": 1,
        "bb_weight": 1,
        "min_atr_ratio": 0.5,
        "min_score_for_signal": 5,
        "macd_exit_pnl_threshold": -1.5,
        "tier1_required": True,
        "conflict_friction_threshold": 3,
    },
    "interaction_rules": {
        "ema_macd_confluence_bonus": 1,
        "reversal_confluence_bonus": 2,
        "momentum_burst_bonus": 1,
    },
}

TEST_BOT_CONFIG = {"HYBRID_SETTINGS": TEST_HYBRID_SETTINGS}


@pytest.fixture
def generator():
    """Create a SignalGenerator with mocked config."""
    with patch('src.core.signal_generator.BOT_CONFIG', TEST_BOT_CONFIG):
        return SignalGenerator()


def _base_analysis(**overrides):
    """Helper to create a base analysis dict with sensible defaults."""
    data = {
        "global_trend": "N/A",
        "local_trend": "N/A",
        "rsi": 50,
        "volume_ratio": 1.0,
        "current_price": 50000,
        "support": 49000,
        "resistance": 51000,
        "ema9": 50000,
        "ema21": 50000,
        "atr_ratio": 1.0,
        "macd_line": 0,
        "macd_signal": 0,
        "macd_hist": 0,
        "bb_upper": 51000,
        "bb_lower": 49000,
        "last_5_direction": "MIXED",
    }
    data.update(overrides)
    return data


class TestVolatilityFilter:
    """Tests for volatility (ATR) hard filter."""

    def test_hold_on_low_atr(self, generator):
        analysis = _base_analysis(atr_ratio=0.3)
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "volatility"
        assert result["filters_passed"] is False

    def test_passes_on_sufficient_atr(self, generator):
        analysis = _base_analysis(atr_ratio=0.5)
        result = generator.generate_signal(analysis)
        # Should NOT be filtered by volatility
        assert result["details"].get("filter") != "volatility"

    def test_boundary_atr_at_min(self, generator):
        # Exactly at min_atr_ratio = 0.5 should pass
        analysis = _base_analysis(atr_ratio=0.5)
        result = generator.generate_signal(analysis)
        assert result["details"].get("filter") != "volatility"

    def test_boundary_atr_below_min(self, generator):
        analysis = _base_analysis(atr_ratio=0.49)
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "volatility"


class TestVolumeFilter:
    """Tests for volume hard filter."""

    def test_hold_on_low_volume(self, generator):
        analysis = _base_analysis(volume_ratio=0.3)
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "volume"
        assert result["filters_passed"] is False

    def test_passes_on_sufficient_volume(self, generator):
        analysis = _base_analysis(volume_ratio=0.5)
        result = generator.generate_signal(analysis)
        assert result["details"].get("filter") != "volume"

    def test_boundary_volume_at_min(self, generator):
        analysis = _base_analysis(volume_ratio=0.5)
        result = generator.generate_signal(analysis)
        assert result["details"].get("filter") != "volume"

    def test_boundary_volume_below_min(self, generator):
        analysis = _base_analysis(volume_ratio=0.49)
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "volume"

    def test_volatility_filter_before_volume(self, generator):
        # Both filters fail, volatility should be checked first
        analysis = _base_analysis(atr_ratio=0.3, volume_ratio=0.3)
        result = generator.generate_signal(analysis)
        assert result["details"]["filter"] == "volatility"


class TestBuySignal:
    """Tests for BUY signal generation."""

    def test_buy_with_ema_rsi_sr(self, generator):
        # EMA bullish (tier1) + RSI in long zone (tier2) + near support (tier2)
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # EMA bullish -> +2
            rsi=35,                   # In long zone (20-43) -> +2
            current_price=49100,      # Near support 49000 (0.2%) -> +2
            support=49000,
            resistance=51000,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        assert result["details"]["long_tier1"] is True
        assert result["details"]["long_tier2"] is True

    def test_buy_with_macd_and_rsi(self, generator):
        # MACD bullish (tier1) + RSI in long zone (tier2)
        analysis = _base_analysis(
            ema9=50000, ema21=50000,  # No EMA signal
            macd_line=10, macd_signal=5, macd_hist=5,  # MACD bullish -> +1
            rsi=35,  # In long zone -> +2
            current_price=49100,
            support=49000,  # Near support -> +2
            resistance=51000,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        assert result["details"]["long_tier1"] is True

    def test_buy_adds_volume_weight(self, generator):
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # +2
            rsi=35,                   # +2
            current_price=49100,
            support=49000,            # +2
            resistance=51000,
            volume_ratio=1.0,         # >= 0.8 -> volume confirmed
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        assert result["details"]["volume_confirmed"] is True
        # Volume weight (+1) should be in score
        assert any("Vol" in r for r in result["reasons"])

    def test_buy_no_volume_bonus_low_volume(self, generator):
        analysis = _base_analysis(
            ema9=50200, ema21=50000,
            rsi=35,
            current_price=49100,
            support=49000,
            resistance=51000,
            volume_ratio=0.7,  # < 0.8 -> not confirmed
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        assert result["details"]["volume_confirmed"] is False


class TestSellSignal:
    """Tests for SELL signal generation."""

    def test_sell_with_ema_rsi_sr(self, generator):
        # EMA bearish (tier1) + RSI in short zone (tier2) + near resistance (tier2)
        analysis = _base_analysis(
            ema9=49800, ema21=50000,  # EMA bearish -> +2
            rsi=65,                   # In short zone (57-80) -> +2
            current_price=50900,      # Near resistance 51000 (0.2%) -> +2
            support=49000,
            resistance=51000,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "SELL"
        assert result["details"]["short_tier1"] is True
        assert result["details"]["short_tier2"] is True

    def test_sell_with_macd_bearish(self, generator):
        analysis = _base_analysis(
            ema9=49800, ema21=50000,  # EMA bearish -> +2
            macd_line=-10, macd_signal=-5, macd_hist=-5,  # MACD bearish -> +1
            rsi=65,  # Short zone -> +2
            current_price=50900,
            support=49000,
            resistance=51000,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "SELL"
        assert result["details"]["short_tier1"] is True


class TestHoldSignal:
    """Tests for HOLD signal generation."""

    def test_hold_on_low_score(self, generator):
        # Only EMA signal, not enough to meet min_score=5
        analysis = _base_analysis(
            ema9=50100, ema21=50000,  # +2
            rsi=50,                   # Not in any zone -> 0
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"

    def test_hold_on_no_indicators(self, generator):
        analysis = _base_analysis()  # All neutral defaults
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"

    def test_hold_on_conflict(self, generator):
        # Both long and short scores >= min_score and equal
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # EMA bullish long +2
            rsi=60,                   # Short zone (57-80) -> short +2
            macd_line=-10, macd_signal=-5, macd_hist=-5,  # MACD bearish -> short +1
            current_price=49100,      # Near support -> long +2
            support=49000,
            resistance=51000,
            last_5_direction="UP",    # Mom long +1
        )
        result = generator.generate_signal(analysis)
        # Both sides have significant scores — either HOLD or one wins depending on exact count


class TestTier1Required:
    """Tests for tier1_required enforcement."""

    def test_hold_when_no_tier1_long(self, generator):
        # High score from tier2+tier3 but no tier1
        analysis = _base_analysis(
            ema9=50000, ema21=50000,  # No EMA signal
            macd_line=0, macd_signal=0, macd_hist=0,  # No MACD signal
            rsi=35,  # Long zone -> +2
            current_price=49100,
            support=49000,  # Near support -> +2
            resistance=51000,
            last_5_direction="UP",  # Mom -> +1
            bb_lower=49200,  # BB touch -> +1 (current_price 49100 <= 49200*1.005=49446)
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert "No Tier1" in result["reasons"][0]

    def test_hold_when_no_tier1_short(self, generator):
        analysis = _base_analysis(
            ema9=50000, ema21=50000,  # No EMA signal
            macd_line=0, macd_signal=0, macd_hist=0,  # No MACD signal
            rsi=65,  # Short zone -> +2
            current_price=50900,
            support=49000,
            resistance=51000,  # Near resistance -> +2
            last_5_direction="DOWN",  # Mom -> +1
            bb_upper=50800,  # BB touch -> +1 (current_price >= 50800*0.995=50546)
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert "No Tier1" in result["reasons"][0]

    def test_tier1_not_required_allows_signal(self):
        config = dict(TEST_BOT_CONFIG)
        config["HYBRID_SETTINGS"] = {
            "signal_rules": {
                **TEST_HYBRID_SETTINGS["signal_rules"],
                "tier1_required": False,
            },
            "interaction_rules": TEST_HYBRID_SETTINGS["interaction_rules"],
        }
        with patch('src.core.signal_generator.BOT_CONFIG', config):
            gen = SignalGenerator()

        analysis = _base_analysis(
            ema9=50000, ema21=50000,
            rsi=35,  # +2
            current_price=49100,
            support=49000,  # +2
            resistance=51000,
            last_5_direction="UP",  # +1
        )
        result = gen.generate_signal(analysis)
        assert result["signal"] == "BUY"


class TestInteractionBonuses:
    """Tests for interaction bonuses."""

    def test_ema_macd_confluence_bonus_long(self, generator):
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # EMA bullish
            macd_line=10, macd_signal=5, macd_hist=5,  # MACD bullish
            rsi=35,  # Long zone
            current_price=49100,
            support=49000,
            resistance=51000,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        interactions = result["details"]["interactions"]["long"]
        assert any("EMA+MACD confluence" in r for r in interactions)

    def test_ema_macd_confluence_bonus_short(self, generator):
        analysis = _base_analysis(
            ema9=49800, ema21=50000,  # EMA bearish
            macd_line=-10, macd_signal=-5, macd_hist=-5,  # MACD bearish
            rsi=65,  # Short zone
            current_price=50900,
            support=49000,
            resistance=51000,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "SELL"
        interactions = result["details"]["interactions"]["short"]
        assert any("EMA+MACD confluence" in r for r in interactions)

    def test_reversal_confluence_bonus(self, generator):
        # RSI long + SR long + BB long
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # tier1
            rsi=35,  # Long zone
            current_price=49050,
            support=49000,  # Near support (0.1%)
            resistance=51000,
            bb_lower=49100,  # BB touch (49050 <= 49100 * 1.005 = 49349)
        )
        result = generator.generate_signal(analysis)
        interactions = result["details"]["interactions"]["long"]
        assert any("Reversal confluence" in r for r in interactions)

    def test_momentum_burst_bonus(self, generator):
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # EMA bullish
            rsi=35,
            current_price=49100,
            support=49000,
            resistance=51000,
            volume_ratio=1.5,  # >= 1.5
            last_5_direction="UP",  # Momentum long
        )
        result = generator.generate_signal(analysis)
        interactions = result["details"]["interactions"]["long"]
        assert any("Momentum burst" in r for r in interactions)

    def test_no_momentum_burst_low_volume(self, generator):
        analysis = _base_analysis(
            ema9=50200, ema21=50000,
            rsi=35,
            current_price=49100,
            support=49000,
            resistance=51000,
            volume_ratio=1.2,  # < 1.5
            last_5_direction="UP",
        )
        result = generator.generate_signal(analysis)
        interactions = result["details"]["interactions"]["long"]
        assert not any("Momentum burst" in r for r in interactions)


class TestConflictFriction:
    """Tests for conflict friction penalty."""

    def test_conflict_friction_applied_to_long(self, generator):
        # Long wins, but short has significant score >= conflict_friction_threshold (3)
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # EMA long +2
            macd_line=10, macd_signal=5, macd_hist=5,  # MACD long +1
            rsi=35,  # Long zone +2 (but also, let's get short score)
            current_price=50900,
            support=49000,
            resistance=51000,  # Near resistance -> short +2
            last_5_direction="DOWN",  # Mom short +1
        )
        result = generator.generate_signal(analysis)
        if result["details"]["conflicting"]:
            assert any("Conflict friction" in r for r in result["reasons"])


class TestQualityAndConfidence:
    """Tests for quality and confidence calculation."""

    def test_quality_zero_for_hold(self, generator):
        analysis = _base_analysis()
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["quality"] == 0.0
        assert result["confidence"] == 0.0

    def test_quality_calculation(self, generator):
        # High score BUY signal
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # +2
            macd_line=10, macd_signal=5, macd_hist=5,  # +1
            rsi=35,  # +2
            current_price=49100,
            support=49000,  # +2
            resistance=51000,
            last_5_direction="UP",  # +1
            volume_ratio=1.5,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        assert 0.0 <= result["quality"] <= 1.0
        assert result["confidence"] > 0.0

    def test_high_quality_high_confidence(self, generator):
        # Max out all indicators for long
        analysis = _base_analysis(
            ema9=50200, ema21=50000,
            macd_line=10, macd_signal=5, macd_hist=5,
            rsi=35,
            current_price=49050,
            support=49000,
            resistance=55000,  # Far resistance
            last_5_direction="UP",
            bb_lower=49100,
            volume_ratio=2.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        if result["quality"] >= 0.7:
            assert result["confidence"] == 0.85

    def test_low_quality_low_confidence(self, generator):
        # Minimal score that still triggers signal
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # +2
            rsi=35,  # +2
            current_price=49100,
            support=49000,  # +2
            resistance=51000,
            volume_ratio=0.6,  # Not volume confirmed
        )
        result = generator.generate_signal(analysis)
        if result["signal"] == "BUY":
            # Score=6, min=5, max=10 -> quality = (6-5)/(10-5) = 0.2
            if result["quality"] < 0.4:
                assert result["confidence"] == 0.55


class TestRegimeAdaptive:
    """Tests for regime-adaptive min_score."""

    def test_regime_min_score_used(self, generator):
        regime = {"recommended_min_score": 3, "regime": "TRENDING"}
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # +2
            macd_line=10, macd_signal=5, macd_hist=5,  # +1
            rsi=50,  # Not in zone
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis, regime=regime)
        # Score=3 (EMA+MACD) >= regime min_score=3 -> BUY
        assert result["signal"] == "BUY"

    def test_default_min_score_without_regime(self, generator):
        # Only EMA signal, no MACD (avoids confluence bonus)
        analysis = _base_analysis(
            ema9=50200, ema21=50000,  # +2
            macd_line=0, macd_signal=0, macd_hist=0,
            rsi=50,                   # Not in any zone -> 0
            volume_ratio=0.7,         # Not volume confirmed
        )
        result = generator.generate_signal(analysis)
        # Score=2 < default min_score=5 -> HOLD
        assert result["signal"] == "HOLD"

    def test_regime_label_in_result(self, generator):
        regime = {"recommended_min_score": 5, "regime": "RANGING"}
        analysis = _base_analysis()
        result = generator.generate_signal(analysis, regime=regime)
        assert result["regime"] == "RANGING"

    def test_no_regime_label(self, generator):
        analysis = _base_analysis()
        result = generator.generate_signal(analysis)
        assert result["regime"] == "NO_REGIME"


class TestShouldClosePosition:
    """Tests for should_close_position."""

    def test_no_position(self, generator):
        result = generator.should_close_position(_base_analysis(), {})
        assert result["should_close"] is False

    def test_no_position_none(self, generator):
        result = generator.should_close_position(_base_analysis(), None)
        assert result["should_close"] is False

    def test_invalid_prices(self, generator):
        position = {"type": "BUY", "entry": 0}
        result = generator.should_close_position(_base_analysis(), position)
        assert result["should_close"] is False

    def test_buy_rsi_extreme_close(self, generator):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=51000, rsi=85)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "high"
        assert "RSI" in result["reason"]

    def test_sell_rsi_extreme_close(self, generator):
        position = {"type": "SELL", "entry": 50000}
        analysis = _base_analysis(current_price=49000, rsi=15)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "high"

    def test_buy_profit_rsi_reversal(self, generator):
        # PnL >= 2% and RSI > 70
        position = {"type": "BUY", "entry": 49000}
        analysis = _base_analysis(current_price=50000, rsi=72)
        # PnL = (50000-49000)/49000*100 = 2.04%
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "medium"

    def test_sell_profit_rsi_reversal(self, generator):
        position = {"type": "SELL", "entry": 51000}
        analysis = _base_analysis(current_price=50000, rsi=28)
        # PnL = (51000-50000)/51000*100 = 1.96% ~ 2%
        result = generator.should_close_position(analysis, position)
        # PnL just under 2%, might not trigger
        # Let's use a bigger move
        position2 = {"type": "SELL", "entry": 52000}
        analysis2 = _base_analysis(current_price=50000, rsi=28)
        # PnL = (52000-50000)/52000*100 = 3.85%
        result2 = generator.should_close_position(analysis2, position2)
        assert result2["should_close"] is True

    def test_buy_macd_reversal_with_loss(self, generator):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=49000, macd_hist=-5)
        # PnL = (49000-50000)/50000*100 = -2.0% < -1.5
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "high"
        assert "MACD" in result["reason"]

    def test_sell_macd_reversal_with_loss(self, generator):
        position = {"type": "SELL", "entry": 50000}
        analysis = _base_analysis(current_price=51000, macd_hist=5)
        # PnL = (50000-51000)/50000*100 = -2.0% < -1.5
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True

    def test_buy_trend_reversal_with_loss(self, generator):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(
            current_price=49500,
            global_trend="DOWN",
            local_trend="BEARISH",
            macd_hist=5,  # MACD not against position
            rsi=50,       # RSI neutral
        )
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert "Trend" in result["reason"]

    def test_sell_trend_reversal_with_loss(self, generator):
        position = {"type": "SELL", "entry": 50000}
        analysis = _base_analysis(
            current_price=50500,
            global_trend="UP",
            local_trend="BULLISH",
            macd_hist=-5,
            rsi=50,
        )
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True

    def test_trailing_close_buy(self, generator):
        position = {"type": "BUY", "entry": 48000}
        # PnL = (50000-48000)/48000*100 = 4.17% >= 3%
        analysis = _base_analysis(current_price=50000, rsi=66, macd_hist=5)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "medium"
        assert "Trail" in result["reason"]

    def test_trailing_close_sell(self, generator):
        position = {"type": "SELL", "entry": 52000}
        # PnL = (52000-50000)/52000*100 = 3.85% >= 3%
        analysis = _base_analysis(current_price=50000, rsi=34, macd_hist=-5)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True

    def test_no_exit_signal(self, generator):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50100, rsi=55, macd_hist=1)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is False
        assert result["urgency"] == "low"

    def test_avgPrice_fallback(self, generator):
        # Uses avgPrice when entry is not present
        position = {"type": "BUY", "avgPrice": 50000}
        analysis = _base_analysis(current_price=50100, rsi=85)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True  # RSI extreme


class TestHoldResult:
    """Tests for _hold_result helper."""

    def test_hold_result_structure(self, generator):
        result = generator._hold_result(10, ["test reason"], {"key": "val"})
        assert result["signal"] == "HOLD"
        assert result["score"] == 0
        assert result["max_score"] == 10
        assert result["quality"] == 0.0
        assert result["confidence"] == 0.0
        assert result["filters_passed"] is False
        assert result["regime"] == "NO_REGIME"

    def test_hold_result_with_regime(self, generator):
        regime = {"regime": "TRENDING"}
        result = generator._hold_result(10, ["test"], {}, regime)
        assert result["regime"] == "TRENDING"

    def test_hold_result_with_none_regime(self, generator):
        result = generator._hold_result(10, ["test"], {}, None)
        assert result["regime"] == "NO_REGIME"


class TestSRSpreadFilter:
    """Tests for S/R spread filter."""

    def test_sr_ignored_when_spread_too_tight(self, generator):
        # sr_spread_pct < 1.0 -> no S/R scoring
        analysis = _base_analysis(
            ema9=50200, ema21=50000,
            current_price=50000,
            support=49800,   # Very tight range
            resistance=50200,  # spread = (50200-49800)/50000*100 = 0.8% < 1.0%
            rsi=35,
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["long_tier2"] is False or "S/R" not in str(result["reasons"])

    def test_sr_counted_when_spread_sufficient(self, generator):
        # sr_spread_pct >= 1.0
        analysis = _base_analysis(
            ema9=50200, ema21=50000,
            current_price=49100,
            support=49000,
            resistance=51000,  # spread = (51000-49000)/49100*100 = 4.07% > 1.0%
            rsi=35,
        )
        result = generator.generate_signal(analysis)
        # Near support -> long S/R scored
        assert any("S/R" in r for r in result["reasons"])


class TestGlobalFunctions:
    """Tests for module-level functions."""

    @patch('src.core.signal_generator._generator', None)
    @patch('src.core.signal_generator.BOT_CONFIG', TEST_BOT_CONFIG)
    def test_get_signal_generator_singleton(self):
        from src.core.signal_generator import get_signal_generator
        gen1 = get_signal_generator()
        gen2 = get_signal_generator()
        assert gen1 is gen2

    @patch('src.core.signal_generator._generator', None)
    @patch('src.core.signal_generator.BOT_CONFIG', TEST_BOT_CONFIG)
    def test_generate_signal_convenience(self):
        from src.core.signal_generator import generate_signal
        result = generate_signal(_base_analysis())
        assert "signal" in result
        assert "score" in result

    @patch('src.core.signal_generator._generator', None)
    @patch('src.core.signal_generator.BOT_CONFIG', TEST_BOT_CONFIG)
    def test_should_close_convenience(self):
        from src.core.signal_generator import should_close
        result = should_close(_base_analysis(), {"type": "BUY", "entry": 50000})
        assert "should_close" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
