import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs, unquote

from fastapi import Header, HTTPException, Request

from ..config import BOT_TOKEN, ALLOWED_IDS
from .token_store import get_token_store

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


def validate_init_data_string(init_data: str) -> None:
    """Validate Telegram initData passed as a query parameter (for <img src> etc.).

    Raises HTTPException on failure.  When BOT_TOKEN is empty (dev mode),
    any value is accepted.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing auth parameter")

    if not BOT_TOKEN:
        # Dev mode: no token configured, skip validation
        return

    result = validate_init_data(init_data, BOT_TOKEN)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid auth parameter")

    user_id = _extract_user_id(result)
    if ALLOWED_IDS and user_id not in ALLOWED_IDS:
        raise HTTPException(status_code=403, detail="Access denied")


async def get_current_user(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
) -> dict:
    """FastAPI dependency: validates Telegram initData and checks user against allowed list.

    This is kept for backward compatibility. For web token support, use get_current_user_with_token.
    """
    # Delegate to the combined function
    return await get_current_user_with_token(
        x_telegram_init_data=x_telegram_init_data,
        x_web_token="",
    )


# ---------------------------------------------------------------------------
# Web Token Authentication (для /weblink)
# ----------------------------------------------------------------------------


def validate_web_token(token: str) -> dict | None:
    """Validate a web access token from /weblink command.

    Returns user data dict if valid, None if invalid/expired.
    """
    if not token:
        return None

    try:
        token_store = get_token_store()
        result = token_store.validate_token(token)

        if result is None:
            return None

        user_id = result.get("user_id")
        if user_id is None:
            return None

        # Check user is in allowed list
        if ALLOWED_IDS and user_id not in ALLOWED_IDS:
            logger.warning("Web token access denied for user_id=%d", user_id)
            return None

        return {
            "user": {
                "id": user_id,
                "is_web_token": True,
            },
            "auth_method": "web_token",
            "token_created_at": result.get("created_at"),
            "token_expires_at": result.get("expires_at"),
        }

    except Exception as e:
        logger.error("Web token validation error: %s", e)
        return None


async def get_current_user_with_token(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
    x_web_token: str = Header(default="", alias="X-Web-Token"),
) -> dict:
    """FastAPI dependency: supports both Telegram initData and web token auth.

    Auth flow:
    1. If X-Web-Token provided → validate web token
    2. Else if X-Telegram-Init-Data → validate Telegram auth
    3. Neither → 401
    4. Invalid credentials → 401
    5. User not in ALLOWED_IDS → 403
    6. All checks pass → return user data
    """
    # Try web token first
    if x_web_token:
        result = validate_web_token(x_web_token)
        if result is not None:
            return result
        # Invalid web token - try Telegram auth as fallback
        if not x_telegram_init_data:
            raise HTTPException(
                status_code=401,
                detail="Невалидная ссылка. Используйте /weblink для новой ссылки."
            )

    # Fall back to Telegram initData
    if x_telegram_init_data:
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

    # No auth provided
    raise HTTPException(
        status_code=401,
        detail="Требуется авторизация: откройте панель через Telegram Mini App "
                "или используйте ссылку от /weblink"
    )


def validate_auth_string(auth: str) -> None:
    """Validate auth from query parameter (for <img src> etc.).

    Supports both Telegram initData and web token.
    """
    if not auth:
        raise HTTPException(status_code=401, detail="Missing auth parameter")

    # Check if it's a web token (contains no hash= which is required for Telegram initData)
    if "hash=" not in auth:
        # Try as web token
        result = validate_web_token(auth)
        if result is None:
            raise HTTPException(status_code=401, detail="Invalid auth parameter")
        return

    # Otherwise validate as Telegram initData
    validate_init_data_string(auth)
