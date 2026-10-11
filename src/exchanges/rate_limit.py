"""Общий SQLite sliding-window limiter: durable cooldown, fork-safe, fail-closed.

MEXC Spot: https://www.mexc.com/api-docs/spot-v3/introduction#rate-limits
Create/cancel: /api-docs/spot-v3/spot-account-trade/new-order (12/s).
Futures: /api-docs/futures/account-and-trading-endpoints/get-current-orders (20/2s),
/api-docs/futures/account-and-trading-endpoints/place-tpsl-order-by-position (5/2s).
Общий Futures 20/2s и BingX 1/s — более строгие локальные политики, не лимиты API.
Все процессы/контейнеры одного egress должны использовать один локальный data volume.
"""

from __future__ import annotations

from contextlib import closing
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from time import monotonic as _monotonic, sleep as _sleep
import uuid

from .errors import ExchangeStateUnavailableError

_DEFAULTS = {
    "mexc": {"public": (300, 10), "private": (500, 10), "orders": (12, 1)},
    "bingx": {"public": (1, 1), "private": (1, 1), "orders": (1, 1), "global": (1, 1)},
}
_HOSTS = {"mexc": "api.mexc.com", "bingx": "open-api.bingx.com"}
_SCHEMA_VERSION = 1
_NS = 1_000_000_000


def _unavailable(message):
    from src.utils.logger import error
    error(f"❌ Rate limiter: {message}")
    raise ExchangeStateUnavailableError(f"Rate limiter: {message}") from None


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2_147_483_647:
        raise ValueError(f"Rate limiter: {label} должен быть положительным целым числом")
    return value


def _seconds(value, label, *, allow_zero=False):
    if isinstance(value, bool):
        raise ValueError(f"Rate limiter: некорректный {label}")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"Rate limiter: некорректный {label}") from None
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0) or value > 31_536_000:
        raise ValueError(f"Rate limiter: некорректный {label}")
    return value


def _digest(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _clock_sample():
    # BOOTTIME учитывает suspend; оба clock доступны всем процессам одного ядра.
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as handle:
        boot_id = handle.read(128).strip()
    if not boot_id:
        raise OSError("Отсутствует boot_id")
    clock = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)
    return boot_id, time.clock_gettime_ns(clock)


def _sleep_bounded(delay, deadline):
    remaining = deadline - _monotonic()
    if remaining <= 0 or delay > remaining:
        _unavailable("истёк max_wait; HTTP не отправлен")
    _sleep(delay)


class ExchangeRateLimiter:
    """Соединения и locks не сохраняются в объекте: его можно передавать fork/spawn.

    account_id — стабильный UID/логическое имя счёта, НЕ API key. Без него все ключи
    консервативно разделяют один account budget. IP budget никогда не делится по UID.
    max_wait ограничивает ожидание бюджета/SQLite lock, но не сокращает cooldown.
    """

    def __init__(self, exchange, bucket="public", capacity=None, period=None, *,
                 data_dir=None, db_path=None, account_id=None, ip_id=None, host=None,
                 scope=None, max_wait=None):
        if not isinstance(exchange, str) or not exchange.strip() or not isinstance(bucket, str) or not bucket:
            raise ValueError("Rate limiter: нужны exchange и bucket")
        exchange = exchange.lower()
        if exchange in {"mexc_spot", "mexc_futures"}:
            exchange = "mexc"
        default = _DEFAULTS.get(exchange, {}).get(bucket)
        if capacity is None or period is None:
            if default is None:
                raise ValueError("Rate limiter: неизвестный bucket требует capacity и period")
            capacity = default[0] if capacity is None else capacity
            period = default[1] if period is None else period
        self.capacity = _positive_integer(capacity, "capacity")
        self.period = _seconds(period, "period")
        self._period_ns = math.ceil(self.period * _NS)
        max_wait = os.getenv("SERVO_RATE_LIMIT_MAX_WAIT", "5") if max_wait is None else max_wait
        self.max_wait = _seconds(max_wait, "max_wait", allow_zero=True)
        self.exchange, self.bucket = exchange, bucket
        if scope is None:
            scope = "account" if bucket in {"private", "orders", "futures_stoporder"} else "ip"
        if scope not in {"ip", "account"}:
            raise ValueError("Rate limiter: scope должен быть ip или account")
        for identifier in (account_id, ip_id, host):
            if identifier is not None and (isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier)):
                raise ValueError("Rate limiter: некорректный идентификатор scope")
        host = str(host or _HOSTS.get(exchange, exchange))
        ip_hash = _digest("ip", str(ip_id) if ip_id is not None else "shared-egress")
        account_hash = _digest("account", str(account_id) if account_id is not None else "shared-account")
        self._key = _digest(exchange, host, bucket, scope, ip_hash if scope == "ip" else account_hash)
        self._cooldown_key = _digest(exchange, host, "cooldown", ip_hash)
        if db_path is not None and data_dir is not None:
            raise ValueError("Rate limiter: укажите только data_dir или db_path")
        try:
            if db_path is None:
                root = Path(data_dir or os.getenv("SERVO_DATA_DIR") or Path(__file__).resolve().parents[2] / "data")
                root.mkdir(parents=True, exist_ok=True)
                path = root / "exchange_rate_limits.sqlite3"
            else:
                path = Path(db_path)
            if str(path) == ":memory:" or not path.parent.is_dir():
                _unavailable("нужна локальная файловая SQLite база с доступным parent directory")
            self.db_path = str(path.absolute())
            self._guard_path = self.db_path + ".guard"
            self._initialize(_monotonic() + self.max_wait)
        except (OSError, sqlite3.Error, UnicodeError, OverflowError):
            _unavailable("невозможно открыть/инициализировать хранилище")

    def _connect(self, deadline):
        # mode=rw запрещает незаметное создание пустой БД после удаления файла.
        timeout = min(0.1, max(0.0, deadline - _monotonic()))
        connection = sqlite3.connect(Path(self.db_path).as_uri() + "?mode=rw", uri=True,
                                     timeout=timeout, isolation_level=None)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except BaseException:
            connection.close()
            raise

    def _initialize(self, deadline):
        # Guard не содержит ключей/UID; он отличает первый запуск от потери БД.
        fd = os.open(self._guard_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            delay = 0.01
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    _sleep_bounded(delay, deadline)
                    delay = min(0.2, delay * 2)
            guard = os.read(fd, 128).decode("ascii").strip()
            if guard and (len(guard) != 32 or any(c not in "0123456789abcdef" for c in guard)):
                _unavailable("повреждён guard хранилища")
            path = Path(self.db_path)
            if guard and not path.is_file():
                _unavailable("durable база удалена; автоматическое обнуление запрещено")
            if not path.exists():
                created = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
                os.close(created)
            with closing(self._connect(deadline)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version not in {0, _SCHEMA_VERSION}:
                        _unavailable("неподдерживаемая версия SQLite схемы")
                    if version == 0:
                        if guard or connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                            _unavailable("схема durable базы отсутствует/повреждена")
                        boot, tick = _clock_sample()
                        identity = uuid.uuid4().hex
                        connection.execute("CREATE TABLE clock_state (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL, boot TEXT NOT NULL, tick INTEGER NOT NULL, logical INTEGER NOT NULL)")
                        connection.execute("INSERT INTO clock_state VALUES (1, ?, ?, ?, 0)", (identity, boot, tick))
                        connection.execute("CREATE TABLE budgets (key TEXT PRIMARY KEY, capacity INTEGER NOT NULL CHECK(capacity>0), period INTEGER NOT NULL CHECK(period>0))")
                        connection.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, key TEXT NOT NULL, at INTEGER NOT NULL, weight INTEGER NOT NULL CHECK(weight>0))")
                        connection.execute("CREATE INDEX events_window ON events(key, at)")
                        connection.execute("CREATE TABLE cooldowns (key TEXT PRIMARY KEY, until INTEGER NOT NULL)")
                        connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                    row = connection.execute("SELECT identity FROM clock_state WHERE id=1").fetchone()
                    if row is None or (guard and row[0] != guard):
                        _unavailable("идентичность durable базы не совпадает")
                    self._identity = row[0]
                    self._register(connection)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            if not guard:
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, self._identity.encode("ascii"))
                os.ftruncate(fd, 32)
                os.fsync(fd)
                # Сохраняем также directory entry guard/базы при первом создании.
                directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            stat = path.stat()
            self._file_identity = (stat.st_dev, stat.st_ino)
        finally:
            os.close(fd)

    def _register(self, connection):
        row = connection.execute("SELECT capacity, period FROM budgets WHERE key=?", (self._key,)).fetchone()
        if row is None:
            connection.execute("INSERT INTO budgets VALUES (?, ?, ?)", (self._key, self.capacity, self._period_ns))
        elif row != (self.capacity, self._period_ns):
            _unavailable("процессы используют разные настройки одного bucket")

    def _logical_time(self, connection):
        row = connection.execute("SELECT identity, boot, tick, logical FROM clock_state WHERE id=1").fetchone()
        if row is None or row[0] != self._identity:
            _unavailable("состояние clock/идентичность базы повреждены")
        identity, previous_boot, previous_tick, logical = row
        if not isinstance(previous_tick, int) or not isinstance(logical, int) or min(previous_tick, logical) < 0:
            _unavailable("повреждён durable clock")
        boot, tick = _clock_sample()
        if previous_boot == boot:
            # Регресс clock не выдаёт кредитов: ждём прежнего high-water mark.
            logical += max(0, tick - previous_tick)
            tick = max(tick, previous_tick)
        # После reboot остаток окна/cooldown сохранён полностью; downtime не вычитаем.
        connection.execute("UPDATE clock_state SET boot=?, tick=?, logical=? WHERE id=1", (boot, tick, logical))
        return logical

    def _transaction(self, deadline, action):
        delay = 0.01
        while True:
            try:
                with open(self._guard_path, encoding="ascii") as guard:
                    if guard.read(128).strip() != self._identity:
                        _unavailable("guard/идентичность хранилища повреждены")
                stat = os.stat(self.db_path)
                if (stat.st_dev, stat.st_ino) != self._file_identity:
                    _unavailable("файл базы заменён; автоматический reset запрещён")
                with closing(self._connect(deadline)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        result = action(connection, self._logical_time(connection))
                        connection.commit()
                        return result
                    except BaseException:
                        connection.rollback()
                        raise
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 255
                if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    _unavailable("SQLite недоступен/повреждён")
                _sleep_bounded(delay, deadline)
                delay = min(0.2, delay * 2)
            except (sqlite3.Error, OSError, OverflowError, UnicodeError):
                _unavailable("хранилище или boot clock недоступны")

    def acquire(self, weight=1):
        acquire_rate_limits([(self, weight)])

    def cooldown(self, seconds):
        seconds = _seconds(seconds, "cooldown", allow_zero=True)
        duration = math.ceil(seconds * _NS)
        deadline = _monotonic() + self.max_wait
        def persist(connection, now):
            connection.execute("INSERT INTO cooldowns VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET until=MAX(until, excluded.until)",
                               (self._cooldown_key, now + duration))
        self._transaction(deadline, persist)


def acquire_rate_limits(reservations, *, max_wait=None):
    """Атомарно резервирует IP/UID/endpoint budgets одной SQLite базы."""
    budgets = {}
    all_limiters = []
    for limiter, weight in reservations:
        if not isinstance(limiter, ExchangeRateLimiter):
            raise TypeError("Атомарное резервирование требует ExchangeRateLimiter")
        weight = _positive_integer(weight, "weight")
        if weight > limiter.capacity:
            raise ValueError("Rate limiter: weight превышает capacity")
        all_limiters.append(limiter)
        previous = budgets.get(limiter._key)
        budgets[limiter._key] = (limiter, max(weight, previous[1] if previous else 0))
    if not budgets:
        raise ValueError("Rate limiter: пустой список бюджетов запрещён")
    entries = list(budgets.values())
    first = entries[0][0]
    if any(l.db_path != first.db_path or l._identity != first._identity for l in all_limiters):
        _unavailable("атомарные budgets должны использовать одну SQLite базу")
    wait_limit = min(l.max_wait for l in all_limiters)
    if max_wait is not None:
        wait_limit = min(wait_limit, _seconds(max_wait, "max_wait", allow_zero=True))
    deadline = _monotonic() + wait_limit

    def reserve(connection, now):
        wait_ns = 0
        for limiter, weight in entries:
            limiter._register(connection)
            cooldown = connection.execute("SELECT until FROM cooldowns WHERE key=?", (limiter._cooldown_key,)).fetchone()
            if cooldown:
                if not isinstance(cooldown[0], int) or cooldown[0] < 0:
                    _unavailable("повреждён durable cooldown")
                wait_ns = max(wait_ns, cooldown[0] - now)
            cutoff = now - limiter._period_ns
            connection.execute("DELETE FROM events WHERE key=? AND at<=?", (limiter._key, cutoff))
            rows = connection.execute("SELECT at, weight FROM events WHERE key=? ORDER BY at, seq", (limiter._key,)).fetchall()
            if any(not isinstance(at, int) or not isinstance(w, int) or at < 0 or at > now or w <= 0 for at, w in rows):
                _unavailable("повреждены события sliding window")
            excess = sum(w for _, w in rows) + weight - limiter.capacity
            if excess > 0:
                released = 0
                for at, w in rows:
                    released += w
                    if released >= excess:
                        wait_ns = max(wait_ns, at + limiter._period_ns - now)
                        break
        if wait_ns <= 0:
            for limiter, weight in entries:
                connection.execute("INSERT INTO events(key, at, weight) VALUES (?, ?, ?)", (limiter._key, now, weight))
        return max(0, wait_ns) / _NS

    while True:
        delay = first._transaction(deadline, reserve)
        if delay == 0:
            return
        _sleep_bounded(delay, deadline)


class _PublicReaderBudget:
    """Адаптер текущего public REST-reader: .get(exchange), acquire/cooldown."""

    def __init__(self, *limiters):
        self.limiters = limiters
        self.max_wait = min(limiter.max_wait for limiter in limiters)

    def acquire(self, weight=1):
        acquire_rate_limits([(limiter, weight) for limiter in self.limiters])

    def cooldown(self, seconds):
        # Все члены используют один durable host/IP cooldown.
        self.limiters[0].cooldown(seconds)


def get_exchange_rate_limiters(exchange, *, data_dir=None, db_path=None, account_id=None,
                              ip_id=None, host=None, max_wait=None):
    """Factory без чтения ключей/config/.env; только локальные budgets и policy.

    MEXC mapping применим одновременно к Spot/Futures. Wildcard /api/v1/* — общий
    консервативный Futures REST budget, а не доказанный общий серверный лимит.
    BingX mapping ссылается на один IP-global 1/s limiter, endpoint limits не заявлены.
    Алиасы mexc/bingx совместимы с public_market_rest._limiter(exchange).
    """
    exchange = exchange.lower()
    if exchange in {"mexc_spot", "mexc_futures"}:
        exchange = "mexc"
    if exchange not in _DEFAULTS:
        raise ValueError("Rate limiter: неизвестная биржа")
    options = dict(data_dir=data_dir, db_path=db_path, account_id=account_id,
                   ip_id=ip_id, host=host, max_wait=max_wait)
    if exchange == "bingx":
        global_budget = ExchangeRateLimiter("bingx", "global", scope="ip", **options)
        return {"public": global_budget, "private": global_budget, "orders": global_budget, "bingx": global_budget}
    result = {bucket: ExchangeRateLimiter("mexc", bucket, **options) for bucket in ("public", "private", "orders")}
    futures = ExchangeRateLimiter("mexc", "futures_rest", 20, 2, scope="ip", **options)
    stops = ExchangeRateLimiter("mexc", "futures_stoporder", 5, 2, scope="account", **options)
    result.update({"/api/v1/*": futures, "/api/v1/contract/kline/*": futures,
        "/api/v1/private/order/external/*": futures, "/api/v1/private/order/get/*": futures,
        "/api/v1/private/order/list/open_orders": futures,
        "/api/v1/private/stoporder/place": stops,
        # 5/2s документировано для place; общий stoporder bucket — строгая policy.
        "/api/v1/private/stoporder/*": stops,
        # Reader не различает market_type в limiter API: Spot-свечи тоже получают
        # строгую локальную квоту 20/2s, но не обходят общий Futures budget.
        "mexc": _PublicReaderBudget(result["public"], futures)})
    return result
