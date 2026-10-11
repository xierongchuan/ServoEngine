"""Регрессии авторизации панели без Telegram/биржи и реальных секретов."""

import asyncio
import hashlib
import hmac
import importlib
import json
import multiprocessing
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from src.telegram_panel.backend.services import auth
from src.telegram_panel.backend.services.token_store import RateLimiter, TokenStore, get_project_root


def signed_init_data(timestamp=None, user=None, extra=None, include_auth_date=True):
    values = {"auth_date": str(int(time.time()) if timestamp is None else timestamp),
              "user": json.dumps(user or {"id": 123, "first_name": "Имя %20"}, ensure_ascii=False)}
    values.update(extra or {})
    if not include_auth_date:
        values.pop("auth_date")
    secret = hmac.new(b"WebAppData", b"test-bot-token", hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, "\n".join(f"{key}={value}" for key, value in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


@pytest.fixture
def panel(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    limiter = RateLimiter()
    monkeypatch.setattr(auth, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(auth, "BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(auth, "ALLOWED_IDS", {123})
    monkeypatch.setattr(auth, "get_token_store", lambda: store)
    monkeypatch.delenv("PANEL_ALLOW_LOCAL_DEV_AUTH", raising=False)
    module = importlib.import_module("src.telegram_panel.backend.app")
    monkeypatch.setattr(module, "get_token_store", lambda: store)
    monkeypatch.setattr(module, "_allowed_origins", ["https://panel.example"])
    monkeypatch.setattr(module, "WS_AUTH_CHECK_SECONDS", 0.03)
    return module, store, TestClient(module.app)


def test_init_data_valid_and_single_decode():
    assert auth.validate_init_data(signed_init_data(), "test-bot-token")["user"]["first_name"] == "Имя %20"


@pytest.mark.parametrize("offset", [None, -86401, 120], ids=["missing", "expired", "future"])
def test_init_data_rejects_old_future_missing(offset, monkeypatch):
    now = 1_800_000_000
    # Подменяем часы только auth, а не общий модуль time у SQLite/WS/логирования.
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setenv("PANEL_INIT_DATA_TTL_SECONDS", "86400")
    timestamp = 0 if offset is None else now + offset
    assert auth.validate_init_data(signed_init_data(timestamp), "test-bot-token") is None


@pytest.mark.parametrize("offset, valid", [(-86401, False), (-86400, False), (-86399, True),
                                           (0, True), (30, True), (31, False)])
def test_init_data_exact_age_and_future_boundaries(offset, valid, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setenv("PANEL_INIT_DATA_TTL_SECONDS", "86400")
    assert (auth.validate_init_data(signed_init_data(now + offset), "test-bot-token") is not None) is valid


def test_init_data_missing_auth_date_rejected(monkeypatch):
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: 1_800_000_000))
    assert auth.validate_init_data(signed_init_data(include_auth_date=False), "test-bot-token") is None


def test_init_data_ttl_configurable(monkeypatch):
    monkeypatch.setenv("PANEL_INIT_DATA_TTL_SECONDS", "60")
    assert auth.validate_init_data(signed_init_data(int(time.time()) - 90), "test-bot-token") is None
    assert auth.validate_init_data(signed_init_data(), "test-bot-token")


def test_init_data_signature_duplicates_and_missing_date():
    value = signed_init_data()
    assert auth.validate_init_data(value + "&auth_date=1", "test-bot-token") is None
    assert auth.validate_init_data(value.replace("hash=", "invalid="), "test-bot-token") is None
    assert auth.validate_init_data(signed_init_data("not-a-date"), "test-bot-token") is None


def test_allowlist_failclosed_and_no_implicit_dev(monkeypatch):
    monkeypatch.setattr(auth, "BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(auth, "ALLOWED_IDS", set())
    with pytest.raises(HTTPException) as caught:
        auth.authenticate(init_data=signed_init_data())
    assert caught.value.status_code == 403
    monkeypatch.setattr(auth, "BOT_TOKEN", "")
    with pytest.raises(HTTPException):
        auth.authenticate(init_data="anything")
    with pytest.raises(HTTPException):
        auth.validate_init_data_string("anything")


def test_dev_requires_explicit_loopback(monkeypatch):
    monkeypatch.setattr(auth, "BOT_TOKEN", "")
    monkeypatch.setenv("PANEL_ALLOW_LOCAL_DEV_AUTH", "1")
    local = Request({"type": "http", "client": ("127.0.0.1", 123), "headers": []})
    remote = Request({"type": "http", "client": ("10.0.0.2", 123), "headers": []})
    assert auth.local_dev_enabled(local)
    assert not auth.local_dev_enabled(remote)


def test_web_token_multiuse_revoke_and_quota(tmp_path):
    first = TokenStore(tmp_path / "tokens.json")
    second = TokenStore(tmp_path / "tokens.json")
    token = first.generate_token(123, max_tokens_per_user=1)
    for _ in range(30):
        assert second.validate_token(token)
    with pytest.raises(ValueError):
        second.generate_token(123, max_tokens_per_user=1)
    assert second.revoke_token(token)
    assert first.validate_token(token) is None
    replacement = first.generate_token(123, max_tokens_per_user=1)
    assert second.revoke_all_user_tokens(123) == 1
    assert first.validate_token(replacement) is None


def test_legacy_used_not_revoked(tmp_path):
    now = datetime.now(timezone.utc)
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"tokens": {"legacy": {"user_id": 123, "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(hours=6)).isoformat(), "used": True}}}))
    store = TokenStore(path)
    assert store.validate_token("legacy")
    assert store.revoke_token("legacy")
    assert not store.validate_token("legacy")


def test_corrupted_storage_fails_closed_without_overwrite(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("{broken")
    store = TokenStore(path)
    assert store.validate_token("anything") is None
    with pytest.raises(ValueError):
        store.generate_token(123)
    assert path.read_text() == "{broken"


def _generate_in_process(path, output):
    output.put(TokenStore(path).generate_token(123, max_tokens_per_user=100))


def test_process_safe_generation(tmp_path):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    path = tmp_path / "tokens.json"
    processes = [context.Process(target=_generate_in_process, args=(path, output)) for _ in range(6)]
    for process in processes:
        process.start()
    tokens = [output.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert len(set(tokens)) == 6
    assert all(TokenStore(path).validate_token(token) for token in tokens)
    assert path.stat().st_mode & 0o777 == 0o600


def test_storage_root_correct():
    assert (get_project_root() / "src" / "telegram_panel").is_dir()


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Init-Data": "invalid"}])
def test_private_routes_and_ticket_require_auth(panel, headers):
    _, _, client = panel
    for path in ("/api/logs/system", "/api/logs/events", "/api/logs/sources"):
        assert client.get(path, headers=headers).status_code == 401
    assert client.post("/api/ws-ticket", headers=headers).status_code == 401
    assert client.get("/api/debug", headers=headers).status_code == 401


def test_failed_auth_rate_limit_does_not_throttle_valid_session(panel):
    _, store, client = panel
    for _ in range(10):
        assert client.post("/api/ws-ticket", headers={"X-Web-Token": "invalid"}).status_code == 401
    assert client.post("/api/ws-ticket", headers={"X-Web-Token": "invalid"}).status_code == 429
    assert client.post("/api/ws-ticket", headers={"X-Web-Token": store.generate_token(123)}).status_code == 200


def test_debug_disabled_and_cache_control(panel):
    _, store, client = panel
    headers = {"X-Web-Token": store.generate_token(123)}
    assert client.get("/api/debug", headers=headers).status_code == 404
    response = client.post("/api/ws-ticket", headers=headers)
    assert response.status_code == 200
    assert response.json()["expires_in"] == 30
    assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.parametrize("method", ["web", "telegram"])
def test_ws_dual_auth_ticket_one_use(panel, method):
    _, store, client = panel
    headers = {"X-Web-Token": store.generate_token(123)} if method == "web" else {"X-Telegram-Init-Data": signed_init_data()}
    ticket = client.post("/api/ws-ticket", headers=headers).json()["ticket"]
    with client.websocket_connect("/ws?ticket=" + ticket, headers={"Origin": "https://panel.example"}) as ws:
        ws.send_text("heartbeat")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?ticket=" + ticket, headers={"Origin": "https://panel.example"}):
            pass


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
def test_ws_origin_before_accept(panel, origin):
    _, store, client = panel
    ticket = client.post("/api/ws-ticket", headers={"X-Web-Token": store.generate_token(123)}).json()["ticket"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?ticket=" + ticket, headers={"Origin": origin} if origin else {}):
            pass


def test_ws_revocation_closes_open_idle_socket(panel):
    _, store, client = panel
    token = store.generate_token(123)
    ticket = client.post("/api/ws-ticket", headers={"X-Web-Token": token}).json()["ticket"]
    with client.websocket_connect("/ws?ticket=" + ticket, headers={"Origin": "https://panel.example"}) as ws:
        store.revoke_token(token)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_text()
        assert caught.value.code == 4401


@pytest.mark.parametrize("method", ["web", "telegram"])
def test_ws_expiry_closes_open_socket(panel, monkeypatch, method):
    _, store, client = panel
    token = store.generate_token(123)
    headers = {"X-Web-Token": token} if method == "web" else {"X-Telegram-Init-Data": signed_init_data()}
    ticket = client.post("/api/ws-ticket", headers=headers).json()["ticket"]
    with client.websocket_connect("/ws?ticket=" + ticket, headers={"Origin": "https://panel.example"}) as ws:
        if method == "web":
            with store._transaction(write=True) as data:
                data["tokens"][token]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        else:
            monkeypatch.setenv("PANEL_INIT_DATA_TTL_SECONDS", "1")
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_text()
        assert caught.value.code == 4401


def test_revoked_after_ticket_before_handshake(panel):
    _, store, client = panel
    token = store.generate_token(123)
    ticket = client.post("/api/ws-ticket", headers={"X-Web-Token": token}).json()["ticket"]
    store.revoke_token(token)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?ticket=" + ticket, headers={"Origin": "https://panel.example"}):
            pass


def test_default_web_ttl_six_hours(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    entry = store.validate_token(store.generate_token(123))
    assert datetime.fromisoformat(entry["expires_at"]) - datetime.fromisoformat(entry["created_at"]) == timedelta(hours=6)


def test_ticket_expiry_and_cross_store_consumption(tmp_path):
    path = tmp_path / "tokens.json"
    first, second = TokenStore(path), TokenStore(path)
    ticket = first.issue_ws_ticket({"web_token": "fake"})
    assert second.consume_ws_ticket(ticket) == {"web_token": "fake"}
    assert not first.consume_ws_ticket(ticket)
    ticket = first.issue_ws_ticket({"web_token": "fake"})
    data = json.loads(path.read_text())
    data["ws_tickets"][ticket]["expires"] = time.time() - 1
    path.write_text(json.dumps(data))
    assert not second.consume_ws_ticket(ticket)


def test_error_sanitized_and_no_credentials_in_logs(panel, caplog):
    module, store, client = panel
    token = store.generate_token(123)
    with caplog.at_level("INFO"):
        assert client.post("/api/ws-ticket", headers={"X-Web-Token": token}).status_code == 200
        request = Request({"type": "http", "method": "GET", "path": "/api/test", "headers": []})
        response = asyncio.run(module.global_exception_handler(request, ValueError("secret-value")))
    assert token not in caplog.text
    assert "secret-value" not in caplog.text
    body = json.loads(response.body)
    assert body["request_id"]
    assert body["detail"] == "Внутренняя ошибка панели"
    assert "secret-value" not in response.body.decode()
