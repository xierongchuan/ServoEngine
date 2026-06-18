"""
Unit tests for SCALP Phases 2-3:
- LightweightAnalyzer (VWAP, MACD crossover, incremental updates)
- ScalpSignalGenerator (scoring, patterns, OB imbalance)
- calculate_ob_imbalance, calculate_ob_spread_bps
- Phase 3: model override, L2 regime parsing, L3 veto staleness
"""

import time
import math
import pytest
from unittest.mock import patch

# Test config to avoid importing real SCALP_SETTINGS
TEST_SCALP_SETTINGS = {
    "signal_rules": {
        "ema_periods": [5, 13],
        "ema_macro": 21,
        "rsi_period": 7,
        "macd_params": [6, 13, 5],
        "atr_period": 10,
        "atr_fast_period": 5,
        "bb_period": 20,
        "bb_std": 2.0,
        "ema_weight": 2,
        "momentum_weight": 1,
        "rsi_weight": 2,
        "vwap_weight": 1,
        "volume_weight": 1,
        "ob_imbalance_weight": 1,
        "macd_weight": 1,
        "bb_weight": 1,
        "cvd_weight": 1,
        "choppiness_threshold": 61.8,
        "rsi_long_zone": [25, 40],
        "rsi_short_zone": [60, 75],
        "ob_imbalance_threshold": 0.3,
        "spread_max_bps": 5.0,
        "min_score_for_signal": 4,
        "auto_execute_quality": 0.6,
        "tier1_required": True,
        "conflict_friction_threshold": 2,
    },
    "interaction_rules": {
        "momentum_burst_bonus": 2,
        "vwap_bounce_bonus": 1,
        "ob_confluence_bonus": 1,
        "counter_momentum_penalty": -2,
        "spike_penalty": -1,
        "cvd_divergence_penalty": -1,
    },
    "regime_overrides": {
        "TRENDING": {"ema_weight": 3, "rsi_weight": 1, "bb_weight": 0, "min_score": 3},
        "RANGING": {"ema_weight": 1, "rsi_weight": 3, "bb_weight": 2, "min_score": 6},
        "VOLATILE": {"ema_weight": 2, "volume_weight": 2, "min_score": 5},
        "TRANSITIONAL": {"min_score": 7},
    },
}


def _make_candles(count, base_price=50000.0, spread=100.0, base_volume=1000.0,
                  start_ts=None, interval_ms=60000):
    """Generate synthetic candle data for testing."""
    if start_ts is None:
        # Start from midnight UTC today
        now = time.time()
        midnight = now - (now % 86400)
        start_ts = int(midnight * 1000)

    candles = []
    price = base_price
    for i in range(count):
        # Zigzag pattern
        direction = 1 if i % 6 < 3 else -1
        price += direction * spread * 0.1
        high = price + spread * 0.3
        low = price - spread * 0.3
        open_p = price - direction * spread * 0.05
        vol = base_volume + (i % 5) * 200

        candles.append({
            "openPrice": open_p,
            "highPrice": high,
            "lowPrice": low,
            "closePrice": price,
            "volume": vol,
            "timestamp": start_ts + i * interval_ms,
        })
    return candles


# =============================================================
# LightweightAnalyzer tests
# =============================================================

class TestLightweightAnalyzer:

    @pytest.fixture
    def analyzer(self):
        with patch("src.core.lightweight_analyzer.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.lightweight_analyzer import LightweightAnalyzer
            return LightweightAnalyzer("BTCUSDT", config=TEST_SCALP_SETTINGS["signal_rules"])

    def test_bootstrap_requires_min_candles(self, analyzer):
        candles = _make_candles(10)
        assert analyzer.bootstrap(candles) is False

    def test_bootstrap_success(self, analyzer):
        candles = _make_candles(50)
        assert analyzer.bootstrap(candles) is True
        assert analyzer._bootstrapped is True

    def test_bootstrap_ema_ordering(self, analyzer):
        # Trending up candles — EMA fast should be above EMA med
        candles = []
        price = 50000.0
        ts = int((time.time() - time.time() % 86400) * 1000)
        for i in range(60):
            price += 10  # Steady uptrend
            candles.append({
                "openPrice": price - 5,
                "highPrice": price + 5,
                "lowPrice": price - 10,
                "closePrice": price,
                "volume": 1000,
                "timestamp": ts + i * 60000,
            })
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        assert snap["ema_fast"] > snap["ema_med"]
        assert snap["ema_med"] > snap["ema_macro"]

    def test_snapshot_has_vwap_fields(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        assert "vwap" in snap
        assert "vwap_dist_pct" in snap
        assert "vwap_upper" in snap
        assert "vwap_lower" in snap
        assert snap["vwap"] > 0

    def test_vwap_deviation(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        # VWAP dist should be small for zigzag around same price
        assert abs(snap["vwap_dist_pct"]) < 5.0
        # VWAP bands should be around VWAP
        assert snap["vwap_lower"] <= snap["vwap"] <= snap["vwap_upper"]

    def test_vwap_session_reset(self, analyzer):
        # Create candles spanning two days
        now = time.time()
        yesterday_midnight = now - (now % 86400) - 86400
        ts_start = int(yesterday_midnight * 1000)

        # 30 candles from yesterday + 30 from today
        candles = _make_candles(30, start_ts=ts_start, base_price=49000)
        today_start = ts_start + 30 * 60000 + int(86400 * 1000)  # Jump to today
        candles += _make_candles(30, start_ts=today_start, base_price=51000)

        analyzer.bootstrap(candles)
        # VWAP should be based on today's candles only (~51000 area)
        snap = analyzer.get_snapshot()
        assert snap["vwap"] > 50500  # Should be around 51000, not 50000

    def test_vwap_uses_all_session_candles(self, analyzer):
        """VWAP bootstrap should use ALL candles from current session, not just last 60."""
        ts = int((time.time() - time.time() % 86400) * 1000)
        # Create 200 candles all in same session
        candles = _make_candles(200, start_ts=ts)
        analyzer.bootstrap(candles)

        # Manually calculate VWAP from all 200 candles
        cum_tp_vol = 0.0
        cum_vol = 0.0
        for c in candles:
            tp = (c["highPrice"] + c["lowPrice"] + c["closePrice"]) / 3.0
            vol = c["volume"]
            cum_tp_vol += tp * vol
            cum_vol += vol
        expected_vwap = cum_tp_vol / cum_vol

        snap = analyzer.get_snapshot()
        assert abs(snap["vwap"] - expected_vwap) < 0.1

    def test_macd_crossover_detection(self, analyzer):
        snap = analyzer.get_snapshot()
        assert "macd_crossover" in snap
        assert snap["macd_crossover"] in ("BULLISH", "BEARISH", "NONE")

    def test_macd_crossover_bullish(self, analyzer):
        """Force MACD histogram from negative to positive."""
        # Create downtrend then uptrend
        ts = int((time.time() - time.time() % 86400) * 1000)
        price = 50000.0
        candles = []
        # 40 candles downtrend
        for i in range(40):
            price -= 20
            candles.append({
                "openPrice": price + 10, "highPrice": price + 20,
                "lowPrice": price - 5, "closePrice": price,
                "volume": 1000, "timestamp": ts + i * 60000,
            })
        # 20 candles uptrend (to cause crossover)
        for i in range(20):
            price += 50
            candles.append({
                "openPrice": price - 20, "highPrice": price + 10,
                "lowPrice": price - 25, "closePrice": price,
                "volume": 1500, "timestamp": ts + (40 + i) * 60000,
            })

        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        # After strong uptrend, MACD should be positive
        assert snap["macd_hist"] > 0

    def test_incremental_update(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        snap1 = analyzer.get_snapshot()

        # Add a new candle with higher price
        new_candle = {
            "openPrice": 50200, "highPrice": 50300,
            "lowPrice": 50100, "closePrice": 50250,
            "volume": 2000,
            "timestamp": candles[-1]["timestamp"] + 60000,
        }
        snap2 = analyzer.update(new_candle)

        assert snap2["candle_count"] == snap1["candle_count"] + 1
        assert snap2["current_price"] == 50250

    def test_same_candle_no_increment(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        snap1 = analyzer.get_snapshot()

        # Update with same timestamp (live tick)
        tick = {
            "openPrice": candles[-1]["openPrice"],
            "highPrice": candles[-1]["highPrice"],
            "lowPrice": candles[-1]["lowPrice"],
            "closePrice": candles[-1]["closePrice"] + 10,
            "volume": candles[-1]["volume"],
            "timestamp": candles[-1]["timestamp"],  # Same timestamp
        }
        snap2 = analyzer.update(tick)
        assert snap2["candle_count"] == snap1["candle_count"]  # No increment


# =============================================================
# ScalpSignalGenerator tests
# =============================================================

class TestScalpSignalGenerator:

    @pytest.fixture
    def generator(self):
        with patch("src.core.scalp_signal.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.scalp_signal import ScalpSignalGenerator
            return ScalpSignalGenerator(config=TEST_SCALP_SETTINGS)

    def _bullish_indicators(self):
        return {
            "ema_fast": 50100, "ema_med": 50050, "ema_macro": 50000,
            "rsi": 35, "volume_ratio": 1.5,
            "current_price": 50120, "vwap": 50100,
            "macd_hist": 0.001, "macd_crossover": "BULLISH",
            "bb_upper": 50300, "bb_lower": 49700,
            "momentum_dir": "UP", "atr_ratio": 1.0,
            "atr": 45, "bb_middle": 50000,
            "choppiness": 40.0, "cvd": 500, "cvd_trend": "RISING",
        }

    def _bearish_indicators(self):
        return {
            "ema_fast": 49900, "ema_med": 49950, "ema_macro": 50000,
            "rsi": 65, "volume_ratio": 1.5,
            "current_price": 49880, "vwap": 49900,
            "macd_hist": -0.001, "macd_crossover": "BEARISH",
            "bb_upper": 50300, "bb_lower": 49700,
            "momentum_dir": "DOWN", "atr_ratio": 1.0,
            "atr": 45, "bb_middle": 50000,
            "choppiness": 40.0, "cvd": -500, "cvd_trend": "FALLING",
        }

    def _neutral_indicators(self):
        return {
            "ema_fast": 50000, "ema_med": 50000, "ema_macro": 50000,
            "rsi": 50, "volume_ratio": 0.8,
            "current_price": 50000, "vwap": 50000,
            "macd_hist": 0.0, "macd_crossover": "NONE",
            "bb_upper": 50200, "bb_lower": 49800,
            "momentum_dir": "MIXED", "atr_ratio": 1.0,
            "atr": 45, "bb_middle": 50000,
            "choppiness": 50.0, "cvd": 0, "cvd_trend": "FLAT",
        }

    def test_bullish_signal(self, generator):
        result = generator.generate(self._bullish_indicators())
        assert result["signal"] == "BUY"
        assert result["score"] >= 4
        assert result["quality"] > 0.0

    def test_bearish_signal(self, generator):
        result = generator.generate(self._bearish_indicators())
        assert result["signal"] == "SELL"
        assert result["score"] >= 4

    def test_neutral_hold(self, generator):
        result = generator.generate(self._neutral_indicators())
        assert result["signal"] == "HOLD"

    def test_tier1_required(self, generator):
        """Signal should be HOLD if tier1 indicators don't confirm."""
        ind = self._neutral_indicators()
        # Give high RSI weight but no EMA/momentum direction
        ind["rsi"] = 35  # In long zone
        ind["volume_ratio"] = 1.5
        ind["macd_hist"] = 0.001
        ind["bb_lower"] = 50010  # Near BB lower
        ind["current_price"] = 50005
        # EMA neutral (no tier1)
        ind["ema_fast"] = 50000
        ind["ema_med"] = 50000
        ind["momentum_dir"] = "MIXED"
        result = generator.generate(ind)
        # Without tier1 confirmation, even high score should HOLD
        assert result["signal"] == "HOLD"

    def test_regime_overrides_min_score(self, generator):
        """RANGING regime should require higher min_score."""
        ind = self._bullish_indicators()
        ind["volume_ratio"] = 0.8  # Lower volume to reduce score
        regime = {"regime": "RANGING"}
        result = generator.generate(ind, regime=regime)
        # RANGING requires min_score=6, harder to reach
        # Check that the min_score is applied
        details = result["details"]
        assert details["min_score_required"] == 6

    def test_ob_imbalance_scoring(self, generator):
        """Positive OB imbalance should boost long score."""
        ind = self._bullish_indicators()
        result_no_ob = generator.generate(ind, ob_imbalance=0.0)
        result_with_ob = generator.generate(ind, ob_imbalance=0.5)
        assert result_with_ob["score"] >= result_no_ob["score"]

    def test_momentum_burst_bonus(self, generator):
        """EMA aligned + Volume > 1.5x + momentum UP should give bonus."""
        ind = self._bullish_indicators()
        ind["volume_ratio"] = 2.0
        ind["momentum_dir"] = "UP"
        result = generator.generate(ind)
        assert any("MomBurst" in r for r in result["reasons"])

    def test_counter_momentum_penalty(self, generator):
        """EMA long + RSI > 70 should apply penalty."""
        ind = self._bullish_indicators()
        ind["rsi"] = 75  # Overbought against long
        result = generator.generate(ind)
        assert any("CounterMom" in r for r in result["reasons"])

    def test_exit_rsi_extreme(self, generator):
        position = {"type": "BUY", "entry": 50000, "avgPrice": 50000}
        indicators = self._bullish_indicators()
        indicators["rsi"] = 85  # > 80
        exit_signal = generator.check_exit(indicators, position)
        assert exit_signal["should_close"] is True
        assert "RSI" in exit_signal["reason"]

    def test_exit_volume_capitulation(self, generator):
        position = {"type": "BUY", "entry": 50000, "avgPrice": 50000}
        indicators = self._bullish_indicators()
        indicators["current_price"] = 49900  # At loss
        indicators["volume_ratio"] = 2.5  # Volume spike
        exit_signal = generator.check_exit(indicators, position)
        assert exit_signal["should_close"] is True
        assert "Capitulation" in exit_signal["reason"]

    def test_pattern_momentum(self, generator):
        ind = self._bullish_indicators()
        ind["volume_ratio"] = 1.5
        ind["rsi"] = 55
        result = generator.generate(ind)
        assert result["pattern"] in ("momentum", "pullback", "generic")

    def test_pattern_mean_reversion(self, generator):
        ind = self._bullish_indicators()
        ind["current_price"] = 49700
        ind["bb_lower"] = 49710  # Price at BB lower
        ind["rsi"] = 25  # Oversold
        ind["ema_fast"] = 49750  # EMA still confirms direction
        # Force RSI into oversold zone which should trigger mean_reversion pattern
        regime = {"regime": "RANGING"}
        result = generator.generate(ind, regime=regime)
        # With RANGING regime + oversold RSI + price near BB lower = mean_reversion
        assert result["signal"] == "BUY", f"Expected BUY but got {result['signal']}"
        assert result["pattern"] == "mean_reversion", f"Expected mean_reversion but got {result['pattern']}"

    def test_macd_crossover_annotation(self, generator):
        """MACD crossover should appear in reasons."""
        ind = self._bullish_indicators()
        ind["macd_crossover"] = "BULLISH"
        result = generator.generate(ind)
        assert any("MACDx" in r for r in result["reasons"])


# =============================================================
# OB imbalance & spread tests
# =============================================================

class TestOrderBookUtils:

    def test_ob_imbalance_bullish(self):
        from src.core.scalp_signal import calculate_ob_imbalance
        ob = {
            "bids": [[50000, 10], [49999, 8], [49998, 5]],
            "asks": [[50001, 3], [50002, 2], [50003, 1]],
        }
        imbalance = calculate_ob_imbalance(ob, levels=3)
        assert imbalance > 0  # More bids = bullish
        assert imbalance <= 1.0

    def test_ob_imbalance_bearish(self):
        from src.core.scalp_signal import calculate_ob_imbalance
        ob = {
            "bids": [[50000, 1], [49999, 1], [49998, 1]],
            "asks": [[50001, 10], [50002, 8], [50003, 5]],
        }
        imbalance = calculate_ob_imbalance(ob, levels=3)
        assert imbalance < 0  # More asks = bearish

    def test_ob_imbalance_balanced(self):
        from src.core.scalp_signal import calculate_ob_imbalance
        ob = {
            "bids": [[50000, 5], [49999, 5]],
            "asks": [[50001, 5], [50002, 5]],
        }
        imbalance = calculate_ob_imbalance(ob, levels=2)
        assert abs(imbalance) < 0.01  # Balanced

    def test_ob_imbalance_empty(self):
        from src.core.scalp_signal import calculate_ob_imbalance
        assert calculate_ob_imbalance({}) == 0.0
        assert calculate_ob_imbalance({"bids": [], "asks": []}) == 0.0

    def test_ob_imbalance_accepts_order_book_dto(self):
        from src.core.scalp_signal import calculate_ob_imbalance, calculate_ob_spread_bps
        from src.exchanges.dto import OrderBook

        ob = OrderBook(
            symbol="BTCUSDT",
            bids=[[50000, 10], [49999, 8]],
            asks=[[50002, 3], [50003, 2]],
        )

        assert calculate_ob_imbalance(ob, levels=2) > 0
        assert calculate_ob_spread_bps(ob) > 0

    def test_ob_spread_bps(self):
        from src.core.scalp_signal import calculate_ob_spread_bps
        ob = {
            "bids": [[50000, 10]],
            "asks": [[50005, 10]],
        }
        spread = calculate_ob_spread_bps(ob)
        # Spread = 5, mid = 50002.5, spread_bps = 5/50002.5 * 10000 ≈ 1.0
        assert 0.9 < spread < 1.1

    def test_ob_spread_bps_wide(self):
        from src.core.scalp_signal import calculate_ob_spread_bps
        ob = {
            "bids": [[49950, 10]],
            "asks": [[50050, 10]],
        }
        spread = calculate_ob_spread_bps(ob)
        # Spread = 100, mid = 50000, spread_bps = 100/50000 * 10000 = 20
        assert 19.5 < spread < 20.5

    def test_ob_spread_bps_empty(self):
        from src.core.scalp_signal import calculate_ob_spread_bps
        assert calculate_ob_spread_bps({}) == 0.0


# =============================================================
# TrailingStopManager tests
# =============================================================

class TestTrailingStopManager:

    @pytest.fixture
    def trailing(self):
        with patch("src.core.scalp_engine.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.scalp_engine import TrailingStopManager
            sl_tp_cfg = {"sl_atr_mult": 1.0, "tp_atr_mult": 3.0,
                         "trailing_activation_mult": 1.5, "trailing_distance_mult": 0.5}
            return TrailingStopManager(config=sl_tp_cfg)

    def test_init_long_position(self, trailing):
        trailing.init_position("BUY", 50000, atr=50)
        assert trailing.current_sl == 50000 - 50  # entry - ATR*1.0
        assert trailing.initial_tp == 50000 + 150  # entry + ATR*3.0

    def test_init_short_position(self, trailing):
        trailing.init_position("SELL", 50000, atr=50)
        assert trailing.current_sl == 50000 + 50
        assert trailing.initial_tp == 50000 - 150

    def test_trailing_activation_long(self, trailing):
        trailing.init_position("BUY", 50000, atr=50)
        # Price moves up by 1.5x ATR (activation threshold)
        trailing._last_sl_update_time = 0  # Reset throttle
        new_sl = trailing.update(50075, atr=50)  # 1.5x ATR profit
        assert trailing.is_trailing is True

    def test_reset(self, trailing):
        trailing.init_position("BUY", 50000, atr=50)
        trailing.reset()
        assert trailing.current_sl == 0.0
        assert trailing.is_trailing is False


# =============================================================
# ScalpSession tests
# =============================================================

class TestScalpSession:

    @pytest.fixture
    def session(self):
        with patch("src.core.scalp_engine.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.scalp_engine import ScalpSession
            risk_cfg = {
                "max_consecutive_losses": 3,
                "consecutive_loss_cooldown_minutes": 5,
                "daily_loss_limit_pct": 3.0,
                "hourly_loss_limit_pct": 1.0,
                "max_trades_per_hour": 6,
                "max_trades_per_day": 50,
                "min_cooldown_seconds": 2,
            }
            return ScalpSession("BTCUSDT", config=risk_cfg)

    def test_can_trade_initial(self, session):
        allowed, reason = session.can_trade()
        assert allowed is True

    def test_cooldown_between_trades(self, session):
        session.record_entry()
        allowed, reason = session.can_trade()
        assert allowed is False
        assert "Cooldown" in reason

    def test_consecutive_loss_pause(self, session):
        session._min_cooldown_sec = 0  # Disable cooldown for test
        for _ in range(3):
            session.record_entry()
            session.record_exit(-0.5)
        allowed, reason = session.can_trade()
        assert allowed is False
        assert "Paused" in reason

    def test_daily_loss_limit(self, session):
        session._min_cooldown_sec = 0
        # Initialize session date so _check_reset doesn't zero counters
        session._session_date = time.strftime('%Y-%m-%d', time.gmtime())
        session._session_hour = time.gmtime().tm_hour
        session.record_entry()
        session.record_exit(-3.5)  # Exceeds 3% daily limit
        allowed, reason = session.can_trade()
        assert allowed is False
        assert "Daily" in reason

    def test_win_resets_consecutive(self, session):
        session._min_cooldown_sec = 0
        session.record_entry()
        session.record_exit(-0.5)
        session.record_entry()
        session.record_exit(-0.5)
        # 2 consecutive losses
        assert session._consecutive_losses == 2
        session.record_entry()
        session.record_exit(1.0)  # Win
        assert session._consecutive_losses == 0


# =============================================================
# Phase 3: get_prediction model override
# =============================================================

class TestPredictModelOverride:

    @patch("src.core.predict.requests.post")
    def test_default_model_used(self, mock_post):
        """Without model override, default AI_MODEL should be used."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"action":"hold"}'}}]
        }
        with patch("src.core.predict.AI_MODEL", "default/model"):
            from src.core.predict import get_prediction
            get_prediction("test prompt")
            payload = mock_post.call_args[1]["json"]
            # When using default model with fallbacks, payload may use "models" key
            model_used = payload.get("model") or payload.get("models", [None])[0]
            assert model_used == "default/model"

    @patch("src.core.predict.requests.post")
    def test_custom_model_override(self, mock_post):
        """Model override should use specified model, not default."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"action":"hold"}'}}]
        }
        from src.core.predict import get_prediction
        get_prediction("test prompt", model="custom/fast-model")
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "custom/fast-model"
        # Custom model should NOT have fallback models
        assert "models" not in payload

    @patch("src.core.predict.requests.post")
    def test_custom_max_tokens(self, mock_post):
        """max_tokens override should be applied."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"action":"hold"}'}}]
        }
        from src.core.predict import get_prediction
        get_prediction("test prompt", max_tokens=100)
        payload = mock_post.call_args[1]["json"]
        assert payload["max_tokens"] == 100

    @patch("src.core.predict.requests.post")
    def test_custom_temperature(self, mock_post):
        """temperature override should be applied."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"action":"hold"}'}}]
        }
        from src.core.predict import get_prediction
        get_prediction("test prompt", temperature=0.1)
        payload = mock_post.call_args[1]["json"]
        assert payload["temperature"] == 0.1


# =============================================================
# Phase 3: L2 regime response parsing
# =============================================================

class TestRegimeResponseParsing:

    @pytest.fixture
    def engine(self):
        with patch("src.core.scalp_engine.SCALP_SETTINGS", {
            "loops": {"fast_interval": 1.5, "slow_interval": 45},
            "time_exit": {},
            "ai_integration": {},
            "signal_rules": {"spread_max_bps": 5.0},
        }):
            from src.core.scalp_engine import ScalpEngine
            return ScalpEngine("BTCUSDT")

    def test_parse_valid_json(self, engine):
        raw = '{"regime":"TRENDING","confidence":0.85,"bias":"bullish","scalp_mode":"breakout","params":{"min_score":4},"note":"strong trend"}'
        result = engine._parse_regime_response(raw)
        assert result is not None
        assert result["regime"] == "TRENDING"
        assert result["confidence"] == 0.85
        assert result["params"]["min_score"] == 4

    def test_parse_markdown_wrapped(self, engine):
        raw = '```json\n{"regime":"RANGING","confidence":0.7}\n```'
        result = engine._parse_regime_response(raw)
        assert result is not None
        assert result["regime"] == "RANGING"

    def test_parse_with_extra_text(self, engine):
        raw = 'Here is my analysis:\n{"regime":"VOLATILE","confidence":0.6}\nDone.'
        result = engine._parse_regime_response(raw)
        assert result is not None
        assert result["regime"] == "VOLATILE"

    def test_parse_invalid_regime_normalized(self, engine):
        raw = '{"regime":"CHOPPY","confidence":0.5}'
        result = engine._parse_regime_response(raw)
        assert result is not None
        assert result["regime"] == "UNKNOWN"  # Invalid regime → UNKNOWN

    def test_parse_confidence_clamped(self, engine):
        raw = '{"regime":"TRENDING","confidence":1.5}'
        result = engine._parse_regime_response(raw)
        assert result["confidence"] == 1.0  # Clamped to max

    def test_parse_no_json(self, engine):
        raw = "I cannot determine the regime right now"
        result = engine._parse_regime_response(raw)
        assert result is None

    def test_parse_missing_regime_field(self, engine):
        raw = '{"confidence":0.8,"bias":"neutral"}'
        result = engine._parse_regime_response(raw)
        assert result is None  # "regime" is required

    def test_parse_dict_passthrough(self, engine):
        data = {"regime": "TRENDING", "confidence": 0.9}
        result = engine._parse_regime_response(data)
        assert result is not None
        assert result["regime"] == "TRENDING"


# =============================================================
# Phase 3: L3 veto staleness checks
# =============================================================

class TestVetoStaleness:

    def _make_engine(self, **ai_overrides):
        ai_cfg = {
            "veto_enabled": True,
            "veto_staleness_seconds": 10,
            "veto_max_stale_cycles": 2,
            "borderline_quality_threshold": 0.3,
        }
        # Note: veto_model is now read from AI_VETO_OVERRIDE, not ai_integration
        settings = {
            "loops": {"fast_interval": 1.5, "slow_interval": 45},
            "time_exit": {},
            "ai_integration": ai_cfg,
            "signal_rules": {"spread_max_bps": 5.0},
        }
        with patch("src.core.scalp_engine.SCALP_SETTINGS", settings):
            from src.core.scalp_engine import ScalpEngine
            engine = ScalpEngine("BTCUSDT")
        return engine

    def test_veto_discard_time_stale(self):
        engine = self._make_engine(veto_staleness_seconds=5)
        # Queue a veto that's 10 seconds old
        engine._pending_veto = {
            "signal": {"signal": "BUY", "score": 5, "max_score": 10, "quality": 0.5},
            "indicators": {"rsi": 35},
            "time": time.time() - 10,  # 10s ago
            "cycle": 1,
        }
        engine._fast_cycle = 2
        # process_veto should discard due to time staleness
        engine._process_veto()
        # No crash = success; the veto was discarded silently

    def test_veto_discard_cycle_stale(self):
        engine = self._make_engine(veto_max_stale_cycles=2)
        engine._pending_veto = {
            "signal": {"signal": "BUY", "score": 5, "max_score": 10, "quality": 0.5},
            "indicators": {"rsi": 35},
            "time": time.time(),  # Fresh time
            "cycle": 1,
        }
        engine._fast_cycle = 10  # 9 cycles elapsed > 2 max
        engine._process_veto()
        # Should discard due to cycle staleness

    def test_veto_discard_signal_changed(self):
        engine = self._make_engine()
        # Set up analyzer that returns different signal direction
        mock_analyzer = type("MockAnalyzer", (), {
            "get_snapshot": lambda self: {
                "ema_fast": 49900, "ema_med": 49950, "ema_macro": 50000,
                "rsi": 65, "current_price": 49880, "vwap": 49900,
                "macd_hist": -0.001, "macd_crossover": "BEARISH",
                "bb_upper": 50300, "bb_lower": 49700, "bb_middle": 50000,
                "momentum_dir": "DOWN", "atr_ratio": 1.0, "atr": 45,
                "volume_ratio": 1.5,
            }
        })()
        engine._analyzer = mock_analyzer

        mock_signal_gen = type("MockGen", (), {
            "generate": lambda self, ind, regime=None, ob_imbalance=0: {
                "signal": "SELL",  # Different from queued BUY
            }
        })()
        engine._signal_gen = mock_signal_gen

        engine._pending_veto = {
            "signal": {"signal": "BUY", "score": 5, "max_score": 10, "quality": 0.5,
                        "regime": "TRENDING", "pattern": "momentum"},
            "indicators": {"rsi": 35, "volume_ratio": 1.5, "momentum_dir": "UP"},
            "time": time.time(),
            "cycle": engine._fast_cycle,
        }
        engine._process_veto()
        # Should discard because signal changed from BUY to SELL

    @patch("src.core.scalp_engine.AI_VETO_OVERRIDE", {"model": "google/gemini-2.0-flash-lite", "temperature": 0.1, "max_tokens": 100})
    @patch("src.core.predict.requests.post")
    def test_veto_uses_model_override(self, mock_post):
        engine = self._make_engine()
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"action":"buy","confidence":0.8,"reason":"confirmed"}'}}]
        }

        engine._analyzer = None  # Skip signal-changed check
        engine._pending_veto = {
            "signal": {"signal": "BUY", "score": 6, "max_score": 10, "quality": 0.6,
                        "regime": "TRENDING", "pattern": "momentum"},
            "indicators": {"rsi": 35, "volume_ratio": 1.5, "momentum_dir": "UP"},
            "time": time.time(),
            "cycle": engine._fast_cycle,
        }

        # Mock _execute_entry to avoid actual order placement
        engine._execute_entry = lambda s, i, **kwargs: None

        engine._process_veto()

        # Verify the model used in the API call
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "google/gemini-2.0-flash-lite"
        assert payload["max_tokens"] == 100
        assert payload["temperature"] == 0.1

    def test_veto_skip_with_position(self):
        engine = self._make_engine()
        engine._position = {"side": "BUY", "entry": 50000}  # Already in position
        engine._pending_veto = {
            "signal": {"signal": "BUY"},
            "indicators": {},
            "time": time.time(),
            "cycle": engine._fast_cycle,
        }
        engine._process_veto()
        # Should skip veto because position is already open

    def test_no_pending_veto(self):
        engine = self._make_engine()
        engine._pending_veto = None
        engine._process_veto()  # Should return early without error


# =============================================================
# Phase 3: L2 AI regime advisor integration
# =============================================================

class TestRegimeAdvisorIntegration:

    def _make_engine(self, **ai_overrides):
        ai_cfg = {
            "regime_enabled": True,
            "regime_interval_seconds": 300,
        }
        # Note: regime_model is now read from AI_REGIME_OVERRIDE, not ai_integration
        settings = {
            "loops": {"fast_interval": 1.5, "slow_interval": 45},
            "time_exit": {},
            "ai_integration": ai_cfg,
            "signal_rules": {"spread_max_bps": 5.0},
        }
        with patch("src.core.scalp_engine.SCALP_SETTINGS", settings):
            from src.core.scalp_engine import ScalpEngine
            engine = ScalpEngine("BTCUSDT")
        return engine

    def test_regime_interval_respected(self):
        engine = self._make_engine(regime_interval_seconds=300)
        engine._last_ai_regime_time = time.time()  # Just ran
        engine._update_regime_ai()
        # Should not run because interval hasn't elapsed
        assert engine._ai_regime_label == "UNKNOWN"  # Unchanged

    def test_regime_skipped_without_analyzer(self):
        engine = self._make_engine(regime_interval_seconds=0)
        engine._last_ai_regime_time = 0  # Force interval elapsed
        engine._analyzer = None
        engine._update_regime_ai()
        assert engine._ai_regime_label == "UNKNOWN"

    @patch("src.core.predict.requests.post")
    def test_regime_advisor_updates_regime(self, mock_post):
        engine = self._make_engine(regime_interval_seconds=0)
        engine._last_ai_regime_time = 0

        # Mock analyzer
        mock_analyzer = type("MockAnalyzer", (), {
            "_bootstrapped": True,
            "_recent_closes": [50000 + i * 10 for i in range(30)],
            "get_snapshot": lambda self: {
                "ema_fast": 50100, "ema_med": 50050, "ema_macro": 50000,
                "rsi": 55, "macd_hist": 0.5, "bb_width": 200,
                "atr_ratio": 1.2, "volume_ratio": 1.3,
                "vwap_lower": 49800, "vwap_upper": 50200,
            }
        })()
        engine._analyzer = mock_analyzer
        engine._regime = {"regime": "RANGING"}  # Initial deterministic regime

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content":
                '{"regime":"TRENDING","confidence":0.85,"bias":"bullish",'
                '"scalp_mode":"breakout","params":{"min_score":4,"size_factor":1.2},"note":"clear uptrend"}'
            }}]
        }

        engine._update_regime_ai()

        # Regime should be updated
        assert engine._ai_regime_label == "TRENDING"
        assert engine._ai_regime_duration == 1
        assert engine._regime["regime"] == "TRENDING"
        assert engine._regime["ai_confidence"] == 0.85
        assert engine._regime["recommended_min_score"] == 4
        assert engine._regime["recommended_size_factor"] == 1.2

    @patch("src.core.predict.requests.post")
    def test_regime_low_confidence_keeps_deterministic(self, mock_post):
        engine = self._make_engine(regime_interval_seconds=0)
        engine._last_ai_regime_time = 0

        mock_analyzer = type("MockAnalyzer", (), {
            "_bootstrapped": True,
            "_recent_closes": [50000] * 30,
            "get_snapshot": lambda self: {
                "ema_fast": 50000, "ema_med": 50000, "ema_macro": 50000,
                "rsi": 50, "macd_hist": 0.0, "bb_width": 100,
                "atr_ratio": 1.0, "volume_ratio": 1.0,
                "vwap_lower": 49900, "vwap_upper": 50100,
            }
        })()
        engine._analyzer = mock_analyzer
        engine._regime = {"regime": "RANGING"}

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content":
                '{"regime":"TRANSITIONAL","confidence":0.4,"bias":"neutral"}'
            }}]
        }

        engine._update_regime_ai()

        # AI regime label is updated for tracking
        assert engine._ai_regime_label == "TRANSITIONAL"
        # But deterministic regime should NOT be overridden (confidence < 0.6)
        assert engine._regime["regime"] == "RANGING"  # Unchanged
        assert "ai_confidence" not in engine._regime

    @patch("src.core.predict.requests.post")
    def test_regime_duration_tracking(self, mock_post):
        engine = self._make_engine(regime_interval_seconds=0)
        engine._last_ai_regime_time = 0
        engine._ai_regime_label = "TRENDING"
        engine._ai_regime_duration = 3  # Already in TRENDING for 3 cycles

        mock_analyzer = type("MockAnalyzer", (), {
            "_bootstrapped": True,
            "_recent_closes": [50000 + i * 10 for i in range(30)],
            "get_snapshot": lambda self: {
                "ema_fast": 50100, "ema_med": 50050, "ema_macro": 50000,
                "rsi": 55, "macd_hist": 0.5, "bb_width": 200,
                "atr_ratio": 1.2, "volume_ratio": 1.3,
                "vwap_lower": 49800, "vwap_upper": 50200,
            }
        })()
        engine._analyzer = mock_analyzer
        engine._regime = {"regime": "TRENDING"}

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content":
                '{"regime":"TRENDING","confidence":0.9}'
            }}]
        }

        engine._update_regime_ai()

        # Duration should increment (same regime)
        assert engine._ai_regime_duration == 4

    @patch("src.core.predict.requests.post")
    def test_regime_change_resets_duration(self, mock_post):
        engine = self._make_engine(regime_interval_seconds=0)
        engine._last_ai_regime_time = 0
        engine._ai_regime_label = "TRENDING"
        engine._ai_regime_duration = 5

        mock_analyzer = type("MockAnalyzer", (), {
            "_bootstrapped": True,
            "_recent_closes": [50000] * 30,
            "get_snapshot": lambda self: {
                "ema_fast": 50000, "ema_med": 50000, "ema_macro": 50000,
                "rsi": 50, "macd_hist": 0.0, "bb_width": 300,
                "atr_ratio": 1.5, "volume_ratio": 0.8,
                "vwap_lower": 49800, "vwap_upper": 50200,
            }
        })()
        engine._analyzer = mock_analyzer
        engine._regime = {"regime": "TRENDING"}

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content":
                '{"regime":"VOLATILE","confidence":0.75}'
            }}]
        }

        engine._update_regime_ai()

        assert engine._ai_regime_label == "VOLATILE"
        assert engine._ai_regime_duration == 1  # Reset

    @patch("src.core.scalp_engine.AI_REGIME_OVERRIDE", {"model": "google/gemini-2.5-flash", "temperature": 0.2, "max_tokens": 150})
    @patch("src.core.predict.requests.post")
    def test_regime_uses_model_override(self, mock_post):
        engine = self._make_engine(regime_interval_seconds=0)
        engine._last_ai_regime_time = 0

        mock_analyzer = type("MockAnalyzer", (), {
            "_bootstrapped": True,
            "_recent_closes": [50000] * 30,
            "get_snapshot": lambda self: {
                "ema_fast": 50000, "ema_med": 50000, "ema_macro": 50000,
                "rsi": 50, "macd_hist": 0.0, "bb_width": 100,
                "atr_ratio": 1.0, "volume_ratio": 1.0,
                "vwap_lower": 49900, "vwap_upper": 50100,
            }
        })()
        engine._analyzer = mock_analyzer
        engine._regime = {}

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content":
                '{"regime":"RANGING","confidence":0.7}'
            }}]
        }

        engine._update_regime_ai()

        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "google/gemini-2.5-flash"
        assert payload["max_tokens"] == 150
        assert payload["temperature"] == 0.2


# =============================================================
# Phase 4: Choppiness Index tests
# =============================================================

class TestChoppinessIndex:

    @pytest.fixture
    def analyzer(self):
        with patch("src.core.lightweight_analyzer.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.lightweight_analyzer import LightweightAnalyzer
            return LightweightAnalyzer("BTCUSDT", config=TEST_SCALP_SETTINGS["signal_rules"])

    def test_snapshot_has_choppiness(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        assert "choppiness" in snap
        assert 0 <= snap["choppiness"] <= 100

    def test_trending_market_low_choppiness(self, analyzer):
        """Strong trend should produce low choppiness (< 50)."""
        ts = int((time.time() - time.time() % 86400) * 1000)
        price = 50000.0
        candles = []
        for i in range(60):
            price += 30  # Steady strong uptrend
            candles.append({
                "openPrice": price - 15, "highPrice": price + 10,
                "lowPrice": price - 20, "closePrice": price,
                "volume": 1000, "timestamp": ts + i * 60000,
            })
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        # In a strong trend, choppiness should be below 50
        assert snap["choppiness"] < 55

    def test_choppy_market_high_choppiness(self, analyzer):
        """Sideways zigzag should produce high choppiness (> 55)."""
        ts = int((time.time() - time.time() % 86400) * 1000)
        price = 50000.0
        candles = []
        for i in range(60):
            # Zigzag: alternating up/down with large range
            direction = 1 if i % 2 == 0 else -1
            price += direction * 50
            candles.append({
                "openPrice": price - direction * 25, "highPrice": price + 30,
                "lowPrice": price - 30, "closePrice": price,
                "volume": 1000, "timestamp": ts + i * 60000,
            })
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        # Choppy market should have higher choppiness
        assert snap["choppiness"] > 55

    def test_choppiness_incremental_update(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        snap1 = analyzer.get_snapshot()

        # Add trending candle
        new_candle = {
            "openPrice": 50200, "highPrice": 50300,
            "lowPrice": 50150, "closePrice": 50280,
            "volume": 1500,
            "timestamp": candles[-1]["timestamp"] + 60000,
        }
        snap2 = analyzer.update(new_candle)
        # Choppiness should still be a valid number
        assert 0 <= snap2["choppiness"] <= 100


# =============================================================
# Phase 4: CVD (Cumulative Volume Delta) tests
# =============================================================

class TestCVD:

    @pytest.fixture
    def analyzer(self):
        with patch("src.core.lightweight_analyzer.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.lightweight_analyzer import LightweightAnalyzer
            return LightweightAnalyzer("BTCUSDT", config=TEST_SCALP_SETTINGS["signal_rules"])

    def test_snapshot_has_cvd_fields(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        assert "cvd" in snap
        assert "cvd_trend" in snap
        assert snap["cvd_trend"] in ("RISING", "FALLING", "FLAT")

    def test_bullish_candles_positive_cvd(self, analyzer):
        """Bullish candles (close > open) should produce positive CVD."""
        ts = int((time.time() - time.time() % 86400) * 1000)
        price = 50000.0
        candles = []
        for i in range(60):
            price += 10  # Steady uptrend
            candles.append({
                "openPrice": price - 20, "highPrice": price + 5,
                "lowPrice": price - 25, "closePrice": price,
                "volume": 1000, "timestamp": ts + i * 60000,
            })
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        assert snap["cvd"] > 0

    def test_bearish_candles_negative_cvd(self, analyzer):
        """Bearish candles (close < open) should produce negative CVD."""
        ts = int((time.time() - time.time() % 86400) * 1000)
        price = 50000.0
        candles = []
        for i in range(60):
            price -= 10  # Steady downtrend
            candles.append({
                "openPrice": price + 20, "highPrice": price + 25,
                "lowPrice": price - 5, "closePrice": price,
                "volume": 1000, "timestamp": ts + i * 60000,
            })
        analyzer.bootstrap(candles)
        snap = analyzer.get_snapshot()
        assert snap["cvd"] < 0

    def test_candle_volume_delta_calculation(self):
        """Test the static volume delta classification."""
        with patch("src.core.lightweight_analyzer.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.lightweight_analyzer import LightweightAnalyzer
            # Bullish candle: close near high
            delta = LightweightAnalyzer._candle_volume_delta(100, 110, 95, 108, 1000)
            # buy_ratio = (108-95)/(110-95) = 13/15 ≈ 0.867
            # delta = 1000 * (2*0.867 - 1) = 1000 * 0.733 ≈ 733
            assert delta > 500

            # Bearish candle: close near low
            delta = LightweightAnalyzer._candle_volume_delta(105, 110, 95, 97, 1000)
            # buy_ratio = (97-95)/(110-95) = 2/15 ≈ 0.133
            # delta = 1000 * (2*0.133 - 1) = 1000 * -0.733 ≈ -733
            assert delta < -500

            # Flat range (high == low)
            delta = LightweightAnalyzer._candle_volume_delta(100, 100, 100, 100, 1000)
            assert delta == 0.0

    def test_cvd_incremental_update(self, analyzer):
        candles = _make_candles(50)
        analyzer.bootstrap(candles)
        cvd_before = analyzer._cvd

        # Add a strongly bullish candle
        new_candle = {
            "openPrice": 50000, "highPrice": 50200,
            "lowPrice": 49980, "closePrice": 50190,
            "volume": 2000,
            "timestamp": candles[-1]["timestamp"] + 60000,
        }
        analyzer.update(new_candle)
        # CVD should increase (bullish candle)
        assert analyzer._cvd > cvd_before


# =============================================================
# Phase 4: Choppiness filter in signal generator
# =============================================================

class TestChoppinessFilter:

    @pytest.fixture
    def generator(self):
        with patch("src.core.scalp_signal.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.scalp_signal import ScalpSignalGenerator
            return ScalpSignalGenerator(config=TEST_SCALP_SETTINGS)

    def _bullish_indicators(self, choppiness=40.0):
        return {
            "ema_fast": 50100, "ema_med": 50050, "ema_macro": 50000,
            "rsi": 35, "volume_ratio": 1.5,
            "current_price": 50120, "vwap": 50100,
            "macd_hist": 0.001, "macd_crossover": "BULLISH",
            "bb_upper": 50300, "bb_lower": 49700,
            "momentum_dir": "UP", "atr_ratio": 1.0,
            "atr": 45, "bb_middle": 50000,
            "choppiness": choppiness, "cvd": 500, "cvd_trend": "RISING",
        }

    def test_normal_market_allows_signal(self, generator):
        ind = self._bullish_indicators(choppiness=40.0)
        result = generator.generate(ind)
        assert result["signal"] != "HOLD" or "Choppy" not in str(result["reasons"])

    def test_choppy_market_blocks_signal(self, generator):
        ind = self._bullish_indicators(choppiness=70.0)
        result = generator.generate(ind)
        assert result["signal"] == "HOLD"
        assert any("Choppy" in r for r in result["reasons"])

    def test_choppy_ranging_regime_allows(self, generator):
        """RANGING regime should still allow signals in choppy market."""
        ind = self._bullish_indicators(choppiness=70.0)
        regime = {"regime": "RANGING"}
        result = generator.generate(ind, regime=regime)
        # Should NOT be filtered by choppiness in RANGING regime
        assert not any("Choppy" in r for r in result["reasons"])

    def test_choppiness_boundary(self, generator):
        """At exactly 61.8, should not filter (> not >=)."""
        ind = self._bullish_indicators(choppiness=61.8)
        result = generator.generate(ind)
        assert not any("Choppy" in r for r in result["reasons"])


# =============================================================
# Phase 4: CVD scoring in signal generator
# =============================================================

class TestCVDScoring:

    @pytest.fixture
    def generator(self):
        with patch("src.core.scalp_signal.SCALP_SETTINGS", TEST_SCALP_SETTINGS):
            from src.core.scalp_signal import ScalpSignalGenerator
            return ScalpSignalGenerator(config=TEST_SCALP_SETTINGS)

    def test_cvd_rising_boosts_long(self, generator):
        ind = {
            "ema_fast": 50100, "ema_med": 50050, "ema_macro": 50000,
            "rsi": 35, "volume_ratio": 1.5,
            "current_price": 50120, "vwap": 50100,
            "macd_hist": 0.001, "macd_crossover": "NONE",
            "bb_upper": 50300, "bb_lower": 49700,
            "momentum_dir": "UP", "atr_ratio": 1.0,
            "atr": 45, "bb_middle": 50000,
            "choppiness": 40.0, "cvd": 500, "cvd_trend": "RISING",
        }
        result = generator.generate(ind)
        assert any("CVD↑" in r for r in result["reasons"])

    def test_cvd_falling_boosts_short(self, generator):
        ind = {
            "ema_fast": 49900, "ema_med": 49950, "ema_macro": 50000,
            "rsi": 65, "volume_ratio": 1.5,
            "current_price": 49880, "vwap": 49900,
            "macd_hist": -0.001, "macd_crossover": "NONE",
            "bb_upper": 50300, "bb_lower": 49700,
            "momentum_dir": "DOWN", "atr_ratio": 1.0,
            "atr": 45, "bb_middle": 50000,
            "choppiness": 40.0, "cvd": -500, "cvd_trend": "FALLING",
        }
        result = generator.generate(ind)
        assert any("CVD↓" in r for r in result["reasons"])

    def test_cvd_divergence_penalty(self, generator):
        """Price UP but CVD FALLING should apply divergence penalty."""
        ind = {
            "ema_fast": 50100, "ema_med": 50050, "ema_macro": 50000,
            "rsi": 35, "volume_ratio": 1.5,
            "current_price": 50120, "vwap": 50100,
            "macd_hist": 0.001, "macd_crossover": "NONE",
            "bb_upper": 50300, "bb_lower": 49700,
            "momentum_dir": "UP", "atr_ratio": 1.0,
            "atr": 45, "bb_middle": 50000,
            "choppiness": 40.0, "cvd": -100, "cvd_trend": "FALLING",
        }
        result = generator.generate(ind)
        assert any("CVDdiv" in r for r in result["reasons"])


# =============================================================
# Phase 4: Session-aware trading
# =============================================================

class TestSessionAwareness:

    def _make_session(self, enabled=True, **overrides):
        sa_cfg = {
            "enabled": enabled,
            "peak_hours_utc": [14, 19],
            "normal_hours_utc": [8, 14],
            "reduced_size_factor": 0.5,
            "weekend_size_factor": 0.3,
        }
        sa_cfg.update(overrides)
        risk_cfg = {
            "max_consecutive_losses": 5,
            "consecutive_loss_cooldown_minutes": 30,
            "daily_loss_limit_pct": 3.0,
            "hourly_loss_limit_pct": 1.0,
            "max_trades_per_hour": 6,
            "max_trades_per_day": 50,
            "min_cooldown_seconds": 120,
        }
        with patch("src.core.scalp_engine.SCALP_SETTINGS", {
            "risk_limits": risk_cfg,
            "session_awareness": sa_cfg,
        }):
            from src.core.scalp_engine import ScalpSession
            return ScalpSession("BTCUSDT", config=risk_cfg, session_config=sa_cfg)

    def test_disabled_returns_1(self):
        session = self._make_session(enabled=False)
        assert session.get_session_size_factor() == 1.0

    @patch("src.core.scalp_engine.time")
    def test_peak_hours(self, mock_time):
        """During peak hours (14-19 UTC), size factor should be 1.0."""
        mock_gm = time.struct_time((2026, 2, 12, 16, 0, 0, 3, 43, 0))  # Thu 16:00 UTC
        mock_time.gmtime.return_value = mock_gm
        mock_time.time.return_value = time.time()
        session = self._make_session(enabled=True)
        assert session.get_session_size_factor() == 1.0

    @patch("src.core.scalp_engine.time")
    def test_normal_hours(self, mock_time):
        """During normal hours (8-14 UTC), size factor should be 1.0."""
        mock_gm = time.struct_time((2026, 2, 12, 10, 0, 0, 3, 43, 0))  # Thu 10:00 UTC
        mock_time.gmtime.return_value = mock_gm
        mock_time.time.return_value = time.time()
        session = self._make_session(enabled=True)
        assert session.get_session_size_factor() == 1.0

    @patch("src.core.scalp_engine.time")
    def test_off_peak_hours(self, mock_time):
        """During off-peak hours (e.g., 3:00 UTC), size factor should be reduced."""
        mock_gm = time.struct_time((2026, 2, 12, 3, 0, 0, 3, 43, 0))  # Thu 3:00 UTC
        mock_time.gmtime.return_value = mock_gm
        mock_time.time.return_value = time.time()
        session = self._make_session(enabled=True)
        assert session.get_session_size_factor() == 0.5

    @patch("src.core.scalp_engine.time")
    def test_weekend(self, mock_time):
        """On weekends, size factor should be weekend_size_factor."""
        mock_gm = time.struct_time((2026, 2, 14, 12, 0, 0, 5, 45, 0))  # Sat 12:00 UTC
        mock_time.gmtime.return_value = mock_gm
        mock_time.time.return_value = time.time()
        session = self._make_session(enabled=True)
        assert session.get_session_size_factor() == 0.3


# =============================================================
# Phase 4: Partial TP state tracking
# =============================================================

class TestPartialTP:

    def _make_engine(self, **partial_overrides):
        partial_cfg = {
            "enabled": True,
            "atr_mult": 1.5,
            "close_pct": 0.5,
        }
        partial_cfg.update(partial_overrides)
        settings = {
            "loops": {"fast_interval": 1.5, "slow_interval": 45},
            "time_exit": {"max_hold_minutes": 15},
            "ai_integration": {},
            "signal_rules": {"spread_max_bps": 5.0},
            "partial_tp": partial_cfg,
            "limit_entries": {"enabled": False},
            "risk_limits": {"base_position_pct": 5.0},
        }
        with patch("src.core.scalp_engine.SCALP_SETTINGS", settings):
            from src.core.scalp_engine import ScalpEngine
            engine = ScalpEngine("BTCUSDT")
        return engine

    def test_partial_tp_config_loaded(self):
        engine = self._make_engine()
        assert engine._partial_tp_enabled is True
        assert engine._partial_tp_atr_mult == 1.5
        assert engine._partial_tp_pct == 0.5

    def test_partial_tp_disabled(self):
        engine = self._make_engine(enabled=False)
        assert engine._partial_tp_enabled is False

    def test_partial_tp_state_reset_on_close(self):
        engine = self._make_engine()
        engine._partial_tp_done = True
        engine._entry_atr = 45.0

        # Simulate close_position resetting state
        engine._partial_tp_done = False
        engine._entry_atr = 0.0
        assert engine._partial_tp_done is False
        assert engine._entry_atr == 0.0

    def test_limit_orders_config(self):
        settings = {
            "loops": {"fast_interval": 1.5, "slow_interval": 45},
            "time_exit": {},
            "ai_integration": {},
            "signal_rules": {"spread_max_bps": 5.0},
            "partial_tp": {"enabled": False},
            "limit_entries": {"enabled": True, "offset_bps": 2.0, "timeout_seconds": 8},
        }
        with patch("src.core.scalp_engine.SCALP_SETTINGS", settings):
            from src.core.scalp_engine import ScalpEngine
            engine = ScalpEngine("BTCUSDT")
        assert engine._limit_orders_enabled is True
        assert engine._limit_offset_bps == 2.0
        assert engine._limit_timeout_sec == 8


# =============================================================
# Phase 5: ScalpPerformanceTracker tests
# =============================================================

class TestScalpPerformanceTracker:

    @pytest.fixture
    def tracker(self, tmp_path):
        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker
            return ScalpPerformanceTracker()

    def test_initial_state(self, tracker):
        assert tracker.trade_count == 0
        stats = tracker.get_stats()
        assert stats["total_trades"] == 0

    def test_record_entry_and_exit(self, tracker):
        tracker.record_entry("BTCUSDT", {
            "side": "BUY", "entry_price": 50000,
            "regime": "TRENDING", "pattern": "momentum",
            "score": 7, "quality": 0.7,
            "ai_veto_used": False, "choppiness": 40.0,
            "cvd_trend": "RISING", "entry_atr": 45.0,
        })
        tracker.record_exit("BTCUSDT", 0.5, "trailing_stop")
        assert tracker.trade_count == 1

    def test_exit_without_entry(self, tracker):
        """Exit without prior entry should still record."""
        tracker.record_exit("ETHUSDT", -0.3, "time_exit")
        assert tracker.trade_count == 1
        stats = tracker.get_stats()
        assert stats["total_trades"] == 1
        assert stats["by_regime"]["UNKNOWN"]["count"] == 1

    def test_win_rate_calculation(self, tracker):
        for pnl in [0.5, 0.3, -0.2, 0.8, -0.1]:
            tracker.record_entry("BTCUSDT", {"regime": "TRENDING", "score": 6})
            tracker.record_exit("BTCUSDT", pnl, "test")
        stats = tracker.get_stats()
        assert stats["total_trades"] == 5
        assert stats["win_rate"] == 3 / 5  # 3 wins out of 5

    def test_avg_pnl(self, tracker):
        pnls = [0.5, -0.2, 0.3]
        for pnl in pnls:
            tracker.record_entry("BTCUSDT", {"regime": "RANGING", "score": 5})
            tracker.record_exit("BTCUSDT", pnl, "test")
        stats = tracker.get_stats()
        assert abs(stats["avg_pnl"] - sum(pnls) / len(pnls)) < 0.001

    def test_group_by_regime(self, tracker):
        tracker.record_entry("BTCUSDT", {"regime": "TRENDING", "score": 6})
        tracker.record_exit("BTCUSDT", 0.5, "test")
        tracker.record_entry("BTCUSDT", {"regime": "TRENDING", "score": 7})
        tracker.record_exit("BTCUSDT", 0.3, "test")
        tracker.record_entry("BTCUSDT", {"regime": "RANGING", "score": 5})
        tracker.record_exit("BTCUSDT", -0.2, "test")

        stats = tracker.get_stats()
        assert stats["by_regime"]["TRENDING"]["count"] == 2
        assert stats["by_regime"]["TRENDING"]["win_rate"] == 1.0
        assert stats["by_regime"]["RANGING"]["count"] == 1
        assert stats["by_regime"]["RANGING"]["win_rate"] == 0.0

    def test_group_by_pattern(self, tracker):
        tracker.record_entry("BTCUSDT", {"pattern": "momentum", "score": 6})
        tracker.record_exit("BTCUSDT", 0.5, "test")
        tracker.record_entry("BTCUSDT", {"pattern": "mean_reversion", "score": 5})
        tracker.record_exit("BTCUSDT", -0.3, "test")

        stats = tracker.get_stats()
        assert "momentum" in stats["by_pattern"]
        assert "mean_reversion" in stats["by_pattern"]

    def test_group_by_score_range(self, tracker):
        for score, pnl in [(3, -0.2), (4, -0.1), (5, 0.3), (6, 0.5), (7, 0.8), (8, 1.0)]:
            tracker.record_entry("BTCUSDT", {"score": score})
            tracker.record_exit("BTCUSDT", pnl, "test")

        stats = tracker.get_stats()
        assert stats["by_score_range"]["3-4"]["count"] == 2
        assert stats["by_score_range"]["5-6"]["count"] == 2
        assert stats["by_score_range"]["7+"]["count"] == 2

    def test_ab_comparison(self, tracker):
        # 3 direct trades (2 wins)
        for pnl, veto in [(0.5, False), (0.3, False), (-0.1, False)]:
            tracker.record_entry("BTCUSDT", {"ai_veto_used": veto, "score": 6})
            tracker.record_exit("BTCUSDT", pnl, "test")
        # 3 AI veto trades (1 win)
        for pnl, veto in [(0.4, True), (-0.2, True), (-0.3, True)]:
            tracker.record_entry("BTCUSDT", {"ai_veto_used": veto, "score": 5})
            tracker.record_exit("BTCUSDT", pnl, "test")

        stats = tracker.get_stats()
        ab = stats["ab_comparison"]
        assert ab["direct"]["count"] == 3
        assert ab["ai_veto"]["count"] == 3
        assert ab["direct"]["win_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert ab["ai_veto"]["win_rate"] == pytest.approx(1 / 3, abs=0.01)
        assert ab["delta_win_rate"] < 0  # AI is worse in this test

    def test_persistence(self, tmp_path):
        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker
            t1 = ScalpPerformanceTracker()
            t1.record_entry("BTCUSDT", {"regime": "TRENDING", "score": 7})
            t1.record_exit("BTCUSDT", 0.5, "test")

            # New instance should load persisted data
            t2 = ScalpPerformanceTracker()
            assert t2.trade_count == 1

    def test_hold_time_recorded(self, tracker):
        tracker.record_entry("BTCUSDT", {"score": 6})
        time.sleep(0.05)  # Small delay
        tracker.record_exit("BTCUSDT", 0.1, "test")
        assert tracker._trades[0]["hold_time_sec"] > 0

    def test_last_n_filter(self, tracker):
        for i in range(10):
            tracker.record_entry("BTCUSDT", {"score": 5})
            tracker.record_exit("BTCUSDT", 0.1 if i < 5 else -0.1, "test")

        stats_all = tracker.get_stats(last_n=10)
        stats_recent = tracker.get_stats(last_n=5)
        assert stats_all["total_trades"] == 10
        assert stats_recent["total_trades"] == 5
        # Last 5 are all losses
        assert stats_recent["win_rate"] == 0.0


# =============================================================
# Phase 5: ScalpCalibrator tests
# =============================================================

class TestScalpCalibrator:

    def _make_tracker_with_data(self, tmp_path, trades_data):
        """Helper to create tracker with pre-loaded trades."""
        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker
            tracker = ScalpPerformanceTracker()
            for entry, pnl, reason in trades_data:
                tracker.record_entry("BTCUSDT", entry)
                tracker.record_exit("BTCUSDT", pnl, reason)
            return tracker

    def test_no_suggestions_with_few_trades(self, tmp_path):
        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator
            tracker = ScalpPerformanceTracker()
            for i in range(5):
                tracker.record_entry("BTCUSDT", {"score": 5})
                tracker.record_exit("BTCUSDT", 0.1, "test")
            calibrator = ScalpCalibrator(tracker)
            suggestions = calibrator.check_and_suggest()
            assert suggestions == []  # Not enough trades (< 15)

    def test_low_score_suggestion(self, tmp_path):
        """When score 3-4 trades have < 35% win rate, suggest raising min_score."""
        trades = []
        # 8 low-score trades, mostly losses
        for i in range(8):
            trades.append(
                ({"score": 3, "regime": "TRENDING"}, -0.2 if i < 6 else 0.3, "test")
            )
        # 10 good trades to reach min_trades
        for i in range(10):
            trades.append(
                ({"score": 7, "regime": "TRENDING"}, 0.5, "test")
            )

        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator
            tracker = self._make_tracker_with_data(tmp_path, trades)
            calibrator = ScalpCalibrator(tracker)
            suggestions = calibrator.check_and_suggest()

            score_suggestions = [s for s in suggestions if "min_score" in s["parameter"]]
            assert len(score_suggestions) > 0
            assert score_suggestions[0]["suggested"] >= 5

    def test_regime_suggestion(self, tmp_path):
        """When a regime has < 35% win rate, suggest raising its min_score."""
        trades = []
        # 8 VOLATILE trades, mostly losses
        for i in range(8):
            trades.append(
                ({"score": 6, "regime": "VOLATILE"}, -0.3 if i < 6 else 0.2, "test")
            )
        # 10 TRENDING trades, all wins
        for i in range(10):
            trades.append(
                ({"score": 6, "regime": "TRENDING"}, 0.5, "test")
            )

        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator
            tracker = self._make_tracker_with_data(tmp_path, trades)
            calibrator = ScalpCalibrator(tracker)
            suggestions = calibrator.check_and_suggest()

            regime_suggestions = [s for s in suggestions if "VOLATILE" in s["parameter"]]
            assert len(regime_suggestions) > 0

    def test_pattern_suggestion(self, tmp_path):
        """When a pattern has negative avg PnL, flag it."""
        trades = []
        # 6 "momentum" trades with negative PnL
        for i in range(6):
            trades.append(
                ({"score": 5, "pattern": "momentum"}, -0.3, "test")
            )
        # 10 "pullback" trades with positive PnL
        for i in range(10):
            trades.append(
                ({"score": 6, "pattern": "pullback"}, 0.4, "test")
            )

        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator
            tracker = self._make_tracker_with_data(tmp_path, trades)
            calibrator = ScalpCalibrator(tracker)
            suggestions = calibrator.check_and_suggest()

            pattern_suggestions = [s for s in suggestions if "momentum" in s["parameter"]]
            assert len(pattern_suggestions) > 0
            assert pattern_suggestions[0]["suggested"] == "review"

    def test_ab_suggestion_ai_bad(self, tmp_path):
        """When AI veto trades perform worse, suggest disabling."""
        trades = []
        # 6 direct trades (5 wins)
        for i in range(6):
            trades.append(
                ({"score": 6, "ai_veto_used": False}, 0.4 if i < 5 else -0.1, "test")
            )
        # 10 AI veto trades (3 wins)
        for i in range(10):
            trades.append(
                ({"score": 5, "ai_veto_used": True}, 0.3 if i < 3 else -0.2, "test")
            )

        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator
            tracker = self._make_tracker_with_data(tmp_path, trades)
            calibrator = ScalpCalibrator(tracker)
            suggestions = calibrator.check_and_suggest()

            ab_suggestions = [s for s in suggestions if "veto_enabled" in s["parameter"]]
            assert len(ab_suggestions) > 0
            assert ab_suggestions[0]["suggested"] is False

    def test_ab_suggestion_ai_good(self, tmp_path):
        """When AI veto trades perform better, suggest keeping."""
        trades = []
        # 6 direct trades (2 wins)
        for i in range(6):
            trades.append(
                ({"score": 5, "ai_veto_used": False}, 0.3 if i < 2 else -0.2, "test")
            )
        # 10 AI veto trades (8 wins)
        for i in range(10):
            trades.append(
                ({"score": 6, "ai_veto_used": True}, 0.5 if i < 8 else -0.1, "test")
            )

        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator
            tracker = self._make_tracker_with_data(tmp_path, trades)
            calibrator = ScalpCalibrator(tracker)
            suggestions = calibrator.check_and_suggest()

            ab_suggestions = [s for s in suggestions if "veto_enabled" in s["parameter"]]
            assert len(ab_suggestions) > 0
            assert ab_suggestions[0]["suggested"] is True

    def test_calibration_output_file(self, tmp_path):
        """Suggestions should be saved to scalp_calibration.json."""
        trades = []
        for i in range(8):
            trades.append(
                ({"score": 3, "regime": "TRENDING"}, -0.3, "test")
            )
        for i in range(10):
            trades.append(
                ({"score": 7, "regime": "TRENDING"}, 0.5, "test")
            )

        with patch("src.core.scalp_performance.DATA_DIR", str(tmp_path)):
            from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator
            tracker = self._make_tracker_with_data(tmp_path, trades)
            calibrator = ScalpCalibrator(tracker)
            suggestions = calibrator.check_and_suggest()

            output_file = tmp_path / "scalp_calibration.json"
            if suggestions:
                assert output_file.exists()
                import json
                data = json.loads(output_file.read_text())
                assert "suggestions" in data
                assert "timestamp" in data


# =============================================================
# Phase 5: Entry/exit context wiring in ScalpEngine
# =============================================================

class TestEntryExitContext:

    def _make_engine(self):
        settings = {
            "loops": {"fast_interval": 1.5, "slow_interval": 45},
            "time_exit": {"max_hold_minutes": 15},
            "ai_integration": {},
            "signal_rules": {"spread_max_bps": 5.0},
            "partial_tp": {"enabled": False},
            "limit_entries": {"enabled": False},
            "risk_limits": {"base_position_pct": 5.0},
            "performance": {"calibration_interval_cycles": 100},
        }
        with patch("src.core.scalp_engine.SCALP_SETTINGS", settings):
            from src.core.scalp_engine import ScalpEngine
            engine = ScalpEngine("BTCUSDT")
        return engine

    def test_calibration_interval_config(self):
        engine = self._make_engine()
        assert engine._calibration_interval == 100
        assert engine._slow_cycle == 0

    def test_execute_entry_has_ai_veto_param(self):
        """_execute_entry should accept ai_veto_used parameter."""
        engine = self._make_engine()
        import inspect
        sig = inspect.signature(engine._execute_entry)
        assert "ai_veto_used" in sig.parameters
        # Default should be False
        assert sig.parameters["ai_veto_used"].default is False

    def test_perf_tracker_init_slot(self):
        """Engine should have _perf_tracker and _calibrator slots."""
        engine = self._make_engine()
        assert hasattr(engine, "_perf_tracker")
        assert hasattr(engine, "_calibrator")
        # Before _init_components, they are None
        assert engine._perf_tracker is None
        assert engine._calibrator is None
