import threading

from src.exchanges.dto.models import Balance
from src.exchanges.impl.bingx_client import BingXClient


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
