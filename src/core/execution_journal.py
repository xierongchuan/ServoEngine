"""Durable журнал исполнения: SQLite/WAL, FULL sync, блокировки между процессами."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from enum import Enum
from pathlib import Path

from src.exchanges.errors import ExchangeStateUnavailableError

STATES = frozenset({"PREPARED", "ACK", "UNKNOWN", "PARTIAL", "FILLED", "REJECTED", "RESOLVED"})
PENDING = ("PREPARED", "ACK", "UNKNOWN", "PARTIAL")


def canonical_symbol(symbol):
    from src.exchanges.market_data import canonical_symbol as normalize
    return normalize(symbol)


def _json(value):
    def encode(item):
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("Нефинитный Decimal в журнале")
            return format(item, "f")
        if isinstance(item, Enum):
            return item.value
        raise TypeError(f"Неподдерживаемый тип журнала: {type(item).__name__}")
    def check(item):
        if isinstance(item, dict):
            for key, nested in item.items():
                if str(key).lower().replace("_", "").replace("-", "") in {"apikey", "apisecret", "apisecretkey", "secret", "secretkey", "signature", "authorization", "accesstoken", "xbxapikey", "xmexcapikey"}:
                    raise ValueError("Секреты запрещены в журнале исполнения")
                check(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                check(nested)
    check(value)
    return json.dumps(value, default=encode, allow_nan=False, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_client_id(account, command_id):
    return "se" + hashlib.sha256(f"{account}:{command_id}".encode()).hexdigest()[:28]


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class ExecutionJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS intents (
                    account TEXT NOT NULL, command_id TEXT NOT NULL, client_id TEXT NOT NULL,
                    owner TEXT NOT NULL, symbol TEXT NOT NULL, operation TEXT NOT NULL,
                    state TEXT NOT NULL, request TEXT NOT NULL, context TEXT NOT NULL,
                    order_id TEXT, result TEXT, updated REAL NOT NULL,
                    PRIMARY KEY(account, command_id), UNIQUE(account, client_id));
                CREATE INDEX IF NOT EXISTS intents_symbol ON intents(account, symbol, state);
                CREATE TABLE IF NOT EXISTS wires (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, command_id TEXT NOT NULL,
                    state TEXT NOT NULL, event TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ownership (
                    account TEXT NOT NULL, owner TEXT NOT NULL, symbol TEXT NOT NULL,
                    position_id TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
                    snapshot TEXT NOT NULL, PRIMARY KEY(account, owner, symbol, position_id));
                CREATE TABLE IF NOT EXISTS protection (
                    account TEXT NOT NULL, owner TEXT NOT NULL, symbol TEXT NOT NULL,
                    order_id TEXT NOT NULL, position_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                    PRIMARY KEY(account, order_id));
                CREATE TABLE IF NOT EXISTS blocks (
                    account TEXT NOT NULL, symbol TEXT NOT NULL, reason TEXT NOT NULL,
                    updated REAL NOT NULL, PRIMARY KEY(account, symbol));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    command_id TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
                PRAGMA user_version=1;
            """)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=_Connection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def transaction(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @contextmanager
    def symbol_lock(self, account, symbol):
        digest = hashlib.sha256(f"{account}:{canonical_symbol(symbol)}".encode()).hexdigest()
        directory = self.path.parent / (self.path.name + ".locks")
        directory.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(directory / digest, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ExchangeStateUnavailableError("Символ исполняется другим процессом") from exc
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _record(row):
        if row is None:
            return None
        value = dict(row)
        for key in ("request", "context", "result", "event", "snapshot"):
            if key in value and value[key] is not None:
                value[key] = json.loads(value[key])
        return value

    def get(self, account, command_id):
        with self._connect() as db:
            return self._record(db.execute("SELECT * FROM intents WHERE account=? AND command_id=?", (account, command_id)).fetchone())

    def intents(self, account, *, symbol=None, pending_only=False):
        query, args = "SELECT * FROM intents WHERE account=?", [account]
        if symbol is not None:
            query += " AND symbol=?"
            args.append(canonical_symbol(symbol))
        if pending_only:
            query += " AND state IN ('PREPARED','ACK','UNKNOWN','PARTIAL')"
        with self._connect() as db:
            return [self._record(row) for row in db.execute(query + " ORDER BY updated,command_id", args)]

    def blocked(self, account, symbol, *, ignore=()):
        symbol = canonical_symbol(symbol)
        with self._connect() as db:
            row = db.execute("SELECT reason FROM blocks WHERE account=? AND symbol=?", (account, symbol)).fetchone()
            if row:
                return row[0]
            pending = db.execute("SELECT command_id,state FROM intents WHERE account=? AND symbol=? AND state IN ('PREPARED','ACK','UNKNOWN','PARTIAL')", (account, symbol))
            return next((f"{row['state']}: {row['command_id']}" for row in pending if row['command_id'] not in ignore), None)

    def prepare(self, account, owner, symbol, operation, request, context=None, *, command_id=None, client_id=None, allow_pending=(), safety=False, grid_queue=False):
        symbol = canonical_symbol(symbol)
        command_id = str(command_id or uuid.uuid4().hex)
        client_id = client_id or stable_client_id(account, command_id)
        encoded = _json(request)
        with self.transaction() as db:
            old = db.execute("SELECT * FROM intents WHERE account=? AND command_id=?", (account, command_id)).fetchone()
            if old:
                if old["request"] != encoded or old["operation"] != operation or old["owner"] != owner or old["symbol"] != symbol or old["client_id"] != client_id:
                    raise ExchangeStateUnavailableError("command_id уже связан с другой командой")
                return self._record(old), False
            if not safety and db.execute("SELECT 1 FROM blocks WHERE account=? AND symbol=?", (account, symbol)).fetchone():
                raise ExchangeStateUnavailableError("Символ приостановлен durable журналом")
            rows = list(db.execute("SELECT * FROM intents WHERE account=? AND symbol=? AND state IN ('PREPARED','ACK','UNKNOWN','PARTIAL')", (account, symbol)))
            if grid_queue:
                if operation not in {"grid_entry", "grid_reduce"} or request.get("order_type") != "LIMIT":
                    raise ExchangeStateUnavailableError("Очередь разрешена только отдельным GRID LIMIT операциям")
                if any(not self._grid_ack(row, owner) for row in rows):
                    raise ExchangeStateUnavailableError("GRID допускает только доказанные ACK LIMIT того же владельца")
            elif any(row["command_id"] not in allow_pending for row in rows):
                raise ExchangeStateUnavailableError("Есть неразрешённая команда по символу; повтор запрещён")
            now = time.time()
            db.execute("INSERT INTO intents VALUES (?,?,?,?,?,?,'PREPARED',?,?,NULL,NULL,?)",
                       (account, command_id, client_id, owner, symbol, operation, encoded, _json(context or {}), now))
            db.execute("INSERT INTO events(account,command_id,state,detail,created) VALUES (?,?,'PREPARED',?,?)", (account, command_id, encoded, now))
        return self.get(account, command_id), True

    @staticmethod
    def _grid_ack(row, owner):
        request = json.loads(row["request"]) if isinstance(row["request"], str) else row["request"]
        return (row["owner"] == owner and row["operation"] in {"grid_entry", "grid_reduce"}
                and row["state"] == "ACK" and bool(row["order_id"]) and request.get("order_type") == "LIMIT")

    def grid_ack_ids(self, account, owner, symbol):
        return tuple(r["command_id"] for r in self.intents(account, symbol=symbol, pending_only=True) if self._grid_ack(r, owner))

    def finish_many(self, account, updates, *, ownership=(), pause_reason=None, block=None):
        """Fills всей сетки и агрегированный owned snapshot фиксируются одной транзакцией."""
        with self.transaction() as db:
            for update in updates:
                state = update["state"]
                if state not in STATES:
                    raise ValueError("Неизвестное состояние intent")
                old = db.execute("SELECT state FROM intents WHERE account=? AND command_id=?", (account, update["command_id"])).fetchone()
                if old is None or (old["state"] in {"FILLED", "REJECTED", "RESOLVED"} and state in PENDING):
                    raise ExchangeStateUnavailableError("Недопустимый переход aggregate intent")
                result = _json(update["result"])
                db.execute("UPDATE intents SET state=?,result=?,order_id=COALESCE(?,order_id),updated=? WHERE account=? AND command_id=?",
                           (state, result, update.get("order_id"), time.time(), account, update["command_id"]))
                if state in {"FILLED", "REJECTED", "RESOLVED"}:
                    db.execute("UPDATE wires SET state='RESOLVED' WHERE account=? AND command_id=?", (account, update["command_id"]))
                db.execute("INSERT INTO events(account,command_id,state,detail,created) VALUES (?,?,?,?,?)",
                           (account, update["command_id"], state, result, time.time()))
            for row in ownership:
                self._write_owned(db, account, row)
                if pause_reason:
                    insert = "INSERT OR IGNORE" if block else "INSERT OR REPLACE"
                    db.execute(insert + " INTO blocks VALUES (?,?,?,?)", (account, canonical_symbol(row["symbol"]), pause_reason, time.time()))
            if block:
                # Ошибка защиты и fills фиксируются вместе; чужая пауза не заменяется.
                db.execute("INSERT OR IGNORE INTO blocks VALUES (?,?,?,?)", (account, canonical_symbol(block[0]), block[1], time.time()))

    def finish(self, account, command_id, state, *, result=None, order_id=None, context=None, ownership=(), pause_reason=None):
        if state not in STATES:
            raise ValueError("Неизвестное состояние intent")
        with self.transaction() as db:
            current = db.execute("SELECT state,context FROM intents WHERE account=? AND command_id=?", (account, command_id)).fetchone()
            if current is None:
                raise ExchangeStateUnavailableError("Intent отсутствует в журнале")
            if current["state"] in {"FILLED", "REJECTED", "RESOLVED"} and state in PENDING:
                raise ExchangeStateUnavailableError("Запрещён откат терминального intent")
            db.execute("UPDATE intents SET state=?,result=COALESCE(?,result),order_id=COALESCE(?,order_id),context=?,updated=? WHERE account=? AND command_id=?",
                       (state, _json(result) if result is not None else None, str(order_id) if order_id is not None else None,
                        _json(context) if context is not None else current["context"], time.time(), account, command_id))
            for value in ownership:
                self._write_owned(db, account, value)
                if pause_reason:
                    db.execute("INSERT OR REPLACE INTO blocks VALUES (?,?,?,?)", (account, canonical_symbol(value["symbol"]), pause_reason, time.time()))
            if state in {"FILLED", "REJECTED", "RESOLVED"}:
                db.execute("UPDATE wires SET state='RESOLVED' WHERE account=? AND command_id=?", (account, command_id))
            db.execute("INSERT INTO events(account,command_id,state,detail,created) VALUES (?,?,?,?,?)", (account, command_id, state, _json(result or {}), time.time()))
        if current["state"] != state:
            from src.utils.logger import info, warning, log_context
            receipt = self.get(account, command_id)
            with log_context(category="trade" if state == "FILLED" else "order",
                             symbol=receipt["symbol"], instance_id=receipt["owner"],
                             request_id=command_id, order_id=receipt.get("order_id")):
                emit = warning if state in {"UNKNOWN", "PARTIAL", "REJECTED"} else info
                emit(f"📋 {receipt['operation']}: {current['state']} → {state}")

    @staticmethod
    def _write_owned(db, account, value):
        db.execute("INSERT OR REPLACE INTO ownership VALUES (?,?,?,?,?,?,?)", (account, value["owner"], canonical_symbol(value["symbol"]),
                   str(value["position_id"]), value["side"], str(value["quantity"]), _json(value.get("snapshot", {}))))

    def owned(self, account, *, owner=None, symbol=None):
        query, args = "SELECT * FROM ownership WHERE account=?", [account]
        if owner is not None:
            query += " AND owner=?"
            args.append(owner)
        if symbol is not None:
            query += " AND symbol=?"
            args.append(canonical_symbol(symbol))
        with self._connect() as db:
            return [self._record(row) for row in db.execute(query, args)]

    def wire(self, account, command_id, event, *, wire_id=None):
        wire_id = wire_id or uuid.uuid4().hex
        state = {"ACKNOWLEDGED": "ACK"}.get(event.get("status"), event.get("status", "UNKNOWN"))
        if state not in STATES:
            raise ValueError("Неизвестное состояние wire")
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO wires VALUES (?,?,?,?,?,?)", (wire_id, account, command_id, state, _json(event), time.time()))
        return wire_id

    def wires(self, account, command_id=None):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM wires WHERE account=?" + (" AND command_id=?" if command_id else "") + " ORDER BY updated", (account, command_id) if command_id else (account,))
            return [self._record(row) for row in rows]

    def save_protection(self, account, owner, symbol, order_id, position_id, snapshot):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO protection VALUES (?,?,?,?,?,?)", (account, owner, canonical_symbol(symbol), str(order_id), str(position_id), _json(snapshot)))

    def protections(self, account, *, owner=None, symbol=None):
        query, args = "SELECT * FROM protection WHERE account=?", [account]
        if owner is not None:
            query += " AND owner=?"
            args.append(owner)
        if symbol is not None:
            query += " AND symbol=?"
            args.append(canonical_symbol(symbol))
        with self._connect() as db:
            return [self._record(row) for row in db.execute(query, args)]

    def remove_protection(self, account, order_id):
        with self.transaction() as db:
            db.execute("DELETE FROM protection WHERE account=? AND order_id=?", (account, str(order_id)))

    def pause(self, account, symbol, reason, *, preserve_existing=False):
        with self.transaction() as db:
            insert = "INSERT OR IGNORE" if preserve_existing else "INSERT OR REPLACE"
            db.execute(insert + " INTO blocks VALUES (?,?,?,?)", (account, canonical_symbol(symbol), str(reason), time.time()))

    def resolve_block(self, account, symbol, *, expected_reason=None):
        """Только coordinator после доказанной сверки; pending intents всё равно блокируют."""
        with self.transaction() as db:
            if expected_reason is None:
                raise ExchangeStateUnavailableError("Снятие блокировки требует доказанного ожидаемого состояния")
            db.execute("DELETE FROM blocks WHERE account=? AND symbol=? AND reason=?", (account, canonical_symbol(symbol), expected_reason))
