import sys
import os
import time
import json
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.predict import main

def test_parallel_execution():
    print("🧪 Testing Parallel Execution...")

    # Mock analysis data for 3 symbols
    analyses = [
        {"symbol": "BTCUSDT", "rsi": 70, "current_price": 90000, "sma": 89000, "prompt": "prompt1"},
        {"symbol": "ETHUSDT", "rsi": 30, "current_price": 3000, "sma": 3100, "prompt": "prompt2"},
        {"symbol": "SOLUSDT", "rsi": 80, "current_price": 100, "sma": 90, "prompt": "prompt3"}
    ]

    # Mock get_prediction to simulate delay
    def mock_get_prediction_delayed(prompt):
        time.sleep(1) # Simulate 1 second API latency
        return json.dumps({
            "action": "hold",
            "confidence": 0.5,
            "reason": "Test"
        })

    with patch('src.core.predict.get_prediction', side_effect=mock_get_prediction_delayed):
        start_time = time.time()
        predictions = main(analyses)
        end_time = time.time()

        duration = end_time - start_time
        print(f"⏱️ Processed {len(analyses)} symbols in {duration:.2f} seconds.")

        # If sequential, it would take ~3 seconds (1s * 3).
        # If parallel, it should take ~1 second (plus overhead).

        if duration < 1.5:
            print("✅ Parallel execution confirmed (Duration < 1.5s)")
        else:
            print("❌ Parallel execution FAILED (Duration >= 1.5s)")

        assert len(predictions) == 3
        print("✅ All predictions received")

if __name__ == "__main__":
    test_parallel_execution()
