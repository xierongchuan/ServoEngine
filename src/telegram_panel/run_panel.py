#!/usr/bin/env python3
"""Запуск FastAPI и Telegram-бота в едином namespace.

Из корня репозитория и текущего контейнера: python -m src.telegram_panel.run_panel.
Из src/telegram_panel: python run_panel.py (корень добавляется в sys.path).
"""

import logging
import os
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------# Load .env from project root BEFORE any other imports that use env vars
# ---------------------------------------------------------------------------
_this_dir = Path(__file__).resolve().parent

# Detect if we're in Docker (/app/) or local (src/telegram_panel/)
if _this_dir.name == "telegram_panel":
    # Local: .../src/telegram_panel/  → project root is 2 levels up
    _project_root = _this_dir.parent.parent
else:
    # Docker: /app/ — project root is cwd or parent
    _project_root = _this_dir

# Load .env FIRST, before any imports that might use environment variables
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

# Ensure project root is in sys.path so `src.*` imports work locally
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Один namespace предотвращает дублирование singleton-хранилищ в bot/backend.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("panel")
# HTTPX печатает URL Telegram API с bot token на INFO; секреты не должны уходить в журнал.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _import_bot_class():
    """Импорт бота в едином namespace приложения."""
    from src.telegram_panel.bot import TelegramPanelBot
    return TelegramPanelBot


def _import_config():
    """Импорт общей конфигурации бота и backend."""
    from src.telegram_panel.backend.config import PANEL_PORT, BOT_TOKEN
    return PANEL_PORT, BOT_TOKEN


def _get_app_import_string() -> str:
    """Return the correct uvicorn import string for the app."""
    return "src.telegram_panel.backend.app:app"


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
                logger.warning("⚠️ Попытка запуска бота %d/%d: %s. Повтор через %ds", attempt, max_retries, type(e).__name__, delay)
                time.sleep(delay)
            else:
                logger.error("❌ Бот не запущен после %d попыток: %s", max_retries, type(e).__name__)


def main():
    import uvicorn

    PANEL_PORT, BOT_TOKEN = _import_config()
    from src.telegram_panel.backend.config import DATA_DIR
    from src.utils.logger import configure_logging, protect_logging_handlers

    configure_logging(data_dir=DATA_DIR)
    protect_logging_handlers()

    # Start Telegram bot in background thread
    if BOT_TOKEN:
        bot_thread = threading.Thread(target=run_bot_polling, daemon=True, name="telegram-bot")
        bot_thread.start()
        logger.info("✅ Telegram-бот запущен в фоновом потоке")
    else:
        logger.warning("⚠️ TELEGRAM_BOT_TOKEN не задан — бот отключён")

    # Run FastAPI server (blocking)
    app_string = _get_app_import_string()
    logger.info("Запуск FastAPI на порту %d (app=%s)", PANEL_PORT, app_string)
    uvicorn.run(
        app_string,
        host="127.0.0.1" if os.getenv("PANEL_ALLOW_LOCAL_DEV_AUTH") == "1" else "0.0.0.0",
        proxy_headers=os.getenv("PANEL_ALLOW_LOCAL_DEV_AUTH") != "1",
        port=PANEL_PORT,
        log_level="info",
        access_log=False,  # Query старых PNG-ссылок и WS-билеты не попадают в access log.
    )


if __name__ == "__main__":
    import uvicorn
    main()
