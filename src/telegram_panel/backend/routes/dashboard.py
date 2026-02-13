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

    return {
        "active_trades_count": len(active),
        "strategy_style": config.get("STRATEGY_STYLE", "UNKNOWN"),
        "symbols": symbols,
        "active_symbols": list(active.keys()),
    }
