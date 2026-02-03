import sys
import os
import logging
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.executor import create_order, main

def test_trade_logging():
    print("🧪 Testing Trade Logging...")

    # Mock ExchangeClient
    mock_client = MagicMock()
    mock_client.get_balance.return_value = {'balance': 1000.0}
    mock_client.place_order.return_value = "ORDER_123"
    mock_client.get_positions.return_value = {}
    mock_client.check_prerequisites.return_value = True
    mock_client.close_position.return_value = True

    # Mock log_trade AND all logger functions to prevent contaminating production logs
    with patch('src.core.executor.log_trade') as mock_log_trade, \
         patch('src.core.executor.info') as mock_info, \
         patch('src.core.executor.warning') as mock_warning, \
         patch('src.core.executor.error') as mock_error:
        with patch('src.core.executor.get_exchange_client', return_value=mock_client):

            # Test 1: Open Order Logging
            print("\n--- Test 1: Open Order ---")
            create_order(
                symbol="BTC-USDT",
                direction="BUY",
                price=50000,
                reason="Test Strategy",
                confidence=0.95,
                ai_tp=51000,
                ai_sl=49000
            )

            # Verify log_trade called
            if mock_log_trade.called:
                args, _ = mock_log_trade.call_args
                log_msg = args[0]
                print(f"Log Message: {log_msg}")

                if "Qty=" in log_msg and "Reason: Test Strategy" in log_msg and "Conf: 0.95" in log_msg:
                    print("✅ Open Order Logged correctly with details")
                else:
                    print("❌ Open Order Log missing details")
            else:
                print("❌ log_trade NOT called for Open Order")

            mock_log_trade.reset_mock()

            # Test 2: Close Position Logging
            print("\n--- Test 2: Close Position ---")
            # Setup positions for main()
            mock_client.get_positions.return_value = {
                "BTC-USDT": [{"dealId": "DEAL_123", "size": 0.1, "type": "BUY"}]
            }

            predictions = [{
                "symbol": "BTC-USDT",
                "current_price": 52000,
                "action": "close",
                "confidence": 0.9,
                "reason": "Take Profit Hit"
            }]

            main(predictions)

            # Verify log_trade called
            if mock_log_trade.called:
                args, _ = mock_log_trade.call_args
                log_msg = args[0]
                print(f"Log Message: {log_msg}")

                if "закрыта" in log_msg and "Причина: Take Profit Hit" in log_msg:
                    print("✅ Close Position Logged correctly")
                else:
                    print("❌ Close Position Log missing details")
            else:
                print("❌ log_trade NOT called for Close Position")

if __name__ == "__main__":
    test_trade_logging()
