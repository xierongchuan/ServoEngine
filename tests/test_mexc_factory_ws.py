"""Factory/config/WebSocket проверки интеграции MEXC."""

from unittest.mock import patch

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


def test_factory_singleton_is_keyed_by_market(monkeypatch):
    from src.exchanges import exchange_factory
    exchange_factory.reset_client()
    with patch.object(exchange_factory, "_selection", side_effect=[
        ("mexc", "spot", False), ("mexc", "spot", False),
        ("mexc", "perpetual", False),
    ]), patch.object(exchange_factory, "_create_client", side_effect=[object(), object()]) as create:
        spot_one = exchange_factory.get_trading_client()
        spot_two = exchange_factory.get_trading_client()
        perpetual = exchange_factory.get_trading_client()
    assert spot_one is spot_two
    assert spot_one is not perpetual
    assert create.call_count == 2


def test_factory_returns_correct_client_types(monkeypatch):
    monkeypatch.setenv("MEXC_ENABLE_LIVE_TRADING", "false")
    from src.exchanges.exchange_factory import _create_client
    assert isinstance(_create_client("mexc", "spot", False), MEXCSpotClient)
    assert isinstance(_create_client("mexc", "perpetual", False), MEXCFuturesClient)


def test_ws_kline_updates_existing_candle():
    provider = MEXCFuturesWebSocketDataProvider()
    provider._cache = {"BTCUSDT": [{"timestamp": 1700000000000, "closePrice": 1}]}
    provider._ready = {}
    provider._on_message(None, '{"channel":"push.kline","data":{'
                               '"symbol":"BTC_USDT","t":1700000000,'
                               '"o":"10","h":"12","l":"9","c":"11","v":"2"}}')
    assert len(provider._cache["BTCUSDT"]) == 1
    assert provider._cache["BTCUSDT"][0]["closePrice"] == 11


def test_ws_subscription_disables_gzip_and_uses_mexc_symbol():
    provider = MEXCFuturesWebSocketDataProvider()
    provider._symbols = ["BTCUSDT"]
    provider._interval = "5m"
    provider._running = False
    sent = []
    ws = type("WS", (), {"send": lambda self, value: sent.append(value)})()
    provider._on_open(ws)
    assert '"method": "sub.kline"' in sent[0]
    assert '"symbol": "BTC_USDT"' in sent[0]
    assert '"gzip": false' in sent[0]
