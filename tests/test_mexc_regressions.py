"""Fault injection: неисполненные ордера, ручные изменения и повреждённые схемы."""

from decimal import Decimal

import pytest
import requests

from test_mexc import (
    config, Response, Session, FuturesTransport, SpotTransport, contract, position,
    futures_order, stop_order, spot_rules, spot_order,
)
from src.exchanges.dto.models import OrderSide, OrderType, PositionSide, SpotOrderRequest
from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from src.exchanges.impl.mexc_transport import MEXCFuturesTransport, MEXCSpotTransport
from src.exchanges.impl.mexc_futures_client import MEXCFuturesClient
from src.exchanges.impl.mexc_spot_client import MEXCSpotClient


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("src.exchanges.impl.mexc_transport.time.sleep", lambda _: None)


@pytest.mark.parametrize("transport_cls", [MEXCSpotTransport, MEXCFuturesTransport])
@pytest.mark.parametrize("failure", [requests.exceptions.ChunkedEncodingError("секрет"), requests.exceptions.Timeout("секрет"), requests.exceptions.ConnectionError("секрет")])
def test_mutation_network_fault_is_one_send_unknown(transport_cls, failure):
    session = Session([failure])
    transport = transport_cls(config(), session)
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError) as exc:
        transport.request("POST", "/api/v1/private/order/create", {"signature": "секрет"}, private=True, max_attempts=99)
    assert len(session.calls) == 1
    assert "секрет" not in str(exc.value)


@pytest.mark.parametrize("payload", [ValueError("не JSON"), None, {}, {"success": "true"}, {"success": False, "code": 0}])
def test_malformed_futures_mutation_envelope_is_unknown(payload):
    session = Session([Response(payload)])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError):
        transport.request("POST", "/api/v1/private/order/create", {}, private=True)
    assert len(session.calls) == 1


@pytest.mark.parametrize("code", [500, 501, 801, 9999, 3016, 3017, 3018, 3019, 603, 2042, 2056, 6008, 9909])
def test_ambiguous_api_codes_are_unknown(code):
    session = Session([Response({"success": False, "code": code, "data": None})])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError):
        transport.request("POST", "/api/v1/private/order/create", {})
    assert len(session.calls) == 1


def test_definite_rejection_is_not_retried():
    session = Session([Response({"success": False, "code": 2005, "data": None})])
    transport = MEXCFuturesTransport(config(), session)
    transport._time_synced = True
    with pytest.raises(ExchangeAPIError) as exc:
        transport.request("POST", "/api/v1/private/order/create", {})
    assert exc.value.code == 2005 and len(session.calls) == 1


def test_get_chunked_failure_retries_bounded():
    session = Session([requests.exceptions.ChunkedEncodingError(), Response({"serverTime": 123})])
    assert MEXCSpotTransport(config(), session).request("GET", "/api/v3/time")["serverTime"] == 123
    assert len(session.calls) == 2


def test_retry_after_is_never_capped_and_cooldown_is_host_wide(monkeypatch):
    class Limiter:
        def __init__(self):
            self.weights, self.delays = [], []
        def acquire(self, weight=1):
            self.weights.append(weight)
        def cooldown(self, seconds):
            self.delays.append(seconds)
    public, private, orders = Limiter(), Limiter(), Limiter()
    session = Session([Response({"code": 429}, status=429, headers={"Retry-After": "120"})])
    transport = MEXCSpotTransport(config(), session, limiters={"public": public, "private": private, "orders": orders})
    transport._time_synced = True
    with pytest.raises(ExchangeAPIError):
        transport.request("DELETE", "/api/v3/order", {"orderId": "abc"}, private=True)
    assert all(l.delays == [120] for l in (public, private, orders))
    assert all(l.weights == [1] for l in (public, private, orders))
    assert transport._retry_delay(Response({}, headers={"Retry-After": "120"}), 0) == 120


def test_limiter_get_query_has_no_orders_budget():
    class Limiter:
        def __init__(self):
            self.weights, self.delays = [], []
        def acquire(self, weight=1):
            self.weights.append(weight)
        def cooldown(self, seconds):
            self.delays.append(seconds)
    public, private, orders, specific = [Limiter() for _ in range(4)]
    transport = MEXCSpotTransport(config(), Session([Response({})]), limiters={"public": public, "private": private, "orders": orders, "/api/v3/order": specific})
    transport._time_synced = True
    transport.request("GET", "/api/v3/order", private=True)
    assert public.weights == private.weights == [2]
    assert specific.weights == [1] and orders.weights == []


@pytest.mark.parametrize("field,value", [("contractSize", None), ("contractSize", "NaN"), ("priceUnit", 0), ("volUnit", -1), ("minVol", "Infinity"), ("apiAllowed", "true"), ("positionOpenType", None), ("riskLimitCustom", None)])
def test_futures_rules_fail_closed(field, value):
    transport = FuturesTransport({"/api/v1/contract/detail/country": {"success": True, "data": contract(**{field: value})}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCFuturesClient(config(), transport).get_instrument_rules("BTCUSDT")


@pytest.mark.parametrize("data", [None, {}, "", [{"holdVol": "12"}]])
def test_missing_position_data_never_means_flat(data):
    transport = FuturesTransport({"/api/v1/private/position/open_positions": {"success": True, "data": data}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCFuturesClient(config(), transport).get_positions()


@pytest.mark.parametrize("overrides", [dict(execution_mode="read_only"), dict(execution_mode="demo"), dict(margin_mode="cross")])
def test_futures_client_guards_before_private_reads(overrides):
    transport = FuturesTransport()
    with pytest.raises((ExchangeAPIError, ExchangeStateUnavailableError)):
        MEXCFuturesClient(config(**overrides), transport).place_order("BTCUSDT", OrderSide.BUY, 0.012)
    assert transport.calls == []


@pytest.mark.parametrize("mode", [2, [], {}, None, 9])
def test_actual_mode_mismatch_or_schema_blocks_mutations(mode):
    transport = FuturesTransport({"/api/v1/private/position/position_mode": {"success": True, "data": mode}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCFuturesClient(config(), transport).place_order("BTCUSDT", OrderSide.BUY, 0.012)
    assert not any(c[0] == "POST" for c in transport.calls)


def test_cross_position_blocks_execution_without_switch():
    transport = FuturesTransport({"/api/v1/private/position/open_positions": {"success": True, "data": [position(openType=2)]}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCFuturesClient(config(), transport).close_position("BTCUSDT", "42")
    assert not any(c[0] == "POST" for c in transport.calls)


def test_oneway_entry_cannot_reverse_manual_position():
    transport = FuturesTransport({"/api/v1/private/position/position_mode": {"success": True, "data": 2}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCFuturesClient(config(position_mode="one_way"), transport).place_order("BTCUSDT", OrderSide.SELL, 0.012, position_side=PositionSide.BOTH)
    assert not any(c[0] == "POST" for c in transport.calls)


def test_hedge_incompatible_side_rejected_before_network():
    transport = FuturesTransport()
    with pytest.raises(ValueError):
        MEXCFuturesClient(config(), transport).place_order("BTCUSDT", OrderSide.SELL, 0.012, position_side=PositionSide.LONG)
    assert transport.calls == []


@pytest.mark.parametrize("status,filled", [(1, "0"), (2, "6"), (4, "6"), (3, "12")])
def test_close_pending_partial_or_no_position_reduction_is_unknown(status, filled):
    transport = FuturesTransport({
        "/api/v1/private/position/open_positions": {"success": True, "data": [position()]},
        "/api/v1/private/order/external/BTC_USDT/stable-close": {"success": True, "data": futures_order(state=status, dealVol=filled)},
    })
    with pytest.raises(UnknownOrderStateError) as exc:
        MEXCFuturesClient(config(), transport).close_position("BTCUSDT", "42", client_order_id="stable-close")
    assert exc.value.order_id == "99" and exc.value.client_order_id == "stable-close"
    assert len([c for c in transport.calls if c[0] == "POST"]) == 1


def test_close_terminal_rejected_no_fill_is_not_success():
    transport = FuturesTransport({"/api/v1/private/order/external/BTC_USDT/stable-close": {"success": True, "data": futures_order(state=5, dealVol="0")}})
    with pytest.raises(ExchangeAPIError):
        MEXCFuturesClient(config(), transport).close_position("BTCUSDT", "42", client_order_id="stable-close")


def test_close_read_failure_preserves_unknown():
    transport = FuturesTransport({"/api/v1/private/order/external/BTC_USDT/stable-close": ExchangeStateUnavailableError("нет данных")})
    with pytest.raises(UnknownOrderStateError):
        MEXCFuturesClient(config(), transport).close_position("BTCUSDT", "42", client_order_id="stable-close")
    assert len([c for c in transport.calls if c[0] == "POST"]) == 1


def test_manual_full_protection_is_readonly_success():
    transport = FuturesTransport({"/api/v1/private/stoporder/list/orders": {"success": True, "data": [stop_order()]}})
    client = MEXCFuturesClient(config(), transport)
    assert client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59000, tp=62000)
    assert client.last_protection_confirmed and not any(c[0] == "POST" for c in transport.calls)


@pytest.mark.parametrize("changed", [dict(vol="6"), dict(lossTrend=1), dict(stopLossType=1), dict(stopLossPrice="58000"), dict(stopLossReverse=1)])
def test_manual_wrong_or_partial_protection_is_not_modified(changed):
    transport = FuturesTransport({"/api/v1/private/stoporder/list/orders": {"success": True, "data": [stop_order(**changed)]}})
    client = MEXCFuturesClient(config(), transport)
    with pytest.raises(ExchangeStateUnavailableError):
        client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59000, tp=62000)
    assert not client.last_protection_confirmed and not any(c[0] == "POST" for c in transport.calls)


def test_owned_stop_changed_manually_invalidates_ownership():
    original = stop_order()
    transport = FuturesTransport({"/api/v1/private/stoporder/list/orders": {"success": True, "data": [stop_order(version=2)]}})
    client = MEXCFuturesClient(config(), transport)
    client.restore_protection_ownership(original)
    with pytest.raises(ExchangeStateUnavailableError):
        client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=60000)
    assert not any(c[0] == "POST" for c in transport.calls)


def test_protection_creation_needs_full_readback():
    transport = FuturesTransport({"/api/v1/private/stoporder/place": {"success": True, "data": 88}})
    client = MEXCFuturesClient(config(), transport)
    with pytest.raises(UnknownOrderStateError):
        client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59000)
    assert not client.last_protection_confirmed
    assert len([c for c in transport.calls if c[0] == "POST"]) == 1


def test_owned_stop_update_preserves_tp_and_verifies_readback():
    current = stop_order()
    def mutate(_, params):
        current.update(stopLossPrice=params["stopLossPrice"], takeProfitPrice=params["takeProfitPrice"], version=2)
        return {"success": True, "data": None}
    transport = FuturesTransport({
        "/api/v1/private/stoporder/list/orders": lambda *_: {"success": True, "data": [dict(current)]},
        "/api/v1/private/stoporder/change_plan_price": mutate,
    })
    client = MEXCFuturesClient(config(), transport)
    client.restore_protection_ownership(dict(current))
    assert client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=60000)
    assert current["takeProfitPrice"] == "62000" and client.last_protection_confirmed


def test_paginated_orders_not_truncated_and_repeated_pages_rejected():
    transport = FuturesTransport({"/pages": lambda _, p: {"success": True, "data": [{"id": n} for n in range(100)] if p["page_num"] == 1 else [{"id": 100}]}})
    client = MEXCFuturesClient(config(), transport)
    assert len(client._pages("/pages")) == 101
    transport.overrides["/pages"] = {"success": True, "data": [{"id": n} for n in range(100)]}
    with pytest.raises(ExchangeStateUnavailableError):
        client._pages("/pages")


def test_cancel_current_format_matching_result_and_readback():
    transport = FuturesTransport({
        "/api/v1/private/order/cancel": {"success": True, "data": [{"orderId": 99, "errorCode": 0}]},
        "/api/v1/private/order/get/99": {"success": True, "data": futures_order(state=4, dealVol="0")},
    })
    client = MEXCFuturesClient(config(), transport)
    client.restore_order_ownership("BTCUSDT", "99")
    assert client.cancel_order("BTCUSDT", "99")
    assert next(c for c in transport.calls if c[0] == "POST")[2] == {"orderIds": [99]}


@pytest.mark.parametrize("results", [[], [{"orderId": 100, "errorCode": 0}], None])
def test_bad_cancel_ack_is_unknown(results):
    transport = FuturesTransport({"/api/v1/private/order/cancel": {"success": True, "data": results}})
    client = MEXCFuturesClient(config(), transport)
    client.restore_order_ownership("BTCUSDT", "99")
    with pytest.raises(UnknownOrderStateError):
        client.cancel_order("BTCUSDT", "99")


@pytest.mark.parametrize("field,value", [("baseAssetPrecision", None), ("baseAssetPrecision", -1), ("baseSizePrecision", "NaN"), ("quoteAmountPrecisionMarket", None), ("isSpotTradingAllowed", "true"), ("orderTypes", ["NEW_TYPE"])])
def test_spot_rules_fail_closed(field, value):
    transport = SpotTransport({("GET", "/api/v3/exchangeInfo"): {"symbols": [spot_rules(**{field: value})]}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCSpotClient(config(), transport).get_instrument_rules("BTCUSDT")


def test_spot_qty_and_cumulative_quote_yield_actual_average():
    order = MEXCSpotClient(config(), SpotTransport())._parse_order(spot_order(status="PARTIALLY_FILLED", Qty="2", executedQty="0.5", cumulativeQuoteQty="30000"))
    assert order.quantity == 2 and order.average_price == 60000


@pytest.mark.parametrize("changed", [dict(type="NEW_TYPE"), dict(status="NEW_STATUS"), dict(Qty="NaN"), dict(executedQty="Infinity"), dict(orderId=None)])
def test_spot_unknown_order_schema_rejected(changed):
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCSpotClient(config(), SpotTransport())._parse_order(spot_order(**changed))


def test_spot_special_type_is_not_silently_limit():
    order = MEXCSpotClient(config(), SpotTransport())._parse_order(spot_order(type="LIMIT_MAKER"))
    assert order.order_type == OrderType.LIMIT_MAKER
    assert order.order_type != OrderType.LIMIT


@pytest.mark.parametrize("quote", ["0", "NaN", "Infinity", "9.99", "100001"])
def test_spot_market_notional_and_invalid_numbers_block_mutation(quote):
    transport = SpotTransport()
    with pytest.raises((ValueError, ExchangeStateUnavailableError)):
        MEXCSpotClient(config(), transport).place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.BUY, OrderType.MARKET, quote_quantity=Decimal(quote)))
    assert not any(c[0] == "POST" for c in transport.calls)


def test_spot_market_sell_notional_enforced():
    transport = SpotTransport()
    with pytest.raises(ValueError):
        MEXCSpotClient(config(), transport).place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.SELL, OrderType.MARKET, base_quantity=Decimal("0.0001")))
    assert not any(c[0] == "POST" for c in transport.calls)


@pytest.mark.parametrize("data", [{}, {"orderId": None}, {"orderId": "abc", "symbol": "ETHUSDT", "type": "MARKET", "side": "BUY"}])
def test_spot_bad_create_ack_is_unknown(data):
    transport = SpotTransport({("POST", "/api/v3/order"): data})
    with pytest.raises(UnknownOrderStateError) as exc:
        MEXCSpotClient(config(), transport).place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.BUY, OrderType.MARKET, quote_quantity=Decimal("25"), client_order_id="stable-spot"))
    assert exc.value.client_order_id == "stable-spot"


def test_spot_lookup_read_failure_not_absorbed():
    transport = SpotTransport({("GET", "/api/v3/order"): ExchangeStateUnavailableError("нет данных")})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCSpotClient(config(), transport)._query_by_client_id("BTCUSDT", "stable-spot")


@pytest.mark.parametrize("mode", ["read_only", "demo"])
def test_spot_test_order_guard_also_blocks_before_network(mode):
    transport = SpotTransport()
    with pytest.raises(ExchangeAPIError):
        MEXCSpotClient(config(execution_mode=mode), transport).place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.BUY, OrderType.MARKET, quote_quantity=Decimal("25"), test_only=True))
    assert transport.calls == []


def test_partial_close_confirms_exact_contract_reduction():
    transport = FuturesTransport()
    transport.overrides["/api/v1/private/position/open_positions"] = lambda *_: {"success": True, "data": [position(holdVol="6" if transport.closed else "12")]}
    client = MEXCFuturesClient(config(), transport)
    assert client.close_position("BTCUSDT", "42", percentage=0.5, client_order_id="stable-close")
    assert transport.last_create["vol"] == "6"
    assert client._positions_cache is None and client._balance_cache is None


def test_close_rejected_lookup_after_ack_is_unknown_not_definite_rejection():
    transport = FuturesTransport({"/api/v1/private/order/external/BTC_USDT/stable-close": ExchangeAPIError("lookup отклонён", code=511)})
    with pytest.raises(UnknownOrderStateError):
        MEXCFuturesClient(config(), transport).close_position("BTCUSDT", "42", client_order_id="stable-close")


def test_protection_split_plans_need_full_sl_and_tp_coverage():
    plans = [stop_order(id=88, vol="6"), stop_order(id=89, vol="6")]
    transport = FuturesTransport({"/api/v1/private/stoporder/list/orders": {"success": True, "data": plans}})
    client = MEXCFuturesClient(config(), transport)
    assert client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59000, tp=62000)
    assert not any(c[0] == "POST" for c in transport.calls)
    transport.overrides["/api/v1/private/stoporder/list/orders"]["data"][1]["takeProfitPrice"] = "63000"
    with pytest.raises(ExchangeStateUnavailableError):
        client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59000, tp=62000)
    assert not client.last_protection_confirmed


def test_protection_separate_volume_cannot_hide_incomplete_sl():
    plan = stop_order(profitLossVolType="SEPARATE", stopLossVol="6", takeProfitVol="12")
    transport = FuturesTransport({"/api/v1/private/stoporder/list/orders": {"success": True, "data": [plan]}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCFuturesClient(config(), transport).set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59000, tp=62000)
    assert not any(c[0] == "POST" for c in transport.calls)


def test_creation_verified_only_after_readback_of_ack_id():
    created = []
    def create(_, params):
        created.append(stop_order(stopLossPrice=params["stopLossPrice"], takeProfitPrice=params["takeProfitPrice"] or "0"))
        return {"success": True, "data": 88}
    transport = FuturesTransport({"/api/v1/private/stoporder/place": create,
        "/api/v1/private/stoporder/list/orders": lambda *_: {"success": True, "data": list(created)}})
    client = MEXCFuturesClient(config(), transport)
    assert client.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59000)
    assert client.last_protection_confirmed and "88" in client._owned_stop_orders
    snapshots = client.get_owned_protection_snapshots()
    assert snapshots[0]["id"] == 88
    snapshots[0]["version"] = 999
    assert client.get_owned_protection_snapshots()[0]["version"] == 1


def test_manual_cancel_and_bulk_cancel_never_touch_unowned_orders():
    transport = FuturesTransport({"/api/v1/private/order/list/open_orders": {"success": True, "data": [futures_order(state=1, dealVol="0")]}})
    client = MEXCFuturesClient(config(), transport)
    with pytest.raises(ExchangeStateUnavailableError):
        client.cancel_order("BTCUSDT", "99")
    assert client.cancel_all_orders("BTCUSDT")
    assert not any(c[0] == "POST" for c in transport.calls)


def test_cancel_matching_ambiguous_result_is_unknown():
    transport = FuturesTransport({"/api/v1/private/order/cancel": {"success": True, "data": [{"orderId": 99, "errorCode": 3016}]}})
    client = MEXCFuturesClient(config(), transport)
    client.restore_order_ownership("BTCUSDT", "99")
    with pytest.raises(UnknownOrderStateError):
        client.cancel_order("BTCUSDT", "99")


def test_risk_limit_includes_manual_pending_entry_volume():
    pending = futures_order(side=1, state=1, vol="1000000", dealVol="0", openType=1, leverage=10)
    transport = FuturesTransport({"/api/v1/private/order/list/open_orders": {"success": True, "data": [pending]}})
    with pytest.raises(ValueError):
        MEXCFuturesClient(config(), transport).place_order("BTCUSDT", OrderSide.BUY, 0.012)
    assert not any(c[0] == "POST" for c in transport.calls)


def test_leverage_existing_position_uses_position_id_and_readback():
    leverage = [10]
    def change(_, params):
        assert params == {"positionId": 42, "leverage": 20}
        leverage[0] = 20
        return {"success": True, "data": None}
    transport = FuturesTransport({"/api/v1/private/position/change_leverage": change,
        "/api/v1/private/position/open_positions": lambda *_: {"success": True, "data": [position(leverage=leverage[0])]}})
    assert MEXCFuturesClient(config(), transport).set_leverage("BTCUSDT", 20, PositionSide.LONG)


def test_leverage_without_position_uses_isolated_side_and_readback():
    transport = FuturesTransport({
        "/api/v1/private/position/open_positions": {"success": True, "data": []},
        "/api/v1/private/position/change_leverage": {"success": True, "data": None},
        "/api/v1/private/position/leverage": {"success": True, "data": [{"positionType": 1, "openType": 1, "leverage": 20}]},
    })
    assert MEXCFuturesClient(config(), transport).set_leverage("BTCUSDT", 20, PositionSide.LONG)
    payload = next(c for c in transport.calls if c[0] == "POST")[2]
    assert payload == {"symbol": "BTC_USDT", "openType": 1, "positionType": 1, "leverage": 20}


@pytest.mark.parametrize("fee_mode", ["NORMAL", "LEVERAGE", "TIERED"])
def test_fees_use_actual_rates_not_original_or_fixed_fallback(fee_mode):
    fees = dict(symbol="BTC_USDT", feeRateMode=fee_mode, realMakerFee="0", realTakerFee="0.0004", originalTakerFee="0.001", minLeverage=1, maxLeverage=50)
    client = MEXCFuturesClient(config(), FuturesTransport({"/api/v1/private/account/tiered_fee_rate/v2": {"success": True, "data": fees}}))
    assert client.get_commission_rate("BTCUSDT").taker == pytest.approx(0.04)
    fees.pop("realTakerFee")
    with pytest.raises(ExchangeStateUnavailableError):
        client.get_commission_rate("BTCUSDT")


def test_limit_max_volume_differs_from_market_max():
    client = MEXCFuturesClient(config(), FuturesTransport())
    rules = client.get_instrument_rules("BTCUSDT")
    assert client._contracts_from_base(rules, 150, limit_order=True) == 150000
    with pytest.raises(ValueError):
        client._contracts_from_base(rules, 150)


def test_spot_ticker_fraction_percent_and_null_quote_volume():
    ticker = dict(symbol="BTCUSDT", lastPrice="60000", bidPrice="59999", askPrice="60001", volume="10", quoteVolume=None,
        priceChange="600", priceChangePercent="0.01", highPrice="61000", lowPrice="59000")
    result = MEXCSpotClient(config(), SpotTransport({("GET", "/api/v3/ticker/24hr"): ticker})).get_ticker("BTCUSDT")
    assert result.price_change_percent_24h == 1 and result.quote_volume_24h == 0


def test_spot_owned_cancel_validates_exact_final_result():
    transport = SpotTransport({("DELETE", "/api/v3/order"): spot_order(status="CANCELED")})
    client = MEXCSpotClient(config(), transport)
    client.restore_order_ownership("BTCUSDT", "abc123")
    assert client.cancel_order("BTCUSDT", "abc123")
    with pytest.raises(ExchangeStateUnavailableError):
        client.cancel_order("BTCUSDT", "abc123")


def test_spot_cancel_pending_or_wrong_id_is_unknown():
    transport = SpotTransport({("DELETE", "/api/v3/order"): spot_order(status="NEW")})
    client = MEXCSpotClient(config(), transport)
    client.restore_order_ownership("BTCUSDT", "abc123")
    with pytest.raises(UnknownOrderStateError):
        client.cancel_order("BTCUSDT", "abc123")


def test_spot_side_price_filter_unknown_semantics_is_failclosed():
    rules = spot_rules(filters=[{"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierUp": "0.2", "askMultiplierDown": "0.2"}])
    transport = SpotTransport({("GET", "/api/v3/exchangeInfo"): {"symbols": [rules]}})
    with pytest.raises(UnsupportedCapabilityError):
        MEXCSpotClient(config(), transport).place_spot_order(SpotOrderRequest("BTCUSDT", OrderSide.BUY, OrderType.LIMIT, base_quantity=Decimal("0.01"), price=Decimal("60000")))
    assert not any(c[0] == "POST" for c in transport.calls)


def test_futures_rest_candles_validate_parallel_arrays():
    transport = FuturesTransport({"/api/v1/contract/kline/BTC_USDT": {"success": True, "data": {"time": [1700000000], "open": [1], "high": [], "low": [1], "close": [1], "vol": [1]}}})
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCFuturesClient(config(), transport).get_klines("BTCUSDT")


def test_monthly_candle_uses_calendar_end_not_thirty_days(monkeypatch):
    from datetime import datetime, timezone
    start = int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp())
    now = int(datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr("src.exchanges.impl.mexc_futures_client.time.time", lambda: now)
    data = dict(time=[start], open=[1], high=[2], low=[1], close=[2], vol=[1])
    client = MEXCFuturesClient(config(supported_intervals={"1M": "Month1"}), FuturesTransport({"/api/v1/contract/kline/BTC_USDT": {"success": True, "data": data}}))
    assert client.get_klines("BTCUSDT", "1M", 1)[0].closed


@pytest.mark.parametrize("code", [-1006, -1007])
def test_spot_execution_unknown_codes_are_not_definite_rejection(code):
    transport = MEXCSpotTransport(config(), Session([Response({"code": code})]))
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError):
        transport.request("POST", "/api/v3/order", {})


def test_http_date_retry_after_is_not_capped():
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    future = datetime.now(timezone.utc) + timedelta(seconds=120)
    delay = MEXCSpotTransport._retry_delay(Response({}, headers={"Retry-After": format_datetime(future)}), 0)
    assert 118 <= delay <= 120


def test_spot_filled_without_actual_quote_price_is_failclosed():
    with pytest.raises(ExchangeStateUnavailableError):
        MEXCSpotClient(config(), SpotTransport())._parse_order(spot_order(status="FILLED", executedQty="0.01", cumulativeQuoteQty="0"))


def test_futures_dynamic_endpoints_share_one_budget():
    class Limiter:
        def __init__(self):
            self.weights, self.delays = [], []
        def acquire(self, weight=1):
            self.weights.append(weight)
        def cooldown(self, seconds):
            self.delays.append(seconds)
    budget = Limiter()
    session = Session([Response({"success": True, "data": {}}), Response({"success": True, "data": {}})])
    transport = MEXCFuturesTransport(config(), session, limiters={"/api/v1/contract/kline/*": budget})
    transport.request("GET", "/api/v1/contract/kline/BTC_USDT")
    transport.request("GET", "/api/v1/contract/kline/ETH_USDT")
    assert budget.weights == [1, 1]


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_mutation_redirect_is_unknown_and_never_followed(status):
    session = Session([Response({}, status=status, headers={"Location": "https://other.invalid"})])
    transport = MEXCSpotTransport(config(), session)
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError):
        transport.request("POST", "/api/v3/order", {})
    assert len(session.calls) == 1 and session.calls[0][2]["allow_redirects"] is False
