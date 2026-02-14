"""
Unit tests for Market Regime Detector module (regime.py).
"""

import pytest
from unittest.mock import patch

from src.core.regime import MarketRegimeDetector


# Default test config matching bot_config.json structure
TEST_REGIME_CONFIG = {
    "lookback_candles": 10,
    "ema_spread_thresholds": {
        "no_trend": 0.15,
        "weak": 0.5,
        "strong": 1.5
    },
    "volatility_percentile_window": 100,
    "regime_params": {
        "TRENDING": {
            "min_score": 4,
            "sl_multiplier": 1.5,
            "tp_multiplier": 3.5,
            "position_size_factor": 1.2
        },
        "RANGING": {
            "min_score": 6,
            "sl_multiplier": 1.0,
            "tp_multiplier": 1.5,
            "position_size_factor": 0.8
        },
        "VOLATILE": {
            "min_score": 5,
            "sl_multiplier": 2.5,
            "tp_multiplier": 2.5,
            "position_size_factor": 0.6
        },
        "TRANSITIONAL": {
            "min_score": 7,
            "sl_multiplier": 2.0,
            "tp_multiplier": 2.5,
            "position_size_factor": 0.5
        }
    }
}


@pytest.fixture
def detector():
    """Create a MarketRegimeDetector with test config."""
    return MarketRegimeDetector(config=TEST_REGIME_CONFIG)


class TestTrendStrength:
    """Tests for _calculate_trend_strength."""

    def test_no_trend_when_ema9_none(self, detector):
        strength, category = detector._calculate_trend_strength(None, 50000)
        assert strength == 0.0
        assert category == "NO_TREND"

    def test_no_trend_when_ema21_none(self, detector):
        strength, category = detector._calculate_trend_strength(50000, None)
        assert strength == 0.0
        assert category == "NO_TREND"

    def test_no_trend_when_both_none(self, detector):
        strength, category = detector._calculate_trend_strength(None, None)
        assert strength == 0.0
        assert category == "NO_TREND"

    def test_no_trend_when_ema21_zero(self, detector):
        strength, category = detector._calculate_trend_strength(50000, 0)
        assert strength == 0.0
        assert category == "NO_TREND"

    def test_no_trend_small_spread(self, detector):
        # spread = |50000 - 49990| / 49990 * 100 = 0.02% < 0.15%
        strength, category = detector._calculate_trend_strength(50000, 49990)
        assert strength == 0.0
        assert category == "NO_TREND"

    def test_weak_trend(self, detector):
        # spread = |50200 - 50000| / 50000 * 100 = 0.4% (>0.15, <0.5)
        strength, category = detector._calculate_trend_strength(50200, 50000)
        assert strength == 0.33
        assert category == "WEAK_TREND"

    def test_moderate_trend(self, detector):
        # spread = |50500 - 50000| / 50000 * 100 = 1.0% (>0.5, <1.5)
        strength, category = detector._calculate_trend_strength(50500, 50000)
        assert strength == 0.66
        assert category == "MODERATE_TREND"

    def test_strong_trend(self, detector):
        # spread = |51000 - 50000| / 50000 * 100 = 2.0% (>1.5)
        strength, category = detector._calculate_trend_strength(51000, 50000)
        assert strength == 1.0
        assert category == "STRONG_TREND"

    def test_strong_trend_bearish(self, detector):
        # ema9 < ema21, spread = |49000 - 50000| / 50000 * 100 = 2.0% (>1.5)
        strength, category = detector._calculate_trend_strength(49000, 50000)
        assert strength == 1.0
        assert category == "STRONG_TREND"

    def test_boundary_no_trend_to_weak(self, detector):
        # Exactly at no_trend threshold (0.15%)
        # spread = 0.15 => ema9 = 50000 * (1 + 0.0015) = 50075
        strength, category = detector._calculate_trend_strength(50075, 50000)
        assert category == "WEAK_TREND"
        assert strength == 0.33


class TestVolatilityState:
    """Tests for _calculate_volatility_state."""

    def test_normal_when_bb_upper_none(self, detector):
        result = detector._calculate_volatility_state(None, 49000, [50000], 1.0)
        assert result == "NORMAL"

    def test_normal_when_bb_lower_none(self, detector):
        result = detector._calculate_volatility_state(51000, None, [50000], 1.0)
        assert result == "NORMAL"

    def test_normal_when_close_prices_empty(self, detector):
        result = detector._calculate_volatility_state(51000, 49000, [], 1.0)
        assert result == "NORMAL"

    def test_compressed_low_atr_insufficient_history(self, detector):
        # < 20 history entries, atr_ratio < 0.7 -> COMPRESSED
        result = detector._calculate_volatility_state(51000, 49000, [50000], 0.5)
        assert result == "COMPRESSED"

    def test_expanded_high_atr_insufficient_history(self, detector):
        # < 20 history entries, atr_ratio > 2.0 -> EXPANDED
        result = detector._calculate_volatility_state(51000, 49000, [50000], 2.5)
        assert result == "EXPANDED"

    def test_normal_atr_insufficient_history(self, detector):
        # < 20 history entries, 0.7 <= atr_ratio <= 2.0 -> NORMAL
        result = detector._calculate_volatility_state(51000, 49000, [50000], 1.0)
        assert result == "NORMAL"

    def test_with_sufficient_history_normal(self, detector):
        # Fill bb_width_history with a range of widths
        for i in range(25):
            detector.bb_width_history.append(1000 + i * 100)
        # Median-ish width: sorted from 1000 to 3400, p20 ~= 1400, p80 ~= 2800
        # Current width = 2000 (between p20 and p80), atr_ratio = 1.0
        result = detector._calculate_volatility_state(51000, 49000, [50000], 1.0)
        assert result == "NORMAL"

    def test_with_sufficient_history_expanded_by_atr(self, detector):
        # Fill with normal widths
        for _ in range(25):
            detector.bb_width_history.append(2000)

        # atr_ratio > 2.0 -> EXPANDED even with normal BB width
        result = detector._calculate_volatility_state(51000, 49000, [50000], 2.5)
        assert result == "EXPANDED"

    def test_with_sufficient_history_compressed(self, detector):
        # Fill with widths where current will be in bottom 20%
        for i in range(25):
            detector.bb_width_history.append(2000 + i * 100)

        # Current width = 100 (much smaller than all others), atr_ratio < 0.7
        result = detector._calculate_volatility_state(50050, 49950, [50000], 0.5)
        assert result == "COMPRESSED"

    def test_with_sufficient_history_expanded_by_bb_width(self, detector):
        # Fill with small widths
        for _ in range(25):
            detector.bb_width_history.append(500)

        # Current width = 5000 (in top 20%), atr_ratio normal
        result = detector._calculate_volatility_state(52500, 47500, [50000], 1.0)
        assert result == "EXPANDED"


class TestDirectionalConsistency:
    """Tests for _calculate_directional_consistency."""

    def test_empty_prices(self, detector):
        consistency, category = detector._calculate_directional_consistency([])
        assert consistency == 0.0
        assert category == "CHOPPY"

    def test_single_price(self, detector):
        consistency, category = detector._calculate_directional_consistency([100])
        assert consistency == 0.0
        assert category == "CHOPPY"

    def test_all_up_directional(self, detector):
        # 10 consecutive up moves: up_count=9, total=9, ratio=1.0
        # consistency = |1.0 - 0.5| * 2 = 1.0
        prices = [100 + i for i in range(10)]
        consistency, category = detector._calculate_directional_consistency(prices)
        assert consistency == 1.0
        assert category == "DIRECTIONAL"

    def test_all_down_directional(self, detector):
        # All down moves: up_count=0, total=9, ratio=0.0
        # consistency = |0.0 - 0.5| * 2 = 1.0
        prices = [110 - i for i in range(10)]
        consistency, category = detector._calculate_directional_consistency(prices)
        assert consistency == 1.0
        assert category == "DIRECTIONAL"

    def test_alternating_choppy(self, detector):
        # Alternating up/down: up_count ~= total/2
        prices = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101]
        consistency, category = detector._calculate_directional_consistency(prices)
        # up_count = 5, total = 9 -> ratio = 5/9 ~ 0.556
        # consistency = |0.556 - 0.5| * 2 = 0.111
        assert consistency < 0.2
        assert category == "CHOPPY"

    def test_mixed_category(self, detector):
        # 6 up, 3 down -> up_count=6, total=9, ratio=6/9=0.667
        # consistency = |0.667 - 0.5| * 2 = 0.333
        prices = [100, 101, 102, 101, 102, 103, 102, 103, 104, 103]
        consistency, category = detector._calculate_directional_consistency(prices)
        assert 0.2 <= consistency < 0.6
        assert category == "MIXED"

    def test_uses_lookback_limit(self, detector):
        # detector.lookback_candles = 10, but provide 20 prices
        # Only last 10 should be used
        old_prices = [100 - i for i in range(10)]  # All down (not used)
        recent_prices = [200 + i for i in range(10)]  # All up
        prices = old_prices + recent_prices
        consistency, category = detector._calculate_directional_consistency(prices)
        assert consistency == 1.0
        assert category == "DIRECTIONAL"

    def test_two_prices_only(self, detector):
        # total_moves = 1
        prices = [100, 101]
        consistency, category = detector._calculate_directional_consistency(prices)
        # up_count=1, total=1, ratio=1.0, consistency=|1.0-0.5|*2=1.0
        assert consistency == 1.0
        assert category == "DIRECTIONAL"


class TestClassifyRegime:
    """Tests for _classify_regime."""

    def test_volatile_extreme_atr(self, detector):
        regime, confidence = detector._classify_regime(
            "NO_TREND", "NORMAL", "CHOPPY", 3.0
        )
        assert regime == "VOLATILE"
        assert confidence == 0.9

    def test_trending_strong_directional(self, detector):
        regime, confidence = detector._classify_regime(
            "STRONG_TREND", "NORMAL", "DIRECTIONAL", 1.0
        )
        assert regime == "TRENDING"
        assert confidence == 0.85

    def test_trending_moderate_expanded_directional(self, detector):
        regime, confidence = detector._classify_regime(
            "MODERATE_TREND", "EXPANDED", "DIRECTIONAL", 1.0
        )
        assert regime == "TRENDING"
        assert confidence == 0.85

    def test_ranging_no_trend_compressed_choppy(self, detector):
        regime, confidence = detector._classify_regime(
            "NO_TREND", "COMPRESSED", "CHOPPY", 1.0
        )
        assert regime == "RANGING"
        assert confidence == 0.8

    def test_ranging_weak_trend_normal_mixed(self, detector):
        regime, confidence = detector._classify_regime(
            "WEAK_TREND", "NORMAL", "MIXED", 1.0
        )
        assert regime == "RANGING"
        assert confidence == 0.8

    def test_volatile_expanded_no_trend(self, detector):
        regime, confidence = detector._classify_regime(
            "NO_TREND", "EXPANDED", "DIRECTIONAL", 1.5
        )
        assert regime == "VOLATILE"
        assert confidence == 0.75

    def test_volatile_expanded_weak_trend(self, detector):
        regime, confidence = detector._classify_regime(
            "WEAK_TREND", "EXPANDED", "DIRECTIONAL", 1.5
        )
        assert regime == "VOLATILE"
        assert confidence == 0.75

    def test_transitional_strong_trend_choppy(self, detector):
        regime, confidence = detector._classify_regime(
            "STRONG_TREND", "COMPRESSED", "CHOPPY", 1.0
        )
        assert regime == "TRANSITIONAL"
        assert confidence == 0.7

    def test_transitional_moderate_trend_choppy(self, detector):
        regime, confidence = detector._classify_regime(
            "MODERATE_TREND", "COMPRESSED", "CHOPPY", 1.0
        )
        assert regime == "TRANSITIONAL"
        assert confidence == 0.7

    def test_transitional_compressed_directional(self, detector):
        regime, confidence = detector._classify_regime(
            "NO_TREND", "COMPRESSED", "DIRECTIONAL", 1.0
        )
        assert regime == "TRANSITIONAL"
        assert confidence == 0.65

    def test_transitional_fallback(self, detector):
        # None of the specific rules match
        regime, confidence = detector._classify_regime(
            "MODERATE_TREND", "NORMAL", "MIXED", 1.0
        )
        assert regime == "TRANSITIONAL"
        assert confidence == 0.5

    def test_atr_extreme_overrides_everything(self, detector):
        # Even with strong trend + directional, extreme ATR -> VOLATILE
        regime, confidence = detector._classify_regime(
            "STRONG_TREND", "NORMAL", "DIRECTIONAL", 2.6
        )
        assert regime == "VOLATILE"
        assert confidence == 0.9


class TestDetect:
    """Tests for the main detect() method."""

    def test_trending_regime_full(self, detector):
        analysis = {
            "ema9": 51000,
            "ema21": 50000,  # 2% spread -> STRONG_TREND
            "bb_upper": 52000,
            "bb_lower": 48000,
            "close_prices": [100 + i for i in range(10)],  # All up -> DIRECTIONAL
            "atr_ratio": 1.0,
        }
        result = detector.detect(analysis)
        assert result["regime"] == "TRENDING"
        assert result["recommended_min_score"] == 4
        assert result["position_size_factor"] == 1.2

    def test_ranging_regime_full(self, detector):
        analysis = {
            "ema9": 50010,
            "ema21": 50000,  # 0.02% spread -> NO_TREND
            "bb_upper": 51000,
            "bb_lower": 49000,
            "close_prices": [100, 101, 100, 101, 100, 101, 100, 101, 100, 101],  # CHOPPY
            "atr_ratio": 1.0,
        }
        result = detector.detect(analysis)
        assert result["regime"] == "RANGING"
        assert result["recommended_min_score"] == 6
        assert result["position_size_factor"] == 0.8

    def test_volatile_regime_extreme_atr(self, detector):
        analysis = {
            "ema9": 50000,
            "ema21": 50000,
            "bb_upper": 52000,
            "bb_lower": 48000,
            "close_prices": [100 + i for i in range(10)],
            "atr_ratio": 3.0,
        }
        result = detector.detect(analysis)
        assert result["regime"] == "VOLATILE"
        assert result["recommended_min_score"] == 5
        assert result["position_size_factor"] == 0.6

    def test_detect_with_missing_ema(self, detector):
        analysis = {
            "close_prices": [100, 101, 102],
            "atr_ratio": 1.0,
        }
        result = detector.detect(analysis)
        # With no EMA data, trend_strength=0, NO_TREND
        assert result["trend_strength"] == 0.0
        assert "regime" in result

    def test_detect_with_empty_data(self, detector):
        analysis = {}
        result = detector.detect(analysis)
        assert "regime" in result
        assert "confidence" in result
        assert "recommended_min_score" in result
        assert "position_size_factor" in result

    def test_detect_returns_all_fields(self, detector):
        analysis = {
            "ema9": 50500,
            "ema21": 50000,
            "bb_upper": 51000,
            "bb_lower": 49000,
            "close_prices": [100, 101, 102],
            "atr_ratio": 1.0,
        }
        result = detector.detect(analysis)

        expected_keys = [
            "regime", "trend_strength", "volatility_state",
            "directional_consistency", "confidence",
            "recommended_min_score", "sl_multiplier",
            "tp_multiplier", "position_size_factor"
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_regime_params_from_config(self, detector):
        # TRENDING regime should use config params
        analysis = {
            "ema9": 51000,
            "ema21": 50000,
            "bb_upper": 52000,
            "bb_lower": 48000,
            "close_prices": [100 + i for i in range(10)],
            "atr_ratio": 1.0,
        }
        result = detector.detect(analysis)
        assert result["regime"] == "TRENDING"
        assert result["sl_multiplier"] == 1.5
        assert result["tp_multiplier"] == 3.5

    def test_unknown_regime_uses_defaults(self):
        """When regime_params doesn't have the regime, defaults are used."""
        config = {
            "lookback_candles": 10,
            "ema_spread_thresholds": {"no_trend": 0.15, "weak": 0.5, "strong": 1.5},
            "volatility_percentile_window": 100,
            "regime_params": {}  # Empty regime params
        }
        det = MarketRegimeDetector(config=config)
        analysis = {
            "ema9": 51000,
            "ema21": 50000,
            "close_prices": [100 + i for i in range(10)],
            "atr_ratio": 1.0,
        }
        result = det.detect(analysis)
        # Should still return valid result with default values
        assert result["recommended_min_score"] == 5
        assert result["position_size_factor"] == 1.0


class TestDetectorInitialization:
    """Tests for detector initialization and config handling."""

    def test_custom_config(self):
        config = {
            "lookback_candles": 20,
            "ema_spread_thresholds": {"no_trend": 0.1, "weak": 0.3, "strong": 1.0},
            "volatility_percentile_window": 50,
        }
        det = MarketRegimeDetector(config=config)
        assert det.lookback_candles == 20
        assert det.volatility_window == 50
        assert det.ema_spread_thresholds["no_trend"] == 0.1

    @patch('src.core.regime.BOT_CONFIG', {"REGIME_SETTINGS": {"lookback_candles": 15}})
    def test_config_from_bot_config(self):
        det = MarketRegimeDetector()
        assert det.lookback_candles == 15

    @patch('src.core.regime.BOT_CONFIG', {})
    def test_config_missing_regime_settings(self):
        det = MarketRegimeDetector()
        # Uses defaults
        assert det.lookback_candles == 10

    def test_default_ema_spread_thresholds(self):
        config = {}
        det = MarketRegimeDetector(config=config)
        assert det.ema_spread_thresholds["no_trend"] == 0.15
        assert det.ema_spread_thresholds["weak"] == 0.5
        assert det.ema_spread_thresholds["strong"] == 1.5


class TestGlobalFunctions:
    """Tests for module-level convenience functions."""

    @patch('src.core.regime._detector', None)
    @patch('src.core.regime.BOT_CONFIG', {"REGIME_SETTINGS": TEST_REGIME_CONFIG})
    def test_get_regime_detector_creates_singleton(self):
        from src.core.regime import get_regime_detector
        det1 = get_regime_detector()
        det2 = get_regime_detector()
        assert det1 is det2

    @patch('src.core.regime._detector', None)
    @patch('src.core.regime.BOT_CONFIG', {"REGIME_SETTINGS": TEST_REGIME_CONFIG})
    def test_detect_regime_convenience(self):
        from src.core.regime import detect_regime
        result = detect_regime({
            "ema9": 51000,
            "ema21": 50000,
            "close_prices": [100 + i for i in range(10)],
            "atr_ratio": 1.0,
        })
        assert "regime" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
