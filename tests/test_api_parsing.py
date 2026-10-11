"""Парсинг BingX через фактический адаптер/REST: реалистичные схемы, typed errors и owned fills."""

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from test_bingx_safety_regressions import Response
from test_bingx_unit import client as bingx_client, exchange, position_response


@pytest.fixture
def klines_dict_response():
    return {"code": 0, "data": [
        {"time": 1700000000000, "open": "96500", "high": "97000", "low": "96200", "close": "96800.5", "volume": "1234.56"},
        {"time": 1700000300000, "open": "96800.5", "high": "97300", "low": "96700", "close": "97100", "volume": "987.12"},
        {"time": 1700000600000, "open": "97100", "high": "97200", "low": "96600", "close": "96900", "volume": "1500"},
    ]}


@pytest.fixture
def klines_list_response(klines_dict_response):
    return {"code": 0, "data": [[r[k] for k in ("time", "open", "high", "low", "close", "volume")]
                               for r in klines_dict_response["data"][:2]]}


@pytest.fixture
def positions_response():
    return {"code": 0, "data": [
        position_response(positionAmt=".05", availableAmt=".05", avgPrice="96500", unrealizedProfit="150.25", positionId="pos_123456")["data"][0],
        position_response(symbol="ETH-USDT", positionSide="SHORT", positionAmt="-2.5", availableAmt="2.5", avgPrice="3400", unrealizedProfit="-25.50", positionId="pos_789012")["data"][0],
        {"symbol": "SOL-USDT", "positionSide": "BOTH", "positionAmt": "0"},
    ]}


@pytest.fixture
def order_response_flat():
    return {"code": 0, "data": {"orderId": "order_flat_111"}}


@pytest.fixture
def order_response_nested():
    return {"code": 0, "data": {"order": {"orderId": "order_nested_222"}}}


@pytest.fixture
def order_book_response():
    return {"code": 0, "data": {"bids": [["96750", "1.5"], ["96740", "2.3"], ["96730", ".8"]],
                                "asks": [["96760", "1.2"], ["96770", "3.1"], ["96780", ".5"]]}}


@pytest.fixture
def ticker_response():
    return {"code": 0, "data": {"bestBidPrice": "96750", "bestAskPrice": "96760", "lastPrice": "96755", "volume": "45678.90"}}


@pytest.fixture
def balance_response():
    return {"code": 0, "data": {"balance": {"asset": "USDT", "balance": "10000", "availableMargin": "8500", "equity": "10150.50", "unrealizedProfit": "150.50"}}}


@pytest.fixture
def api_error_response():
    return {"code": 80001, "msg": "Невалидный параметр", "data": None}


def response(payload):
    result = Response()
    result.payload = copy.deepcopy(payload)
    return result


def candles(client, payload, *, symbol="BTCUSDT", interval="5m", limit=10):
    with patch("requests.get", return_value=response(payload)):
        return client._fetch_klines_rest(symbol, interval, limit)


def positions(client, payload):
    with patch.object(client._client, "_make_request", return_value=payload):
        return client.get_positions()


def limit(client, *, side="BUY"):
    return client.place_order("BTC/USDT", side, 50000, ".01", type="LIMIT", client_order_id="parsed-limit")


def open_owned(client, *, side="BUY", quantity=".05"):
    return client.place_order("BTCUSDT", side, 50000, quantity, client_order_id="parsed-entry")


class TestKlineParsing:
    def test_klines_dict_format(self, bingx_client, klines_dict_response):
        result = candles(bingx_client, klines_dict_response)
        assert len(result) == 3
        assert {"snapshotTimeUTC", "openPrice", "closePrice", "highPrice", "lowPrice", "volume"} <= result[0].keys()

    def test_klines_dict_values(self, bingx_client, klines_dict_response):
        candle = candles(bingx_client, klines_dict_response)[0]
        assert candle["openPrice"] == 96500 and candle["closePrice"] == 96800.5
        assert candle["highPrice"] == 97000 and candle["lowPrice"] == 96200 and candle["volume"] == 1234.56

    def test_klines_list_format(self, bingx_client, klines_list_response):
        result = candles(bingx_client, klines_list_response)
        assert len(result) == 2
        assert [result[0][k] for k in ("openPrice", "closePrice", "highPrice", "lowPrice", "volume")] == [96500, 96800.5, 97000, 96200, 1234.56]

    def test_klines_sorted_by_time(self, bingx_client, klines_dict_response):
        klines_dict_response["data"].reverse()
        result = candles(bingx_client, klines_dict_response)
        timestamps = [c["snapshotTimeUTC"] for c in result]
        assert len(timestamps) == 3 and timestamps == sorted(timestamps)

    def test_klines_interval_mapping(self, bingx_client, klines_dict_response):
        for verbose, expected in {"MINUTE_1": "1m", "MINUTE_5": "5m", "MINUTE_15": "15m", "MINUTE_30": "30m", "HOUR_1": "1h", "HOUR_4": "4h", "DAY_1": "1d"}.items():
            with patch("requests.get", return_value=response(klines_dict_response)) as request:
                bingx_client._fetch_klines_rest("BTC-USDT", verbose, 10)
            assert request.call_args.kwargs["params"]["interval"] == expected

    def test_klines_passthrough_interval(self, bingx_client, klines_dict_response):
        with patch("requests.get", return_value=response(klines_dict_response)) as request:
            bingx_client._fetch_klines_rest("BTCUSDT", "15m", 10)
        assert request.call_args.kwargs["params"]["interval"] == "15m"

    def test_klines_symbol_formatting(self, bingx_client, klines_dict_response):
        for symbol, expected in [("BTC/USDT", "BTC-USDT"), ("BTCUSDT", "BTC-USDT"), ("ETH/USDT", "ETH-USDT"), ("ETHUSDT", "ETH-USDT"), ("BTC-USDT", "BTC-USDT")]:
            with patch("requests.get", return_value=response(klines_dict_response)) as request:
                bingx_client._fetch_klines_rest(symbol, "5m", 10)
            assert request.call_args.kwargs["params"]["symbol"] == expected
        with pytest.raises(UnsupportedCapabilityError):
            bingx_client._fetch_klines_rest("BTC/USD")

    def test_klines_empty_data(self, bingx_client):
        assert candles(bingx_client, {"code": 0, "data": []}) == []

    def test_klines_api_error(self, bingx_client, api_error_response):
        with pytest.raises(ExchangeAPIError) as caught:
            candles(bingx_client, api_error_response)
        assert caught.value.code == 80001

    def test_klines_network_error(self, bingx_client):
        with patch("requests.get", side_effect=requests.ConnectionError("офлайн")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                bingx_client._fetch_klines_rest("BTCUSDT")
        assert request.call_count == 3

    def test_klines_mixed_types_skipped(self, bingx_client, klines_dict_response):
        """Повреждённая история отклоняется целиком: молчаливые пробелы недопустимы."""
        klines_dict_response["data"].extend(["invalid_string_entry", 42])
        with pytest.raises(ExchangeStateUnavailableError):
            candles(bingx_client, klines_dict_response)

    def test_klines_timestamp_conversion(self, bingx_client, klines_dict_response):
        expected = datetime.fromtimestamp(1700000000, timezone.utc).isoformat()
        assert candles(bingx_client, klines_dict_response)[0]["snapshotTimeUTC"] == expected


class TestPositionsParsing:
    def test_positions_basic_parsing(self, bingx_client, positions_response):
        result = positions(bingx_client, positions_response)
        assert "BTCUSDT" in result and "ETHUSDT" in result and "SOLUSDT" not in result

    def test_positions_long_side(self, bingx_client, positions_response):
        pos = positions(bingx_client, positions_response)["BTCUSDT"][0]
        assert pos["type"] == "buy" and pos["entry"] == 96500 and pos["size"] == .05
        assert pos["pnl"] == 150.25 and pos["dealId"] == pos["workingOrderId"] == "pos_123456"
        assert pos["created"] is None

    def test_positions_short_side(self, bingx_client, positions_response):
        pos = positions(bingx_client, positions_response)["ETHUSDT"][0]
        assert pos["type"] == "sell" and pos["size"] == 2.5 and pos["pnl"] == -25.50

    def test_positions_zero_size_filtered(self, bingx_client, positions_response):
        assert "SOLUSDT" not in positions(bingx_client, positions_response)

    def test_positions_symbol_normalization(self, bingx_client):
        result = positions(bingx_client, position_response(symbol="DOGE-USDT", positionAmt="1000", availableAmt="1000", avgPrice=".15"))
        assert "DOGEUSDT" in result and "DOGE-USDT" not in result

    def test_positions_oneway_mode_positive_amount(self, bingx_client):
        assert positions(bingx_client, position_response(positionSide="BOTH", positionAmt=".1", availableAmt=".1"))["BTCUSDT"][0]["type"] == "buy"

    def test_positions_oneway_mode_negative_amount(self, bingx_client):
        pos = positions(bingx_client, position_response(positionSide="BOTH", positionAmt="-.1", availableAmt=".1"))["BTCUSDT"][0]
        assert pos["type"] == "sell" and pos["size"] == .1

    def test_positions_empty_data(self, bingx_client):
        assert positions(bingx_client, {"code": 0, "data": []}) == {}

    def test_positions_api_error(self, bingx_client, api_error_response):
        with pytest.raises(ExchangeAPIError) as caught:
            positions(bingx_client, api_error_response)
        assert caught.value.code == 80001

    def test_positions_none_response(self, bingx_client):
        with pytest.raises(ExchangeStateUnavailableError):
            positions(bingx_client, None)

    def test_positions_multiple_per_symbol(self, bingx_client, positions_response):
        positions_response["data"][1]["symbol"] = "BTC-USDT"
        result = positions(bingx_client, positions_response)
        assert len(result["BTCUSDT"]) == 2
        assert [p["type"] for p in result["BTCUSDT"]] == ["buy", "sell"]

    def test_positions_cache(self, bingx_client, positions_response):
        with patch.object(bingx_client._client, "_make_request", return_value=positions_response) as request:
            first, second = bingx_client.get_positions(), bingx_client.get_positions()
        assert request.call_count == 2 and first == second


class TestPlaceOrderParsing:
    def test_order_id_flat_format(self, bingx_client, order_response_flat):
        with patch("requests.post", return_value=response(order_response_flat)) as request:
            assert limit(bingx_client) == "order_flat_111"
        assert request.call_count == 1
        assert bingx_client._client.last_submission["status"] == "ACKNOWLEDGED"
        assert bingx_client._client._owned_positions == {}

    def test_order_id_nested_format(self, bingx_client, order_response_nested):
        with patch("requests.post", return_value=response(order_response_nested)):
            assert limit(bingx_client, side="SELL") == "order_nested_222"
        assert bingx_client._client._owned_positions == {}

    def test_order_api_error(self, bingx_client, api_error_response):
        with patch("requests.post", return_value=response(api_error_response)) as request:
            with pytest.raises(ExchangeAPIError) as caught:
                limit(bingx_client)
        assert caught.value.code == 80001 and request.call_count == 1
        assert bingx_client._client._owned_positions == {}

    def test_order_none_response(self, bingx_client):
        bad = Response()
        bad.payload = None
        with patch("requests.post", return_value=bad) as request:
            with pytest.raises(UnknownOrderStateError):
                limit(bingx_client)
            with pytest.raises(UnknownOrderStateError):
                limit(bingx_client)
        assert request.call_count == 1 and bingx_client._client._owned_positions == {}

    def test_order_empty_data(self, bingx_client):
        with patch("requests.post", return_value=Response({})) as request:
            with pytest.raises(UnknownOrderStateError):
                limit(bingx_client)
        assert request.call_count == 1 and bingx_client._client._owned_positions == {}

    def test_order_with_tp_sl(self, bingx_client, exchange):
        oid = bingx_client.place_order("BTCUSDT", "BUY", 50000, ".01", sl=49500, tp=51500, client_order_id="protected")
        assert oid == "1" and exchange.amount == Decimal(".01")
        assert "stopLoss" not in exchange.orders[oid] and "takeProfit" not in exchange.orders[oid]
        sl, tp = exchange.orders["2"], exchange.orders["3"]
        assert sl["type"] == "STOP_MARKET" and Decimal(sl["stopPrice"]) == 49500
        assert tp["type"] == "TAKE_PROFIT_MARKET" and Decimal(tp["stopPrice"]) == 51500
        assert sl["side"] == tp["side"] == "SELL" and sl["positionSide"] == tp["positionSide"] == "LONG"
        assert bingx_client.last_protection_confirmed is True
        assert bingx_client._client._owned_positions["p1"][1] == Decimal(".01")

    def test_order_position_side_buy(self, bingx_client, exchange):
        oid = limit(bingx_client)
        assert exchange.orders[oid]["positionSide"] == "LONG" and exchange.orders[oid]["side"] == "BUY"

    def test_order_position_side_sell(self, bingx_client, exchange):
        oid = limit(bingx_client, side="SELL")
        assert exchange.orders[oid]["positionSide"] == "SHORT" and exchange.orders[oid]["side"] == "SELL"


class TestOrderBookParsing:
    def test_order_book_basic(self, bingx_client, order_book_response):
        with patch("requests.get", return_value=response(order_book_response)):
            result = bingx_client.get_order_book("BTCUSDT")
        assert len(result["bids"]) == len(result["asks"]) == 3

    def test_order_book_float_conversion(self, bingx_client, order_book_response):
        with patch("requests.get", return_value=response(order_book_response)):
            result = bingx_client.get_order_book("BTCUSDT")
        assert all(isinstance(v, float) for side in result.values() for row in side for v in row)
        assert result["bids"][0] == [96750.0, 1.5] and result["asks"][0] == [96760.0, 1.2]

    def test_order_book_api_error(self, bingx_client, api_error_response):
        with patch("requests.get", return_value=response(api_error_response)):
            with pytest.raises(ExchangeAPIError):
                bingx_client.get_order_book("BTCUSDT")

    def test_order_book_empty_data(self, bingx_client):
        with patch("requests.get", return_value=Response({"bids": [], "asks": []})):
            assert bingx_client.get_order_book("BTCUSDT") == {"bids": [], "asks": []}

    def test_order_book_network_error(self, bingx_client):
        with patch("requests.get", side_effect=requests.ConnectionError("офлайн")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                bingx_client.get_order_book("BTCUSDT")
        assert request.call_count == 3

    def test_order_book_symbol_formatting(self, bingx_client, order_book_response):
        with patch("requests.get", return_value=response(order_book_response)) as request:
            bingx_client.get_order_book("BTC/USDT")
        assert request.call_args.kwargs["params"]["symbol"] == "BTC-USDT"


class TestTickerParsing:
    def test_ticker_basic(self, bingx_client, ticker_response):
        with patch("requests.get", return_value=response(ticker_response)):
            result = bingx_client.get_ticker("BTCUSDT")
        assert result["bidPrice"] == 96750 and result["askPrice"] == 96760
        assert result["lastPrice"] == 96755 and result["volume"] == 45678.90

    def test_ticker_missing_fields(self, bingx_client):
        with patch("requests.get", return_value=Response({"lastPrice": "50000"})):
            result = bingx_client.get_ticker("BTCUSDT")
        assert result["lastPrice"] == 50000
        assert result["bidPrice"] == result["askPrice"] == result["volume"] == 0

    def test_ticker_null_fields(self, bingx_client):
        with patch("requests.get", return_value=Response({"lastPrice": None, "bestBidPrice": None, "volume": None})):
            with pytest.raises(ExchangeStateUnavailableError):
                bingx_client.get_ticker("BTCUSDT")

    def test_ticker_api_error(self, bingx_client, api_error_response):
        with patch("requests.get", return_value=response(api_error_response)):
            with pytest.raises(ExchangeAPIError):
                bingx_client.get_ticker("BTCUSDT")

    def test_ticker_network_error(self, bingx_client):
        with patch("requests.get", side_effect=requests.Timeout("офлайн")) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                bingx_client.get_ticker("BTCUSDT")
        assert request.call_count == 3

    def test_ticker_symbol_formatting(self, bingx_client, ticker_response):
        with patch("requests.get", return_value=response(ticker_response)) as request:
            bingx_client.get_ticker("ETHUSDT")
        assert request.call_args.kwargs["params"]["symbol"] == "ETH-USDT"


class TestClosePositionParsing:
    def test_close_position_not_found(self, bingx_client, exchange):
        with pytest.raises(ExchangeStateUnavailableError):
            bingx_client.close_position("BTCUSDT", "nonexistent_id")
        assert not exchange.mutations

    def test_close_position_zero_quantity(self, bingx_client, exchange):
        open_owned(bingx_client, quantity=".01")
        with pytest.raises(ExchangeStateUnavailableError):
            bingx_client.close_position("BTCUSDT", "p1", percentage=.001)
        assert len(exchange.mutations) == 1 and exchange.amount == Decimal(".01")

    def test_close_position_success(self, bingx_client, exchange):
        open_owned(bingx_client)
        assert bingx_client.close_position("BTCUSDT", "p1", client_order_id="close") is True
        assert exchange.amount == 0
        assert Decimal(exchange.mutations[-1][2]["quantity"]) == Decimal(".05")

    def test_close_position_short(self, bingx_client, exchange):
        open_owned(bingx_client, side="SELL", quantity="2")
        assert bingx_client.close_position("BTCUSDT", "p1", client_order_id="close-short") is True
        params = exchange.mutations[-1][2]
        assert params["side"] == "BUY" and params["positionSide"] == "SHORT" and exchange.amount == 0

    def test_close_position_api_failure(self, bingx_client, exchange, api_error_response):
        open_owned(bingx_client)
        with patch("requests.post", return_value=response(api_error_response)) as request:
            with pytest.raises(ExchangeAPIError):
                bingx_client.close_position("BTCUSDT", "p1", client_order_id="failed-close")
        assert request.call_count == 1 and exchange.amount == Decimal(".05")
        assert bingx_client._client._owned_positions["p1"][1] == Decimal(".05")

    def test_close_position_partial(self, bingx_client, exchange):
        open_owned(bingx_client, quantity=".1")
        assert bingx_client.close_position("BTCUSDT", "p1", percentage=.5, client_order_id="half-close") is True
        assert Decimal(exchange.mutations[-1][2]["quantity"]) == Decimal(".05")
        assert exchange.amount == bingx_client._client._owned_positions["p1"][1] == Decimal(".05")


class TestBalanceParsing:
    def test_balance_success(self, bingx_client, balance_response):
        with patch.object(bingx_client._client, "_make_request", return_value=balance_response):
            result = bingx_client.get_perpetual_balance()
        assert result["balance"] == 10000 and result["equity"] == 10150.50 and result["available"] == 8500

    def test_balance_api_error(self, bingx_client, api_error_response):
        with patch.object(bingx_client._client, "_make_request", return_value=api_error_response):
            with pytest.raises(ExchangeAPIError):
                bingx_client.get_perpetual_balance()

    def test_balance_none_response(self, bingx_client):
        with patch.object(bingx_client._client, "_make_request", return_value=None):
            with pytest.raises(ExchangeStateUnavailableError):
                bingx_client.get_perpetual_balance()

    def test_balance_cache(self, bingx_client, balance_response):
        with patch.object(bingx_client._client, "_make_request", return_value=balance_response) as request:
            first, second = bingx_client.get_perpetual_balance(), bingx_client.get_perpetual_balance()
        assert request.call_count == 2 and first == second


class TestSymbolFormatting:
    def test_format_usd_suffix(self, bingx_client):
        with pytest.raises(UnsupportedCapabilityError):
            bingx_client._format_symbol("BTC/USD")

    def test_format_usdt_suffix(self, bingx_client):
        assert bingx_client._format_symbol("BTCUSDT") == "BTC-USDT"

    def test_format_already_correct(self, bingx_client):
        assert bingx_client._format_symbol("BTC-USDT") == "BTC-USDT"

    def test_format_slash_pair(self, bingx_client):
        assert bingx_client._format_symbol("ETH/USDT") == "ETH-USDT"

    def test_format_ethusdt(self, bingx_client):
        assert bingx_client._format_symbol("ETHUSDT") == "ETH-USDT"

    def test_format_solusdt(self, bingx_client):
        assert bingx_client._format_symbol("SOLUSDT") == "SOL-USDT"


class TestMakeRequest:
    def test_make_request_json_response(self, bingx_client):
        with patch("requests.get", return_value=Response({"test": True})):
            assert bingx_client.make_request("get", "/read")["data"] == {"test": True}

    def test_make_request_network_retry(self, bingx_client):
        with patch("requests.get", side_effect=[requests.ConnectionError(), requests.Timeout(), Response({})]) as request:
            result = bingx_client.make_request("get", "/read")
        assert result["code"] == 0 and request.call_count == 3

    def test_make_request_all_retries_exhausted(self, bingx_client):
        with patch("requests.get", side_effect=requests.ConnectionError()) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                bingx_client.make_request("get", "/read")
        assert request.call_count == 3

    def test_make_request_post_method(self, bingx_client):
        with patch("requests.post", return_value=Response({})) as request:
            with pytest.raises(UnsupportedCapabilityError):
                bingx_client.make_request("post", "/test/endpoint")
        request.assert_not_called()
        events = []
        bingx_client._client._submission_observer = events.append
        with patch("requests.post", return_value=Response({})) as request:
            assert bingx_client._client._make_request("post", "/openApi/swap/v2/trade/order", {"symbol": "BTC-USDT", "clientOrderId": "intent"})["code"] == 0
        assert request.call_count == 1 and events[0]["status"] == "PREPARED"
        assert events[0]["request_params"]["clientOrderId"] == "intent"

    def test_make_request_delete_method(self, bingx_client, exchange):
        oid = limit(bingx_client)
        assert bingx_client.cancel_order("BTCUSDT", oid)
        assert exchange.mutations[-1][0] == "delete" and exchange.orders[oid]["status"] == "CANCELLED"

    def test_make_request_unsupported_method(self, bingx_client):
        with pytest.raises(UnsupportedCapabilityError):
            bingx_client.make_request("patch", "/test/endpoint")

    def test_make_request_malformed_json(self, bingx_client):
        bad = Response()
        with patch.object(bad, "json", side_effect=json.JSONDecodeError("Повреждён JSON", "", 0)), patch("requests.get", return_value=bad) as request:
            with pytest.raises(ExchangeStateUnavailableError):
                bingx_client.make_request("get", "/read")
        assert request.call_count == 1


class TestGetKlineDataWithWsCache:
    def test_kline_data_falls_back_to_rest(self, bingx_client, klines_dict_response):
        with patch("requests.get", return_value=response(klines_dict_response)), patch.dict("sys.modules", {"src.exchanges.bingx_ws_data_provider": None}):
            result = bingx_client.get_kline_data("BTCUSDT", "5m", 3)
        assert len(result) == 3

    def test_kline_data_uses_ws_cache(self, bingx_client, klines_dict_response):
        """Symbol-only кеш без interval/freshness не принимается raw-адаптером."""
        mock_ws = MagicMock()
        mock_ws.is_cache_ready.return_value = True
        mock_ws.get_klines_from_shared_cache.return_value = [{"closePrice": 1}]
        with patch.dict("sys.modules", {"src.exchanges.bingx_ws_data_provider": mock_ws}), patch("requests.get", return_value=response(klines_dict_response)) as request:
            result = bingx_client.get_kline_data("BTCUSDT", "5m", 2)
        assert len(result) == 2 and result[-1]["closePrice"] == 96900
        assert request.call_count == 1
        mock_ws.get_klines_from_shared_cache.assert_not_called()
