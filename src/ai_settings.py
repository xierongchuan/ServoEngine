"""Единая схема AI, миграция старых настроек и выбор роли без секретов."""

from copy import deepcopy
import math
import os
import re
from urllib.parse import urlsplit

PROVIDERS = {
    "openrouter": {"label": "OpenRouter", "env": "OPENROUTER_API_KEY", "requires_api_key": True},
    "deepseek": {"label": "DeepSeek", "env": "DEEPSEEK_API_KEY", "requires_api_key": True},
    "lmstudio": {"label": "LM Studio", "env": "LM_STUDIO_API_KEY", "requires_api_key": False},
}
DEFAULT_AI_SETTINGS = {
    "provider": "openrouter",
    "providers": {
        "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "google/gemini-3.1-flash-lite-preview"},
        "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash",
                     "thinking": {"enabled": False, "effort": "high"}},
        "lmstudio": {"base_url": "http://host.containers.internal:1234/v1", "model": ""},
    },
    "temperature": 0.3, "max_tokens": 4096, "request_timeout": 60,
    "retry_count": 3, "retry_backoff_base": 2,
    "reasoning": {"enabled": True, "effort": "high", "exclude": True},
    "provider_routing": {"allow_fallbacks": True}, "fallback_models": [],
    "overrides": {
        "veto": {"provider": None, "model": None, "temperature": 0.1, "max_tokens": 100},
        "regime": {"provider": None, "model": None, "temperature": 0.2, "max_tokens": 150},
    },
}
_SECRET_FIELD = re.compile(r"secret|password|passwd|api.?key|authorization|token(?!s$)|private.?key|credential", re.I)
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@+\-]{0,255}$")


class AISettingsError(ValueError):
    """Сообщения содержат только фиксированные имена полей, никогда значения."""


def reject_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or _SECRET_FIELD.search(key):
                raise AISettingsError("Секреты в конфигурации запрещены; используйте переменные окружения")
            reject_secrets(key)
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)
    elif isinstance(value, str):
        for name, secret in os.environ.copy().items():
            if secret and _SECRET_FIELD.search(name) and secret in value:
                raise AISettingsError("Секреты в конфигурации запрещены; используйте переменные окружения")
        if re.search(r"\bBearer\s+|-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{8,}", value):
            raise AISettingsError("Секреты в конфигурации запрещены; используйте переменные окружения")


def _object(value, allowed, path):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise AISettingsError(f"{path}: ожидается объект с допустимыми полями")


def _number(value, low, high, path, integer=False):
    valid = type(value) is int if integer else type(value) in (int, float)
    if not valid or not low <= value <= high or not math.isfinite(value):
        raise AISettingsError(f"{path}: значение вне допустимого диапазона {low}–{high}")


def _model(value, path, empty=False, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not ((empty and value == "") or _MODEL.fullmatch(value)):
        raise AISettingsError(f"{path}: недопустимый идентификатор модели")


def _url(value, provider):
    path = "ai.providers.base_url"
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise AISettingsError(f"{path}: недопустимый URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or (provider != "lmstudio" and parsed.scheme != "https")
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
    except ValueError:
        raise AISettingsError(f"{path}: нужен HTTP(S) URL без credentials, query и fragment") from None
    return value.rstrip("/").removesuffix("/chat/completions")


def _deepseek_thinking(value):
    _object(value, {"enabled", "effort"}, "ai.providers.deepseek.thinking")
    result = deepcopy(value)
    if "enabled" in result and type(result["enabled"]) is not bool:
        raise AISettingsError("ai.providers.deepseek.thinking.enabled: ожидается boolean")
    if "effort" in result:
        effort = result["effort"]
        if not isinstance(effort, str) or effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise AISettingsError("ai.providers.deepseek.thinking.effort: недопустимый уровень")
        # Совместимые effort aliases относятся только к профилю DeepSeek.
        if effort == "none":
            result.update(enabled=False, effort="high")
        else:
            result["effort"] = {"minimal": "low", "medium": "high", "xhigh": "high"}.get(effort, effort)
    return result


def migrate_ai_settings(value, default_provider="openrouter"):
    """Мигрирует только явно заданные поля слоя: не заполняет defaults профиля."""
    _object(value, set(DEFAULT_AI_SETTINGS) | {"model", "base_url"}, "ai")
    reject_secrets(value)
    result = deepcopy(value)
    provider = result.get("provider", default_provider)
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise AISettingsError("ai.provider: неизвестный провайдер")
    profiles = result.setdefault("providers", {})
    _object(profiles, PROVIDERS, "ai.providers")
    if "model" in result or "base_url" in result:
        selected = profiles.setdefault(provider, {})
        _object(selected, {"model", "base_url", "thinking"} if provider == "deepseek" else {"model", "base_url"}, "ai.providers")
        for key in ("model", "base_url"):
            if key in result:
                selected.setdefault(key, result.pop(key))
    for name, profile in profiles.items():
        _object(profile, {"model", "base_url", "thinking"} if name == "deepseek" else {"model", "base_url"}, "ai.providers")
        if "thinking" in profile:
            profile["thinking"] = _deepseek_thinking(profile["thinking"])
        if "base_url" in profile:
            profile["base_url"] = _url(profile["base_url"], name)
        if "model" in profile:
            _model(profile["model"], "ai.providers.model", empty=name == "lmstudio")
    for key, bounds in {
        "temperature": (0, 2, False), "max_tokens": (1, 1000000, True),
        "request_timeout": (1, 300, False), "retry_count": (0, 5, True),
        "retry_backoff_base": (1, 10, False),
    }.items():
        if key in result:
            _number(result[key], *bounds[:2], f"ai.{key}", integer=bounds[2])
    if "reasoning" in result:
        reasoning = result["reasoning"]
        _object(reasoning, {"enabled", "effort", "exclude"}, "ai.reasoning")
        for key in ("enabled", "exclude"):
            if key in reasoning and type(reasoning[key]) is not bool:
                raise AISettingsError("ai.reasoning: enabled/exclude должны быть boolean")
        if "effort" in reasoning and (not isinstance(reasoning["effort"], str) or reasoning["effort"] not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}):
            raise AISettingsError("ai.reasoning.effort: недопустимый уровень")
    if "provider_routing" in result:
        routing = result["provider_routing"]
        _object(routing, {"allow_fallbacks"}, "ai.provider_routing")
        if "allow_fallbacks" in routing and type(routing["allow_fallbacks"]) is not bool:
            raise AISettingsError("ai.provider_routing.allow_fallbacks: ожидается boolean")
    if "fallback_models" in result:
        models = result["fallback_models"]
        if not isinstance(models, list) or len(models) > 20:
            raise AISettingsError("ai.fallback_models: ожидается список до 20 моделей")
        for model in models:
            _model(model, "ai.fallback_models")
    if "overrides" in result:
        _object(result["overrides"], {"veto", "regime"}, "ai.overrides")
        for override in result["overrides"].values():
            _object(override, {"provider", "model", "temperature", "max_tokens"}, "ai.overrides")
            if "provider" in override and override["provider"] is not None and (not isinstance(override["provider"], str) or override["provider"] not in PROVIDERS):
                raise AISettingsError("ai.overrides.provider: неизвестный провайдер")
            if "model" in override:
                _model(override["model"], "ai.overrides.model", nullable=True)
            if "temperature" in override:
                _number(override["temperature"], 0, 2, "ai.overrides.temperature")
            if "max_tokens" in override:
                _number(override["max_tokens"], 1, 1000000, "ai.overrides.max_tokens", integer=True)
    return result


def merge_ai_settings(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = merge_ai_settings(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


_UNSET = object()


def normalize_ai_settings(value=_UNSET):
    if value is _UNSET:
        value = {}
    return merge_ai_settings(DEFAULT_AI_SETTINGS, migrate_ai_settings(value))


def api_key(provider):
    return os.getenv(PROVIDERS[provider]["env"], "").strip()


def resolve_ai_role(settings, role=None):
    settings = normalize_ai_settings(settings)
    if role is not None and (not isinstance(role, str) or role not in settings["overrides"]):
        raise AISettingsError("ai.role: неизвестная роль")
    override = settings["overrides"][role] if role else {}
    provider = override.get("provider") or settings["provider"]
    return {**settings, **settings["providers"][provider], "provider": provider,
            "model": override.get("model") or settings["providers"][provider]["model"],
            "temperature": override.get("temperature", settings["temperature"]),
            "max_tokens": override.get("max_tokens", settings["max_tokens"])}


def provider_statuses(settings):
    settings = normalize_ai_settings(settings)
    return [{"id": name, "label": metadata["label"], "requires_api_key": metadata["requires_api_key"],
             "has_api_key": bool(api_key(name)),
             "configured": bool(settings["providers"][name]["model"]) and (bool(api_key(name)) or not metadata["requires_api_key"])}
            for name, metadata in PROVIDERS.items()]


def guard_config_ai(config):
    """Общий guard всех save-path: сохраняет частичность слоёв и отсекает секреты."""
    if not isinstance(config, dict):
        raise AISettingsError("Конфигурация должна быть объектом")
    reject_secrets(config)
    result = deepcopy(config)
    for key in ("ai", "AI_SETTINGS"):
        if key in result:
            validated = migrate_ai_settings(result[key])
            # Без явного provider legacy scalar наследует провайдера нижнего слоя.
            # Его можно мигрировать лишь при merge с base/strategy, не при guard.
            if "provider" not in result[key] and {"model", "base_url"} & result[key].keys():
                continue
            result[key] = validated
    return result
