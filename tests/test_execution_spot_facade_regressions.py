"""Core common facade с настоящим Spot ledger, fake Spot и без второго coordinator."""

import pytest

from src.core.execution_coordinator import attach_execution_safety
from src.core.execution_helpers import create_protected_order
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from test_spot_trading_adapter import setup


def test_attach_trusted_spot_returns_same_adapter_and_checks_identity(setup, tmp_path):
    _, _, make = setup
    adapter = make()
    assert not hasattr(adapter, "_transport")
    assert attach_execution_safety(adapter, owner_id="instance-a", account_id="account-a", settings=adapter.settings) is adapter
    with pytest.raises(ExchangeStateUnavailableError):
        attach_execution_safety(adapter, owner_id="other-instance")
    with pytest.raises(ExchangeStateUnavailableError):
        attach_execution_safety(adapter, journal_path=tmp_path / "other.sqlite")


def test_arbitrary_safety_flag_is_not_trusted(tmp_path):
    class Untrusted:
        execution_safety_enabled = True
    with pytest.raises((UnsupportedCapabilityError, ExchangeStateUnavailableError)):
        attach_execution_safety(Untrusted(), journal_path=tmp_path / "unsafe.sqlite",
                                settings={"exchange": "bingx", "execution_mode": "demo"})


def test_core_helper_spot_fill_local_protection_and_stable_replay(setup, monkeypatch):
    from src import config
    spot, _, make = setup
    adapter = make()
    monkeypatch.setattr(config, "LEVERAGE", 100)
    monkeypatch.setattr(config, "MIN_TRADE_AMOUNT_USDT", 1)
    oid = create_protected_order(adapter, "BTCUSDT", "BUY", 100, sl=90, tp=110,
                                 size_pct=10, command_id="core-entry", owner_id="instance-a")
    assert oid == "1" and len(spot.requests) == 1
    record = adapter.journal.get(adapter.account_id, "core-entry")
    assert record["state"] == "FILLED" and record["result"]["entry_complete"] is True
    assert not adapter.capabilities.native_protection
    rows = adapter.get_owned_positions("BTCUSDT")["BTCUSDT"]
    assert len(rows) == 1 and rows[0].leverage == 1
    assert create_protected_order(adapter, "BTCUSDT", "BUY", 100, sl=90, tp=110,
                                size_pct=10, command_id="core-entry", owner_id="instance-a") == oid
    assert len(spot.requests) == 1


@pytest.mark.parametrize("start,local", [(False, True), (False, False)])
def test_local_protection_requires_opt_in_and_supervisor(setup, monkeypatch, start, local):
    _, _, make = setup
    adapter = make(start=start, local_protection=local)
    with pytest.raises(UnsupportedCapabilityError):
        create_protected_order(adapter, "BTCUSDT", "BUY", 100, sl=90, tp=110, size_pct=10)


def test_core_limit_ack_is_not_command_success(setup, monkeypatch):
    from src import config
    spot, _, make = setup
    adapter = make()
    monkeypatch.setattr(config, "MIN_TRADE_AMOUNT_USDT", 1)
    oid = create_protected_order(adapter, "BTCUSDT", "BUY", 100, sl=90, tp=110,
                                order_type="LIMIT", size_pct=10, command_id="core-limit")
    assert adapter.journal.get(adapter.account_id, "core-limit")["state"] == "ACK"
    assert adapter.is_symbol_blocked("BTCUSDT")
    cid = spot.requests[-1].client_order_id
    spot.fill(cid, spot.requests[-1].base_quantity)
    assert adapter.wait_for_entry("BTCUSDT", oid, timeout=0)
    assert adapter.journal.get(adapter.account_id, "core-limit")["state"] == "FILLED"


def test_monitor_full_close_uses_common_facade_proof(setup, monkeypatch):
    from src.core import monitor
    _, _, make = setup
    adapter = make()
    adapter.place_order("BTCUSDT", "BUY", "1", sl="90", tp="110", command_id="entry")
    pid = adapter.get_owned_positions("BTCUSDT")["BTCUSDT"][0].position_id
    monkeypatch.setattr(monitor, "get_exchange_client", lambda: adapter)
    assert monitor.close_position("BTCUSDT", pid) is True
    assert adapter.closure_confirmed("BTCUSDT", pid)


def test_monitor_ack_without_proven_completion_is_not_success(monkeypatch):
    from src.core import monitor
    class Pending:
        def position_command_id(self, *args):
            return "close"
        def close_position(self, *args, **kwargs):
            return True
        def closure_confirmed(self, *args):
            return False
    monkeypatch.setattr(monitor, "get_exchange_client", lambda: Pending())
    with pytest.raises(UnknownOrderStateError):
        monitor.close_position("BTCUSDT", "own-lot")
