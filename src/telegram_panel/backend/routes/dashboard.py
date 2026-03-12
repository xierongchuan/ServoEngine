from fastapi import APIRouter, Depends

from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api", tags=["dashboard"])
reader = DataReader()


@router.get("/dashboard")
async def get_dashboard(_user: dict = Depends(get_current_user)) -> dict:
    config = reader.read_config()
    active = reader.read_active_trades()

    symbols = []
    exchange_symbols = config.get("EXCHANGE_SYMBOLS", {})
    for syms in exchange_symbols.values():
        symbols.extend(syms)

    # Handle both dict and list formats for active_trades
    # Dict format: {symbol: trade_data} - expected format from trade_tracker.py
    # List format: [{symbol: ..., ...}] - possible if file was corrupted or has legacy format
    if isinstance(active, dict):
        active_symbols = list(active.keys())
        active_count = len(active)
    elif isinstance(active, list):
        # Extract symbols from list items if they have 'symbol' key
        active_symbols = [t.get("symbol") for t in active if isinstance(t, dict) and t.get("symbol")]
        active_count = len(active)
    else:
        active_symbols = []
        active_count = 0

    return {
        "active_trades_count": active_count,
        "strategy_style": config.get("STRATEGY_STYLE", "UNKNOWN"),
        "symbols": symbols,
        "active_symbols": active_symbols,
    }
