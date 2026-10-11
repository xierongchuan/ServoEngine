"""Многоразовые web-токены и одноразовые WS-билеты с межпроцессной блокировкой."""

import fcntl
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("panel.token_store")
DEFAULT_TOKEN_EXPIRY_SECONDS = 6 * 60 * 60
DEFAULT_MAX_TOKENS_PER_USER = 5
MAX_VALIDATION_ATTEMPTS = 10
VALIDATION_WINDOW_SECONDS = 60


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def get_data_dir() -> Path:
    path = Path(os.environ.get("PANEL_DATA_DIR", str(get_project_root() / "data")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_tokens_file() -> Path:
    return get_data_dir() / "web_tokens.json"


class RateLimiter:
    def __init__(self, max_attempts=MAX_VALIDATION_ATTEMPTS, window_seconds=VALIDATION_WINDOW_SECONDS):
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, identifier: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._attempts = defaultdict(list, {
                key: [t for t in values if now - t < self._window_seconds]
                for key, values in self._attempts.items()
                if values and now - values[-1] < self._window_seconds
            })
            if len(self._attempts[identifier]) >= self._max_attempts:
                return False
            self._attempts[identifier].append(now)
            return True

    def reset(self, identifier: str) -> None:
        with self._lock:
            self._attempts.pop(identifier, None)


_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter


class TokenStore:
    def __init__(self, storage_path: Path | None = None, rate_limiter: RateLimiter | None = None):
        self._lock = threading.RLock()
        self._storage_path = Path(storage_path or get_tokens_file())
        self._rate_limiter = rate_limiter or get_rate_limiter()

    @contextmanager
    def _transaction(self, write=False):
        # Блокируется отдельный файл: inode JSON меняется при atomic replace.
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(str(self._storage_path) + ".lock", "a", encoding="utf-8") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                try:
                    with self._storage_path.open(encoding="utf-8") as source:
                        data = json.load(source)
                    if not isinstance(data, dict) or not isinstance(data.get("tokens"), dict):
                        raise ValueError("Некорректное хранилище токенов")
                except FileNotFoundError:
                    data = {"tokens": {}}
                data.setdefault("ws_tickets", {})
                yield data
                if write:
                    self._save(data)
            except (OSError, ValueError):
                logger.error("❌ Ошибка хранилища авторизации")
                raise
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _save(self, data):
        fd, temporary = tempfile.mkstemp(prefix=".web_tokens-", dir=self._storage_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump(data, target, ensure_ascii=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self._storage_path)
            directory = os.open(self._storage_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _active(data):
        try:
            expiry = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
            return not data.get("revoked", False) and expiry > datetime.now(timezone.utc)
        except (KeyError, TypeError, ValueError):
            return False

    def generate_token(self, user_id: int, expiry_seconds=DEFAULT_TOKEN_EXPIRY_SECONDS,
                       max_tokens_per_user=DEFAULT_MAX_TOKENS_PER_USER) -> str:
        if expiry_seconds <= 0:
            raise ValueError("Срок действия должен быть положительным")
        with self._transaction(write=True) as data:
            tokens = data["tokens"] = {key: value for key, value in data["tokens"].items() if self._active(value)}
            # used не означает revoked: старые многоразовые токены остаются совместимыми.
            if sum(t.get("user_id") == user_id and self._active(t) for t in tokens.values()) >= max_tokens_per_user:
                raise ValueError("Достигнут лимит активных ссылок. Сначала используйте /revoketoken.")
            token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            tokens[token] = {"user_id": user_id, "created_at": now.isoformat(),
                             "expires_at": (now + timedelta(seconds=expiry_seconds)).isoformat(),
                             "used": False, "revoked": False}
        return token

    def validate_token(self, token: str) -> dict[str, Any] | None:
        # Лимит не применяется к успешным запросам: polling и WS проверяют одну сессию.
        try:
            with self._transaction() as data:
                entry = data["tokens"].get(token)
                if not isinstance(entry, dict) or not self._active(entry):
                    return None
                return {key: entry[key] for key in ("user_id", "created_at", "expires_at")}
        except (OSError, ValueError, KeyError):
            return None

    def revoke_token(self, token: str) -> bool:
        with self._transaction(write=True) as data:
            entry = data["tokens"].get(token)
            if entry is None:
                return False
            entry["revoked"] = True
            return True

    def get_user_tokens(self, user_id: int) -> list[dict[str, Any]]:
        with self._transaction() as data:
            return [{"token_prefix": token[:8] + "...", "created_at": entry["created_at"],
                     "expires_at": entry["expires_at"], "used": entry.get("used", False),
                     "revoked": entry.get("revoked", False)}
                    for token, entry in data["tokens"].items()
                    if entry.get("user_id") == user_id and self._active(entry)]

    def revoke_all_user_tokens(self, user_id: int) -> int:
        with self._transaction(write=True) as data:
            count = 0
            for entry in data["tokens"].values():
                if entry.get("user_id") == user_id and self._active(entry):
                    entry["revoked"] = True
                    count += 1
            return count

    def issue_ws_ticket(self, credentials: dict[str, str]) -> str:
        with self._transaction(write=True) as data:
            now = time.time()
            tickets = {key: value for key, value in data["ws_tickets"].items() if value["expires"] > now}
            if len(tickets) >= 1024:
                raise ValueError("Лимит WS-билетов")
            ticket = secrets.token_urlsafe(32)
            tickets[ticket] = {"credentials": credentials, "expires": now + 30}
            data["ws_tickets"] = tickets
            return ticket

    def consume_ws_ticket(self, ticket: str) -> dict[str, str] | None:
        with self._transaction(write=True) as data:
            entry = data["ws_tickets"].pop(ticket, None)
            return entry["credentials"] if entry and entry["expires"] > time.time() else None


_token_store: TokenStore | None = None


def get_token_store() -> TokenStore:
    global _token_store
    if _token_store is None:
        _token_store = TokenStore()
    return _token_store
