"""Все четыре scalp caller используют explicit role и новый instance snapshot."""

from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest

from src.ai import client
from src.ai_settings import DEFAULT_AI_SETTINGS
from src import config
from src.core import predict
from src.core.scalp_engine import ScalpEngine
from src.core.strategies.scalp.veto import ScalpVetoProcessor
from test_ai_runtime import FakeTransport, completion


@pytest.mark.parametrize("implementation", ["engine", "processor"])
@pytest.mark.parametrize("role", ["veto", "regime"])
def test_scalp_caller_uses_explicit_role_and_current_instance_config(implementation, role, monkeypatch):
    analyzer = SimpleNamespace(_bootstrapped=True, _recent_closes=[100, 101, 102],
                               get_snapshot=lambda: {"ema_fast": 102, "ema_med": 101, "rsi": 50})
    lock = threading.Lock()
    signal = {"signal": "BUY", "score": 3, "max_score": 5, "quality": 0.6}
    pending = {"time": time.time(), "cycle": 1, "signal": signal, "indicators": {}}
    if implementation == "engine":
        caller = ScalpEngine.__new__(ScalpEngine)
        caller.symbol = "BTCUSDT"
        caller._lock = lock
        caller._analyzer = analyzer if role == "regime" else None
        caller._regime = {"regime": "RANGING"}
        caller._fast_cycle = 1
        caller._position = None
        caller._veto_staleness_sec = 10
        caller._veto_max_cycles = 2
        caller._regime_interval_sec = 0
    else:
        # Здесь config — strategy subsection, не весь resolved instance.
        caller = ScalpVetoProcessor("BTCUSDT", {"ai_integration": {"regime_interval_seconds": 0}}, None)
    caller._ai_regime_label = "UNKNOWN"
    caller._ai_regime_duration = 0
    # Старые поля не должны влиять ни на один запрос.
    caller._veto_model = caller._regime_model = "stale/model"
    caller._veto_temperature = caller._regime_temperature = 1.9
    caller._veto_max_tokens = caller._regime_max_tokens = 1
    response = '{"action":"hold","confidence":0}' if role == "veto" else '{"regime":"RANGING","confidence":0.1}'
    transport = FakeTransport(completion(response), completion(response))
    monkeypatch.setattr(client.requests, "request", transport.request)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek-key")
    monkeypatch.setenv("LM_STUDIO_API_KEY", "synthetic-lmstudio-key")
    original_prediction = predict.get_prediction
    calls = []

    def spy(prompt, **kwargs):
        calls.append(kwargs)
        return original_prediction(prompt, **kwargs)

    monkeypatch.setattr(predict, "get_prediction", spy)
    for index, provider in enumerate(("deepseek", "lmstudio")):
        settings = deepcopy(DEFAULT_AI_SETTINGS)
        settings["providers"][provider] = {"base_url": f"https://instance-{index}.example/v1", "model": f"current/model-{index}"}
        settings["overrides"][role].update(provider=provider, temperature=0.4 + index / 10, max_tokens=234 + index)
        current = {"AI_SETTINGS": settings, "STRATEGY_INSTANCE_ID": "btc-scalp"}
        monkeypatch.setattr(config, "BOT_CONFIG", current)
        caller._last_ai_regime_time = 0
        if implementation == "engine":
            if role == "veto":
                caller._pending_veto = pending
                caller._process_veto()
            else:
                caller._update_regime_ai()
        elif role == "veto":
            caller.process_veto(pending, 1, None, lock, {}, 0, None,
                                lambda *args, **kwargs: pytest.fail("Сделка запрещена в AI-тесте"))
        else:
            caller.update_regime_ai(analyzer, lock, {})
        assert len(calls) == index + 1
        assert calls[-1] == {"role": role, "config": current}
        assert calls[-1]["config"] is current
        _, url, kwargs = transport.calls[-1]
        assert url == f"https://instance-{index}.example/v1/chat/completions"
        assert kwargs["json"]["model"] == f"current/model-{index}"
        assert kwargs["json"]["max_tokens"] == 234 + index
        assert kwargs["json"]["temperature"] == 0.4 + index / 10
