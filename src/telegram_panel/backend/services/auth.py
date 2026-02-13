import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs, unquote

from fastapi import Header, HTTPException

from ..config import BOT_TOKEN, ALLOWED_IDS

logger = logging.getLogger("panel.auth")


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validate Telegram Mini App initData HMAC-SHA256 signature.

    Returns parsed user data dict if valid, None if invalid.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not bot_token:
        return None

    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        # Each value in parse_qs is a list; take the first element
        flat: dict[str, str] = {k: v[0] for k, v in parsed.items()}

        received_hash = flat.pop("hash", None)
        if not received_hash:
            return None

        # Build data_check_string: sorted key=value pairs joined by \n
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(flat.items())
        )

        # secret_key = HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
        ).digest()

        # computed_hash = HMAC_SHA256(secret_key, data_check_string)
        computed_hash = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        # Parse user JSON if present
        user_str = flat.get("user")
        if user_str:
            flat["user"] = json.loads(unquote(user_str))

        return flat

    except Exception as e:
        logger.warning("initData validation error: %s", e)
        return None


def _extract_user_id(data: dict) -> int:
    """Extract Telegram user ID from validated initData."""
    user = data.get("user")
    if isinstance(user, dict):
        return user.get("id", 0)
    return 0


async def get_current_user(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
) -> dict:
    """FastAPI dependency: validates Telegram initData and checks user against allowed list.

    Auth flow:
    1. No initData → 401 (must open via Telegram Mini App)
    2. Invalid HMAC → 401
    3. User ID not in ALLOWED_IDS → 403
    4. All checks pass → return user data
    """
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Откройте панель через Telegram Mini App")

    if not BOT_TOKEN:
        return {"user": {"id": 0, "first_name": "dev"}}

    result = validate_init_data(x_telegram_init_data, BOT_TOKEN)
    if result is None:
        raise HTTPException(status_code=401, detail="Невалидные данные авторизации Telegram")

    user_id = _extract_user_id(result)
    if ALLOWED_IDS and user_id not in ALLOWED_IDS:
        logger.warning("Доступ запрещён для user_id=%d", user_id)
        raise HTTPException(status_code=403, detail="У вас нет доступа к этой панели")

    return result
