"""Изолированный транспорт: без redirects, сырых ошибок и скрытых completion-probe."""

import json
import time

import requests

from src.ai_settings import PROVIDERS, api_key
from src.utils.logger import warning

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class AIProviderError(ValueError):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def request_headers(provider):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = api_key(provider)
    if PROVIDERS[provider]["requires_api_key"] and not key:
        raise AIProviderError("missing_api_key", "Ключ API провайдера не задан")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if provider == "openrouter":
        headers.update({"HTTP-Referer": "https://github.com/xierongchuan/ServoEngine", "X-Title": "Servo Engine"})
    return headers


def completion_payload(settings, prompt, use_fallbacks=True):
    if not settings["model"]:
        raise AIProviderError("missing_model", "Модель провайдера не выбрана")
    payload = {"model": settings["model"], "messages": [{"role": "user", "content": prompt}],
               "max_tokens": settings["max_tokens"], "temperature": settings["temperature"], "stream": False}
    if settings["provider"] == "openrouter":
        if settings["reasoning"]["enabled"]:
            payload["reasoning"] = {key: value for key, value in settings["reasoning"].items() if key != "enabled"}
        payload["provider"] = settings["provider_routing"]
        if use_fallbacks and settings["fallback_models"]:
            payload["models"] = [payload.pop("model")] + settings["fallback_models"]
    elif settings["provider"] == "deepseek":
        thinking = settings["thinking"]
        payload["thinking"] = {"type": "enabled" if thinking["enabled"] else "disabled"}
        payload["reasoning_effort"] = thinking["effort"] if thinking["enabled"] else "none"
    # Общие reasoning/routing-поля OpenRouter не отправляются другим провайдерам.
    return payload


def request_json(settings, endpoint, payload=None, transport=None, timeout=None):
    transport = transport or requests
    method = "GET" if payload is None else "POST"
    deadline = time.monotonic() + (timeout or settings["request_timeout"])
    response = None
    try:
        response = transport.request(method, settings["base_url"].rstrip("/") + "/" + endpoint,
                                     headers=request_headers(settings["provider"]), json=payload,
                                     timeout=(min(5, timeout or settings["request_timeout"]), timeout or settings["request_timeout"]),
                                     allow_redirects=False, stream=True)
        status = response.status_code
        if 300 <= status < 400:
            raise AIProviderError("redirect_blocked", "Перенаправление AI API запрещено")
        if status != 200:
            raise AIProviderError(f"http_{status}", f"AI API вернул HTTP {status}", status in RETRYABLE_STATUS_CODES)
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                raise AIProviderError("response_limit", "Превышен лимит ответа AI API")
            chunks.append(chunk)
        data = json.loads(b"".join(chunks))
        if not isinstance(data, dict) or "error" in data:
            raise AIProviderError("invalid_response", "Некорректный ответ AI API")
        return data
    except AIProviderError:
        raise
    except requests.exceptions.Timeout:
        raise AIProviderError("timeout", "Таймаут AI API", True) from None
    except requests.exceptions.ConnectionError:
        raise AIProviderError("connection_error", "Ошибка соединения с AI API", True) from None
    except (requests.exceptions.RequestException, ValueError, TypeError):
        raise AIProviderError("invalid_response", "Некорректный ответ AI API") from None
    finally:
        if response is not None:
            response.close()


def final_content(data):
    try:
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content")
        if (choice.get("finish_reason") not in {None, "stop"}
                or message.get("role", "assistant") != "assistant" or message.get("tool_calls")
                or message.get("refusal") or not isinstance(content, str) or not content.strip()):
            raise ValueError()
        # Внутренний reasoning никогда не становится торговым ответом.
        if "<think>" in content.lower() or "<analysis>" in content.lower():
            raise ValueError()
        return content
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise AIProviderError("missing_final_content", "Нет завершённого финального ответа AI") from None


def complete(settings, prompt, transport=None, sleep=time.sleep, use_fallbacks=True):
    payload = completion_payload(settings, prompt, use_fallbacks)
    for attempt in range(settings["retry_count"] + 1):
        try:
            return final_content(request_json(settings, "chat/completions", payload, transport))
        except AIProviderError as exc:
            warning(f"⚠️ Запрос AI: провайдер={settings['provider']}, код={exc.code}, попытка={attempt + 1}", category="llm")
            if not exc.retryable or attempt >= settings["retry_count"]:
                raise
            sleep(min(60, settings["retry_backoff_base"] ** (attempt + 1)))


def list_models(settings, transport=None, timeout=10):
    data = request_json(settings, "models", transport=transport, timeout=timeout)
    models = data.get("data")
    if not isinstance(models, list):
        raise AIProviderError("invalid_models", "Некорректный список моделей AI")
    from src.ai_settings import _model, reject_secrets, AISettingsError
    result = []
    for item in models[:2000]:
        if not isinstance(item, dict):
            continue
        model = item.get("id")
        try:
            _model(model, "model")
            reject_secrets(model)
        except AISettingsError:
            continue
        result.append({"id": model})
    return result


def probe_provider(settings, allow_paid_completion=False, transport=None):
    start = time.monotonic()
    result = {"provider": settings["provider"], "ok": False, "model": settings["model"]}
    try:
        if allow_paid_completion:
            # Только явный opt-in; не используем торговый prompt, reasoning или fallbacks.
            payload = {"model": settings["model"], "messages": [{"role": "user", "content": "Reply OK."}],
                       "max_tokens": 16, "temperature": 0, "stream": False}
            if not settings["model"]:
                raise AIProviderError("missing_model", "Модель провайдера не выбрана")
            if settings["provider"] == "deepseek":
                # Probe остаётся минимальным даже при включённом thinking профиля.
                payload.update(thinking={"type": "disabled"}, reasoning_effort="none")
            final_content(request_json(settings, "chat/completions", payload, transport, timeout=10))
            result.update(ok=True, message="Минимальный completion выполнен")
        else:
            models = list_models(settings, transport)
            found = any(item["id"] == settings["model"] for item in models)
            result.update(ok=True, message="API доступен; модель найдена" if found else "API доступен; выбранная модель отсутствует в списке")
    except AIProviderError as exc:
        result.update(message=str(exc), error_code=exc.code)
    result["latency_ms"] = round((time.monotonic() - start) * 1000)
    return result
