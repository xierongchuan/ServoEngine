"""
Unit tests for MACDX advanced exit system (src/core/signals/macdx.py).

Tests cover:
- Prioritized exit checks (emergency, trailing profit, MACD weakening,
  impulse candle, ATR trailing stop, max hold time, standard exits)
- Position guard (fast loop via WebSocket cache)
- Regime adaptation for exit parameters
- Exit context updates
- Timeframe utility (tf_to_minutes)
- Backward compatibility with existing should_close behavior
"""

import pytest
from unittest.mock import patch, MagicMock

from src.core.signals.macdx import MacdxSignalGenerator, position_guard_check, tf_to_minutes


# ============================================================================
# TEST SETTINGS
# ============================================================================

def _make_settings(**overrides):
    """Create MACDX settings with exit_rules."""
    settings = {
        "signal_rules": {
            "macd_cross_weight": 2,
            "rsi_zone_weight": 2,
            "ema_alignment_weight": 2,
            "not_sideways_weight": 1,
            "no_exhaustion_weight": 1,
            "volume_weight": 1,
            "min_score_for_signal": 4,
            "min_confirmations": 3,
            "enable_volume_filter": False,
        },
        "preset": {
            "timeframe": "1h",
            "loop_interval": 60,
            "position_check_interval": 15,
            "leverage": 6,
            "atr_sl_mult": 1.5,
            "atr_tp_mult": 3.0,
        },
        "exit_rules": {
            "macd_weakening": {
                "enabled": True,
                "threshold": 0.50,
                "min_candles_after_peak": 3,
                "min_candles_in_trade": 2,
                "min_profit_pct": 0.5,
                "adx_override": 30,
            },
            "trailing_profit": {
                "enabled": True,
                "activation_pnl": 2.0,
                "drawdown_levels": {
                    "high_profit": {"threshold": 5.0, "max_drawdown": 0.30},
                    "medium_profit": {"threshold": 3.0, "max_drawdown": 0.40},
                    "low_profit": {"threshold": 2.0, "max_drawdown": 0.50},
                },
            },
            "impulse_candle": {
                "enabled": True,
                "body_atr_multiplier": 2.0,
                "min_profit_pct": 1.5,
                "volume_confirm_ratio": 2.5,
                "volume_min_profit_pct": 1.0,
            },
            "trailing_stop": {
                "enabled": True,
                "atr_multiplier": 1.5,
                "activation_atr": 1.0,
            },
            "pump_guard": {
                "enabled": True,
                "reversal_pct": 1.5,
                "profit_lock_pct": 5.0,
                "max_staleness_sec": 60,
                "fallback_on_error": "skip",
            },
            "emergency": {
                "max_loss_atr": 2.0,
                "max_loss_pct": 3.0,
                "max_hold_candles": 80,
                "protect_matching_trend": True,
            },
            "regime_adaptation": {
                "enabled": True,
                "TRENDING": {"weakening_threshold": 0.40, "trailing_drawdown": 0.50, "impulse_min_profit": 2.0},
                "RANGING": {"weakening_threshold": 0.60, "trailing_drawdown": 0.35, "impulse_min_profit": 0.8},
                "VOLATILE": {"weakening_threshold": 0.55, "trailing_drawdown": 0.30, "impulse_min_profit": 1.0},
                "TRANSITIONAL": {"weakening_threshold": 0.50, "trailing_drawdown": 0.40, "impulse_min_profit": 1.5},
            },
        },
    }
    for k, v in overrides.items():
        settings[k] = v
    return settings


@pytest.fixture
def gen():
    """Create MacdxSignalGenerator with full exit_rules config."""
    return MacdxSignalGenerator(_make_settings())


def _base_analysis(**overrides):
    """Base analysis dict with sensible defaults."""
    data = {
        "current_price": 50000,
        "rsi": 50,
        "volume_ratio": 1.0,
        "ema9": 50000,
        "ema21": 50000,
        "macd_line": 0,
        "macd_signal": 0,
        "macd_hist": 5,
        "macd_hist_prev": 3,
        "bb_upper": 51000,
        "bb_lower": 49000,
        "bb_middle": 50000,
        "atr": 500,
        "atr_ratio": 1.0,
        "adx": 25,
        "close_prices": [49800, 49900, 50000],
        "open_prices": [49700, 49800, 49900],
        "rsi_values": [],
    }
    data.update(overrides)
    return data


# ============================================================================
# TF_TO_MINUTES UTILITY
# ============================================================================

class TestTfToMinutes:
    def test_known_timeframes(self):
        assert tf_to_minutes("1m") == 1
        assert tf_to_minutes("5m") == 5
        assert tf_to_minutes("15m") == 15
        assert tf_to_minutes("30m") == 30
        assert tf_to_minutes("1h") == 60
        assert tf_to_minutes("4h") == 240
        assert tf_to_minutes("1D") == 1440

    def test_unknown_defaults_to_60(self):
        assert tf_to_minutes("2h") == 60
        assert tf_to_minutes("") == 60


# ============================================================================
# BACKWARD COMPATIBILITY — standard exits still work
# ============================================================================

class TestStandardExits:
    """Existing should_close behavior must remain intact."""

    def test_no_position(self, gen):
        result = gen.should_close(_base_analysis(), None)
        assert result["should_close"] is False

    def test_invalid_prices(self, gen):
        position = {"type": "BUY", "entry": 0}
        result = gen.should_close(_base_analysis(), position)
        assert result["should_close"] is False

    def test_buy_macd_reversal_with_profit(self, gen):
        position = {"type": "BUY", "entry": 49500}
        analysis = _base_analysis(current_price=50000, macd_hist=-5, macd_hist_prev=-3)
        result = gen.should_close(analysis, position)
        assert result["should_close"] is True

    def test_buy_rsi_overbought_close(self, gen):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=51000, rsi=85, macd_hist=5)
        result = gen.should_close(analysis, position)
        assert result["should_close"] is True
        assert "RSI" in result["reason"]

    def test_sell_rsi_oversold_close(self, gen):
        position = {"type": "SELL", "entry": 50000}
        analysis = _base_analysis(current_price=49000, rsi=15, macd_hist=-5)
        result = gen.should_close(analysis, position)
        assert result["should_close"] is True

    def test_no_exit_signal(self, gen):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50100, rsi=55, macd_hist=1)
        result = gen.should_close(analysis, position)
        assert result["should_close"] is False

    def test_buy_small_loss_with_macd_reversal_no_close(self, gen):
        """BUY with MACD reversal but small loss (<1%) must NOT trigger close."""
        position = {"type": "BUY", "entry": 50200}
        analysis = _base_analysis(current_price=50000, macd_hist=-5, macd_hist_prev=-3)
        # PnL raw = -0.4%, not enough for loss close
        result = gen.should_close(analysis, position)
        assert result["should_close"] is False


# ============================================================================
# EMERGENCY EXITS
# ============================================================================

class TestEmergencyExits:
    """Priority 1: Emergency exits."""

    def test_max_loss_pct_triggers(self, gen):
        """Max loss % should trigger critical close."""
        position = {"type": "BUY", "entry": 50000}
        # With leverage 6, need price drop > 0.5% to get 3% leveraged PnL loss
        # 3% / 6 = 0.5% price move -> entry * (1 - 0.005) = 49750
        analysis = _base_analysis(current_price=49700, macd_hist=-5, macd_hist_prev=-3)
        result = gen.should_close(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "critical"

    def test_macd_instant_reversal_bearish(self, gen):
        """MACD instant reversal (hist sign change) should trigger for BUY."""
        position = {"type": "BUY", "entry": 50000}
        # macd_hist_prev > 0, now < -threshold => bearish reversal
        # Position does NOT match trend (macd_hist < 0 for BUY)
        analysis = _base_analysis(current_price=50000, macd_hist=-1.0, macd_hist_prev=0.5, atr=500)
        result = gen.should_close(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "critical"
        assert "reversal" in result["reason"].lower()

    def test_macd_instant_reversal_bullish(self, gen):
        """MACD instant reversal should trigger for SELL."""
        position = {"type": "SELL", "entry": 50000}
        analysis = _base_analysis(current_price=50000, macd_hist=1.0, macd_hist_prev=-0.5, atr=500)
        result = gen.should_close(analysis, position)
        assert result["should_close"] is True
        assert result["urgency"] == "critical"

    def test_protect_matching_trend(self, gen):
        """When position matches MACD trend, only max_loss should trigger."""
        position = {"type": "BUY", "entry": 50000}
        # BUY + hist > 0 = matching trend => skip reversal checks
        analysis = _base_analysis(current_price=50100, macd_hist=5, macd_hist_prev=3, atr=500)
        result = gen.should_close(analysis, position)
        # No emergency, no close
        assert result["should_close"] is False

    def test_max_loss_atr(self, gen):
        """Loss exceeding max_loss_atr * ATR triggers close."""
        position = {"type": "BUY", "entry": 50000}
        # ATR = 500, max_loss_atr = 2.0, so loss > 1000 price points
        # price at 48900 => loss = 1100 = 2.2 * ATR
        # But also need position NOT matching trend
        analysis = _base_analysis(current_price=48900, macd_hist=-1.0, macd_hist_prev=-0.5, atr=500)
        exit_context = {"entry_price": 50000}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert result["urgency"] == "critical"


# ============================================================================
# TRAILING PROFIT
# ============================================================================

class TestTrailingProfit:
    """Priority 2: Trailing profit protection."""

    def test_no_trigger_below_activation(self, gen):
        """Trailing profit should not activate below threshold."""
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50050, macd_hist=5)
        exit_context = {"peak_pnl": 1.5, "candles_in_trade": 5}  # Below 2.0 activation
        result = gen.should_close(analysis, position, exit_context=exit_context)
        # Should not close by trailing profit
        assert "Trailing profit" not in result.get("reason", "")

    def test_trigger_high_profit_drawdown(self, gen):
        """High profit (>5%) with >30% drawdown should close."""
        position = {"type": "BUY", "entry": 50000}
        # peak_pnl=6%, current pnl needs to be about 3% => 50% drawdown from peak
        # With leverage 6: pnl_pct = (price - entry) / entry * 100 * 6
        # For pnl=3%: price = entry * (1 + 3/600) = 50000 * 1.005 = 50250
        analysis = _base_analysis(current_price=50250, macd_hist=5)
        exit_context = {"peak_pnl": 6.0, "candles_in_trade": 10}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert "Trailing profit" in result["reason"]

    def test_no_trigger_small_drawdown(self, gen):
        """Small drawdown from peak should not trigger."""
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50400, macd_hist=5)
        # peak_pnl=5%, current ~4.8% => drawdown < 10%
        exit_context = {"peak_pnl": 5.0, "candles_in_trade": 10}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert "Trailing profit" not in result.get("reason", "")

    def test_disabled_trailing_profit(self):
        """Disabled trailing_profit should not trigger."""
        settings = _make_settings()
        settings["exit_rules"]["trailing_profit"]["enabled"] = False
        gen = MacdxSignalGenerator(settings)
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50100, macd_hist=5)
        exit_context = {"peak_pnl": 10.0, "candles_in_trade": 10}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert "Trailing profit" not in result.get("reason", "")


# ============================================================================
# MACD WEAKENING
# ============================================================================

class TestMacdWeakening:
    """Priority 3: MACD momentum weakening."""

    def test_weakening_triggers_close(self, gen):
        """Weakened MACD momentum with profit should trigger close."""
        position = {"type": "BUY", "entry": 49900}
        # Current hist is 40% of peak (below 50% threshold)
        analysis = _base_analysis(current_price=50100, macd_hist=0.4, macd_hist_prev=0.5, atr=500, adx=20)
        exit_context = {
            "macd_peak_hist": 1.0,
            "macd_peak_candle": 2,
            "candles_in_trade": 8,  # 8 - 2 = 6 candles after peak (>= 3)
            "weakening_count": 3,
            "peak_pnl": 3.0,
        }
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert "weakened" in result["reason"].lower()

    def test_no_trigger_too_early(self, gen):
        """Weakening should not trigger before min_candles_in_trade."""
        position = {"type": "BUY", "entry": 49900}
        analysis = _base_analysis(current_price=50100, macd_hist=0.3, atr=500, adx=20)
        exit_context = {
            "macd_peak_hist": 1.0,
            "macd_peak_candle": 1,
            "candles_in_trade": 1,  # Too early
            "weakening_count": 2,
        }
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result.get("should_close") is not True or "weakened" not in result.get("reason", "").lower()

    def test_adx_override(self, gen):
        """Strong ADX with profit should prevent weakening close."""
        position = {"type": "BUY", "entry": 49900}
        analysis = _base_analysis(current_price=50100, macd_hist=0.3, atr=500, adx=35)
        exit_context = {
            "macd_peak_hist": 1.0,
            "macd_peak_candle": 2,
            "candles_in_trade": 8,
            "weakening_count": 3,
            "peak_pnl": 3.0,
        }
        result = gen.should_close(analysis, position, exit_context=exit_context)
        # ADX > 30 with profit -> should NOT close on weakening
        assert "weakened" not in result.get("reason", "").lower()

    def test_macd_crossover_close(self, gen):
        """MACD signal crossover with profit should trigger close."""
        position = {"type": "BUY", "entry": 49900}
        # hist crossed from + to - (bearish crossover)
        analysis = _base_analysis(current_price=50100, macd_hist=-0.1, macd_hist_prev=0.1, atr=500, adx=20)
        exit_context = {
            "candles_in_trade": 5,
            "macd_peak_hist": 1.0,
            "macd_peak_candle": 2,
            "weakening_count": 0,
        }
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert "crossover" in result["reason"].lower()

    def test_disabled_macd_weakening(self):
        """Disabled MACD weakening should not trigger."""
        settings = _make_settings()
        settings["exit_rules"]["macd_weakening"]["enabled"] = False
        gen = MacdxSignalGenerator(settings)
        position = {"type": "BUY", "entry": 49900}
        analysis = _base_analysis(current_price=50100, macd_hist=0.3, atr=500, adx=20)
        exit_context = {
            "macd_peak_hist": 1.0, "macd_peak_candle": 2,
            "candles_in_trade": 8, "weakening_count": 3,
        }
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert "weakened" not in result.get("reason", "").lower()


# ============================================================================
# IMPULSE CANDLE EXIT
# ============================================================================

class TestImpulseExit:
    """Priority 4: Impulse candle detection."""

    def test_impulse_with_profit(self, gen):
        """Large candle body (>2x ATR) with profit should close."""
        position = {"type": "BUY", "entry": 49500}
        # Body > 2 * 500 = 1000
        # close_prices[-1]=51500, open_prices[-1]=50000 => body=1500 > 1000
        analysis = _base_analysis(
            current_price=51500, macd_hist=5, atr=500,
            close_prices=[49800, 50000, 51500],
            open_prices=[49700, 49800, 50000],
        )
        exit_context = {"candles_in_trade": 5}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert "Impulse" in result["reason"]

    def test_no_impulse_small_body(self, gen):
        """Normal candle body should not trigger impulse exit."""
        position = {"type": "BUY", "entry": 49900}
        analysis = _base_analysis(
            current_price=50100, macd_hist=5, atr=500,
            close_prices=[49900, 50000, 50100],
            open_prices=[49800, 49900, 50000],
        )
        exit_context = {"candles_in_trade": 5}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert "Impulse" not in result.get("reason", "")

    def test_impulse_but_no_profit(self, gen):
        """Impulse candle without sufficient profit should not close."""
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(
            current_price=50050, macd_hist=5, atr=500,
            close_prices=[49800, 50000, 51500],
            open_prices=[49700, 49800, 50000],
            volume_ratio=1.0,
        )
        exit_context = {"candles_in_trade": 5}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert "Impulse" not in result.get("reason", "")

    def test_volume_impulse(self, gen):
        """Impulse with high volume and moderate profit should close."""
        position = {"type": "BUY", "entry": 49700}
        analysis = _base_analysis(
            current_price=50100, macd_hist=5, atr=500,
            close_prices=[49800, 50000, 51500],
            open_prices=[49700, 49800, 50000],
            volume_ratio=3.0,
        )
        exit_context = {"candles_in_trade": 5}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True


# ============================================================================
# ATR TRAILING STOP
# ============================================================================

class TestAtrTrailingStop:
    """Priority 5: ATR trailing stop from peak price."""

    def test_trailing_stop_triggers(self, gen):
        """Price retracing below peak - ATR stop should trigger."""
        position = {"type": "BUY", "entry": 49000}
        # activation: current > entry + 1.0 * ATR = 49000 + 500 = 49500 ✓
        # stop: peak - 1.5 * ATR = 51000 - 750 = 50250
        # current 50200 < 50250 ✓
        analysis = _base_analysis(current_price=50200, macd_hist=5, atr=500)
        exit_context = {"peak_price": 51000, "candles_in_trade": 10}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert "ATR trailing" in result["reason"]

    def test_no_trigger_before_activation(self, gen):
        """ATR trailing should not activate before reaching activation threshold."""
        position = {"type": "BUY", "entry": 50000}
        # current = 50300, not > entry + 500 = 50500
        analysis = _base_analysis(current_price=50300, macd_hist=5, atr=500)
        exit_context = {"peak_price": 50300, "candles_in_trade": 10}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert "ATR trailing" not in result.get("reason", "")

    def test_sell_trailing_stop(self, gen):
        """ATR trailing stop for SELL positions."""
        position = {"type": "SELL", "entry": 51000}
        # activation: current < entry - 1.0 * ATR = 51000 - 500 = 50500 ✓
        # stop: peak + 1.5 * ATR = 49000 + 750 = 49750
        # current 49800 >= 49750 ✓
        analysis = _base_analysis(current_price=49800, macd_hist=-5, atr=500)
        exit_context = {"peak_price": 49000, "candles_in_trade": 10}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert "ATR trailing" in result["reason"]


# ============================================================================
# MAX HOLD TIME
# ============================================================================

class TestMaxHoldTime:
    """Priority 6: Maximum hold time in candles."""

    def test_max_hold_triggers(self, gen):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50100, macd_hist=5)
        # candles_in_trade=79 will be incremented to 80 by _update_exit_context
        exit_context = {"candles_in_trade": 79}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert result["should_close"] is True
        assert "Max hold" in result["reason"]

    def test_no_trigger_before_max(self, gen):
        position = {"type": "BUY", "entry": 50000}
        analysis = _base_analysis(current_price=50100, macd_hist=5)
        # candles_in_trade=78 will be incremented to 79 (< 80)
        exit_context = {"candles_in_trade": 78}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        assert "Max hold" not in result.get("reason", "")


# ============================================================================
# EXIT CONTEXT UPDATES
# ============================================================================

class TestExitContextUpdates:
    """Test _update_exit_context behavior."""

    def test_peak_price_tracked_buy(self, gen):
        ctx = {"peak_price": 50000}
        gen._update_exit_context(ctx, 50500, 49000, "BUY", 5.0, 1.0, 500)
        assert ctx["peak_price"] == 50500

    def test_peak_price_tracked_sell(self, gen):
        ctx = {"peak_price": 50000}
        gen._update_exit_context(ctx, 49500, 51000, "SELL", 5.0, -1.0, 500)
        assert ctx["peak_price"] == 49500

    def test_peak_pnl_increases(self, gen):
        ctx = {"peak_pnl": 3.0}
        gen._update_exit_context(ctx, 50500, 49000, "BUY", 5.0, 1.0, 500)
        assert ctx["peak_pnl"] == 5.0

    def test_candles_increment(self, gen):
        ctx = {"candles_in_trade": 5}
        gen._update_exit_context(ctx, 50000, 49000, "BUY", 3.0, 1.0, 500)
        assert ctx["candles_in_trade"] == 6

    def test_macd_peak_tracked(self, gen):
        ctx = {"macd_peak_hist": 0.5, "candles_in_trade": 3}
        gen._update_exit_context(ctx, 50000, 49000, "BUY", 3.0, 1.0, 500)
        assert ctx["macd_peak_hist"] == 1.0  # abs(1.0) > abs(0.5)

    def test_weakening_count(self, gen):
        ctx = {"macd_peak_hist": 1.0, "weakening_count": 2, "candles_in_trade": 5}
        gen._update_exit_context(ctx, 50000, 49000, "BUY", 3.0, 0.6, 500)
        assert ctx["weakening_count"] == 3  # abs(0.6) < abs(1.0) => increment

    def test_weakening_count_resets(self, gen):
        ctx = {"macd_peak_hist": 0.5, "weakening_count": 2, "candles_in_trade": 5}
        # New hist (1.0) > peak_hist (0.5) => becomes new peak, resets weakening
        gen._update_exit_context(ctx, 50000, 49000, "BUY", 3.0, 1.0, 500)
        assert ctx["weakening_count"] == 0


# ============================================================================
# REGIME ADAPTATION
# ============================================================================

class TestRegimeAdaptation:
    """Regime-adaptive exit parameters."""

    def test_trending_wider_trailing(self, gen):
        regime = {"regime": "TRENDING"}
        params = gen._get_regime_params(regime)
        assert params["trailing_drawdown"] == 0.50

    def test_ranging_tighter_trailing(self, gen):
        regime = {"regime": "RANGING"}
        params = gen._get_regime_params(regime)
        assert params["trailing_drawdown"] == 0.35

    def test_volatile_regime(self, gen):
        regime = {"regime": "VOLATILE"}
        params = gen._get_regime_params(regime)
        assert params["trailing_drawdown"] == 0.30

    def test_unknown_regime_defaults(self, gen):
        regime = {"regime": "UNKNOWN"}
        params = gen._get_regime_params(regime)
        # Falls back to defaults
        assert "trailing_drawdown" in params

    def test_disabled_regime_adaptation(self):
        settings = _make_settings()
        settings["exit_rules"]["regime_adaptation"]["enabled"] = False
        gen = MacdxSignalGenerator(settings)
        params = gen._get_regime_params({"regime": "TRENDING"})
        assert params["trailing_drawdown"] == 0.40  # default, not TRENDING's 0.50


# ============================================================================
# POSITION GUARD CHECK (WebSocket fast loop)
# ============================================================================

class TestPositionGuardCheck:
    """Tests for position_guard_check function."""

    def _make_ws_cache(self, symbol="BTC-USDT", close_price=50000, timestamp=None):
        """Create mock WebSocket cache."""
        import time
        if timestamp is None:
            timestamp = int(time.time() * 1000)
        return {symbol: [{"closePrice": close_price, "close": close_price, "timestamp": timestamp}]}

    def _make_preset(self, **overrides):
        d = {"leverage": 6, "timeframe": "1h"}
        d.update(overrides)
        return d

    def _make_exit_rules(self, **overrides):
        rules = _make_settings()["exit_rules"]
        rules.update(overrides)
        return rules

    def test_no_ws_cache(self):
        result = position_guard_check("BTC-USDT", {"type": "BUY", "entry": 50000}, {}, None, {}, {})
        assert result["should_close"] is False

    def test_empty_cache(self):
        result = position_guard_check("BTC-USDT", {"type": "BUY", "entry": 50000}, {},
                                      {"BTC-USDT": []}, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is False

    def test_pump_guard_disabled(self):
        exit_rules = self._make_exit_rules()
        exit_rules["pump_guard"]["enabled"] = False
        ws = self._make_ws_cache(close_price=48000)
        result = position_guard_check("BTC-USDT", {"type": "BUY", "entry": 50000}, {},
                                      ws, exit_rules, self._make_preset())
        assert result["should_close"] is False

    def test_stale_data_skips(self):
        import time
        old_ts = int((time.time() - 120) * 1000)  # 2 minutes old
        ws = self._make_ws_cache(close_price=48000, timestamp=old_ts)
        result = position_guard_check("BTC-USDT", {"type": "BUY", "entry": 50000}, {},
                                      ws, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is False
        assert "Stale" in result.get("reason", "")

    def test_reversal_triggers_close(self):
        """Large drawdown from peak should trigger pump reversal close."""
        import time
        ts = int(time.time() * 1000)
        ws = self._make_ws_cache(close_price=49000, timestamp=ts)
        exit_context = {"peak_price": 50000, "peak_pnl": 0, "last_atr": 500}
        # Drawdown: (50000 - 49000) / 50000 * 100 = 2.0% > 1.5%
        result = position_guard_check("BTC-USDT",
                                      {"type": "BUY", "entry": 49500},
                                      exit_context,
                                      ws, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is True
        assert "reversal" in result["reason"].lower()

    def test_profit_lock(self):
        """Large leveraged profit should trigger profit lock."""
        import time
        ts = int(time.time() * 1000)
        # entry=49000, current=49500, leverage=6 => pnl = (500/49000)*100*6 = 6.12%
        ws = self._make_ws_cache(close_price=49500, timestamp=ts)
        exit_context = {"peak_price": 49000, "peak_pnl": 0}
        result = position_guard_check("BTC-USDT",
                                      {"type": "BUY", "entry": 49000},
                                      exit_context,
                                      ws, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is True
        assert "Pump profit lock" in result["reason"]

    def test_max_loss_guard(self):
        """Large leveraged loss should trigger max loss guard."""
        import time
        ts = int(time.time() * 1000)
        # entry=50000, current=49700, leverage=6 => pnl = (-300/50000)*100*6 = -3.6%
        ws = self._make_ws_cache(close_price=49700, timestamp=ts)
        exit_context = {"peak_price": 50000, "peak_pnl": 0}
        result = position_guard_check("BTC-USDT",
                                      {"type": "BUY", "entry": 50000},
                                      exit_context,
                                      ws, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is True
        assert "Max loss" in result["reason"]

    def test_fast_atr_trailing_stop(self):
        """ATR trailing stop in fast loop."""
        import time
        ts = int(time.time() * 1000)
        # peak_price=51000, ATR=500, mult=1.5 => stop = 51000 - 750 = 50250
        # current=50260 => drawdown_pct = (51000-50260)/51000*100 = 1.45% < 1.5% (no pump reversal)
        # But 50260 > 50250, so ATR stop NOT hit. Use 50240 instead:
        # drawdown_pct = (51000-50240)/51000*100 = 1.49% < 1.5% (no pump reversal)
        # 50240 < 50250 => ATR stop hit ✓
        ws = self._make_ws_cache(close_price=50240, timestamp=ts)
        exit_context = {"peak_price": 51000, "peak_pnl": 3.0, "last_atr": 500}
        result = position_guard_check("BTC-USDT",
                                      {"type": "BUY", "entry": 49000},
                                      exit_context,
                                      ws, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is True
        assert "trailing" in result["reason"].lower()

    def test_no_close_normal_conditions(self):
        """Normal conditions should not trigger guard close."""
        import time
        ts = int(time.time() * 1000)
        ws = self._make_ws_cache(close_price=50100, timestamp=ts)
        exit_context = {"peak_price": 50100, "peak_pnl": 0.5, "last_atr": 500}
        result = position_guard_check("BTC-USDT",
                                      {"type": "BUY", "entry": 50000},
                                      exit_context,
                                      ws, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is False

    def test_symbol_normalization(self):
        """Guard should normalize symbol for cache lookup."""
        import time
        ts = int(time.time() * 1000)
        ws = {"BTC-USDT": [{"closePrice": 48000, "close": 48000, "timestamp": ts}]}
        exit_context = {"peak_price": 50000, "peak_pnl": 0}
        # Pass BTCUSDT (without hyphen) -> should normalize to BTC-USDT
        result = position_guard_check("BTCUSDT",
                                      {"type": "BUY", "entry": 50000},
                                      exit_context,
                                      ws, self._make_exit_rules(), self._make_preset())
        # Should have found the cache and detected reversal
        assert result["should_close"] is True

    def test_sell_position_guard(self):
        """Guard should work correctly for SELL positions."""
        import time
        ts = int(time.time() * 1000)
        # SELL entry=50000, peak_price=49000 (lower is better for shorts)
        # current=49800 => drawdown = (49800-49000)/49000*100 = 1.63% > 1.5%
        ws = self._make_ws_cache(close_price=49800, timestamp=ts)
        exit_context = {"peak_price": 49000, "peak_pnl": 5.0, "last_atr": 500}
        result = position_guard_check("BTC-USDT",
                                      {"type": "SELL", "entry": 50000},
                                      exit_context,
                                      ws, self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is True

    def test_error_handling(self):
        """Guard should gracefully handle errors."""
        # Pass broken ws_cache that raises exception
        class BrokenCache:
            def get(self, key, default=None):
                raise RuntimeError("IPC failure")

        result = position_guard_check("BTC-USDT",
                                      {"type": "BUY", "entry": 50000},
                                      {},
                                      BrokenCache(), self._make_exit_rules(), self._make_preset())
        assert result["should_close"] is False
        assert "error" in result.get("reason", "").lower()


# ============================================================================
# PRIORITY ORDER
# ============================================================================

class TestExitPriority:
    """Verify that higher-priority exits take precedence."""

    def test_emergency_beats_trailing(self, gen):
        """Emergency exit should fire even when trailing would also fire."""
        position = {"type": "BUY", "entry": 50000}
        # Max loss + trailing profit both triggered
        analysis = _base_analysis(current_price=49700, macd_hist=-5, macd_hist_prev=3, atr=500)
        exit_context = {"peak_pnl": 10.0, "candles_in_trade": 10, "entry_price": 50000}
        result = gen.should_close(analysis, position, exit_context=exit_context)
        # Emergency should fire first (critical)
        assert result["urgency"] == "critical"


# ============================================================================
# TRADE TRACKER — get_active_trade
# ============================================================================

class TestTradeTrackerGetActiveTrade:
    """Tests for TradeTracker.get_active_trade method."""

    def test_get_existing_trade(self):
        from src.core.trade_tracker import TradeTracker
        tracker = TradeTracker()
        tracker.active_trades["BTCUSDT"] = {"side": "BUY", "entry_price": 50000}
        result = tracker.get_active_trade("BTCUSDT")
        assert result is not None
        assert result["side"] == "BUY"

    def test_get_with_hyphen(self):
        from src.core.trade_tracker import TradeTracker
        tracker = TradeTracker()
        tracker.active_trades["BTCUSDT"] = {"side": "BUY", "entry_price": 50000}
        result = tracker.get_active_trade("BTC-USDT")
        assert result is not None

    def test_get_nonexistent(self):
        from src.core.trade_tracker import TradeTracker
        tracker = TradeTracker()
        result = tracker.get_active_trade("ETHUSDT")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
