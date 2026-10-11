"""API управления runtime торгового бота через файловый control channel."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.symbol_runtime_control import write_symbol_command
from ..config import DATA_DIR
from ..services.auth import get_current_user


logger = logging.getLogger("panel.runtime")
router = APIRouter(prefix="/api/runtime", tags=["runtime"])

COMMAND_PATH = Path(os.getenv("SERVO_RUNTIME_COMMAND_PATH", str(DATA_DIR / "runtime_command.json")))
STATUS_PATH = Path(os.getenv("SERVO_RUNTIME_STATUS_PATH", str(DATA_DIR / "runtime_status.json")))
STATUS_STALE_AFTER_SECONDS = float(os.getenv("SERVO_RUNTIME_STATUS_STALE_AFTER", "15"))
ALLOWED_ACTIONS = {"start", "stop", "restart"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Не удалось прочитать %s: %s", path, exc)
        return None


def _extract_user_label(user: dict) -> str:
    raw_user = user.get("user") if isinstance(user, dict) else None
    if isinstance(raw_user, dict):
        user_id = raw_user.get("id")
        if user_id:
            return f"telegram:{user_id}"
    return str(user.get("auth_method") or "panel")


def _fallback_status() -> dict[str, Any]:
    return {
        "state": "unavailable",
        "control_enabled": False,
        "supervisor_pid": None,
        "runtime_pid": None,
        "started_at": None,
        "stopped_at": None,
        "last_exit_code": None,
        "last_error": None,
        "last_command_id": None,
        "last_command_action": None,
        "last_command_at": None,
        "updated_at": None,
        "updated_at_ts": None,
        "stale": True,
        "command_path": str(COMMAND_PATH),
        "status_path": str(STATUS_PATH),
    }


def _normalize_status(status: dict[str, Any] | None) -> dict[str, Any]:
    if not status:
        return _fallback_status()

    updated_ts = status.get("updated_at_ts")
    stale = True
    if isinstance(updated_ts, (int, float)):
        stale = time.time() - float(updated_ts) > STATUS_STALE_AFTER_SECONDS

    result = {**_fallback_status(), **status}
    result["stale"] = stale
    result["control_enabled"] = bool(result.get("control_enabled")) and not stale
    result["command_path"] = str(COMMAND_PATH)
    result["status_path"] = str(STATUS_PATH)
    return result


@router.get("/status")
async def get_runtime_status(_user: dict = Depends(get_current_user)) -> dict:
    """Вернуть актуальный статус supervisor/runtime."""
    return _normalize_status(_read_json(STATUS_PATH))


@router.post("/{action}")
async def enqueue_runtime_command(action: str, user: dict = Depends(get_current_user)) -> dict:
    """Положить команду для supervisor торгового бота."""
    action = action.lower()
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown runtime action: {action}")

    status = _normalize_status(_read_json(STATUS_PATH))
    if not status.get("control_enabled"):
        raise HTTPException(status_code=409, detail="Runtime supervisor недоступен")

    command = {
        "id": uuid.uuid4().hex,
        "action": action,
        "requested_by": _extract_user_label(user),
        "requested_at": _utc_now(),
        "requested_at_ts": time.time(),
    }

    try:
        _atomic_write_json(COMMAND_PATH, command)
    except OSError as exc:
        logger.error("Не удалось записать runtime command: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to write runtime command: {exc}")

    logger.info("Runtime command queued: %s by %s", action, command["requested_by"])
    return {
        "status": "queued",
        "command": command,
        "runtime_status": status,
    }


@router.post("/symbol/{action}")
async def enqueue_symbol_runtime_command(
    action: str,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    """Положить команду для перезапуска/остановки worker конкретного символа или instance."""
    action = action.lower()
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown symbol runtime action: {action}")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Ожидается JSON-объект команды worker")
    symbol = data.get("symbol")
    instance_id = data.get("instance_id")
    instance_ids = data.get("instance_ids")
    if instance_ids is not None and not isinstance(instance_ids, list):
        raise HTTPException(status_code=400, detail="instance_ids must be a list")
    if not symbol and not instance_id and not instance_ids:
        raise HTTPException(status_code=400, detail="symbol, instance_id or instance_ids is required")

    try:
        command = write_symbol_command(
            action,
            requested_by=_extract_user_label(user),
            symbol=symbol,
            instance_id=instance_id,
            instance_ids=instance_ids,
            reason=data.get("reason") or "panel",
        )
    except ValueError as exc:
        logger.warning("⚠️ Команда worker отклонена: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except OSError:
        logger.error("❌ Не удалось записать команду worker")
        raise HTTPException(status_code=503, detail="Не удалось записать команду worker; повторите позже") from None

    logger.info("Symbol runtime command queued: %s by %s", action, command["requested_by"])
    return {"status": "queued", "command": command}
