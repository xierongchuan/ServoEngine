"""Офлайн core/контрактные объёмы MEXC: только fake transport и временный журнал."""

import copy
import threading
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.execution_coordinator import attach_execution_safety
from src.exchanges.dto.models import OrderSide, OrderType, Position, PositionSide
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError
from src.exchanges.impl.mexc_futures_client import MEXCFuturesClient
from test_mexc import FuturesTransport, config, futures_order, position, stop_order


class AccountTransport(FuturesTransport):
    def __init__(self):
        super().__init__()
        self.hold = Decimal(0)
        self.direction = 1
        self.pid = 42
        self.orders = {}
        self.stops = {}
        self.leverage = 10

    def request(self, method, endpoint, params=None, **kwargs):
        if endpoint in self.overrides:
            return super().request(method, endpoint, params, **kwargs)
        self.calls.append((method, endpoint, copy.deepcopy(params), kwargs))
        if endpoint == "/api/v1/private/position/open_positions":
            return {"success": True, "data": [position(holdVol=str(self.hold), positionType=self.direction,
                positionId=self.pid, leverage=self.leverage)] if self.hold else []}
        if endpoint == "/api/v1/private/order/create":
            self.last_create = dict(params)
            oid = len(self.orders) + 99
            self.orders[params["externalOid"]] = futures_order(orderId=oid, externalOid=params["externalOid"],
                vol=params["vol"], dealVol=params["vol"], side=params["side"], orderType=params["type"])
            self.hold += Decimal(params["vol"]) if params["side"] in {1, 3} else -Decimal(params["vol"])
            self.direction = 1 if params["side"] in {1, 4} else 2
            return {"success": True, "data": {"orderId": oid}}
        if "/order/external/" in endpoint:
            return {"success": True, "data": copy.deepcopy(self.orders.get(endpoint.rsplit("/", 1)[1]))}
        if endpoint == "/api/v1/private/stoporder/list/orders":
            return {"success": True, "data": copy.deepcopy(list(self.stops.values()))}
        if endpoint == "/api/v1/private/stoporder/place":
            self.stops[88] = stop_order(vol=params["vol"], stopLossPrice=params["stopLossPrice"] or "0",
                                       takeProfitPrice=params["takeProfitPrice"] or "0")
            return {"success": True, "data": {"orderId": 88}}
        if endpoint == "/api/v1/private/stoporder/change_plan_price":
            self.stops[params["stopPlanOrderId"]].update(stopLossPrice=params["stopLossPrice"], takeProfitPrice=params["takeProfitPrice"])
            return {"success": True, "data": {}}
        if endpoint == "/api/v1/private/position/change_leverage":
            self.leverage = params["leverage"]
            return {"success": True, "data": {}}
        self.calls.pop()
        return super().request(method, endpoint, params, **kwargs)


def mexc_wrap(tmp_path, transport):
    return attach_execution_safety(MEXCFuturesClient(config(), transport), journal_path=tmp_path / "mexc.sqlite",
        owner_id="mexc-owner", settings={"exchange": "mexc", "execution_mode": "live"})


def test_mexc_contract_entry_partial_close_and_restart(tmp_path):
    transport = AccountTransport()
    client = mexc_wrap(tmp_path, transport)
    assert client.place_order("BTCUSDT", OrderSide.BUY, Decimal(".012"), command_id="entry") == "99"
    assert transport.last_create["vol"] == "12"
    restarted = mexc_wrap(tmp_path, transport)
    restarted.recover()
    assert ("BTCUSDT", "99") in restarted.raw_client._owned_orders
    assert restarted.close_position("BTCUSDT", "42", Decimal(".5"), command_id="half")
    assert transport.last_create["vol"] == "6" and transport.last_create["side"] == 4
    assert restarted.journal.owned(restarted.account_id)[0]["quantity"] == "0.006"
    assert restarted.close_position("BTCUSDT", "42", Decimal(".5"), command_id="half")
    assert len([c for c in transport.calls if c[0] == "POST"]) == 2


@pytest.mark.parametrize("change", ["quantity", "side", "id"])
def test_mexc_manual_race_pre_http_forbids_excess_or_wrong_side_close(tmp_path, monkeypatch, change):
    transport = AccountTransport()
    client = mexc_wrap(tmp_path, transport)
    client.place_order("BTCUSDT", "BUY", ".012", command_id="entry")
    original = client.raw_client.close_position
    def raced(*args, **kwargs):
        if change == "quantity":
            transport.hold += 6
        elif change == "side":
            transport.direction = 2
        else:
            transport.pid = 43
        return original(*args, **kwargs)
    monkeypatch.setattr(client.raw_client, "close_position", raced)
    with pytest.raises(ExchangeStateUnavailableError):
        client.close_position("BTCUSDT", "42", command_id="close")
    assert len([c for c in transport.calls if c[0] == "POST"]) == 1
    assert client.is_symbol_blocked("BTCUSDT")
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.012"


def test_mexc_protection_restart_restores_exact_snapshot_and_detects_manual_edit(tmp_path):
    transport = AccountTransport()
    client = mexc_wrap(tmp_path, transport)
    client.place_order("BTCUSDT", "BUY", ".012", price=60000, sl=59000, tp=62000, command_id="entry")
    assert len(client.journal.protections(client.account_id)) == 1
    restarted = mexc_wrap(tmp_path, transport)
    restarted.recover()
    assert "88" in restarted.raw_client._owned_stop_orders
    assert restarted.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=59500, position_id="42", command_id="trail")
    assert transport.stops[88]["takeProfitPrice"] == "62000"
    transport.stops[88]["version"] += 1
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.get_owned_positions("BTCUSDT")
    assert restarted.is_symbol_blocked("BTCUSDT")
    assert transport.hold == 12


def test_mexc_ack_read_failure_unknown_is_never_resent(tmp_path):
    transport = AccountTransport()
    client = mexc_wrap(tmp_path, transport)
    original = transport.request
    def broken(method, endpoint, params=None, **kwargs):
        if "/order/external/" in endpoint:
            raise ExchangeStateUnavailableError("Адресный lookup недоступен")
        return original(method, endpoint, params, **kwargs)
    transport.request = broken
    with pytest.raises(UnknownOrderStateError):
        client.place_order("BTCUSDT", "BUY", ".012", command_id="entry")
    restarted = mexc_wrap(tmp_path, transport)
    restarted.recover()
    with pytest.raises(UnknownOrderStateError):
        restarted.place_order("BTCUSDT", "BUY", ".012", command_id="entry")
    assert len([c for c in transport.calls if c[0] == "POST"]) == 1


@pytest.mark.parametrize("actual", [False, None, "order-ack", True])
def test_command_success_is_actual_boolean_not_ack(monkeypatch, actual):
    from src.core import executor
    from src.core.commands.executor import CommandExecutor
    from src.core.commands.models import TradeCommand
    execute = Mock(return_value=actual)
    monkeypatch.setattr(executor, "execute_prediction", execute)
    command = TradeCommand.entry("BTCUSDT", "BUY", 50000, .95, strategy="SCALP")
    first = CommandExecutor().execute(command)
    second = CommandExecutor().execute(command)
    assert first.success is (actual is True)
    assert first.executed_price is None and first.executed_quantity is None
    assert execute.call_args_list[0].args[0]["command_id"] == execute.call_args_list[1].args[0]["command_id"]


def test_hold_with_protection_executes_and_unknown_propagates(monkeypatch):
    from src.core import executor
    from src.core.commands.executor import CommandExecutor
    from src.core.commands.models import TradeCommand, TradeAction
    execute = Mock(side_effect=UnknownOrderStateError("UNKNOWN protection"))
    monkeypatch.setattr(executor, "execute_prediction", execute)
    command = TradeCommand("BTCUSDT", TradeAction.HOLD, .9, 50000, stop_loss=49000)
    with pytest.raises(UnknownOrderStateError):
        CommandExecutor().execute(command)
    assert execute.call_count == 1


def scalp_engine():
    from src.core.scalp_engine import ScalpEngine
    engine = ScalpEngine.__new__(ScalpEngine)
    engine.symbol, engine._owner_id = "BTCUSDT", "scalp-a"
    engine._position_open_time = time.time()
    engine._max_hold_minutes, engine._be_timeout_minutes = 100, 0
    engine._partial_tp_enabled, engine._partial_tp_done = True, False
    engine._entry_atr, engine._partial_tp_atr_mult, engine._partial_tp_pct = 10, 1, .5
    engine._signal_gen = SimpleNamespace(check_exit=lambda *_: {"should_close": False})
    engine._trailing = SimpleNamespace(update=lambda *_: None, current_sl=90, is_trailing=False)
    return engine


@pytest.mark.parametrize("confirmed", [True, False, None])
def test_scalp_partial_tp_flag_requires_confirmed_close(confirmed):
    engine = scalp_engine()
    engine._partial_close = lambda *_: confirmed
    p = Position("BTCUSDT", PositionSide.LONG, .01, 100, 0, position_id="p")
    engine._manage_position({"current_price": 120, "atr": 10}, p)
    assert engine._partial_tp_done is (confirmed is True)


def test_scalp_unknown_partial_close_keeps_flag_and_ownership():
    engine = scalp_engine()
    engine._client = SimpleNamespace(close_position=Mock(side_effect=UnknownOrderStateError("partial неизвестен")),
                                         position_command_id=lambda *_: "stable-tp1")
    engine._ownership = Mock()
    p = Position("BTCUSDT", PositionSide.LONG, .01, 100, 0, position_id="p")
    with pytest.raises(UnknownOrderStateError):
        engine._manage_position({"current_price": 120, "atr": 10}, p)
    assert not engine._partial_tp_done
    engine._ownership.release_if_owner.assert_not_called()


def test_tracker_keeps_stored_trade_on_unknown(monkeypatch):
    from src.core import trade_tracker
    tracker = trade_tracker.TradeTracker.__new__(trade_tracker.TradeTracker)
    tracker.active_trades = {"BTCUSDT": {"dealId": "own-p", "amount": ".01"}}
    tracker._mark_dirty = Mock()
    tracker._handle_closed_trade = Mock()
    client = SimpleNamespace(is_symbol_blocked=lambda _: False,
        get_owned_positions=Mock(side_effect=UnknownOrderStateError("Поздний close fill")))
    monkeypatch.setattr("src.core.execution_coordinator.attach_execution_safety", lambda c: c)
    with pytest.raises(UnknownOrderStateError):
        tracker.sync_position("BTC-USDT", None, client)
    assert tracker.active_trades["BTCUSDT"]["execution_status"] == "PAUSED"
    tracker._handle_closed_trade.assert_not_called()


def test_monitor_does_not_swallow_unknown(monkeypatch):
    from src.core import monitor
    client = SimpleNamespace(get_owned_positions=Mock(side_effect=UnknownOrderStateError("Неизвестна защита")))
    monkeypatch.setattr(monitor, "get_exchange_client", lambda: client)
    with pytest.raises(UnknownOrderStateError):
        monitor.monitor_symbol("BTCUSDT")


def test_mexc_unknown_protection_ack_recovers_exact_id_without_resend(tmp_path, monkeypatch):
    transport = AccountTransport()
    client = mexc_wrap(tmp_path, transport)
    original = client.raw_client._get_position_stop_orders
    def unavailable(*args):
        if transport.stops:
            raise ExchangeStateUnavailableError("Readback после ACK недоступен")
        return original(*args)
    monkeypatch.setattr(client.raw_client, "_get_position_stop_orders", unavailable)
    with pytest.raises(UnknownOrderStateError):
        client.place_order("BTCUSDT", "BUY", ".012", price=60000, sl=59000, tp=62000, command_id="entry")
    assert client.is_symbol_blocked("BTCUSDT")
    restarted = mexc_wrap(tmp_path, transport)
    restarted.recover()
    assert not restarted.is_symbol_blocked("BTCUSDT")
    assert "88" in restarted.raw_client._owned_stop_orders
    assert len([c for c in transport.calls if c[0] == "POST"]) == 2


def test_mexc_unknown_close_recovery_proves_zero_without_resend(tmp_path, monkeypatch):
    transport = AccountTransport()
    client = mexc_wrap(tmp_path, transport)
    client.place_order("BTCUSDT", "BUY", ".012", command_id="entry")
    monkeypatch.setattr(client.raw_client, "_query_external", Mock(side_effect=ExchangeStateUnavailableError("Close lookup недоступен")))
    with pytest.raises(UnknownOrderStateError):
        client.close_position("BTCUSDT", "42", command_id="close")
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.012"
    restarted = mexc_wrap(tmp_path, transport)
    restarted.recover()
    assert restarted.closure_confirmed("BTCUSDT", "42")
    assert restarted.close_position("BTCUSDT", "42", command_id="close")
    assert len([c for c in transport.calls if c[0] == "POST"]) == 2


def test_scalp_reads_only_own_closed_envelope_and_fresh_exact_live(tmp_path, monkeypatch):
    from src.core import scalp_engine as module
    from src.exchanges.market_data import StreamKey, StreamStore
    from src.exchanges.market_data_storage import publish_market_snapshot, publish_strategy_view
    from src.exchanges import ws_provider_factory
    from src.trading_settings import validate_trading_settings
    from test_market_data_layer import Clock, T, raw
    clock = Clock()
    monkeypatch.setattr("src.exchanges.market_data.time.time", clock)
    monkeypatch.setattr(module.time, "time", clock)
    monkeypatch.setattr(module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ws_provider_factory, "get_market_snapshot", lambda *args, **kwargs: None)
    settings = validate_trading_settings({"exchange": "bingx", "execution_mode": "demo"})
    engine = scalp_engine()
    engine._client = SimpleNamespace(settings=settings)
    key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
    store = StreamStore(settings={"min_history": 2, "history_limit": 10}, clock=clock)
    snapshot = store.merge(key, [raw(T + i * 60000, 10 + i) for i in range(3)], status="rest")
    publish_market_snapshot(tmp_path, snapshot)
    publish_strategy_view(tmp_path, snapshot, instance_id="scalp-a", closed_only=True, legacy=False)
    assert [r["timestamp"] for r in engine._get_candles(2)] == [T, T + 60000]
    assert engine._get_candles(1, closed_only=False)[0]["timestamp"] == T + 120000
    engine._owner_id = "missing-instance"
    assert engine._get_candles(2) == []
    assert engine._get_candles(1, closed_only=False)
    engine._owner_id = "scalp-a"
    clock.now += 31
    assert engine._get_candles(2) == [] and engine._get_candles(1, closed_only=False) == []
