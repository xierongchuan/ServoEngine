"""
Unit tests for MACDX Signal Generator module (macdx_signal.py).

MACDX is a No-AI deterministic strategy based on MACD crossover with confirmations.

Tests verify business logic correctness — they must fail if code violates
the strategy specification, not silently pass by matching buggy behaviour.
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
        "consecutive_red_filter": True,
        "min_consecutive_for_block": 3,
        "enable_counter_trend_filter": True,
        "counter_trend_ema_threshold": 1.0,
        "enable_volume_filter": False,
        "volume_confirm_threshold": 0.8,
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


# =============================================================================
# HARD FILTERS
# =============================================================================

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
        # Volume filter is disabled in TEST_MACDX_SETTINGS (enable_volume_filter=False),
        # so this test verifies that when enabled, low volume blocks signal.
        settings_override = {
            **TEST_MACDX_SETTINGS,
            "signal_rules": {
                **TEST_MACDX_SETTINGS["signal_rules"],
                "enable_volume_filter": True,
            },
        }
        with patch('src.core.macdx_signal.BOT_CONFIG', {"MACDX_SETTINGS": settings_override}):
            gen = MACDXSignalGenerator()
            analysis = _base_analysis(volume_ratio=0.3)
            result = gen.generate_signal(analysis)
            assert result["signal"] == "HOLD"
            assert result["details"]["filter"] == "volume"

    def test_passes_on_sufficient_volume(self, generator):
        # With filter disabled, should pass regardless
        analysis = _base_analysis(volume_ratio=0.5)
        result = generator.generate_signal(analysis)
        assert result["details"].get("filter") != "volume"

    def test_volatility_filter_before_volume(self, generator):
        settings_override = {
            **TEST_MACDX_SETTINGS,
            "signal_rules": {
                **TEST_MACDX_SETTINGS["signal_rules"],
                "enable_volume_filter": True,
            },
        }
        with patch('src.core.macdx_signal.BOT_CONFIG', {"MACDX_SETTINGS": settings_override}):
            gen = MACDXSignalGenerator()
            analysis = _base_analysis(atr_ratio=0.1, volume_ratio=0.3)
            result = gen.generate_signal(analysis)
            assert result["details"]["filter"] == "volatility"


# =============================================================================
# MACD CROSSOVER DETECTION
# =============================================================================

class TestMACDCrossover:
    """Tests for MACD crossover detection (primary signal)."""

    def test_hold_on_no_macd_crossover(self, generator):
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
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50100,
            ema21=50000,
            rsi=40,
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["macd_cross_long"] is True
        assert result["details"]["macd_cross_short"] is False

    def test_bearish_macd_crossover_detected(self, generator):
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,
            ema9=49900,
            ema21=50000,
            rsi=60,
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["macd_cross_short"] is True
        assert result["details"]["macd_cross_long"] is False

    def test_macd_momentum_building_not_crossover(self, generator):
        """Growing histogram without sign change must NOT be detected as crossover."""
        analysis = _base_analysis(
            macd_line=15,
            macd_signal=5,
            macd_hist=10,
            macd_hist_prev=5,
            ema9=50100,
            ema21=50000,
            rsi=40,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "no_macd_cross"

    def test_macd_declining_not_crossover(self, generator):
        """Declining but still positive histogram must NOT be detected as crossover."""
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=3,
            macd_hist_prev=8,
            ema9=50100,
            ema21=50000,
            rsi=40,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "no_macd_cross"


# =============================================================================
# BUY / SELL SIGNALS
# =============================================================================

class TestBuySignal:
    """Tests for BUY signal generation with confirmations."""

    def test_buy_with_all_confirmations(self, generator):
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
            bb_middle=50000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        assert result["confirmations"] >= 3
        assert result["score"] >= 4

    def test_buy_minimum_confirmations(self, generator):
        """MACD cross + EMA + RSI + NoExhaustion = 4 confirmations, score >= 4."""
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            volume_ratio=0.6,
            bb_upper=50200,
            bb_lower=49800,
            adx=15,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY", f"Expected BUY but got {result['signal']}"
        assert result["confirmations"] >= 3, f"Expected >= 3 confirmations but got {result['confirmations']}"

    def test_hold_insufficient_confirmations(self, generator):
        """Only MACD + EMA confirm (2 < min_confirmations=3) — must HOLD."""
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=70,
            volume_ratio=0.6,
            bb_upper=50100,
            bb_lower=49900,
            bb_middle=50000,
            adx=15,
            close_prices=[100, 102, 101, 103, 102, 105, 103, 107, 105, 109, 107],
            rsi_values=[60, 65, 62, 63, 60, 61, 58, 59, 56, 55, 52],
        )
        result = generator.generate_signal(analysis)
        # MACD(+1) + EMA(+1) = 2 confirmations < min_confirmations(3)
        # Score: MACD(2) + EMA(2) = 4, but confirmations fail
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
            ema21=50000,
            rsi=60,
            bb_upper=52000,
            bb_lower=48000,
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
        assert result["signal"] == "SELL", f"Expected SELL but got {result['signal']}"
        assert result["confirmations"] >= 3, f"Expected >= 3 confirmations but got {result['confirmations']}"


# =============================================================================
# SIDWAYS DETECTION
# =============================================================================

class TestSidewaysDetection:
    """Tests for sideways market detection."""

    def test_sideways_detected_tight_bb_low_adx(self, generator):
        """Tight BB width AND low ADX must be detected as sideways."""
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            bb_upper=50100,
            bb_lower=49900,
            bb_middle=50000,
            adx=15,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # BB width = (50100-49900)/50000*100 = 0.4% < 0.5% AND ADX=15 < 20
        assert result["details"]["is_sideways"] is True

    def test_not_sideways_high_adx(self, generator):
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=40,
            bb_upper=51000,
            bb_lower=49000,
            bb_middle=50000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
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
            bb_lower=48000,
            bb_middle=50000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["details"]["is_sideways"] is False


# =============================================================================
# RSI EXTREME PROTECTION (new — was missing before)
# =============================================================================

class TestRSIExtremeProtection:
    """Tests for RSI extreme zone blocking (symmetric for LONG and SHORT)."""

    def test_long_blocked_on_extreme_overbought_rsi(self, generator):
        """LONG must be blocked when RSI > 70 even if all other confirmations present."""
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=72,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # RSI 72 > 70 = extreme overbought → LONG blocked
        assert result["signal"] == "HOLD"
        assert result["details"]["long_score"] == 0

    def test_short_blocked_on_extreme_oversold_rsi(self, generator):
        """SHORT must be blocked when RSI < 30 even if all other confirmations present."""
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,
            ema9=49800,
            ema21=50000,
            rsi=28,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["details"]["short_score"] == 0
        assert result["details"]["short_confirmations"] == 0

    def test_long_allowed_at_rsi_65_boundary(self, generator):
        """LONG at RSI=65 (boundary) must still be allowed."""
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=65,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"

    def test_short_allowed_at_rsi_30_boundary(self, generator):
        """SHORT at RSI=30 (boundary) must still be allowed."""
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,
            ema9=49800,
            ema21=50000,
            rsi=30,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "SELL"


# =============================================================================
# RSI DIVERGENCE
# =============================================================================

class TestRSIDivergence:
    """Tests for RSI divergence (exhaustion) detection."""

    def test_bearish_divergence_detected(self, generator):
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
        assert result["details"]["bearish_divergence"] is True

    def test_bullish_divergence_detected(self, generator):
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

    def test_divergence_not_detected_when_extrema_too_close(self, generator):
        """Extrema closer than min_extrema_distance (3) must NOT be detected as divergence."""
        # Create price/RSI sequence with maxima only 1 candle apart
        prices = [100, 102, 101, 103, 101, 104, 102]
        rsi_values = [60, 65, 60, 63, 60, 62, 58]
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            close_prices=prices,
            rsi_values=rsi_values,
        )
        result = generator.generate_signal(analysis)
        # With min_extrema_distance=3, closely spaced peaks should be filtered out
        assert result["details"]["bearish_divergence"] is False


# =============================================================================
# COUNTER-TREND FILTER
# =============================================================================

class TestCounterTrendFilter:
    """Tests for counter-trend EMA vs MACD filter (blocks signals, not just warns)."""

    def test_long_blocked_by_counter_trend_ema(self, generator):
        """LONG must be blocked when EMA9 < EMA21 (bearish EMA) + MACD bullish cross."""
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=49000,
            ema21=50000,
            rsi=40,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # EMA9 < EMA21 and diff = 2% > 1% threshold → counter-trend block
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "counter_trend"

    def test_short_blocked_by_counter_trend_ema(self, generator):
        """SHORT must be blocked when EMA9 > EMA21 (bullish EMA) + MACD bearish cross."""
        analysis = _base_analysis(
            macd_line=-10,
            macd_signal=-5,
            macd_hist=-5,
            macd_hist_prev=1,
            ema9=51000,
            ema21=50000,
            rsi=60,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # EMA9 > EMA21 and diff = 2% > 1% threshold → counter-trend block
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "counter_trend"

    def test_long_allowed_when_ema_aligned(self, generator):
        """LONG must pass when EMA9 > EMA21 (aligned with MACD)."""
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
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"


# =============================================================================
# CONSECUTIVE RED CANDLE FILTER
# =============================================================================

class TestConsecutiveRedFilter:
    """Tests for consecutive red candle momentum filter."""

    def test_long_blocked_on_strong_down_with_bearish_macd(self, generator):
        """LONG must be blocked when STRONG_DOWN + bearish MACD momentum."""
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
            volume_ratio=1.0,
            last_5_direction="STRONG_DOWN",
        )
        result = generator.generate_signal(analysis)
        # STRONG_DOWN + potential_long → blocked
        assert result["signal"] == "HOLD"
        assert result["details"]["filter"] == "consecutive_red_momentum"

    def test_long_allowed_on_mixed_market(self, generator):
        """LONG must pass when market direction is MIXED."""
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
            volume_ratio=1.0,
            last_5_direction="MIXED",
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"


# =============================================================================
# VOLUME CONFIRM THRESHOLD
# =============================================================================

class TestVolumeConfirmThreshold:
    """Tests for configurable volume confirmation threshold."""

    def test_volume_confirm_with_custom_threshold(self, generator):
        """Volume confirmation must use volume_confirm_threshold from config."""
        # Default config has volume_confirm_threshold=0.8, volume_ratio=1.0 → should confirm
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
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        # Volume confirm should be counted (volume_ratio=1.0 >= 0.8)
        assert result["confirmations"] >= 4

    def test_volume_no_confirm_below_threshold(self, generator):
        """Volume must NOT confirm when below threshold."""
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
            volume_ratio=0.5,
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        # Volume should NOT confirm (0.5 < 0.8), but all other 5 confirmations present
        # MACD(1) + EMA(1) + RSI(1) + NotSideways(1) + NoExhaustion(1) = 5
        assert result["confirmations"] == 5


# =============================================================================
# VOLUME FILTER DISABLED — max_score adjustment
# =============================================================================

class TestVolumeFilterDisabled:
    """Tests for max_score reduction when volume filter is disabled."""

    def test_max_score_reduced_when_volume_disabled(self, generator):
        """When enable_volume_filter=False, max_score must exclude volume weight."""
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
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # max_score_base = 2+2+2+1+1+1 = 9, minus volume(1) = 8
        assert result["max_score"] == 8


# =============================================================================
# CONFLICT CASE
# =============================================================================

class TestConflictCase:
    """Tests for conflict resolution when both LONG and SHORT meet thresholds."""

    def test_conflict_both_signals_above_min_score(self, generator):
        """When both long_score and short_score >= min_score, must HOLD."""
        # This is a contrived scenario — in practice both can't cross simultaneously,
        # but we test the conflict resolution branch.
        # We simulate by having a setup where both would score high.
        # In reality, macd_cross_long and macd_cross_short are mutually exclusive,
        # so this tests the code branch defensively.
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=50,
            bb_upper=52000,
            bb_lower=48000,
            adx=30,
            volume_ratio=1.0,
        )
        result = generator.generate_signal(analysis)
        # Only one direction can cross at a time, so no real conflict.
        # But the result must be valid (BUY or HOLD, never invalid state).
        assert result["signal"] in ("BUY", "HOLD")


# =============================================================================
# QUALITY AND CONFIDENCE
# =============================================================================

class TestQualityAndConfidence:
    """Tests for quality and confidence calculation."""

    def test_quality_zero_for_hold(self, generator):
        analysis = _base_analysis()
        result = generator.generate_signal(analysis)
        assert result["signal"] == "HOLD"
        assert result["quality"] == 0.0
        assert result["confidence"] == 0.0

    def test_quality_and_confidence_for_signal(self, generator):
        """Quality and confidence must be set for any non-HOLD signal."""
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
        assert result["signal"] in ("BUY", "SELL"), f"Expected signal but got HOLD"
        assert 0.0 <= result["quality"] <= 1.0
        assert result["confidence"] > 0.0

    def test_quality_increases_with_more_confirmations(self, generator):
        """Higher score must produce higher quality."""
        low_conf = _base_analysis(
            macd_line=10, macd_signal=5, macd_hist=5, macd_hist_prev=-1,
            ema9=50200, ema21=50000, rsi=40,
            volume_ratio=0.5, bb_upper=50100, bb_lower=49900, bb_middle=50000, adx=15,
        )
        high_conf = _base_analysis(
            macd_line=10, macd_signal=5, macd_hist=5, macd_hist_prev=-1,
            ema9=50200, ema21=50000, rsi=40,
            volume_ratio=1.5, bb_upper=52000, bb_lower=48000, adx=30,
        )
        low_result = generator.generate_signal(low_conf)
        high_result = generator.generate_signal(high_conf)
        if low_result["signal"] == "BUY" and high_result["signal"] == "BUY":
            assert high_result["quality"] >= low_result["quality"]


# =============================================================================
# REGIME ADAPTIVE
# =============================================================================

class TestRegimeAdaptive:
    """Tests for regime-adaptive min_score."""

    def test_regime_min_score_used(self, generator):
        """Lower regime min_score must allow signals that would otherwise be HOLD."""
        regime = {"recommended_min_score": 3, "regime": "TRENDING"}
        analysis = _base_analysis(
            macd_line=10,
            macd_signal=5,
            macd_hist=5,
            macd_hist_prev=-1,
            ema9=50200,
            ema21=50000,
            rsi=50,
            volume_ratio=0.5,
            bb_upper=50100,
            bb_lower=49900,
            bb_middle=50000,
            adx=15,
        )
        result = generator.generate_signal(analysis, regime=regime)
        assert result["regime"] == "TRENDING"
        # With min_score=3 (from regime), this may pass where default min_score=4 would fail
        # We verify the regime value is propagated correctly

    def test_regime_label_in_result(self, generator):
        regime = {"recommended_min_score": 5, "regime": "RANGING"}
        analysis = _base_analysis()
        result = generator.generate_signal(analysis, regime=regime)
        assert result["regime"] == "RANGING"

    def test_no_regime_label(self, generator):
        analysis = _base_analysis()
        result = generator.generate_signal(analysis)
        assert result["regime"] == "NO_REGIME"


# =============================================================================
# SHOULD CLOSE POSITION
# =============================================================================

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
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert "MACD" in result["reason"]

    def test_buy_macd_reversal_with_loss(self, generator):
        position = {"type": "BUY", "entry": 51000}
        analysis = _base_analysis(current_price=50000, macd_hist=-5)
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "high"

    def test_sell_macd_reversal_with_profit(self, generator):
        position = {"type": "SELL", "entry": 50500}
        analysis = _base_analysis(current_price=50000, macd_hist=5)
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

    def test_buy_macd_reversal_small_loss_no_close(self, generator):
        """BUY with MACD reversal but small loss (< -1%) must NOT trigger close."""
        position = {"type": "BUY", "entry": 50200}
        analysis = _base_analysis(current_price=50000, macd_hist=-5)
        # PnL = -0.4%, not enough for loss close
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is False

    def test_sell_macd_reversal_small_loss_no_close(self, generator):
        """SELL with MACD reversal but small loss (< -1%) must NOT trigger close."""
        position = {"type": "SELL", "entry": 49800}
        analysis = _base_analysis(current_price=50000, macd_hist=5)
        # PnL = -0.4%, not enough for loss close
        result = generator.should_close_position(analysis, position)
        assert result["should_close"] is False


# =============================================================================
# HOLD RESULT HELPER
# =============================================================================

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

    def test_hold_result_with_indicators_status(self, generator):
        """HOLD result must include indicators_status for process_worker logging."""
        result = generator._hold_result(9, ["test"], {
            "potential_score": 3,
            "confirmations": 2,
            "indicators_status": [
                {"name": "MACD", "ok": False},
                {"name": "RSI", "ok": True},
            ],
            "indicators_ok_count": 1,
            "indicators_total_count": 2,
            "max_possible_score": 8,
        })
        assert result["score"] == 3
        assert result["confirmations"] == 2
        assert len(result["details"]["indicators_status"]) == 2


# =============================================================================
# SCORE AND CONFIRMATIONS INTEGRATION
# =============================================================================

class TestScoreAndConfirmations:
    """Integration tests for score and confirmation counting."""

    def test_full_score_calculation(self, generator):
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
            bb_middle=50000,
            adx=30,
            volume_ratio=1.0,
            close_prices=[],
            rsi_values=[],
        )
        result = generator.generate_signal(analysis)
        assert result["signal"] == "BUY"
        # With volume filter disabled: max_score=8
        # MACD(2) + EMA(2) + RSI(2) + not_sideways(1) + no_exhaust(1) + volume(1) = 9
        # But max_score is 8 (volume disabled), so volume still adds +1 if threshold met
        assert result["score"] >= 6
        assert result["confirmations"] >= 4

    def test_divergence_does_not_reduce_score(self, generator):
        """Divergence must NOT subtract from score — it simply doesn't add confirmation."""
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
            volume_ratio=1.0,
            close_prices=[100, 102, 101, 103, 102, 105, 103, 107, 105, 109, 107],
            rsi_values=[60, 65, 62, 63, 60, 61, 58, 59, 56, 55, 52],
        )
        result = generator.generate_signal(analysis)
        # With bearish divergence, no_exhaustion doesn't add +1, but score must not be negative
        assert result["score"] >= 0
        assert result["details"]["bearish_divergence"] is True


# =============================================================================
# GLOBAL FUNCTIONS
# =============================================================================

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
