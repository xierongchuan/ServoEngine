from src.exchanges.config.bingx_config import BingXConfig
from src.exchanges.dto.models import OrderSide, OrderType, PositionSide
from src.exchanges.impl.bingx_client import BingXClient


class _FakeResponse:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "code": 0,
            "data": [{
                "time": 1781779200000,
                "open": "1",
                "high": "2",
                "low": "0.5",
                "close": "1.5",
                "volume": "100",
            }],
        }


def test_market_data_uses_public_market_url_in_demo(monkeypatch):
    calls = []
    monkeypatch.setenv("BINGX_MARKET_API_URL", "https://market.example.test")

    def fake_get(url, params=None, timeout=6):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.get", fake_get)

    client = BingXClient(BingXConfig(is_demo=True))
    result = client.get_kline_data("BTCUSDT", interval="5m", limit=1)

    assert result[0]["closePrice"] == 1.5
    assert calls[0]["url"] == "https://market.example.test/openApi/swap/v3/quote/klines"
    assert calls[0]["params"]["symbol"] == "BTC-USDT"
    assert calls[0]["params"]["interval"] == "5m"


def test_market_data_keeps_rest_intervals_compact(monkeypatch):
    calls = []
    monkeypatch.setenv("BINGX_MARKET_API_URL", "https://market.example.test")

    def fake_get(url, params=None, timeout=6):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.get", fake_get)

    client = BingXClient(BingXConfig(is_demo=True))
    client.get_kline_data("BTCUSDT", interval="15m", limit=1)
    client.get_kline_data("BTCUSDT", interval="1h", limit=1)

    assert [call["params"]["interval"] for call in calls] == ["15m", "1h"]


def test_market_data_maps_verbose_intervals_to_compact(monkeypatch):
    calls = []
    monkeypatch.setenv("BINGX_MARKET_API_URL", "https://market.example.test")

    def fake_get(url, params=None, timeout=6):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.get", fake_get)

    client = BingXClient(BingXConfig(is_demo=True))
    client.get_kline_data("BTCUSDT", interval="MINUTE_5", limit=1)
    client.get_kline_data("BTCUSDT", interval="HOUR_1", limit=1)
    client.get_kline_data("BTCUSDT", interval="DAY_1", limit=1)

    assert [call["params"]["interval"] for call in calls] == ["5m", "1h", "1d"]


def test_config_intervals_expose_public_compact_values_only():
    intervals = BingXConfig(is_demo=True).to_dict()["intervals"]

    assert "5m" in intervals
    assert "15m" in intervals
    assert "1h" in intervals
    assert "MINUTE_5" not in intervals
    assert "HOUR_1" not in intervals


def test_place_order_uses_explicit_runtime_leverage(monkeypatch):
    client = BingXClient(BingXConfig(is_demo=True))
    leverage_calls = []

    def fake_set_leverage(symbol, leverage, position_side):
        leverage_calls.append((symbol, leverage, position_side))
        return True

    monkeypatch.setattr(client, "set_leverage", fake_set_leverage)
    monkeypatch.setattr(
        client,
        "_make_request",
        lambda method, endpoint, params: {"code": 0, "data": {"orderId": "order-1"}},
    )

    order_id = client.place_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        order_type=OrderType.MARKET,
        leverage=5,
    )

    assert order_id == "order-1"
    assert leverage_calls == [("BTCUSDT", 5, PositionSide.LONG)]


def test_market_data_logs_bingx_api_error(monkeypatch, caplog):
    class ErrorResponse:
        status_code = 200
        headers = {}
        text = '{"code":109400,"msg":"interval: invalid","data":{}}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 109400, "msg": "interval: invalid", "data": {}}

    monkeypatch.setenv("BINGX_MARKET_API_URL", "https://market.example.test")
    monkeypatch.setattr(
        "src.exchanges.impl.bingx_client.requests.get",
        lambda url, params=None, timeout=6: ErrorResponse(),
    )

    client = BingXClient(BingXConfig(is_demo=True))

    with caplog.at_level("WARNING", logger="src.exchanges.impl.bingx_client"):
        result = client.get_kline_data("BTCUSDT", interval="5m", limit=1)

    assert result == []
    assert "BingX klines API error" in caplog.text
    assert "code=109400" in caplog.text
    assert "symbol=BTC-USDT" in caplog.text
