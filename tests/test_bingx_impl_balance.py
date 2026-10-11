"""Баланс BingX: wallet и equity не смешиваются, нулевой available сохраняется."""

import pytest
from src.exchanges.config.bingx_config import BingXConfig
from src.exchanges.impl.bingx_client import BingXClient
from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError


def make_client_with_balance_response(response):
    client = BingXClient(BingXConfig(settings={"execution_mode": "read_only"}))
    client._make_request = lambda *a, **k: response
    return client


def test_balance_equity_without_double_pnl():
    client = make_client_with_balance_response({"code": 0, "data": {"balance": {
        "asset": "USDT", "balance": "10000", "equity": "10150.50",
        "unrealizedProfit": "150.50", "availableMargin": "8500"}}})
    balance = client.get_balance()
    assert balance.total_balance == 10000
    assert balance.total_with_pnl == 10150.50
    assert balance.available_balance == 8500
    assert balance.locked_balance == 1500


@pytest.mark.parametrize("key", ["availableBalance", "availableMargin", "available", "free"])
def test_zero_available_not_replaced_with_wallet(key):
    balance = make_client_with_balance_response({"code": 0, "data": {"balance": {
        "balance": "250.25", "walletBalance": "251", key: "0"}}}).get_balance()
    assert balance.available_balance == 0
    assert balance.total_balance == 250.25 and balance.unrealized_pnl == 0


@pytest.mark.parametrize("mode,allowed", [("demo", True), ("read_only", False), ("live", False)])
def test_vst_balance_only_allowed_on_demo_endpoint(mode, allowed):
    client = BingXClient(BingXConfig(settings={"execution_mode": mode}))
    client._make_request = lambda *a, **k: {"code": 0, "data": {"balance": {
        "asset": "VST", "balance": "100", "equity": "105", "unrealizedProfit": "5", "availableMargin": "0"}}}
    if allowed:
        assert client.base_url == "https://open-api-vst.bingx.com"
        balance = client.get_balance()
        assert balance.asset == "VST" and balance.available_balance == 0
        assert balance.total_with_pnl == 105
    else:
        with pytest.raises(ExchangeStateUnavailableError):
            client.get_balance()


def test_equity_only_wallet_derived_once():
    balance = make_client_with_balance_response({"code": 0, "data": {"balance": {
        "equity": "120", "unrealizedProfit": "20", "availableMargin": "0"}}}).get_balance()
    assert balance.total_balance == 100 and balance.total_with_pnl == 120


@pytest.mark.parametrize("response,error", [(None, ExchangeStateUnavailableError),
    ({"code": 0, "data": {}}, ExchangeStateUnavailableError),
    ({"code": 0, "data": {"balance": {"balance": "100"}}}, ExchangeStateUnavailableError),
    ({"code": 100001, "msg": "ошибка авторизации"}, ExchangeAPIError)])
def test_unavailable_balance_typed_errors(response, error):
    with pytest.raises(error):
        make_client_with_balance_response(response).get_balance()


@pytest.mark.parametrize("field", ["balance", "availableMargin", "unrealizedProfit"])
def test_nonfinite_balance_rejected(field):
    raw = {"balance": "100", "availableMargin": "0", "unrealizedProfit": "0", field: "NaN"}
    with pytest.raises(ExchangeStateUnavailableError):
        make_client_with_balance_response({"code": 0, "data": {"balance": raw}}).get_balance()
