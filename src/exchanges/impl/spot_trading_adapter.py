"""Автоматизация Spot: только собственные лоты и явно локальная защита.

Все экземпляры одного аккаунта обязаны использовать один ledger_path. quote_budget
— явное выделение средств оператором, а не импорт кошелька. Local SL/TP работает
только пока жив supervisor и доступна биржа; биржевых защитных ордеров здесь нет.
Factory/main должны явно разрешить этот риск, запустить supervisor и передавать SL
в submit_order даже при attached_protection=False. SELL означает уменьшение LONG.
settings.spot / spot_options задают ОБЩИЙ account quote-pool; legacy quote_budget
без settings остаётся явным per-instance выделением. Эти режимы нельзя смешивать.
Trusted attach вызывает validate_execution_context; journal — только read-only
проекция ledger (get/intents/owned/protections/blocked), не ExecutionCoordinator.
close()/shutdown() закрывают ресурсы, не отменяют ордера и не ликвидируют лоты.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path

from ..dto.models import (
    Balance, ExchangeCapabilities, MarketType, OrderSide, OrderStatus,
    OrderSubmission, OrderType, Position, PositionSide, SpotOrderRequest,
    SubmissionStatus,
)
from ..errors import (
    ExchangeError, ExchangeStateUnavailableError, UnknownOrderStateError,
    UnsupportedCapabilityError,
)
from ..exchange_client import ExchangeClient
from ..market_data import StreamKey, interval_start, timestamp_ms
from ..market_data_storage import read_market_snapshot
from src.utils.logger import error, info, warning


_TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS capital (
 account TEXT PRIMARY KEY, asset TEXT NOT NULL, budget TEXT NOT NULL, cash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owners (
 ns TEXT PRIMARY KEY, account TEXT NOT NULL, instance TEXT NOT NULL,
 asset TEXT NOT NULL, cash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pauses (account TEXT PRIMARY KEY, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS symbol_pauses (
 account TEXT NOT NULL, symbol TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(account, symbol));
CREATE TABLE IF NOT EXISTS command_meta (
 ns TEXT NOT NULL, command_id TEXT NOT NULL, cid TEXT,
 symbol TEXT NOT NULL, operation TEXT NOT NULL, request TEXT NOT NULL,
 state TEXT NOT NULL, result TEXT, created REAL NOT NULL,
 PRIMARY KEY(ns, command_id), UNIQUE(ns, cid));
CREATE TABLE IF NOT EXISTS commands (
 cid TEXT PRIMARY KEY, ns TEXT NOT NULL, symbol TEXT NOT NULL,
 side TEXT NOT NULL, kind TEXT NOT NULL, qty TEXT NOT NULL, budget TEXT NOT NULL,
 price TEXT, oid TEXT, status TEXT NOT NULL, filled TEXT NOT NULL DEFAULT '0',
 quote_filled TEXT NOT NULL DEFAULT '0', fees TEXT NOT NULL DEFAULT '{}',
 sl TEXT, tp TEXT, allocations TEXT NOT NULL DEFAULT '[]', fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills (
 cid TEXT NOT NULL, trade_id TEXT NOT NULL, oid TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(cid, trade_id));
CREATE TABLE IF NOT EXISTS lot_lifecycle (id TEXT PRIMARY KEY, opened_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lots (
 id TEXT PRIMARY KEY, ns TEXT NOT NULL, symbol TEXT NOT NULL,
 qty TEXT NOT NULL, cost TEXT NOT NULL, sl TEXT, tp TEXT,
 triggered INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS wallets (
 account TEXT NOT NULL, asset TEXT NOT NULL, total TEXT NOT NULL,
 PRIMARY KEY(account, asset));
CREATE TABLE IF NOT EXISTS movements (
 account TEXT NOT NULL, asset TEXT NOT NULL, amount TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS commands_ns ON commands(ns, symbol);
CREATE INDEX IF NOT EXISTS lots_ns ON lots(ns, symbol);
"""


def _number(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result <= 0):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        error("❌ Spot: некорректное числовое значение")
        raise ExchangeStateUnavailableError("Spot: некорректное числовое значение") from exc


def _floor(value, step):
    step = _number(step, positive=True)
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _fail(message, cls=UnsupportedCapabilityError):
    error("❌ " + message)
    raise cls(message)


class _SpotJournalView:
    """Read-only проекция Spot ledger; не поддельный ExecutionJournal/transport."""

    def __init__(self, adapter):
        self._adapter = adapter
        self.path = adapter._path

    def _check_account(self, account):
        if str(account) != self._adapter.account_id:
            _fail("Spot: journal account_id не совпадает", ExchangeStateUnavailableError)

    def get(self, account, command_id):
        self._check_account(account)
        return self._adapter.get_execution_command(command_id)

    def intents(self, account, *, symbol=None, pending_only=False):
        self._check_account(account)
        return self._adapter.get_execution_commands(symbol, pending_only=pending_only)

    def owned(self, account, *, owner=None, symbol=None):
        self._check_account(account)
        if owner is not None and str(owner) != self._adapter.owner_id:
            return []
        with self._adapter._guard():
            return [self._adapter._lot_snapshot(row) for row in self._adapter._rows("lots", symbol)]

    def protections(self, account, *, owner=None, symbol=None):
        return [{"account": account, "owner": self._adapter.owner_id, "symbol": row["symbol"],
                 "position_id": row["position_id"], "order_id": "local:" + row["position_id"],
                 "snapshot": {"protection_mode": "local_only", "native": False,
                              "stopLossPrice": row["snapshot"]["protection"]["sl"],
                              "takeProfitPrice": row["snapshot"]["protection"]["tp"],
                              "quantity": row["quantity"]}}
                for row in self.owned(account, owner=owner, symbol=symbol)
                if Decimal(row["quantity"]) > 0 and row["snapshot"]["protection"]["sl"]]

    def blocked(self, account, symbol, *, ignore=()):
        self._check_account(account)
        with self._adapter._guard():
            return self._adapter._block_reason(symbol, ignore=ignore)


class SpotTradingAdapter(ExchangeClient):
    """Контракт executor поверх Spot REST без шортов, Cross и кредитования."""

    EXCHANGE_NAME = "mexc"
    execution_safety_enabled = True
    protection_mode = "local_only"
    protection_confirmation_scope = "local_registration"
    margin_mode = "isolated"  # Изоляция лотов, не биржевой Spot margin.
    default_leverage = 1

    def __init__(self, spot_client, *, ledger_path, account_id, instance_id=None,
                 owner_id=None, settings=None, spot_options=None,
                 quote_asset="USDT", quote_budget=None, execution_mode=None,
                 local_protection=None, data_dir="data", snapshot_reader=None,
                 permission_checker=None, clock=time.time, tick_max_age=10,
                 ticker_interval=2, monitor_interval=1):
        if getattr(spot_client, "EXCHANGE_NAME", None) != "mexc":
            _fail("Spot: адаптер поддерживает только MEXC Spot")
        from src.trading_settings import DEFAULT_TRADING_SETTINGS, validate_trading_settings

        selected = deepcopy(settings) if settings is not None else {
            "exchange": "mexc", "market_type": "spot", "execution_mode": execution_mode or "read_only"}
        if not isinstance(selected, dict):
            raise ValueError("Spot: settings должен быть объектом execution")
        configured_spot = selected.get("spot", {})
        # До добавления execution.spot settings-agent сохраняем ту же строгую
        # валидацию основных полей; Spot-опции проверяем здесь отдельно.
        if "spot" not in DEFAULT_TRADING_SETTINGS:
            selected.pop("spot", None)
        validated = validate_trading_settings(selected)
        if (validated["exchange"], validated["market_type"]) != ("mexc", "spot"):
            _fail("Spot: execution settings должны выбирать mexc/spot")
        if execution_mode is not None and validated["execution_mode"] != execution_mode:
            _fail("Spot: execution_mode расходится с settings")
        if validated["execution_mode"] not in {"read_only", "live"}:
            _fail("Spot: demo запрещён")
        if spot_options is not None and configured_spot and spot_options != configured_spot:
            raise ValueError("Spot: spot_options расходятся с settings.spot")
        options = deepcopy(spot_options if spot_options is not None else configured_spot)
        if not isinstance(options, dict) or options.keys() - {"quote_budget_usdt", "allow_local_protection"}:
            raise ValueError("Spot: неизвестные поля spot_options")
        if "allow_local_protection" in options and type(options["allow_local_protection"]) is not bool:
            raise ValueError("Spot: allow_local_protection должен быть bool")
        if local_protection is not None and type(local_protection) is not bool:
            raise ValueError("Spot: local_protection должен быть bool")
        budget_option = _number(options.get("quote_budget_usdt", 0))
        if quote_budget is not None and "quote_budget_usdt" in options and _number(quote_budget) != budget_option:
            raise ValueError("Spot: quote_budget расходится с spot_options")
        if local_protection is not None and "allow_local_protection" in options and local_protection != options["allow_local_protection"]:
            raise ValueError("Spot: local_protection расходится с spot_options")
        quote_budget = budget_option if quote_budget is None else _number(quote_budget)
        local_protection = options.get("allow_local_protection", False) if local_protection is None else local_protection
        validated["spot"] = {"quote_budget_usdt": float(quote_budget), "allow_local_protection": local_protection}
        _number(validated["spot"]["quote_budget_usdt"])
        if owner_id is not None and instance_id is not None and str(owner_id) != str(instance_id):
            _fail("Spot: owner_id и instance_id должны совпадать")
        instance_id = instance_id if instance_id is not None else owner_id
        if not account_id or not instance_id or quote_asset != validated["settle_asset"]:
            raise ValueError("Spot: обязательны стабильные account_id, owner/instance_id и settle_asset=USDT")
        self.spot = spot_client
        self._shared_quote_budget = settings is not None or spot_options is not None
        self._execution_settings = validated
        self._execution_mode = validated["execution_mode"]
        self._local_protection = local_protection
        self._owner_id = str(instance_id)
        self._account_id = str(account_id)
        native_config = getattr(spot_client, "_config", None)
        self._credentials = (getattr(native_config, "api_key", None), getattr(native_config, "secret_key", None))
        self.quote_asset = quote_asset
        self.account = json.dumps([self.account_id, "mexc", "spot"], separators=(",", ":"))
        self.ns = json.dumps([self.account, self.owner_id], separators=(",", ":"))
        self._path = Path(ledger_path).resolve()
        self._clock = clock
        self._data_dir = data_dir
        self._snapshot_reader = snapshot_reader
        self._permission_checker = permission_checker
        self._tick_max_age = float(_number(tick_max_age, positive=True))
        self._ticker_interval = float(_number(ticker_interval, positive=True))
        self._monitor_interval = float(_number(monitor_interval, positive=True))
        self._ticker_cache = {}
        self._ticker_attempts = {}
        self._lock = threading.RLock()
        self._guard_depth = 0
        self._stop = threading.Event()
        self._thread = None
        self._heartbeat = None
        self._closed = False
        self._recovery_errors = {}
        self.last_execution_result = None
        self._pid = os.getpid()
        self.last_protection_confirmed = False
        self.last_submission = None
        self._lockfile = None
        self._readonly_snapshot = None
        budget = _number(quote_budget)
        if self.read_only:
            if self._path.exists():
                # SQLite mode=ro сам может создавать WAL/SHM. Читаем согласованную
                # временную копию под общим lock, исходный ledger не трогаем.
                self._readonly_snapshot = tempfile.TemporaryDirectory(prefix="servo-spot-view-")
                target = Path(self._readonly_snapshot.name) / "ledger.sqlite"
                source_lock = open(str(self._path) + ".lock", "rb")
                try:
                    fcntl.flock(source_lock, fcntl.LOCK_SH)
                    shutil.copyfile(self._path, target)
                    wal = Path(str(self._path) + "-wal")
                    if wal.exists():
                        shutil.copyfile(wal, str(target) + "-wal")
                finally:
                    source_lock.close()
                self._db = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
            else:
                # Пустой просмотр не создаёт файлы ledger, WAL или lock.
                self._db = sqlite3.connect(":memory:", check_same_thread=False)
                self._db.executescript(_SCHEMA)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._lockfile = open(str(self._path) + ".lock", "a")
            self._db = sqlite3.connect(self._path, timeout=30, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript(_SCHEMA)
        self._db.row_factory = sqlite3.Row
        if self.read_only:
            self._db.execute("PRAGMA query_only=ON")
        if not self.read_only:
            with self._guard():
                owner = self._owner()
                pool = self._capital()
                if self._shared_quote_budget:
                    if pool is None:
                        legacy_cash = self._db.execute("SELECT cash FROM owners WHERE account=?", (self.account,)).fetchall()
                        legacy_lots = self._db.execute("SELECT l.qty FROM lots l JOIN owners o ON l.ns=o.ns WHERE o.account=?", (self.account,)).fetchall()
                        if (any(Decimal(r[0]) > 0 for r in legacy_cash + legacy_lots)
                                or any(r["status"] not in _TERMINAL for r in self._account_commands())):
                            _fail("Spot: переход legacy funding → общий бюджет требует ручной миграции ledger", ExchangeStateUnavailableError)
                        self._db.execute("INSERT INTO capital VALUES (?,?,?,?)", (self.account, quote_asset, str(budget), str(budget)))
                    elif Decimal(pool["budget"]) != budget:
                        if Decimal(pool["budget"]) == 0 and not self._account_commands():
                            self._db.execute("UPDATE capital SET budget=?,cash=? WHERE account=?", (str(budget), str(budget), self.account))
                        else:
                            _fail("Spot: изменение выделенного общего бюджета требует явной сверки/funding ledger", ExchangeStateUnavailableError)
                elif pool is not None:
                    _fail("Spot: общий account budget нельзя заменить legacy per-instance funding", ExchangeStateUnavailableError)
                if owner is None:
                    self._db.execute("INSERT INTO owners VALUES (?,?,?,?,?)",
                                     (self.ns, self.account, str(instance_id), quote_asset, "0" if self._shared_quote_budget else str(budget)))
                    self._db.commit()
                elif owner["asset"] != quote_asset:
                    _fail("Spot: нельзя менять quote_asset существующего ledger")
                self._db.commit()
            directory = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self.journal = _SpotJournalView(self)
        self.validate_execution_context()
        warning("⚠️ MEXC Spot: SL/TP только ЛОКАЛЬНЫЕ; остановка процесса/сети оставляет лоты без биржевой защиты")

    def validate_execution_context(self, *, owner_id=None, account_id=None, settings=None, journal_path=None):
        """Trusted attach должен вызвать эту проверку вместо создания coordinator."""
        from src.trading_settings import DEFAULT_TRADING_SETTINGS, validate_trading_settings

        if owner_id is not None and str(owner_id) != self.owner_id:
            _fail("Spot: адаптер уже закреплён за другим instance", ExchangeStateUnavailableError)
        if account_id is not None and str(account_id) != self.account_id:
            _fail("Spot: нельзя менять account_id", ExchangeStateUnavailableError)
        if journal_path is not None and Path(journal_path).resolve() != self._path:
            _fail("Spot: нельзя менять ledger_path", ExchangeStateUnavailableError)
        if settings is not None:
            selected = deepcopy(settings)
            supplied_spot = selected.get("spot")
            if "spot" not in DEFAULT_TRADING_SETTINGS:
                selected.pop("spot", None)
            checked = validate_trading_settings(selected)
            if any(checked[k] != v for k, v in self._execution_settings.items() if k != "spot"):
                _fail("Spot: изменение settings требует нового клиента", ExchangeStateUnavailableError)
            if supplied_spot is not None and (
                    _number(supplied_spot.get("quote_budget_usdt", 0)) != _number(self._execution_settings["spot"]["quote_budget_usdt"])
                    or supplied_spot.get("allow_local_protection", False) is not self.local_protection):
                _fail("Spot: изменение spot_options требует нового клиента", ExchangeStateUnavailableError)
        if (self.execution_mode != self._execution_settings["execution_mode"]
                or self.local_protection is not self._execution_settings["spot"]["allow_local_protection"]
                or self.execution_account_id != self.account_id
                or self.account != json.dumps([self.account_id, "mexc", "spot"], separators=(",", ":"))
                or self.ns != json.dumps([self.account, self.owner_id], separators=(",", ":"))):
            _fail("Spot: execution identity изменена без остановки runtime", ExchangeStateUnavailableError)
        config = getattr(self.spot, "_config", None)
        if (getattr(config, "api_key", None), getattr(config, "secret_key", None)) != self._credentials:
            _fail("Spot: смена credentials требует нового adapter/account context", ExchangeStateUnavailableError)
        actual = getattr(config, "trading_settings", None)
        for key in ("execution_mode", "margin_mode", "position_mode", "settle_asset"):
            native_value = actual.get(key) if isinstance(actual, dict) else getattr(config, key, None)
            if key == "execution_mode" and self.read_only and native_value in {"read_only", "live"}:
                continue  # Read-only facade может безопасно сужать права raw клиента.
            if native_value is not None and native_value != self._execution_settings[key]:
                _fail(f"Spot: native клиент и settings расходятся: {key}", ExchangeStateUnavailableError)
        if isinstance(actual, dict) and any(actual.get(k, v) != v for k, v in
                                          (("exchange", "mexc"), ("market_type", "spot"))):
            _fail("Spot: native клиент использует другой торговый продукт", ExchangeStateUnavailableError)
        return self

    @property
    def owner_id(self):
        return self._owner_id

    @property
    def account_id(self):
        return self._account_id

    @property
    def execution_account_id(self):
        return self._account_id

    @property
    def execution_mode(self):
        return self._execution_mode

    @property
    def local_protection(self):
        return self._local_protection

    @property
    def settings(self):
        return deepcopy(self._execution_settings)

    @property
    def read_only(self):
        return self.execution_mode == "read_only"

    @property
    def capabilities(self):
        return ExchangeCapabilities(market_type=MarketType.SPOT, positions=True,
                                    automated_strategy=self.local_protection and not self.read_only)

    @property
    def supervisor_running(self):
        return (not self._closed and not self._stop.is_set()
                and self._thread is not None and self._thread.is_alive()
                and self._heartbeat is not None
                and 0 <= self._clock() - self._heartbeat <= max(10, self._monitor_interval * 3))

    @contextmanager
    def _guard(self, *, validate_identity=True):
        if self._closed:
            _fail("Spot: адаптер закрыт; требуется новый клиент", ExchangeStateUnavailableError)
        if os.getpid() != self._pid:
            _fail("Spot: адаптер нельзя разделять между OS-процессами")
        if validate_identity:
            self.validate_execution_context()
        with self._lock:
            if self._lockfile and self._guard_depth == 0:
                fcntl.flock(self._lockfile, fcntl.LOCK_EX)
            self._guard_depth += 1
            try:
                yield
            except BaseException:
                self._db.rollback()
                raise
            finally:
                self._guard_depth -= 1
                if self._lockfile and self._guard_depth == 0:
                    fcntl.flock(self._lockfile, fcntl.LOCK_UN)

    def _capital(self):
        if not self._has_table("capital"):
            return None
        return self._db.execute("SELECT * FROM capital WHERE account=?", (self.account,)).fetchone()

    def _account_commands(self):
        return self._db.execute("SELECT c.* FROM commands c JOIN owners o ON c.ns=o.ns WHERE o.account=? ORDER BY c.rowid", (self.account,)).fetchall()

    def _quote_state(self):
        owners = self._db.execute("SELECT * FROM owners WHERE account=?", (self.account,)).fetchall()
        pool = self._capital()
        cash = ((Decimal(pool["cash"]) if pool else Decimal(0))
                + sum((Decimal(o["cash"]) for o in owners), Decimal(0))
                if self._shared_quote_budget else Decimal(self._owner()["cash"]) if self._owner() else Decimal(0))
        commands = self._account_commands() if self._shared_quote_budget else self._rows("commands")
        reserved = sum((max(Decimal(0), Decimal(r["budget"]) - Decimal(r["quote_filled"])) for r in commands
                        if r["side"] == "BUY" and r["status"] not in _TERMINAL), Decimal(0))
        return cash, reserved

    def _fund_owner(self, amount, own_reserved):
        """Перед intent переносим только свободный bot quote, не wallet balance."""
        if not self._shared_quote_budget:
            return
        owner = self._owner()
        needed = max(Decimal(0), amount + own_reserved - Decimal(owner["cash"]))
        pool = self._capital()
        if pool is not None:
            used = min(needed, Decimal(pool["cash"]))
            self._db.execute("UPDATE capital SET cash=? WHERE account=?", (str(Decimal(pool["cash"]) - used), self.account))
            needed -= used
        reserved = {}
        for row in self._account_commands():
            if row["side"] == "BUY" and row["status"] not in _TERMINAL:
                reserved[row["ns"]] = reserved.get(row["ns"], Decimal(0)) + max(Decimal(0), Decimal(row["budget"]) - Decimal(row["quote_filled"]))
        for sibling in self._db.execute("SELECT * FROM owners WHERE account=? AND ns!=? ORDER BY ns", (self.account, self.ns)).fetchall():
            used = min(needed, max(Decimal(0), Decimal(sibling["cash"]) - reserved.get(sibling["ns"], Decimal(0))))
            self._db.execute("UPDATE owners SET cash=? WHERE ns=?", (str(Decimal(sibling["cash"]) - used), sibling["ns"]))
            needed -= used
        if needed:
            _fail("Spot: свободный общий bot quote исчерпан", ExchangeStateUnavailableError)
        self._db.execute("UPDATE owners SET cash=? WHERE ns=?", (str(max(Decimal(owner["cash"]), amount + own_reserved)), self.ns))

    def _owner(self, ns=None):
        return self._db.execute("SELECT * FROM owners WHERE ns=?", (ns or self.ns,)).fetchone()

    def _rows(self, table, symbol=None, ns=None):
        if table not in {"commands", "lots"}:
            raise ValueError("Spot: неизвестная таблица")
        query, args = f"SELECT * FROM {table} WHERE ns=?", [ns or self.ns]
        if symbol is not None:
            query += " AND symbol=?"
            args.append(self.normalize_symbol(symbol))
        return self._db.execute(query + " ORDER BY rowid", args).fetchall()

    def _pause(self, reason):
        if not self.read_only:
            self._db.execute("INSERT OR REPLACE INTO pauses VALUES (?,?)", (self.account, reason))
            self._db.commit()
        _fail(reason, ExchangeStateUnavailableError)

    @property
    def paused_reason(self):
        row = self._db.execute("SELECT reason FROM pauses WHERE account=?", (self.account,)).fetchone()
        return row[0] if row else None

    def _mutation_allowed(self):
        self.last_protection_confirmed = False
        self.validate_execution_context()
        if threading.current_thread() is self._thread and self._stop.is_set():
            _fail("Spot: supervisor остановлен; новые мутации запрещены", ExchangeStateUnavailableError)
        if self.read_only:
            _fail("Spot: read_only запрещает любые мутации")
        if self.paused_reason:
            _fail(self.paused_reason, ExchangeStateUnavailableError)
        config = getattr(self.spot, "_config", None)
        if config is not None and getattr(config, "execution_mode", None) != "live":
            _fail("Spot: native клиент не разрешает live исполнение")

    def get_instrument_rules(self, symbol):
        return self.spot.get_instrument_rules(symbol)

    def normalize_symbol(self, symbol):
        return StreamKey("mexc", "spot", symbol, "1m").symbol

    def denormalize_symbol(self, symbol):
        return self.normalize_symbol(symbol)

    def get_klines(self, symbol, interval="5m", limit=288):
        return self.spot.get_klines(symbol, interval, limit)

    def get_ticker(self, symbol):
        return self.spot.get_ticker(symbol)

    def get_order_book(self, symbol, limit=20):
        return self.spot.get_order_book(symbol, limit)

    def get_commission_rate(self, symbol):
        return self.spot.get_commission_rate(symbol)

    def get_funding_rate(self, symbol):
        return None

    def set_leverage(self, symbol, leverage, position_side=PositionSide.BOTH):
        if _number(leverage, positive=True) != 1 or PositionSide(position_side) == PositionSide.SHORT:
            _fail("Spot: допустим только LONG без заимствования, leverage=1")
        return True  # Локальная проверка, не изменение аккаунта биржи.

    def check_prerequisites(self, *, authenticated=False):
        if self._closed:
            _fail("Spot: адаптер закрыт", ExchangeStateUnavailableError)
        self.validate_execution_context()
        if self.read_only and not authenticated:
            return True
        if not self.spot.check_prerequisites():
            return False
        if authenticated:
            if self._permission_checker is not None:
                permissions = self._permission_checker()
            else:
                transport = getattr(self.spot, "_transport", None)
                if transport is None:
                    _fail("Spot: нет authenticated проверки разрешений", ExchangeStateUnavailableError)
                permissions = transport.request("GET", "/api/v3/account", private=True)
            if (not isinstance(permissions, dict) or permissions.get("canTrade") is not True
                    or permissions.get("canRead") is False):
                _fail("Spot: право trade не подтверждено", ExchangeStateUnavailableError)
            self.spot.get_asset_balances()
        if self.read_only:
            return True
        with self._guard():
            self.validate_execution_context()
            self._sync()
            owner = self._owner()
            funded = bool(owner and (self._quote_state()[0] > 0
                          or any(Decimal(r["qty"]) > 0 for r in self._rows("lots"))))
            return self.local_protection and self.supervisor_running and funded and not self.paused_reason

    def _query(self, row):
        # Native lookup проверяет и symbol, и clientOrderId; ACK не является fill.
        order = self.spot._query_by_client_id(row["symbol"], row["cid"])
        if order is None:
            return None
        if (order.symbol != row["symbol"] or order.side.value != row["side"]
                or order.order_type.value != row["kind"]
                or order.raw_data.get("clientOrderId") != row["cid"]
                or (row["oid"] and order.order_id != row["oid"])):
            self._pause("Spot: identity ордера не совпадает с журналом")
        if (row["kind"] == "LIMIT" or row["side"] == "SELL") and _number(
                order.raw_data.get("origQty", order.quantity)) != Decimal(row["qty"]):
            self._pause("Spot: исходный объём ордера не совпадает с intent")
        return order

    def _fills(self, row, order):
        raw = order.raw_data
        filled = _number(raw.get("executedQty", order.filled_quantity))
        quote = _number(raw.get("cumulativeQuoteQty", raw.get("cummulativeQuoteQty",
                        filled * _number(order.average_price))))
        if not filled:
            if quote:
                self._pause("Spot: quote fill без base fill")
            return filled, quote, {}
        if hasattr(self.spot, "get_order_trades"):
            trades = self.spot.get_order_trades(row["symbol"], order.order_id)
        else:
            trades = self.spot._transport.request("GET", "/api/v3/myTrades",
                {"symbol": row["symbol"], "orderId": order.order_id, "limit": 1000}, private=True)
        if not isinstance(trades, list) or len(trades) >= 1000:
            _fail("Spot: история fills неполна; требуется recovery", ExchangeStateUnavailableError)
        qty, amount, fees, seen = Decimal(0), Decimal(0), {}, set()
        confirmed_trades = []
        for trade in trades:
            if not isinstance(trade, dict):
                _fail("Spot: fill должен быть объектом", ExchangeStateUnavailableError)
            if str(trade.get("orderId")) != order.order_id:
                continue
            trade_id = trade.get("id")
            tid = str(trade_id)
            if (isinstance(trade_id, bool) or not isinstance(trade_id, (str, int))
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tid)
                    or tid in seen or trade.get("symbol", row["symbol"]) != row["symbol"]):
                self._pause("Spot: недостоверные trade IDs/symbol")
            seen.add(tid)
            trade_qty = _number(trade.get("qty"), positive=True)
            trade_quote = _number(trade.get("quoteQty", trade_qty * _number(trade.get("price"))), positive=True)
            qty += trade_qty
            amount += trade_quote
            asset = trade.get("commissionAsset")
            if not isinstance(asset, str) or not asset:
                self._pause("Spot: актив комиссии неизвестен")
            commission = _number(trade.get("commission"))
            fees[asset] = fees.get(asset, Decimal(0)) + commission
            payload = json.dumps([format(trade_qty.normalize(), "f"), format(trade_quote.normalize(), "f"),
                                  asset, format(commission.normalize(), "f")], separators=(",", ":"))
            confirmed_trades.append((row["cid"], tid, order.order_id, payload))
        if qty != filled or amount != quote or quote <= 0:
            _fail("Spot: order status и фактические fills ещё не согласованы", ExchangeStateUnavailableError)
        previous = {r["trade_id"]: r for r in self._db.execute("SELECT * FROM fills WHERE cid=?", (row["cid"],))}
        if not set(previous).issubset(seen):
            self._pause("Spot: подтверждённые trade IDs исчезли из истории")
        for cid, tid, oid, payload in confirmed_trades:
            if tid in previous and (previous[tid]["oid"] != oid or previous[tid]["payload"] != payload):
                self._pause("Spot: подтверждённый fill изменился; требуется ручная сверка")
        self._db.executemany("INSERT OR IGNORE INTO fills VALUES (?,?,?,?)", confirmed_trades)
        return filled, quote, fees

    def _movement(self, asset, amount):
        self._db.execute("INSERT INTO movements VALUES (?,?,?)", (self.account, asset, str(amount)))

    def _reconcile_row(self, row):
        order = self._query(row)
        if order is None:
            # Даже not found после timeout не даёт права повторять POST.
            _fail("Spot: результат отправки неизвестен; повтор запрещён", UnknownOrderStateError)
        filled, quote, fees = self._fills(row, order)
        old_fees = {a: Decimal(v) for a, v in json.loads(row["fees"]).items()}
        dq, dc = filled - Decimal(row["filled"]), quote - Decimal(row["quote_filled"])
        df = {a: v - old_fees.get(a, Decimal(0)) for a, v in fees.items()}
        if dq < 0 or dc < 0 or any(v < 0 for v in df.values()) or any(a not in fees for a in old_fees):
            self._pause("Spot: накопленные fills/комиссии уменьшились")
        rules = self.get_instrument_rules(row["symbol"])
        owner = self._owner(row["ns"])
        base_fee, quote_fee = df.get(rules.base_asset, Decimal(0)), df.get(rules.quote_asset, Decimal(0))
        cash = Decimal(owner["cash"])
        if row["side"] == "BUY":
            lot = self._db.execute("SELECT * FROM lots WHERE id=?", (row["cid"],)).fetchone()
            qty = (Decimal(lot["qty"]) if lot else Decimal(0)) + dq - base_fee
            cost = (Decimal(lot["cost"]) if lot else Decimal(0)) + dc + quote_fee
            cash -= dc + quote_fee
            if qty < 0 or cash < 0:
                self._pause("Spot: исполнение превысило выделенный бюджет/лот")
            if filled:
                self._db.execute("INSERT INTO lots VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET qty=excluded.qty,cost=excluded.cost",
                    (row["cid"], row["ns"], row["symbol"], str(qty), str(cost), row["sl"], row["tp"]))
                self._db.execute("INSERT OR IGNORE INTO lot_lifecycle VALUES (?,?)", (row["cid"], self._clock()))
            self._movement(rules.base_asset, dq - base_fee)
            self._movement(rules.quote_asset, -dc - quote_fee)
        else:
            remaining = dq + base_fee
            updates = []
            for lot_id in json.loads(row["allocations"]):
                lot = self._db.execute("SELECT * FROM lots WHERE id=? AND ns=?", (lot_id, row["ns"])).fetchone()
                if lot is None:
                    self._pause("Spot: потеряна принадлежность SELL лота")
                owned = Decimal(lot["qty"])
                used = min(remaining, owned)
                cost = Decimal(lot["cost"]) * (owned - used) / owned if owned else Decimal(0)
                updates.append((str(owned - used), str(cost), lot_id))
                remaining -= used
            if remaining > 0:
                self._pause("Spot: SELL/комиссия превышает собственные лоты")
            self._db.executemany("UPDATE lots SET qty=?,cost=? WHERE id=?", updates)
            cash += dc - quote_fee
            self._movement(rules.base_asset, -dq - base_fee)
            self._movement(rules.quote_asset, dc - quote_fee)
        for asset, value in df.items():
            if asset not in {rules.base_asset, rules.quote_asset}:
                self._movement(asset, -value)
        self._db.execute("UPDATE owners SET cash=? WHERE ns=?", (str(cash), row["ns"]))
        self._db.execute("UPDATE commands SET oid=?,status=?,filled=?,quote_filled=?,fees=? WHERE cid=?",
            (order.order_id, order.status.value, str(filled), str(quote),
             json.dumps({a: str(v) for a, v in fees.items()}), row["cid"]))
        self._db.commit()
        return order

    def _sync(self):
        # Сначала все экземпляры аккаунта: sibling fills не считаются ручным выводом.
        if not self.read_only:
            rows = self._account_commands()
            for row in rows:
                if row["status"] not in _TERMINAL:
                    self._reconcile_row(row)
        balances = {}
        for balance in self.spot.get_asset_balances():
            if balance.asset in balances:
                _fail("Spot: повторный актив баланса", ExchangeStateUnavailableError)
            balances[balance.asset] = balance
            _number(balance.free)
            _number(balance.locked)
        pool = self._capital()
        required = {pool["asset"]: Decimal(pool["cash"])} if pool else {}
        owners = self._db.execute("SELECT * FROM owners WHERE account=?", (self.account,)).fetchall()
        for owner in owners:
            required[owner["asset"]] = required.get(owner["asset"], Decimal(0)) + Decimal(owner["cash"])
            for lot in self._rows("lots", ns=owner["ns"]):
                base = self.get_instrument_rules(lot["symbol"]).base_asset
                required[base] = required.get(base, Decimal(0)) + Decimal(lot["qty"])
        movements = {}
        for row in self._db.execute("SELECT * FROM movements WHERE account=?", (self.account,)):
            movements[row["asset"]] = movements.get(row["asset"], Decimal(0)) + Decimal(row["amount"])
        snapshots = {row["asset"]: Decimal(row["total"]) for row in self._db.execute("SELECT * FROM wallets WHERE account=?", (self.account,))}
        for asset in set(balances) | set(required) | set(snapshots):
            actual = balances[asset].total if asset in balances else Decimal(0)
            if actual < required.get(asset, Decimal(0)):
                self._pause(f"Spot: {asset} ниже собственных средств; ручное уменьшение, пауза")
            if asset in snapshots and actual < snapshots[asset] + movements.get(asset, Decimal(0)):
                self._pause(f"Spot: обнаружено внешнее уменьшение {asset}; требуется ручная сверка")
        if not self.read_only:
            for asset in set(balances) | set(snapshots):
                total = balances[asset].total if asset in balances else Decimal(0)
                self._db.execute("INSERT OR REPLACE INTO wallets VALUES (?,?,?)", (self.account, asset, str(total)))
            self._db.execute("DELETE FROM movements WHERE account=?", (self.account,))
            self._db.commit()
            self._reconcile_actions()
        return balances

    def _active(self, symbol):
        return [r for r in self._rows("commands", symbol) if r["status"] not in _TERMINAL]

    def _has_table(self, name):
        return self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def _client_id(self, command_id=None, client_order_id=None):
        return client_order_id or ("se" + hashlib.sha256((self.ns + ":" + str(command_id)).encode()).hexdigest()[:30]
                                   if command_id is not None else "se" + uuid.uuid4().hex[:30])

    def _prepare_meta(self, command_id, cid, symbol, operation, request):
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
        previous = self._db.execute("SELECT * FROM command_meta WHERE ns=? AND command_id=?", (self.ns, command_id)).fetchone()
        if previous is not None:
            if (previous["cid"] != cid or previous["symbol"] != symbol
                    or previous["operation"] != operation or previous["request"] != encoded):
                _fail("Spot: повторный ID с другой командой")
            return previous
        if cid is not None and self._db.execute("SELECT 1 FROM command_meta WHERE ns=? AND cid=?", (self.ns, cid)).fetchone():
            _fail("Spot: clientOrderId уже связан с другим command_id", ExchangeStateUnavailableError)
        self._db.execute("INSERT INTO command_meta VALUES (?,?,?,?,?,?,'PREPARED',NULL,?)",
                         (self.ns, command_id, cid, symbol, operation, encoded, self._clock()))
        self._db.commit()
        return None

    def _finish_meta(self, command_id, state, result):
        self._db.execute("UPDATE command_meta SET state=?,result=? WHERE ns=? AND command_id=?",
                         (state, json.dumps(result, sort_keys=True), self.ns, command_id))
        self._db.commit()

    def _command_view(self, meta):
        command = (self._db.execute("SELECT * FROM commands WHERE ns=? AND cid=?", (self.ns, meta["cid"])).fetchone()
                   if meta["cid"] else None)
        request = json.loads(meta["request"])
        result = json.loads(meta["result"]) if meta["result"] else {}
        state = meta["state"]
        if command is not None:
            status = command["status"]
            filled = Decimal(command["filled"])
            state = ("FILLED" if status == "FILLED" else "REJECTED" if status == "REJECTED"
                     else "PARTIAL" if filled > 0 else "RESOLVED" if status in _TERMINAL
                     else "UNKNOWN" if status == "INTENT" else "ACK")
            pids = json.loads(command["allocations"]) if command["side"] == "SELL" else [command["cid"]]
            remaining = sum((Decimal(r[0]) for pid in pids for r in
                             self._db.execute("SELECT qty FROM lots WHERE ns=? AND id=?", (self.ns, pid))), Decimal(0))
            close_confirmed = status == "FILLED" and command["side"] == "SELL"
            if request.get("expected_remaining") is not None:
                expected = Decimal(request["expected_remaining"])
                # Base fee также уменьшает экспозицию. Partial подтверждаем по
                # net reduction; full close — только при строго нулевом остатке.
                reduced = remaining == 0 if Decimal(request.get("percentage") or 1) == 1 else remaining <= expected
                close_confirmed = close_confirmed and reduced
            result = {"order_id": command["oid"], "filled_quantity": str(filled),
                      "average_price": str(Decimal(command["quote_filled"]) / filled if filled else 0),
                      "position_id": pids[0] if len(pids) == 1 else None,
                      "remaining_quantity": str(remaining), "close_confirmed": close_confirmed,
                      "entry_complete": status == "FILLED" and command["side"] == "BUY",
                      "terminal": status in _TERMINAL, "protection_mode": "local_only"}
        return {"account": self.account_id, "owner": self.owner_id, "command_id": meta["command_id"],
                "client_id": meta["cid"], "symbol": meta["symbol"], "operation": meta["operation"],
                "state": state, "request": request, "context": {},
                "order_id": command["oid"] if command is not None else result.get("order_id"),
                "result": result, "updated": meta["created"]}

    def get_execution_command(self, command_id):
        with self._guard():
            if not self._has_table("command_meta"):
                return None
            row = self._db.execute("SELECT * FROM command_meta WHERE ns=? AND command_id=?", (self.ns, str(command_id))).fetchone()
            return self._command_view(row) if row else None

    def get_execution_commands(self, symbol=None, *, pending_only=False):
        with self._guard():
            if not self._has_table("command_meta"):
                return []
            rows = self._db.execute("SELECT * FROM command_meta WHERE ns=? ORDER BY created,command_id", (self.ns,)).fetchall()
            return [view for row in rows for view in [self._command_view(row)]
                    if (symbol is None or view["symbol"] == self.normalize_symbol(symbol))
                    and (not pending_only or view["state"] in {"PREPARED", "ACK", "UNKNOWN", "PARTIAL"})]

    def _lot_snapshot(self, lot):
        command = self._db.execute("SELECT * FROM commands WHERE cid=? AND ns=?", (lot["id"], self.ns)).fetchone()
        lifecycle = (self._db.execute("SELECT opened_at FROM lot_lifecycle WHERE id=?", (lot["id"],)).fetchone()
                     if self._has_table("lot_lifecycle") else None)
        return {"account": self.account_id, "owner": self.owner_id, "symbol": lot["symbol"],
                "position_id": lot["id"], "side": "LONG", "quantity": lot["qty"],
                "snapshot": {"entry_order_id": command["oid"] if command else None,
                             "client_order_id": lot["id"], "opened_at": lifecycle[0] if lifecycle else None,
                             "opened_at_source": "first_fill_observed" if lifecycle else "unknown",
                             "protection_mode": "local_only", "protection": {"sl": lot["sl"], "tp": lot["tp"]},
                             "triggered": bool(lot["triggered"]), "cost": lot["cost"]}}

    def get_position_execution_snapshot(self, symbol, position_id):
        with self._guard():
            lot = next((r for r in self._rows("lots", symbol) if r["id"] == str(position_id)), None)
            if lot is None:
                _fail("Spot: нет snapshot собственного лота", ExchangeStateUnavailableError)
            return {**self._lot_snapshot(lot), "protections": [r for r in self.journal.protections(
                self.account_id, owner=self.owner_id, symbol=symbol) if r["position_id"] == str(position_id)]}

    def position_command_id(self, symbol, position_id, operation):
        snapshot = self.get_position_execution_snapshot(symbol, position_id)
        token = f"{self.account_id}:{self.owner_id}:{self.normalize_symbol(symbol)}:{position_id}:{snapshot['snapshot']['client_order_id']}:{operation}"
        return hashlib.sha256(token.encode()).hexdigest()

    def get_sellable_quantity(self, symbol, position_id=None, quantity=None):
        """GRID/core могут заранее согласовать точный LIMIT SELL с base fee reserve."""
        with self._guard():
            balances = self._sync()
            rules = self.get_instrument_rules(symbol)
            lots = [r for r in self._rows("lots", symbol) if Decimal(r["qty"]) > 0
                    and (position_id is None or r["id"] == str(position_id))]
            reserved = {pid for r in self._active(symbol) if r["side"] == "SELL"
                        for pid in json.loads(r["allocations"])}
            owned = sum((Decimal(r["qty"]) for r in lots if r["id"] not in reserved), Decimal(0))
            fee = self.get_commission_rate(symbol)
            if fee is None:
                _fail("Spot: комиссия SELL неизвестна", ExchangeStateUnavailableError)
            free = balances[rules.base_asset].free if rules.base_asset in balances else Decimal(0)
            cap = min(owned / (1 + _number(max(fee.maker, fee.taker)) / 100), free, rules.max_quantity or owned)
            if quantity is not None:
                cap = min(cap, _number(quantity, positive=True))
            return _floor(cap, rules.quantity_step)

    def owns_position(self, symbol, position_id):
        with self._guard():
            return any(r["id"] == str(position_id) and Decimal(r["qty"]) > 0 for r in self._rows("lots", symbol))

    def _symbol_pause(self, symbol):
        if not self._has_table("symbol_pauses"):
            return None
        row = self._db.execute("SELECT reason FROM symbol_pauses WHERE account=? AND symbol=?",
                               (self.account, self.normalize_symbol(symbol))).fetchone()
        return row[0] if row else None

    def pause_symbol(self, symbol, reason):
        with self._guard():
            if self.read_only:
                _fail("Spot: read_only запрещает изменение пауз")
            self._db.execute("INSERT OR REPLACE INTO symbol_pauses VALUES (?,?,?)",
                             (self.account, self.normalize_symbol(symbol), str(reason)))
            self._db.commit()
            warning(f"⏸️ Spot {symbol}: {reason}; автоматическое снятие паузы запрещено")

    def _block_reason(self, symbol, *, ignore=()):
        reason = self.paused_reason or self._symbol_pause(symbol) or self._recovery_errors.get(self.normalize_symbol(symbol))
        if reason:
            return reason
        ignored_ids = {self._client_id(command_id=key) for key in ignore} | set(ignore)
        for view in self.get_execution_commands(symbol, pending_only=True):
            if view["command_id"] not in ignore and view["client_id"] not in ignored_ids:
                # Terminal partial LIMIT с локальной защитой сохраняет lot и не
                # блокирует безопасные GRID-операции; wait_for_entry всё равно не True.
                row = self._db.execute("SELECT status FROM commands WHERE cid=?", (view["client_id"],)).fetchone()
                if row is None or row[0] not in _TERMINAL:
                    return f"{view['state']}: {view['command_id']}"
        return next((f"{r['status']}: {r['cid']}" for r in self._active(symbol) if r["cid"] not in ignored_ids), None)

    def recover(self, symbol=None):
        """Только GET + durable сверка; не POST, не auto-clear пользовательских пауз."""
        with self._guard():
            self.validate_execution_context()
            symbols = {r["symbol"] for r in self._rows("commands")} | {r["symbol"] for r in self._rows("lots")}
            if symbol is not None:
                symbols.add(self.normalize_symbol(symbol))
            try:
                self._sync()
                self._recovery_errors.clear()
            except ExchangeError as exc:
                self._recovery_errors.update({key: str(exc) for key in symbols})
                return {key: self.paused_reason or str(exc) for key in symbols}
            return {key: reason for key in symbols if (symbol is None or key == self.normalize_symbol(symbol))
                    and (reason := self._block_reason(key))}

    def reconcile_pending(self, symbol=None):
        return self.recover(symbol)

    def is_symbol_blocked(self, symbol):
        self.recover(symbol)
        with self._guard():
            return self._block_reason(symbol) is not None

    def closure_confirmed(self, symbol, position_id):
        with self._guard():
            self._sync()
            lot = next((r for r in self._rows("lots", symbol) if r["id"] == str(position_id)), None)
            if lot is None or Decimal(lot["qty"]) != 0 or self._block_reason(symbol):
                return False
            return any(r["side"] == "SELL" and r["status"] == "FILLED"
                       and str(position_id) in json.loads(r["allocations"]) for r in self._rows("commands", symbol))

    def get_owned_positions(self, symbol=None):
        positions = self.get_positions()
        result = {}
        for key, rows in positions.items():
            if symbol is not None and self.normalize_symbol(symbol) != key:
                continue
            if self.is_symbol_blocked(key):
                _fail("Spot: owned positions заблокированы неразрешённой командой/паузой", ExchangeStateUnavailableError)
            self.verify_protection(key)
            result[key] = rows
        return result

    def verify_protection(self, symbol, *, position_id=None, _apply_policy=True):
        with self._guard():
            self.last_protection_confirmed = False
            self._sync()
            tracked = [r for r in self._rows("lots", symbol) if position_id is None or r["id"] == str(position_id)]
            if position_id is not None and not tracked:
                _fail("Spot: verify_protection требует собственного position_id")
            lots = [r for r in tracked if Decimal(r["qty"]) > 0]
            valid = (not lots or (self.local_protection and (self.read_only or self.supervisor_running)
                     and all(r["sl"] and Decimal(r["sl"]) > 0
                             and (not r["tp"] or Decimal(r["tp"]) > Decimal(r["sl"])) for r in lots)))
            self.last_protection_confirmed = bool(lots) and valid
            if not valid:
                if _apply_policy and not self.read_only:
                    self.pause_symbol(symbol, "LOCAL_PROTECTION_UNAVAILABLE")
                _fail("Spot: локальная защита/живой supervisor не подтверждены", ExchangeStateUnavailableError)
            return True

    def wait_for_entry(self, symbol, order_id, *, timeout=None, cancel_on_timeout=True):
        duration = float(_number(self.settings["protection"]["verification_timeout_seconds"] if timeout is None else timeout))
        duration = min(duration, self.settings["protection"]["verification_timeout_seconds"])
        attempts = max(1, self.settings["protection"]["recovery_attempts"])
        for attempt in range(attempts):
            with self._guard():
                row = next((r for r in self._rows("commands", symbol) if r["oid"] == str(order_id) and r["side"] == "BUY"), None)
                if row is None:
                    _fail("Spot: wait_for_entry требует собственного BUY orderId")
                order = self.get_order(symbol, order_id)
                self._sync()
                if order.status == OrderStatus.FILLED:
                    if self.read_only and row["status"] != "FILLED":
                        _fail("Spot: read_only не подтверждает новые fills", UnknownOrderStateError)
                    self.verify_protection(symbol)
                    self.last_execution_result = self._receipt_for_cid(row["cid"])
                    return True
                if order.status.value in _TERMINAL:
                    if order.filled_quantity == 0:
                        return False
                    if not self.read_only:
                        self.pause_symbol(symbol, "ENTRY_PARTIAL_REQUIRES_ACKNOWLEDGEMENT")
                    _fail("Spot: terminal partial entry; lot сохранён, полный вход не подтверждён", UnknownOrderStateError)
            if attempt < attempts - 1 and duration:
                time.sleep(duration / max(1, attempts - 1))
        if cancel_on_timeout and not self.read_only:
            self.cancel_order(symbol, order_id, command_id=f"entry-timeout:{order_id}")
            return self.wait_for_entry(symbol, order_id, timeout=0, cancel_on_timeout=False)
        _fail("Spot: entry остаётся pending/partial; повторная отправка запрещена", UnknownOrderStateError)

    def _reconcile_actions(self):
        for meta in self._db.execute("SELECT * FROM command_meta WHERE ns=? AND cid IS NULL AND state IN ('PREPARED','UNKNOWN')", (self.ns,)).fetchall():
            request = json.loads(meta["request"])
            if meta["operation"] == "cancel":
                command = next((r for r in self._rows("commands", meta["symbol"]) if r["oid"] == request["order_id"]), None)
                if command is not None and command["status"] in _TERMINAL:
                    self._finish_meta(meta["command_id"], "RESOLVED", {
                        "order_id": command["oid"], "cancel_confirmed": command["status"] == "CANCELED"})
            elif meta["operation"] == "protection":
                lots = [r for r in self._rows("lots", meta["symbol"])
                        if request["position_id"] is None or r["id"] == request["position_id"]]
                rules = self.get_instrument_rules(meta["symbol"])
                expected_sl = _floor(_number(request["sl"], positive=True), rules.price_step) if request["sl"] is not None else None
                expected_tp = _floor(_number(request["tp"], positive=True), rules.price_step) if request["tp"] is not None else None
                if (lots and all(r["sl"] and (expected_sl is None or Decimal(r["sl"]) == expected_sl)
                                 and (expected_tp is None or Decimal(r["tp"] or 0) == expected_tp) for r in lots)):
                    self._finish_meta(meta["command_id"], "RESOLVED", {
                        "protection_confirmed": True, "confirmation_scope": "local_registration",
                        "protection_mode": "local_only", "native": False, "position_id": request["position_id"]})

    def _receipt_for_cid(self, cid):
        if not self._has_table("command_meta"):
            return None
        meta = self._db.execute("SELECT * FROM command_meta WHERE ns=? AND cid=?", (self.ns, cid)).fetchone()
        return self._command_view(meta)["result"] if meta else None

    def get_balance(self):
        with self._guard():
            balances = self._sync()
            cash, reserved = self._quote_state()
            wallet_free = balances[self.quote_asset].free if self.quote_asset in balances else Decimal(0)
            available = max(Decimal(0), min(cash - reserved, wallet_free))
            # Не прибавляем ручные монеты и не выдаём locked за доступные средства.
            return Balance(float(cash), float(available), locked_balance=float(max(Decimal(0), cash - available)), asset=self.quote_asset)

    def get_positions(self):
        with self._guard():
            self._sync()
            result = {}
            for lot in self._rows("lots"):
                qty = Decimal(lot["qty"])
                if qty <= 0:
                    continue
                mark = self._live_price(lot["symbol"])
                cost = Decimal(lot["cost"])
                result.setdefault(lot["symbol"], []).append(Position(
                    lot["symbol"], PositionSide.LONG, float(qty), float(cost / qty),
                    float(qty * mark - cost), leverage=1, position_id=lot["id"],
                    mark_price=float(mark), margin=float(cost), exchange_quantity=float(qty), contract_size=1,
                    created_at=(datetime.fromtimestamp(opened, timezone.utc) if
                                (opened := self._lot_snapshot(lot)["snapshot"]["opened_at"]) is not None else None)))
            return result

    def _live_price(self, symbol):
        key, now = StreamKey("mexc", "spot", symbol, "1m"), self._clock()
        try:
            snapshot = (self._snapshot_reader(key) if self._snapshot_reader else
                        read_market_snapshot(self._data_dir, key, allow_stale=True, max_age_seconds=self._tick_max_age))
            if snapshot and StreamKey.from_value(snapshot["stream"]) == key:
                candle = snapshot["candles"][-1]
                age = now - float(snapshot["last_valid_update"])
                candle_age = now * 1000 - timestamp_ms(candle["timestamp"])
                if (snapshot.get("status") not in {"error", "stale"} and not snapshot.get("stale", False)
                        and 0 <= age <= self._tick_max_age and 0 <= candle_age < 60000
                        and not candle.get("is_closed", True)
                        and timestamp_ms(candle["timestamp"]) == interval_start(int(now * 1000), "1m")):
                    return _number(candle["closePrice"], positive=True)
        except (KeyError, IndexError, ValueError, TypeError, OSError):
            pass
        last = self._ticker_attempts.get(symbol)
        if last is None or now - last >= self._ticker_interval:
            self._ticker_attempts[symbol] = now
            ticker = self.spot.get_ticker(symbol)
            if self.normalize_symbol(ticker.symbol) != key.symbol:
                _fail("Spot: неверный символ публичного тикера", ExchangeStateUnavailableError)
            self._ticker_cache[symbol] = (now, _number(ticker.last_price, positive=True))
        cached = self._ticker_cache.get(symbol)
        if cached is None or not 0 <= now - cached[0] <= self._tick_max_age:
            _fail("Spot: нет свежей цены для локальной защиты", ExchangeStateUnavailableError)
        return cached[1]

    def submit_order(self, symbol, side, quantity, order_type=OrderType.MARKET,
                     price=None, sl=None, tp=None, position_side=None, leverage=None,
                     *, command_id=None, client_order_id=None, position_id=None,
                     _operation=None, _expected_remaining=None, _percentage=None):
        symbol, side, kind = self.normalize_symbol(symbol), OrderSide(side), OrderType(order_type)
        cid = self._client_id(command_id, client_order_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(cid)):
            _fail("Spot: clientOrderId должен иметь 1..32 безопасных символа")
        command_id = str(command_id if command_id is not None else cid)
        request = {"side": side.value, "quantity": str(quantity), "order_type": kind.value,
                   "price": str(price) if price is not None else None,
                   "sl": str(sl) if sl is not None else None, "tp": str(tp) if tp is not None else None,
                   "position_id": str(position_id) if position_id is not None else None,
                   "position_side": getattr(position_side, "value", position_side),
                   "leverage": str(leverage) if leverage is not None else None,
                   "expected_remaining": _expected_remaining, "percentage": _percentage}
        operation = _operation or ("entry" if side == OrderSide.BUY else "close")
        with self._guard():
            self._mutation_allowed()
            self.last_execution_result = None
            self._prepare_meta(command_id, cid, symbol, operation, request)
            try:
                result = self._submit_order(symbol, side, quantity, kind, price, sl, tp,
                    position_side, leverage, command_id=command_id, client_order_id=cid, position_id=position_id)
                self.last_execution_result = self._receipt_for_cid(cid)
                self._finish_meta(command_id, "ACK", self.last_execution_result)
                return result
            except Exception as exc:
                row = self._db.execute("SELECT status FROM commands WHERE ns=? AND cid=?", (self.ns, cid)).fetchone()
                if row is None:
                    self._finish_meta(command_id, "REJECTED", {"not_sent": True})
                    exc.submission_status = "NOT_SENT"
                elif row["status"] == "INTENT":
                    self._finish_meta(command_id, "UNKNOWN", {"reconciliation_required": True})
                exc.symbol, exc.command_id, exc.client_order_id = symbol, command_id, cid
                raise

    def _submit_order(self, symbol, side, quantity, order_type=OrderType.MARKET,
                      price=None, sl=None, tp=None, position_side=None, leverage=None,
                      *, command_id=None, client_order_id=None, position_id=None):
        side, kind = OrderSide(side), OrderType(order_type)
        symbol = self.normalize_symbol(symbol)
        cid = client_order_id or ("se" + hashlib.sha256((self.ns + ":" + str(command_id)).encode()).hexdigest()[:30]
                                  if command_id is not None else "se" + uuid.uuid4().hex[:30])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cid):
            raise ValueError("Spot: clientOrderId должен иметь 1..32 безопасных символа")
        fingerprint = json.dumps([symbol, side.value, str(quantity), kind.value, str(price), str(sl), str(tp),
                                  str(position_side), str(leverage), position_id], separators=(",", ":"))
        with self._guard():
            self._mutation_allowed()
            existing = self._db.execute("SELECT * FROM commands WHERE cid=?", (cid,)).fetchone()
            if existing:
                if existing["ns"] != self.ns or existing["fingerprint"] != fingerprint:
                    _fail("Spot: повторный ID с другой командой")
                order = self._reconcile_row(existing)
                return self._submission(order, cid)
            if kind not in {OrderType.MARKET, OrderType.LIMIT}:
                _fail("Spot: разрешены только MARKET/LIMIT")
            self.set_leverage(symbol, 1 if leverage is None else leverage,
                              PositionSide.LONG if position_side is None else position_side)
            rules = self.get_instrument_rules(symbol)
            if rules.quote_asset != self.quote_asset or not rules.tradable or kind.value not in rules.order_types:
                _fail("Spot: инструмент/quote_asset/тип не разрешён")
            qty = _floor(_number(quantity, positive=True), rules.quantity_step)
            limit_price = _floor(_number(price, positive=True), rules.price_step) if kind == OrderType.LIMIT else None
            if qty < rules.min_quantity or qty <= 0 or (side == OrderSide.BUY and rules.max_quantity and qty > rules.max_quantity):
                _fail("Spot: объём вне правил инструмента")
            if limit_price is not None and limit_price <= 0:
                _fail("Spot: LIMIT price округлился до нуля")
            balances = self._sync()
            active = self._active(symbol)
            if any(r["status"] == "INTENT" or r["kind"] == "MARKET" for r in active):
                _fail("Spot: есть неизвестная/незавершённая MARKET команда", ExchangeStateUnavailableError)
            if side == OrderSide.BUY:
                if self._symbol_pause(symbol):
                    _fail("Spot: символ приостановлен оператором/защитой", ExchangeStateUnavailableError)
                if kind == OrderType.MARKET and active:
                    _fail("Spot: MARKET BUY запрещён при pending LIMIT", ExchangeStateUnavailableError)
            stop = _floor(_number(sl, positive=True), rules.price_step) if sl is not None else None
            take = _floor(_number(tp, positive=True), rules.price_step) if tp is not None else None
            allocations, budget = [], Decimal(0)
            if side == OrderSide.BUY:
                if not self.local_protection or not self.supervisor_running or stop is None:
                    _fail("Spot: BUY требует согласия на LOCAL защиту, живого supervisor и SL")
                reference = limit_price or self._live_price(symbol)
                if stop <= 0 or stop >= reference or (take is not None and take <= reference):
                    _fail("Spot: неверный SL/TP для LONG")
                budget = qty * reference if kind == OrderType.LIMIT else _floor(qty * reference, rules.price_step)
                fee = self.get_commission_rate(symbol)
                if fee is None:
                    _fail("Spot: комиссия для расчёта бюджета неизвестна", ExchangeStateUnavailableError)
                fee_rate = _number(fee.taker if kind == OrderType.MARKET else max(fee.maker, fee.taker)) / 100
                reserved_budget = budget * (1 + fee_rate)
                reserved = sum((max(Decimal(0), Decimal(r["budget"]) - Decimal(r["quote_filled"]))
                                for r in self._rows("commands")
                                if r["side"] == "BUY" and r["status"] not in _TERMINAL), Decimal(0))
                free_quote = balances[self.quote_asset].free if self.quote_asset in balances else Decimal(0)
                quote_cash, all_reserved = self._quote_state()
                if reserved_budget > quote_cash - all_reserved or reserved_budget > free_quote:
                    _fail("Spot: выделенного свободного quote-бюджета недостаточно")
                self._fund_owner(reserved_budget, reserved)
            else:
                lots = [r for r in self._rows("lots", symbol)
                        if Decimal(r["qty"]) > 0 and (position_id is None or r["id"] == position_id)]
                selected_ids = {r["id"] for r in lots}
                if any(r["side"] == "SELL" and selected_ids.intersection(json.loads(r["allocations"])) for r in active):
                    _fail("Spot: pending SELL уже резервирует выбранный лот", ExchangeStateUnavailableError)
                if any(r["side"] == "BUY" and r["cid"] in selected_ids for r in active):
                    _fail("Spot: сначала отмените pending BUY выбранного лота", ExchangeStateUnavailableError)
                owned = sum((Decimal(r["qty"]) for r in lots), Decimal(0))
                if not owned:
                    _fail("Spot: SELL без собственных LONG лотов запрещён; SHORT не создаётся")
                fees = self.get_commission_rate(symbol)
                if fees is None:
                    _fail("Spot: комиссия SELL неизвестна", ExchangeStateUnavailableError)
                fee_rate = _number(max(fees.maker, fees.taker)) / 100
                # Пока актив комиссии неизвестен, резервируем её в base. Иначе
                # полный SELL может списать комиссию из ручных монет кошелька.
                safe_owned = owned / (1 + fee_rate)
                if kind == OrderType.LIMIT and qty > _floor(safe_owned, rules.quantity_step):
                    _fail("Spot: LIMIT SELL превышает собственный объём с резервом комиссии")
                qty = _floor(min(qty, safe_owned, rules.max_quantity or owned), rules.quantity_step)
                if qty <= 0 or qty < rules.min_quantity:
                    _fail("Spot: остаток собственного лота ниже минимального; ручная обработка dust")
                if rules.base_asset not in balances or qty > balances[rules.base_asset].free:
                    _fail("Spot: собственного свободного base недостаточно", ExchangeStateUnavailableError)
                allocations = [r["id"] for r in lots]
                reserved_budget = Decimal(0)
                stop = take = None
            request = SpotOrderRequest(symbol, side, kind,
                base_quantity=None if side == OrderSide.BUY and kind == OrderType.MARKET else qty,
                quote_quantity=budget if side == OrderSide.BUY and kind == OrderType.MARKET else None,
                price=limit_price, client_order_id=cid)
            # Intent и защита фиксируются ДО POST; crash/timeout никогда не повторяет POST.
            self._db.execute("INSERT INTO commands (cid,ns,symbol,side,kind,qty,budget,price,status,sl,tp,allocations,fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, self.ns, symbol, side.value, kind.value, str(qty), str(reserved_budget),
                 str(limit_price) if limit_price is not None else None, "INTENT",
                 str(stop) if stop is not None else None, str(take) if take is not None else None,
                 json.dumps(allocations), fingerprint))
            self._db.commit()
            try:
                submission = self.spot.place_spot_order(request)
            except Exception as exc:
                # Даже исключение до ACK не доказывает отсутствие принятого ордера.
                self.last_submission = OrderSubmission(SubmissionStatus.UNKNOWN, client_order_id=cid,
                    message="Spot: отправка требует сверки по clientOrderId")
                raise UnknownOrderStateError(self.last_submission.message) from exc
            self.last_submission = submission
            if (submission.status != SubmissionStatus.ACKNOWLEDGED or not submission.order_id
                    or submission.client_order_id != cid):
                _fail("Spot: ACK не подтверждён; требуется recovery", UnknownOrderStateError)
            self._db.execute("UPDATE commands SET oid=?,status='NEW' WHERE cid=?", (submission.order_id, cid))
            self._db.commit()
            row = self._db.execute("SELECT * FROM commands WHERE cid=?", (cid,)).fetchone()
            order = self._reconcile_row(row)
            # ACK сам по себе никогда не подтверждает даже локальную защиту.
            self.last_protection_confirmed = side == OrderSide.BUY and order.filled_quantity > 0 and stop is not None
            return self._submission(order, cid)

    def _submission(self, order, cid):
        self.last_submission = OrderSubmission(SubmissionStatus.ACKNOWLEDGED, order.order_id, cid,
            message="Spot: ордер сверён; защита только local_registration", raw_data=order.raw_data)
        return self.last_submission

    def place_order(self, symbol, side, quantity, order_type=OrderType.MARKET, price=None,
                    sl=None, tp=None, position_side=None, leverage=None, **kwargs):
        submission = self.submit_order(symbol, side, quantity, order_type, price, sl, tp,
                                       position_side, leverage, **kwargs)
        if OrderType(order_type) == OrderType.MARKET:
            order = self.get_order(symbol, submission.order_id)
            if order.status != OrderStatus.FILLED:
                exc = UnknownOrderStateError("Spot: MARKET ACK без полного fill; повтор запрещён")
                exc.symbol, exc.order_id, exc.client_order_id = symbol, submission.order_id, submission.client_order_id
                raise exc
            self.verify_protection(symbol)
        return submission.order_id

    def get_order(self, symbol, order_id=None, *, client_order_id=None):
        with self._guard():
            rows = [r for r in self._rows("commands", symbol)
                    if (client_order_id is not None and r["cid"] == client_order_id)
                    or (order_id is not None and r["oid"] == str(order_id))]
            if len(rows) != 1:
                _fail("Spot: ордер не принадлежит этому экземпляру")
            return self._query(rows[0]) if self.read_only else self._reconcile_row(rows[0])

    def close_position(self, symbol, position_id, percentage=1.0, *, command_id=None, client_order_id=None):
        fraction = _number(percentage, positive=True)
        if fraction > 1:
            raise ValueError("Spot: percentage должен быть в (0,1]")
        symbol, position_id = self.normalize_symbol(symbol), str(position_id)
        with self._guard():
            self._mutation_allowed()
            cid = self._client_id(command_id, client_order_id)
            previous = self._db.execute("SELECT * FROM commands WHERE cid=?", (cid,)).fetchone()
            if previous is not None:
                if (previous["ns"] != self.ns or previous["symbol"] != symbol or previous["side"] != "SELL"
                        or json.loads(previous["allocations"]) != [position_id]):
                    _fail("Spot: повторный ID закрытия относится к другой позиции")
                meta = self._db.execute("SELECT * FROM command_meta WHERE ns=? AND cid=?", (self.ns, cid)).fetchone()
                if meta:
                    request = json.loads(meta["request"])
                    if request.get("percentage") is None or Decimal(request["percentage"]) != fraction or (
                            command_id is not None and meta["command_id"] != str(command_id)):
                        _fail("Spot: повторный ID закрытия с другим percentage/command_id", ExchangeStateUnavailableError)
                self._reconcile_row(previous)
                self._sync()
                self.last_execution_result = self._receipt_for_cid(cid)
                return bool(self.last_execution_result and self.last_execution_result["close_confirmed"])
            self._sync()
            # Полное/частичное закрытие не оставляет entry или LIMIT SELL того же
            # лота, который мог бы позднее снова увеличить/уменьшить inventory.
            for pending in self._active(symbol):
                if ((pending["side"] == "BUY" and pending["cid"] == position_id)
                        or (pending["side"] == "SELL" and position_id in json.loads(pending["allocations"]))):
                    if pending["kind"] != "LIMIT":
                        _fail("Spot: pending MARKET/UNKNOWN запрещает повтор close", UnknownOrderStateError)
                    self.cancel_order(symbol, pending["oid"], command_id=f"close-cancel:{cid}:{pending['oid']}")
            lot = next((r for r in self._rows("lots", symbol) if r["id"] == position_id), None)
            if lot is None or Decimal(lot["qty"]) <= 0:
                _fail("Spot: закрытие чужого/пустого лота запрещено")
            rules = self.get_instrument_rules(symbol)
            quantity = _floor(Decimal(lot["qty"]) * fraction, rules.quantity_step)
            expected = "0" if fraction == 1 else str(Decimal(lot["qty"]) - quantity)
            submission = self.submit_order(symbol, OrderSide.SELL, quantity,
                position_id=position_id, command_id=command_id, client_order_id=cid,
                _operation="close", _expected_remaining=expected, _percentage=str(fraction))
            self.get_order(symbol, submission.order_id)
            self._sync()
            self.last_execution_result = self._receipt_for_cid(cid)
            # Dust/резерв комиссии не выдаём за завершённое полное закрытие.
            return bool(self.last_execution_result and self.last_execution_result["close_confirmed"])

    def set_sl_tp(self, symbol, position_side, sl=None, tp=None, quantity=None, *, position_id=None, command_id=None):
        with self._guard():
            self._mutation_allowed()
            if not self.local_protection or PositionSide(position_side) not in {PositionSide.LONG, PositionSide.BOTH}:
                _fail("Spot: доступна только локальная защита LONG")
            symbol = self.normalize_symbol(symbol)
            command_id = str(command_id or "local-protection:" + uuid.uuid4().hex)
            request = {"position_id": str(position_id) if position_id is not None else None,
                       "sl": str(sl) if sl is not None else None, "tp": str(tp) if tp is not None else None,
                       "quantity": str(quantity) if quantity is not None else None,
                       "position_side": PositionSide(position_side).value}
            self._sync()
            rules = self.get_instrument_rules(symbol)
            stop = _floor(_number(sl, positive=True), rules.price_step) if sl is not None else None
            take = _floor(_number(tp, positive=True), rules.price_step) if tp is not None else None
            lots = [r for r in self._rows("lots", symbol) if Decimal(r["qty"]) > 0
                    and (position_id is None or r["id"] == position_id)]
            if not lots:
                _fail("Spot: защита требует подтверждённых собственных fills")
            owned = sum((Decimal(r["qty"]) for r in lots), Decimal(0))
            if quantity is not None and _number(quantity, positive=True) != owned:
                _fail("Spot: локальная защита должна покрывать весь выбранный объём")
            levels = [(r, str(stop) if stop is not None else r["sl"],
                       str(take) if take is not None else r["tp"]) for r in lots]
            if any(not new_sl or Decimal(new_sl) <= 0 or (new_tp and Decimal(new_tp) <= Decimal(new_sl))
                   for _, new_sl, new_tp in levels):
                _fail("Spot: обязательный локальный SL/TP некорректен")
            previous = self._prepare_meta(command_id, None, symbol, "protection", request)
            if previous is not None and previous["state"] == "RESOLVED":
                if any((stop is not None and Decimal(r["sl"] or 0) != stop)
                       or (take is not None and Decimal(r["tp"] or 0) != take) for r in lots):
                    _fail("Spot: повтор старой защиты не должен отменять более новые уровни", ExchangeStateUnavailableError)
                self.last_protection_confirmed = True
                self.last_execution_result = json.loads(previous["result"])
                return True
            for lot, new_sl, new_tp in levels:
                self._db.execute("UPDATE lots SET sl=?,tp=? WHERE id=?", (new_sl, new_tp, lot["id"]))
                # Сохраняем новые уровни и для будущих частичных fills того же BUY.
                self._db.execute("UPDATE commands SET sl=?,tp=? WHERE cid=?", (new_sl, new_tp, lot["id"]))
            self._db.commit()
            self.last_protection_confirmed = True
            self.last_execution_result = {"protection_confirmed": True, "confirmation_scope": "local_registration",
                                          "protection_mode": "local_only", "native": False,
                                          "position_id": str(position_id) if position_id is not None else None}
            self._finish_meta(command_id, "RESOLVED", self.last_execution_result)
            info("✅ Spot: LOCAL SL/TP сохранены; это НЕ биржевые защитные ордера")
            return True

    def check_protection(self, symbol, *, position_id=None):
        with self._guard():
            lots = [r for r in self._rows("lots", symbol) if Decimal(r["qty"]) > 0
                    and (position_id is None or r["id"] == position_id)]
            return (self.local_protection and bool(lots)
                    and all(r["sl"] and Decimal(r["sl"]) > 0 for r in lots))

    def cancel_order(self, symbol, order_id, *, command_id=None):
        with self._guard():
            self._mutation_allowed()
            symbol, order_id = self.normalize_symbol(symbol), str(order_id)
            # Сначала доказанная ownership, затем cancel intent до HTTP.
            order = self.get_order(symbol, order_id)
            command_id = str(command_id or f"cancel:{order_id}")
            if order.status.value not in _TERMINAL and any(
                    r["operation"] == "cancel" and r["request"].get("order_id") == order_id
                    for r in self.get_execution_commands(symbol, pending_only=True)):
                _fail("Spot: предыдущая отмена не разрешена; повтор DELETE запрещён", UnknownOrderStateError)
            previous = self._prepare_meta(command_id, None, symbol, "cancel", {"order_id": order_id})
            if order.status.value in _TERMINAL:
                result = {"order_id": order_id, "cancel_confirmed": order.status == OrderStatus.CANCELED}
                self._finish_meta(command_id, "RESOLVED", result)
                self.last_execution_result = result
                return result["cancel_confirmed"]
            if previous is not None:
                _fail("Spot: предыдущая отмена не разрешена; повтор DELETE запрещён", UnknownOrderStateError)
            self.spot.restore_order_ownership(symbol, order.order_id)
            try:
                self.spot.cancel_order(symbol, order.order_id)
                confirmed = self.get_order(symbol, order_id)
                if confirmed.status.value not in _TERMINAL:
                    _fail("Spot: отмена не подтверждена", UnknownOrderStateError)
            except Exception:
                self._finish_meta(command_id, "UNKNOWN", {"order_id": order_id, "reconciliation_required": True})
                raise
            result = {"order_id": order_id, "cancel_confirmed": confirmed.status == OrderStatus.CANCELED}
            self._finish_meta(command_id, "RESOLVED", result)
            self.last_execution_result = result
            self._sync()
            return result["cancel_confirmed"]

    def get_open_orders(self, symbol=None):
        with self._guard():
            result = []
            for row in self._rows("commands", symbol):
                if row["status"] not in _TERMINAL:
                    order = self._query(row) if self.read_only else self._reconcile_row(row)
                    if order and order.status.value not in _TERMINAL:
                        result.append(order)
            return result

    def get_recent_orders(self, symbol, limit=10):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Spot: limit должен быть положительным целым")
        with self._guard():
            result = []
            for row in self._rows("commands", symbol)[-limit:]:
                order = self._query(row) if self.read_only else self._reconcile_row(row)
                if order:
                    result.append(order)
            return result

    def cancel_all_orders(self, symbol):
        with self._guard():
            self._mutation_allowed()
            return all([self.cancel_order(symbol, o.order_id) for o in self.get_open_orders(symbol)])

    def monitor_once(self):
        """Сверка pending fills и local SL/TP; read_only возвращает только события."""
        events = []
        with self._guard():
            self._sync()
            if self.paused_reason:
                _fail(self.paused_reason, ExchangeStateUnavailableError)
            if not self.read_only and not self.local_protection:
                _fail("Spot: автоматический monitor требует согласия на LOCAL защиту")
            for pending in self._rows("commands"):
                if pending["side"] != "BUY" or pending["status"] in _TERMINAL:
                    continue
                mark = self._live_price(pending["symbol"])
                if (not pending["sl"] or mark <= Decimal(pending["sl"])
                        or (pending["tp"] and mark >= Decimal(pending["tp"]))):
                    events.append({"client_order_id": pending["cid"], "symbol": pending["symbol"],
                                   "price": str(mark), "protection_mode": "local_only",
                                   "read_only": self.read_only, "action": "cancel_entry"})
                    if not self.read_only:
                        self._mutation_allowed()
                        order = self.get_order(pending["symbol"], client_order_id=pending["cid"])
                        if order is None:
                            _fail("Spot: entry неизвестен; отмена требует recovery", UnknownOrderStateError)
                        self.cancel_order(pending["symbol"], order.order_id)
                        # Отмена могла подтвердить late fills: они также должны выйти,
                        # даже если цена успела восстановиться во время запроса.
                        self._db.execute("UPDATE lots SET triggered=1 WHERE id=?", (pending["cid"],))
                        self._db.commit()
            for lot in self._rows("lots"):
                if Decimal(lot["qty"]) <= 0:
                    continue
                mark = self._live_price(lot["symbol"])
                trigger = (lot["triggered"] or not lot["sl"] or mark <= Decimal(lot["sl"])
                           or (lot["tp"] is not None and mark >= Decimal(lot["tp"])))
                if not trigger:
                    continue
                events.append({"position_id": lot["id"], "symbol": lot["symbol"], "price": str(mark),
                               "protection_mode": "local_only", "read_only": self.read_only})
                if self.read_only:
                    continue
                self._mutation_allowed()
                # Latch переживает рестарт: восстановление цены не отменяет выход.
                self._db.execute("UPDATE lots SET triggered=1 WHERE id=?", (lot["id"],))
                self._db.commit()
                for pending in self._active(lot["symbol"]):
                    order = self.get_order(lot["symbol"], client_order_id=pending["cid"])
                    if order is None:
                        _fail("Spot: неопределённый ордер блокирует выход", UnknownOrderStateError)
                    if pending["side"] == "BUY" or pending["kind"] == "LIMIT":
                        self.cancel_order(lot["symbol"], order.order_id)
                if self._active(lot["symbol"]):
                    continue
                current = self._db.execute("SELECT * FROM lots WHERE id=?", (lot["id"],)).fetchone()
                if Decimal(current["qty"]) > 0:
                    # Новый ID только после подтверждённого terminal предыдущего выхода.
                    sequence = len(self._rows("commands", lot["symbol"]))
                    self.close_position(lot["symbol"], lot["id"], command_id=f"local-exit:{lot['id']}:{sequence}")
        return events

    def start_supervisor(self):
        if not self.read_only and not self.local_protection:
            _fail("Spot: запуск требует явного согласия на локальную защиту")
        if self._closed:
            _fail("Spot: закрытый адаптер нельзя перезапускать", ExchangeStateUnavailableError)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._heartbeat = None

        def run():
            while not self._stop.is_set():
                try:
                    self.monitor_once()
                    self._heartbeat = self._clock()
                except Exception as exc:
                    self._heartbeat = None
                    error(f"❌ Spot LOCAL supervisor: {exc}; новые входы заблокированы")
                self._stop.wait(self._monitor_interval)

        self._thread = threading.Thread(target=run, name="spot-local-protection", daemon=True)
        self._thread.start()

    def stop_supervisor(self, timeout=10):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                _fail("Spot: supervisor ещё выполняет запрос; ledger нельзя закрывать", ExchangeStateUnavailableError)
        self._heartbeat = None

    def shutdown(self):
        if self._closed:
            return
        self.stop_supervisor()
        with self._guard(validate_identity=False):
            self._db.close()
            self._closed = True
        if self._lockfile:
            self._lockfile.close()
        if self._readonly_snapshot:
            self._readonly_snapshot.cleanup()

    def close(self):
        """Factory reset закрывает ресурсы, не торгует и не присваивает wallet."""
        self.shutdown()

    def invalidate_cache(self, cache_type=None):
        # Балансы и ордера всегда свежие; только публичный ticker имеет rate limit.
        return None
