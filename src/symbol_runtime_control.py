"""Файловый control channel для управления worker-процессами отдельных символов."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.runtime import normalize_symbol_key, validate_runtime_config_source
from src.trading_settings import get_trading_settings, trading_settings_lock, validate_trading_settings
from src.utils.logger import error, warning


DATA_DIR = Path(os.getenv("SERVO_DATA_DIR", "data"))
COMMAND_PATH = Path(os.getenv("SERVO_SYMBOL_RUNTIME_COMMAND_PATH", str(DATA_DIR / "symbol_runtime_command.json")))
STATUS_PATH = Path(os.getenv("SERVO_SYMBOL_RUNTIME_STATUS_PATH", str(DATA_DIR / "symbol_runtime_status.json")))
ALLOWED_ACTIONS = {"start", "stop", "restart"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if data.get("action") in {"start", "restart"}:
            validate_symbol_command_context(data)
        return data
    except FileNotFoundError:
        return None
    except Exception as exc:
        warning(f"⚠️ Control файл {path.name} отклонён: {exc}")
        return None


def validate_symbol_command_context(command: dict[str, Any]) -> None:
    """Команда worker не меняет execution и не переносится в другой runtime."""
    validate_runtime_config_source(command)
    if "runtime_pid" in command and command["runtime_pid"] != os.getpid():
        raise ValueError("Команда относится к другому runtime")
    if "execution" in command and validate_trading_settings(command["execution"]) != get_trading_settings():
        raise ValueError("Команда не совпадает со снимком runtime")


def build_symbol_command(
    action: str,
    *,
    requested_by: str = "panel",
    symbol: str | None = None,
    instance_id: str | None = None,
    instance_ids: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    action = str(action or "").lower()
    if action not in ALLOWED_ACTIONS:
        error(f"❌ Неизвестная команда worker: {action}")
        raise ValueError(f"Неизвестная команда worker: {action}")
    normalized_instance_ids = [str(item).lower() for item in (instance_ids or []) if str(item).strip()]
    if not symbol and not instance_id and not normalized_instance_ids:
        error("❌ Для команды worker не задан symbol или instance_id")
        raise ValueError("Требуется symbol, instance_id или instance_ids")

    command: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "action": action,
        "requested_by": requested_by,
        "requested_at": utc_now(),
        "requested_at_ts": time.time(),
    }
    if symbol:
        command["symbol"] = normalize_symbol_key(symbol)
    if instance_id:
        command["instance_id"] = str(instance_id).lower()
    if normalized_instance_ids:
        command["instance_ids"] = normalized_instance_ids
    if reason:
        command["reason"] = str(reason)
    return command


def write_symbol_command(
    action: str,
    *,
    requested_by: str = "panel",
    symbol: str | None = None,
    instance_id: str | None = None,
    instance_ids: list[str] | None = None,
    reason: str | None = None,
    command_path: Path = COMMAND_PATH,
    settings_path: Path | None = None,
) -> dict[str, Any]:
    command = build_symbol_command(
        action,
        requested_by=requested_by,
        symbol=symbol,
        instance_id=instance_id,
        instance_ids=instance_ids,
        reason=reason,
    )
    with trading_settings_lock(settings_path):
        if command["action"] in {"start", "restart"}:
            parent_status_path = Path(os.getenv("SERVO_RUNTIME_STATUS_PATH", str(command_path.parent / "runtime_status.json")))
            status = read_json(parent_status_path) or {}
            updated = status.get("updated_at_ts")
            max_age = float(os.getenv("SERVO_RUNTIME_STATUS_STALE_AFTER", "15"))
            if (status.get("state") != "running" or not status.get("control_enabled") or
                    type(updated) not in (int, float) or not 0 <= time.time() - updated <= max_age or
                    type(status.get("runtime_pid")) is not int or status["runtime_pid"] <= 0 or
                    not isinstance(status.get("execution"), dict)):
                error("❌ Запуск worker запрещён: нет свежего снимка работающего runtime")
                raise ValueError("Запуск worker требует свежего статуса running с execution; запустите весь runtime")
            command["execution"] = validate_trading_settings(status["execution"])
            command["runtime_pid"] = status["runtime_pid"]
        atomic_write_json(command_path, command)
    return command
