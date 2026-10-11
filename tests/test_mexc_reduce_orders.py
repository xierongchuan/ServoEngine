"""Raw reduce MEXC: доказанный snapshot, один send и отказ при неоднозначности."""

from decimal import Decimal
import hashlib
import hmac
import json

import pytest
import requests

from src.exchanges.dto.models import OrderSide, OrderType, PositionSide
from src.exchanges.errors import (
    ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError,
    UnsupportedCapabilityError,
)
from src.exchanges.impl.mexc_futures_client import MEXCFuturesClient
from src.exchanges.impl.mexc_transport import MEXCFuturesTransport
from test_mexc import config, contract, position, futures_order, FuturesTransport, Response, Session


CREATE = "/api/v1/private/order/create"
POSITIONS = "/api/v1/private/position/open_positions"
MODE = "/api/v1/private/position/position_mode"
RULES = "/api/v1/contract/detail/country"


def make_client(*, rows=None, mode="hedge", overrides=None, settings=None, own=True,
                own_side=PositionSide.LONG, own_quantity="0.012"):
    replies = dict(overrides or {})
    if rows is not None:
        replies[POSITIONS] = {"success": True, "data": rows}
    if mode == "one_way" and MODE not in replies:
        replies[MODE] = {"success": True, "data": 2}
    transport = FuturesTransport(replies)
    client = MEXCFuturesClient(config(position_mode=mode, **(settings or {})), transport)
    if own:
        client.register_owned_position("BTC-USDT", "42", own_quantity, position_side=own_side)
    return client, transport


def reduce(client, **kwargs):
    params = dict(symbol="BTC-USDT", side=OrderSide.SELL, quantity=Decimal("0.006"),
                  order_type=OrderType.LIMIT, price=Decimal("60000.1"),
                  position_side=PositionSide.LONG, position_id="42",
                  client_order_id="grid-command-42", command_id="journal-42")
    params.update(kwargs)
    return client.place_reduce_order(**params)


def posts(transport):
    return [call for call in transport.calls if call[0] == "POST"]


@pytest.mark.parametrize("ptype,pos_side,side,code", [
    (1, PositionSide.LONG, OrderSide.SELL, 4),
    (2, PositionSide.SHORT, OrderSide.BUY, 2),
])
def test_hedge_limit_close_ack_only(ptype, pos_side, side, code):
    client, transport = make_client(rows=[position(positionType=ptype)], own_side=pos_side,
                                   settings={"protection": {"trigger_price": "invalid"}})
    before = dict(client._owned_positions)
    assert reduce(client, side=side, position_side=pos_side, leverage=10) == "99"
    assert transport.last_create == {
        "symbol": "BTC_USDT", "price": "60000.1", "vol": "6", "side": code,
        "type": 1, "openType": 1, "positionId": 42, "externalOid": "grid-command-42",
        "positionMode": 1,
    }
    assert posts(transport) == [("POST", CREATE, transport.last_create, {"private": True, "mutation": True})]
    assert transport.calls[-1][1] == CREATE
    assert client._owned_positions == before
    assert client._owned_orders == {("BTCUSDT", "99")}
    assert client._positions_cache is None and client._balance_cache is None
    assert not any("stoporder" in c[1] or "/external/" in c[1] or "fee_rate" in c[1]
                   or "change_leverage" in c[1] for c in transport.calls)


@pytest.mark.parametrize("ptype,actual_side,side", [
    (1, PositionSide.LONG, OrderSide.SELL), (2, PositionSide.SHORT, OrderSide.BUY),
])
@pytest.mark.parametrize("both", [False, True])
def test_one_way_close_uses_reduce_only(ptype, actual_side, side, both):
    client, transport = make_client(rows=[position(positionType=ptype)], mode="one_way", own_side=actual_side)
    assert reduce(client, side=side, position_side=PositionSide.BOTH if both else actual_side) == "99"
    assert transport.last_create["reduceOnly"] is True
    assert transport.last_create["side"] == (4 if ptype == 1 else 2)
    assert transport.last_create["positionMode"] == 2
    assert transport.last_create["positionId"] == 42
    assert transport.last_create["openType"] == 1
    assert "leverage" not in transport.last_create


def test_market_reduce_has_no_limit_price():
    client, transport = make_client()
    assert reduce(client, order_type=OrderType.MARKET, price=None) == "99"
    assert transport.last_create["price"] == "0" and transport.last_create["type"] == 5


@pytest.mark.parametrize("settings", [
    {"execution_mode": "read_only"}, {"execution_mode": "demo"},
    {"margin_mode": "cross"}, {"is_demo": True}, {"api_key": ""},
])
def test_reduce_guard_before_private_reads(settings):
    client, transport = make_client(settings=settings)
    with pytest.raises((ExchangeAPIError, ExchangeStateUnavailableError)):
        reduce(client)
    assert transport.calls == []


@pytest.mark.parametrize("changes", [
    {"reduce_only": False}, {"reduce_only": 1}, {"reduce_only": "true"},
    {"client_order_id": None}, {"client_order_id": ""}, {"client_order_id": "a" * 33},
    {"client_order_id": "manual id"}, {"position_side": None}, {"position_id": None},
    {"position_id": True}, {"position_id": "0"}, {"command_id": " "},
    {"command_id": False}, {"command_id": []}, {"order_type": "STOP_MARKET"},
])
def test_invalid_intent_blocked_before_http(changes):
    client, transport = make_client()
    with pytest.raises((ValueError, ExchangeStateUnavailableError, UnsupportedCapabilityError)):
        reduce(client, **changes)
    assert transport.calls == []


@pytest.mark.parametrize("changes", [
    {"symbol": "ETHUSDT"}, {"position_id": "43"}, {"client_order_id": "se-owned-looking"},
])
def test_id_or_prefix_does_not_adopt_position(changes):
    client, transport = make_client(own=False)
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client, **changes)
    assert transport.calls == []


@pytest.mark.parametrize("changes", [
    {"position_side": PositionSide.BOTH}, {"position_side": None},
    {"quantity": "NaN"}, {"quantity": "0"}, {"position_id": -1}, {"symbol": "BTCUSD"},
])
def test_register_ownership_rejects_invalid_snapshot(changes):
    client, transport = make_client(own=False)
    params = dict(symbol="BTCUSDT", position_id=42, quantity="0.012", position_side=PositionSide.LONG)
    params.update(changes)
    with pytest.raises((ValueError, ExchangeStateUnavailableError)):
        client.register_owned_position(**params)
    assert client._owned_positions == {} and transport.calls == []


@pytest.mark.parametrize("rows", [
    [], [position(positionId=43)], [position(positionType=2)],
    [position(holdVol="13")], [position(holdVol="11")],
    [position(openType=2)], [position(), position(positionId=43, openType=2)],
    [position(), position()], [position(frozenVol="13")],
])
def test_actual_position_change_or_cross_fails_closed(rows):
    client, transport = make_client(rows=rows)
    with pytest.raises((ValueError, ExchangeStateUnavailableError)):
        reduce(client)
    assert not posts(transport)


def test_symbol_replaced_at_same_position_id_fails_closed():
    def rules_reply(_, params):
        return {"success": True, "data": contract(symbol=params["symbol"])}
    client, transport = make_client(rows=[position(symbol="ETH_USDT")], overrides={RULES: rules_reply})
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client)
    assert not posts(transport)


@pytest.mark.parametrize("mode,actual", [("hedge", 2), ("one_way", 1), ("hedge", None)])
def test_actual_account_mode_must_match_without_switching(mode, actual):
    client, transport = make_client(mode=mode, overrides={MODE: {"success": True, "data": actual}})
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client)
    assert not posts(transport)


def test_one_way_rejects_ambiguous_two_positions():
    client, transport = make_client(mode="one_way", rows=[position(), position(positionId=43, positionType=2)])
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client, position_side=PositionSide.BOTH)
    assert not posts(transport)


def test_snapshot_is_fresh_despite_valid_cached_positions():
    client, transport = make_client(settings={"positions_cache_ttl": 300})
    client.get_positions()
    transport.overrides[POSITIONS] = {"success": True, "data": [position(holdVol="13")]}
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client)
    assert len([c for c in transport.calls if c[1] == POSITIONS]) == 2
    assert not posts(transport)


@pytest.mark.parametrize("ptype,own_side,wrong_side", [
    (1, PositionSide.LONG, OrderSide.BUY), (2, PositionSide.SHORT, OrderSide.SELL),
])
def test_never_swap_entry_side_to_close(ptype, own_side, wrong_side):
    client, transport = make_client(rows=[position(positionType=ptype)], own_side=own_side)
    with pytest.raises(ValueError):
        reduce(client, side=wrong_side, position_side=own_side)
    assert not posts(transport)


@pytest.mark.parametrize("mode,pos_side", [("hedge", PositionSide.BOTH), ("hedge", PositionSide.SHORT), ("one_way", PositionSide.SHORT)])
def test_wrong_position_side_is_not_inferred(mode, pos_side):
    client, transport = make_client(mode=mode)
    with pytest.raises(ValueError):
        reduce(client, position_side=pos_side)
    assert not posts(transport)


@pytest.mark.parametrize("quantity", ["0.0065", "0.0005", "0", "-0.001", "NaN", "Infinity", "0.013", True])
def test_exact_positive_contract_quantity_and_hold_bounds(quantity):
    client, transport = make_client()
    with pytest.raises((ValueError, ExchangeStateUnavailableError)):
        reduce(client, quantity=quantity)
    assert not posts(transport)


@pytest.mark.parametrize("metadata,quantity,kind,price", [
    ({"volUnit": "2"}, "0.005", "LIMIT", "60000.1"),
    ({"minVol": "7"}, "0.006", "LIMIT", "60000.1"),
    ({"maxVol": "5", "minVol": "1"}, "0.006", "MARKET", None),
    ({"limitMaxVol": "5"}, "0.006", "LIMIT", "60000.1"),
    ({"apiAllowed": False}, "0.006", "LIMIT", "60000.1"),
])
def test_rules_and_order_specific_maximum(metadata, quantity, kind, price):
    client, transport = make_client(overrides={RULES: {"success": True, "data": contract(**metadata)}})
    with pytest.raises((ValueError, ExchangeAPIError)):
        reduce(client, quantity=quantity, order_type=kind, price=price)
    assert not posts(transport)


def test_limit_uses_limit_max_vol_not_market_max_vol():
    client, transport = make_client(overrides={RULES: {"success": True, "data": contract(maxVol="5", limitMaxVol="10")}})
    assert reduce(client) == "99"
    assert transport.last_create["vol"] == "6"


def test_frozen_manual_orders_are_not_cancelled():
    client, transport = make_client(rows=[position(frozenVol="7")])
    with pytest.raises(ValueError):
        reduce(client)
    assert not posts(transport)
    assert reduce(client, quantity="0.005") == "99"
    assert len(posts(transport)) == 1 and posts(transport)[0][1] == CREATE


@pytest.mark.parametrize("price", [None, "60000.15", 0, "-1", "NaN", True])
def test_limit_price_exact_positive_tick(price):
    client, transport = make_client()
    with pytest.raises((ValueError, ExchangeStateUnavailableError)):
        reduce(client, price=price)
    assert not posts(transport)


def test_market_rejects_price_instead_of_dropping_it():
    client, transport = make_client()
    with pytest.raises(ValueError):
        reduce(client, order_type=OrderType.MARKET)
    assert not posts(transport)


def test_leverage_mismatch_blocks_without_changing_account():
    client, transport = make_client()
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client, leverage=11)
    assert not posts(transport)


@pytest.mark.parametrize("error_class", [UnknownOrderStateError, ExchangeAPIError])
def test_error_propagates_same_object_one_send_with_command_metadata(error_class):
    fault = error_class("Инъекция сбоя")
    client, transport = make_client(overrides={CREATE: fault})
    with pytest.raises(error_class) as caught:
        reduce(client)
    assert caught.value is fault
    assert fault.command_id == "journal-42" and fault.position_id == "42"
    assert fault.reduce_only is True and fault.request_params == posts(transport)[0][2]
    if error_class is UnknownOrderStateError:
        assert fault.client_order_id == fault.external_oid == "grid-command-42"
        assert fault.symbol == "BTCUSDT"
    assert len(posts(transport)) == 1 and transport.calls[-1][1] == CREATE
    assert not client._owned_orders
    assert client._positions_cache is None and client._balance_cache is None
    assert client._owned_positions["42"][2] == Decimal("0.012")


@pytest.mark.parametrize("response", [
    {"success": True, "data": 99}, {"success": True}, {"success": True, "data": {}},
    {"success": True, "data": {"orderId": None}}, {"success": True, "data": {"orderId": True}},
    {"success": True, "data": {"orderId": "0"}}, {"success": True, "data": {"orderId": "x"}},
    {"success": "true", "data": {"orderId": 99}},
])
def test_invalid_ack_is_unknown_no_retry_or_lookup(response):
    client, transport = make_client(overrides={CREATE: response})
    with pytest.raises(UnknownOrderStateError) as caught:
        reduce(client)
    assert caught.value.client_order_id == "grid-command-42"
    assert caught.value.command_id == "journal-42"
    assert len(posts(transport)) == 1 and transport.calls[-1][1] == CREATE
    assert not client._owned_orders


@pytest.mark.parametrize("echo", [
    {"symbol": "ETH_USDT"}, {"positionId": 43}, {"externalOid": "other"},
    {"side": 1}, {"side": True}, {"type": 5}, {"orderType": 5}, {"openType": 2},
    {"positionMode": 2}, {"vol": "7"}, {"vol": "NaN"}, {"price": "60000.2"},
    {"reduceOnly": True}, {"reduceOnly": "false"},
])
def test_ack_echo_mismatch_is_unknown_with_known_order_id(echo):
    client, transport = make_client(overrides={CREATE: {"success": True, "data": {"orderId": "99", **echo}}})
    with pytest.raises(UnknownOrderStateError) as caught:
        reduce(client)
    assert caught.value.order_id == "99" and caught.value.command_id == "journal-42"
    assert len(posts(transport)) == 1 and not client._owned_orders


def test_ack_numeric_equivalence_and_one_way_boolean():
    echo = {"orderId": "99", "positionId": "42", "side": "4", "type": "1", "openType": "1",
            "positionMode": "2", "vol": "6.0", "price": "60000.10", "reduceOnly": True}
    client, transport = make_client(mode="one_way", overrides={CREATE: {"success": True, "data": echo}})
    assert reduce(client, position_side=PositionSide.BOTH) == "99"
    assert len(posts(transport)) == 1


def test_ack_registers_order_for_existing_owned_cancel_path():
    client, transport = make_client(overrides={
        "/api/v1/private/order/cancel": {"success": True, "data": [{"orderId": 99, "errorCode": 0}]},
        "/api/v1/private/order/get/99": {"success": True, "data": futures_order(state=4, orderType=1, dealVol="0", dealAvgPrice="0")},
    })
    assert reduce(client) == "99"
    assert client.cancel_order("BTCUSDT", "99") is True
    assert posts(transport)[1][2] == {"orderIds": [99]}
    assert not client._owned_orders


@pytest.mark.parametrize("fault", [
    requests.exceptions.ChunkedEncodingError("обрыв"), requests.exceptions.ReadTimeout("таймаут"),
    Response({}, status=503), Response(ValueError("не JSON")),
])
def test_raw_reduce_real_transport_fake_http_one_mutation_and_signature(tmp_path, monkeypatch, fault):
    session = Session([
        Response({"success": True, "data": 1}),
        Response({"success": True, "data": [position()]}),
        Response({"success": True, "data": contract()}), fault,
    ])
    cfg = config(rate_limit_data_dir=tmp_path)
    transport = MEXCFuturesTransport(cfg, session)
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 1700000000000)
    client = MEXCFuturesClient(cfg, transport)
    client.register_owned_position("BTCUSDT", 42, "0.012", position_side=PositionSide.LONG)
    with pytest.raises(UnknownOrderStateError) as caught:
        reduce(client)
    assert caught.value.command_id == "journal-42"
    assert caught.value.client_order_id == "grid-command-42"
    assert len(session.calls) == 4 and session.calls[-1][0] == "POST"
    kwargs = session.calls[-1][2]
    payload = json.loads(kwargs["data"])
    assert payload == caught.value.request_params
    target = "fake-key1700000000000" + kwargs["data"]
    assert kwargs["headers"]["Signature"] == hmac.new(b"fake-secret", target.encode(), hashlib.sha256).hexdigest()
    assert kwargs["allow_redirects"] is False
    assert not client._owned_orders
