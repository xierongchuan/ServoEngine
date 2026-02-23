import unittest
from unittest.mock import patch
from src.core.predict import validate_prediction

class TestRRValidation(unittest.TestCase):
    @patch('src.core.predict.warning')
    @patch('src.core.predict.info')
    def test_good_rr(self, mock_info, mock_warning):
        """Test that a good R/R ratio is accepted"""
        prediction = {
            "action": "buy",
            "confidence": 0.9,
            "stop_loss": 99.0,
            "take_profit": 102.0,
            "reason": "Test"
        }
        current_price = 100.0
        # Risk = 1.0, Reward = 2.0, R/R = 2.0 > 1.5

        result = validate_prediction(prediction, current_price)
        self.assertEqual(result["action"], "buy")
        self.assertEqual(result["confidence"], 0.9)

    @patch('src.core.predict.warning')
    @patch('src.core.predict.info')
    def test_bad_rr_buy_auto_corrects_tp(self, mock_info, mock_warning):
        """Test that a bad R/R ratio for BUY auto-corrects TP instead of blocking"""
        prediction = {
            "action": "buy",
            "confidence": 0.9,
            "stop_loss": 90.0,
            "take_profit": 105.0,
            "reason": "Test"
        }
        current_price = 100.0
        # Risk = 10.0, Reward = 5.0, R/R = 0.5 < 0.8 (soft_limit)
        # Expected: TP adjusted to 100 + 10*0.8 = 108.0

        result = validate_prediction(prediction, current_price)
        self.assertEqual(result["action"], "buy")  # NOT blocked
        self.assertEqual(result["confidence"], 0.9)  # confidence preserved
        self.assertGreater(result["take_profit"], 105.0)  # TP moved further
        self.assertIn("AUTO-FIX: TP adjusted", result["reason"])

    @patch('src.core.predict.warning')
    @patch('src.core.predict.info')
    def test_bad_rr_sell_auto_corrects_tp(self, mock_info, mock_warning):
        """Test that a bad R/R ratio for SELL auto-corrects TP instead of blocking"""
        prediction = {
            "action": "sell",
            "confidence": 0.9,
            "stop_loss": 110.0,
            "take_profit": 95.0,
            "reason": "Test"
        }
        current_price = 100.0
        # Risk = 10.0, Reward = 5.0, R/R = 0.5 < 0.8 (soft_limit)
        # Expected: TP adjusted to 100 - 10*0.8 = 92.0

        result = validate_prediction(prediction, current_price)
        self.assertEqual(result["action"], "sell")  # NOT blocked
        self.assertEqual(result["confidence"], 0.9)  # confidence preserved
        self.assertLess(result["take_profit"], 95.0)  # TP moved further down
        self.assertIn("AUTO-FIX: TP adjusted", result["reason"])

    @patch('src.core.predict.warning')
    @patch('src.core.predict.info')
    def test_missing_sl_tp(self, mock_info, mock_warning):
        """Test that missing SL/TP results in HOLD"""
        prediction = {
            "action": "buy",
            "confidence": 0.9,
            "reason": "Test"
        }
        current_price = 100.0

        result = validate_prediction(prediction, current_price)
        self.assertEqual(result["action"], "hold")
        self.assertIn("Missing SL/TP", result["reason"])

    @patch('src.core.predict.warning')
    @patch('src.core.predict.info')
    def test_hold_action_ignored(self, mock_info, mock_warning):
        """Test that HOLD action is not validated for R/R"""
        prediction = {
            "action": "hold",
            "confidence": 0.5,
            "stop_loss": 90.0, # Bad R/R but shouldn't matter
            "take_profit": 105.0,
            "reason": "Test"
        }
        current_price = 100.0

        result = validate_prediction(prediction, current_price)
        self.assertEqual(result["action"], "hold")
        self.assertEqual(result["confidence"], 0.5)

if __name__ == '__main__':
    unittest.main()
