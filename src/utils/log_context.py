"""Контекст и очистка логов без импортов торгового конфига."""

import contextvars
import logging
import os
import re
import threading
from contextlib import contextmanager
from urllib.parse import quote, quote_plus

CATEGORIES = ("runtime", "exchange", "market_data", "strategy", "llm", "risk", "order", "trade", "config", "panel")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
CONTEXT_FIELDS = ("service", "exchange", "market_type", "execution_mode", "symbol", "instance_id", "strategy", "profile", "request_id", "order_id", "position_id", "cycle_id", "category")
_context = contextvars.ContextVar("servo_log_context", default={})
_process_context = {}
_context_lock = threading.RLock()


def _after_fork():
    global _context_lock
    _context_lock = threading.RLock()
    # Временный request-контекст родителя не должен попасть в новый worker.
    _context.set({})


os.register_at_fork(after_in_child=_after_fork)
_SECRET_NAME = re.compile(r"secret|token|password|passwd|api.?key|private.?key|authorization|init.?data|^auth$|signature", re.I)
_SECRET_PAIR = re.compile(
    r'''(?<![\w-])(?P<key>["']?(?:[\w-]*(?:token|secret|password|passwd|api[_-]?key|apikey|signature|init[_-]?data)|authorization|auth)["']?\s*(?:=|:|%3[dD]|%3[aA])\s*)(?:\[REDACTED\]|"[^"\n]*"|'[^'\n]*'|[^\s&;,}\]\n]+)''', re.I
)
_BEARER = re.compile(r"\bBearer\s+[^\s,;\"']+", re.I)
_BOT_URL = re.compile(r"/bot[^/\s?\"']+", re.I)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)


def normalize_symbol(symbol):
    value = str(symbol or "").replace("-", "").replace("/", "").upper()
    if not re.fullmatch(r"[A-Z0-9]{1,40}", value):
        raise ValueError("Недопустимый символ")
    return value


def _fields(fields):
    aliases = {"instance": "instance_id", "request": "request_id", "order": "order_id"}
    result = {}
    for name, value in fields.items():
        name = aliases.get(name, name)
        if name not in CONTEXT_FIELDS:
            raise ValueError(f"Неизвестное поле контекста: {name}")
        if name == "symbol" and value is not None:
            value = normalize_symbol(value)
        if name == "category" and value is not None and value not in CATEGORIES:
            raise ValueError("Недопустимая категория")
        result[name] = str(value)[:256] if value is not None else None
    return result


def set_log_context(**fields):
    """Задаёт defaults процесса; новые потоки видят тот же worker-контекст."""
    values = _fields(fields)
    with _context_lock:
        for key, value in values.items():
            if value is None:
                _process_context.pop(key, None)
            else:
                _process_context[key] = value


def clear_log_context():
    with _context_lock:
        _process_context.clear()
    _context.set({})


def get_log_context():
    with _context_lock:
        return {**_process_context, **_context.get()}


@contextmanager
def log_context(**fields):
    """Временный контекст запроса/ордера, изолированный между async tasks."""
    token = _context.set({**_context.get(), **_fields(fields)})
    try:
        yield
    finally:
        _context.reset(token)


class SecretRedactor:
    def __init__(self):
        self.refresh()

    def refresh(self):
        # Читаем только окружение процесса, никогда не открываем .env.
        values = set()
        for key, value in os.environ.copy().items():
            if value and _SECRET_NAME.search(key):
                values.update((value, quote(value, safe=""), quote_plus(value)))
        self._values = tuple(sorted(values, key=len, reverse=True))

    def text(self, value):
        text = str(value)
        for secret in self._values:
            text = text.replace(secret, "[REDACTED]")
        text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
        text = _BOT_URL.sub("/bot[REDACTED]", text)
        text = _BEARER.sub("Bearer [REDACTED]", text)
        return _SECRET_PAIR.sub(lambda m: m.group("key") + "[REDACTED]", text)

    def clean(self, value):
        if isinstance(value, dict):
            return {key: "[REDACTED]" if _SECRET_NAME.search(str(key)) else self.clean(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self.clean(item) for item in value]
        return self.text(value) if isinstance(value, str) else value


class RedactingFormatter(logging.Formatter):
    """Очистка ПОСЛЕ форматирования, включая traceback и нестандартные поля."""
    def __init__(self, formatter, redactor):
        super().__init__()
        self.original = formatter or logging.Formatter()
        self.redactor = redactor

    def format(self, record):
        return self.redactor.text(self.original.format(record))


def infer_category(name):
    if name == "trades":
        return "trade"
    if name.startswith(("panel", "src.telegram_panel", "telegram_panel", "backend")):
        return "panel"
    if ".exchanges" in name:
        return "exchange"
    for part, category in (("collector", "market_data"), ("predict", "llm"), ("risk", "risk"), ("executor", "order"), ("tracking", "trade"), ("config", "config"), ("strateg", "strategy")):
        if part in name:
            return category
    return "runtime"
