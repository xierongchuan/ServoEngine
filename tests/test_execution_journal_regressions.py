"""Офлайн-журнал: рестарт, UNKNOWN, собственный объём, ACK/fill и pre-HTTP durable запись."""

import sqlite3
from decimal import Decimal

import pytest

from src.core.execution_journal import ExecutionJournal, stable_client_id
from src.core.execution_coordinator import attach_execution_safety
from src.core.execution_helpers import size_from_budget
from src.exchanges.dto.models import Balance, OrderType
from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from test_bingx_safety_regressions import FakeExchange, new_client

SETTINGS = {"exchange": "bingx", "execution_mode": "demo"}


@pytest.fixture
def exchange(monkeypatch):
    monkeypatch.setenv("BINGX_API_KEY", "execution-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "execution-fake-secret")
    monkeypatch.setenv("SERVO_DATA_DIR", "/tmp/execution-test-data")
    fake = FakeExchange()
    fake.install(monkeypatch)
    return fake


def wrap(path, *, owner="strategy-a", settings=None):
    return attach_execution_safety(new_client(), journal_path=path, settings=settings or SETTINGS, owner_id=owner)


def test_journal_full_sync_and_stable_ids(tmp_path):
    path = tmp_path / "journal.sqlite"
    journal = ExecutionJournal(path)
    row, fresh = journal.prepare("account", "owner", "BTC-USDT", "entry", {"quantity": Decimal(".01")}, command_id="cmd1")
    assert fresh and row["client_id"] == stable_client_id("account", "cmd1") and len(row["client_id"]) <= 32
    restored = ExecutionJournal(path)
    assert restored.blocked("account", "BTCUSDT")
    same, fresh = restored.prepare("account", "owner", "BTCUSDT", "entry", {"quantity": Decimal(".01")}, command_id="cmd1")
    assert not fresh and same["client_id"] == row["client_id"]
    with journal._connect() as db:
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_shared_account_pending_blocks_other_owner(tmp_path):
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    journal.prepare("a", "one", "BTCUSDT", "entry", {}, command_id="first")
    with pytest.raises(ExchangeStateUnavailableError):
        journal.prepare("a", "two", "BTCUSDT", "close", {}, command_id="second")
    assert journal.prepare("different-account", "two", "BTCUSDT", "entry", {}, command_id="second")[1]


def test_secrets_and_nonfinite_not_persisted(tmp_path):
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    for payload in ({"api_key": "secret"}, {"quantity": float("nan")}, {"signature": "secret"}):
        with pytest.raises(ValueError):
            journal.prepare("a", "o", "BTCUSDT", "entry", payload)
    assert not journal.intents("a")


def test_prepared_is_committed_before_http_and_wire_payload(exchange, tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    original = exchange.request
    def checked(method, url, **kwargs):
        if method == "post":
            check = ExecutionJournal(path)
            intents = check.intents(client.account_id, pending_only=True)
            assert len(intents) == 1 and intents[0]["state"] == "PREPARED"
            assert check.wires(client.account_id, intents[0]["command_id"])[-1]["event"]["request_params"]["quantity"] == "0.01"
        return original(method, url, **kwargs)
    monkeypatch.setattr(exchange, "request", checked)
    assert client.place_order("BTCUSDT", "BUY", ".01", command_id="entry1") == "1"
    assert client.journal.get(client.account_id, "entry1")["state"] == "FILLED"
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.01"


def test_restart_restores_ownership_and_no_double_entry(exchange, tmp_path):
    path = tmp_path / "journal.sqlite"
    first = wrap(path)
    first.place_order("BTCUSDT", "BUY", ".02", command_id="entry1")
    restarted = wrap(path)
    restarted.recover()
    assert restarted.raw_client._owned_positions["p1"][1] == Decimal(".02")
    assert restarted.place_order("BTCUSDT", "BUY", ".02", command_id="entry1") == "1"
    assert len(exchange.mutations) == 1
    assert restarted.close_position("BTCUSDT", "p1", .5, command_id="close1")
    assert restarted.journal.owned(restarted.account_id)[0]["quantity"] == "0.010"
    assert len(exchange.mutations) == 2


def test_ack_limit_not_fill_and_restart_symbol_blocking(exchange, tmp_path):
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    client.place_order("BTCUSDT", "BUY", ".01", order_type=OrderType.LIMIT, price=49000, command_id="limit1")
    assert client.journal.get(client.account_id, "limit1")["state"] == "ACK"
    assert not client.journal.owned(client.account_id)
    restarted = wrap(path)
    restarted.recover()
    assert restarted.is_symbol_blocked("BTCUSDT")
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.place_order("BTCUSDT", "BUY", ".01", command_id="different-command")
    assert len(exchange.mutations) == 1


def test_limit_cancellation_zero_fill_resolves_entry(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    oid = client.place_order("BTCUSDT", "BUY", ".01", order_type="LIMIT", price=49000, command_id="limit1")
    assert client.wait_for_entry("BTCUSDT", oid, timeout=0) is False
    assert client.journal.get(client.account_id, "limit1")["state"] == "RESOLVED"
    assert not client.is_symbol_blocked("BTCUSDT") and not client.journal.owned(client.account_id)
    assert len(exchange.mutations) == 2


def test_limit_fill_is_protected_after_fill_not_ack(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    oid = client.place_order("BTCUSDT", "BUY", ".01", order_type="LIMIT", price=49000, sl=48000, tp=51000, command_id="limit1")
    assert len(exchange.mutations) == 1
    exchange.orders[oid].update(status="FILLED", executedQty="0.01")
    exchange.amount = Decimal(".01")
    assert client.wait_for_entry("BTCUSDT", oid, timeout=0)
    assert len(exchange.mutations) == 3 and not client.is_symbol_blocked("BTCUSDT")
    assert len(client.journal.protections(client.account_id)) == 2


def test_late_unknown_retains_intent_and_never_resends(exchange, tmp_path):
    path = tmp_path / "journal.sqlite"
    exchange.late_fill = True
    client = wrap(path)
    with pytest.raises(UnknownOrderStateError) as caught:
        client.place_order("BTCUSDT", "BUY", ".01", command_id="late1")
    assert caught.value.command_id == "late1" and caught.value.account_id == client.account_id
    assert caught.value.journal_path == str(path) and caught.value.owner_id == client.owner_id
    assert client.is_symbol_blocked("BTCUSDT")
    restarted = wrap(path)
    restarted.recover()
    with pytest.raises(UnknownOrderStateError):
        restarted.place_order("BTCUSDT", "BUY", ".01", command_id="late1")
    assert len(exchange.mutations) == 1


@pytest.mark.parametrize("change", ["size", "side", "id"])
def test_manual_changes_pause_no_adoption_or_close(change, exchange, tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    client.place_order("BTCUSDT", "BUY", ".02", command_id="entry1")
    if change == "size":
        exchange.amount += Decimal(".01")
    elif change == "side":
        exchange.direction = "SHORT"
    else:
        original = exchange.request
        def moved(method, url, **kwargs):
            result = original(method, url, **kwargs)
            if "/positions" in url:
                result.payload["data"][0]["positionId"] = "manual-new-id"
            return result
        monkeypatch.setattr(exchange, "request", moved)
    restarted = wrap(path)
    restarted.recover()
    assert restarted.is_symbol_blocked("BTCUSDT")
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.close_position("BTCUSDT", "p1", command_id="close1")
    assert len(exchange.mutations) == 1 and restarted.journal.owned(restarted.account_id)[0]["quantity"] == "0.02"


def test_foreign_position_is_not_adopted(exchange, tmp_path):
    exchange.amount = Decimal(".02")
    client = wrap(tmp_path / "journal.sqlite")
    assert client.get_owned_positions() == {}
    with pytest.raises(UnsupportedCapabilityError):
        client.close_position("BTCUSDT", "p1")
    assert not exchange.mutations


def test_protection_snapshots_survive_restart(exchange, tmp_path):
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, tp=51000, command_id="entry1")
    assert len(client.journal.protections(client.account_id)) == 2
    restarted = wrap(path)
    restarted.recover()
    assert restarted.get_owned_positions("BTCUSDT")["BTCUSDT"]
    assert "2" in restarted.raw_client._owned_orders and "3" in restarted.raw_client._owned_orders
    assert len(exchange.mutations) == 3


def test_external_protection_cancel_defaults_pause(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, command_id="entry1")
    exchange.orders["2"]["status"] = "CANCELLED"
    with pytest.raises(UnknownOrderStateError):
        client.get_owned_positions("BTCUSDT")
    assert client.is_symbol_blocked("BTCUSDT") and exchange.amount == Decimal(".02")
    assert len(exchange.mutations) == 2


def test_budget_zero_never_falls_back_to_equity(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path / "journal.sqlite")
    monkeypatch.setattr(client.raw_client, "get_balance", lambda: Balance(total_balance=10000, available_balance=0))
    with pytest.raises(ExchangeStateUnavailableError):
        size_from_budget(client, "BTCUSDT", 50000, 10, 5)
    assert not exchange.mutations


def test_budget_rounds_down_and_does_not_inflate_minimum(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path / "journal.sqlite")
    monkeypatch.setattr(client.raw_client, "get_balance", lambda: Balance(total_balance=10000, available_balance=100))
    qty = size_from_budget(client, "BTCUSDT", 50000, 100, 5, fee_pct=".05")
    assert qty == Decimal(".009") and qty * 50000 / 5 < 100
    with pytest.raises(ExchangeStateUnavailableError):
        size_from_budget(client, "BTCUSDT", 50000, ".1", 5, minimum_margin=5)


def test_raw_bypass_is_blocked_after_attach(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    with pytest.raises(UnsupportedCapabilityError):
        client.make_request("post", "/openApi/swap/v2/trade/order", {})
    with pytest.raises(ExchangeStateUnavailableError):
        client.raw_client._make_request("post", "/openApi/swap/v2/trade/order", {})
    assert not exchange.mutations


def test_exact_external_client_id_is_used_and_persisted(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    assert client.place_order("BTCUSDT", "BUY", ".01", client_order_id="external-stable") == "1"
    row = client.journal.get(client.account_id, "external-stable")
    assert row["client_id"] == exchange.orders["1"]["clientOrderId"] == "external-stable"
    assert client.place_order("BTCUSDT", "BUY", ".01", client_order_id="external-stable") == "1"
    assert len(exchange.mutations) == 1


def test_replay_zero_fill_close_is_not_success(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    client.place_order("BTCUSDT", "BUY", ".01", command_id="entry")
    client.journal.prepare(client.account_id, client.owner_id, "BTCUSDT", "close",
                           {"position_id": "p1", "percentage": "1"}, command_id="close")
    client.journal.finish(client.account_id, "close", "RESOLVED", order_id="rejected-close",
                          result={"close_confirmed": False, "filled_quantity": "0"})
    assert client.close_position("BTCUSDT", "p1", percentage=1, command_id="close") is False
    assert exchange.amount == Decimal(".01") and len(exchange.mutations) == 1


def test_cancel_late_fill_replay_is_not_cancel_success(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path / "journal.sqlite")
    oid = client.place_order("BTCUSDT", "BUY", ".01", order_type="LIMIT", price=49000, command_id="entry")
    def late_fill(*_):
        exchange.orders[oid].update(status="FILLED", executedQty="0.01")
        exchange.amount = Decimal(".01")
        raise UnknownOrderStateError("Отмена могла пересечься с fill")
    monkeypatch.setattr(client.raw_client, "cancel_order", late_fill)
    assert client.cancel_order("BTCUSDT", oid, command_id="cancel") is False
    assert client.cancel_order("BTCUSDT", oid, command_id="cancel") is False
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.01"
    assert client.journal.get(client.account_id, "entry")["state"] == "FILLED"


def test_partial_limit_cancel_retains_quantity_and_pending_protection(exchange, tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.execution_coordinator.time.sleep", lambda _: None)
    client = wrap(tmp_path / "journal.sqlite")
    oid = client.place_order("BTCUSDT", "BUY", ".02", order_type="LIMIT", price=49000, sl=48000, command_id="entry")
    exchange.orders[oid].update(status="PARTIALLY_FILLED", executedQty="0.01")
    exchange.amount = Decimal(".01")
    with pytest.raises(UnknownOrderStateError):
        client.wait_for_entry("BTCUSDT", oid, timeout=0)
    intent = client.journal.get(client.account_id, "entry")
    assert intent["state"] == "PARTIAL" and intent["result"]["filled_quantity"] == "0.01"
    assert exchange.orders[oid]["status"] == "CANCELLED"
    assert client.is_symbol_blocked("BTCUSDT")
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.01"
    protection = client.journal.protections(client.account_id)
    assert len(protection) == 1 and Decimal(protection[0]["snapshot"]["request_params"]["quantity"]) == Decimal(".01")
    mutations = len(exchange.mutations)
    with pytest.raises(UnknownOrderStateError):
        client.wait_for_entry("BTCUSDT", oid, timeout=0)
    assert len(exchange.mutations) == mutations


def test_stale_lower_fill_never_reduces_persistent_ownership(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    oid = client.place_order("BTCUSDT", "BUY", ".02", order_type="LIMIT", price=49000, command_id="entry")
    exchange.orders[oid].update(status="PARTIALLY_FILLED", executedQty="0.01")
    exchange.amount = Decimal(".01")
    intent = client.journal.get(client.account_id, "entry")
    client._settle_entry(intent, client._query(intent))
    exchange.orders[oid]["executedQty"] = "0.005"
    with pytest.raises(ExchangeStateUnavailableError):
        client._settle_entry(intent, client._query(intent))
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.01"


def test_crash_after_fill_before_protection_is_blocked_and_can_complete_plan(exchange, tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    def crash(*args, **kwargs):
        raise RuntimeError("Процесс завершился после durable fill")
    monkeypatch.setattr(client, "set_sl_tp", crash)
    with pytest.raises(RuntimeError):
        client.place_order("BTCUSDT", "BUY", ".02", sl=49000, tp=51000, command_id="entry")
    assert client.journal.get(client.account_id, "entry")["state"] == "FILLED"
    assert client.is_symbol_blocked("BTCUSDT")
    restarted = wrap(path)
    restarted.recover()
    assert restarted.is_symbol_blocked("BTCUSDT") and len(exchange.mutations) == 1
    assert restarted.wait_for_entry("BTCUSDT", "1", timeout=0)
    assert not restarted.is_symbol_blocked("BTCUSDT") and len(exchange.mutations) == 3


def test_external_cancel_close_policy_only_exact_owned_volume(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite", settings={**SETTINGS, "protection": {"failure_policy": "close"}})
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, command_id="entry")
    exchange.orders["2"]["status"] = "CANCELLED"
    with pytest.raises(UnknownOrderStateError):
        client.get_owned_positions("BTCUSDT")
    assert exchange.amount == 0
    assert Decimal(exchange.mutations[-1][2]["quantity"]) == Decimal("0.02")
    assert client.is_symbol_blocked("BTCUSDT")


def test_external_cancel_close_policy_refuses_manual_quantity(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite", settings={**SETTINGS, "protection": {"failure_policy": "close"}})
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, command_id="entry")
    exchange.orders["2"]["status"] = "CANCELLED"
    exchange.amount += Decimal(".01")
    with pytest.raises(ExchangeStateUnavailableError):
        client.get_owned_positions("BTCUSDT")
    assert exchange.amount == Decimal(".03") and len(exchange.mutations) == 2


def test_adapter_receipt_replay_shape_and_no_resend(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    count = []
    def submit(cid):
        count.append(cid)
        return {"order_id": "domain-1"}
    receipt = {"state": "RESOLVED", "result": {"verified": True}, "order_id": "domain-1"}
    call = lambda: client.run_verified_operation("BTCUSDT", "domain_operation", {"test": True}, submit,
                                                 lambda ack: receipt, command_id="adapter")
    assert call() == call() == receipt
    assert len(count) == 1


def test_unsent_prepared_recovery_is_not_unknown_or_resend(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    client.journal.prepare(client.account_id, client.owner_id, "BTCUSDT", "entry", {}, command_id="unsent")
    client.recover()
    assert client.journal.get(client.account_id, "unsent")["state"] == "REJECTED"
    assert not client.is_symbol_blocked("BTCUSDT") and not exchange.mutations


@pytest.mark.parametrize("sl,tp", [("nan", None), (None, "Infinity"), (-1, None), (51000, 49000)])
def test_invalid_protection_is_rejected_before_entry(exchange, tmp_path, sl, tp):
    client = wrap(tmp_path / "journal.sqlite")
    with pytest.raises(ExchangeStateUnavailableError):
        client.place_order("BTCUSDT", "BUY", ".01", price=50000, sl=sl, tp=tp)
    assert not exchange.mutations


def test_crash_after_close_fill_before_protection_cleanup_keeps_block(exchange, tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, command_id="entry")
    def crash(*_):
        raise RuntimeError("Сбой после close fill до cleanup readback")
    monkeypatch.setattr(client, "_verify_closed_protection", crash)
    with pytest.raises(RuntimeError):
        client.close_position("BTCUSDT", "p1", command_id="close")
    assert client.journal.get(client.account_id, "close")["state"] == "FILLED"
    assert not client.closure_confirmed("BTCUSDT", "p1")
    restarted = wrap(path)
    restarted.recover()
    assert restarted.closure_confirmed("BTCUSDT", "p1")
    assert not restarted.journal.protections(restarted.account_id)
    assert len(exchange.mutations) == 4


def test_attach_refuses_changed_account_or_journal(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    assert attach_execution_safety(client.raw_client) is client
    with pytest.raises(ExchangeStateUnavailableError):
        attach_execution_safety(client, account_id="another-account")
    with pytest.raises(ExchangeStateUnavailableError):
        attach_execution_safety(client.raw_client, journal_path=tmp_path / "different.sqlite")


def test_raw_live_cannot_be_attached_as_demo(exchange, tmp_path):
    with pytest.raises(ExchangeStateUnavailableError):
        attach_execution_safety(new_client(mode="live"), journal_path=tmp_path / "journal.sqlite", settings=SETTINGS)
    assert not exchange.calls


def test_position_lifecycle_command_prevents_second_tp1_after_restart(exchange, tmp_path):
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    client.place_order("BTCUSDT", "BUY", ".02", command_id="entry")
    token = client.position_command_id("BTCUSDT", "p1", "scalp:tp1")
    assert client.close_position("BTCUSDT", "p1", .5, command_id=token)
    restarted = wrap(path)
    restarted.recover()
    assert restarted.position_command_id("BTCUSDT", "p1", "scalp:tp1") == token
    assert restarted.close_position("BTCUSDT", "p1", .5, command_id=token)
    assert exchange.amount == Decimal(".01") and len(exchange.mutations) == 2


def _hold_symbol_lock(path, ready, release):
    journal = ExecutionJournal(path)
    with journal.symbol_lock("shared-account", "BTCUSDT"):
        ready.send(True)
        release.recv()


def test_symbol_lock_is_exclusive_between_processes_and_releases(tmp_path):
    import multiprocessing
    context = multiprocessing.get_context("fork")
    ready_parent, ready_child = context.Pipe()
    release_parent, release_child = context.Pipe()
    path = tmp_path / "journal.sqlite"
    journal = ExecutionJournal(path)
    process = context.Process(target=_hold_symbol_lock, args=(path, ready_child, release_child))
    process.start()
    try:
        assert ready_parent.poll(5) and ready_parent.recv()
        with pytest.raises(ExchangeStateUnavailableError):
            with journal.symbol_lock("shared-account", "BTC-USDT"):
                pytest.fail("Второй процесс получил тот же символ")
        release_parent.send(True)
        process.join(5)
        assert process.exitcode == 0
        with journal.symbol_lock("shared-account", "BTCUSDT"):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_scalp_restores_protection_tp1_flag_and_open_time(exchange, tmp_path):
    import threading
    from src.core.scalp_engine import ScalpEngine, TrailingStopManager
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, tp=51000, command_id="entry")
    token = client.position_command_id("BTCUSDT", "p1", "scalp:tp1")
    assert client.close_position("BTCUSDT", "p1", .5, command_id=token)
    restarted = wrap(path)
    restarted.recover()
    engine = ScalpEngine.__new__(ScalpEngine)
    engine.symbol, engine._owner_id, engine._client = "BTCUSDT", "strategy-a", restarted
    engine._lock, engine._position, engine._position_open_time = threading.RLock(), None, 0
    engine._trailing, engine._ownership, engine._tracker = TrailingStopManager(), None, None
    engine._partial_tp_done = False
    engine._sync_position()
    assert engine._partial_tp_done and engine._position is not None
    assert engine._trailing.current_sl == 49000 and engine._trailing.initial_tp == 51000
    assert engine._position_open_time == restarted.get_position_execution_snapshot("BTCUSDT", "p1")["snapshot"]["opened_at"]


def test_executor_unknown_close_preserves_symbol_ownership(exchange, tmp_path, monkeypatch):
    from unittest.mock import Mock
    from src.core import executor
    client = wrap(tmp_path / "journal.sqlite")
    client.place_order("BTCUSDT", "BUY", ".02", command_id="entry")
    ownership = Mock()
    ownership.get_owner.return_value = None
    monkeypatch.setattr(executor, "get_exchange_client", lambda: client)
    monkeypatch.setattr("src.core.position_ownership.get_position_ownership_store", lambda: ownership)
    monkeypatch.setattr(executor, "MIN_CONFIDENCE_THRESHOLD", 0)
    exchange.late_fill = True
    with pytest.raises(UnknownOrderStateError):
        executor.execute_prediction({"symbol": "BTCUSDT", "action": "close", "confidence": 1,
                                     "position_id": "p1", "command_id": "close"})
    ownership.release_if_owner.assert_not_called()
    assert client.is_symbol_blocked("BTCUSDT")
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.02"


def test_executor_market_actual_fill_protection_and_decimal_budget(exchange, tmp_path, monkeypatch):
    from unittest.mock import Mock
    from src.core import executor
    from src import config
    client = wrap(tmp_path / "journal.sqlite")
    ownership = Mock()
    ownership.get_owner.return_value = None
    ownership.try_acquire.return_value = (True, None)
    monkeypatch.setattr(executor, "get_exchange_client", lambda: client)
    monkeypatch.setattr("src.core.position_ownership.get_position_ownership_store", lambda: ownership)
    monkeypatch.setattr(config, "LEVERAGE", 10)
    monkeypatch.setattr(config, "MIN_TRADE_AMOUNT_USDT", 5)
    monkeypatch.setattr(config, "TRADING_FEE_TAKER", 0)
    monkeypatch.setattr(executor, "MIN_CONFIDENCE_THRESHOLD", 0)
    result = executor.execute_prediction({"symbol": "BTCUSDT", "action": "buy", "current_price": 50000,
        "confidence": 1, "size_pct": 1, "stop_loss": 49000, "take_profit": 51000, "command_id": "core-entry"})
    assert result is True and exchange.amount == Decimal(".02")
    assert len(client.journal.protections(client.account_id)) == 2
    assert client.journal.get(client.account_id, "core-entry")["state"] == "FILLED"


def test_executor_does_not_select_first_hedge_side(exchange, tmp_path, monkeypatch):
    from unittest.mock import Mock
    from src.core import executor
    from src.exchanges.dto.models import Position, PositionSide
    client = wrap(tmp_path / "journal.sqlite")
    rows = [Position("BTCUSDT", PositionSide.LONG, .01, 50000, 0, position_id="long"),
            Position("BTCUSDT", PositionSide.SHORT, .01, 50000, 0, position_id="short")]
    monkeypatch.setattr(client, "get_owned_positions", lambda _: {"BTCUSDT": rows})
    monkeypatch.setattr(executor, "get_exchange_client", lambda: client)
    ownership = Mock()
    ownership.get_owner.return_value = None
    monkeypatch.setattr("src.core.position_ownership.get_position_ownership_store", lambda: ownership)
    with pytest.raises(ExchangeStateUnavailableError):
        executor.execute_prediction({"symbol": "BTCUSDT", "action": "hold", "stop_loss": 49000})
    update = Mock(return_value=True)
    monkeypatch.setattr(client, "set_sl_tp", update)
    assert executor.execute_prediction({"symbol": "BTCUSDT", "action": "hold", "stop_loss": 51000,
                                       "position_side": "SHORT", "command_id": "short-sl"})
    assert update.call_args.args[1] == PositionSide.SHORT and update.call_args.kwargs["position_id"] == "short"
    assert not exchange.mutations


def test_adapter_ack_reconciliation_is_read_only_and_does_not_resend(exchange, tmp_path):
    client = wrap(tmp_path / "journal.sqlite")
    submit_ids = []
    receipt = client.run_verified_operation("BTCUSDT", "inventory_entry", {"quantity": ".01"},
        lambda cid: submit_ids.append(cid) or "ack", lambda ack: {"state": "ACK", "result": {}, "order_id": "spot-1"}, command_id="adapter")
    assert receipt["state"] == "ACK" and client.is_symbol_blocked("BTCUSDT")
    def unsafe_verify(intent):
        client.raw_client._make_request("post", "/openApi/swap/v2/trade/order", {})
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_verified_operation("adapter", unsafe_verify)
    assert client.is_symbol_blocked("BTCUSDT") and not exchange.mutations
    result = client.reconcile_verified_operation("adapter", lambda intent: {
        "state": "FILLED", "result": {"filled_quantity": ".01", "inventory_proven": True}, "order_id": intent["order_id"]})
    assert result["state"] == "FILLED" and not client.is_symbol_blocked("BTCUSDT")
    assert len(submit_ids) == 1 and not client.journal.owned(client.account_id)


@pytest.mark.parametrize("hedge", [True, False])
def test_coordinator_hedge_and_one_way_protected_close(exchange, tmp_path, hedge):
    exchange.hedge = hedge
    settings = {**SETTINGS, "position_mode": "hedge" if hedge else "one_way", "grid_mode": "hedged" if hedge else "inventory"}
    client = attach_execution_safety(new_client(hedge=hedge), journal_path=tmp_path / "journal.sqlite",
                                     settings=settings, owner_id="strategy-a")
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, tp=51000, command_id="entry")
    assert client.close_position("BTCUSDT", "p1", .5, command_id="partial")
    assert client.close_position("BTCUSDT", "p1", command_id="full")
    assert client.closure_confirmed("BTCUSDT", "p1")
    close_orders = [params for method, endpoint, params in exchange.mutations if endpoint.endswith("/order") and method == "post" and params.get("type") == "MARKET" and params["side"] == "SELL"]
    assert len(close_orders) == 2
    if not hedge:
        assert all(params.get("reduceOnly") == "true" and params["positionSide"] == "BOTH" for params in close_orders)


def test_lost_conditional_ack_without_documented_client_id_never_adopts_or_resends(exchange, tmp_path, monkeypatch):
    from test_bingx_safety_regressions import Response
    path = tmp_path / "journal.sqlite"
    client = wrap(path)
    original = exchange.request
    unavailable = [True]
    def interrupted(method, url, **kwargs):
        if method == "post" and "type=STOP_MARKET" in str(kwargs.get("data", "")):
            exchange.lost_response = True
            try:
                return original(method, url, **kwargs)
            finally:
                exchange.lost_response = False
        if unavailable[0] and method == "get" and "/trade/order?" in url and len(exchange.orders) >= 2:
            return Response({}, code=80014)
        return original(method, url, **kwargs)
    monkeypatch.setattr(exchange, "request", interrupted)
    with pytest.raises(UnknownOrderStateError):
        client.place_order("BTCUSDT", "BUY", ".02", sl=49000, command_id="entry")
    wires = client.journal.wires(client.account_id, "entry:protection")
    assert "clientOrderId" not in wires[0]["event"]["request_params"]
    assert not wires[0]["event"].get("order_id") and not wires[0]["event"].get("client_order_id")
    unavailable[0] = False
    restarted = wrap(path)
    restarted.recover()
    assert restarted.is_symbol_blocked("BTCUSDT")
    assert restarted.journal.get(restarted.account_id, "entry:protection")["state"] == "UNKNOWN"
    assert not restarted.journal.protections(restarted.account_id)
    assert "2" not in restarted.raw_client._owned_orders
    assert len(exchange.mutations) == 2
