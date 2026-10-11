"""Read-only проекция durable-журналов; не импортирует торговые clients/coordinators."""

import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from src.utils.log_context import SecretRedactor

from ..config import DATA_DIR
from ..services.auth import get_current_user

router = APIRouter(prefix="/api/execution", tags=["execution"])
logger = logging.getLogger("panel.execution")
PENDING_SQL = "('PREPARED','ACK','UNKNOWN','PARTIAL')"


def _rows(db, query, limit):
    rows = [dict(row) for row in db.execute(query + " LIMIT ?", (limit,))]
    total = rows[0].pop("total") if rows else 0
    for row in rows:
        row.pop("total", None)
        # Размер полей тоже ограничен; произвольные JSON-поля никогда не возвращаются.
        for key, value in row.items():
            if isinstance(value, str):
                row[key] = value[:512]
    return rows, total


def _execution(db, limit):
    pending = _rows(db, f"""
        SELECT account, owner, symbol, operation, state, order_id, updated,
               json_extract(request, '$.quantity') AS quantity,
               json_extract(result, '$.reason') AS reason, COUNT(*) OVER() AS total
        FROM intents WHERE state IN {PENDING_SQL} ORDER BY updated DESC, account, command_id
    """, limit)
    blocks = _rows(db, """
        SELECT account, NULL AS owner, symbol, 'pause' AS operation, 'PAUSED' AS state,
               NULL AS order_id, updated, reason, NULL AS quantity, COUNT(*) OVER() AS total
        FROM blocks ORDER BY updated DESC, account, symbol
    """, limit)
    ownership = _rows(db, """
        SELECT account, owner, symbol, 'ownership' AS operation, 'OWNED' AS state,
               position_id, NULL AS order_id, NULL AS updated, NULL AS reason, quantity,
               COUNT(*) OVER() AS total FROM ownership ORDER BY account, owner, symbol, position_id
    """, limit)
    return {"pending": pending, "blocks": blocks, "ownership": ownership}


def _spot(db, limit):
    # Проекция повторяет _command_view: PREPARED meta не скрывает ACK/fill из commands.
    pending = _rows(db, f"""
        WITH projected AS (
          SELECT json_extract(o.account, '$[0]') AS account, o.instance AS owner,
                 m.symbol, m.operation, m.created AS updated,
                 COALESCE(c.oid, json_extract(m.result, '$.order_id')) AS order_id,
                 COALESCE(c.qty, json_extract(m.request, '$.quantity')) AS quantity,
                 json_extract(m.result, '$.reason') AS reason,
                 CASE WHEN c.cid IS NULL THEN m.state
                      WHEN c.status = 'FILLED' THEN 'FILLED'
                      WHEN c.status = 'REJECTED' THEN 'REJECTED'
                      WHEN CAST(c.filled AS REAL) > 0 THEN 'PARTIAL'
                      WHEN c.status IN ('CANCELED','EXPIRED') THEN 'RESOLVED'
                      WHEN c.status = 'INTENT' THEN 'UNKNOWN' ELSE 'ACK' END AS state,
                 CASE WHEN c.cid IS NULL THEN NULL
                      ELSE c.status IN ('FILLED','CANCELED','REJECTED','EXPIRED') END AS terminal
          FROM command_meta m JOIN owners o ON o.ns = m.ns
          LEFT JOIN commands c ON c.cid = m.cid AND c.ns = m.ns
        ) SELECT *, COUNT(*) OVER() AS total FROM projected
          WHERE state IN {PENDING_SQL} ORDER BY updated DESC, account, owner, symbol
    """, limit)
    # Пауза аккаунта относится ко всем его символам: symbol=NULL, а не выдуманный символ.
    blocks = _rows(db, """
        SELECT json_extract(account, '$[0]') AS account, NULL AS owner, symbol,
               'pause' AS operation, 'PAUSED' AS state, NULL AS order_id,
               NULL AS updated, reason, NULL AS quantity, COUNT(*) OVER() AS total
        FROM (SELECT account, NULL AS symbol, reason FROM pauses
              UNION ALL SELECT account, symbol, reason FROM symbol_pauses)
        ORDER BY account, symbol
    """, limit)
    ownership = _rows(db, """
        SELECT json_extract(o.account, '$[0]') AS account, o.instance AS owner, l.symbol,
               'ownership' AS operation, 'OWNED' AS state, l.id AS position_id,
               NULL AS order_id, NULL AS updated, NULL AS reason, l.qty AS quantity,
               COUNT(*) OVER() AS total FROM lots l JOIN owners o ON o.ns = l.ns
        ORDER BY account, owner, l.symbol, l.id
    """, limit)
    return {"pending": pending, "blocks": blocks, "ownership": ownership}


def _read(path: Path, reader, limit):
    if path.is_symlink():
        raise OSError("Недопустимый журнал")
    if not path.exists():
        return None
    if not path.is_file():
        raise OSError("Журнал недоступен")
    # mode=ro не создаёт отсутствующую БД; immutable запрещён, иначе теряется актуальный WAL.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 1
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute("BEGIN")
        return reader(db, limit)


@router.get("/state")
def get_execution_state(response: Response, limit: int = Query(100, ge=1, le=100),
                        _user: dict = Depends(get_current_user)) -> dict:
    """Sync endpoint выполняется в threadpool; только чтение одного snapshot на БД."""
    response.headers["Cache-Control"] = "no-store"
    result = {"pending": [], "blocks": [], "ownership": [],
              "counts": {"pending": 0, "blocks": 0, "ownership": 0}, "sources": {},
              "checked_at": time.time(), "limit": limit}
    try:
        for source, filename, reader in (("execution", "execution.sqlite3", _execution),
                                         ("spot", "spot_ledger.sqlite3", _spot)):
            state = _read(DATA_DIR / filename, reader, limit)
            result["sources"][source] = state is not None
            if state is None:
                continue
            for category, (rows, total) in state.items():
                result[category].extend({**row, "source": source} for row in rows)
                result["counts"][category] += total
        result["truncated"] = {}
        for category in ("pending", "blocks", "ownership"):
            result[category] = result[category][:limit]
            result["truncated"][category] = result["counts"][category] > len(result[category])
        result["status"] = ("not_initialized" if not any(result["sources"].values()) else
                            "attention" if result["counts"]["pending"] or result["counts"]["blocks"] else "ok")
        return SecretRedactor().clean(result)
    except (sqlite3.Error, OSError, ValueError):
        logger.warning("⚠️ Журнал исполнения временно недоступен")
        raise HTTPException(503, "Журнал исполнения недоступен. Повторите запрос позже.") from None
