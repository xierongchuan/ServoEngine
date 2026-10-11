"""Офлайн attached lifecycle: сохранённые команды, GET-only transport, без opt-in входов."""

import copy
import json
from decimal import Decimal

import pytest

from src.core.execution_coordinator import attach_execution_safety
from src.core.strategies.grid.safety import grid_protection_capabilities
from src.exchanges.errors import ExchangeStateUnavailableError, UnsupportedCapabilityError
from src.exchanges.impl.mexc_futures_client import MEXCFuturesClient
from src.trading_settings import validate_trading_settings
from test_mexc import FuturesTransport, config, futures_order, position, stop_order
from test_bingx_safety_regressions import FakeExchange, new_client

D = Decimal


class AttachedExchange(FuturesTransport):
    def __init__(self):
        super().__init__()
        self.entries, self.plans, self.children = {}, {}, {}
        self.manual = {}
        self.mode = 1

    def request(self, method, endpoint, params=None, **kwargs):
        assert method == "GET", "Тест запрещает любые биржевые мутации"
        self.calls.append((method, endpoint, copy.deepcopy(params), kwargs))
        if endpoint == "/api/v1/private/position/position_mode":
            return {"success": True, "data": self.mode}
        if "/order/external/" in endpoint:
            return {"success": True, "data": copy.deepcopy(self.entries[endpoint.rsplit("/", 1)[1]])}
        if "/order/get/" in endpoint:
            return {"success": True, "data": copy.deepcopy(self.children[endpoint.rsplit("/", 1)[1]])}
        if endpoint == "/api/v1/private/stoporder/list/orders":
            return {"success": True, "data": copy.deepcopy(list(self.plans.values()))}
        if endpoint == "/api/v1/private/position/open_positions":
            totals = dict(self.manual)
            for row in self.entries.values():
                side = 1 if row["side"] == 1 else 2
                totals[side] = totals.get(side, D(0)) + D(row["dealVol"])
            for child in self.children.values():
                side = 1 if child["positionId"] == 42 else 2
                totals[side] = totals.get(side, D(0)) - D(child["dealVol"])
            return {"success": True, "data": [position(holdVol=str(q), positionType=s, positionId=42 if s == 1 else 43)
                                                 for s, q in totals.items() if q]}
        self.calls.pop()
        return super().request(method, endpoint, params, **kwargs)


def wrap(tmp_path, exchange, *, position_mode="hedge"):
    return attach_execution_safety(MEXCFuturesClient(config(position_mode=position_mode), exchange), journal_path=tmp_path / "attached.sqlite",
        owner_id="grid", account_id="offline-account", settings={"exchange": "mexc", "execution_mode": "live", "position_mode": position_mode,
                        "grid_mode": "hedged" if position_mode == "hedge" else "inventory"})


def seed(client, exchange, command="saved", *, side="LONG", quantity=".012", with_wire=True, wire_change=None):
    spec = {"side": "BUY" if side == "LONG" else "SELL", "quantity": quantity, "price": "60000",
            "order_type": "LIMIT", "position_side": side if client.settings["position_mode"] == "hedge" else "BOTH",
            "owned_side": side, "leverage": 10, "gross_limit": ".1",
            "sl": "59000" if side == "LONG" else "61000", "tp": "62000" if side == "LONG" else "58000",
            "attached_protection": "exchange"}
    intent, _ = client.journal.prepare(client.account_id, client.owner_id, "BTCUSDT", "grid_entry", spec,
                                      command_id=command, grid_queue=True)
    oid, sid = str(99 + len(exchange.entries)), str(88 + len(exchange.entries))
    vol = str(D(quantity) / D(".001"))
    exchange.entries[intent["client_id"]] = futures_order(orderId=oid, externalOid=intent["client_id"], side=1 if side == "LONG" else 3,
        orderType=1, price="60000", vol=vol, dealVol="0", state=2, openType=1, positionId=42 if side == "LONG" else 43)
    exchange.plans[sid] = stop_order(id=sid, orderId=oid, positionId=42 if side == "LONG" else 43,
        positionType=1 if side == "LONG" else 2, vol="0", stopLossPrice=spec["sl"], takeProfitPrice=spec["tp"])
    if with_wire:
        params = {"externalOid": intent["client_id"], "type": 1,
            "symbol": "BTC_USDT", "side": 1 if side == "LONG" else 3, "vol": vol, "price": "60000",
            "openType": 1, "positionMode": 1 if client.settings["position_mode"] == "hedge" else 2, "leverage": 10,
            "stopLossPrice": spec["sl"], "takeProfitPrice": spec["tp"], "lossTrend": 2, "profitTrend": 2}
        params.update(wire_change or {})
        client.journal.wire(client.account_id, command, {"status": "PREPARED", "method": "POST",
            "endpoint": "/api/v1/private/order/create", "request_params": params})
    client.journal.finish(client.account_id, command, "ACK", order_id=oid,
                          result={"filled_quantity": "0", "terminal": False})
    return intent["client_id"], sid


def fill(exchange, cid, sid, vol):
    row = exchange.entries[cid]
    row.update(dealVol=str(vol), state=3 if D(str(vol)) == D(row["vol"]) else 2)
    exchange.plans[sid].update(vol=str(vol), version=exchange.plans[sid]["version"] + 1)


def receipt(client, command="saved"):
    return client.journal.get(client.account_id, command)["result"]["attachment"]


def owned(client):
    return sum((D(r["quantity"]) for r in client.journal.owned(client.account_id, owner=client.owner_id)), D(0))


@pytest.mark.parametrize("exchange", ["bingx", "mexc"])
@pytest.mark.parametrize("mode", ["hedge", "one_way"])
def test_docs_contract_cannot_be_enabled_by_local_opt_in(exchange, mode):
    settings = validate_trading_settings({"exchange": exchange, "position_mode": mode,
        "grid_mode": "inventory", "spot": {"allow_local_protection": True}})
    capabilities = grid_protection_capabilities(settings)
    assert not capabilities["entry_allowed"] and not capabilities["atomic_partial_protection"]
    assert capabilities["max_safe_entry_parallelism"] == 0
    assert capabilities["attached_entry_fields"] and capabilities["unresolved"]


def test_explicit_sltp_entry_still_fails_before_io_or_prepared(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    with pytest.raises(UnsupportedCapabilityError, match="атомарную"):
        client.place_grid_order("BTC-USDT", "BUY", ".012", price=60000, sl=59000, tp=62000,
                                gross_limit=".1", command_id="fresh")
    assert not exchange.calls and not client.journal.intents(client.account_id)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("position_mode", ["hedge", "one_way"])
def test_pending_partial_full_restart_are_read_only(tmp_path, side, position_mode):
    exchange = AttachedExchange()
    exchange.mode = 1 if position_mode == "hedge" else 2
    client = wrap(tmp_path, exchange, position_mode=position_mode)
    cid, sid = seed(client, exchange, side=side)
    client.reconcile_grid("BTCUSDT")
    assert receipt(client)["state"] == "PENDING" and not receipt(client)["confirmed"]
    fill(exchange, cid, sid, 4)
    client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".004") and receipt(client)["confirmed"]
    assert client.is_grid_symbol_blocked("BTCUSDT")
    fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    restarted = wrap(tmp_path, exchange, position_mode=position_mode)
    restarted.recover()
    assert restarted.verify_protection("BTCUSDT")
    assert owned(restarted) == D(".012")
    assert receipt(restarted)["plan_ids"] == [sid]
    assert not restarted.is_grid_symbol_blocked("BTCUSDT")
    assert all(c[0] == "GET" for c in exchange.calls)


@pytest.mark.parametrize("change", ["missing", "id", "parent", "position", "side", "sl", "tp", "volume", "reference", "version", "duplicate"])
def test_manual_protection_drift_never_adopts_similar_plan(tmp_path, change):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    row = exchange.plans[sid]
    changes = {"id": ("id", "123"), "parent": ("orderId", "manual"), "position": ("positionId", 55),
        "side": ("positionType", 2), "sl": ("stopLossPrice", "58000"), "tp": ("takeProfitPrice", "63000"),
        "volume": ("vol", "11"), "reference": ("lossTrend", 1), "version": ("version", 9)}
    if change == "missing":
        exchange.plans.clear()
    elif change == "duplicate":
        exchange.plans["other"] = dict(row, id="other")
    else:
        key, value = changes[change]
        row[key] = value
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012")
    assert client.is_grid_symbol_blocked("BTCUSDT")
    assert not receipt(client)["confirmed"]


def test_missing_protection_preserves_initial_fill_and_durable_pause(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    exchange.plans.clear()
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012")
    assert client.journal.get(client.account_id, "saved")["state"] == "FILLED"
    assert client.journal.blocked(client.account_id, "BTCUSDT") == "GRID_ATTACHED_PROTECTION_UNKNOWN"
    restarted = wrap(tmp_path, exchange)
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.recover()
    assert owned(restarted) == D(".012")


def test_partial_cancel_keeps_active_fill_protection(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 4)
    exchange.entries[cid]["state"] = 4
    client.reconcile_grid("BTCUSDT")
    assert client.journal.get(client.account_id, "saved")["state"] == "PARTIAL"
    assert receipt(client)["confirmed"] and owned(client) == D(".004")
    exchange.plans[sid].update(state=2, isFinished=1)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".004")


def test_no_fill_cancel_requires_terminal_plan(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    exchange.entries[cid]["state"] = 4
    exchange.plans[sid].update(state=2, isFinished=1)
    client.reconcile_grid("BTCUSDT")
    assert receipt(client)["state"] == "CANCELED" and owned(client) == 0


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_trigger_uses_exact_child_and_zeroes_only_proven_owned_fill(tmp_path, side):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange, side=side)
    fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    exchange.plans[sid].update(state=3, isFinished=1, triggerSide=2, placeOrderId="500", realityVol="12")
    exchange.children["500"] = futures_order(orderId="500", side=4 if side == "LONG" else 2,
        positionId=42 if side == "LONG" else 43, vol="12", dealVol="12", state=3)
    restarted = wrap(tmp_path, exchange)
    restarted.recover()
    assert owned(restarted) == 0 and receipt(restarted)["state"] == "TRIGGERED"
    assert receipt(restarted)["closed_quantity"] == ".012" or D(receipt(restarted)["closed_quantity"]) == D(".012")
    restarted.reconcile_grid("BTCUSDT")
    assert owned(restarted) == 0


@pytest.mark.parametrize("change", [{"orderId": "wrong"}, {"side": 1}, {"positionId": 50}, {"vol": "13", "dealVol": "13"}, {"state": 2, "dealVol": "6"}])
def test_trigger_child_wrong_or_partial_keeps_last_ownership(tmp_path, change):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    exchange.plans[sid].update(state=3, isFinished=1, triggerSide=2, placeOrderId="500", realityVol="12")
    exchange.children["500"] = futures_order(orderId="500", **change) if "orderId" not in change else futures_order(**change)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012") and client.is_grid_symbol_blocked("BTCUSDT")


def test_manual_position_drift_is_not_attributed_to_protection_trigger(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    exchange.manual[1] = D(3)
    with pytest.raises(ExchangeStateUnavailableError, match="агрегированным"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012")


def test_missing_wire_never_promotes_echo_to_protection_proof(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange, with_wire=False)
    fill(exchange, cid, sid, 12)
    with pytest.raises(ExchangeStateUnavailableError, match="durable wire"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012") and not receipt(client)["confirmed"]


def test_multi_level_both_sides_aggregate_without_resizing_or_post(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    levels = [seed(client, exchange, command=str(i), side=side) for i, side in enumerate(["LONG", "LONG", "SHORT"])]
    for cid, sid in levels:
        fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".036") and client.verify_protection("BTCUSDT")
    rows = client.journal.owned(client.account_id)
    assert {r["side"]: D(r["quantity"]) for r in rows} == {"LONG": D(".024"), "SHORT": D(".012")}
    before = len(exchange.calls)
    with pytest.raises(UnsupportedCapabilityError):
        client.place_reduce_order("BTCUSDT", "SELL", ".004", price=61000, position_id="42")
    with pytest.raises(UnsupportedCapabilityError):
        client.close_position("BTCUSDT", "42", command_id="close")
    with pytest.raises(UnsupportedCapabilityError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=58000, position_id="42", command_id="resize")
    assert not client.journal.get(client.account_id, "close") and not client.journal.get(client.account_id, "resize")
    assert all(c[0] == "GET" for c in exchange.calls[before:])


@pytest.mark.parametrize("change", [{"symbol": "ETH_USDT"}, {"side": 3}, {"vol": "13"}, {"price": "61000"},
    {"openType": 2}, {"positionMode": 2}, {"leverage": 20}, {"reduceOnly": True}, {"positionId": 42},
    {"externalOid": "other"}, {"type": 5}, {"stopLossPrice": "58000"}, {"profitTrend": 1}])
def test_durable_wire_must_match_complete_entry(tmp_path, change):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange, wire_change=change)
    fill(exchange, cid, sid, 12)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012") and not receipt(client)["confirmed"]
    assert client.is_grid_symbol_blocked("BTCUSDT")


@pytest.mark.parametrize("missing", [False, True])
def test_no_fill_cancel_does_not_accept_active_or_missing_plan(tmp_path, missing):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    exchange.entries[cid]["state"] = 4
    if missing:
        exchange.plans.clear()
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0 and client.is_grid_symbol_blocked("BTCUSDT")


@pytest.mark.parametrize("filled", [0, 4, 12])
def test_attached_cancel_never_mutates_without_preservation_contract(tmp_path, filled):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    if filled:
        fill(exchange, cid, sid, filled)
    with pytest.raises(UnsupportedCapabilityError, match="concurrent partial fill"):
        client.cancel_order("BTCUSDT", exchange.entries[cid]["orderId"], command_id="cancel")
    assert not client.journal.get(client.account_id, "cancel") and not exchange.calls


def test_attachment_pause_never_overwrites_or_resolves_unrelated_pause(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    client.journal.pause(client.account_id, "BTCUSDT", "Ручная пауза оператора")
    row = exchange.plans.pop(sid)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert client.journal.blocked(client.account_id, "BTCUSDT") == "Ручная пауза оператора"
    exchange.plans[sid] = row
    client.reconcile_grid("BTCUSDT")
    assert receipt(client)["confirmed"]
    assert client.journal.blocked(client.account_id, "BTCUSDT") == "Ручная пауза оператора"


def test_attachment_failure_fill_and_pause_commit_before_restore_crash(tmp_path, monkeypatch):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    exchange.plans.clear()
    def crash(row):
        raise RuntimeError("Эмуляция crash после commit")
    monkeypatch.setattr(client, "_restore_position", crash)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012") and not receipt(client)["confirmed"]
    assert client.journal.blocked(client.account_id, "BTCUSDT") == "GRID_ATTACHED_PROTECTION_UNKNOWN"


def test_unknown_wire_restart_queries_exact_entry_without_post(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    client.journal.finish(client.account_id, "saved", "UNKNOWN")
    restarted = wrap(tmp_path, exchange)
    restarted.recover()
    assert restarted.journal.get(restarted.account_id, "saved")["state"] == "ACK"
    assert receipt(restarted)["state"] == "PENDING"
    fill(exchange, cid, sid, 12)
    restarted.recover()
    assert owned(restarted) == D(".012")
    assert all(call[0] == "GET" for call in exchange.calls)


def test_mixed_legacy_and_attached_aggregate_is_not_confirmed(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    client.journal.finish(client.account_id, "saved", "ACK", ownership=[{
        "owner": client.owner_id, "symbol": "BTCUSDT", "position_id": "42", "side": "LONG",
        "quantity": ".005", "snapshot": {}}])
    exchange.manual[1] = D(5)
    fill(exchange, cid, sid, 12)
    with pytest.raises(ExchangeStateUnavailableError, match="смешанный"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".017") and not receipt(client)["confirmed"]


def test_multi_level_trigger_closes_only_exact_parent_volume(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    first, second = [seed(client, exchange, command=str(i)) for i in range(2)]
    for cid, sid in (first, second):
        fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    exchange.plans[first[1]].update(state=3, isFinished=1, triggerSide=1, placeOrderId="500", realityVol="12")
    exchange.children["500"] = futures_order(orderId="500")
    client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012") and client.verify_protection("BTCUSDT")
    assert receipt(client, "0")["state"] == "TRIGGERED" and receipt(client, "1")["confirmed"]
    exchange.plans[first[1]]["placeOrderId"] = "501"
    exchange.children["501"] = exchange.children.pop("500")
    exchange.children["501"]["orderId"] = "501"
    with pytest.raises(ExchangeStateUnavailableError, match="терминальный trigger snapshot"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012")


def test_trigger_during_pending_partial_entry_keeps_symbol_unknown(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 4)
    client.reconcile_grid("BTCUSDT")
    exchange.plans[sid].update(state=3, isFinished=1, triggerSide=2, placeOrderId="500", realityVol="4")
    exchange.children["500"] = futures_order(orderId="500", vol="4", dealVol="4")
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".004") and client.is_grid_symbol_blocked("BTCUSDT")


def test_other_owner_cannot_mutate_attached_exposure(tmp_path):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    other = attach_execution_safety(MEXCFuturesClient(config(), exchange), journal_path=tmp_path / "attached.sqlite",
        owner_id="other", account_id=client.account_id, settings={"exchange": "mexc", "execution_mode": "live"})
    with pytest.raises(UnsupportedCapabilityError, match="атомарного lifecycle"):
        other.set_leverage("BTCUSDT", 20, command_id="other-leverage")
    assert not other.journal.get(other.account_id, "other-leverage")


class BingAttachedExchange(FakeExchange):
    def request(self, method, url, **kwargs):
        assert method == "get", "Тест запрещает любые биржевые мутации"
        return super().request(method, url, **kwargs)


def seed_bingx(tmp_path, monkeypatch, *, side="LONG", wire_change=None, position_mode="hedge"):
    monkeypatch.setenv("BINGX_API_KEY", "attached-offline-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "attached-offline-secret")
    exchange = BingAttachedExchange(hedge=position_mode == "hedge")
    exchange.install(monkeypatch)
    client = attach_execution_safety(new_client(hedge=position_mode == "hedge"), journal_path=tmp_path / "bing-attached.sqlite",
        owner_id="grid", account_id="bing-offline", settings={"exchange": "bingx", "execution_mode": "demo", "position_mode": position_mode,
                    "grid_mode": "hedged" if position_mode == "hedge" else "inventory"})
    spec = {"side": "BUY" if side == "LONG" else "SELL", "quantity": ".012", "price": "50000",
        "order_type": "LIMIT", "position_side": side if position_mode == "hedge" else "BOTH",
        "owned_side": side, "leverage": 10, "gross_limit": ".1",
        "sl": "48000" if side == "LONG" else "52000", "tp": "52000" if side == "LONG" else "48000",
        "attached_protection": "exchange"}
    intent, _ = client.journal.prepare(client.account_id, client.owner_id, "BTCUSDT", "grid_entry", spec,
                                      command_id="saved", grid_queue=True)
    params = {"symbol": "BTC-USDT", "side": spec["side"], "positionSide": spec["position_side"], "type": "LIMIT",
        "quantity": ".012", "price": "50000", "clientOrderId": intent["client_id"]}
    for key, field, kind in (("sl", "stopLoss", "STOP_MARKET"), ("tp", "takeProfit", "TAKE_PROFIT_MARKET")):
        params[field] = json.dumps({"type": kind, "stopPrice": spec[key], "workingType": "MARK_PRICE"})
    row = exchange.add_order(params)
    wire_params = dict(params, **(wire_change or {}))
    client.journal.wire(client.account_id, "saved", {"status": "PREPARED", "method": "POST",
        "endpoint": "/openApi/swap/v2/trade/order", "request_params": wire_params})
    client.journal.finish(client.account_id, "saved", "ACK", order_id=row["orderId"],
                          result={"filled_quantity": "0", "terminal": False})
    return client, exchange, row


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("position_mode", ["hedge", "one_way"])
def test_bingx_pending_echo_never_promises_partial_protection(tmp_path, monkeypatch, side, position_mode):
    client, exchange, row = seed_bingx(tmp_path, monkeypatch, side=side, position_mode=position_mode)
    client.reconcile_grid("BTCUSDT")
    assert receipt(client)["state"] == "PENDING" and not receipt(client)["confirmed"]
    row.update(executedQty=".004", status="PARTIALLY_FILLED")
    exchange.amount, exchange.direction = D(".004"), side
    with pytest.raises(ExchangeStateUnavailableError, match="durable REST"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".004") and not receipt(client)["confirmed"]
    assert client.is_grid_symbol_blocked("BTCUSDT") and not exchange.mutations
    restarted = attach_execution_safety(new_client(hedge=position_mode == "hedge"), journal_path=tmp_path / "bing-attached.sqlite",
        owner_id="grid", account_id=client.account_id, settings={"exchange": "bingx", "execution_mode": "demo", "position_mode": position_mode,
                    "grid_mode": "hedged" if position_mode == "hedge" else "inventory"})
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.recover()
    assert owned(restarted) == D(".004") and not exchange.mutations


@pytest.mark.parametrize("attachment", [None, "invalid-json", {"type": "STOP_MARKET", "stopPrice": "47000", "workingType": "MARK_PRICE"},
    {"type": "STOP", "stopPrice": "48000", "workingType": "MARK_PRICE"},
    {"type": "STOP_MARKET", "stopPrice": "48000", "workingType": "CONTRACT_PRICE"}])
def test_bingx_changed_or_missing_echo_pauses_without_adoption(tmp_path, monkeypatch, attachment):
    client, exchange, row = seed_bingx(tmp_path, monkeypatch)
    row["stopLoss"] = attachment
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0 and client.is_grid_symbol_blocked("BTCUSDT") and not exchange.mutations


@pytest.mark.parametrize("change", [{"symbol": "ETH-USDT"}, {"side": "SELL"}, {"quantity": ".013"}, {"price": "51000"},
    {"positionSide": "BOTH"}, {"reduceOnly": "true"}, {"closePosition": "true"}, {"stopLoss": "{}"}])
def test_bingx_original_wire_identity_required(tmp_path, monkeypatch, change):
    client, exchange, row = seed_bingx(tmp_path, monkeypatch, wire_change=change)
    with pytest.raises(ExchangeStateUnavailableError, match="durable wire"):
        client.reconcile_grid("BTCUSDT")
    assert not receipt(client)["confirmed"] and not exchange.mutations


def test_bingx_cancel_echo_does_not_prove_attached_cleanup(tmp_path, monkeypatch):
    client, exchange, row = seed_bingx(tmp_path, monkeypatch)
    row["status"] = "CANCELLED"
    with pytest.raises(ExchangeStateUnavailableError, match="cleanup"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0 and client.is_grid_symbol_blocked("BTCUSDT") and not exchange.mutations


@pytest.mark.parametrize("field", ["volType", "lossTrend", "profitTrend", "stopLossType", "stopLossReverse", "priceProtect", "profitLossVolType"])
def test_incomplete_mexc_snapshot_never_defaults_protection_semantics(tmp_path, field):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    exchange.plans[sid].pop(field)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012") and not receipt(client)["confirmed"]


@pytest.mark.parametrize("change", [{"side": 2}, {"openType": 2}])
def test_mexc_readback_cannot_turn_close_or_cross_into_attached_entry(tmp_path, change):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    exchange.entries[cid].update(change)
    with pytest.raises(ExchangeStateUnavailableError, match="opening side/isolated"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0 and client.is_grid_symbol_blocked("BTCUSDT")


@pytest.mark.parametrize("exchange_name", ["mexc", "bingx"])
def test_account_mode_drift_blocks_saved_attached_lifecycle(tmp_path, monkeypatch, exchange_name):
    if exchange_name == "mexc":
        exchange = AttachedExchange()
        client = wrap(tmp_path, exchange)
        seed(client, exchange)
        exchange.mode = 2
    else:
        client, exchange, _ = seed_bingx(tmp_path, monkeypatch)
        exchange.hedge = False
    with pytest.raises(ExchangeStateUnavailableError, match="mode|режим"):
        client.reconcile_grid("BTCUSDT")
    assert client.is_grid_symbol_blocked("BTCUSDT") and not receipt(client)["confirmed"]


def test_terminal_parent_lookup_outage_is_durable_unknown_without_repost(tmp_path, monkeypatch):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    client.reconcile_grid("BTCUSDT")
    request = exchange.request
    def unavailable(method, endpoint, *args, **kwargs):
        if "/order/external/" in endpoint:
            raise ExchangeStateUnavailableError("Офлайн-эмуляция недоступного exact-ID lookup")
        return request(method, endpoint, *args, **kwargs)
    monkeypatch.setattr(exchange, "request", unavailable)
    restarted = wrap(tmp_path, exchange)
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.recover()
    assert owned(restarted) == D(".012") and not receipt(restarted)["confirmed"]
    assert restarted.journal.blocked(restarted.account_id, "BTCUSDT") == "GRID_ATTACHED_PROTECTION_UNKNOWN"
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.recover()
    assert all(call[0] == "GET" for call in exchange.calls)


def test_trigger_child_binding_survives_unknown_receipt(tmp_path, monkeypatch):
    exchange = AttachedExchange()
    client = wrap(tmp_path, exchange)
    cid, sid = seed(client, exchange)
    fill(exchange, cid, sid, 12)
    exchange.plans[sid].update(state=3, isFinished=1, triggerSide=2, placeOrderId="500", realityVol="12")
    exchange.children["500"] = futures_order(orderId="500")
    client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0
    query = client._query
    def outage(intent):
        raise ExchangeStateUnavailableError("Офлайн-ошибка чтения")
    monkeypatch.setattr(client, "_query", outage)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert receipt(client)["state"] == "UNKNOWN"
    monkeypatch.setattr(client, "_query", query)
    exchange.plans[sid]["placeOrderId"] = "501"
    exchange.children["501"] = exchange.children.pop("500")
    exchange.children["501"]["orderId"] = "501"
    with pytest.raises(ExchangeStateUnavailableError, match="терминальный trigger snapshot"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0 and receipt(client)["plans"][0]["placeOrderId"] == "500"


def test_saved_one_way_opposite_positions_never_become_valid_aggregate(tmp_path):
    exchange = AttachedExchange()
    exchange.mode = 2
    client = wrap(tmp_path, exchange, position_mode="one_way")
    for i, side in enumerate(["LONG", "SHORT"]):
        cid, sid = seed(client, exchange, command=str(i), side=side)
        fill(exchange, cid, sid, 12)
    with pytest.raises(ExchangeStateUnavailableError, match="противоположные позиции в One-way"):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0 and client.is_grid_symbol_blocked("BTCUSDT")
