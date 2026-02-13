from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import DATA_DIR
from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/logs", tags=["logs"])
reader = DataReader()


@router.get("/system")
async def get_system_logs(
    lines: int = Query(100, ge=1, le=1000),
    _user: dict = Depends(get_current_user),
) -> dict:
    log_lines = reader.read_log_tail(DATA_DIR / "steps.log", lines)
    return {"source": "system", "lines": log_lines}


@router.get("/{symbol}")
async def get_symbol_logs(
    symbol: str,
    lines: int = Query(100, ge=1, le=1000),
    _user: dict = Depends(get_current_user),
) -> dict:
    # Sanitize symbol to prevent path traversal
    safe_symbol = symbol.replace("/", "").replace("\\", "").replace("..", "")
    if not safe_symbol:
        raise HTTPException(status_code=400, detail="Invalid symbol")

    log_path = DATA_DIR / "logs" / f"{safe_symbol}.log"
    log_lines = reader.read_log_tail(log_path, lines)
    return {"source": safe_symbol, "lines": log_lines}
