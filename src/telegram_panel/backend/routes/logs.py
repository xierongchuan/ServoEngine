from datetime import datetime, timezone
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.utils.log_context import normalize_symbol
from src.utils.helpers import get_filename
from src.utils.log_store import InvalidLogCursor, LogQueryTimeout
from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/logs", tags=["logs"])
reader = DataReader()


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except ValueError:
        raise HTTPException(422, "Время должно быть ISO 8601 с часовым поясом") from None


def _read(function, *args):
    try:
        return function(*args)
    except InvalidLogCursor as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except (LogQueryTimeout, sqlite3.Error, OSError):
        raise HTTPException(503, "Журнал временно недоступен; повторите запрос или сузьте фильтры") from None


# Sync endpoints FastAPI выполняет в threadpool, не в event loop.
@router.get("/events")
def get_events(
    response: Response,
    level: str | None = Query(None, max_length=80),
    category: str | None = Query(None, max_length=150),
    exchange: str | None = Query(None, max_length=64),
    symbol: str | None = Query(None, max_length=40),
    instance: str | None = Query(None, max_length=256),
    search: str | None = Query(None, max_length=256),
    order: str | None = Query(None, max_length=256),
    request: str | None = Query(None, max_length=256),
    source: str | None = Query(None, max_length=40),
    time_from: str | None = Query(None, max_length=40),
    time_to: str | None = Query(None, max_length=40),
    cursor: str | None = Query(None, max_length=1024),
    limit: int = Query(100, ge=1, le=200),
    _user: dict = Depends(get_current_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    filters = {"level": level, "category": category, "exchange": exchange, "symbol": symbol,
               "instance": instance, "search": search, "order": order, "request": request,
               "source": source, "time_from": _time(time_from), "time_to": _time(time_to)}
    if filters["time_from"] and filters["time_to"] and filters["time_from"] > filters["time_to"]:
        raise HTTPException(422, "Начало периода позже окончания")
    return _read(reader.read_log_events, filters, cursor, limit)


@router.get("/sources")
def get_sources(response: Response, _user: dict = Depends(get_current_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return _read(reader.read_log_sources)


@router.get("/system")
def get_system_logs(response: Response, lines: int = Query(100, ge=1, le=1000), _user: dict = Depends(get_current_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"source": "system", "lines": _read(reader.read_log_tail, reader.data_dir / "steps.log", lines)}


@router.get("/{symbol}")
def get_symbol_logs(symbol: str, response: Response, lines: int = Query(100, ge=1, le=1000), _user: dict = Depends(get_current_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        safe_symbol = normalize_symbol(symbol)
    except ValueError:
        raise HTTPException(400, "Недопустимый символ") from None
    return {"source": safe_symbol, "lines": _read(reader.read_log_tail, reader.data_dir / "logs" / f"{get_filename(safe_symbol)}.log", lines)}
