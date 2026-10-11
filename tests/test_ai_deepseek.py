"""Актуальный DeepSeek: defaults, изолированный thinking и сохранение legacy профилей."""

from copy import deepcopy

import pytest

from src.ai import client
from src.ai_settings import AISettingsError, normalize_ai_settings, resolve_ai_role
from test_ai_runtime import FakeResponse, FakeTransport, completion


def test_new_deepseek_defaults_use_root_flash_and_disable_thinking():
    settings = normalize_ai_settings({"provider": "deepseek"})
    assert settings["providers"]["deepseek"] == {
        "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
        "thinking": {"enabled": False, "effort": "high"},
    }
    selected = resolve_ai_role(settings, "veto")
    payload = client.completion_payload(selected, "offline")
    assert payload["model"] == "deepseek-flash" and payload["max_tokens"] == 100
    assert payload["thinking"] == {"type": "disabled"} and payload["reasoning_effort"] == "none"
    assert not {"reasoning", "provider", "models"} & payload.keys()


@pytest.mark.parametrize("model", ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro", "custom/model"])
@pytest.mark.parametrize("url", ["https://api.deepseek.com/v1", "https://custom.example/api/v1"])
def test_explicit_legacy_deepseek_model_and_endpoint_never_overwritten(model, url):
    legacy = {"provider": "deepseek", "model": model, "base_url": url + "/chat/completions"}
    canonical = {"provider": "deepseek", "providers": {"deepseek": {"model": model, "base_url": url}}}
    for raw in (legacy, canonical):
        before = deepcopy(raw)
        profile = normalize_ai_settings(raw)["providers"]["deepseek"]
        assert profile["model"] == model and profile["base_url"] == url
        assert profile["thinking"] == {"enabled": False, "effort": "high"}
        assert raw == before


@pytest.mark.parametrize("effort,expected", [("minimal", "low"), ("low", "low"), ("medium", "high"),
                                             ("high", "high"), ("xhigh", "high"), ("max", "max"), ("none", "none")])
def test_deepseek_effort_aliases_canonicalized_without_touching_openrouter(effort, expected):
    settings = normalize_ai_settings({"provider": "deepseek",
        "providers": {"deepseek": {"thinking": {"enabled": True, "effort": effort}}},
        "reasoning": {"enabled": True, "effort": "medium", "exclude": True}})
    profile = settings["providers"]["deepseek"]["thinking"]
    assert profile["effort"] in {"low", "high", "max"}
    assert profile["enabled"] is (effort != "none")
    payload = client.completion_payload(resolve_ai_role(settings), "offline")
    assert payload["reasoning_effort"] == expected
    assert payload["thinking"] == {"type": "disabled" if effort == "none" else "enabled"}
    assert "reasoning" not in payload
    settings["provider"] = "openrouter"
    openrouter = client.completion_payload(resolve_ai_role(settings), "offline")
    assert openrouter["reasoning"]["effort"] == "medium"
    assert not {"thinking", "reasoning_effort"} & openrouter.keys()
    settings["provider"] = "lmstudio"
    settings["providers"]["lmstudio"]["model"] = "local/model"
    local = client.completion_payload(resolve_ai_role(settings), "offline")
    assert not {"reasoning", "thinking", "reasoning_effort", "provider", "models"} & local.keys()


@pytest.mark.parametrize("thinking", [None, [], {"enabled": 1}, {"enabled": "true"}, {"effort": []},
                                       {"effort": "invalid"}, {"type": "enabled"}, {"api_key": "private"}])
def test_deepseek_thinking_validation(thinking):
    with pytest.raises(AISettingsError):
        normalize_ai_settings({"providers": {"deepseek": {"thinking": thinking}}})


@pytest.mark.parametrize("provider", ["openrouter", "lmstudio"])
def test_thinking_cannot_be_saved_on_other_provider(provider):
    with pytest.raises(AISettingsError):
        normalize_ai_settings({"providers": {provider: {"thinking": {"enabled": True, "effort": "high"}}}})


def test_deepseek_payload_hotreloads_profile_thinking_and_keeps_reasoning_private(monkeypatch):
    from src import config
    from src.core.predict import get_prediction
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek-key")
    transport = FakeTransport(completion(), completion(), completion(None, reasoning_content='{"action":"buy"}'))
    monkeypatch.setattr(client.requests, "request", transport.request)
    for index, enabled in enumerate((False, True, True)):
        settings = {"provider": "deepseek", "providers": {"deepseek": {"thinking": {"enabled": enabled, "effort": "max"}}}}
        monkeypatch.setattr(config, "BOT_CONFIG", {"AI_SETTINGS": settings})
        result = get_prediction("offline", role="veto")
        method, url, kwargs = transport.calls[index]
        assert method == "POST" and url == "https://api.deepseek.com/chat/completions"
        assert kwargs["json"]["reasoning_effort"] == ("max" if enabled else "none")
        assert "HTTP-Referer" not in kwargs["headers"] and "X-Title" not in kwargs["headers"]
        if index == 2:
            assert result["action"] == "hold" and result["reason"] == "Ошибка API: missing_final_content"
        else:
            assert isinstance(result, str)


def test_deepseek_models_and_paid_probe_use_root_without_hidden_thinking(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek-key")
    settings = resolve_ai_role(normalize_ai_settings({"provider": "deepseek", "providers": {"deepseek": {"thinking": {"enabled": True}}}}))
    transport = FakeTransport(FakeResponse({"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]}), completion("OK"))
    assert client.list_models(settings, transport) == [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]
    assert transport.calls[0][0:2] == ("GET", "https://api.deepseek.com/models")
    assert client.probe_provider(settings, True, transport)["ok"]
    payload = transport.calls[1][2]["json"]
    assert payload["max_tokens"] == 16 and payload["thinking"] == {"type": "disabled"}
    assert payload["reasoning_effort"] == "none" and "reasoning" not in payload
