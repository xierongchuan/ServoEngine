"""Структурированные логи с SQLite/WAL, JSONL и совместимыми text-файлами."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading
import time
import uuid

from .log_context import (
    CONTEXT_FIELDS, CATEGORIES, RedactingFormatter, SecretRedactor,
    clear_log_context, get_log_context, infer_category, log_context, set_log_context,
)
from .log_store import EventStore, LogMirrorError

LOG_DIR = os.getenv("SERVO_DATA_DIR", "data")
code_logger = logging.getLogger("steps")
trades_logger = logging.getLogger("trades")
_configure_lock = threading.RLock()
_redactor = SecretRedactor()
_MAX_TEXT = 16384


class ElapsedFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "elapsed"):
            record.elapsed = "-----"
        return True


formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(elapsed)-5s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
formatter.converter = time.gmtime


def _emergency(message):
    try:
        os.write(2, (_redactor.text("⚠️ Ошибка записи журнала: " + str(message))[:1024] + "\n").encode("utf-8", errors="replace"))
    except OSError:
        pass


class StructuredEventHandler(logging.Handler):
    _servo_capture = True

    def __init__(self, data_dir, level=logging.INFO, max_bytes=8 * 1024 * 1024):
        super().__init__(level)
        self.data_dir = Path(data_dir)
        self.max_bytes = max_bytes
        self.store = EventStore(self.data_dir, max_bytes)
        self.owner_pid = os.getpid()

    def emit(self, record):
        if not (record.name in ("root", "steps", "trades") or record.name.startswith(("src.", "panel", "telegram_panel.", "backend."))):
            return
        # Чтение журнала не должно само порождать бесконечные live-обновления.
        if record.name == "panel.app" and record.levelno <= logging.INFO and any(
            isinstance(arg, str) and arg.startswith("/api/logs") for arg in (record.args if isinstance(record.args, tuple) else ())
        ):
            return
        # Один LogRecord может пройти handlers ребёнка и root; записываем его один раз.
        if getattr(record, "_servo_persisted", False):
            return
        record._servo_persisted = True
        if self.owner_pid != os.getpid():
            self.owner_pid = os.getpid()
            self.store = EventStore(self.data_dir, self.max_bytes)
        event = None
        try:
            context = get_log_context()
            context.update(getattr(record, "log_context", {}) or {})
            context.update({key: getattr(record, key) for key in CONTEXT_FIELDS if hasattr(record, key)})
            category = context.get("category") or infer_category(record.pathname.replace("/", ".") if record.name == "steps" else record.name)
            if category not in CATEGORIES:
                category = "runtime"
            context.pop("category", None)
            event = {
                **{key: str(context[key])[:256] if context.get(key) is not None else None for key in CONTEXT_FIELDS if key != "category"},
                "schema_version": 1, "event_id": uuid.uuid4().hex,
                "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "pid": os.getpid(), "run_id": self.store.run_id, "logger": record.name,
                "level": record.levelname, "category": category,
                "message": _redactor.text(record.getMessage())[:_MAX_TEXT],
                "elapsed": _redactor.text(getattr(record, "elapsed", "-----"))[:32],
                "duration_ms": getattr(record, "duration_ms", None),
            }
            if event.get("symbol"):
                from .log_context import normalize_symbol
                event["symbol"] = normalize_symbol(event["symbol"])
            event["source"] = event.get("symbol") or "system"
            event["service"] = event.get("service") or ("panel" if category == "panel" else "bot")
            if record.exc_info:
                event["exception"] = _redactor.text(logging.Formatter().formatException(record.exc_info))[:_MAX_TEXT]
            elif record.exc_text:
                event["exception"] = _redactor.text(record.exc_text)[:_MAX_TEXT]
            if record.stack_info:
                event["stack"] = _redactor.text(record.stack_info)[:_MAX_TEXT]
            event = _redactor.clean(event)
            self.store.write(event)
        except Exception as exc:
            # При недоступной SQLite сохраняем очищенное событие хотя бы в JSONL.
            if event is not None and not isinstance(exc, LogMirrorError):
                try:
                    self.store.append_jsonl(event)
                except Exception:
                    pass
            _emergency(exc)


def protect_logging_handlers():
    """Main вызывает после настройки Uvicorn/root, чтобы очистить и console/access."""
    _redactor.refresh()
    loggers = [logging.getLogger()] + [value for value in logging.Logger.manager.loggerDict.values() if isinstance(value, logging.Logger)]
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler.formatter, RedactingFormatter):
                handler.formatter.redactor = _redactor
            elif not isinstance(handler, StructuredEventHandler):
                handler.setFormatter(RedactingFormatter(handler.formatter, _redactor))


def configure_logging(data_dir=None, level="INFO", max_bytes=8 * 1024 * 1024):
    """Идемпотентная установка capture handler; не удаляет чужие handlers."""
    global LOG_DIR
    with _configure_lock:
        if data_dir is not None:
            LOG_DIR = str(data_dir)
        numeric_level = logging._nameToLevel.get(str(level).upper(), level if isinstance(level, int) else logging.INFO)
        root = logging.getLogger()
        root.setLevel(numeric_level)
        managed = [handler for handler in root.handlers if getattr(handler, "_servo_capture", False)]
        keep = next((handler for handler in managed if isinstance(handler, StructuredEventHandler) and handler.data_dir == Path(LOG_DIR)), None)
        for handler in managed:
            if handler is not keep:
                root.removeHandler(handler)
                handler.close()
        if keep is None:
            keep = StructuredEventHandler(LOG_DIR, numeric_level, max_bytes)
            root.addHandler(keep)
        keep.setLevel(numeric_level)
        keep.max_bytes = max(1024, int(max_bytes))
        keep.store.max_bytes = keep.max_bytes
        for name in ("steps", "trades", "src", "panel", "telegram_panel", "backend"):
            logger = logging.getLogger(name)
            logger.setLevel(numeric_level)
            logger.propagate = True
        protect_logging_handlers()
        return keep


def setup_symbol_logger(symbol):
    # Совместимый вызов больше не меняет handlers и не теряет instance-контекст.
    set_log_context(symbol=symbol)


def safe_log(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except Exception as exc:
        _emergency(exc)


def _log(logger, level, message, args, fields):
    exc_info = fields.pop("exc_info", None)
    stack_info = fields.pop("stack_info", False)
    extra = dict(fields.pop("extra", {}) or {})
    extra["log_context"] = fields
    safe_log(logger.log, level, message, *args, extra=extra, exc_info=exc_info, stack_info=stack_info, stacklevel=4)


def info(msg, *args, **fields):
    _log(code_logger, logging.INFO, msg, args, fields)


def warning(msg, *args, **fields):
    _log(code_logger, logging.WARNING, msg, args, fields)


def error(msg, *args, **fields):
    _log(code_logger, logging.ERROR, msg, args, fields)


def debug(msg, *args, **fields):
    _log(code_logger, logging.DEBUG, msg, args, fields)


def log_trade(message, level="INFO", **fields):
    fields["category"] = "trade"
    _log(trades_logger, logging._nameToLevel.get(str(level).upper(), logging.INFO), message, (), fields)


def info_timed(msg, elapsed, **fields):
    extra = dict(fields.pop("extra", {}) or {})
    extra.update(elapsed=f"{elapsed:.2f}s", duration_ms=round(elapsed * 1000, 3))
    info(msg, extra=extra, **fields)


class StageTimer:
    def __init__(self, stage_name, symbol=None, emoji="⏱️"):
        self.stage_name, self.symbol, self.emoji = stage_name, symbol, emoji
        self.start = None

    def __enter__(self):
        self.start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.monotonic() - self.start
        message = f"{self.emoji} {self.stage_name}"
        fields = {"symbol": self.symbol} if self.symbol else {}
        if exc_type:
            error(message, exc_info=(exc_type, exc, tb), extra={"elapsed": f"{elapsed:.2f}s", "duration_ms": round(elapsed * 1000, 3)}, **fields)
        else:
            info_timed(message, elapsed, **fields)


def _after_fork():
    global _configure_lock
    _configure_lock = threading.RLock()


os.register_at_fork(after_in_child=_after_fork)
configure_logging()
