"""Fake transport для всех провайдеров: HTTP, hot-reload, роли и безопасность."""

import json
from copy import deepcopy

import pytest
import requests

from src.ai import client
from src.ai_settings import DEFAULT_AI_SETTINGS, PROVIDERS, resolve_ai_role


class FakeResponse:
    def __init__(self, data=None, status=200, body=None):
        self.status_code = status
        self.body = body if body is not None else json.dumps(data or {}).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def completion(content='{"action":"hold","confidence":0}', **message):
    return FakeResponse({"choices": [{"finish_reason": "stop", "message": {"content": content, **message}}]})


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    for name, metadata in PROVIDERS.items():
        monkeypatch.setenv(metadata["env"], "synthetic-" + name + "-key")
    monkeypatch.setattr(requests, "request", lambda *args, **kwargs: pytest.fail("Реальный AI-запрос запрещён"))


def settings_for(provider):
    settings = deepcopy(DEFAULT_AI_SETTINGS)
    settings["provider"] = provider
    if provider == "lmstudio":
        settings["providers"][provider]["model"] = "local/model"
    return resolve_ai_role(settings)


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_provider_payload_url_and_credentials(provider):
    settings = settings_for(provider)
    settings["fallback_models"] = ["fallback/model"]
    transport = FakeTransport(completion())
    assert client.complete(settings, "PRIVATE PROMPT", transport) == '{"action":"hold","confidence":0}'
    method, url, kwargs = transport.calls[0]
    assert method == "POST" and url == settings["base_url"] + "/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-" + provider + "-key"
    assert kwargs["allow_redirects"] is False
    if provider == "openrouter":
        assert kwargs["json"]["models"] == [settings["model"], "fallback/model"]
        assert "reasoning" in kwargs["json"] and "X-Title" in kwargs["headers"]
    else:
        assert not {"reasoning", "provider", "models"} & kwargs["json"].keys()
        assert not {"HTTP-Referer", "X-Title"} & kwargs["headers"].keys()
    assert transport.responses == []


def test_lmstudio_optional_key(monkeypatch):
    monkeypatch.delenv("LM_STUDIO_API_KEY")
    transport = FakeTransport(completion())
    client.complete(settings_for("lmstudio"), "test", transport)
    assert "Authorization" not in transport.calls[0][2]["headers"]


@pytest.mark.parametrize("provider", ["openrouter", "deepseek"])
def test_missing_required_key_no_request(provider, monkeypatch):
    monkeypatch.delenv(PROVIDERS[provider]["env"])
    transport = FakeTransport()
    with pytest.raises(client.AIProviderError, match="Ключ API"):
        client.complete(settings_for(provider), "test", transport)
    assert transport.calls == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_bounded_retry_status(status):
    settings = settings_for("deepseek")
    settings["retry_count"] = 2
    sleeps = []
    response = FakeResponse(status=status, body=b"PRIVATE BODY")
    transport = FakeTransport(response, response, completion())
    client.complete(settings, "PRIVATE PROMPT", transport, sleep=sleeps.append)
    assert sleeps == [2, 4] and len(transport.calls) == 3 and response.closed


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 404])
def test_nonretryable_status_safe_message(status):
    transport = FakeTransport(FakeResponse(status=status, body=b"PRIVATE BODY synthetic-deepseek-key"))
    with pytest.raises(client.AIProviderError) as exc:
        client.complete(settings_for("deepseek"), "PRIVATE PROMPT", transport)
    assert len(transport.calls) == 1
    assert "PRIVATE" not in str(exc.value) and "synthetic" not in str(exc.value)
    assert transport.calls[0][2]["allow_redirects"] is False


@pytest.mark.parametrize("content", [None, "", "   ", [], {"action": "buy"}, '<think>{"action":"buy"}</think>'])
def test_never_trade_on_reasoning_only(content):
    transport = FakeTransport(completion(content, reasoning_content='{"action":"buy"}', reasoning='{"action":"buy"}'))
    with pytest.raises(client.AIProviderError, match="финального"):
        client.complete(settings_for("deepseek"), "test", transport)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", "error"])
def test_nonfinal_completion_rejected(finish_reason):
    response = FakeResponse({"choices": [{"finish_reason": finish_reason, "message": {"content": '{"action":"buy"}'}}]})
    with pytest.raises(client.AIProviderError):
        client.complete(settings_for("openrouter"), "test", FakeTransport(response))


def test_network_exception_safe_retry():
    sleeps = []
    transport = FakeTransport(requests.exceptions.Timeout("PRIVATE URL KEY"), completion())
    client.complete(settings_for("deepseek"), "private", transport, sleep=sleeps.append)
    assert sleeps == [2]


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_default_probe_only_get_models(provider):
    transport = FakeTransport(FakeResponse({"data": [{"id": settings_for(provider)["model"]}]}))
    result = client.probe_provider(settings_for(provider), transport=transport)
    assert result["ok"] and result["provider"] == provider
    assert transport.calls[0][0] == "GET" and transport.calls[0][2]["json"] is None
    assert transport.calls[0][1].endswith("/models")


def test_explicit_paid_probe_minimal():
    transport = FakeTransport(completion("OK"))
    assert client.probe_provider(settings_for("openrouter"), True, transport)["ok"]
    payload = transport.calls[0][2]["json"]
    assert payload["max_tokens"] == 16 and payload["messages"][0]["content"] == "Reply OK."
    assert not {"reasoning", "provider", "models"} & payload.keys()


def test_runtime_provider_and_role_hotreload(monkeypatch):
    from src import config
    from src.core.predict import get_prediction
    settings = deepcopy(DEFAULT_AI_SETTINGS)
    monkeypatch.setattr(config, "BOT_CONFIG", {"AI_SETTINGS": settings})
    transport = FakeTransport(completion(), completion(), completion(), completion())
    monkeypatch.setattr(client.requests, "request", transport.request)
    assert isinstance(get_prediction("private"), str)
    settings["provider"] = "deepseek"
    settings["providers"]["deepseek"]["base_url"] = "https://custom.example/v1"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-rotated-key")
    assert isinstance(get_prediction("private"), str)
    assert transport.calls[1][1] == "https://custom.example/v1/chat/completions"
    assert transport.calls[1][2]["headers"]["Authorization"] == "Bearer synthetic-rotated-key"
    settings["providers"]["lmstudio"]["model"] = "local/model"
    settings["overrides"]["veto"]["provider"] = "lmstudio"
    assert isinstance(get_prediction("private", role="veto"), str)
    assert transport.calls[2][2]["json"]["model"] == "local/model"
    assert transport.calls[2][2]["json"]["max_tokens"] == 100
    # Обычный запрос с такими же лимитами не становится veto и сохраняет explicit model.
    assert isinstance(get_prediction("private", model="explicit/model", max_tokens=100, temperature=0.1), str)
    assert transport.calls[3][1] == "https://custom.example/v1/chat/completions"
    assert transport.calls[3][2]["json"]["model"] == "explicit/model"


@pytest.mark.parametrize("max_tokens,temperature", [(100, 0.1), (150, 0.2)])
@pytest.mark.parametrize("model", [None, "explicit/model"])
def test_normal_sampling_limits_never_select_role(max_tokens, temperature, model, monkeypatch):
    from src import config
    from src.core.predict import get_prediction
    settings = deepcopy(DEFAULT_AI_SETTINGS)
    settings["providers"]["lmstudio"]["model"] = "local/role"
    settings["overrides"]["veto"]["provider"] = "deepseek"
    settings["overrides"]["regime"]["provider"] = "lmstudio"
    instance_config = {"AI_SETTINGS": settings}
    monkeypatch.setattr(config, "BOT_CONFIG", {"AI_SETTINGS": {"provider": "deepseek"}})
    transport = FakeTransport(completion())
    monkeypatch.setattr(client.requests, "request", transport.request)
    assert isinstance(get_prediction("ordinary", model=model, max_tokens=max_tokens, temperature=temperature, config=instance_config), str)
    _, url, kwargs = transport.calls[0]
    assert url == settings["providers"]["openrouter"]["base_url"] + "/chat/completions"
    assert kwargs["json"]["model"] == (model or settings["providers"]["openrouter"]["model"])
    assert kwargs["json"]["max_tokens"] == max_tokens and kwargs["json"]["temperature"] == temperature


def test_runtime_errors_hold_without_private_logs(monkeypatch, caplog):
    from src.core.predict import get_prediction
    from src import config

    monkeypatch.setattr(config, "BOT_CONFIG", {"AI_SETTINGS": {"provider": "deepseek"}})
    monkeypatch.setattr(client.requests, "request", FakeTransport(FakeResponse(status=401, body=b"PRIVATE BODY synthetic-deepseek-key")).request)
    result = get_prediction("PRIVATE PROMPT")
    assert result["action"] == "hold" and result["confidence"] == 0
    assert "PRIVATE" not in caplog.text and "synthetic-deepseek-key" not in caplog.text
    assert "PRIVATE" not in json.dumps(result)


def test_models_filter_invalid_and_secret_ids():
    response = FakeResponse({"data": [{"id": "valid/model"}, {"id": "synthetic-deepseek-key"}, {"id": "invalid model"}]})
    assert client.list_models(settings_for("deepseek"), FakeTransport(response)) == [{"id": "valid/model"}]


def test_transport_response_size_and_invalid_json(monkeypatch):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 16)
    oversized = FakeResponse(body=b"x" * 17)
    with pytest.raises(client.AIProviderError) as exc:
        client.request_json(settings_for("deepseek"), "models", transport=FakeTransport(oversized))
    assert exc.value.code == "response_limit" and oversized.closed
    invalid = FakeResponse(body=b"not json")
    with pytest.raises(client.AIProviderError) as exc:
        client.request_json(settings_for("deepseek"), "models", transport=FakeTransport(invalid))
    assert exc.value.code == "invalid_response" and invalid.closed


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_config_reload_updates_compatible_globals(provider, monkeypatch):
    from src import config
    for name in dir(config):
        if name.isupper() or name in {"_config_mtime", "_config_callbacks"}:
            monkeypatch.setattr(config, name, getattr(config, name))
    settings = deepcopy(DEFAULT_AI_SETTINGS)
    settings["provider"] = provider
    settings["providers"][provider]["model"] = "updated/model"
    settings["providers"][provider]["base_url"] = "https://updated.example/v1"
    settings["overrides"]["veto"]["provider"] = "deepseek"
    monkeypatch.setattr(config, "load_bot_config", lambda: {"AI_SETTINGS": settings})
    monkeypatch.setattr(config, "_use_new_config_system", lambda: False)
    monkeypatch.setattr(config, "_config_callbacks", [])
    config.reload_bot_config()
    assert config.AI_PROVIDER == provider and config.AI_MODEL == "updated/model"
    assert config.AI_API_KEY == "synthetic-" + provider + "-key"
    assert config.AI_BASE_URL == "https://updated.example/v1/chat/completions"
    assert config.AI_VETO_OVERRIDE["provider"] == "deepseek"


@pytest.mark.parametrize("invalid", [None, {"provider": "unknown"}, {"max_tokens": 0},
                                      {"temperature": float("nan")}, {"overrides": {"veto": {"provider": []}}}])
def test_invalid_ai_reload_preserves_entire_published_snapshot(invalid, monkeypatch):
    from src import config
    old_config = config.BOT_CONFIG
    before = {name: deepcopy(getattr(config, name)) for name in dir(config) if name.isupper()}
    old_mtime = config._config_mtime
    monkeypatch.setattr(config, "_use_new_config_system", lambda: False)
    monkeypatch.setattr(config, "load_bot_config", lambda: {"AI_SETTINGS": invalid, "POSITION_SIZE_PERCENT": 99})
    monkeypatch.setattr(config, "get_config_mtime", lambda: pytest.fail("mtime не должен обновляться до валидации"))
    monkeypatch.setattr(config, "_config_callbacks", [lambda *args: pytest.fail("callback не должен видеть invalid snapshot")])
    assert config.reload_bot_config() is old_config
    assert config._config_mtime == old_mtime
    assert {name: getattr(config, name) for name in before} == before


def test_corrupt_modular_base_reload_does_not_fall_back_to_legacy(tmp_path, monkeypatch):
    from src import config, config_loader
    before = config.BOT_CONFIG
    (tmp_path / "active.json").write_text('{"strategy":"SCALP"}')
    (tmp_path / "base.json").write_text('{"ai":')
    monkeypatch.setattr(config, "_NEW_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config_loader, "_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_use_new_config_system", lambda: True)
    monkeypatch.setattr(config_loader, "clear_config_cache", lambda: None)
    assert config.reload_bot_config() is before
