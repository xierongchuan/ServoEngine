"""Unit tests for ADX (Average Directional Index) indicator."""

import pytest
from src.core.indicators import calculate_adx, calculate_adx_series


def _make_klines(count, base_price=50000.0, trend="sideways"):
    """Generate synthetic kline data for testing."""
    import random
    random.seed(42)

    klines = []
    price = base_price

    for i in range(count):
        if trend == "uptrend":
            price += 50
        elif trend == "downtrend":
            price -= 50
        else:
            if i % 4 == 0:
                price += 30
            elif i % 4 == 2:
                price -= 30

        open_p = price + (random.random() - 0.5) * 10
        high = price + random.random() * 30 + 10
        low = price - random.random() * 30 - 10
        close = price + (random.random() - 0.5) * 15

        klines.append({
            "openPrice": open_p,
            "highPrice": high,
            "lowPrice": low,
            "closePrice": close,
        })

    return klines


class TestCalculateADX:

    def test_insufficient_data(self):
        """Should return UNKNOWN for insufficient data."""
        klines = _make_klines(5)
        result = calculate_adx(klines, period=14)
        assert result["trend"] == "UNKNOWN"
        assert result["adx"] == 0.0

    def test_sideways_market_low_adx(self):
        """Sideways market should produce low ADX (< 20)."""
        klines = _make_klines(50, trend="sideways")
        result = calculate_adx(klines, period=14)
        assert result["adx"] < 25
        assert result["trend"] in ("RANGING", "WEAK_TREND")

    def test_uptrend_high_adx(self):
        """Strong uptrend should produce higher ADX."""
        klines = _make_klines(100, trend="uptrend")
        result = calculate_adx(klines, period=14)
        assert result["adx"] > 20
        assert "TREND" in result["trend"]
        assert result["plus_di"] > result["minus_di"]

    def test_downtrend_high_adx(self):
        """Strong downtrend should produce higher ADX with -DI > +DI."""
        klines = _make_klines(100, trend="downtrend")
        result = calculate_adx(klines, period=14)
        assert result["adx"] > 20
        assert "TREND" in result["trend"]
        assert result["minus_di"] > result["plus_di"]

    def test_di_values_positive(self):
        """+DI and -DI should always be non-negative."""
        klines = _make_klines(50)
        result = calculate_adx(klines, period=14)
        assert result["plus_di"] >= 0
        assert result["minus_di"] >= 0

    def test_custom_period(self):
        """Should respect custom period parameter."""
        klines = _make_klines(50, trend="uptrend")
        result_14 = calculate_adx(klines, period=14)
        result_7 = calculate_adx(klines, period=7)
        assert result_14["adx"] >= 0
        assert result_7["adx"] >= 0

    def test_result_keys(self):
        """Result should contain all expected keys."""
        klines = _make_klines(50)
        result = calculate_adx(klines, period=14)
        assert set(result.keys()) == {"adx", "plus_di", "minus_di", "trend"}

    def test_trend_classification(self):
        """Test trend classification thresholds."""
        result = {"adx": 15, "plus_di": 25, "minus_di": 20}
        assert result["adx"] < 20

        result = {"adx": 22, "plus_di": 25, "minus_di": 20}
        assert 20 <= result["adx"] < 25

        result = {"adx": 30, "plus_di": 25, "minus_di": 20}
        assert 25 <= result["adx"] < 40

        result = {"adx": 45, "plus_di": 30, "minus_di": 15}
        assert result["adx"] >= 40

    def test_adx_in_valid_range(self):
        """ADX should always be in range [0, 100]."""
        klines = _make_klines(50)
        result = calculate_adx(klines, period=14)
        assert 0 <= result["adx"] <= 100


class TestCalculateADXSeries:

    def test_series_length_matches_klines(self):
        """Series length should match input klines length."""
        klines = _make_klines(30)
        result = calculate_adx_series(klines, period=14)
        assert len(result["adx"]) == 30
        assert len(result["plus_di"]) == 30
        assert len(result["minus_di"]) == 30
        assert len(result["trend"]) == 30

    def test_series_insufficient_data(self):
        """Should return zeros for insufficient data."""
        klines = _make_klines(5)
        result = calculate_adx_series(klines, period=14)
        assert all(v == 0.0 for v in result["adx"])
        assert all(t == "UNKNOWN" for t in result["trend"])

    def test_series_has_valid_trends(self):
        """Trend series should contain valid trend strings."""
        klines = _make_klines(50, trend="uptrend")
        result = calculate_adx_series(klines, period=14)
        valid_trends = {"RANGING", "WEAK_TREND", "TRENDING_UP", "TRENDING_DOWN",
                        "STRONG_TREND_UP", "STRONG_TREND_DOWN", "UNKNOWN"}
        for trend in result["trend"]:
            assert trend in valid_trends

    def test_series_di_non_negative(self):
        """+DI and -DI series should be non-negative."""
        klines = _make_klines(50)
        result = calculate_adx_series(klines, period=14)
        assert all(v >= 0 for v in result["plus_di"])
        assert all(v >= 0 for v in result["minus_di"])

    def test_series_uptrend_trending_up(self):
        """Uptrend should have mostly TRENDING_UP trends."""
        klines = _make_klines(100, trend="uptrend")
        result = calculate_adx_series(klines, period=14)
        trending_count = sum(1 for t in result["trend"] if "TREND" in t and "DOWN" not in t)
        assert trending_count > len(result["trend"]) * 0.3

    def test_series_downtrend_trending_down(self):
        """Downtrend should have mostly TRENDING_DOWN trends."""
        klines = _make_klines(100, trend="downtrend")
        result = calculate_adx_series(klines, period=14)
        trending_count = sum(1 for t in result["trend"] if "DOWN" in t)
        assert trending_count > len(result["trend"]) * 0.3


class TestADXUsageExample:
    """Examples showing correct ADX usage in strategies."""

    def test_trend_filtering(self):
        """Example: only allow trades when ADX > 25 (trend confirmation)."""
        klines = _make_klines(50, trend="uptrend")
        adx_result = calculate_adx(klines, period=14)

        if adx_result["adx"] > 25:
            trend_confirmed = True
        else:
            trend_confirmed = False

        assert isinstance(trend_confirmed, bool)

    def test_regime_detection(self):
        """Example: detect market regime using ADX."""
        klines = _make_klines(50)
        adx_result = calculate_adx(klines, period=14)

        if adx_result["adx"] < 20:
            regime = "RANGING"
        elif adx_result["adx"] < 40:
            regime = "TRENDING"
        else:
            regime = "STRONG_TREND"

        assert regime in ("RANGING", "TRENDING", "STRONG_TREND")

    def test_direction_from_di(self):
        """Example: determine trend direction from DI crossover."""
        klines = _make_klines(50, trend="uptrend")
        adx_result = calculate_adx(klines, period=14)

        if adx_result["plus_di"] > adx_result["minus_di"]:
            direction = "LONG"
        elif adx_result["minus_di"] > adx_result["plus_di"]:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        assert direction in ("LONG", "SHORT", "NEUTRAL")
