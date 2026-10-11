"""Параллельное прогнозирование через fake HTTP и настоящие свежие snapshots."""

import json
import threading

from src.ai import client as ai_client
from src.core.predict import main
from src.core.data.reader import read_strategy_data, market_data_context, validate_analysis_market_data
from src.exchanges import ws_provider_factory as factory
from market_data_fixtures import FixedClock, runtime_config, publish_instance
from test_ai_runtime import completion


def test_parallel_execution(tmp_path, monkeypatch, request):
    clock = FixedClock()
    monkeypatch.setattr("src.exchanges.market_data.time.time", clock)
    previous_cache = factory.get_shared_cache()
    factory.set_shared_cache(None, None)
    request.addfinalizer(lambda: factory.set_shared_cache(*previous_cache))
    config = runtime_config(tmp_path, instance="parallel-predict")
    config.update(ENABLE_PARALLEL_PROCESSING=True, ENABLE_AI_SKIP_ON_RSI=False,
                  AI_SETTINGS={"provider": "lmstudio", "providers": {"lmstudio": {"model": "offline/model"}}})
    analyses = []
    for symbol, price in (("BTCUSDT", 90000), ("ETHUSDT", 3000), ("SOLUSDT", 100)):
        publish_instance(config, symbol, clock, close=price)
        view = read_strategy_data(symbol, config)
        analysis = {"symbol": symbol, "rsi": 70, "current_price": view["candles"][-1]["closePrice"],
                    "sma": price, "prompt": f"offline {symbol}", "market_data": market_data_context(view)}
        assert validate_analysis_market_data(analysis, config)
        analyses.append(analysis)

    # Последовательный runtime не сможет пройти barrier: проверяем перекрытие
    # реальных transport-вызовов, а не нестабильный лимит времени на CI.
    barrier = threading.Barrier(3, timeout=5)
    lock = threading.Lock()
    calls = []

    def fake_request(method, url, **kwargs):
        assert method == "POST" and url.endswith("/chat/completions")
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["json"]["model"] == "offline/model"
        with lock:
            calls.append(kwargs["json"]["messages"][0]["content"])
        barrier.wait()
        return completion(json.dumps({"action": "hold", "confidence": 0.5, "reason": "Test"}))

    monkeypatch.setattr(ai_client.requests, "request", fake_request)
    predictions = main(analyses, config=config)
    assert len(predictions) == 3
    assert set(calls) == {analysis["prompt"] for analysis in analyses}
    assert {prediction["symbol"] for prediction in predictions} == {analysis["symbol"] for analysis in analyses}
    for prediction in predictions:
        assert prediction["action"] == "hold" and prediction["reason"] == "Test"
        assert prediction["market_data"]["instance_id"] == "parallel-predict"
        assert validate_analysis_market_data(prediction, config)
