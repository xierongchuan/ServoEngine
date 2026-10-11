"""AI control API: конфигурация без секретов и только явный платный probe."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from src.ai_settings import (AISettingsError, PROVIDERS, normalize_ai_settings,
                             merge_ai_settings, migrate_ai_settings, provider_statuses, resolve_ai_role)
from src.ai.client import AIProviderError, list_models, probe_provider
from src.ai.settings_store import config_write_lock
from ..services.auth import get_current_user
from . import config_routes

router = APIRouter(prefix="/api/config/ai", tags=["ai"])
# Semaphore остаётся занятым до завершения thread, даже если HTTP-клиент ушёл.
_network_slots = asyncio.Semaphore(4)


def _document_target():
    modular = config_routes._use_new_config_system()
    return ((config_routes.CONFIG_DIR / "base.json", "ai", True) if modular
            else (config_routes.reader.config_path, "AI_SETTINGS", False))


def _document_settings(document, section):
    if section not in document:
        raise HTTPException(422, "Сохранённая конфигурация AI отсутствует")
    try:
        return normalize_ai_settings(document[section])
    except AISettingsError:
        raise HTTPException(422, "Сохранённая конфигурация AI некорректна") from None


def persisted_settings():
    path, section, _ = _document_target()
    return _document_settings(config_routes._read_config_document(path), section)


def _persist_settings(value):
    path, section, modular = _document_target()
    try:
        with config_write_lock(path):
            document = config_routes._read_config_document(path)
            current = _document_settings(document, section)
            patch = migrate_ai_settings(value, current["provider"])
            settings = normalize_ai_settings(merge_ai_settings(current, patch))
            document[section] = settings
            if modular:
                config_routes._save_json(path, document)
            else:
                config_routes.reader.write_config(config_routes._guard_ai_request(document))
            return settings
    except AISettingsError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(500, "Не удалось сохранить конфигурацию AI") from None


def selected_provider(provider):
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise HTTPException(422, "Неизвестный провайдер AI")
    settings = persisted_settings()
    settings["provider"] = provider
    return resolve_ai_role(settings)


async def request_object(request):
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(413, "Слишком большой запрос AI")
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Некорректный JSON") from None
    if not isinstance(value, dict):
        raise HTTPException(422, "Ожидается JSON объект")
    return value


async def bounded_call(function, *args):
    if _network_slots.locked():
        raise HTTPException(429, "Слишком много запросов AI; повторите позже")

    await _network_slots.acquire()

    async def work():
        try:
            return await asyncio.to_thread(function, *args)
        finally:
            _network_slots.release()

    task = asyncio.create_task(work())
    task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=15)
    except asyncio.TimeoutError:
        # Thread имеет собственный timeout и лимит ответа; не освобождаем slot раньше.
        raise HTTPException(504, "Таймаут AI API") from None


@router.get("")
async def get_ai(_user: dict = Depends(get_current_user)):
    settings = persisted_settings()
    return {"settings": settings, "providers": provider_statuses(settings)}


@router.put("")
async def put_ai(request: Request, _user: dict = Depends(get_current_user)):
    value = await request_object(request)
    # Принимаем сам AISettings; envelope settings поддержан для совместимости клиентов.
    if "settings" in value:
        if set(value) != {"settings"}:
            raise HTTPException(422, "Недопустимые поля запроса AI")
        value = value["settings"]
    settings = await asyncio.to_thread(_persist_settings, value)
    return {"settings": settings, "needs_restart": True}


@router.post("/probe")
async def probe_ai(request: Request, _user: dict = Depends(get_current_user)):
    value = await request_object(request)
    if set(value) - {"provider", "allow_paid_completion"} or type(value.get("allow_paid_completion", False)) is not bool:
        raise HTTPException(422, "Недопустимые поля probe")
    settings = selected_provider(value.get("provider"))
    try:
        return await bounded_call(probe_provider, settings, value.get("allow_paid_completion", False))
    except HTTPException as exc:
        if exc.status_code != 504:
            raise
        return {"provider": settings["provider"], "ok": False, "latency_ms": 15000,
                "message": "Таймаут AI API", "model": settings["model"], "error_code": "timeout"}


@router.get("/models")
async def models_ai(provider: str, _user: dict = Depends(get_current_user)):
    settings = selected_provider(provider)
    try:
        models = await bounded_call(list_models, settings)
    except AIProviderError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"models": models, "provider": provider}
