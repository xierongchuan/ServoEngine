"""Контракт frontend API, авторизация и guard всех способов сохранения AI."""

from copy import deepcopy
import json
import threading
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import requests

from src.ai import client as ai_client
from src.ai_settings import DEFAULT_AI_SETTINGS, PROVIDERS
from src.telegram_panel.backend.routes import ai, config_routes
from src.telegram_panel.backend.services.auth import get_current_user


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def iter_content(self, chunk_size):
        yield json.dumps(self.data).encode()

    def close(self):
        pass


@pytest.fixture
def panel(tmp_path, monkeypatch):
    for provider, metadata in PROVIDERS.items():
        monkeypatch.setenv(metadata["env"], "synthetic-" + provider + "-key")
    monkeypatch.delenv("PANEL_ALLOW_LOCAL_DEV_AUTH", raising=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    strategies = config_dir / "strategies"
    strategies.mkdir()
    profiles = config_dir / "profiles"
    profiles.mkdir()
    (strategies / "scalp.json").write_text('{"preset": {"leverage": 10}}')
    (config_dir / "active.json").write_text('{"strategy":"SCALP","strategy_instances":[]}')
    (config_dir / "trading.json").write_text('{}')
    settings = deepcopy(DEFAULT_AI_SETTINGS)
    settings["providers"]["lmstudio"]["model"] = "local/model"
    settings["providers"]["deepseek"]["base_url"] = "https://persisted.example/v1"
    (config_dir / "base.json").write_text(json.dumps({"ai": settings}))
    monkeypatch.setattr(config_routes, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_routes, "STRATEGIES_DIR", strategies)
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles)
    monkeypatch.setattr(config_routes, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: pytest.fail("Внешние AI-запросы запрещены"))
    app = FastAPI()
    app.include_router(config_routes.router)
    app.include_router(ai.router)
    with TestClient(app) as http:
        yield app, http, config_dir


def authorize(panel):
    panel[0].dependency_overrides[get_current_user] = lambda: {"user": {"id": 1}}
    return panel[1]


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/config/ai", None),
    ("PUT", "/api/config/ai", {"provider": "deepseek"}),
    ("POST", "/api/config/ai/probe", {"provider": "deepseek"}),
    ("GET", "/api/config/ai/models?provider=lmstudio", None),
])
def test_ai_routes_require_existing_auth(panel, method, path, body):
    assert panel[1].request(method, path, json=body).status_code == 401


def test_get_contract_and_secret_free_statuses(panel):
    http = authorize(panel)
    result = http.get("/api/config/ai").json()
    assert set(result) == {"settings", "providers"}
    assert result["settings"]["providers"]["deepseek"]["base_url"] == "https://persisted.example/v1"
    assert len(result["providers"]) == 3
    for status in result["providers"]:
        assert set(status) == {"id", "label", "requires_api_key", "has_api_key", "configured"}
        assert status["configured"] and status["has_api_key"]
    assert "synthetic" not in json.dumps(result)


def test_put_persists_canonical_and_preserves_endpoint(panel):
    http = authorize(panel)
    response = http.put("/api/config/ai", json={"provider": "deepseek", "model": "custom-model"})
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"settings", "needs_restart"} and result["needs_restart"] is True
    settings = result["settings"]
    assert settings["provider"] == "deepseek"
    assert settings["providers"]["deepseek"]["base_url"] == "https://persisted.example/v1"
    assert settings["providers"]["deepseek"]["model"] == "custom-model"
    assert not {"base_url", "model", "api_key"} & settings.keys()
    assert json.loads((panel[2] / "base.json").read_text())["ai"] == settings


@pytest.mark.parametrize("value", [[], {"provider": []}, {"temperature": True}, {"api_key": "private"},
                                   {"retry_count": 100}, {"providers": {"lmstudio": {"base_url": "http://user:secret@localhost/v1"}}}])
def test_ai_put_strict_validation_no_write(panel, value):
    http = authorize(panel)
    before = (panel[2] / "base.json").read_bytes()
    response = http.put("/api/config/ai", json=value)
    assert response.status_code == 422
    assert (panel[2] / "base.json").read_bytes() == before
    assert "private" not in response.text and "user:secret" not in response.text


@pytest.mark.parametrize("path,field", [("/api/config", "AI_SETTINGS"), ("/api/config/base", "ai"),
                                         ("/api/config/trading", "ai"), ("/api/config/strategies/scalp", "ai"),
                                         ("/api/config/profiles/test", "ai")])
@pytest.mark.parametrize("invalid", [{"temperature": True}, {"api_key": "PRIVATE KEY"}, {"provider": "wrong"}, None])
def test_all_save_paths_guard_ai(panel, path, field, invalid):
    http = authorize(panel)
    before = {str(file): file.read_bytes() for file in panel[2].rglob("*.json")}
    response = http.put(path, json={field: invalid})
    assert response.status_code == 422, response.text
    assert "PRIVATE KEY" not in response.text
    assert before == {str(file): file.read_bytes() for file in panel[2].rglob("*.json")}


def test_general_config_save_really_persists_ai(panel):
    http = authorize(panel)
    response = http.put("/api/config", json={"AI_SETTINGS": {"provider": "deepseek", "temperature": 0.6}})
    assert response.status_code == 200
    settings = json.loads((panel[2] / "base.json").read_text())["ai"]
    assert settings["provider"] == "deepseek" and settings["temperature"] == 0.6


def test_legacy_model_save_inherits_selected_provider(panel):
    http = authorize(panel)
    assert http.put("/api/config/ai", json={"provider": "deepseek"}).status_code == 200
    for path in ("/api/config/base", "/api/config/strategies/scalp", "/api/config/profiles/test"):
        response = http.put(path, json={"ai": {"model": "profile-model"}})
        assert response.status_code == 200, response.text
        section = response.json().get("config", response.json().get("strategy", response.json().get("profile")))
        assert section["ai"]["providers"]["deepseek"]["model"] == "profile-model"
        assert "model" not in section["ai"]


def test_profile_partial_save_does_not_overwrite_base_provider(panel):
    http = authorize(panel)
    response = http.put("/api/config/profiles/test", json={"_strategy": "SCALP", "ai": {"providers": {"deepseek": {"model": "custom-model"}}}})
    assert response.status_code == 200, response.text
    saved = json.loads((panel[2] / "profiles/test.json").read_text())["ai"]
    assert saved == {"providers": {"deepseek": {"model": "custom-model"}}}


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_probe_uses_persisted_profile_and_only_get(panel, monkeypatch, provider):
    http = authorize(panel)
    calls = []
    main_thread = threading.get_ident()

    def fake(method, url, **kwargs):
        assert threading.get_ident() != main_thread
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        calls.append((method, url, kwargs))
        return Response({"data": [{"id": "available-model"}]})

    monkeypatch.setattr(ai_client.requests, "request", fake)
    response = http.post("/api/config/ai/probe", json={"provider": provider})
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] and result["provider"] == provider and type(result["latency_ms"]) is int
    assert calls[0][0] == "GET" and calls[0][2]["json"] is None
    if provider == "deepseek":
        assert calls[0][1] == "https://persisted.example/v1/models"
    assert calls[0][2]["allow_redirects"] is False
    if provider != "openrouter":
        assert "X-Title" not in calls[0][2]["headers"]


@pytest.mark.parametrize("body", [{"provider": "wrong"}, {"provider": []}, {"provider": "deepseek", "base_url": "https://evil.example"},
                                  {"provider": "deepseek", "allow_paid_completion": "true"}, {"provider": "deepseek", "prompt": "private"}])
def test_probe_rejects_unpersisted_config_and_implicit_paid(panel, body):
    assert authorize(panel).post("/api/config/ai/probe", json=body).status_code == 422


def test_models_contract(panel, monkeypatch):
    calls = []
    def fake(method, url, **kwargs):
        calls.append((method, url))
        return Response({"data": [{"id": "local/model", "owned_by": "private"}]})
    monkeypatch.setattr(ai_client.requests, "request", fake)
    response = authorize(panel).get("/api/config/ai/models?provider=lmstudio")
    assert response.status_code == 200
    assert response.json() == {"provider": "lmstudio", "models": [{"id": "local/model"}]}
    assert calls == [("GET", "http://host.containers.internal:1234/v1/models")]


def test_explicit_paid_probe(panel, monkeypatch):
    calls = []
    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})
    monkeypatch.setattr(ai_client.requests, "request", fake)
    response = authorize(panel).post("/api/config/ai/probe", json={"provider": "deepseek", "allow_paid_completion": True})
    assert response.json()["ok"]
    assert len(calls) == 1 and calls[0][0] == "POST"
    assert calls[0][1] == "https://persisted.example/v1/chat/completions"
    assert calls[0][2]["json"]["max_tokens"] == 16


def test_old_config_secrets_not_returned(panel):
    raw = {"ai": {"api_key": "private-unknown-key", "model": "test-model"}, "DEEPSEEK_API_KEY": "private-unknown-key"}
    (panel[2] / "base.json").write_text(json.dumps(raw))
    response = authorize(panel).get("/api/config/base")
    assert "private-unknown-key" not in response.text and "api_key" not in response.text.lower()


def test_cancelled_http_call_keeps_network_slot_until_thread_finishes(monkeypatch):
    from fastapi import HTTPException
    entered = threading.Event()
    release = threading.Event()

    def blocking():
        entered.set()
        assert release.wait(timeout=3)
        return "done"

    async def exercise():
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(ai, "_network_slots", slots)
        task = asyncio.create_task(ai.bounded_call(blocking))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert slots.locked()
        with pytest.raises(HTTPException) as exc:
            await ai.bounded_call(lambda: "unexpected")
        assert exc.value.status_code == 429
        release.set()
        for _ in range(100):
            if not slots.locked():
                break
            await asyncio.sleep(0.01)
        assert not slots.locked()

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_legacy_ai_api_uses_same_guard_and_contract(panel, monkeypatch):
    class LegacyReader:
        def __init__(self):
            self.config = {"AI_SETTINGS": {"model": "legacy/model", "base_url": "https://legacy.example/v1/chat/completions"}}
            self.config_path = panel[2] / "legacy.json"
            self.config_path.write_text(json.dumps(self.config))
        def read_config(self):
            return deepcopy(self.config)
        def write_config(self, value):
            self.config = deepcopy(value)
            self.config_path.write_text(json.dumps(value))
    reader = LegacyReader()
    monkeypatch.setattr(config_routes, "reader", reader)
    monkeypatch.setattr(config_routes, "_use_new_config_system", lambda: False)
    http = authorize(panel)
    settings = http.get("/api/config/ai").json()["settings"]
    assert settings["providers"]["openrouter"]["base_url"] == "https://legacy.example/v1"
    response = http.put("/api/config/ai", json={"provider": "deepseek"})
    assert response.status_code == 200 and response.json()["needs_restart"] is True
    assert reader.config["AI_SETTINGS"]["provider"] == "deepseek"
    before = deepcopy(reader.config)
    assert http.put("/api/config", json={"AI_SETTINGS": {"api_key": "private"}}).status_code == 422
    assert reader.config == before


@pytest.mark.parametrize("contents", [b'{"ai":', b'[]', b'null', b'{}', b'{"ai":null}', b'\xff', None])
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/config/ai", None),
    ("POST", "/api/config/ai/probe", {"provider": "deepseek"}),
    ("GET", "/api/config/ai/models?provider=deepseek", None),
    ("PUT", "/api/config/ai", {"provider": "lmstudio"}),
    ("PUT", "/api/config/base", {"ai": {"provider": "deepseek"}}),
])
def test_corrupt_saved_ai_is_never_defaulted_or_overwritten(panel, contents, method, path, body):
    target = panel[2] / "base.json"
    if contents is None:
        target.unlink()
    else:
        target.write_bytes(contents)
    response = authorize(panel).request(method, path, json=body)
    assert response.status_code == 422, response.text
    assert target.read_bytes() == contents if contents is not None else not target.exists()


def test_deepseek_thinking_api_contract_and_legacy_target_preservation(panel):
    http = authorize(panel)
    patch = {"provider": "deepseek", "providers": {"deepseek": {"thinking": {"enabled": True, "effort": "minimal"}}}}
    response = http.put("/api/config/ai", json=patch)
    assert response.status_code == 200, response.text
    settings = response.json()["settings"]
    assert settings["providers"]["deepseek"]["thinking"] == {"enabled": True, "effort": "low"}
    assert settings["providers"]["deepseek"]["base_url"] == "https://persisted.example/v1"
    assert settings["providers"]["deepseek"]["model"] == "deepseek-flash"
    assert http.get("/api/config/ai").json()["settings"] == settings
    assert json.loads((panel[2] / "base.json").read_text())["ai"] == settings


@pytest.mark.parametrize("path,field", [("/api/config/ai", None), ("/api/config", "AI_SETTINGS"),
                                       ("/api/config/base", "ai"), ("/api/config/strategies/scalp", "ai"),
                                       ("/api/config/profiles/test", "ai")])
def test_all_save_paths_reject_invalid_thinking_without_writes(panel, path, field):
    patch = {"providers": {"deepseek": {"thinking": {"enabled": "true", "effort": "max"}}}}
    before = {str(file): file.read_bytes() for file in panel[2].rglob("*.json")}
    response = authorize(panel).put(path, json={field: patch} if field else patch)
    assert response.status_code == 422, response.text
    assert before == {str(file): file.read_bytes() for file in panel[2].rglob("*.json")}


def test_ai_put_preserves_all_non_ai_sections_without_redaction(panel):
    target = panel[2] / "base.json"
    before = json.loads(target.read_text())
    before.update({"_description": "Метаданные", "news": {"enabled": True, "text": "token=placeholder"},
                   "chart_settings": {"max_tokens": 10, "custom": [1, 2]}, "other": {"untouched": None}})
    target.write_text(json.dumps(before))
    assert authorize(panel).put("/api/config/ai", json={"temperature": 0.6}).status_code == 200
    after = json.loads(target.read_text())
    assert {key: value for key, value in after.items() if key != "ai"} == {key: value for key, value in before.items() if key != "ai"}


@pytest.mark.parametrize("first,second", [("ai", "base"), ("base", "ai"), ("ai", "ai")])
def test_ai_and_base_writers_share_read_modify_write_lock(panel, monkeypatch, first, second):
    from concurrent.futures import ThreadPoolExecutor
    entered, release, started, second_read = (threading.Event() for _ in range(4))
    original_save = config_routes._save_json
    original_read = config_routes._read_config_document
    second_thread = []

    def blocked_save(*args):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        return original_save(*args)

    def observed_read(*args):
        if second_thread and threading.get_ident() == second_thread[0]:
            second_read.set()
        return original_read(*args)

    def write(kind, index):
        if index == 2:
            second_thread.append(threading.get_ident())
            started.set()
        if kind == "ai":
            return ai._persist_settings({"temperature": 0.4} if index == 1 else {"max_tokens": 1234})
        return config_routes._update_base_document({"news": {"enabled": True}})

    monkeypatch.setattr(config_routes, "_save_json", blocked_save)
    monkeypatch.setattr(config_routes, "_read_config_document", observed_read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first_result = workers.submit(write, first, 1)
        assert entered.wait(timeout=3)
        second_result = workers.submit(write, second, 2)
        try:
            assert started.wait(timeout=3)
            assert not second_read.wait(timeout=0.1)
        finally:
            release.set()
        first_result.result(timeout=3)
        second_result.result(timeout=3)
    saved = json.loads((panel[2] / "base.json").read_text())
    if first == "ai":
        assert saved["ai"]["temperature"] == 0.4
    if second == "ai":
        assert saved["ai"]["max_tokens"] == 1234
    if "base" in {first, second}:
        assert saved["news"] == {"enabled": True}
    assert second_read.is_set()
