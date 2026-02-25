#!/usr/bin/env python3
"""Entry point for the Telegram Panel — runs FastAPI backend + Telegram bot.

Can be run from project root:    python -m src.telegram_panel.run_panel
Or from telegram_panel/ dir:     python run_panel.py
Or inside Docker container:      python run_panel.py
"""

import logging
import os
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root & fix sys.path for both local and Docker execution
# ---------------------------------------------------------------------------
_this_dir = Path(__file__).resolve().parent

# Detect if we're in Docker (/app/) or local (src/telegram_panel/)
if _this_dir.name == "telegram_panel":
    # Local: .../src/telegram_panel/  → project root is 2 levels up
    _project_root = _this_dir.parent.parent
else:
    # Docker: /app/ — project root is cwd or parent
    _project_root = _this_dir

# Ensure project root is in sys.path so `src.*` imports work locally
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Also add telegram_panel dir so relative imports work in Docker
if str(_this_dir) not in sys.path:
    sys.path.insert(0, str(_this_dir))

# ---------------------------------------------------------------------------
# Load .env from project root
# ---------------------------------------------------------------------------
_env_path = _project_root / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                _key = _key.strip()
                if _key not in os.environ:
                    os.environ[_key] = _val.strip().strip('"').strip("'")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("panel")


def _import_bot_class():
    """Import TelegramPanelBot with fallback for different project layouts."""
    try:
        from src.telegram_panel.bot import TelegramPanelBot
    except ImportError:
        from bot import TelegramPanelBot
    return TelegramPanelBot


def _import_config():
    """Import panel config with fallback for different project layouts."""
    try:
        from src.telegram_panel.backend.config import PANEL_PORT, BOT_TOKEN
    except ImportError:
        from backend.config import PANEL_PORT, BOT_TOKEN
    return PANEL_PORT, BOT_TOKEN


def _get_app_import_string() -> str:
    """Return the correct uvicorn import string for the app."""
    try:
        import src.telegram_panel.backend.app  # noqa: F401
        return "src.telegram_panel.backend.app:app"
    except ImportError:
        return "backend.app:app"


def run_bot_polling():
    """Run Telegram bot in a separate thread with its own event loop.

    Cannot use app.run_polling() here because it sets signal handlers
    which only work in the main thread.  Instead we manually drive the
    Application lifecycle with lower-level async methods.

    Retries with exponential backoff to handle DNS race conditions
    when the container network is not yet ready.
    """
    import asyncio
    import time

    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            BotClass = _import_bot_class()
            bot = BotClass()
            app = bot.get_app()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _run() -> None:
                await app.initialize()
                await app.start()
                await app.updater.start_polling()
                # Block until the daemon thread is killed with the process
                stop = asyncio.Event()
                await stop.wait()

            loop.run_until_complete(_run())
            break
        except Exception as e:
            if attempt < max_retries:
                delay = 2 ** attempt
                logger.warning("Telegram bot attempt %d/%d failed: %s. Retry in %ds...", attempt, max_retries, e, delay)
                time.sleep(delay)
            else:
                logger.error("Telegram bot failed after %d attempts: %s", max_retries, e)


def main():
    PANEL_PORT, BOT_TOKEN = _import_config()

    # Start Telegram bot in background thread
    if BOT_TOKEN:
        bot_thread = threading.Thread(target=run_bot_polling, daemon=True, name="telegram-bot")
        bot_thread.start()
        logger.info("Telegram bot started in background thread")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")

    # Run FastAPI server (blocking)
    app_string = _get_app_import_string()
    logger.info("Starting FastAPI on port %d (app=%s)", PANEL_PORT, app_string)
    uvicorn.run(
        app_string,
        host="0.0.0.0",
        port=PANEL_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    import uvicorn
    main()
