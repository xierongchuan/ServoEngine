"""Web token management for browser-based panel access.

Provides secure token generation, storage, and validation for the /weblink feature.
Tokens are cryptographically secure (32 bytes / 256 bits) and valid for 6 hours.
"""

import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("panel.token_store")

# Default token expiry: 6 hours
DEFAULT_TOKEN_EXPIRY_SECONDS = 6 * 60 * 60  # 21600 seconds

# Max tokens per user to prevent abuse
DEFAULT_MAX_TOKENS_PER_USER = 5

# Rate limiting for token validation
MAX_VALIDATION_ATTEMPTS = 10  # Max attempts per window
VALIDATION_WINDOW_SECONDS = 60  # 1 minute window


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).resolve().parent.parent.parent.parent


def get_data_dir() -> Path:
    """Get data directory for token storage."""
    project_root = get_project_root()
    data_dir = Path(os.environ.get("PANEL_DATA_DIR", str(project_root / "data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_tokens_file() -> Path:
    """Get path to tokens storage file."""
    return get_data_dir() / "web_tokens.json"


class RateLimiter:
    """Simple in-memory rate limiter for token validation attempts."""

    def __init__(self, max_attempts: int = MAX_VALIDATION_ATTEMPTS, window_seconds: int = VALIDATION_WINDOW_SECONDS):
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, identifier: str) -> bool:
        """Check if identifier is allowed to make a request."""
        now = time.time()
        with self._lock:
            # Clean old attempts outside the window
            self._attempts[identifier] = [
                t for t in self._attempts[identifier]
                if now - t < self._window_seconds
            ]

            if len(self._attempts[identifier]) >= self._max_attempts:
                return False

            self._attempts[identifier].append(now)
            return True

    def reset(self, identifier: str) -> None:
        """Reset rate limit for identifier."""
        with self._lock:
            self._attempts.pop(identifier, None)


# Global rate limiter instance
_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    return _rate_limiter


class TokenStore:
    """Thread-safe token storage with expiry management."""

    def __init__(self, storage_path: Path | None = None, rate_limiter: RateLimiter | None = None):
        self._lock = threading.RLock()
        self._storage_path = storage_path or get_tokens_file()
        self._tokens: dict[str, dict[str, Any]] = {}
        self._rate_limiter = rate_limiter or get_rate_limiter()
        self._load()

    def _load(self) -> None:
        """Load tokens from file."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._tokens = data.get("tokens", {})
                    # Clean expired tokens on load
                    self._cleanup_expired()
                    logger.info("Loaded %d tokens from storage", len(self._tokens))
            except Exception as e:
                logger.warning("Failed to load tokens: %s", e)
                self._tokens = {}

    def _save(self) -> None:
        """Save tokens to file."""
        try:
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump({"tokens": self._tokens}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Failed to save tokens: %s", e)

    def _cleanup_expired(self) -> int:
        """Remove expired tokens. Returns count of removed tokens."""
        now = datetime.now(timezone.utc)
        expired = [
            token for token, data in self._tokens.items()
            if datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")) < now
        ]
        for token in expired:
            del self._tokens[token]
        return len(expired)

    def generate_token(
        self,
        user_id: int,
        expiry_seconds: int = DEFAULT_TOKEN_EXPIRY_SECONDS,
        max_tokens_per_user: int = DEFAULT_MAX_TOKENS_PER_USER,
    ) -> str:
        """Generate a new web access token for a user.

        Args:
            user_id: Telegram user ID
            expiry_seconds: Token validity duration (default 6 hours)
            max_tokens_per_user: Maximum active tokens per user

        Returns:
            Cryptographically secure token string

        Raises:
            ValueError: If user already has too many tokens
        """
        with self._lock:
            # Clean expired first
            self._cleanup_expired()

            # Count user's existing tokens
            user_tokens = [
                t for t, data in self._tokens.items()
                if data.get("user_id") == user_id and not data.get("used", False)
            ]

            if len(user_tokens) >= max_tokens_per_user:
                raise ValueError(
                    f"Maximum {max_tokens_per_user} active tokens per user. "
                    "Please revoke some tokens first."
                )

            # Generate cryptographically secure token (32 bytes = 256 bits)
            token = secrets.token_urlsafe(32)

            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=expiry_seconds)

            self._tokens[token] = {
                "user_id": user_id,
                "created_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "used": False,
            }

            self._save()
            logger.info("Generated new token for user_id=%d, expires=%s", user_id, expires)

            return token

    def validate_token(self, token: str) -> dict[str, Any] | None:
        """Validate a token and return user data if valid.

        Args:
            token: Token string to validate

        Returns:
            Dict with user_id if valid, None if invalid/expired
        """
        # DEBUG: Temporarily disable rate limiter to test
        # if not self._rate_limiter.is_allowed(token[:8] if len(token) >= 8 else token):
        #     logger.warning("Rate limit exceeded for token validation")
        #     return None

        with self._lock:
            # Reload tokens from file to ensure we have latest data
            # This is important because bot and backend run in different processes
            self._load()

            # Cleanup expired tokens first
            self._cleanup_expired()

            token_data = self._tokens.get(token)
            if not token_data:
                logger.warning("Token not found: %s...", token[:8])
                return None

            # Check if already used (optional: single-use tokens)
            # For now, we allow reuse within validity period
            # if token_data.get("used", False):
            #     logger.warning("Token already used: %s...", token[:8])
            #     return None

            # Validate expiry
            expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))
            if expires_at < datetime.now(timezone.utc):
                logger.warning("Token expired: %s...", token[:8])
                del self._tokens[token]
                self._save()
                return None

            # Return user data
            return {
                "user_id": token_data["user_id"],
                "created_at": token_data["created_at"],
                "expires_at": token_data["expires_at"],
            }

    def revoke_token(self, token: str) -> bool:
        """Revoke a token (mark as used).

        Args:
            token: Token string to revoke

        Returns:
            True if revoked, False if not found
        """
        with self._lock:
            if token in self._tokens:
                self._tokens[token]["used"] = True
                self._save()
                logger.info("Token revoked: %s...", token[:8])
                return True
            return False

    def get_user_tokens(self, user_id: int) -> list[dict[str, Any]]:
        """Get all active tokens for a user.

        Args:
            user_id: Telegram user ID

        Returns:
            List of token info (without the actual token)
        """
        with self._lock:
            self._cleanup_expired()

            result = []
            for token, data in self._tokens.items():
                if data.get("user_id") == user_id:
                    result.append({
                        "token_prefix": token[:8] + "...",
                        "created_at": data["created_at"],
                        "expires_at": data["expires_at"],
                        "used": data.get("used", False),
                    })
            return result

    def revoke_all_user_tokens(self, user_id: int) -> int:
        """Revoke all tokens for a user.

        Args:
            user_id: Telegram user ID

        Returns:
            Number of tokens revoked
        """
        with self._lock:
            count = 0
            for token, data in self._tokens.items():
                if data.get("user_id") == user_id:
                    self._tokens[token]["used"] = True
                    count += 1
            if count > 0:
                self._save()
                logger.info("Revoked %d tokens for user_id=%d", count, user_id)
            return count


# Global singleton instance
_token_store: TokenStore | None = None


def get_token_store() -> TokenStore:
    """Get the global token store instance."""
    global _token_store
    if _token_store is None:
        _token_store = TokenStore()
    return _token_store
