"""SQLite/WAL журнал событий и безопасные совместимые файловые представления."""

import base64
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .log_context import CATEGORIES, LEVELS, normalize_symbol
from .helpers import get_filename

DB_NAME = "events.sqlite3"
FILTER_FIELDS = ("level", "category", "exchange", "symbol", "instance", "search", "order", "request", "source", "time_from", "time_to")
_COLUMNS = {"instance": "instance_id", "order": "order_id", "request": "request_id"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log_catalog (kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(kind,value));
CREATE TABLE IF NOT EXISTS log_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 timestamp TEXT NOT NULL, level TEXT NOT NULL, category TEXT NOT NULL,
 exchange TEXT, symbol TEXT, instance_id TEXT, order_id TEXT, request_id TEXT,
 source TEXT NOT NULL, service TEXT, message TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS log_symbol_seq ON log_events(symbol, sequence);
CREATE INDEX IF NOT EXISTS log_source_seq ON log_events(source, sequence);
CREATE INDEX IF NOT EXISTS log_exchange_seq ON log_events(exchange, sequence);
CREATE INDEX IF NOT EXISTS log_category_seq ON log_events(category, sequence);
CREATE INDEX IF NOT EXISTS log_level_seq ON log_events(level, sequence);
CREATE INDEX IF NOT EXISTS log_order_seq ON log_events(order_id, sequence);
CREATE INDEX IF NOT EXISTS log_request_seq ON log_events(request_id, sequence);
CREATE INDEX IF NOT EXISTS log_instance_seq ON log_events(instance_id, sequence);
CREATE INDEX IF NOT EXISTS log_time ON log_events(timestamp);
"""


class LogMirrorError(RuntimeError):
    pass


class InvalidLogCursor(ValueError):
    pass


class LogQueryTimeout(RuntimeError):
    pass


def database_path(data_dir):
    return Path(data_dir) / "logs" / DB_NAME


def _append_locked(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lock-файл не ротируется; каждый append заново открывает текущее имя журнала.
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    lock_fd = os.open(str(path) + ".lock", flags, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd = os.open(path, flags | os.O_APPEND, 0o600)
        try:
            data = text.encode("utf-8")
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("Не удалось дописать журнал")
                data = data[written:]
        finally:
            os.close(fd)
    finally:
        os.close(lock_fd)


class EventStore:
    def __init__(self, data_dir, max_bytes=8 * 1024 * 1024):
        self.data_dir = Path(data_dir)
        self.path = database_path(data_dir)
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.RLock()
        self._pid = os.getpid()
        self._initialized = False
        self._segment = 0
        self._run_id = uuid.uuid4().hex

    def _prepare_process(self):
        if self._pid != os.getpid():
            self._lock = threading.RLock()
            self._pid = os.getpid()
            self._run_id = uuid.uuid4().hex
            self._segment = 0
            self._initialized = False

    @property
    def run_id(self):
        self._prepare_process()
        return self._run_id

    def write(self, event):
        """Нет наследуемых SQLite connections или shared rotating handlers."""
        self._prepare_process()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            connection = sqlite3.connect(self.path, timeout=2)
            try:
                if not self._initialized:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.executescript(_SCHEMA)
                connection.execute("PRAGMA synchronous=FULL")
                if not self._initialized:
                    connection.execute("INSERT OR IGNORE INTO log_meta VALUES ('store_id', ?)", (uuid.uuid4().hex,))
                columns = ("event_id", "timestamp", "level", "category", "exchange", "symbol", "instance_id", "order_id", "request_id", "source", "service", "message")
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO log_events (" + ",".join(columns) + ",payload) VALUES (" + ",".join("?" for _ in range(13)) + ")",
                    (*[event.get(key) for key in columns], payload),
                ).rowcount
                connection.executemany("INSERT OR IGNORE INTO log_catalog(kind,value) VALUES (?,?)", [(key, event[key]) for key in ("source", "exchange", "symbol", "instance_id") if event.get(key)])
                connection.commit()
                self._initialized = True
            finally:
                connection.close()
            if not inserted:
                return
            try:
                self.append_jsonl(event)
                self.append_text(event)
            except OSError as exc:
                raise LogMirrorError("Событие сохранено в SQLite, но не удалось записать файловую копию") from exc

    def append_jsonl(self, event):
        self._prepare_process()
        directory = self.path.parent / "events"
        directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        path = directory / f"{self._pid}-{self._run_id}-{self._segment:06d}.jsonl"
        if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > self.max_bytes:
            self._segment += 1
            path = directory / f"{self._pid}-{self._run_id}-{self._segment:06d}.jsonl"
        _append_locked(path, line)

    def append_text(self, event):
        message = event["message"]
        if event.get("exception"):
            message += "\n" + event["exception"]
        message = message.replace("\r", "\\r").replace("\n", "\\n")
        timestamp = event["timestamp"][:19].replace("T", " ")
        if event["category"] == "trade":
            path = self.data_dir / "trades.log"
            line = f"{timestamp} | {message}\n"
        else:
            symbol = event.get("symbol")
            path = self.data_dir / "logs" / f"{get_filename(normalize_symbol(symbol))}.log" if symbol else self.data_dir / "steps.log"
            line = f"{timestamp} | {event['level']:<8} | {event.get('elapsed', '-----'):<5} | {message}\n"
        _append_locked(path, line)


class EventReader:
    def __init__(self, data_dir, query_seconds=0.75):
        self.data_dir = Path(data_dir)
        self.path = database_path(data_dir)
        self.query_seconds = query_seconds

    def _connect(self):
        if not self.path.exists():
            return None
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)
        connection.row_factory = sqlite3.Row
        connection.create_function("casefold", 1, lambda value: str(value or "").casefold(), deterministic=True)
        deadline = time.monotonic() + self.query_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _cursor(store_id, fingerprint, mode, edge, snapshot):
        value = json.dumps([1, store_id, fingerprint, mode, edge, snapshot], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(cursor, store_id, fingerprint):
        try:
            if len(cursor) > 1024:
                raise ValueError()
            value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
            version, database, filters, mode, edge, snapshot = value
            if version != 1 or database != store_id or filters != fingerprint or mode not in ("before", "after"):
                raise ValueError()
            if type(edge) is not int or type(snapshot) is not int or not 0 <= edge <= snapshot < 2**63:
                raise ValueError()
            return mode, edge, snapshot
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            raise InvalidLogCursor("Курсор недействителен; обновите журнал") from None

    def events(self, filters=None, cursor=None, limit=100):
        filters = {key: value for key, value in (filters or {}).items() if value not in (None, "")}
        if set(filters) - set(FILTER_FIELDS) or not 1 <= limit <= 200:
            raise ValueError("Недопустимые параметры журнала")
        if "symbol" in filters:
            filters["symbol"] = normalize_symbol(filters["symbol"])
        for key, allowed in (("level", LEVELS), ("category", CATEGORIES)):
            if key in filters and any(item not in allowed for item in filters[key].split(",")):
                raise ValueError(f"Недопустимый фильтр {key}")
        fingerprint = hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:24]
        connection = self._connect()
        if connection is None:
            if cursor:
                raise InvalidLogCursor("Журнал изменился; обновите страницу")
            return {"events": [], "next_cursor": None, "live_cursor": None, "has_more": False, "watermark": 0}
        try:
            connection.execute("BEGIN")
            metadata = connection.execute("SELECT value FROM log_meta WHERE key='store_id'").fetchone()
            if metadata is None:
                raise LogQueryTimeout("Журнал инициализируется; повторите запрос")
            store_id = metadata[0]
            watermark = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM log_events").fetchone()[0]
            mode, edge, snapshot = self._decode(cursor, store_id, fingerprint) if cursor else ("before", watermark + 1, watermark)
            if snapshot > watermark:
                raise InvalidLogCursor("Журнал изменился; обновите страницу")
            conditions, params = [], []
            for key, value in filters.items():
                if key in ("level", "category"):
                    items = value.split(",")
                    conditions.append(f"{key} IN ({','.join('?' for _ in items)})")
                    params.extend(items)
                elif key == "search":
                    conditions.append("instr(casefold(message), casefold(?)) > 0")
                    params.append(value)
                elif key in ("time_from", "time_to"):
                    conditions.append("timestamp " + (">=" if key == "time_from" else "<=") + " ?")
                    params.append(value)
                else:
                    conditions.append(_COLUMNS.get(key, key) + " = ?")
                    params.append(value)
            upper = watermark if mode == "after" else snapshot
            conditions.extend(("sequence <= ?", "sequence > ?" if mode == "after" else "sequence < ?"))
            params.extend((upper, edge))
            rows = connection.execute(
                "SELECT sequence,payload FROM log_events WHERE " + " AND ".join(conditions) + " ORDER BY sequence " + ("ASC" if mode == "after" else "DESC") + " LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            more = len(rows) > limit
            selected, size = [], 0
            for row in rows[:limit]:
                size += len(row["payload"].encode("utf-8"))
                if selected and size > 1024 * 1024:
                    more = True
                    break
                selected.append(row)
            rows = selected
            if mode == "before":
                rows.reverse()
            events = [{**json.loads(row["payload"]), "sequence": row["sequence"]} for row in rows]
            next_cursor = self._cursor(store_id, fingerprint, "before", rows[0]["sequence"], snapshot) if mode == "before" and more else None
            live_edge = snapshot if mode == "before" else (rows[-1]["sequence"] if more else watermark)
            return {"events": events, "next_cursor": next_cursor, "live_cursor": self._cursor(store_id, fingerprint, "after", live_edge, watermark), "has_more": more, "watermark": watermark}
        except sqlite3.OperationalError as exc:
            raise LogQueryTimeout("Чтение журнала временно недоступно; сузьте фильтры или повторите запрос") from exc
        finally:
            connection.close()

    def sources(self):
        result = {"categories": list(CATEGORIES), "levels": list(LEVELS), "sources": [], "exchanges": [], "symbols": [], "instances": [], "legacy_sources": [], "watermark": 0}
        connection = self._connect()
        if connection:
            try:
                connection.execute("BEGIN")
                result["watermark"] = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM log_events").fetchone()[0]
                for column, key in (("source", "sources"), ("exchange", "exchanges"), ("symbol", "symbols"), ("instance_id", "instances")):
                    result[key] = [row[0] for row in connection.execute("SELECT value FROM log_catalog WHERE kind=? ORDER BY value LIMIT 200", (column,))]
            except sqlite3.OperationalError as exc:
                raise LogQueryTimeout("Не удалось прочитать источники журнала") from exc
            finally:
                connection.close()
        # Старую историю не импортируем: иначе текстовые mirrors продублируют JSON-события.
        paths = [self.data_dir / "steps.log", self.data_dir / "trades.log"]
        directory = self.data_dir / "logs"
        if directory.is_dir():
            paths.extend(itertools.islice(directory.glob("*.log"), 200))
        for path in paths:
            if path.is_file() and not path.is_symlink():
                result["legacy_sources"].append({"source": "system" if path.name == "steps.log" else path.stem, "filename": path.name})
        return result
