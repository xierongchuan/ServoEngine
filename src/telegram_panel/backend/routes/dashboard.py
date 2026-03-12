import json
from pathlib import Path

from fastapi import APIRouter, Depends

from ..services.auth import get_current_user
from ..services.data_reader import DataReader
from ..config import CONFIG_PATH

router = APIRouter(prefix="/api", tags=["dashboard"])
reader = DataReader()

# New config system dir
_CONFIG_DIR = CONFIG_PATH.parent / "config"


def _get_symbols_and_strategy() -> tuple[list[str], str]:
    """Get symbols and strategy from new config system or legacy config."""
    active_path = _CONFIG_DIR / "active.json"
    if active_path.exists():
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                active_cfg = json.load(f)
            symbols_map = active_cfg.get("symbols", {})
            symbols = []
            for syms in symbols_map.values():
                symbols.extend(syms)
            strategy = active_cfg.get("strategy", "UNKNOWN")
            return symbols, strategy
        except Exception:
            pass

    # Legacy fallback
    config = reader.read_config()
    symbols = []
    for syms in config.get("EXCHANGE_SYMBOLS", {}).values():
        symbols.extend(syms)
    return symbols, config.get("STRATEGY_STYLE", "UNKNOWN")


@router.get("/dashboard")
async def get_dashboard(_user: dict = Depends(get_current_user)) -> dict:
    symbols, strategy = _get_symbols_and_strategy()
    active = reader.read_active_trades()

    if isinstance(active, dict):
        active_symbols = list(active.keys())
        active_count = len(active)
    elif isinstance(active, list):
        active_symbols = [t.get("symbol") for t in active if isinstance(t, dict) and t.get("symbol")]
        active_count = len(active)
    else:
        active_symbols = []
        active_count = 0

    return {
        "active_trades_count": active_count,
        "strategy": strategy,
        "strategy_style": strategy,
        "symbols": symbols,
        "active_symbols": active_symbols,
    }
