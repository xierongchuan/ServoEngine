import sys
import os
import json
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.executor import main, create_order
from src.core.predict import parse_response

def test_dynamic_sltp():
    print("🧪 Testing Dynamic SL/TP Logic...")

    # 1. Test Parsing
    print("\n1. Testing Response Parsing:")
    ai_response = """
    ```json
    {
        "action": "buy",
        "confidence": 0.9,
        "stop_loss": 90000.5,
        "take_profit": 95000.0,
        "reason": "Test reason"
    }
    ```
    """
    parsed = parse_response(ai_response)
    print(f"   Parsed: {parsed}")
    assert parsed["stop_loss"] == 90000.5
    assert parsed["take_profit"] == 95000.0
    print("   ✅ Parsing successful")

    # 2. Test Execution (New Order)
    print("\n2. Testing Execution (New Order):")

    with patch('src.core.executor.get_exchange_client') as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock balance to allow order creation
        mock_client.get_balance.return_value = {"balance": 1000}
        mock_client.check_prerequisites.return_value = True
        mock_client.get_positions.return_value = {} # No positions initially
        mock_client.place_order.return_value = "12345"

        predictions = [{
            "symbol": "BTCUSDT",
            "current_price": 92000,
            "action": "buy",
            "confidence": 0.9,
            "stop_loss": 90000,
            "take_profit": 95000,
            "reason": "Test"
        }]

        main(predictions)

        # Verify place_order called with correct SL/TP
        args, kwargs = mock_client.place_order.call_args
        print(f"   place_order called with: {kwargs}")

        # Check if SL/TP passed to place_order (via create_order logic)
        # Note: create_order calculates quantity and calls place_order
        # We need to verify that the SL/TP passed to place_order matches AI values
        assert kwargs["sl"] == 90000
        assert kwargs["tp"] == 95000
        print("   ✅ New order used AI SL/TP")

    # 3. Test Update (Existing Position)
    print("\n3. Testing Update (Existing Position):")

    with patch('src.core.executor.get_exchange_client') as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.check_prerequisites.return_value = True

        # Mock existing position
        mock_client.get_positions.return_value = {
            "BTCUSDT": [{
                "symbol": "BTCUSDT",
                "type": "buy",
                "size": 0.1,
                "entry": 91000,
                "dealId": "111",
                "pnl": 100
            }]
        }

        # Prediction says HOLD but updates SL/TP
        predictions = [{
            "symbol": "BTCUSDT",
            "current_price": 92000,
            "action": "hold",
            "confidence": 0.5,
            "stop_loss": 91500, # Move SL to profit
            "take_profit": 96000,
            "reason": "Update SL"
        }]

        main(predictions)

        # Verify set_sl_tp called
        mock_client.set_sl_tp.assert_called_once()
        args, kwargs = mock_client.set_sl_tp.call_args
        print(f"   set_sl_tp called with: args={args}, kwargs={kwargs}")

        assert args[0] == "BTCUSDT"
        assert args[1] == "LONG" # Derived from 'buy' type
        assert kwargs["sl"] == 91500
        assert kwargs["tp"] == 96000
        print("   ✅ Existing position SL/TP updated")

if __name__ == "__main__":
    test_dynamic_sltp()
