"""Блокировки смены execution по реальным durable-схемам, без сети и ключей."""

import json
import sqlite3

import pytest

from src.core.execution_journal import ExecutionJournal
from src.exchanges.impl.spot_trading_adapter import _SCHEMA
from src.trading_settings import has_managed_state, managed_state_block_reason


@pytest.fixture
def journal(tmp_path):
    return ExecutionJournal(tmp_path / "execution.sqlite3")


def prepare(journal):
    intent, _ = journal.prepare("old-account:mexc:live", "owner", "BTC-USDT", "entry", {}, command_id="command")
    return intent


def test_initialized_empty_journal_is_allowed_without_migration(journal, tmp_path):
    original = journal.path.read_bytes()
    assert managed_state_block_reason(tmp_path) is None
    assert not has_managed_state(tmp_path)
    assert journal.path.read_bytes() == original


@pytest.mark.parametrize("state", ["PREPARED", "ACK", "UNKNOWN", "PARTIAL"])
def test_pending_intent_in_any_account_blocks(journal, tmp_path, state):
    prepare(journal)
    journal.finish("old-account:mexc:live", "command", state)
    assert has_managed_state(tmp_path)
    assert "execution.sqlite3" in managed_state_block_reason(tmp_path)


@pytest.mark.parametrize("state", ["FILLED", "REJECTED", "RESOLVED"])
def test_terminal_journal_history_does_not_block(journal, tmp_path, state):
    prepare(journal)
    journal.wire("old-account:mexc:live", "command", {"status": "UNKNOWN"})
    journal.finish("old-account:mexc:live", "command", state)
    assert managed_state_block_reason(tmp_path) is None


@pytest.mark.parametrize("state", ["PREPARED", "ACK", "UNKNOWN", "PARTIAL", "INVALID"])
def test_pending_or_unknown_wire_blocks_even_without_intent(journal, tmp_path, state):
    with journal.transaction() as db:
        db.execute("INSERT INTO wires VALUES ('wire','old','command',?,'{}',0)", (state,))
    assert has_managed_state(tmp_path)


@pytest.mark.parametrize("quantity,blocked", [("0", False), ("0.00000000000000000000000000001", True),
    ("1000000000000000000000000000000", True), ("-1", True), ("NaN", True), ("Infinity", True), ("garbage", True)])
def test_ownership_uses_decimal_not_lossy_sql_cast(journal, tmp_path, quantity, blocked):
    with journal.transaction() as db:
        db.execute("INSERT INTO ownership VALUES ('old','owner','BTCUSDT','pid','LONG',?,'{}')", (quantity,))
    assert has_managed_state(tmp_path) is blocked


@pytest.mark.parametrize("table,values", [
    ("protection", "('old','owner','BTCUSDT','sl','pid','{}')"),
    ("blocks", "('old','BTCUSDT','unknown',0)"),
])
def test_protection_and_safety_blocks_prevent_source_switch(journal, tmp_path, table, values):
    with journal.transaction() as db:
        db.execute(f"INSERT INTO {table} VALUES {values}")
    assert has_managed_state(tmp_path)


def test_guard_reads_committed_wal_and_does_not_initialize_database(journal, tmp_path):
    db = sqlite3.connect(journal.path)
    try:
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("INSERT INTO ownership VALUES ('old','owner','BTCUSDT','pid','LONG','1','{}')")
        db.commit()
        assert (tmp_path / "execution.sqlite3-wal").exists()
        assert has_managed_state(tmp_path)
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["unknown_schema", "corrupt", "version", "missing_column", "locked"])
def test_unknown_unreadable_or_locked_journal_fails_closed(tmp_path, kind):
    path = tmp_path / "execution.sqlite3"
    if kind == "corrupt":
        path.write_bytes(b"not sqlite")
        assert has_managed_state(tmp_path)
        return
    if kind != "unknown_schema":
        ExecutionJournal(path)
    db = sqlite3.connect(path)
    try:
        if kind == "unknown_schema":
            db.execute("CREATE TABLE unknown (id TEXT)")
        elif kind == "version":
            db.execute("PRAGMA user_version=99")
        elif kind == "missing_column":
            db.execute("ALTER TABLE ownership RENAME COLUMN quantity TO unknown")
        db.commit()
        if kind == "locked":
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("BEGIN EXCLUSIVE")
        assert has_managed_state(tmp_path)
    finally:
        db.close()


@pytest.fixture
def spot_ledger(tmp_path):
    path = tmp_path / "custom" / "ledger.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.executescript(_SCHEMA)
    return path


def test_initialized_clean_spot_ledger_is_allowed(spot_ledger, tmp_path):
    with sqlite3.connect(spot_ledger) as db:
        db.execute("INSERT INTO owners VALUES ('ns','old','owner','USDT','0')")
        db.execute("INSERT INTO lots VALUES ('lot','ns','BTCUSDT','0','0',NULL,NULL,0)")
        db.execute("INSERT INTO wallets VALUES ('old','BTC','10')")
    # Ручные монеты кошелька не являются собственными лотами бота.
    assert not has_managed_state(tmp_path)


@pytest.mark.parametrize("status,blocked", [("FILLED", False), ("CANCELED", False),
    ("REJECTED", False), ("EXPIRED", False), ("PREPARED", True), ("ACK", True),
    ("UNKNOWN", True), ("PARTIAL", True), ("NEW", True), ("INVALID", True)])
def test_spot_commands_guard_nonterminal_state(spot_ledger, tmp_path, status, blocked):
    with sqlite3.connect(spot_ledger) as db:
        db.execute("INSERT INTO commands(cid,ns,symbol,side,kind,qty,budget,status,fingerprint) VALUES ('cid','ns','BTCUSDT','BUY','MARKET','1','100',?,'test')", (status,))
    assert has_managed_state(tmp_path) is blocked


@pytest.mark.parametrize("quantity", ["1", "1e-1000", "NaN", "-1", "bad"])
def test_spot_lots_block_old_account_switch(spot_ledger, tmp_path, quantity):
    with sqlite3.connect(spot_ledger) as db:
        db.execute("INSERT INTO lots VALUES ('lot','old-ns','BTCUSDT',?,'1','90','120',0)", (quantity,))
    assert has_managed_state(tmp_path)


@pytest.mark.parametrize("sql", ["INSERT INTO pauses VALUES ('old','unknown')",
    "INSERT INTO owners VALUES ('ns','old','owner','USDT','100')",
    "INSERT INTO owners VALUES ('ns','old','owner','USDT','NaN')"])
def test_spot_pause_and_reserved_owner_cash_block(spot_ledger, tmp_path, sql):
    with sqlite3.connect(spot_ledger) as db:
        db.execute(sql)
    assert has_managed_state(tmp_path)


def grid_registry(tmp_path):
    directory = tmp_path / "grid"
    directory.mkdir()
    state = {"version": 1, "identity": {"account_id": "old"}, "signature": {},
        "commands": {}, "owned_positions": {}, "paused_reason": "",
        "state": {key: 0 for key in ("inventory", "long_inventory", "short_inventory", "gross_inventory", "owned_long", "owned_short")}}
    return directory / "BTCUSDT_scope.json", state


def test_clean_grid_registry_and_terminal_history_allowed(tmp_path):
    path, state = grid_registry(tmp_path)
    state["commands"]["cid"] = {"client_order_id": "cid", "order_id": "oid", "status": "FILLED"}
    path.write_text(json.dumps(state))
    assert not has_managed_state(tmp_path)


@pytest.mark.parametrize("change", ["pending", "ownership", "inventory", "unknown", "paused", "version"])
def test_grid_subdirectory_blocks_pending_owned_or_unknown_registry(tmp_path, change):
    path, state = grid_registry(tmp_path)
    if change == "pending":
        state["commands"]["cid"] = {"client_order_id": "cid", "status": "UNKNOWN"}
    elif change == "ownership":
        state["owned_positions"]["pid"] = {"quantity": 1, "side": "LONG"}
    elif change == "inventory":
        state["state"]["inventory"] = -1
    elif change == "unknown":
        state["state"]["inventory"] = "NaN"
    elif change == "paused":
        state["paused_reason"] = "unknown recovery"
    else:
        state["version"] = 99
    path.write_text(json.dumps(state))
    assert has_managed_state(tmp_path)


def test_missing_state_does_not_create_files(tmp_path):
    assert not has_managed_state(tmp_path)
    assert list(tmp_path.iterdir()) == []
