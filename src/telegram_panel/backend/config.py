import os
from pathlib import Path


def get_project_root() -> Path:
    """Get project root (3 levels up from this file: backend/ -> telegram_panel/ -> src/ -> root)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _parse_id_list(raw: str) -> set[int]:
    """Parse comma-separated list of Telegram user IDs."""
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


PROJECT_ROOT = get_project_root()

PANEL_PORT: int = int(os.getenv("PANEL_PORT", "8080"))
DATA_DIR: Path = Path(os.getenv("PANEL_DATA_DIR", str(PROJECT_ROOT / "data")))
CHARTS_DIR: Path = Path(os.getenv("PANEL_CHARTS_DIR", str(PROJECT_ROOT / "charts")))
CONFIG_PATH: Path = Path(os.getenv("PANEL_CONFIG_PATH", str(PROJECT_ROOT / "bot_config.json")))
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID: str = os.getenv("TELEGRAM_ADMIN_ID", "")
PANEL_URL: str = os.getenv("TELEGRAM_PANEL_URL", "")

# Множество Telegram user ID с доступом к панели и боту.
# Если пустое — фолбек на ADMIN_ID (обратная совместимость).
ALLOWED_IDS: set[int] = _parse_id_list(os.getenv("TELEGRAM_ALLOWED_IDS", ""))
if not ALLOWED_IDS and ADMIN_ID.isdigit():
    ALLOWED_IDS = {int(ADMIN_ID)}
