"""BingX unit: словарный адаптер, строгий REST и owned-only исполнение, полностью офлайн."""

import hashlib
import hmac
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from src.exchanges.bingx_client import BingXClient
from src.exchanges.config.bingx_config import BingXConfig
from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from test_bingx_safety_regressions import FakeExchange, Response

SETTINGS = {"exchange": "bingx", "execution_mode": "demo"}


@pytest.fixture
def exchange(monkeypatch):
    monkeypatch.setenv("BINGX_API_KEY", "test-api-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "test-secret-key")
    fake = FakeExchange()
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def client(exchange):
    return BingXClient(api_key="test-api-key", secret_key="test-secret-key", trading_settings=SETTINGS)


@pytest.fixture
def client_no_keys(exchange):
    return BingXClient(api_key="", secret_key="", trading_settings=SETTINGS)


def position_response(**changes):
    row = {"symbol": "BTC-USDT", "positionAmt": ".5", "availableAmt": ".5", "avgPrice": "60000",
           "positionSide": "LONG", "positionId": "pos123", "unrealizedProfit": "500", "isolated": True}
    row.update(changes)
    return {"code": 0, "data": [row]}


def signature(client, params):
    with patch("src.exchanges.impl.bingx_client.time.time", return_value=1234):
        return client._client._signed_payload("get", params)[0].rsplit("&signature=", 1)[1]


def owned_limit(client, cid="owned"):
    return client.place_order("BTCUSDT", "BUY", price=50000, quantity=".01", type="LIMIT", client_order_id=cid)


class TestFormatSymbol:
    def test_slash_usd(self, client):
        with pytest.raises(UnsupportedCapabilityError):
            client._format_symbol("BTC/USD")

    def test_plain_usdt(self, client):
        assert client._format_symbol("BTCUSDT") == "BTC-USDT"

    def test_already_formatted(self, client):
        assert client._format_symbol("BTC-USDT") == "BTC-USDT"

    def test_slash_usdt(self, client):
        assert client._format_symbol("BTC/USDT") == "BTC-USDT"

    def test_eth_slash_usd(self, client):
        with pytest.raises(UnsupportedCapabilityError):
            client._format_symbol("ETH/USD")

    def test_ethusdt(self, client):
        assert client._format_symbol("ETHUSDT") == "ETH-USDT"

    def test_sol_dash(self, client):
        assert client._format_symbol("SOL-USDT") == "SOL-USDT"

    def test_doge_plain(self, client):
        assert client._format_symbol("DOGEUSDT") == "DOGE-USDT"


class TestGetSign:
    def test_signature_correct(self, client):
        with patch("src.exchanges.impl.bingx_client.time.time", return_value=1234):
            wire, headers = client._client._signed_payload("get", {"symbol": "BTC-USDT", "timestamp": 1})
        unsigned = "recvWindow=5000&symbol=BTC-USDT&timestamp=1234000"
        expected = hmac.new(b"test-secret-key", unsigned.encode(), hashlib.sha256).hexdigest()
        assert wire == unsigned + "&signature=" + expected
        assert headers == {"X-BX-APIKEY": "test-api-key"}

    def test_signature_deterministic(self, client):
        assert signature(client, {"a": "1"}) == signature(client, {"a": "1"})

    def test_signature_changes_with_params(self, client):
        assert signature(client, {"a": "1"}) != signature(client, {"a": "2"})

    def test_signature_without_secret_raises(self, client_no_keys):
        with pytest.raises(ExchangeStateUnavailableError):
            client_no_keys.make_request("get", "/read")

    def test_params_sorted(self, client):
        assert signature(client, {"b": "2", "a": "1"}) == signature(client, {"a": "1", "b": "2"})


class TestGetOrderBook:
    def test_success(self, client):
        with patch("requests.get", return_value=Response({"bids": [["50000", "1.5"], ["49999", "2"]], "asks": [["50001", ".8"], ["50002", "1.2"]]})):
            result = client.get_order_book("BTCUSDT")
        assert len(result["bids"]) == len(result["asks"]) == 2
        assert result["bids"][0] == [50000.0, 1.5]
        assert result["asks"][0] == [50001.0, .8]

    def test_api_error_code(self, client):
        with patch("requests.get", return_value=Response({}, code=100001)) as request:
            with pytest.raises(ExchangeAPIError) as caught:
                client.get_order_book("BTCUSDT")
        assert caught.value.code == 100001 and request.call_count == 1

    def test_network_error_retries(self, client):
        with patch("requests.get", side_effect=requests.ConnectionError("офлайн")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                client.get_order_book("BTCUSDT")
        assert request.call_count == 3

    def test_limit_capped_at_100(self, client):
        """Невалидный limit отклоняется, а не молча меняется."""
        with patch("requests.get") as request:
            with pytest.raises(ExchangeStateUnavailableError):
                client.get_order_book("BTCUSDT", limit=500)
        request.assert_not_called()
        with patch("requests.get", return_value=Response({"bids": [], "asks": []})) as request:
            assert client.get_order_book("BTCUSDT", limit=100) == {"bids": [], "asks": []}
        assert request.call_args.kwargs["params"]["limit"] == 100

    def test_symbol_formatting(self, client):
        with patch("requests.get", return_value=Response({"bids": [], "asks": []})) as request:
            client.get_order_book("BTC/USDT")
        assert request.call_args.kwargs["params"]["symbol"] == "BTC-USDT"

    def test_unexpected_exception(self, client):
        with patch("requests.get", side_effect=ValueError("повреждён ответ")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                client.get_order_book("BTCUSDT")
        assert request.call_count == 3


class TestGetTicker:
    def test_success(self, client):
        with patch("requests.get", return_value=Response({"bestBidPrice": "50000", "bestAskPrice": "50001", "lastPrice": "50000.5", "volume": "12345.6"})):
            result = client.get_ticker("BTCUSDT")
        assert result["bidPrice"] == 50000 and result["askPrice"] == 50001
        assert result["lastPrice"] == 50000.5 and result["volume"] == 12345.6

    def test_api_error(self, client):
        with patch("requests.get", return_value=Response({}, code=100001)):
            with pytest.raises(ExchangeAPIError):
                client.get_ticker("BTCUSDT")

    def test_missing_fields_default_zero(self, client):
        with patch("requests.get", return_value=Response({"lastPrice": "50000.5"})):
            result = client.get_ticker("BTCUSDT")
        assert result["bidPrice"] == result["askPrice"] == result["volume"] == 0
        assert result["lastPrice"] == 50000.5

    def test_network_error_retries(self, client):
        with patch("requests.get", side_effect=requests.Timeout("офлайн")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                client.get_ticker("BTCUSDT")
        assert request.call_count == 3

    def test_none_values_handled(self, client):
        with patch("requests.get", return_value=Response({"lastPrice": "50000", "bestBidPrice": None, "volume": None})):
            with pytest.raises(ExchangeStateUnavailableError):
                client.get_ticker("BTCUSDT")


class TestCancelAllOrders:
    def test_success(self, client, exchange):
        oid = owned_limit(client)
        manual = exchange.add_order({"symbol": "BTC-USDT", "type": "LIMIT", "side": "BUY", "positionSide": "LONG", "quantity": ".01", "price": "50000", "clientOrderId": "manual"})
        assert client.cancel_all_orders("BTCUSDT") is True
        assert exchange.orders[oid]["status"] == "CANCELLED"
        assert manual["status"] == "NEW"
        assert not any("allOpenOrders" in path for _, path, _ in exchange.calls)

    def test_no_orders_to_cancel(self, client, exchange):
        assert client.cancel_all_orders("BTCUSDT") is True
        assert not exchange.mutations

    def test_api_failure(self, client, exchange):
        owned_limit(client)
        with patch("requests.delete", return_value=Response({}, code=100001)):
            with pytest.raises(ExchangeAPIError):
                client.cancel_all_orders("BTCUSDT")
        assert exchange.orders["1"]["status"] == "NEW"

    def test_none_response(self, client, exchange):
        owned_limit(client)
        with patch("requests.delete", side_effect=requests.Timeout("офлайн")) as request:
            with pytest.raises(UnknownOrderStateError):
                client.cancel_all_orders("BTCUSDT")
        assert request.call_count == 1 and exchange.orders["1"]["status"] == "NEW"

    def test_symbol_formatted(self, client, exchange):
        owned_limit(client)
        client.cancel_all_orders("BTC/USDT")
        assert exchange.mutations[-1][2]["symbol"] == "BTC-USDT"

    def test_uses_delete_method(self, client, exchange):
        owned_limit(client)
        client.cancel_all_orders("BTCUSDT")
        assert exchange.mutations[-1][0] == "delete"
        assert exchange.mutations[-1][1].endswith("/order")


class TestMakeRequest:
    def test_get_success(self, client):
        with patch("requests.get", return_value=Response("test")):
            assert client.make_request("get", "/read", {"key": "value"})["data"] == "test"

    @pytest.mark.parametrize("method", ["post", "delete"])
    def test_post_success(self, client, method):
        with patch("requests." + method, return_value=Response({})) as request:
            with pytest.raises(UnsupportedCapabilityError):
                client.make_request(method, "/test/endpoint")
        request.assert_not_called()

    def test_delete_success(self, client):
        events = []
        client._client._submission_observer = events.append
        with patch("requests.delete", return_value=Response({})) as request:
            assert client._client._make_request("delete", "/openApi/swap/v2/trade/order", {"symbol": "BTC-USDT", "orderId": "owned"})["code"] == 0
        assert request.call_count == 1 and events[0]["status"] == "PREPARED"
        assert events[0]["request_params"]["orderId"] == "owned"

    def test_unsupported_method(self, client):
        with pytest.raises(UnsupportedCapabilityError):
            client.make_request("patch", "/test/endpoint")

    def test_retry_on_connection_error(self, client):
        with patch("requests.get", side_effect=requests.ConnectionError("офлайн")) as request, patch("src.exchanges.impl.bingx_client.time.sleep") as sleep:
            with pytest.raises(ExchangeStateUnavailableError):
                client.make_request("get", "/read")
        assert request.call_count == 3
        assert [c.args[0] for c in sleep.call_args_list] == [1, 2]

    def test_retry_on_timeout(self, client):
        with patch("requests.get", side_effect=requests.Timeout("офлайн")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                client.make_request("get", "/read")
        assert request.call_count == 3

    def test_retry_on_chunked_encoding(self, client):
        with patch("requests.get", side_effect=requests.exceptions.ChunkedEncodingError("офлайн")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                client.make_request("get", "/read")
        assert request.call_count == 3

    def test_retry_then_success(self, client):
        with patch("requests.get", side_effect=[requests.ConnectionError(), requests.Timeout(), Response("ok")]) as request:
            assert client.make_request("get", "/read")["data"] == "ok"
        assert request.call_count == 3

    def test_http_error_no_retry(self, client):
        with patch("requests.get", return_value=Response({}, status=400)) as request:
            with pytest.raises(ExchangeAPIError) as caught:
                client.make_request("get", "/read")
        assert caught.value.code == 400 and request.call_count == 1

    def test_signature_appended(self, client):
        with patch("requests.get", return_value=Response({})) as request:
            client.make_request("get", "/read", {"key": "value"})
        assert "signature=" in request.call_args.args[0]
        assert "apiKey=" not in request.call_args.args[0]
        assert request.call_args.kwargs["headers"]["X-BX-APIKEY"] == "test-api-key"

    def test_no_keys_no_signature(self, client_no_keys):
        with patch("requests.get") as request:
            with pytest.raises(ExchangeStateUnavailableError):
                client_no_keys.make_request("get", "/private")
        request.assert_not_called()
        with patch("requests.get", return_value=Response({"lastPrice": "50000"})) as request:
            assert client_no_keys.get_ticker("BTCUSDT")["lastPrice"] == 50000
        assert "signature=" not in request.call_args.args[0] and "headers" not in request.call_args.kwargs


class TestSetLeverage:
    def test_success(self, client, exchange):
        assert client.set_leverage("BTCUSDT", 5, "LONG") is True
        assert exchange.leverage == 5 and exchange.calls[-1][0] == "get"

    def test_already_set_code_80001(self, client, exchange):
        exchange.leverage_error = 80001
        with pytest.raises(ExchangeAPIError) as caught:
            client.set_leverage("BTCUSDT", 5, "LONG")
        assert caught.value.code == 80001 and exchange.leverage == 10

    def test_api_failure(self, client, exchange):
        exchange.leverage_error = 100001
        with pytest.raises(ExchangeAPIError):
            client.set_leverage("BTCUSDT", 5, "LONG")
        assert exchange.leverage == 10

    def test_none_response(self, client):
        with patch.object(client._client, "_make_request", return_value=None):
            with pytest.raises(ExchangeStateUnavailableError):
                client.set_leverage("BTCUSDT", 10, "LONG")

    def test_exception_handling(self, client):
        with patch("requests.post", side_effect=requests.Timeout("офлайн")) as request:
            with pytest.raises(UnknownOrderStateError):
                client.set_leverage("BTCUSDT", 5, "LONG")
        assert request.call_count == 1

    def test_symbol_formatting(self, client, exchange):
        client.set_leverage("BTC/USDT", 20, "SHORT")
        params = exchange.mutations[0][2]
        assert params == {"symbol": "BTC-USDT", "side": "SHORT", "leverage": "20"}

    def test_uses_post_method(self, client, exchange):
        client.set_leverage("BTCUSDT", 5, "LONG")
        assert [method for method, _, _ in exchange.mutations] == ["post"]
        assert exchange.calls[-1][1].endswith("/leverage")


class TestPositionsCache:
    def test_cache_hit(self, client):
        """По умолчанию общий с ручной торговлей аккаунт читается заново."""
        with patch.object(client._client, "_make_request", return_value=position_response()) as request:
            first, second = client.get_positions(), client.get_positions()
        assert request.call_count == 2 and first == second

    def test_cache_expired(self, client):
        client._client._positions_cache_ttl = 5
        with patch.object(client._client, "_make_request", return_value=position_response()) as request:
            client.get_positions()
            client._client._positions_cache_time -= 10
            client.get_positions()
        assert request.call_count == 2

    def test_cache_shared_between_instances(self, client):
        """Кеш никогда не разделяется между экземплярами/аккаунтами."""
        client2 = BingXClient(api_key="other-fake-key", secret_key="other-fake-secret", trading_settings=SETTINGS)
        with patch.object(client._client, "_make_request", return_value=position_response()):
            assert client.get_positions()["BTCUSDT"][0]["size"] == .5
        with patch.object(client2._client, "_make_request", return_value={"code": 0, "data": []}) as request:
            assert client2.get_positions() == {}
        assert request.call_count == 1 and client2._client._owned_positions == {}


class TestBalanceCache:
    def test_cache_hit(self, client):
        with patch.object(client._client, "_make_request", return_value={"code": 0, "data": {"balance": {"equity": "1000", "availableMargin": "500"}}}) as request:
            first, second = client.get_perpetual_balance(), client.get_perpetual_balance()
        assert request.call_count == 2 and first == second and first["equity"] == 1000

    def test_cache_expired(self, client):
        with patch.object(client._client, "_make_request", side_effect=[{"code": 0, "data": {"balance": {"equity": "1000", "availableMargin": "500"}}}, {"code": 0, "data": {"balance": {"equity": "1000", "availableMargin": "0"}}}]) as request:
            assert client.get_perpetual_balance()["available"] == 500
            assert client.get_perpetual_balance()["available"] == 0
        assert request.call_count == 2

    def test_api_failure_returns_none(self, client):
        with patch.object(client._client, "_make_request", return_value={"code": 100001}):
            with pytest.raises(ExchangeAPIError):
                client.get_perpetual_balance()

    def test_cache_not_set_on_failure(self, client):
        with patch.object(client._client, "_make_request", side_effect=[{"code": 0, "data": {"equity": "500", "availableMargin": "100"}}, None]):
            assert client.get_perpetual_balance()["equity"] == 500
            with pytest.raises(ExchangeStateUnavailableError):
                client.get_perpetual_balance()
        assert client._client._balance_cache is None


class TestExchangeFactory:
    def test_bingx_client_created(self, client, tmp_path):
        from src.core.execution_coordinator import ExecutionCoordinator
        from src.exchanges import exchange_factory as factory
        from src.exchanges.impl.bingx_client import BingXClient as RESTClient
        with patch.dict(factory._client_instances, {}, clear=True):
            result = factory.get_trading_client(settings=SETTINGS, owner_id="unit-owner", account_id="unit-account", runtime_config={"DATA_DIR": str(tmp_path)})
        assert isinstance(result, ExecutionCoordinator) and isinstance(result.raw_client, RESTClient)
        assert result.owner_id == "unit-owner" and result.account_id == "unit-account"

    def test_unknown_exchange_raises(self, client):
        from src.exchanges import exchange_factory as factory
        with pytest.raises(ValueError):
            factory.get_trading_client(settings={"exchange": "kraken", "execution_mode": "demo"})

    def test_case_insensitive(self, client, tmp_path):
        from src.exchanges import exchange_factory as factory
        with patch.dict(factory._client_instances, {}, clear=True):
            result = factory.get_trading_client(exchange="BingX", settings=SETTINGS, runtime_config={"DATA_DIR": str(tmp_path)})
        assert result.settings["exchange"] == "bingx" and result.execution_safety_enabled is True


class TestCheckPrerequisites:
    def test_with_keys(self, client, exchange):
        assert client.check_prerequisites() is True
        assert any(path.endswith("/dual") for _, path, _ in exchange.calls)
        assert not exchange.mutations

    def test_without_keys(self, client_no_keys, exchange):
        with pytest.raises(ExchangeStateUnavailableError):
            client_no_keys.check_prerequisites()
        assert not exchange.calls


class TestGetPositionsParsing:
    def test_long_position_parsed(self, client):
        with patch.object(client._client, "_make_request", return_value=position_response()):
            pos = client.get_positions()["BTCUSDT"][0]
        assert pos["type"] == "buy" and pos["entry"] == 60000 and pos["size"] == .5
        assert pos["pnl"] == 500 and pos["dealId"] == "pos123"

    def test_short_position_parsed(self, client):
        with patch.object(client._client, "_make_request", return_value=position_response(symbol="ETH-USDT", positionAmt="-2", availableAmt="2", positionSide="SHORT")):
            pos = client.get_positions()["ETHUSDT"][0]
        assert pos["type"] == "sell" and pos["size"] == 2

    def test_zero_size_filtered(self, client):
        with patch.object(client._client, "_make_request", return_value=position_response(positionAmt="0")):
            assert client.get_positions() == {}

    def test_multiple_symbols(self, client):
        rows = position_response()["data"] + position_response(symbol="ETH-USDT", positionId="pos456", positionAmt="-5", availableAmt="5", positionSide="SHORT")["data"]
        with patch.object(client._client, "_make_request", return_value={"code": 0, "data": rows}):
            positions = client.get_positions()
        assert len(positions["BTCUSDT"]) == len(positions["ETHUSDT"]) == 1
