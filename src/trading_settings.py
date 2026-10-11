"""Единый проверенный контракт исполнения; секреты сюда не входят."""

from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import fcntl
import json
import math
import re
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


ACCOUNT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

DEFAULT_TRADING_SETTINGS = {
    "account_id": "default",
    "exchange": "bingx",
    "market_type": "perpetual",
    "execution_mode": "read_only",
    "margin_mode": "isolated",
    "position_mode": "hedge",
    "settle_asset": "USDT",
    "grid_mode": "hedged",
    "spot": {
        "quote_budget_usdt": 0,
        "allow_local_protection": False,
    },
    "protection": {
        "trigger_price": "mark",
        "failure_policy": "pause",
        "verification_timeout_seconds": 10,
        "recovery_attempts": 3,
    },
    "market_data": {
        "max_age_seconds": 30,
        "reconcile_interval_seconds": 60,
        "history_limit": 500,
    },
}
RECONFIGURE_KEYS = frozenset({
    "account_id", "exchange", "market_type", "execution_mode", "margin_mode",
    "position_mode", "settle_asset", "grid_mode", "spot",
})
_runtime_settings: dict[str, Any] | None = None
_settings_lock = RLock()


def validate_trading_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Дополняет defaults, отвергает неизвестные поля и несовместимые режимы."""
    if not isinstance(settings, dict):
        raise ValueError("execution должен быть JSON-объектом")
    unknown = settings.keys() - DEFAULT_TRADING_SETTINGS.keys()
    if unknown:
        raise ValueError(f"Неизвестные поля execution: {', '.join(sorted(unknown))}")
    result = deepcopy(DEFAULT_TRADING_SETTINGS)
    for key, value in settings.items():
        if isinstance(result[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"execution.{key} должен быть объектом")
            unknown_nested = value.keys() - result[key].keys()
            if unknown_nested:
                raise ValueError(f"Неизвестные поля execution.{key}: {', '.join(sorted(unknown_nested))}")
            result[key].update(value)
        else:
            result[key] = value

    account_id = result["account_id"]
    if not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise ValueError("execution.account_id: 1–64 символа a-z, 0-9, _ или -; первый символ a-z или 0-9")
    budget = result["spot"]["quote_budget_usdt"]
    if isinstance(budget, bool) or not isinstance(budget, (int, float, Decimal)):
        raise ValueError("execution.spot.quote_budget_usdt: неотрицательное конечное число")
    if isinstance(budget, Decimal):
        if not budget.is_finite() or budget < 0:
            raise ValueError("execution.spot.quote_budget_usdt: неотрицательное конечное число")
        # JSON не поддерживает Decimal; целые сохраняем точно, дробные — конечным number.
        converted = int(budget) if budget == budget.to_integral_value() else float(budget)
        if isinstance(converted, float) and (not math.isfinite(converted) or (budget > 0 and converted == 0)):
            raise ValueError("execution.spot.quote_budget_usdt: число вне диапазона JSON number")
        budget = converted
    if budget < 0 or (isinstance(budget, float) and not math.isfinite(budget)):
        raise ValueError("execution.spot.quote_budget_usdt: неотрицательное конечное число")
    result["spot"]["quote_budget_usdt"] = budget
    if type(result["spot"]["allow_local_protection"]) is not bool:
        raise ValueError("execution.spot.allow_local_protection: требуется true или false")

    choices = {
        "exchange": {"bingx", "mexc"}, "market_type": {"perpetual", "spot"},
        "execution_mode": {"read_only", "demo", "live"}, "margin_mode": {"isolated"},
        "position_mode": {"hedge", "one_way"}, "settle_asset": {"USDT"},
        "grid_mode": {"inventory", "hedged"},
    }
    for key, allowed in choices.items():
        if not isinstance(result[key], str) or result[key] not in allowed:
            raise ValueError(f"execution.{key}: допустимо {', '.join(sorted(allowed))}")
    if result["exchange"] == "bingx" and result["market_type"] != "perpetual":
        raise ValueError("BingX поддерживает только perpetual")
    if result["exchange"] == "mexc" and result["execution_mode"] == "demo":
        # У MEXC нет sandbox: demo не даёт разрешения на торговлю.
        result["execution_mode"] = "read_only"
    if result["market_type"] == "spot":
        for key, default in (("position_mode", "one_way"), ("grid_mode", "inventory")):
            if key not in settings:
                result[key] = default
            if result[key] != default:
                raise ValueError(f"Для Spot execution.{key} должен быть {default}")
        if "trigger_price" not in settings.get("protection", {}):
            result["protection"]["trigger_price"] = "last"
        if result["protection"]["trigger_price"] != "last":
            raise ValueError("Для Spot trigger_price должен быть last")
    if result["grid_mode"] == "hedged" and result["position_mode"] != "hedge":
        raise ValueError("Сетка hedged требует position_mode=hedge")
    protection = result["protection"]
    for key, allowed in (("trigger_price", {"last", "mark"}), ("failure_policy", {"close", "pause"})):
        if not isinstance(protection[key], str) or protection[key] not in allowed:
            raise ValueError(f"execution.protection.{key}: допустимо {', '.join(sorted(allowed))}")
    ranges = {
        "protection": {"verification_timeout_seconds": (1, 300), "recovery_attempts": (0, 10)},
        "market_data": {"max_age_seconds": (1, 300), "reconcile_interval_seconds": (1, 3600), "history_limit": (50, 2000)},
    }
    for section, fields in ranges.items():
        for key, (minimum, maximum) in fields.items():
            value = result[section][key]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"execution.{section}.{key}: целое число от {minimum} до {maximum}")
    return result


def execution_account_label(settings: dict[str, Any] | None = None) -> str:
    """Стабильный namespace аккаунта, независимый от ключей и их ротации."""
    selected = validate_trading_settings(settings) if settings is not None else get_trading_settings()
    return f"{selected['account_id']}:{selected['exchange']}:{selected['execution_mode']}"


def validate_trading_start(settings: dict[str, Any]) -> dict[str, Any]:
    """Проверяет разрешение старта отдельно от сохранения отключённого Spot-конфига."""
    selected = validate_trading_settings(settings)
    if selected["market_type"] == "spot" and selected["execution_mode"] == "live":
        if selected["spot"]["quote_budget_usdt"] <= 0:
            raise ValueError("LIVE Spot: задайте общий бюджет аккаунта quote_budget_usdt > 0 в панели")
        if not selected["spot"]["allow_local_protection"]:
            raise ValueError("LIVE Spot: явно примите риск локальной SL/TP; биржевой защиты нет")
    return selected


def read_trading_settings(path: Path | str | None = None) -> dict[str, Any]:
    """Читает сохранённый конфиг без кэша; повреждённый файл не маскируется."""
    if path is None:
        from src.config_loader import _get_config_path
        path = _get_config_path("trading")
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ValueError("trading.json должен быть объектом")
        return validate_trading_settings(config.get("execution", {}))
    except FileNotFoundError:
        return validate_trading_settings({})
    except (OSError, ValueError) as exc:
        from src.utils.logger import error
        error(f"❌ Не удалось загрузить настройки исполнения: {exc}")
        raise


def trading_config_path() -> Path:
    """Путь общего источника; operational env не участвует в выборе исполнения."""
    from src.config_loader import _get_config_path
    return Path(_get_config_path("trading"))


@contextmanager
def trading_settings_lock(path: Path | str | None = None):
    """Один межпроцессный lock для PUT execution и lifecycle supervisor."""
    config_path = Path(path) if path is not None else trading_config_path()
    with open(config_path.parent / ".trading.lock", "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def install_trading_settings_snapshot(settings: dict[str, Any]) -> dict[str, Any]:
    """Устанавливает уже выбранный снимок до запуска main/worker, без перечитывания файла."""
    global _runtime_settings
    validated = validate_trading_settings(settings)
    with _settings_lock:
        _runtime_settings = validated
        return deepcopy(validated)


def reset_trading_settings() -> None:
    """Явный сброс только на границе остановленного runtime, не для hot-reload."""
    global _runtime_settings
    with _settings_lock:
        _runtime_settings = None


def refresh_trading_settings(path: Path | str | None = None) -> dict[str, Any]:
    """Lifecycle refresh: сначала валидация файла, затем замена снимка. При ошибке старый сохраняется."""
    return install_trading_settings_snapshot(read_trading_settings(path))


_JOURNAL_SCHEMA = {
    "intents": {"account", "command_id", "client_id", "owner", "symbol", "operation", "state", "request", "context", "order_id", "result", "updated"},
    "wires": {"id", "account", "command_id", "state", "event", "updated"},
    "ownership": {"account", "owner", "symbol", "position_id", "side", "quantity", "snapshot"},
    "protection": {"account", "owner", "symbol", "order_id", "position_id", "snapshot"},
    "blocks": {"account", "symbol", "reason", "updated"},
    "events": {"sequence", "account", "command_id", "state", "detail", "created"},
}
_SPOT_SCHEMA = {
    "owners": {"ns", "account", "instance", "asset", "cash"},
    "pauses": {"account", "reason"},
    "commands": {"cid", "ns", "symbol", "side", "kind", "qty", "budget", "price", "oid", "status", "filled", "quote_filled", "fees", "sl", "tp", "allocations", "fingerprint"},
    "lots": {"id", "ns", "symbol", "qty", "cost", "sl", "tp", "triggered"},
    "fills": {"cid", "trade_id", "oid", "payload"},
    "wallets": {"account", "asset", "total"},
    "movements": {"account", "asset", "amount"},
}
# Эти БД не хранят владение/заявки. Распознаём схему, не имя файла или каталог:
# неизвестная/смешанная схема по-прежнему блокирует смену исполнения.
_INFRA_SCHEMAS = (
    (0, {
        "log_meta": {"key", "value"},
        "log_catalog": {"kind", "value"},
        "log_events": {"sequence", "event_id", "timestamp", "level", "category", "exchange", "symbol",
                       "instance_id", "order_id", "request_id", "source", "service", "message", "payload"},
    }),
    (1, {
        "clock_state": {"id", "identity", "boot", "tick", "logical"},
        "budgets": {"key", "capacity", "period"},
        "events": {"seq", "key", "at", "weight"},
        "cooldowns": {"key", "until"},
    }),
)
_ORDER_TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


def _state_quantity(value: Any, *, signed: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Некорректное количество в сохранённом состоянии")
    quantity = Decimal(str(value))
    if not quantity.is_finite() or (not signed and quantity < 0):
        raise ValueError("Неконечное или отрицательное количество в сохранённом состоянии")
    return quantity


def _sqlite_has_managed_state(path: Path) -> bool:
    # mode=ro видит WAL; immutable запрещён, поскольку скрывает свежие изменения.
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("Повреждён SQLite-журнал")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if _JOURNAL_SCHEMA.keys() <= tables:
            schema, version = _JOURNAL_SCHEMA, 1
        elif _SPOT_SCHEMA.keys() <= tables:
            schema, version = _SPOT_SCHEMA, 0
        else:
            match = next(((expected_version, known) for expected_version, known in _INFRA_SCHEMAS
                          if tables - {"sqlite_sequence"} == known.keys()), None)
            if match is None:
                raise ValueError("Неизвестная схема SQLite-журнала")
            version, schema = match
        if db.execute("PRAGMA user_version").fetchone()[0] != version:
            raise ValueError("Неизвестная версия SQLite-журнала")
        infrastructure = any(schema is known for _, known in _INFRA_SCHEMAS)
        for table, required in schema.items():
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not required <= columns or (infrastructure and columns != required):
                raise ValueError(f"Неполная или неизвестная схема SQLite-журнала: {table}")
        if infrastructure:
            return False
        # Проверяем все аккаунты: новый account_id не вправе скрывать старое владение.
        if schema is _JOURNAL_SCHEMA:
            for table in ("intents", "wires"):
                if any(row[0] not in {"FILLED", "REJECTED", "RESOLVED"}
                       for row in db.execute(f"SELECT state FROM {table}")):
                    return True
            if any(_state_quantity(row[0]) > 0 for row in db.execute("SELECT quantity FROM ownership")):
                return True
            return any(db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                       for table in ("protection", "blocks"))
        if any(row[0] not in _ORDER_TERMINAL for row in db.execute("SELECT status FROM commands")):
            return True
        if db.execute("SELECT 1 FROM pauses LIMIT 1").fetchone():
            return True
        if any(_state_quantity(row[0]) > 0 for row in db.execute("SELECT qty FROM lots")):
            return True
        # Остаток выделенного owner cash нельзя незаметно импортировать в новый namespace.
        return any(_state_quantity(row[0]) > 0 for row in db.execute("SELECT cash FROM owners"))
    finally:
        db.close()


def _grid_has_managed_state(state: Any) -> bool:
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("Неизвестная версия GRID-реестра")
    for key in ("identity", "signature", "state", "commands", "owned_positions"):
        if not isinstance(state.get(key), dict):
            raise ValueError(f"Некорректное поле GRID-реестра: {key}")
    if state.get("paused_reason") or state.get("owned_positions"):
        return True
    order_ids = set()
    for cid, command in state["commands"].items():
        if not isinstance(command, dict) or command.get("client_order_id") != cid:
            raise ValueError("Неизвестная команда GRID")
        oid = command.get("order_id")
        if oid is not None:
            if not isinstance(oid, str) or not oid or oid in order_ids:
                raise ValueError("Некорректный order ID GRID")
            order_ids.add(oid)
        if command.get("status") not in _ORDER_TERMINAL:
            return True
    inventory = state["state"]
    for key in ("inventory", "long_inventory", "short_inventory", "gross_inventory", "owned_long", "owned_short"):
        if key not in inventory:
            raise ValueError(f"Неполное состояние GRID: {key}")
        if _state_quantity(inventory[key], signed=key == "inventory") != 0:
            return True
    return False


def managed_state_block_reason(data_dir: Path | str) -> str | None:
    """Читает durable-состояние без миграций; неизвестное состояние запрещает смену."""
    directory = Path(data_dir)
    paths = {directory / "active_trades.json", directory / "position_owners.json"}
    for pattern in ("managed_state*.json", "grid_state*.json", "pending_orders*.json",
                    "unknown_orders*.json", "order_journal*.json", "execution_journal*.json"):
        paths.update(directory.glob(pattern))
    grid_paths = set((directory / "grid").glob("*.json"))
    paths.update(grid_paths)
    for path in sorted(paths):
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                state = json.load(handle)
            if not isinstance(state, (dict, list)):
                raise ValueError("Ожидался объект или список")
            occupied = _grid_has_managed_state(state) if path in grid_paths else bool(state)
        except (OSError, ValueError, InvalidOperation) as exc:
            from src.utils.logger import warning
            warning(f"⚠️ Не удалось проверить управляемое состояние {path.name}: {exc}")
            return f"Не удалось проверить состояние {path.name}; смена исполнения запрещена"
        if occupied:
            return f"Есть открытое управляемое состояние ({path.name}); смена исполнения запрещена"
    # Ledger может иметь произвольное имя; файлы WAL/SHM не являются отдельными БД.
    databases = set()
    for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
        databases.update(directory.rglob(pattern))
    for path in sorted(databases):
        try:
            if _sqlite_has_managed_state(path):
                return f"Есть незавершённое управляемое состояние ({path.name}); смена исполнения запрещена"
        except (OSError, sqlite3.Error, ValueError, InvalidOperation) as exc:
            from src.utils.logger import warning
            warning(f"⚠️ Не удалось проверить SQLite-состояние {path.name}: {exc}")
            return f"Не удалось проверить SQLite-журнал {path.name}; смена исполнения запрещена"
    return None


def has_managed_state(data_dir: Path | str) -> bool:
    """Неизвестное/нечитаемое состояние тоже считается занятым (fail closed)."""
    return managed_state_block_reason(data_dir) is not None


def get_trading_settings() -> dict[str, Any]:
    """Снимок на процесс: hot-reload не переключает биржу, счёт или режим."""
    global _runtime_settings
    with _settings_lock:
        if _runtime_settings is None:
            _runtime_settings = read_trading_settings()
        return deepcopy(_runtime_settings)
