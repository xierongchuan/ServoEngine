from datetime import datetime

from fastapi import APIRouter, Depends, Query

from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/trades", tags=["trades"])
reader = DataReader()


@router.get("/active")
async def get_active_trades(_user: dict = Depends(get_current_user)) -> dict:
    return reader.read_active_trades()


@router.get("/history")
async def get_trade_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user: dict = Depends(get_current_user),
) -> dict:
    history = reader.read_trade_history()
    total = len(history)
    # Return newest first
    history.reverse()
    page = history[offset : offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "trades": page}


@router.get("/stats")
async def get_trade_stats(_user: dict = Depends(get_current_user)) -> dict:
    history = reader.read_trade_history()
    if not history:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_duration_hours": 0.0,
            "wins": 0,
            "losses": 0,
        }

    wins = 0
    total_pnl = 0.0
    durations: list[float] = []

    for trade in history:
        pnl = float(trade.get("last_pnl", 0) or 0)
        total_pnl += pnl
        if pnl > 0:
            wins += 1

        open_time = trade.get("open_time")
        close_time = trade.get("close_time")
        if open_time and close_time:
            try:
                t_open = datetime.fromisoformat(open_time)
                t_close = datetime.fromisoformat(close_time)
                durations.append((t_close - t_open).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass

    total = len(history)
    return {
        "total_trades": total,
        "win_rate": round(wins / total * 100, 2) if total else 0.0,
        "total_pnl": round(total_pnl, 4),
        "avg_duration_hours": round(sum(durations) / len(durations), 2) if durations else 0.0,
        "wins": wins,
        "losses": total - wins,
    }
