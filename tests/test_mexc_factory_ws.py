"""Factory/config/WebSocket проверки интеграции MEXC."""

import json
from unittest.mock import Mock, patch

import pytest

from src.exchanges.market_data import StreamKey
from market_data_fixtures import FixedClock, candle_rows

from src.exchanges.config.base import ConfigFactory
from src.exchanges.impl.mexc_futures_client import MEXCFuturesClient
from src.exchanges.impl.mexc_spot_client import MEXCSpotClient
from src.exchanges.mexc_futures_ws_data_provider import MEXCFuturesWebSocketDataProvider


def test_config_factory_creates_both_mexc_products(monkeypatch):
    monkeypatch.setenv("MEXC_API_KEY", "key")
    monkeypatch.setenv("MEXC_SECRET_KEY", "secret")
    futures = ConfigFactory.create("mexc", market_type="perpetual")
    spot = ConfigFactory.create("mexc", market_type="spot")
    assert futures.ws_url == "wss://contract.mexc.com/edge"
    assert spot.supported_intervals["1h"] == "60m"
    assert futures.api_key == "key"
    assert "secret_key" not in futures.to_dict()


def test_factory_singleton_is_keyed_by_market(monkeypatch, tmp_path):
    from src.exchanges import exchange_factory
    exchange_factory.reset_client()
    settings = {"exchange": "mexc", "execution_mode": "read_only"}
    runtime = {"DATA_DIR": str(tmp_path), "STRATEGY_INSTANCE_ID": "factory-test"}
    try:
        with patch.object(exchange_factory, "_create_client", side_effect=[object(), object()]) as create:
            spot_one = exchange_factory.get_trading_client(settings={**settings, "market_type": "spot"}, runtime_config=runtime)
            spot_two = exchange_factory.get_trading_client(settings={**settings, "market_type": "spot"}, runtime_config=runtime)
            perpetual = exchange_factory.get_trading_client(settings={**settings, "market_type": "perpetual"}, runtime_config=runtime)
        assert spot_one is spot_two
        assert spot_one is not perpetual
        assert create.call_count == 2
    finally:
        exchange_factory.reset_client()


def test_factory_returns_correct_client_types(monkeypatch):
    monkeypatch.setenv("MEXC_ENABLE_LIVE_TRADING", "false")
    from src.exchanges.exchange_factory import _create_client
    assert isinstance(_create_client("mexc", "spot", False), MEXCSpotClient)
    assert isinstance(_create_client("mexc", "perpetual", False), MEXCFuturesClient)


@pytest.fixture
def ws_provider(monkeypatch):
    clock = FixedClock()
    monkeypatch.setattr("src.exchanges.market_data.time.time", clock)
    key = StreamKey("mexc", "perpetual", "BTCUSDT", "5m")
    rows = candle_rows(key, clock, 1, close=1)
    provider = MEXCFuturesWebSocketDataProvider(
        settings={"history_limit": 3, "min_history": 1}, clock=clock, monotonic=clock,
        rest_fetcher=Mock(return_value=rows), publisher=Mock())
    provider.sync_subscriptions([key])
    ws = Mock()
    provider._ws = ws
    provider._on_open(ws)
    provider.reconcile_once(key)
    try:
        yield provider, ws, key, clock
    finally:
        provider.stop()


def test_ws_kline_updates_existing_candle(ws_provider):
    provider, ws, key, clock = ws_provider
    before = provider.store.snapshot(key)
    provider._on_message(ws, json.dumps({"channel": "push.kline", "ts": int(clock() * 1000), "data": {
        "symbol": "BTC_USDT", "interval": "Min5", "t": before["candles"][0]["timestamp"] // 1000,
        "o": "10", "h": "12", "l": "9", "c": "11", "v": "2"}}))
    value = provider.store.snapshot(key)
    assert len(value["candles"]) == 1
    assert value["candles"][0]["closePrice"] == 11
    assert value["stream"] == key.to_dict()
    assert value["ready"] and value["subscribed"]
    assert value["candles"][0]["source"] == "ws"
    assert not value["candles"][0]["is_closed"]


def test_ws_subscription_disables_gzip_and_uses_mexc_symbol(ws_provider):
    provider, ws, key, _ = ws_provider
    request = json.loads(ws.send.call_args_list[0].args[0])
    assert request["method"] == "sub.kline"
    assert request["param"]["symbol"] == "BTC_USDT"
    assert request["gzip"] is False
    assert request["param"]["interval"] == "Min5"
    assert provider.streams == [key]
