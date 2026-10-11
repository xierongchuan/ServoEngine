"""Ошибка JSON в финальном content повторяет анализ без обхода market-data guards."""

import json
from unittest.mock import Mock

from src.ai import client as ai_client
from src.core.predict import process_analysis
from src.core.data.reader import read_strategy_data, market_data_context, validate_analysis_market_data
from src.exchanges import ws_provider_factory as factory
from market_data_fixtures import FixedClock, runtime_config, publish_instance
from test_ai_runtime import FakeTransport, completion


def test_retry_logic(tmp_path, monkeypatch, request):
    clock = FixedClock()
    monkeypatch.setattr("src.exchanges.market_data.time.time", clock)
    previous_cache = factory.get_shared_cache()
    factory.set_shared_cache(None, None)
    request.addfinalizer(lambda: factory.set_shared_cache(*previous_cache))
    config = runtime_config(tmp_path, instance="retry-predict")
    config.update(ENABLE_AI_SKIP_ON_RSI=False,
                  AI_SETTINGS={"provider": "lmstudio", "providers": {"lmstudio": {"model": "offline/model"}}})
    publish_instance(config, "BTCUSDT", clock, close=90000)
    view = read_strategy_data("BTCUSDT", config)
    analysis = {"symbol": "BTCUSDT", "rsi": 70, "current_price": view["candles"][-1]["closePrice"],
                "sma": 89000, "prompt": "offline retry", "market_data": market_data_context(view)}
    assert validate_analysis_market_data(analysis, config)
    responses = [completion("Invalid JSON"),
                 completion(json.dumps({"action": "hold", "confidence": 0.5, "reason": "Test Success"}))]
    transport = FakeTransport(*responses)
    sleep = Mock()
    monkeypatch.setattr(ai_client.requests, "request", transport.request)
    monkeypatch.setattr("src.core.predict.time.sleep", sleep)
    result = process_analysis(analysis, config=config)
    assert result["action"] == "hold" and result["reason"] == "Test Success"
    assert validate_analysis_market_data(result, config)
    assert len(transport.calls) == 2
    assert all(response.closed for response in responses)
    sleep.assert_called_once_with(1)
    for method, url, kwargs in transport.calls:
        assert method == "POST" and url.endswith("/chat/completions")
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "offline retry"}]
