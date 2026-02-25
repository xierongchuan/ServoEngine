"""
Tests for plan gap features:
- RSI divergence penalty (signal_generator.py)
- validate_risk_parameters (risk_manager.py)
- should_adjust_thresholds + save_calibration_suggestions (performance.py)
- Configurable sizing weights (risk_manager.py)
"""

import sys
import os
import json
import unittest
from unittest.mock import patch, mock_open, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# ============================================================
# A) RSI Divergence Penalty (signal_generator.py)
# ============================================================

class TestRSIDivergence(unittest.TestCase):
    """Tests for _detect_rsi_divergence and its penalty integration."""

    def test_bearish_divergence_detected(self):
        """Price making higher highs + RSI making lower highs -> bearish divergence."""
        from src.core.signal_generator import SignalGenerator

        # Construct data with two local maxima where price goes up but RSI goes down
        # Pattern: ...valley, peak1, valley, peak2(higher), valley...
        prices = [
            100, 101, 102, 105, 103, 100, 99, 100,
            101, 103, 106, 108, 106, 103, 101, 100,
            99, 98, 97, 96
        ]
        # RSI: peak1 at index 3 (RSI=70), peak2 at index 11 (RSI=60) -> lower RSI high
        rsi_values = [
            50, 55, 60, 70, 65, 50, 45, 50,
            55, 58, 62, 60, 55, 50, 45, 42,
            40, 38, 36, 35
        ]

        bearish, bullish = SignalGenerator._detect_rsi_divergence(prices, rsi_values)
        self.assertTrue(bearish, "Should detect bearish divergence (higher price high, lower RSI high)")

    def test_bullish_divergence_detected(self):
        """Price making lower lows + RSI making higher lows -> bullish divergence."""
        from src.core.signal_generator import SignalGenerator

        # Pattern: ...peak, valley1, peak, valley2(lower), peak...
        prices = [
            100, 99, 98, 95, 97, 100, 101, 100,
            99, 97, 94, 92, 94, 97, 99, 100,
            101, 102, 103, 104
        ]
        # RSI: valley1 at index 3 (RSI=30), valley2 at index 11 (RSI=35) -> higher RSI low
        rsi_values = [
            50, 45, 40, 30, 35, 50, 55, 50,
            45, 40, 38, 35, 40, 50, 55, 58,
            60, 62, 64, 65
        ]

        bearish, bullish = SignalGenerator._detect_rsi_divergence(prices, rsi_values)
        self.assertTrue(bullish, "Should detect bullish divergence (lower price low, higher RSI low)")

    def test_no_divergence(self):
        """Normal aligned data -> no divergence."""
        from src.core.signal_generator import SignalGenerator

        # Prices and RSI move in the same direction
        prices = [
            100, 102, 104, 106, 104, 102, 100, 102,
            104, 106, 108, 110, 108, 106, 104, 102,
            104, 106, 108, 110
        ]
        # RSI also goes higher at each peak
        rsi_values = [
            50, 55, 60, 65, 60, 55, 50, 55,
            60, 65, 70, 75, 70, 65, 60, 55,
            60, 65, 70, 75
        ]

        bearish, bullish = SignalGenerator._detect_rsi_divergence(prices, rsi_values)
        self.assertFalse(bearish, "No bearish divergence when price and RSI highs align")
        self.assertFalse(bullish, "No bullish divergence when price and RSI lows align")

    def test_divergence_too_few_data_points(self):
        """Fewer than 5 data points -> no divergence detected."""
        from src.core.signal_generator import SignalGenerator

        bearish, bullish = SignalGenerator._detect_rsi_divergence([1, 2, 3], [50, 60, 70])
        self.assertFalse(bearish)
        self.assertFalse(bullish)

    @patch('src.core.signal_generator.BOT_CONFIG', {
        "HYBRID_SETTINGS": {
            "signal_rules": {
                "ema_cross_weight": 2,
                "rsi_zone_weight": 2,
                "volume_weight": 1,
                "sr_weight": 2,
                "momentum_weight": 1,
                "macd_weight": 1,
                "bb_weight": 1,
                "min_volume_ratio": 0.5,
                "rsi_long_max": 43,
                "rsi_long_min": 20,
                "rsi_short_max": 80,
                "rsi_short_min": 57,
                "sr_proximity_pct": 2.0,
                "min_atr_ratio": 0.5,
                "tier1_required": True,
                "conflict_friction_threshold": 3,
                "min_score_for_signal": 5,
            },
            "interaction_rules": {
                "ema_macd_confluence_bonus": 1,
                "reversal_confluence_bonus": 2,
                "momentum_burst_bonus": 1,
                "rsi_divergence_penalty": -2,
            }
        }
    })
    def test_divergence_penalty_applied_to_signal(self):
        """Full generate_signal with bearish divergence data -> BUY long_score reduced."""
        from src.core.signal_generator import SignalGenerator

        # Build prices with bearish divergence (higher price highs, lower RSI highs)
        close_prices = [
            100, 101, 102, 105, 103, 100, 99, 100,
            101, 103, 106, 108, 106, 103, 101, 100,
            99, 98, 97, 96
        ]
        rsi_values = [
            50, 55, 60, 70, 65, 50, 45, 50,
            55, 58, 62, 60, 55, 50, 45, 42,
            40, 38, 36, 35
        ]

        # Create analysis with enough data for BUY signal + divergence data
        analysis = {
            "global_trend": "UP",
            "local_trend": "BULLISH",
            "rsi": 35,  # In long RSI zone (20-43)
            "volume_ratio": 1.0,
            "current_price": 96,
            "support": 94,  # Within 2% proximity
            "resistance": 110,
            "ema9": 100,
            "ema21": 98,  # EMA9 > EMA21 -> bullish
            "atr_ratio": 1.0,
            "macd_line": 1.0,
            "macd_signal": 0.5,
            "macd_hist": 0.5,  # MACD bullish
            "bb_upper": 110,
            "bb_lower": 90,
            "last_5_direction": "UP",
            "close_prices": close_prices,
            "rsi_values": rsi_values,
        }

        gen = SignalGenerator()
        result = gen.generate_signal(analysis)

        # Verify bearish divergence penalty was applied to long interactions
        long_int = result["details"]["interactions"]["long"]
        has_penalty = any("divergence" in r.lower() for r in long_int)
        self.assertTrue(has_penalty, f"Bearish divergence penalty should be in long interactions: {long_int}")

    @patch('src.core.signal_generator.BOT_CONFIG', {
        "HYBRID_SETTINGS": {
            "signal_rules": {},
            "interaction_rules": {
                "rsi_divergence_penalty": -3,
            }
        }
    })
    def test_divergence_penalty_config_value(self):
        """Penalty value reads from config."""
        from src.core.signal_generator import SignalGenerator

        gen = SignalGenerator()
        penalty = gen.interactions.get("rsi_divergence_penalty", -2)
        self.assertEqual(penalty, -3, "Penalty should read from config (-3)")


# ============================================================
# B) validate_risk_parameters (risk_manager.py)
# ============================================================

class TestValidateRiskParameters(unittest.TestCase):
    """Tests for validate_risk_parameters function."""

    @patch('src.core.risk_manager.BOT_CONFIG', {"MIN_RISK_REWARD_RATIO": 1.2})
    def test_valid_params_pass(self):
        """R/R=1.5, risk=2%, reward=3% -> True (fee-adjusted R/R still above 1.2)."""
        from src.core.risk_manager import validate_risk_parameters

        result = validate_risk_parameters({
            "risk_reward": 1.5,
            "risk_pct": 2.0,
            "reward_pct": 3.0,
            "stop_loss": 100.0,
            "take_profit": 110.0,
        })
        self.assertTrue(result)

    @patch('src.core.risk_manager.BOT_CONFIG', {"MIN_RISK_REWARD_RATIO": 1.2})
    def test_low_rr_fails(self):
        """R/R=0.8 -> False."""
        from src.core.risk_manager import validate_risk_parameters

        result = validate_risk_parameters({
            "risk_reward": 0.8,
            "risk_pct": 2.0,
            "reward_pct": 1.6,
            "stop_loss": 100.0,
            "take_profit": 110.0,
        })
        self.assertFalse(result)

    @patch('src.core.risk_manager.BOT_CONFIG', {"MIN_RISK_REWARD_RATIO": 1.2})
    def test_high_risk_fails(self):
        """risk=12% -> False."""
        from src.core.risk_manager import validate_risk_parameters

        result = validate_risk_parameters({
            "risk_reward": 2.0,
            "risk_pct": 12.0,
            "reward_pct": 24.0,
            "stop_loss": 100.0,
            "take_profit": 120.0,
        })
        self.assertFalse(result)

    @patch('src.core.risk_manager.BOT_CONFIG', {"MIN_RISK_REWARD_RATIO": 1.2})
    def test_zero_sl_fails(self):
        """SL=0 -> False."""
        from src.core.risk_manager import validate_risk_parameters

        result = validate_risk_parameters({
            "risk_reward": 2.0,
            "risk_pct": 2.0,
            "reward_pct": 4.0,
            "stop_loss": 0,
            "take_profit": 110.0,
        })
        self.assertFalse(result)

    @patch('src.core.risk_manager.BOT_CONFIG', {"MIN_RISK_REWARD_RATIO": 1.2})
    def test_zero_tp_fails(self):
        """TP=0 -> False."""
        from src.core.risk_manager import validate_risk_parameters

        result = validate_risk_parameters({
            "risk_reward": 2.0,
            "risk_pct": 2.0,
            "stop_loss": 100.0,
            "take_profit": 0,
        })
        self.assertFalse(result)

    @patch('src.core.risk_manager.BOT_CONFIG', {"MIN_RISK_REWARD_RATIO": 1.2})
    def test_custom_min_rr(self):
        """custom min_rr_ratio=2.0, R/R=1.5 -> False."""
        from src.core.risk_manager import validate_risk_parameters

        result = validate_risk_parameters(
            {
                "risk_reward": 1.5,
                "risk_pct": 2.0,
                "stop_loss": 100.0,
                "take_profit": 110.0,
            },
            min_rr_ratio=2.0
        )
        self.assertFalse(result)


# ============================================================
# C) should_adjust_thresholds + save_calibration_suggestions (performance.py)
# ============================================================

class TestPerformanceTracker(unittest.TestCase):
    """Tests for should_adjust_thresholds and save_calibration_suggestions."""

    @patch('src.core.performance.BOT_CONFIG', {
        "PERFORMANCE_TRACKING": {"enabled": True, "min_trades_for_analysis": 10},
        "HYBRID_SETTINGS": {"signal_rules": {"min_score_for_signal": 5}},
        "REGIME_SETTINGS": {"regime_params": {}},
    })
    @patch('src.core.performance.DATA_DIR', '/tmp/test_data')
    @patch('os.path.exists', return_value=False)
    def test_no_suggestions_insufficient_trades(self, mock_exists):
        """Empty/few trades -> empty suggestions list."""
        from src.core.performance import PerformanceTracker

        tracker = PerformanceTracker()
        # history is empty (file doesn't exist)
        suggestions = tracker.should_adjust_thresholds()
        self.assertEqual(suggestions, [])

    @patch('src.core.performance.BOT_CONFIG', {
        "PERFORMANCE_TRACKING": {"enabled": True, "min_trades_for_analysis": 10, "win_rate_floor": 0.30},
        "HYBRID_SETTINGS": {"signal_rules": {"min_score_for_signal": 5, "min_volume_ratio": 0.5}},
        "REGIME_SETTINGS": {"regime_params": {}},
    })
    @patch('src.core.performance.DATA_DIR', '/tmp/test_data')
    def test_low_score_win_rate_suggestion(self):
        """Low win-rate at score 4-5 -> suggests raising min_score."""
        from src.core.performance import PerformanceTracker

        # Build history: 15 trades at score 4-5, mostly losses (win_rate < 30%)
        trades = []
        for i in range(15):
            trades.append({
                "symbol": "BTC-USDT",
                "entry_score": 4 if i % 2 == 0 else 5,
                "last_pnl": -10.0 if i < 12 else 5.0,  # 3 wins out of 15 = 20%
            })

        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=json.dumps(trades))):
            tracker = PerformanceTracker()

        suggestions = tracker.should_adjust_thresholds()

        # Should suggest raising min_score_for_signal
        min_score_suggestions = [s for s in suggestions if s["parameter"] == "min_score_for_signal"]
        self.assertTrue(len(min_score_suggestions) > 0, "Should suggest raising min_score_for_signal")
        self.assertEqual(min_score_suggestions[0]["suggested"], 6)  # current 5 + 1
        self.assertEqual(min_score_suggestions[0]["current"], 5)

    @patch('src.core.performance.BOT_CONFIG', {
        "PERFORMANCE_TRACKING": {"enabled": True},
        "HYBRID_SETTINGS": {"signal_rules": {"min_score_for_signal": 5}},
        "REGIME_SETTINGS": {"regime_params": {}},
    })
    @patch('src.core.performance.DATA_DIR', '/tmp/test_perf_save')
    @patch('os.path.exists', return_value=False)
    def test_save_calibration_writes_json(self, mock_exists):
        """save_calibration_suggestions writes valid JSON with timestamp + suggestions."""
        from src.core.performance import PerformanceTracker

        tracker = PerformanceTracker()

        suggestions = [
            {
                "parameter": "min_score_for_signal",
                "current": 5,
                "suggested": 6,
                "reason": "Low win rate",
                "confidence": 0.7,
                "auto_apply": False,
            }
        ]

        m = mock_open()
        with patch('builtins.open', m):
            tracker.save_calibration_suggestions(suggestions)

        # Verify file was opened for writing
        m.assert_called_once_with(
            os.path.join('/tmp/test_perf_save', 'calibration_suggestions.json'),
            'w', encoding='utf-8'
        )

        # Collect all written data
        written_data = ''.join(
            call.args[0] for call in m().write.call_args_list
        )
        parsed = json.loads(written_data)

        self.assertIn("timestamp", parsed)
        self.assertIn("suggestions", parsed)
        self.assertEqual(len(parsed["suggestions"]), 1)
        self.assertEqual(parsed["suggestions"][0]["parameter"], "min_score_for_signal")


# ============================================================
# D) Configurable sizing weights (risk_manager.py)
# ============================================================

class TestConfigurableSizingWeights(unittest.TestCase):
    """Tests for calculate_position_size with configurable DYNAMIC_SIZING weights."""

    @patch('src.core.risk_manager.BOT_CONFIG', {
        "DYNAMIC_SIZING": {
            "enabled": True,
            "min_size_pct": 3.0,
            "max_size_pct": 20.0,
            "quality_base": 0.3,
            "quality_weight": 0.5,
            "min_trades_for_streak": 5,
            "cold_streak_threshold": 0.3,
            "hot_streak_threshold": 0.6,
            "cold_streak_factor": 0.5,
            "hot_streak_factor": 1.1,
        }
    })
    def test_quality_factor_uses_config(self):
        """quality_factor uses config quality_base=0.3, quality_weight=0.5."""
        from src.core.risk_manager import calculate_position_size

        base_pct = 10.0
        quality = 1.0  # maximum quality
        regime = {"position_size_factor": 1.0}

        result = calculate_position_size(base_pct, quality, regime)
        # quality_factor = 0.3 + (1.0 * 0.5) = 0.8
        # adjusted = 10 * 1.0 * 0.8 * 1.0 = 8.0
        expected = 8.0
        self.assertAlmostEqual(result, expected, places=2)

    @patch('src.core.risk_manager.BOT_CONFIG', {
        "DYNAMIC_SIZING": {
            "enabled": True,
            "min_size_pct": 3.0,
            "max_size_pct": 20.0,
            "quality_base": 0.5,
            "quality_weight": 0.7,
            "min_trades_for_streak": 5,
            "cold_streak_threshold": 0.35,
            "hot_streak_threshold": 0.6,
            "cold_streak_factor": 0.4,
            "hot_streak_factor": 1.1,
        }
    })
    def test_cold_streak_from_config(self):
        """Win rate below custom cold_streak_threshold=0.35 -> uses cold_streak_factor=0.4."""
        from src.core.risk_manager import calculate_position_size

        base_pct = 10.0
        quality = 0.5
        regime = {"position_size_factor": 1.0}
        performance = {"win_rate": 0.2, "total_trades": 10}

        result = calculate_position_size(base_pct, quality, regime, performance)
        # quality_factor = 0.5 + (0.5 * 0.7) = 0.85
        # perf_factor = 0.4 (cold streak)
        # adjusted = 10.0 * 1.0 * 0.85 * 0.4 = 3.4
        expected = 3.4
        self.assertAlmostEqual(result, expected, places=2)

    @patch('src.core.risk_manager.BOT_CONFIG', {
        "DYNAMIC_SIZING": {
            "enabled": True,
            "min_size_pct": 3.0,
            "max_size_pct": 20.0,
            "quality_base": 0.5,
            "quality_weight": 0.7,
            "min_trades_for_streak": 5,
            "cold_streak_threshold": 0.3,
            "hot_streak_threshold": 0.55,
            "cold_streak_factor": 0.5,
            "hot_streak_factor": 1.3,
        }
    })
    def test_hot_streak_from_config(self):
        """Win rate above custom hot_streak_threshold=0.55 -> uses hot_streak_factor=1.3."""
        from src.core.risk_manager import calculate_position_size

        base_pct = 10.0
        quality = 0.5
        regime = {"position_size_factor": 1.0}
        performance = {"win_rate": 0.7, "total_trades": 10}

        result = calculate_position_size(base_pct, quality, regime, performance)
        # quality_factor = 0.5 + (0.5 * 0.7) = 0.85
        # perf_factor = 1.3 (hot streak)
        # adjusted = 10.0 * 1.0 * 0.85 * 1.3 = 11.05
        expected = 11.05
        self.assertAlmostEqual(result, expected, places=2)

    @patch('src.core.risk_manager.BOT_CONFIG', {
        "DYNAMIC_SIZING": {}
    })
    def test_backward_compatible_defaults(self):
        """Empty DYNAMIC_SIZING -> uses hardcoded defaults, doesn't crash."""
        from src.core.risk_manager import calculate_position_size

        base_pct = 10.0
        quality = 0.5
        regime = {"position_size_factor": 1.0}

        # Should not raise
        result = calculate_position_size(base_pct, quality, regime)

        # Defaults: quality_base=0.5, quality_weight=0.7
        # quality_factor = 0.5 + (0.5 * 0.7) = 0.85
        # adjusted = 10.0 * 1.0 * 0.85 * 1.0 = 8.5
        expected = 8.5
        self.assertAlmostEqual(result, expected, places=2)

    @patch('src.core.risk_manager.BOT_CONFIG', {
        "DYNAMIC_SIZING": {
            "enabled": False,
        }
    })
    def test_disabled_returns_base(self):
        """When DYNAMIC_SIZING.enabled is False, return base_pct unchanged."""
        from src.core.risk_manager import calculate_position_size

        result = calculate_position_size(10.0, 0.8, {"position_size_factor": 1.0})
        self.assertEqual(result, 10.0)


if __name__ == "__main__":
    unittest.main()
