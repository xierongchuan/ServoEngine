"""Opt-in GET /models для сохранённых AI-профилей; никаких prompts/completions."""

import argparse
import json
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


def main(argv=None):
    parser = argparse.ArgumentParser(description="GET-only AI probe; платные генерации запрещены")
    parser.add_argument("--confirm-get-only", action="store_true", required=True)
    parser.parse_args(argv)
    import requests
    from src.ai.client import AIProviderError, list_models
    from src.ai_settings import PROVIDERS, api_key, normalize_ai_settings, resolve_ai_role

    settings = normalize_ai_settings(json.loads(Path("config/base.json").read_text(encoding="utf-8"))["ai"])
    endpoints = {profile["base_url"].rstrip("/") + "/models" for profile in settings["providers"].values()}
    original = requests.sessions.Session.request
    reads, results = [], []
    deadline = time.monotonic() + 45

    def guarded(session, method, url, **kwargs):
        parsed = urlsplit(url)
        if (str(method).upper() != "GET" or url not in endpoints or parsed.username or parsed.password
                or parsed.query or parsed.fragment or kwargs.get("allow_redirects") is not False
                or time.monotonic() >= deadline or len(reads) >= 3):
            raise RuntimeError("AI probe: разрешён только bounded GET /models сохранённого профиля")
        reads.append(url)
        return original(session, method, url, **kwargs)

    with patch.object(requests.sessions.Session, "request", guarded):
        for provider in PROVIDERS:
            selected = resolve_ai_role(dict(settings, provider=provider))
            result = {"provider": provider, "has_api_key": bool(api_key(provider))}
            if PROVIDERS[provider]["requires_api_key"] and not result["has_api_key"]:
                result.update(status="skipped", reason="Нет ключа в окружении")
            else:
                start = time.monotonic()
                try:
                    models = list_models(selected, timeout=5)
                    result.update(status="ok", model_count=len(models),
                                  selected_model_found=any(m["id"] == selected["model"] for m in models))
                except AIProviderError as exc:
                    result.update(status="unavailable", error_code=exc.code)
                result["latency_ms"] = round((time.monotonic() - start) * 1000)
            results.append(result)
    print(json.dumps({"get_only": True, "completion_count": 0, "get_count": len(reads),
                      "providers": results}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
