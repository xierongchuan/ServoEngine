"""Изолированные тесты REST MEXC с фиктивными ключами и ответами."""

import copy
import hashlib
import hmac
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import requests

from src.exchanges.dto.models import MarketType, OrderSide, OrderType, PositionSide, SpotOrderRequest
from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from src.exchanges.impl.mexc_futures_client import MEXCFuturesClient
from src.exchanges.impl.mexc_spot_client import MEXCSpotClient
from src.exchanges.impl.mexc_transport import MEXCFuturesTransport, MEXCSpotTransport


def config(**overrides):
    values = dict(base_url="https://api.mexc.invalid", api_key="fake-key", secret_key="fake-secret",
        request_timeout=2, is_demo=False, execution_mode="live", live_trading_enabled=True,
        settle_asset="USDT", margin_mode="isolated", position_mode="hedge", default_leverage=10,
        positions_cache_ttl=0, balance_cache_ttl=0, supported_intervals={"1m": "Min1", "5m": "Min5"},
        protection={"trigger_price": "mark", "failure_policy": "pause"})
    values.update(overrides)
    return SimpleNamespace(**values)


class Response:
    def __init__(self, payload, status=200, headers=None):
        self._payload, self.status_code, self.headers = payload, status, headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_spot_signature_is_deterministic(monkeypatch):
    session = Session([Response({"balances": []})])
    transport = MEXCSpotTransport(config(), session)
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 1700000000000)
    transport.request("GET", "/api/v3/account", {"symbol": "BTCUSDT"}, private=True)
    unsigned = [("recvWindow", 5000), ("symbol", "BTCUSDT"), ("timestamp", 1700000000000)]
    expected = hmac.new(b"fake-secret", urlencode(unsigned).encode(), hashlib.sha256).hexdigest()
    assert session.calls[0][2]["params"] == unsigned + [("signature", expected)]
    assert session.calls[0][2]["headers"] == {"X-MEXC-APIKEY": "fake-key"}
    assert session.calls[0][2]["allow_redirects"] is False


def test_futures_post_signs_exact_json(monkeypatch):
    session = Session([Response({"success": True, "data": {"orderId": 7}})])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 12)
    transport.request("POST", "/api/v1/private/order/create", {"vol": "2", "symbol": "BTC_USDT"}, private=True)
    call = session.calls[0][2]
    assert call["data"] == '{"symbol":"BTC_USDT","vol":"2"}'
    target = f"fake-key12{call['data']}"
    assert call["headers"]["Signature"] == hmac.new(b"fake-secret", target.encode(), hashlib.sha256).hexdigest()


def test_futures_get_uses_exact_sorted_signed_query(monkeypatch):
    session = Session([Response({"success": True, "data": []})])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 12)
    transport.request("GET", "/private", {"z": "a b", "a": 1}, private=True)
    assert session.calls[0][2]["params"] == "a=1&z=a%20b"
    expected = hmac.new(b"fake-secret", b"fake-key12a=1&z=a%20b", hashlib.sha256).hexdigest()
    assert session.calls[0][2]["headers"]["Signature"] == expected


def test_time_offset_is_synchronized(monkeypatch):
    transport = MEXCSpotTransport(config(), Session([Response({"serverTime": 1700000001000})]))
    monkeypatch.setattr("src.exchanges.impl.mexc_transport.time.time", lambda: 1700000000.0)
    transport.sync_time()
    assert transport.time_offset_ms == 1000


def test_post_5xx_is_ambiguous_and_not_retried():
    session = Session([Response({}, status=503)])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError) as exc:
        transport.request("POST", "/api/v1/private/order/create", {"secret": "must-not-leak"}, private=True, max_attempts=3)
    assert len(session.calls) == 1
    assert "must-not-leak" not in str(exc.value)


@pytest.mark.parametrize("transport_cls", [MEXCSpotTransport, MEXCFuturesTransport])
@pytest.mark.parametrize("mode", ["read_only", "demo"])
def test_live_guard_blocks_before_network(transport_cls, mode):
    session = Session([])
    transport = transport_cls(config(execution_mode=mode), session)
    with pytest.raises(ExchangeAPIError):
        transport.request("POST", "/api/v1/private/order/create", {})
    assert session.calls == []


def contract(**overrides):
    item = dict(symbol="BTC_USDT", futureType=1, settleCoin="USDT", state=0, apiAllowed=True,
        baseCoin="BTC", quoteCoin="USDT", contractSize="0.001", priceUnit="0.1", volUnit="1",
        minVol="1", maxVol="100000", limitMaxVol="200000", minLeverage=1, maxLeverage=125,
        positionOpenType=3, riskLimitMode="CUSTOM", riskLimitCustom=[{"maxVol": 1000000, "maxLeverage": 125}])
    item.update(overrides)
    return item


def position(**overrides):
    item = dict(symbol="BTC_USDT", holdVol="12", frozenVol="0", positionType=1, openType=1,
        holdAvgPrice="60000", unRealizedPnl="3.2", leverage=10, positionId=42)
    item.update(overrides)
    return item


def futures_order(**overrides):
    item = dict(orderId=99, symbol="BTC_USDT", side=4, orderType=5, state=3, vol="12", dealVol="12",
        dealAvgPrice="60000", price="0", externalOid="stable-close", positionId=42)
    item.update(overrides)
    return item


def stop_order(**overrides):
    item = dict(id=88, symbol="BTC_USDT", positionId=42, positionType=1, stopLossPrice="59000",
        takeProfitPrice="62000", state=1, isFinished=0, lossTrend=2, profitTrend=2, vol="12",
        volType=2, profitLossVolType="SAME", stopLossType=0, takeProfitType=0, stopLossReverse=2,
        takeProfitReverse=2, errorCode=0, version=1, updateTime=1700000000000, priceProtect=0)
    item.update(overrides)
    return item


class FuturesTransport:
    def __init__(self, overrides=None):
        self.calls, self.overrides, self.last_create, self.closed = [], overrides or {}, None, False

    def request(self, method, endpoint, params=None, **kwargs):
        self.calls.append((method, endpoint, params, kwargs))
        if endpoint in self.overrides:
            result = self.overrides[endpoint]
            if callable(result):
                return result(method, params)
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)
        if endpoint == "/api/v1/contract/detail/country":
            return {"success": True, "data": contract()}
        if endpoint == "/api/v1/private/position/position_mode":
            return {"success": True, "data": 1}
        if endpoint == "/api/v1/private/account/tiered_fee_rate/v2":
            return {"success": True, "data": dict(symbol="BTC_USDT", feeRateMode="NORMAL", realMakerFee="0.0001", realTakerFee="0.0002", minLeverage=1, maxLeverage=50)}
        if endpoint == "/api/v1/private/order/list/open_orders":
            return {"success": True, "data": []}
        if endpoint == "/api/v1/private/position/open_positions":
            return {"success": True, "data": [] if self.closed else [position()]}
        if endpoint == "/api/v1/private/order/create":
            self.last_create = dict(params)
            self.closed = params["side"] in {2, 4}
            return {"success": True, "data": {"orderId": 99}}
        if endpoint.startswith("/api/v1/private/order/external/"):
            return {"success": True, "data": futures_order(externalOid=self.last_create["externalOid"], vol=self.last_create["vol"], dealVol=self.last_create["vol"], side=self.last_create["side"])}
        if endpoint == "/api/v1/private/stoporder/list/orders":
            return {"success": True, "data": []}
        raise AssertionError((method, endpoint))


def test_futures_rules_contract_conversion_and_actual_fees():
    client = MEXCFuturesClient(config(), FuturesTransport())
    rules = client.get_instrument_rules("BTCUSDT")
    assert rules.exchange_symbol == "BTC_USDT" and rules.contract_size == Decimal("0.001")
    assert rules.leverage_tiers[0]["maxLeverage"] == 125
    assert client.get_positions()["BTCUSDT"][0].size == pytest.approx(0.012)
    assert client.get_commission_rate("BTCUSDT").taker == pytest.approx(0.02)


def test_futures_entry_has_stable_id_and_no_automatic_leverage_change():
    transport = FuturesTransport()
    client = MEXCFuturesClient(config(), transport)
    assert client.place_order("BTCUSDT", OrderSide.BUY, 0.012, sl=59000, tp=62000, client_order_id="stable-entry") == "99"
    payload = transport.last_create
    assert payload["vol"] == "12" and payload["side"] == 1 and payload["openType"] == 1
    assert payload["stopLossPrice"] == "59000" and payload["takeProfitPrice"] == "62000"
    assert payload["externalOid"] == "stable-entry"
    assert not client.last_protection_confirmed
    assert not any(c[1].endswith("change_leverage") for c in transport.calls)


def test_futures_unknown_is_not_absorbed_or_retried():
    unknown_error = UnknownOrderStateError("неизвестно")
    transport = FuturesTransport({"/api/v1/private/order/create": unknown_error})
    client = MEXCFuturesClient(config(), transport)
    with pytest.raises(UnknownOrderStateError) as exc:
        client.place_order("BTCUSDT", OrderSide.BUY, 0.012, client_order_id="stable-entry")
    assert exc.value is unknown_error and exc.value.client_order_id == "stable-entry"
    assert len([c for c in transport.calls if c[0] == "POST"]) == 1
    assert not any("/external/" in c[1] for c in transport.calls)


@pytest.mark.parametrize("position_type,expected_side", [(1, 4), (2, 2)])
def test_futures_close_requires_fill_and_fresh_position_reduction(position_type, expected_side):
    transport = FuturesTransport()
    transport.overrides["/api/v1/private/position/open_positions"] = lambda *_: {"success": True, "data": [] if transport.closed else [position(positionType=position_type)]}
    client = MEXCFuturesClient(config(), transport)
    assert client.close_position("BTCUSDT", "42", client_order_id="stable-close")
    assert transport.last_create["side"] == expected_side
    assert transport.last_create["positionId"] == 42
    assert "reduceOnly" not in transport.last_create
    assert not any(c[1].endswith("cancel_all") for c in transport.calls)


def test_futures_one_way_close_is_reduce_only():
    transport = FuturesTransport({"/api/v1/private/position/position_mode": {"success": True, "data": 2}})
    client = MEXCFuturesClient(config(position_mode="one_way"), transport)
    assert client.close_position("BTCUSDT", "42", client_order_id="stable-close")
    assert transport.last_create["reduceOnly"] is True


def spot_rules(**overrides):
    item = dict(symbol="BTCUSDT", baseAsset="BTC", quoteAsset="USDT", status="1", isSpotTradingAllowed=True,
        tradeSideType=1, baseSizePrecision="0.00001", baseAssetPrecision=8, quotePrecision=2,
        quoteAmountPrecision="5", maxQuoteAmount="1000000", quoteAmountPrecisionMarket="10",
        maxQuoteAmountMarket="100000", orderTypes=["MARKET", "LIMIT", "LIMIT_MAKER"], filters=[])
    item.update(overrides)
    return item


def spot_order(**overrides):
    item = dict(orderId="abc123", symbol="BTCUSDT", side="BUY", type="LIMIT", status="NEW",
        price="60000", Qty="0.01", executedQty="0", cumulativeQuoteQty="0", clientOrderId="stable-spot")
    item.update(overrides)
    return item


class SpotTransport:
    def __init__(self, overrides=None):
        self.calls, self.overrides = [], overrides or {}

    def request(self, method, endpoint, params=None, **kwargs):
        self.calls.append((method, endpoint, params, kwargs))
        key = (method, endpoint)
        if key in self.overrides:
            result = self.overrides[key]
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)
        if endpoint == "/api/v3/exchangeInfo":
            return {"symbols": [spot_rules()]}
        if endpoint == "/api/v3/ticker/price":
            return {"symbol": "BTCUSDT", "price": "60000"}
        if endpoint == "/api/v3/order/test":
            return {}
        if endpoint == "/api/v3/order" and method == "POST":
            return dict(orderId="abc123", symbol=params["symbol"], side=params["side"], type=params["type"])
        raise AssertionError(key)


def test_spot_market_buy_test_uses_quote():
    transport = SpotTransport()
    client = MEXCSpotClient(config(), transport)
    submission = client.place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.BUY, OrderType.MARKET, quote_quantity=Decimal("25.129"), test_only=True))
    assert transport.calls[-1][1] == "/api/v3/order/test"
    assert transport.calls[-1][2]["quoteOrderQty"] == "25.12"
    assert submission.client_order_id.startswith("se")


def test_spot_market_sell_preserves_base_precision_not_minimum_step():
    transport = SpotTransport()
    client = MEXCSpotClient(config(), transport)
    client.place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.SELL, OrderType.MARKET, base_quantity=Decimal("0.123456789")))
    assert transport.calls[-1][2]["quantity"] == "0.12345678"
    assert any(c[1] == "/api/v3/ticker/price" for c in transport.calls)


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_spot_limit_rounded_price_and_notional(side):
    transport = SpotTransport()
    MEXCSpotClient(config(), transport).place_spot_order(SpotOrderRequest("BTCUSDT", side, OrderType.LIMIT, base_quantity=Decimal("0.010009"), price=Decimal("60123.129")))
    assert transport.calls[-1][2]["quantity"] == "0.010009"
    assert transport.calls[-1][2]["price"] == "60123.12"


def test_spot_unknown_is_not_absorbed():
    transport = SpotTransport({("POST", "/api/v3/order"): UnknownOrderStateError("неизвестно")})
    with pytest.raises(UnknownOrderStateError) as exc:
        MEXCSpotClient(config(), transport).place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.BUY, OrderType.MARKET, quote_quantity=Decimal("25"), client_order_id="stable-spot"))
    assert exc.value.client_order_id == "stable-spot"
    assert len([c for c in transport.calls if c[0] == "POST"]) == 1
    assert not any(c[1] == "/api/v3/order" and c[0] == "GET" for c in transport.calls)


def test_spot_rejects_derivatives_and_bulk_cancel_before_network():
    transport = SpotTransport()
    client = MEXCSpotClient(config(), transport)
    assert client.capabilities.market_type == MarketType.SPOT
    for action in (client.get_positions, lambda: client.set_leverage("BTCUSDT", 3), lambda: client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=1), lambda: client.cancel_all_orders("BTCUSDT")):
        with pytest.raises(UnsupportedCapabilityError):
            action()
    assert transport.calls == []
