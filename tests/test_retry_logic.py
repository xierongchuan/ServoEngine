import sys
import os
import time
import json
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.predict import process_analysis

def test_retry_logic():
    print("🧪 Testing Retry Logic...")

    # Mock analysis
    analysis = {"symbol": "BTCUSDT", "rsi": 70, "current_price": 90000, "sma": 89000, "prompt": "prompt"}

    # Mock get_prediction to return bad JSON first, then good JSON
    mock_responses = [
        "Invalid JSON", # Attempt 1 (Fail)
        json.dumps({    # Attempt 2 (Success)
            "action": "hold",
            "confidence": 0.5,
            "reason": "Test Success"
        })
    ]

    def side_effect(prompt):
        return mock_responses.pop(0)

    with patch('src.core.predict.get_prediction', side_effect=side_effect), \
         patch('src.core.predict.should_call_ai', return_value=(True, None)), \
         patch('src.core.predict.info'), \
         patch('src.core.predict.warning'), \
         patch('src.core.predict.error'), \
         patch('time.sleep', return_value=None) as mock_sleep:
                result = process_analysis(analysis)

                print(f"Result: {result['reason']}")

                if result['reason'] == "Test Success":
                    print("✅ Retry logic worked! (Recovered from error)")
                else:
                    print(f"❌ Retry logic failed. Reason: {result['reason']}")

                # Verify sleep was called
                if mock_sleep.called:
                    print("✅ Sleep was called (Delay respected)")
                else:
                    print("❌ Sleep was NOT called")

if __name__ == "__main__":
    test_retry_logic()
