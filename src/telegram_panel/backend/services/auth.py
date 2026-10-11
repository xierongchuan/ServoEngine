"""Единая закрытая по умолчанию авторизация HTTP и WebSocket."""

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs

from fastapi import Header, HTTPException, Request

from ..config import BOT_TOKEN, ALLOWED_IDS
from .token_store import get_token_store, get_rate_limiter


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    if not init_data or not bot_token or len(init_data) > 16384:
        return None
    try:
        parsed = parse_qs(init_data, keep_blank_values=True, max_num_fields=32)
        if any(len(values) != 1 for values in parsed.values()):
            return None
        flat = {key: values[0] for key, values in parsed.items()}
        received_hash = flat.pop("hash", "")
        check = "\n".join(f"{key}={value}" for key, value in sorted(flat.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None
        auth_date = int(flat["auth_date"])
        ttl = int(os.getenv("PANEL_INIT_DATA_TTL_SECONDS", "86400"))
        now = time.time()
        if ttl <= 0 or auth_date <= 0 or auth_date > now + 30 or now - auth_date >= ttl:
            return None
        # parse_qs уже декодировал значение: повторный unquote искажает JSON пользователя.
        user = json.loads(flat["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
            return None
        return {**flat, "user": user, "auth_method": "telegram", "expires_at": auth_date + ttl}
    except (ValueError, TypeError, KeyError):
        return None


def _extract_user_id(data: dict) -> int:
    return data.get("user", {}).get("id", 0)


def validate_web_token(token: str) -> dict | None:
    if not token or len(token) > 256:
        return None
    result = get_token_store().validate_token(token)
    if not result or result.get("user_id") not in ALLOWED_IDS:
        return None
    return {"user": {"id": result["user_id"], "is_web_token": True},
            "auth_method": "web_token", "token_created_at": result["created_at"],
            "token_expires_at": result["expires_at"]}


def authenticate(init_data: str = "", web_token: str = "") -> dict:
    if web_token:
        result = validate_web_token(web_token)
        if result:
            return result
        raise HTTPException(401, "Ссылка истекла, отозвана или недействительна. Получите новую через /weblink.")
    if init_data:
        result = validate_init_data(init_data, BOT_TOKEN)
        if result is None:
            raise HTTPException(401, "Сессия Telegram истекла или недействительна. Закройте панель и откройте её заново через бота.")
        if _extract_user_id(result) not in ALLOWED_IDS:
            raise HTTPException(403, "У вас нет доступа к этой панели")
        return result
    raise HTTPException(401, "Откройте панель заново через Telegram Mini App или получите ссылку /weblink.")


def local_dev_enabled(request: Request) -> bool:
    # Только прямой loopback, без доверия X-Forwarded-For и без production bot token.
    return (os.getenv("PANEL_ALLOW_LOCAL_DEV_AUTH") == "1" and not BOT_TOKEN
            and request.client is not None and request.client.host in {"127.0.0.1", "::1"})


async def get_current_user(request: Request,
                           x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
                           x_web_token: str = Header(default="", alias="X-Web-Token")) -> dict:
    if local_dev_enabled(request):
        return {"user": {"id": 0}, "auth_method": "local_dev"}
    try:
        return authenticate(x_telegram_init_data, x_web_token)
    except HTTPException:
        identifier = request.client.host if request.client else "unknown"
        if not get_rate_limiter().is_allowed(identifier):
            raise HTTPException(429, "Слишком много неудачных попыток авторизации. Повторите через минуту.")
        raise


# Совместимость существующих зависимостей без второго экземпляра сервиса.
get_current_user_with_token = get_current_user


def validate_init_data_string(init_data: str) -> None:
    authenticate(init_data=init_data)


def validate_auth_string(auth: str) -> None:
    if "hash=" in auth:
        authenticate(init_data=auth)
    else:
        authenticate(web_token=auth)
