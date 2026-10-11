"""Read-only API execution: реальные SQLite-схемы, без биржи и lifespan."""

import importlib
import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from src.core.execution_journal import ExecutionJournal
from src.exchanges.impl.spot_trading_adapter import _SCHEMA
from src.telegram_panel.backend.routes import execution
from src.telegram_panel.backend.services import auth
from src.telegram_panel.backend.services.token_store import RateLimiter, TokenStore
from test_telegram_panel_auth import signed_init_data


@pytest.fixture
def panel(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "auth" / "tokens.json")
    limiter = RateLimiter()
    monkeypatch.setattr(auth, "BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(auth, "ALLOWED_IDS", {123})
    monkeypatch.setattr(auth, "get_token_store", lambda: store)
    monkeypatch.setattr(auth, "get_rate_limiter", lambda: limiter)
    monkeypatch.delenv("PANEL_ALLOW_LOCAL_DEV_AUTH", raising=False)
    monkeypatch.setattr(execution, "DATA_DIR", tmp_path / "journals")
    app = importlib.import_module("src.telegram_panel.backend.app").app
    return TestClient(app), {"X-Web-Token": store.generate_token(123)}, execution.DATA_DIR


@pytest.fixture
def journal(panel):
    _, _, root = panel
    writer = ExecutionJournal(root / "execution.sqlite3")
    # Открытый writer удерживает актуальный WAL: API не должен читать только main DB.
    connection = writer._connect()
    yield writer
    connection.close()


@pytest.fixture
def spot(panel):
    _, _, root = panel
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "spot_ledger.sqlite3")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(_SCHEMA)
    account = json.dumps(["spot-account", "mexc", "spot"], separators=(",", ":"))
    ns = json.dumps([account, "spot-owner"], separators=(",", ":"))
    db.execute("INSERT INTO owners VALUES (?,?,?,?,?)", (ns, account, "spot-owner", "USDT", "100"))
    db.execute("INSERT INTO command_meta(ns,command_id,cid,symbol,operation,request,state,created) VALUES (?,?,?,?,?,?,?,?)",
               (ns, "spot-command", "spot-cid", "ETHUSDT", "entry", '{"quantity":".2"}', "PREPARED", 123))
    db.execute("INSERT INTO commands(cid,ns,symbol,side,kind,qty,budget,oid,status,fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("spot-cid", ns, "ETHUSDT", "BUY", "LIMIT", ".2", "10", "spot-order", "NEW", "fingerprint"))
    db.execute("INSERT INTO lots(id,ns,symbol,qty,cost) VALUES (?,?,?,?,?)", ("lot-1", ns, "ETHUSDT", ".1", "5"))
    db.execute("INSERT INTO symbol_pauses VALUES (?,?,?)", (account, "ETHUSDT", "Проверить частичный вход"))
    db.execute("INSERT INTO pauses VALUES (?,?)", (account, "Пауза всего аккаунта"))
    db.commit()
    yield db
    db.close()


def test_auth_before_db_read(panel, monkeypatch):
    client, _, _ = panel
    monkeypatch.setattr(execution, "_read", lambda *args: pytest.fail("Неавторизованный запрос прочитал БД"))
    assert client.get("/api/execution/state").status_code == 401
    assert client.get("/api/execution/state", headers={"X-Web-Token": "invalid"}).status_code == 401


@pytest.mark.parametrize("method", ["web", "telegram"])
def test_absent_db_is_not_created_and_not_healthy(panel, method):
    client, headers, root = panel
    if method == "telegram":
        headers = {"X-Telegram-Init-Data": signed_init_data()}
    response = client.get("/api/execution/state", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "not_initialized"
    assert response.json()["sources"] == {"execution": False, "spot": False}
    assert "no-store" in response.headers["cache-control"]
    assert not root.exists()


@pytest.mark.parametrize("malformed", [b"not a sqlite database", b""])
def test_malformed_or_schema_missing_db_is_sanitized_503(panel, malformed):
    client, headers, root = panel
    root.mkdir()
    (root / "execution.sqlite3").write_bytes(malformed)
    response = client.get("/api/execution/state", headers=headers)
    assert response.status_code == 503
    assert "execution.sqlite3" not in response.text
    assert str(root) not in response.text
    assert "sqlite" not in response.text.lower()


def test_pending_blocks_ownership_and_terminal_proof(panel, journal):
    client, headers, _ = panel
    journal.prepare("account", "owner", "BTC-USDT", "entry", {"quantity": ".01"}, command_id="command")
    response = client.get("/api/execution/state", headers=headers).json()
    assert response["counts"]["pending"] == 1
    assert response["pending"][0]["state"] == "PREPARED"
    assert response["pending"][0]["quantity"] == ".01"
    journal.finish("account", "command", "ACK", order_id="order-1")
    assert client.get("/api/execution/state", headers=headers).json()["pending"][0]["state"] == "ACK"
    journal.pause("account", "BTCUSDT", "Позиция изменилась вручную")
    journal.finish("account", "command", "FILLED", ownership=[{
        "owner": "owner", "symbol": "BTCUSDT", "position_id": "position", "side": "LONG", "quantity": ".01"}])
    state = client.get("/api/execution/state", headers=headers).json()
    assert not state["pending"]
    assert state["blocks"][0]["reason"] == "Позиция изменилась вручную"
    assert state["ownership"][0]["quantity"] == ".01"
    assert state["status"] == "attention"
    # Новый terminal proof не разрешает панели самовольно снимать durable паузу.
    assert journal.blocked("account", "BTCUSDT") == "Позиция изменилась вручную"
    assert client.post("/api/execution/state", headers=headers).status_code == 405


def test_spot_projects_ack_and_new_terminal_fill(panel, spot):
    client, headers, _ = panel
    state = client.get("/api/execution/state", headers=headers).json()
    assert state["counts"] == {"pending": 1, "blocks": 2, "ownership": 1}
    assert state["pending"][0]["state"] == "ACK"
    assert state["pending"][0]["account"] == "spot-account"
    assert state["pending"][0]["owner"] == "spot-owner"
    assert state["pending"][0]["order_id"] == "spot-order"
    assert any(row["symbol"] is None for row in state["blocks"])
    spot.execute("UPDATE commands SET status='FILLED', filled='.2'")
    spot.commit()
    updated = client.get("/api/execution/state", headers=headers).json()
    assert not updated["pending"]
    assert len(updated["blocks"]) == 2
    assert spot.execute("SELECT state FROM command_meta").fetchone()[0] == "PREPARED"


def test_spot_terminal_partial_remains_visible(panel, spot):
    client, headers, _ = panel
    spot.execute("UPDATE commands SET status='CANCELED', filled='.1'")
    spot.commit()
    state = client.get("/api/execution/state", headers=headers).json()
    assert state["pending"][0]["state"] == "PARTIAL"
    assert state["pending"][0]["terminal"] == 1
    assert state["ownership"][0]["quantity"] == ".1"


def test_bounded_snapshot_and_no_raw_secrets(panel, journal):
    client, headers, _ = panel
    for index in range(4):
        journal.prepare("account", "owner", f"SYMBOL{index}", "entry", {"quantity": ".01", "debug": "private-request"}, command_id=str(index))
    journal.pause("account", "SYMBOL0", "token=synthetic-secret")
    response = client.get("/api/execution/state?limit=2", headers=headers)
    state = response.json()
    assert len(state["pending"]) == 2
    assert state["counts"]["pending"] == 4
    assert state["truncated"]["pending"]
    assert "private-request" not in response.text
    assert "synthetic-secret" not in response.text
    assert all(not {"request", "context", "result", "snapshot"}.intersection(row) for row in state["pending"])
    assert client.get("/api/execution/state?limit=101", headers=headers).status_code == 422


def test_reads_threadpool_ro_wal_without_private_calls(panel, journal, monkeypatch):
    client, headers, root = panel
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *args, **kwargs: pytest.fail("API вызвал приватный transport"))
    real_connect = sqlite3.connect
    threads = []
    statements = []
    def connect(path, *args, **kwargs):
        # sqlite3 общий для API и structured logging; наблюдаем только наш журнал.
        if "execution.sqlite3" not in str(path):
            return real_connect(path, *args, **kwargs)
        threads.append(threading.current_thread().name)
        assert str(path).endswith("?mode=ro") and "immutable" not in str(path)
        db = real_connect(path, *args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    journal.prepare("account", "owner", "BTCUSDT", "entry", {"quantity": ".01"})
    before = (root / "execution.sqlite3").stat().st_mtime_ns
    monkeypatch.setattr(execution.sqlite3, "connect", connect)
    assert client.get("/api/execution/state", headers=headers).json()["pending"]
    assert threads and all("AnyIO worker" in name for name in threads)
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for sql in statements)
    assert (root / "execution.sqlite3").stat().st_mtime_ns == before
