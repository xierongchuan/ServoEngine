"""Регрессии запуска и панели: временные данные, без сети и торговых процессов."""

import asyncio
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.core.execution_journal import ExecutionJournal
from src.exchanges.rate_limit import ExchangeRateLimiter
from src.telegram_panel.backend.routes import runtime, trades
from src.telegram_panel.backend.services.data_reader import DataReader
from src.trading_settings import managed_state_block_reason
from src.utils.log_context import get_log_context
from src.utils.log_store import EventStore
from test_runtime_settings_lifecycle import FakeProcess, JsonRequest
from test_runtime_settings_lifecycle import lifecycle as lifecycle


def create_log_store(directory):
    store = EventStore(directory)
    store.write({"event_id": "test-event", "timestamp": "2026-10-11T00:00:00Z",
                 "level": "INFO", "category": "system", "source": "test",
                 "message": "Тестовый запуск"})
    return store.path


@pytest.mark.parametrize("kind", ["logs", "limiter"])
def test_infrastructure_database_does_not_block_or_change_data(tmp_path, kind):
    if kind == "logs":
        path = create_log_store(tmp_path)
    else:
        limiter = ExchangeRateLimiter("test", capacity=5, period=60, data_dir=tmp_path)
        limiter.acquire()
        limiter.cooldown(30)
        path = tmp_path / "exchange_rate_limits.sqlite3"
    before = path.read_bytes()
    assert managed_state_block_reason(tmp_path) is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["extra_table", "missing_column", "extra_column", "version", "corrupt"])
def test_changed_log_schema_remains_fail_closed(tmp_path, change):
    path = create_log_store(tmp_path)
    if change == "corrupt":
        path.write_bytes(b"not sqlite")
    else:
        with sqlite3.connect(path) as db:
            if change == "extra_table":
                db.execute("CREATE TABLE pending_orders (id TEXT)")
            elif change == "missing_column":
                db.execute("ALTER TABLE log_events RENAME COLUMN payload TO unknown")
            elif change == "extra_column":
                db.execute("ALTER TABLE log_events ADD COLUMN ownership TEXT")
            else:
                db.execute("PRAGMA user_version=99")
    assert managed_state_block_reason(tmp_path) is not None


def test_events_filename_in_logs_does_not_hide_execution_journal(tmp_path):
    directory = tmp_path / "logs"
    directory.mkdir()
    journal = ExecutionJournal(directory / "events.sqlite3")
    journal.prepare("old:bingx:demo", "owner", "BTC-USDT", "entry", {})
    reason = managed_state_block_reason(tmp_path)
    assert reason is not None and "events.sqlite3" in reason


def test_infrastructure_does_not_hide_pending_journal(tmp_path):
    create_log_store(tmp_path)
    ExchangeRateLimiter("test", capacity=5, period=60, data_dir=tmp_path)
    journal = ExecutionJournal(tmp_path / "orders.db")
    journal.prepare("old:bingx:demo", "owner", "BTC-USDT", "entry", {})
    reason = managed_state_block_reason(tmp_path)
    assert reason is not None and "orders.db" in reason


def test_infrastructure_guard_sees_committed_schema_in_wal(tmp_path):
    path = create_log_store(tmp_path)
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE unknown_state (id TEXT)")
        db.commit()
        assert path.with_name(path.name + "-wal").stat().st_size > 0
        assert managed_state_block_reason(tmp_path) is not None
    finally:
        db.close()


def test_supervisor_starts_after_logging_and_rate_limiter_initialization(lifecycle):
    supervisor, _, data = lifecycle
    create_log_store(data)
    ExchangeRateLimiter("test", capacity=5, period=60, data_dir=data)
    supervisor.start_runtime()
    assert supervisor.state == "running"
    assert not supervisor.last_error
    assert len(FakeProcess.created) == 1
    assert FakeProcess.created[0].args[0]["execution_mode"] == "read_only"


def test_infrastructure_allows_stopped_execution_config_edit(lifecycle):
    from src.telegram_panel.backend.routes import config_routes
    _, _, data = lifecycle
    create_log_store(data)
    ExchangeRateLimiter("test", capacity=5, period=60, data_dir=data)
    result = asyncio.run(config_routes.update_execution_config(JsonRequest({"account_id": "second"}), {}))
    assert result["execution"]["account_id"] == "second"


@pytest.mark.parametrize("state", ["stopped", "crashed"])
@pytest.mark.parametrize("action", ["start", "restart"])
def test_worker_command_without_runtime_is_conflict(lifecycle, monkeypatch, state, action):
    from src.symbol_runtime_control import write_symbol_command
    supervisor, settings_path, data = lifecycle
    supervisor.state = state
    supervisor._write_status()
    command_path = data / "symbol_runtime_command.json"
    monkeypatch.setattr(runtime, "write_symbol_command", lambda action, **kwargs: write_symbol_command(
        action, command_path=command_path, settings_path=settings_path, **kwargs))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(runtime.enqueue_symbol_runtime_command(action, JsonRequest({"instance_id": "btc_macdx"}), {}))
    assert caught.value.status_code == 409
    assert not command_path.exists()


@pytest.mark.parametrize("body", [None, [], "invalid", 1])
def test_worker_command_requires_object(body, monkeypatch):
    monkeypatch.setattr(runtime, "write_symbol_command", lambda *a, **kw: pytest.fail("Команда не должна отправляться"))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(runtime.enqueue_symbol_runtime_command("restart", JsonRequest(body), {}))
    assert caught.value.status_code == 422


def test_worker_command_io_error_is_safe_503(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("/private/path secret-token")
    monkeypatch.setattr(runtime, "write_symbol_command", fail)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(runtime.enqueue_symbol_runtime_command("restart", JsonRequest({"instance_id": "btc_macdx"}), {}))
    assert caught.value.status_code == 503
    assert "private" not in caught.value.detail
    assert "secret-token" not in caught.value.detail


@pytest.mark.parametrize("data,expected", [
    ({}, []),
    ([{"last_pnl": 1}, None, "bad"], [{"last_pnl": 1}]),
    ({"first": {"last_pnl": 1}, "second": {"last_pnl": -2}}, [{"last_pnl": 1}, {"last_pnl": -2}]),
    ({"BTCUSDT": [{"last_pnl": 1}, False], "ETHUSDT": [{"last_pnl": -2}]}, [{"last_pnl": 1}, {"last_pnl": -2}]),
    ("invalid", []), (None, []),
])
def test_history_normalization_and_routes_leave_file_unchanged(tmp_path, monkeypatch, data, expected):
    path = tmp_path / "trade_history.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    reader = DataReader(data_dir=tmp_path)
    monkeypatch.setattr(trades, "reader", reader)
    assert reader.read_trade_history() == expected
    result = asyncio.run(trades.get_trade_history(limit=50, offset=0, _user={}))
    assert result["total"] == len(expected)
    assert result["trades"] == list(reversed(expected))
    stats = asyncio.run(trades.get_trade_stats({}))
    assert stats["total_trades"] == len(expected)
    assert stats["total_pnl"] == sum(row["last_pnl"] for row in expected)
    assert path.read_bytes() == before


def test_history_endpoint_empty_mapping_returns_200(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from src.telegram_panel.backend.services.auth import get_current_user
    (tmp_path / "trade_history.json").write_text("{}")
    monkeypatch.setattr(trades, "reader", DataReader(data_dir=tmp_path))
    app = FastAPI()
    app.include_router(trades.router)
    def test_user():
        return {}
    app.dependency_overrides[get_current_user] = test_user
    client = TestClient(app)
    assert client.get("/api/trades/history").json()["trades"] == []
    assert client.get("/api/trades/stats").json()["total_trades"] == 0


def test_exception_diagnostics_include_route_and_location_without_secrets(monkeypatch):
    module = importlib.import_module("src.telegram_panel.backend.app")
    records = []
    monkeypatch.setattr(module.logger, "error", lambda message, *args: records.append((message % args, get_log_context())))
    request = Request({"type": "http", "method": "GET", "path": "/api/secret-path",
                       "query_string": b"token=query-secret", "headers": [],
                       "route": SimpleNamespace(path="/api/trades/{symbol}")})
    request.state.request_id = "request-test"
    try:
        raise AttributeError("exception-secret /private/secret-path")
    except AttributeError as exc:
        response = asyncio.run(module.global_exception_handler(request, exc))
    assert response.status_code == 500
    message, context = records[0]
    assert "/api/trades/{symbol}" in message
    assert "AttributeError" in message
    assert "test_runtime_panel_regressions.py:" in message
    assert context["request_id"] == "request-test"
    assert context["category"] == "panel"
    combined = message + response.body.decode()
    for secret in ("exception-secret", "secret-path", "query-secret", "/private/"):
        assert secret not in combined


def test_request_response_logging_uses_matched_route_template(monkeypatch):
    module = importlib.import_module("src.telegram_panel.backend.app")
    from starlette.responses import Response
    records = []
    monkeypatch.setattr(module.logger, "info", lambda message, *args: records.append((message % args, get_log_context())))
    request = Request({"type": "http", "method": "GET", "path": "/api/trades/history",
                       "query_string": b"token=secret-query", "headers": []})
    async def call_next(incoming):
        incoming.scope["route"] = SimpleNamespace(path="/api/trades/history")
        return Response(status_code=200)
    response = asyncio.run(module.request_logging_middleware(request, call_next))
    assert "/api/trades/history" in records[-1][0]
    assert "secret-query" not in str(records)
    assert all(context["request_id"] == response.headers["X-Request-ID"] for _, context in records)
