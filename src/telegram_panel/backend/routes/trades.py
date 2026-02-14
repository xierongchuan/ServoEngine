from datetime import datetime
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query, HTTPException

from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/trades", tags=["trades"])
reader = DataReader()


def get_project_root() -> Path:
    """Resolve project root relative to this file."""
    return Path(__file__).resolve().parent.parent.parent.parent


CONFIG_PATH = Path(os.environ.get("PANEL_CONFIG_PATH", str(get_project_root() / "bot_config.json")))


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


def read_json(path: Path) -> dict | list | None:
    """Safely read and parse a JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path: Path, data: dict | list) -> bool:
    """Safely write data to a JSON file."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


@router.post("/disable/{symbol}")
async def disable_symbol(
    symbol: str,
    _user: dict = Depends(get_current_user),
) -> dict:
    """Disable trading for a symbol."""
    symbol = symbol.upper().replace(" ", "")

    config = read_json(CONFIG_PATH) or {}
    disabled = config.get("DISABLED_SYMBOLS", [])

    if symbol in disabled:
        return {"status": "already_disabled", "symbol": symbol}

    disabled.append(symbol)
    config["DISABLED_SYMBOLS"] = disabled

    if write_json(CONFIG_PATH, config):
        return {"status": "success", "symbol": symbol, "action": "disabled"}
    return {"status": "error", "message": "Failed to write config"}


@router.post("/enable/{symbol}")
async def enable_symbol(
    symbol: str,
    _user: dict = Depends(get_current_user),
) -> dict:
    """Enable trading for a symbol."""
    symbol = symbol.upper().replace(" ", "")

    config = read_json(CONFIG_PATH) or {}
    disabled = config.get("DISABLED_SYMBOLS", [])

    if symbol not in disabled:
        return {"status": "already_enabled", "symbol": symbol}

    disabled.remove(symbol)
    config["DISABLED_SYMBOLS"] = disabled

    if write_json(CONFIG_PATH, config):
        return {"status": "success", "symbol": symbol, "action": "enabled"}
    return {"status": "error", "message": "Failed to write config"}


@router.get("/disabled")
async def get_disabled_symbols(
    _user: dict = Depends(get_current_user),
) -> dict:
    """Get list of disabled symbols."""
    config = read_json(CONFIG_PATH) or {}
    return {"disabled_symbols": config.get("DISABLED_SYMBOLS", [])}


@router.post("/close/{symbol}")
async def close_position_by_symbol(
    symbol: str,
    _user: dict = Depends(get_current_user),
) -> dict:
    """Close position for a symbol at market price."""
    symbol = symbol.upper().replace(" ", "")

    # Read active trades
    project_root = get_project_root()
    active_path = project_root / "data" / "active_trades.json"
    active = read_json(active_path)

    if not isinstance(active, dict) or symbol not in active:
        raise HTTPException(status_code=404, detail=f"No active position for {symbol}")

    trade = active[symbol]
    deal_id = trade.get("deal_id") or trade.get("dealId")

    if not deal_id:
        raise HTTPException(status_code=400, detail=f"Cannot find deal ID for {symbol}")

    # Try to close the position
    try:
        # Import here to avoid circular imports
        import sys
        sys.path.insert(0, str(project_root))
        from src.exchanges.exchange_factory import get_exchange_client

        client = get_exchange_client()

        if hasattr(client, "close_position"):
            success = client.close_position(symbol, deal_id, 1.0)
            if success:
                return {"status": "success", "symbol": symbol, "message": "Position closed"}
            return {"status": "error", "message": "Failed to close position on exchange"}
        raise HTTPException(status_code=400, detail="Exchange client does not support close_position")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
