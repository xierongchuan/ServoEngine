import threading
import pytest

from unittest.mock import MagicMock

from src.exchanges.dto.models import Balance, Position, PositionSide
from src.exchanges.impl.bingx_client import BingXClient
from src.exchanges.errors import ExchangeStateUnavailableError


def make_client_with_balance_response(response: dict) -> BingXClient:
    client = BingXClient.__new__(BingXClient)
    client._cache_lock = threading.RLock()
    client._balance_cache = None
    client._balance_cache_time = 0
    client._make_request = lambda *_args, **_kwargs: response
    return client


def test_get_balance_parses_demo_equity_and_available_margin():
    client = make_client_with_balance_response(
        {
            "code": 0,
            "data": {
                "balance": {
                    "asset": "USDT",
                    "balance": "10000.00",
                    "availableMargin": "8500.00",
                    "equity": "10150.50",
                    "unrealizedProfit": "150.50",
                }
            },
        }
    )

    balance = client.get_balance()

    assert isinstance(balance, Balance)
    assert balance.total_balance == 10150.50
    assert balance.available_balance == 8500.00
    assert balance.unrealized_pnl == 150.50
    assert balance.locked_balance == 1650.50
    assert balance.asset == "USDT"


def test_get_balance_parses_available_balance_format():
    client = make_client_with_balance_response(
        {
            "code": 0,
            "data": {
                "balance": {
                    "asset": "USDT",
                    "balance": "250.25",
                    "availableBalance": "200.10",
                    "walletBalance": "251.00",
                }
            },
        }
    )

    balance = client.get_balance()

    assert balance.total_balance == 250.25
    assert balance.available_balance == 200.10
    assert balance.unrealized_pnl == 0.75


@pytest.mark.parametrize("response", [None, {"code": 100001, "msg": "auth failed"}])
def test_get_balance_fails_closed_when_state_is_unavailable(response):
    client = make_client_with_balance_response(response)
    with pytest.raises(ExchangeStateUnavailableError):
        client.get_balance()


def test_get_positions_fails_closed_and_does_not_cache_empty_state():
    client = BingXClient.__new__(BingXClient)
    client._cache_lock = threading.RLock()
    client._positions_cache = None
    client._positions_cache_time = 0
    client._positions_cache_ttl = 5
    client._make_request = lambda *_args, **_kwargs: None

    with pytest.raises(ExchangeStateUnavailableError):
        client.get_positions()
    assert client._positions_cache is None


def test_close_position_does_not_cancel_unrelated_orders():
    client = BingXClient.__new__(BingXClient)
    position = Position(
        symbol="BTCUSDT", side=PositionSide.LONG, size=0.02,
        entry_price=50_000, unrealized_pnl=0, position_id="position-1",
    )
    client.get_positions = lambda: {"BTCUSDT": [position]}
    client._format_symbol = lambda symbol: "BTC-USDT"
    client._make_request = MagicMock(return_value={"code": 0, "data": {"orderId": "close-1"}})
    client.invalidate_cache = MagicMock()
    client.cancel_all_orders = MagicMock()

    assert client.close_position("BTCUSDT", "position-1") is True
    client.cancel_all_orders.assert_not_called()
    params = client._make_request.call_args.args[2]
    assert params["side"] == "SELL"
    assert params["positionSide"] == "LONG"
    assert params["quantity"] == 0.02
