"""Миграция и строгая валидация AI без обращения к провайдерам."""

from copy import deepcopy
import pytest

from src.ai_settings import (AISettingsError, DEFAULT_AI_SETTINGS, guard_config_ai,
                             normalize_ai_settings, resolve_ai_role)
from src.config_loader import deep_merge


def test_legacy_migration_preserves_explicit_provider_profile():
    raw = {"provider": "deepseek", "model": "legacy-model", "base_url": "https://old.example/v1/chat/completions",
           "providers": {"deepseek": {"base_url": "https://custom.example/api/v1", "model": "custom-model"}}}
    original = deepcopy(raw)
    settings = normalize_ai_settings(raw)
    assert settings["providers"]["deepseek"] == {"base_url": "https://custom.example/api/v1", "model": "custom-model",
                                                      "thinking": {"enabled": False, "effort": "high"}}
    assert "model" not in settings and "base_url" not in settings
    assert raw == original


def test_legacy_model_inherits_non_openrouter_provider():
    base = {"ai": {"provider": "deepseek", "providers": {"deepseek": {"base_url": "https://custom.example/v1", "model": "base-model"}}}}
    result = deep_merge(base, {"ai": {"model": "profile-model"}})
    settings = normalize_ai_settings(result["ai"])
    assert settings["providers"]["deepseek"] == {"base_url": "https://custom.example/v1", "model": "profile-model",
                                                      "thinking": {"enabled": False, "effort": "high"}}
    assert settings["provider"] == "deepseek"


def test_legacy_completion_url_migration():
    settings = normalize_ai_settings({"base_url": "https://custom.example/api/v1/chat/completions/", "model": "custom/model"})
    assert settings["providers"]["openrouter"]["base_url"] == "https://custom.example/api/v1"
    assert settings["providers"]["openrouter"]["model"] == "custom/model"


def test_layer_migration_preserves_profile_override_and_custom_endpoint():
    base = {"ai": {"model": "base/model", "base_url": "https://custom.example/v1/chat/completions"}}
    strategy = {"ai": {"model": "strategy/model"}}
    profile = {"ai": {"providers": {"openrouter": {"model": "profile/model"}}, "overrides": {"veto": {"provider": "deepseek"}}}}
    result = normalize_ai_settings(deep_merge(deep_merge(base, strategy), profile)["ai"])
    assert result["providers"]["openrouter"] == {"base_url": "https://custom.example/v1", "model": "profile/model"}
    assert resolve_ai_role(result, "veto")["provider"] == "deepseek"
    assert resolve_ai_role(result, "veto")["model"] == "deepseek-flash"
    assert resolve_ai_role(result, "regime")["model"] == "profile/model"


@pytest.mark.parametrize("value", [
    [], None, {"provider": "unknown"}, {"provider": []},
    {"temperature": True}, {"temperature": float("nan")}, {"temperature": float("inf")},
    {"max_tokens": 0}, {"max_tokens": 1.5}, {"retry_count": -1}, {"retry_count": 6},
    {"request_timeout": 0}, {"request_timeout": 301}, {"retry_backoff_base": 11},
    {"providers": []}, {"providers": {"unknown": {}}},
    {"providers": {"deepseek": {"base_url": "http://api.deepseek.com/v1"}}},
    {"providers": {"lmstudio": {"base_url": "http://user:private@localhost:1234/v1"}}},
    {"providers": {"lmstudio": {"base_url": "file:///etc/passwd"}}},
    {"providers": {"lmstudio": {"base_url": "http://localhost/v1?api_key=private"}}},
    {"providers": {"lmstudio": {"base_url": "http://localhost/v1#private"}}},
    {"providers": {"lmstudio": {"base_url": "http://localhost:wrong/v1"}}},
    {"providers": {"openrouter": {"model": ""}}}, {"model": "contains spaces"},
    {"reasoning": {"enabled": 1}}, {"reasoning": {"effort": []}}, {"reasoning": {"unknown": True}},
    {"provider_routing": {"allow_fallbacks": "yes"}}, {"fallback_models": "model"},
    {"overrides": {"veto": {"provider": []}}}, {"overrides": {"regime": {"max_tokens": True}}},
    {"api_key": "private"}, {"providers": {"deepseek": {"api_key": "private"}}},
])
def test_invalid_partial_ai_rejected(value):
    with pytest.raises(AISettingsError):
        guard_config_ai({"ai": value})


def test_secret_values_rejected_even_in_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret-test")
    with pytest.raises(AISettingsError) as exc:
        normalize_ai_settings({"model": "synthetic-secret-test"})
    assert "synthetic-secret-test" not in str(exc.value)


def test_roles_select_own_provider_model_and_sampling():
    settings = deepcopy(DEFAULT_AI_SETTINGS)
    settings["providers"]["lmstudio"]["model"] = "local/model"
    settings["overrides"]["regime"].update(provider="lmstudio", model="local/role", temperature=0.4)
    regime = resolve_ai_role(settings, "regime")
    assert regime["provider"] == "lmstudio" and regime["model"] == "local/role"
    assert regime["base_url"] == "http://host.containers.internal:1234/v1"
    assert regime["max_tokens"] == 150 and regime["temperature"] == 0.4
    assert resolve_ai_role(settings, "veto")["model"] == settings["providers"]["openrouter"]["model"]
