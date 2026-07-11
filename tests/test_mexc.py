"""Изолированные fixture-тесты MEXC: сеть и реальные ордера не используются."""

import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import requests

from src.exchanges.dto.models import (
    MarketType, OrderSide, OrderType, PositionSide, SpotOrderRequest,
)
from src.exchanges.errors import (
    ExchangeAPIError, UnknownOrderStateError, UnsupportedCapabilityError,
)
from src.exchanges.impl.mexc_futures_client import MEXCFuturesClient
from src.exchanges.impl.mexc_spot_client import MEXCSpotClient
from src.exchanges.impl.mexc_transport import MEXCFuturesTransport, MEXCSpotTransport


def config(**overrides):
    values = {
        "base_url": "https://api.mexc.com",
        "api_key": "api-key",
        "secret_key": "secret-key",
        "request_timeout": 2,
        "is_demo": False,
        "live_trading_enabled": True,
        "settle_asset": "USDT",
        "margin_mode": "isolated",
        "position_mode": "hedge",
        "default_leverage": 10,
        "positions_cache_ttl": 0,
        "balance_cache_ttl": 0,
        "supported_intervals": {"1m": "Min1", "5m": "Min5"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Response:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


def test_spot_signature_is_deterministic(monkeypatch):
    session = Session([Response({"ok": True})])
    transport = MEXCSpotTransport(config(), session)
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 1700000000000)

    transport.request("GET", "/api/v3/account", {"symbol": "BTCUSDT"}, private=True)

    params = session.calls[0][2]["params"]
    unsigned = [("recvWindow", 5000), ("symbol", "BTCUSDT"), ("timestamp", 1700000000000)]
    expected = hmac.new(
        b"secret-key", urlencode(unsigned).encode(), hashlib.sha256,
    ).hexdigest()
    assert params == unsigned + [("signature", expected)]
    assert session.calls[0][2]["headers"] == {"X-MEXC-APIKEY": "api-key"}


def test_futures_post_signs_exact_json(monkeypatch):
    session = Session([Response({"success": True, "data": {"orderId": 7}})])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 1700000000000)
    payload = {"vol": 2, "symbol": "BTC_USDT"}

    transport.request("POST", "/api/v1/private/order/create", payload, private=True)

    call = session.calls[0][2]
    assert call["data"] == '{"symbol":"BTC_USDT","vol":2}'
    target = f"api-key1700000000000{call['data']}"
    assert call["headers"]["Signature"] == hmac.new(
        b"secret-key", target.encode(), hashlib.sha256,
    ).hexdigest()


def test_futures_get_uses_sorted_signed_query(monkeypatch):
    session = Session([Response({"success": True, "data": []})])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 12)

    transport.request("GET", "/private", {"z": 2, "a": 1}, private=True)

    params = session.calls[0][2]["params"]
    assert params == [("a", 1), ("z", 2)]
    expected = hmac.new(b"secret-key", b"api-key12a=1&z=2", hashlib.sha256).hexdigest()
    assert session.calls[0][2]["headers"]["Signature"] == expected


def test_time_offset_is_synchronized(monkeypatch):
    session = Session([Response({"serverTime": 1700000001000})])
    transport = MEXCSpotTransport(config(), session)
    monkeypatch.setattr("src.exchanges.impl.mexc_transport.time.time", lambda: 1700000000.0)
    transport.sync_time()
    assert transport.time_offset_ms == 1000


def test_post_5xx_is_ambiguous_and_not_retried():
    session = Session([Response({"message": "temporary"}, status=503)])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    from src.exchanges.errors import ExchangeStateUnavailableError
    with pytest.raises(ExchangeStateUnavailableError) as exc:
        transport.request("POST", "/private", {"secret": "must-not-leak"}, private=True)
    assert len(session.calls) == 1
    assert "must-not-leak" not in str(exc.value)


@pytest.mark.parametrize("transport_cls", [MEXCSpotTransport, MEXCFuturesTransport])
def test_live_guard_blocks_demo_and_disabled(transport_cls):
    with pytest.raises(ExchangeAPIError):
        transport_cls(config(is_demo=True), Session([])).assert_mutation_allowed()
    with pytest.raises(ExchangeAPIError):
        transport_cls(config(live_trading_enabled=False), Session([])).assert_mutation_allowed()
    with pytest.raises(ExchangeAPIError):
        transport_cls(config(is_demo=True), Session([])).request("GET", "/private", private=True)


class FuturesTransport:
    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = overrides or {}

    def request(self, method, endpoint, params=None, **kwargs):
        self.calls.append((method, endpoint, params, kwargs))
        if endpoint in self.overrides:
            result = self.overrides[endpoint]
            if isinstance(result, Exception):
                raise result
            return result
        if endpoint == "/api/v1/contract/detail/country":
            return {"success": True, "data": {
                "symbol": "BTC_USDT", "futureType": 1, "settleCoin": "USDT",
                "state": 0, "apiAllowed": True, "baseCoin": "BTC", "quoteCoin": "USDT",
                "contractSize": "0.001", "priceUnit": "0.1", "volUnit": "1",
                "minVol": "1", "maxVol": "100000", "minLeverage": 1, "maxLeverage": 125,
            }}
        if endpoint == "/api/v1/private/position/change_leverage":
            return {"success": True}
        if endpoint == "/api/v1/private/order/create":
            return {"success": True, "data": {"orderId": 99}}
        if endpoint == "/api/v1/private/position/open_positions":
            return {"success": True, "data": [{
                "symbol": "BTC_USDT", "holdVol": "12", "positionType": 1,
                "holdAvgPrice": "60000", "unRealizedPnl": "3.2", "leverage": 10,
                "positionId": 42,
            }]}
        raise AssertionError(endpoint)


def test_futures_rules_and_contract_conversion():
    transport = FuturesTransport()
    client = MEXCFuturesClient(config(), transport)
    rules = client.get_instrument_rules("BTCUSDT")
    positions = client.get_positions()

    assert rules.exchange_symbol == "BTC_USDT"
    assert rules.contract_size == Decimal("0.001")
    assert positions["BTCUSDT"][0].size == pytest.approx(0.012)
    assert positions["BTCUSDT"][0].exchange_quantity == 12


def test_futures_entry_has_external_id_and_attached_protection():
    transport = FuturesTransport()
    client = MEXCFuturesClient(config(), transport)

    order_id = client.place_order(
        "BTCUSDT", OrderSide.BUY, 0.012, OrderType.MARKET,
        sl=59000, tp=62000, leverage=10,
    )

    assert order_id == "99"
    create = next(call for call in transport.calls if call[1] == "/api/v1/private/order/create")
    payload = create[2]
    assert payload["vol"] == 12.0
    assert payload["side"] == 1
    assert payload["stopLossPrice"] == 59000
    assert payload["takeProfitPrice"] == 62000
    assert payload["externalOid"].startswith("se")


def test_futures_timeout_reconciles_by_external_id_without_duplicate():
    from src.exchanges.errors import ExchangeStateUnavailableError

    class TimeoutTransport(FuturesTransport):
        def request(self, method, endpoint, params=None, **kwargs):
            self.calls.append((method, endpoint, params, kwargs))
            if endpoint == "/api/v1/contract/detail/country":
                self.calls.pop()
                return super().request(method, endpoint, params, **kwargs)
            if endpoint == "/api/v1/private/position/change_leverage":
                return {"success": True}
            if endpoint == "/api/v1/private/order/create":
                raise ExchangeStateUnavailableError("timeout")
            if endpoint.startswith("/api/v1/private/order/external/"):
                return {"success": True, "data": {
                    "orderId": 77, "symbol": "BTC_USDT", "side": 1,
                    "orderType": 5, "state": 1, "vol": 12,
                }}
            raise AssertionError(endpoint)

    transport = TimeoutTransport()
    client = MEXCFuturesClient(config(), transport)
    assert client.place_order("BTCUSDT", OrderSide.BUY, 0.012) == "77"
    assert len([call for call in transport.calls if call[1] == "/api/v1/private/order/create"]) == 1
    assert len([call for call in transport.calls if "/external/" in call[1]]) == 1


@pytest.mark.parametrize(
    ("position_type", "expected_side"), [(1, 4), (2, 2)],
)
def test_futures_close_uses_position_id_without_cancel_all(position_type, expected_side):
    transport = FuturesTransport({
        "/api/v1/private/position/open_positions": {"success": True, "data": [{
            "symbol": "BTC_USDT", "holdVol": "10", "positionType": position_type,
            "holdAvgPrice": "60000", "positionId": 42,
        }]},
    })
    client = MEXCFuturesClient(config(), transport)

    assert client.close_position("BTCUSDT", "42", 0.5)
    create = next(call for call in transport.calls if call[1] == "/api/v1/private/order/create")
    assert create[2]["side"] == expected_side
    assert create[2]["positionId"] == 42
    assert create[2]["vol"] == 5.0
    assert not any(call[1].endswith("cancel_all") for call in transport.calls)


def test_futures_one_way_close_is_reduce_only():
    transport = FuturesTransport()
    client = MEXCFuturesClient(config(position_mode="one_way"), transport)
    assert client.close_position("BTCUSDT", "42")
    create = next(call for call in transport.calls if call[1] == "/api/v1/private/order/create")
    assert create[2]["reduceOnly"] is True


class SpotTransport:
    def __init__(self, fail_create=False):
        self.calls = []
        self.fail_create = fail_create

    def request(self, method, endpoint, params=None, **kwargs):
        self.calls.append((method, endpoint, params, kwargs))
        if endpoint == "/api/v3/exchangeInfo":
            return {"symbols": [{
                "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
                "status": "1", "isSpotTradingAllowed": True, "tradeSideType": 1,
                "baseSizePrecision": "0.00001", "baseAssetPrecision": 8,
                "quotePrecision": 2, "quoteAmountPrecision": "5", "maxQuoteAmount": "1000000",
                "orderTypes": ["MARKET", "LIMIT"],
            }]}
        if endpoint == "/api/v3/order" and method == "POST":
            if self.fail_create:
                from src.exchanges.errors import ExchangeStateUnavailableError
                raise ExchangeStateUnavailableError("timeout")
            return {"orderId": 11}
        if endpoint == "/api/v3/order" and method == "GET":
            return None
        if endpoint == "/api/v3/order/test":
            return {}
        raise AssertionError((method, endpoint))


def test_spot_market_buy_uses_quote_and_test_order_is_safe():
    transport = SpotTransport()
    client = MEXCSpotClient(config(supported_intervals={"1m": "1m"}), transport)
    request = SpotOrderRequest(
        "BTCUSDT", OrderSide.BUY, OrderType.MARKET,
        quote_quantity=Decimal("25.129"), test_only=True,
    )

    submission = client.place_spot_order(request)
    call = transport.calls[-1]
    assert call[1] == "/api/v3/order/test"
    assert call[2]["quoteOrderQty"] == "25.12"
    assert call[3]["mutation"] is False
    assert submission.client_order_id.startswith("se")


def test_spot_market_sell_uses_base_quantity():
    transport = SpotTransport()
    client = MEXCSpotClient(config(), transport)
    client.place_spot_order(SpotOrderRequest(
        "BTCUSDT", OrderSide.SELL, OrderType.MARKET,
        base_quantity=Decimal("0.123456"),
    ))
    assert transport.calls[-1][2]["quantity"] == "0.12345"


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_spot_limit_buy_and_sell_use_rounded_price(side):
    transport = SpotTransport()
    client = MEXCSpotClient(config(), transport)
    client.place_spot_order(SpotOrderRequest(
        "BTCUSDT", side, OrderType.LIMIT,
        base_quantity=Decimal("0.010009"), price=Decimal("60123.129"),
    ))
    payload = transport.calls[-1][2]
    assert payload["quantity"] == "0.01000"
    assert payload["price"] == "60123.12"


def test_spot_timeout_is_not_blindly_retried():
    transport = SpotTransport(fail_create=True)
    client = MEXCSpotClient(config(), transport)
    with pytest.raises(UnknownOrderStateError):
        client.place_spot_order(SpotOrderRequest(
            "BTCUSDT", OrderSide.BUY, OrderType.MARKET,
            quote_quantity=Decimal("25"),
        ))
    creates = [call for call in transport.calls if call[0] == "POST"]
    queries = [call for call in transport.calls if call[0] == "GET" and call[1] == "/api/v3/order"]
    assert len(creates) == 1
    assert len(queries) == 1


def test_spot_rejects_derivative_capabilities_before_network():
    transport = SpotTransport()
    client = MEXCSpotClient(config(), transport)
    assert client.capabilities.market_type == MarketType.SPOT
    assert not client.capabilities.automated_strategy
    with pytest.raises(UnsupportedCapabilityError):
        client.get_positions()
    with pytest.raises(UnsupportedCapabilityError):
        client.set_leverage("BTCUSDT", 3)
    with pytest.raises(UnsupportedCapabilityError):
        client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=1)
    with pytest.raises(UnsupportedCapabilityError):
        client.cancel_all_orders("BTCUSDT")
    assert transport.calls == []


def test_unknown_futures_fields_do_not_break_rules():
    transport = FuturesTransport()
    response = transport.request("GET", "/api/v1/contract/detail/country")
    response["data"]["newFutureField"] = {"unknown": True}
    transport.overrides["/api/v1/contract/detail/country"] = response
    assert MEXCFuturesClient(config(), transport).get_instrument_rules("BTCUSDT").tradable
