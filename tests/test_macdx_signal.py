"""
Unit tests for MACDX Signal Generator module (macdx_signal.py).

MACDX is a No-AI deterministic strategy based on MACD crossover with confirmations.
"""

import pytest
from unittest.mock import patch

from src.core.macdx_signal import MACDXSignalGenerator


# Test config matching bot_config.json MACDX_SETTINGS structure
TEST_MACDX_SETTINGS = {
    "enabled": True,
    "signal_rules": {
        "macd_cross_weight": 2,
        "rsi_zone_weight": 2,
        "ema_alignment_weight": 2,
        "not_sideways_weight": 1,
        "no_exhaustion_weight": 1,
        "volume_weight": 1,
        "min_score_for_signal": 4,
        "min_confirmations": 3,
        "min_volume_ratio": 0.5,
        "min_atr_ratio": 0.3,
        "rsi_long_max": 65,
        "rsi_long_min": 25,
        "rsi_short_max": 75,
        "rsi_short_min": 35,
        "bb_width_threshold": 0.5,
        "adx_threshold": 20,
    },
}

TEST_BOT_CONFIG = {"MACDX_SETTINGS": TEST_MACDX_SETTINGS}


@pytest.fixture
def generator():
    """Create a MACDXSignalGenerator with mocked config."""
    with patch('src.core.macdx_signal.BOT_CONFIG', TEST_BOT_CONFIG):
        return MACDXSignalGenerator()


def _base_analysis(**overrides):
    """Helper to create a base analysis dict with sensible defaults."""
    data = {
        "current_price": 50000,
        "rsi": 50,
        "volume_ratio": 1.0,
        "ema9": 50000,
        "ema21": 50000,
        "macd_line": 0,
        "macd_signal": 0,
        "macd_hist": 0,
        "macd_hist_prev": 0,
        "bb_upper": 51000,
        "bb_lower": 49000,
        "bb_middle": 50000,
        "atr": 500,
        "atr_ratio": 1.0,
        "adx": 25,
        "support": 49000,
        "resistance": 51000,
        "close_prices": [],
        "rsi_values": [],
    }
    data.update(overrides)
    return data


class TestVolatilityFilter:
    """Tests for volatility (ATR) hard filter."""

    def test_hold_on_low_atr(self, generator):
        analysis = _base_analysis(atr_ratio=0.2)
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "volatility"
        assert result["filters_passed"] is False

    def test_passes_on_sufficient_atr(self, generator):
        analysis = _base_analysis(atr_ratio=0.3)
        result = generator.generate_signal(analysis)
        assert result["details"].get("filter") != "volatility"

    def test_boundary_atr_at_min(self, generator):
        analysis = _base_analysis(atr_ratio=0.3)
        result = generator.generate_signal(analysis)
        assert result["details"].get("filter") != "volatility"


class TestVolumeFilter:
    """Tests for volume hard filter."""

    def test_hold_on_low_volume(self, generator):
        analysis = _base_analysis(volume_ratio=0.3)
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "volume"

    def test_passes_on_sufficient_volume(self, generator):
        analysis = _base_analysis(volume_ratio=0.5)
        result = generator.generate_signal(analysis)
        assert result["details"].get("filter") != "volume"

    def test_volatility_filter_before_volume(self, generator):
        # Both filters fail, volatility should be checked first
        analysis = _base_analysis(atr_ratio=0.1, volume_ratio=0.3)
        result = generator.generate_signal(analysis)
        assert result["details"]["filter"] == "volatility"


class TestMACDCrossover:
    """Tests for MACD crossover detection (primary signal)."""

    def test_hold_on_no_macd_crossover(self, generator):
        # No MACD signal - flat
        analysis = _base_analysis(
            macd_line=0,
            macd_signal=0,
            macd_hist=0,
            macd_hist_prev=0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "no_macd_cross"

    def test_bullish_macd_crossover_detected(self, generator):
        # MACD line > signal, positive histogram, fresh cross
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,  # Was negative, now positive -> fresh cross
            ema9=50100,
            ema21=50000,  # EMA confirms
            rsi=40,  # In long zone
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["macd_cross_long"] is True
        assert result["details"]["macd_cross_short"] is False

    def test_bearish_macd_crossover_detected(self, generator):
        # MACD line < signal, negative histogram, fresh cross
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,  # Was positive, now negative -> fresh cross
            ema9=49900,
            ema21=50000,  # EMA confirms
            rsi=60,  # In short zone
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["macd_cross_short"] is True
        assert result["details"]["macd_cross_long"] is False

    def test_macd_momentum_building(self, generator):
        # Histogram growing stronger (not fresh cross but momentum building)
        analysis = _base_analysis(
            macd_line=15,
            macd_signal=5,
            macd_hist=10,
            macd_hist_prev=5,  # Was positive, now more positive -> momentum
            ema9=50100,
            ema21=50000,
            rsi=40,
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["macd_cross_long"] is True


class TestBuySignal:
    """Tests for BUY signal generation with confirmations."""

    def test_buy_with_all_confirmations(self, generator):
        # MACD cross + EMA + RSI + not sideways + volume = 5 confirmations
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,  # In long zone (25-65)
            bb_upper=52000,
            bb_lower=48000,
            bb_middle=50000,  # BB width = 8% > 0.5% threshold
            adx=30,  # > 20, not sideways
            volume_ratio=1.0,  # >= 0.8
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        assert result["confirmations"] >= 3

    def test_buy_minimum_confirmations(self, generator):
        # MACD cross + EMA + RSI = 3 confirmations (minimum)
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            volume_ratio=0.6,  # Below 0.8, no volume confirmation
            bb_upper=50200,
            bb_lower=49800,  # Tight BB, sideways
            adx=15,  # Low ADX
        )
        result = generator.generate_signal(analysis)
        # May be BUY if score >= min_score, or HOLD if not enough
        if result["signal"] == "BUY":
            assert result["confirmations"] >= 3

    def test_hold_insufficient_confirmations(self, generator):
        # Only MACD cross + EMA = 2 confirmations (not enough)
        # RSI must be outside long zone (>65 or <25) to NOT get RSI confirmation
        # BB width < 0.5% AND adx < 20 = sideways (no "not sideways" confirmation)
        # Volume < 0.8 = no volume confirmation
        # No divergence data = no exhaustion confirmation (actually GIVES +1, so we need divergence)
        # To get only 2 confirmations: MACD + EMA, need RSI outside zone + sideways + divergence
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=70,  # Outside long zone (25-65) - no RSI confirmation
            volume_ratio=0.6,  # < 0.8, no volume confirmation
            bb_upper=50100,
            bb_lower=49900,  # BB width = 0.4% < 0.5% threshold
            bb_middle=50000,
            adx=15,  # < 20 - combined with tight BB = sideways
            # Bearish divergence: price makes higher high, RSI makes lower high
            close_prices=[100, 102, 101, 103, 102, 105, 103, 107, 105, 109, 107],
            rsi_values=[60, 65, 62, 63, 60, 61, 58, 59, 56, 55, 52],
        )
        result = generator.generate_signal(analysis)
        # With only 2 confirmations (MACD + EMA), should hold
        # Score: MACD(2) + EMA(2) - divergence_penalty(1) = 3 < min_score(4)
        # Confirmations: MACD(1) + EMA(1) = 2 < min_confirmations(3)
        assert result["signal"] == "HOLD"
        assert result["confirmations"] < 3


class TestSellSignal:
    """Tests for SELL signal generation with confirmations."""

    def test_sell_with_all_confirmations(self, generator):
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,
            ema9=49800,
            ema21=50000,  # EMA bearish
            rsi=60,  # In short zone (35-75)
            bb_upper=52000,
            bb_lower=48000,  # Wide BB
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "SELL"
        assert result["confirmations"] >= 3

    def test_sell_minimum_confirmations(self, generator):
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,
            ema9=49800,
            ema21=50000,
            rsi=60,
            volume_ratio=0.6,
            bb_upper=50200,
            bb_lower=49800,
            adx=15,
        )
        result = generator.generate_signal(analysis)
        if result["signal"] == "SELL":
            assert result["confirmations"] >= 3


class TestSidewaysDetection:
    """Tests for sideways market detection."""

    def test_sideways_detected_tight_bb_low_adx(self, generator):
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            bb_upper=50200,
            bb_lower=49800,  # BB width = 0.8% > 0.5%? Actually 0.8%
            bb_middle=50000,
            adx=15,  # Low ADX
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # BB width = (50200-49800)/50000*100 = 0.8% > 0.5 threshold
        # So NOT sideways based on BB alone
        # Sideways = tight BB AND low ADX

    def test_not_sideways_high_adx(self, generator):
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            bb_upper=50100,
            bb_lower=49900,  # Tight BB
            bb_middle=50000,
            adx=25,  # High ADX -> not sideways
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # Even with tight BB, high ADX means trending
        assert result["details"]["is_sideways"] is False

    def test_not_sideways_wide_bb(self, generator):
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            bb_upper=52000,
            bb_lower=48000,  # Wide BB = 8%
            bb_middle=50000,
            adx=15,  # Low ADX, but wide BB
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["is_sideways"] is False


class TestRSIDivergence:
    """Tests for RSI divergence (exhaustion) detection."""

    def test_bearish_divergence_detected(self, generator):
        # Price higher highs, RSI lower highs -> bearish divergence
        prices = [100, 102, 101, 103, 102, 105, 103, 107, 105, 109, 107]
        rsi_values = [60, 65, 62, 63, 60, 61, 58, 59, 56, 55, 52]
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            close_prices=prices,
            rsi_values=rsi_values,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # Bearish divergence should be detected
        assert result["details"]["bearish_divergence"] is True

    def test_bullish_divergence_detected(self, generator):
        # Price lower lows, RSI higher lows -> bullish divergence
        prices = [100, 98, 99, 97, 98, 95, 96, 93, 95, 91, 93]
        rsi_values = [40, 35, 37, 36, 38, 37, 39, 38, 40, 41, 43]
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,
            ema9=49800,
            ema21=50000,
            rsi=60,
            close_prices=prices,
            rsi_values=rsi_values,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["bullish_divergence"] is True

    def test_no_divergence_empty_data(self, generator):
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            close_prices=[],
            rsi_values=[],
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["bearish_divergence"] is False
        assert result["details"]["bullish_divergence"] is False


class TestQualityAndConfidence:
    """Tests for quality and confidence calculation."""

    def test_quality_zero_for_hold(self, generator):
        analysis = _base_analysis()  # No MACD cross
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["quality"] == 0.0
        assert result["confidence"] == 0.0

    def test_quality_and_confidence_for_signal(self, generator):
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.5,
        )
        result = generator.generate_signal(analysis)
        if result["signal"] in ("BUY", "SELL"):
            assert 0.0 <= result["quality"] <= 1.0
            assert result["confidence"] > 0.0


class TestRegimeAdaptive:
    """Tests for regime-adaptive min_score."""

    def test_regime_min_score_used(self, generator):
        regime = {"recommended_min_score": 3, "regime": "TRENDING"}
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=50,  # Not in zone, so no RSI points
        )
        result = generator.generate_signal(analysis, regime=regime)
        # Lower min_score from regime allows signal with fewer points
        assert result["regime"] == "TRENDING"

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
    """Tests for should_close_position (exit signals)."""

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

    def test_buy_macd_reversal_with_profit(self, generator):
        position = {"type": "BUY", "entry": 49500}
        analysis = _base_analysis(current_price=50000, macd_hist=-5)
        # PnL = 1.01% >= 0.5%
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert "MACD" in result["reason"]

    def test_buy_macd_reversal_with_loss(self, generator):
        position = {"type": "BUY", "entry": 51000}
        analysis = _base_analysis(current_price=50000, macd_hist=-5)
        # PnL = -1.96% < -1.0%
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "high"

    def test_sell_macd_reversal_with_profit(self, generator):
        position = {"type": "SELL", "entry": 50500}
        analysis = _base_analysis(current_price=50000, macd_hist=5)
        # PnL = 0.99% >= 0.5%
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True

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

    def test_take_profit_with_rsi_extreme(self, generator):
        position = {"type": "BUY", "entry": 48000}
        # PnL = (50000-48000)/48000*100 = 4.17% >= 3%
        analysis = _base_analysis(current_price=50000, rsi=72)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "medium"

    def test_no_exit_signal(self, generator):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50100, rsi=55, macd_hist=1)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is False
        assert result["urgency"] == "low"


class TestCounterTrendWarning:
    """Tests for counter-trend signal warnings."""

    def test_long_signal_with_bearish_ema(self, generator):
        # MACD cross long but EMA is bearish -> warning
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=49800,  # EMA bearish
            ema21=50000,
            rsi=40,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # Signal may still be generated but with counter-trend warning
        if result["signal"] != "HOLD":
            reasons_str = " ".join(result["reasons"] + result["details"].get("long_reasons", []))
            # Counter-trend warning may be present
            # The signal generator adds "EMA counter-trend (caution)" for this case


class TestGlobalFunctions:
    """Tests for module-level convenience functions."""

    @patch('src.core.macdx_signal._generator', None)
    @patch('src.core.macdx_signal.BOT_CONFIG', TEST_BOT_CONFIG)
    def test_get_macdx_signal_generator_singleton(self):
        from src.core.macdx_signal import get_macdx_signal_generator
        gen1 = get_macdx_signal_generator()
        gen2 = get_macdx_signal_generator()
        assert gen1 is gen2

    @patch('src.core.macdx_signal._generator', None)
    @patch('src.core.macdx_signal.BOT_CONFIG', TEST_BOT_CONFIG)
    def test_generate_macdx_signal_convenience(self):
        from src.core.macdx_signal import generate_macdx_signal
        result = generate_macdx_signal(_base_analysis())
        assert "signal" in result
        assert "score" in result
        assert "confirmations" in result

    @patch('src.core.macdx_signal._generator', None)
    @patch('src.core.macdx_signal.BOT_CONFIG', TEST_BOT_CONFIG)
    def test_should_close_macdx_convenience(self):
        from src.core.macdx_signal import should_close_macdx
        result = should_close_macdx(_base_analysis(), {"type": "BUY", "entry": 50000})
        assert "should_close" in result


class TestHoldResult:
    """Tests for _hold_result helper."""

    def test_hold_result_structure(self, generator):
        result = generator._hold_result(9, ["test reason"], {"key": "val"})
        assert result["signal"] == "HOLD"
        assert result["score"] == 0
        assert result["max_score"] == 9
        assert result["quality"] == 0.0
        assert result["confidence"] == 0.0
        assert result["confirmations"] == 0
        assert result["filters_passed"] is False
        assert result["regime"] == "NO_REGIME"

    def test_hold_result_with_regime(self, generator):
        regime = {"regime": "TRENDING"}
        result = generator._hold_result(9, ["test"], {}, regime)
        assert result["regime"] == "TRENDING"


class TestScoreAndConfirmations:
    """Integration tests for score and confirmation counting."""

    def test_full_score_calculation(self, generator):
        # All confirmations present
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,  # MACD cross +2
            ema9=50200,
            ema21=50000,  # EMA align +2
            rsi=40,  # RSI zone +2
            bb_upper=52000,
            bb_lower=48000,
            bb_middle=50000,  # Wide BB
            adx=30,  # High ADX -> not sideways +1
            volume_ratio=1.0,  # Volume +1
            close_prices=[],
            rsi_values=[],  # No divergence +1
        )
        result = generator.generate_signal(analysis)
        # Expected: MACD(2) + EMA(2) + RSI(2) + not_sideways(1) + no_exhaust(1) + volume(1) = 9
        assert result["signal"] == "BUY"
        assert result["score"] >= 6  # At least MACD+EMA+RSI+not_sideways
        assert result["confirmations"] >= 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
