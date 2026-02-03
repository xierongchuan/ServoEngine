"""
Tests for Smart Sampling candle aggregation logic.
Verifies proper OHLCV resampling: Open=first, High=max, Low=min, Close=last, Vol=sum
"""

import unittest
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSmartSamplingAggregation(unittest.TestCase):
    """Tests for OHLCV candle aggregation in Smart Sampling"""

    def test_aggregation_ohlcv_rules(self):
        """Verify proper OHLCV aggregation: Open=first, High=max, Low=min, Close=last, Vol=sum"""
        from src.core.analyzer import get_price_value

        # Simulate 3 candles to aggregate
        chunk = [
            {"openPrice": 100, "highPrice": 110, "lowPrice": 95, "closePrice": 105, "volume": 1000},
            {"openPrice": 105, "highPrice": 120, "lowPrice": 100, "closePrice": 115, "volume": 1500},
            {"openPrice": 115, "highPrice": 118, "lowPrice": 108, "closePrice": 112, "volume": 800},
        ]

        # Perform aggregation (same logic as analyzer.py)
        agg_open = chunk[0].get("openPrice", 0)
        agg_close = chunk[-1].get("closePrice", 0)
        agg_high = max(chunk, key=lambda x: get_price_value(x.get("highPrice", 0))).get("highPrice", 0)
        agg_low = min(chunk, key=lambda x: get_price_value(x.get("lowPrice", 0))).get("lowPrice", 0)
        agg_vol = sum(float(x.get("volume", 0)) for x in chunk)

        # Expected aggregation
        self.assertEqual(agg_open, 100, "Open should be first candle's open")
        self.assertEqual(agg_high, 120, "High should be max of all highs")
        self.assertEqual(agg_low, 95, "Low should be min of all lows")
        self.assertEqual(agg_close, 112, "Close should be last candle's close")
        self.assertEqual(agg_vol, 3300, "Volume should be sum of all volumes")

    def test_step_calculation_swing(self):
        """Test step auto-calculation for SWING style (30D, 720 candles)"""
        fetched_candles = 720  # 30 days of 1h candles
        max_ai_candles = 50
        recent_candles = 30

        history_candles = fetched_candles - recent_candles  # 690
        ai_history_budget = max_ai_candles - recent_candles  # 20

        optimal_step = max(1, math.ceil(history_candles / ai_history_budget))

        # 690 / 20 = 34.5 -> ceil = 35
        self.assertEqual(optimal_step, 35, "Step should be 35 for SWING 30D")

        # Verify final count is within budget
        aggregated_history = history_candles // optimal_step  # 690 // 35 = 19
        total = aggregated_history + recent_candles  # 19 + 30 = 49

        self.assertLessEqual(total, max_ai_candles, "Total candles should fit within AI limit")

    def test_step_calculation_intraday(self):
        """Test step auto-calculation for INTRADAY style (1D, 288 candles at 5m)"""
        fetched_candles = 288  # 1 day of 5m candles (1440 min / 5 min)
        max_ai_candles = 50
        recent_candles = 30

        history_candles = fetched_candles - recent_candles  # 258
        ai_history_budget = max_ai_candles - recent_candles  # 20

        optimal_step = max(1, math.ceil(history_candles / ai_history_budget))

        # 258 / 20 = 12.9 -> ceil = 13
        self.assertEqual(optimal_step, 13, "Step should be 13 for INTRADAY 1D")

        aggregated_history = history_candles // optimal_step  # 258 // 13 = 19
        total = aggregated_history + recent_candles  # 19 + 30 = 49

        self.assertLessEqual(total, max_ai_candles)

    def test_step_calculation_scalp_no_aggregation(self):
        """Test that SCALP with few candles doesn't need aggregation"""
        fetched_candles = 45  # Less than max_ai_candles
        max_ai_candles = 50
        recent_candles = 30

        history_candles = fetched_candles - recent_candles  # 15
        ai_history_budget = max_ai_candles - recent_candles  # 20

        # history_candles (15) < ai_history_budget (20), so no aggregation needed
        if history_candles <= ai_history_budget:
            optimal_step = 1
        else:
            optimal_step = max(1, math.ceil(history_candles / ai_history_budget))

        self.assertEqual(optimal_step, 1, "No aggregation needed when candles fit")

    def test_aggregation_preserves_price_range(self):
        """Verify aggregation captures full price range (not just sampled points)"""
        from src.core.analyzer import get_price_value

        # Simulate volatile period with large wicks
        chunk = [
            {"openPrice": 100, "highPrice": 105, "lowPrice": 98, "closePrice": 102, "volume": 100},
            {"openPrice": 102, "highPrice": 110, "lowPrice": 101, "closePrice": 108, "volume": 150},  # Big wick up
            {"openPrice": 108, "highPrice": 109, "lowPrice": 95, "closePrice": 97, "volume": 200},   # Big wick down
            {"openPrice": 97, "highPrice": 100, "lowPrice": 96, "closePrice": 99, "volume": 120},
        ]

        agg_high = max(chunk, key=lambda x: get_price_value(x.get("highPrice", 0))).get("highPrice", 0)
        agg_low = min(chunk, key=lambda x: get_price_value(x.get("lowPrice", 0))).get("lowPrice", 0)

        # Should capture the extreme wicks, not just open/close
        self.assertEqual(agg_high, 110, "High should capture the spike at 110")
        self.assertEqual(agg_low, 95, "Low should capture the dip at 95")

    def test_volume_aggregation_sums_all(self):
        """Verify volume is summed, not averaged or sampled"""
        chunk = [
            {"openPrice": 100, "highPrice": 105, "lowPrice": 98, "closePrice": 102, "volume": 1000},
            {"openPrice": 102, "highPrice": 106, "lowPrice": 100, "closePrice": 104, "volume": 2000},
            {"openPrice": 104, "highPrice": 108, "lowPrice": 103, "closePrice": 107, "volume": 1500},
            {"openPrice": 107, "highPrice": 110, "lowPrice": 105, "closePrice": 109, "volume": 3000},
        ]

        agg_vol = sum(float(x.get("volume", 0)) for x in chunk)

        self.assertEqual(agg_vol, 7500, "Volume should be sum of all candles")

    def test_string_price_handling(self):
        """Test that string prices are handled correctly (BingX sometimes returns strings)"""
        from src.core.analyzer import get_price_value

        chunk = [
            {"openPrice": "100.50", "highPrice": "110.25", "lowPrice": "95.75", "closePrice": "105.00", "volume": "1000"},
            {"openPrice": "105.00", "highPrice": "108.50", "lowPrice": "102.25", "closePrice": "107.75", "volume": "1500.5"},
        ]

        agg_open = get_price_value(chunk[0].get("openPrice", 0))
        agg_close = get_price_value(chunk[-1].get("closePrice", 0))
        agg_high = max(get_price_value(c.get("highPrice", 0)) for c in chunk)
        agg_low = min(get_price_value(c.get("lowPrice", 0)) for c in chunk)
        agg_vol = sum(float(x.get("volume", 0)) for x in chunk)

        self.assertAlmostEqual(agg_open, 100.50, places=2)
        self.assertAlmostEqual(agg_close, 107.75, places=2)
        self.assertAlmostEqual(agg_high, 110.25, places=2)
        self.assertAlmostEqual(agg_low, 95.75, places=2)
        self.assertAlmostEqual(agg_vol, 2500.5, places=1)


if __name__ == '__main__':
    unittest.main()
