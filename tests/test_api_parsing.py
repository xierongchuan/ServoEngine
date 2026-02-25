"""
Интеграционные тесты для парсинга ответов BingX API.
Проверяет нормализацию данных, обработку различных форматов ответов и ошибок.
"""

import os
import sys
import time
import json
import pytest
from unittest.mock import patch, MagicMock

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Фикстуры: реалистичные ответы BingX API
# ============================================================

@pytest.fixture
def bingx_client():
    """Создаём клиент BingX с фиктивными ключами (без реальных запросов)."""
    with patch.dict(os.environ, {
        "BINGX_API_KEY": "test_api_key",
        "BINGX_SECRET_KEY": "test_secret_key",
        "BINGX_API_URL": "https://open-api.bingx.com",
        "MODE": "demo",
        "EXCHANGE": "bingx",
    }):
        from src.exchanges.bingx_client import BingXClient
        # Сбрасываем кэш между тестами
        BingXClient._positions_cache = None
        BingXClient._positions_cache_time = 0
        BingXClient._balance_cache = None
        BingXClient._balance_cache_time = 0
        client = BingXClient(api_key="test_api_key", secret_key="test_secret_key")
        yield client


@pytest.fixture
def klines_dict_response():
    """Ответ API klines в формате dict (стандартный формат BingX v3)."""
    return {
        "code": 0,
        "msg": "",
        "data": [
            {
                "open": "96500.0",
                "close": "96800.5",
                "high": "97000.0",
                "low": "96200.0",
                "volume": "1234.56",
                "time": 1700000000000
            },
            {
                "open": "96800.5",
                "close": "97100.0",
                "high": "97300.0",
                "low": "96700.0",
                "volume": "987.12",
                "time": 1700000300000
            },
            {
                "open": "97100.0",
                "close": "96900.0",
                "high": "97200.0",
                "low": "96600.0",
                "volume": "1500.00",
                "time": 1700000600000
            },
        ]
    }


@pytest.fixture
def klines_list_response():
    """Ответ API klines в формате list (альтернативный формат)."""
    return {
        "code": 0,
        "msg": "",
        "data": [
            # [time, open, high, low, close, volume]
            [1700000000000, "96500.0", "97000.0", "96200.0", "96800.5", "1234.56"],
            [1700000300000, "96800.5", "97300.0", "96700.0", "97100.0", "987.12"],
        ]
    }


@pytest.fixture
def positions_response():
    """Ответ API с открытыми позициями."""
    return {
        "code": 0,
        "msg": "",
        "data": [
            {
                "symbol": "BTC-USDT",
                "positionSide": "LONG",
                "positionAmt": "0.05",
                "avgPrice": "96500.00",
                "unrealizedProfit": "150.25",
                "positionId": "pos_123456"
            },
            {
                "symbol": "ETH-USDT",
                "positionSide": "SHORT",
                "positionAmt": "-2.5",
                "avgPrice": "3400.00",
                "unrealizedProfit": "-25.50",
                "positionId": "pos_789012"
            },
            {
                "symbol": "SOL-USDT",
                "positionSide": "BOTH",
                "positionAmt": "0",
                "avgPrice": "0",
                "unrealizedProfit": "0",
                "positionId": "pos_000000"
            }
        ]
    }


@pytest.fixture
def order_response_flat():
    """Ответ API place_order — orderId на верхнем уровне data."""
    return {
        "code": 0,
        "msg": "",
        "data": {
            "orderId": "order_flat_111"
        }
    }


@pytest.fixture
def order_response_nested():
    """Ответ API place_order — orderId вложен в data.order."""
    return {
        "code": 0,
        "msg": "",
        "data": {
            "order": {
                "orderId": "order_nested_222"
            }
        }
    }


@pytest.fixture
def order_book_response():
    """Ответ API order book (depth)."""
    return {
        "code": 0,
        "msg": "",
        "data": {
            "bids": [
                ["96750.0", "1.5"],
                ["96740.0", "2.3"],
                ["96730.0", "0.8"],
            ],
            "asks": [
                ["96760.0", "1.2"],
                ["96770.0", "3.1"],
                ["96780.0", "0.5"],
            ]
        }
    }


@pytest.fixture
def ticker_response():
    """Ответ API ticker."""
    return {
        "code": 0,
        "msg": "",
        "data": {
            "bestBidPrice": "96750.0",
            "bestAskPrice": "96760.0",
            "lastPrice": "96755.0",
            "volume": "45678.90"
        }
    }


@pytest.fixture
def balance_response():
    """Ответ API баланса perpetual."""
    return {
        "code": 0,
        "msg": "",
        "data": {
            "balance": {
                "asset": "USDT",
                "balance": "10000.00",
                "availableMargin": "8500.00",
                "equity": "10150.50",
                "unrealizedProfit": "150.50"
            }
        }
    }


@pytest.fixture
def api_error_response():
    """Ответ API с ошибкой."""
    return {
        "code": 80001,
        "msg": "Invalid parameter",
        "data": None
    }


# ============================================================
# Тесты: _fetch_klines_rest — нормализация OHLCV
# ============================================================

class TestKlineParsing:
    """Тесты парсинга свечных данных (klines)."""

    def test_klines_dict_format(self, bingx_client, klines_dict_response):
        """Проверяет нормализацию klines из dict формата."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = klines_dict_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        assert len(result) == 3
        # Проверяем структуру нормализованных данных
        candle = result[0]
        assert "snapshotTimeUTC" in candle
        assert "openPrice" in candle
        assert "closePrice" in candle
        assert "highPrice" in candle
        assert "lowPrice" in candle
        assert "volume" in candle

    def test_klines_dict_values(self, bingx_client, klines_dict_response):
        """Проверяет корректность значений из dict формата."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = klines_dict_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTCUSDT", "5m", 10)

        # Первая свеча (после сортировки по времени)
        candle = result[0]
        assert candle["openPrice"] == 96500.0
        assert candle["closePrice"] == 96800.5
        assert candle["highPrice"] == 97000.0
        assert candle["lowPrice"] == 96200.0
        assert candle["volume"] == 1234.56

    def test_klines_list_format(self, bingx_client, klines_list_response):
        """Проверяет нормализацию klines из list формата (альтернативный ответ)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = klines_list_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        assert len(result) == 2
        candle = result[0]
        # В list формате: [time, open, high, low, close, volume]
        assert candle["openPrice"] == 96500.0
        assert candle["closePrice"] == 96800.5
        assert candle["highPrice"] == 97000.0
        assert candle["lowPrice"] == 96200.0
        assert candle["volume"] == 1234.56

    def test_klines_sorted_by_time(self, bingx_client):
        """Проверяет что свечи сортируются по времени (snapshotTimeUTC)."""
        # Ответ с неупорядоченными временными метками
        unsorted_response = {
            "code": 0,
            "msg": "",
            "data": [
                {"open": "100", "close": "101", "high": "102", "low": "99",
                 "volume": "10", "time": 1700000600000},  # третья
                {"open": "100", "close": "101", "high": "102", "low": "99",
                 "volume": "10", "time": 1700000000000},  # первая
                {"open": "100", "close": "101", "high": "102", "low": "99",
                 "volume": "10", "time": 1700000300000},  # вторая
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = unsorted_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        # Проверяем порядок: timestamps должны идти по возрастанию
        timestamps = [c["snapshotTimeUTC"] for c in result]
        assert timestamps == sorted(timestamps)

    def test_klines_interval_mapping(self, bingx_client, klines_dict_response):
        """Проверяет маппинг интервалов (MINUTE_1 -> 1m, HOUR_4 -> 4h и т.д.)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = klines_dict_response
        mock_resp.raise_for_status = MagicMock()

        interval_mappings = {
            "MINUTE_1": "1m",
            "MINUTE_5": "5m",
            "MINUTE_15": "15m",
            "MINUTE_30": "30m",
            "HOUR_1": "1h",
            "HOUR_4": "4h",
            "DAY_1": "1d",
        }

        for verbose, expected in interval_mappings.items():
            with patch("requests.get", return_value=mock_resp) as mock_get:
                bingx_client._fetch_klines_rest("BTC-USDT", verbose, 10)
                # Проверяем что в запросе использован правильный интервал
                call_kwargs = mock_get.call_args
                sent_params = call_kwargs.kwargs.get("params", {}) or call_kwargs[1].get("params", {})
                assert sent_params["interval"] == expected, \
                    f"Интервал {verbose} должен маппиться в {expected}, получили {sent_params['interval']}"

    def test_klines_passthrough_interval(self, bingx_client, klines_dict_response):
        """Проверяет что стандартные интервалы (5m, 1h) проходят без изменений."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = klines_dict_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            bingx_client._fetch_klines_rest("BTC-USDT", "15m", 10)
            call_kwargs = mock_get.call_args
            sent_params = call_kwargs.kwargs.get("params", {}) or call_kwargs[1].get("params", {})
            assert sent_params["interval"] == "15m"

    def test_klines_symbol_formatting(self, bingx_client, klines_dict_response):
        """Проверяет форматирование символа: BTC/USD -> BTC-USDT, BTCUSDT -> BTC-USDT."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = klines_dict_response
        mock_resp.raise_for_status = MagicMock()

        test_cases = [
            ("BTC/USD", "BTC-USDT"),
            ("BTCUSDT", "BTC-USDT"),
            ("ETH/USD", "ETH-USDT"),
            ("ETHUSDT", "ETH-USDT"),
            ("BTC-USDT", "BTC-USDT"),  # уже правильный формат
        ]

        for input_symbol, expected_symbol in test_cases:
            with patch("requests.get", return_value=mock_resp) as mock_get:
                bingx_client._fetch_klines_rest(input_symbol, "5m", 10)
                call_kwargs = mock_get.call_args
                sent_params = call_kwargs.kwargs.get("params", {}) or call_kwargs[1].get("params", {})
                assert sent_params["symbol"] == expected_symbol, \
                    f"Символ {input_symbol} должен стать {expected_symbol}, получили {sent_params['symbol']}"

    def test_klines_empty_data(self, bingx_client):
        """Проверяет обработку пустого ответа (code=0, data=[])."""
        empty_response = {"code": 0, "msg": "", "data": []}
        mock_resp = MagicMock()
        mock_resp.json.return_value = empty_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        assert result == []

    def test_klines_api_error(self, bingx_client, api_error_response):
        """Проверяет обработку ошибки API (code != 0)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_error_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        assert result == []

    def test_klines_network_error(self, bingx_client):
        """Проверяет обработку сетевой ошибки (все ретраи исчерпаны)."""
        import requests as req

        with patch("requests.get", side_effect=req.exceptions.ConnectionError("Connection refused")), \
             patch("time.sleep"):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        assert result == []

    def test_klines_mixed_types_skipped(self, bingx_client):
        """Проверяет что элементы неизвестного типа (не dict и не list) пропускаются."""
        mixed_response = {
            "code": 0,
            "msg": "",
            "data": [
                {"open": "100", "close": "101", "high": "102", "low": "99",
                 "volume": "10", "time": 1700000000000},
                "invalid_string_entry",  # не dict и не list — должен быть пропущен
                42,  # число — тоже пропущено
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = mixed_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        assert len(result) == 1

    def test_klines_timestamp_conversion(self, bingx_client):
        """Проверяет конвертацию millisecond timestamp в ISO формат."""
        response = {
            "code": 0,
            "msg": "",
            "data": [
                {"open": "100", "close": "101", "high": "102", "low": "99",
                 "volume": "10", "time": 1700000000000},  # 2023-11-14T22:13:20 UTC
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client._fetch_klines_rest("BTC/USD", "5m", 10)

        expected_time = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(1700000000))
        assert result[0]["snapshotTimeUTC"] == expected_time


# ============================================================
# Тесты: get_positions — нормализация позиций
# ============================================================

class TestPositionsParsing:
    """Тесты парсинга открытых позиций."""

    def test_positions_basic_parsing(self, bingx_client, positions_response):
        """Проверяет базовый парсинг позиций."""
        with patch.object(bingx_client, "make_request", return_value=positions_response):
            result = bingx_client.get_positions()

        # Должны быть 2 позиции (SOL с size=0 отфильтрована)
        assert "BTCUSDT" in result
        assert "ETHUSDT" in result
        assert "SOLUSDT" not in result

    def test_positions_long_side(self, bingx_client, positions_response):
        """Проверяет нормализацию LONG позиции."""
        with patch.object(bingx_client, "make_request", return_value=positions_response):
            result = bingx_client.get_positions()

        btc_pos = result["BTCUSDT"][0]
        assert btc_pos["type"] == "buy"
        assert btc_pos["entry"] == 96500.0
        assert btc_pos["size"] == 0.05
        assert btc_pos["pnl"] == 150.25
        assert btc_pos["dealId"] == "pos_123456"
        assert btc_pos["workingOrderId"] == "pos_123456"
        assert btc_pos["created"] is None

    def test_positions_short_side(self, bingx_client, positions_response):
        """Проверяет нормализацию SHORT позиции."""
        with patch.object(bingx_client, "make_request", return_value=positions_response):
            result = bingx_client.get_positions()

        eth_pos = result["ETHUSDT"][0]
        assert eth_pos["type"] == "sell"
        assert eth_pos["entry"] == 3400.0
        assert eth_pos["size"] == 2.5  # abs(-2.5)
        assert eth_pos["pnl"] == -25.50

    def test_positions_zero_size_filtered(self, bingx_client, positions_response):
        """Проверяет фильтрацию позиций с нулевым размером."""
        with patch.object(bingx_client, "make_request", return_value=positions_response):
            result = bingx_client.get_positions()

        # SOL с positionAmt=0 должна быть отфильтрована
        all_symbols = list(result.keys())
        assert "SOLUSDT" not in all_symbols

    def test_positions_symbol_normalization(self, bingx_client):
        """Проверяет что BTC-USDT превращается в BTCUSDT (убирается дефис)."""
        response = {
            "code": 0,
            "msg": "",
            "data": [
                {
                    "symbol": "DOGE-USDT",
                    "positionSide": "LONG",
                    "positionAmt": "1000",
                    "avgPrice": "0.15",
                    "unrealizedProfit": "5.0",
                    "positionId": "pos_doge_1"
                }
            ]
        }
        with patch.object(bingx_client, "make_request", return_value=response):
            result = bingx_client.get_positions()

        assert "DOGEUSDT" in result
        assert "DOGE-USDT" not in result

    def test_positions_oneway_mode_positive_amount(self, bingx_client):
        """Проверяет определение стороны по знаку positionAmt (One-Way mode, BOTH)."""
        response = {
            "code": 0,
            "msg": "",
            "data": [
                {
                    "symbol": "BTC-USDT",
                    "positionSide": "BOTH",
                    "positionAmt": "0.1",
                    "avgPrice": "95000",
                    "unrealizedProfit": "100",
                    "positionId": "pos_oneway_1"
                }
            ]
        }
        with patch.object(bingx_client, "make_request", return_value=response):
            result = bingx_client.get_positions()

        assert result["BTCUSDT"][0]["type"] == "buy"  # положительный amount = buy

    def test_positions_oneway_mode_negative_amount(self, bingx_client):
        """Проверяет определение стороны по отрицательному positionAmt (One-Way mode)."""
        response = {
            "code": 0,
            "msg": "",
            "data": [
                {
                    "symbol": "BTC-USDT",
                    "positionSide": "BOTH",
                    "positionAmt": "-0.1",
                    "avgPrice": "95000",
                    "unrealizedProfit": "-50",
                    "positionId": "pos_oneway_2"
                }
            ]
        }
        with patch.object(bingx_client, "make_request", return_value=response):
            result = bingx_client.get_positions()

        assert result["BTCUSDT"][0]["type"] == "sell"  # отрицательный amount = sell
        assert result["BTCUSDT"][0]["size"] == 0.1  # abs(-0.1)

    def test_positions_empty_data(self, bingx_client):
        """Проверяет обработку пустого списка позиций."""
        response = {"code": 0, "msg": "", "data": []}
        with patch.object(bingx_client, "make_request", return_value=response):
            result = bingx_client.get_positions()

        assert result == {}

    def test_positions_api_error(self, bingx_client, api_error_response):
        """Проверяет обработку ошибки API при запросе позиций."""
        with patch.object(bingx_client, "make_request", return_value=api_error_response):
            result = bingx_client.get_positions()

        assert result == {}

    def test_positions_none_response(self, bingx_client):
        """Проверяет обработку None ответа (сетевая ошибка)."""
        with patch.object(bingx_client, "make_request", return_value=None):
            result = bingx_client.get_positions()

        assert result == {}

    def test_positions_multiple_per_symbol(self, bingx_client):
        """Проверяет группировку нескольких позиций по одному символу."""
        response = {
            "code": 0,
            "msg": "",
            "data": [
                {
                    "symbol": "BTC-USDT",
                    "positionSide": "LONG",
                    "positionAmt": "0.05",
                    "avgPrice": "96000",
                    "unrealizedProfit": "100",
                    "positionId": "pos_1"
                },
                {
                    "symbol": "BTC-USDT",
                    "positionSide": "SHORT",
                    "positionAmt": "-0.03",
                    "avgPrice": "97000",
                    "unrealizedProfit": "-50",
                    "positionId": "pos_2"
                }
            ]
        }
        with patch.object(bingx_client, "make_request", return_value=response):
            result = bingx_client.get_positions()

        assert len(result["BTCUSDT"]) == 2
        assert result["BTCUSDT"][0]["type"] == "buy"
        assert result["BTCUSDT"][1]["type"] == "sell"

    def test_positions_cache(self, bingx_client, positions_response):
        """Проверяет кэширование позиций (повторный вызов не делает запрос)."""
        with patch.object(bingx_client, "make_request", return_value=positions_response) as mock_req:
            result1 = bingx_client.get_positions()
            result2 = bingx_client.get_positions()

        # make_request должен быть вызван только один раз (второй из кэша)
        assert mock_req.call_count == 1
        assert result1 == result2


# ============================================================
# Тесты: place_order — извлечение orderId
# ============================================================

class TestPlaceOrderParsing:
    """Тесты парсинга ответа place_order."""

    def test_order_id_flat_format(self, bingx_client, order_response_flat):
        """Проверяет извлечение orderId из data.orderId (плоский формат)."""
        with patch.object(bingx_client, "make_request", return_value=order_response_flat), \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            result = bingx_client.place_order("BTC/USD", "BUY", 96500, 0.01)

        assert result == "order_flat_111"

    def test_order_id_nested_format(self, bingx_client, order_response_nested):
        """Проверяет извлечение orderId из data.order.orderId (вложенный формат)."""
        with patch.object(bingx_client, "make_request", return_value=order_response_nested), \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            result = bingx_client.place_order("ETHUSDT", "SELL", 3400, 1.0)

        assert result == "order_nested_222"

    def test_order_api_error(self, bingx_client, api_error_response):
        """Проверяет что при ошибке API возвращается None."""
        with patch.object(bingx_client, "make_request", return_value=api_error_response), \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.place_order("BTC/USD", "BUY", 96500, 0.01)

        assert result is None

    def test_order_none_response(self, bingx_client):
        """Проверяет обработку None ответа (сетевая ошибка в make_request)."""
        with patch.object(bingx_client, "make_request", return_value=None), \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.place_order("BTC/USD", "BUY", 96500, 0.01)

        assert result is None

    def test_order_empty_data(self, bingx_client):
        """Проверяет обработку пустого data (orderId отсутствует)."""
        response = {"code": 0, "msg": "", "data": {}}
        with patch.object(bingx_client, "make_request", return_value=response), \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            result = bingx_client.place_order("BTC/USD", "BUY", 96500, 0.01)

        # orderId не найден — вернётся None
        assert result is None

    def test_order_with_tp_sl(self, bingx_client, order_response_flat):
        """Проверяет что TP/SL параметры передаются в запрос."""
        with patch.object(bingx_client, "make_request", return_value=order_response_flat) as mock_req, \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            bingx_client.place_order("BTC/USD", "BUY", 96500, 0.01,
                                     sl=95000.0, tp=98000.0)

        # Проверяем что TP/SL были в параметрах запроса
        call_args = mock_req.call_args
        params = call_args[1].get("params") if call_args[1] else call_args[0][2]
        assert "takeProfit" in params
        assert "stopLoss" in params
        # Проверяем JSON содержимое
        tp_data = json.loads(params["takeProfit"])
        assert tp_data["stopPrice"] == 98000.0
        sl_data = json.loads(params["stopLoss"])
        assert sl_data["stopPrice"] == 95000.0

    def test_order_position_side_buy(self, bingx_client, order_response_flat):
        """Проверяет что для BUY ордера positionSide = LONG."""
        with patch.object(bingx_client, "make_request", return_value=order_response_flat) as mock_req, \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            bingx_client.place_order("BTC/USD", "BUY", 96500, 0.01)

        call_args = mock_req.call_args
        params = call_args[1].get("params") if call_args[1] else call_args[0][2]
        assert params["positionSide"] == "LONG"
        assert params["side"] == "BUY"

    def test_order_position_side_sell(self, bingx_client, order_response_flat):
        """Проверяет что для SELL ордера positionSide = SHORT."""
        with patch.object(bingx_client, "make_request", return_value=order_response_flat) as mock_req, \
             patch.object(bingx_client, "set_leverage", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            bingx_client.place_order("BTC/USD", "SELL", 96500, 0.01)

        call_args = mock_req.call_args
        params = call_args[1].get("params") if call_args[1] else call_args[0][2]
        assert params["positionSide"] == "SHORT"
        assert params["side"] == "SELL"


# ============================================================
# Тесты: get_order_book — парсинг стакана
# ============================================================

class TestOrderBookParsing:
    """Тесты парсинга order book (стакан заявок)."""

    def test_order_book_basic(self, bingx_client, order_book_response):
        """Проверяет базовый парсинг стакана."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = order_book_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client.get_order_book("BTCUSDT")

        assert "bids" in result
        assert "asks" in result
        assert len(result["bids"]) == 3
        assert len(result["asks"]) == 3

    def test_order_book_float_conversion(self, bingx_client, order_book_response):
        """Проверяет конвертацию строк в float."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = order_book_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client.get_order_book("BTCUSDT")

        # Все значения должны быть float
        bid = result["bids"][0]
        assert isinstance(bid[0], float)
        assert isinstance(bid[1], float)
        assert bid[0] == 96750.0
        assert bid[1] == 1.5

        ask = result["asks"][0]
        assert ask[0] == 96760.0
        assert ask[1] == 1.2

    def test_order_book_api_error(self, bingx_client, api_error_response):
        """Проверяет обработку ошибки API — возвращает пустые списки."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_error_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.get_order_book("BTCUSDT")

        assert result == {"bids": [], "asks": []}

    def test_order_book_empty_data(self, bingx_client):
        """Проверяет обработку пустых bids/asks."""
        response = {"code": 0, "msg": "", "data": {"bids": [], "asks": []}}
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client.get_order_book("BTCUSDT")

        assert result["bids"] == []
        assert result["asks"] == []

    def test_order_book_network_error(self, bingx_client):
        """Проверяет обработку сетевой ошибки в get_order_book."""
        import requests as req

        with patch("requests.get", side_effect=req.exceptions.ConnectionError("timeout")), \
             patch("time.sleep"), \
             patch("src.exchanges.bingx_client.warning"):
            result = bingx_client.get_order_book("BTCUSDT")

        assert result == {"bids": [], "asks": []}

    def test_order_book_symbol_formatting(self, bingx_client, order_book_response):
        """Проверяет что символ форматируется корректно перед отправкой."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = order_book_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            bingx_client.get_order_book("BTC/USD")
            call_kwargs = mock_get.call_args
            sent_params = call_kwargs.kwargs.get("params", {}) or call_kwargs[1].get("params", {})
            assert sent_params["symbol"] == "BTC-USDT"


# ============================================================
# Тесты: get_ticker — парсинг тикера
# ============================================================

class TestTickerParsing:
    """Тесты парсинга тикера."""

    def test_ticker_basic(self, bingx_client, ticker_response):
        """Проверяет базовый парсинг тикера."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = ticker_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client.get_ticker("BTCUSDT")

        assert result["bid"] == 96750.0
        assert result["ask"] == 96760.0
        assert result["last"] == 96755.0
        assert result["volume"] == 45678.90

    def test_ticker_missing_fields(self, bingx_client):
        """Проверяет обработку отсутствующих полей (fallback на 0)."""
        response = {
            "code": 0,
            "msg": "",
            "data": {
                "lastPrice": "50000.0"
                # bestBidPrice, bestAskPrice, volume отсутствуют
            }
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client.get_ticker("BTCUSDT")

        assert result["last"] == 50000.0
        assert result["bid"] == 0
        assert result["ask"] == 0
        assert result["volume"] == 0

    def test_ticker_null_fields(self, bingx_client):
        """Проверяет обработку null значений в полях тикера."""
        response = {
            "code": 0,
            "msg": "",
            "data": {
                "bestBidPrice": None,
                "bestAskPrice": None,
                "lastPrice": None,
                "volume": None
            }
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client.get_ticker("BTCUSDT")

        # None or 0 fallback через `or 0` в коде
        assert result["bid"] == 0
        assert result["ask"] == 0
        assert result["last"] == 0
        assert result["volume"] == 0

    def test_ticker_api_error(self, bingx_client, api_error_response):
        """Проверяет что при ошибке API возвращаются нулевые значения."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_error_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.get_ticker("BTCUSDT")

        assert result == {"bid": 0, "ask": 0, "last": 0, "volume": 0}

    def test_ticker_network_error(self, bingx_client):
        """Проверяет обработку сетевой ошибки в get_ticker."""
        import requests as req

        with patch("requests.get", side_effect=req.exceptions.Timeout("timeout")), \
             patch("time.sleep"), \
             patch("src.exchanges.bingx_client.warning"):
            result = bingx_client.get_ticker("BTCUSDT")

        assert result == {"bid": 0, "ask": 0, "last": 0, "volume": 0}

    def test_ticker_symbol_formatting(self, bingx_client, ticker_response):
        """Проверяет форматирование символа для тикера."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = ticker_response
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            bingx_client.get_ticker("ETHUSDT")
            call_kwargs = mock_get.call_args
            sent_params = call_kwargs.kwargs.get("params", {}) or call_kwargs[1].get("params", {})
            assert sent_params["symbol"] == "ETH-USDT"


# ============================================================
# Тесты: close_position — закрытие позиции
# ============================================================

class TestClosePositionParsing:
    """Тесты закрытия позиции."""

    def test_close_position_not_found(self, bingx_client):
        """Проверяет обработку случая когда позиция не найдена."""
        # get_positions возвращает пустой результат
        with patch.object(bingx_client, "get_positions", return_value={}), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.close_position("BTC/USD", "nonexistent_id")

        assert result is False

    def test_close_position_zero_quantity(self, bingx_client):
        """Проверяет обработку нулевого количества при закрытии."""
        # Позиция с минимальным размером * percentage=0.0001 -> qty округлится до 0
        positions = {
            "BTCUSD": [{
                "type": "buy",
                "entry": 96500.0,
                "dealId": "pos_tiny",
                "workingOrderId": "pos_tiny",
                "created": None,
                "size": 0.0001,
                "pnl": 0
            }]
        }
        with patch.object(bingx_client, "get_positions", return_value=positions), \
             patch.object(bingx_client, "cancel_all_orders", return_value=True), \
             patch("src.exchanges.bingx_client.error"), \
             patch("src.exchanges.bingx_client.info"):
            # percentage очень маленький -> qty_to_close ~= 0
            result = bingx_client.close_position("BTC/USD", "pos_tiny", percentage=0.001)

        assert result is False

    def test_close_position_success(self, bingx_client):
        """Проверяет успешное закрытие позиции."""
        positions = {
            "BTCUSD": [{
                "type": "buy",
                "entry": 96500.0,
                "dealId": "pos_close_ok",
                "workingOrderId": "pos_close_ok",
                "created": None,
                "size": 0.05,
                "pnl": 150.0
            }]
        }
        close_response = {"code": 0, "msg": "", "data": {"orderId": "close_123"}}

        with patch.object(bingx_client, "get_positions", return_value=positions), \
             patch.object(bingx_client, "make_request", return_value=close_response), \
             patch.object(bingx_client, "cancel_all_orders", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            result = bingx_client.close_position("BTC/USD", "pos_close_ok")

        assert result is True

    def test_close_position_short(self, bingx_client):
        """Проверяет закрытие SHORT позиции (side=BUY, positionSide=SHORT)."""
        positions = {
            "ETHUSD": [{
                "type": "sell",
                "entry": 3400.0,
                "dealId": "pos_short_1",
                "workingOrderId": "pos_short_1",
                "created": None,
                "size": 2.0,
                "pnl": -20.0
            }]
        }
        close_response = {"code": 0, "msg": "", "data": {"orderId": "close_456"}}

        with patch.object(bingx_client, "get_positions", return_value=positions), \
             patch.object(bingx_client, "make_request", return_value=close_response) as mock_req, \
             patch.object(bingx_client, "cancel_all_orders", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            result = bingx_client.close_position("ETH/USD", "pos_short_1")

        assert result is True
        # Проверяем параметры запроса на закрытие SHORT
        call_args = mock_req.call_args
        params = call_args[1].get("params") if call_args[1] else call_args[0][2]
        assert params["side"] == "BUY"  # для закрытия SHORT нужен BUY
        assert params["positionSide"] == "SHORT"

    def test_close_position_api_failure(self, bingx_client):
        """Проверяет обработку ошибки API при закрытии позиции."""
        positions = {
            "BTCUSD": [{
                "type": "buy",
                "entry": 96500.0,
                "dealId": "pos_fail",
                "workingOrderId": "pos_fail",
                "created": None,
                "size": 0.05,
                "pnl": 0
            }]
        }
        error_response = {"code": 80001, "msg": "Insufficient margin", "data": None}

        with patch.object(bingx_client, "get_positions", return_value=positions), \
             patch.object(bingx_client, "make_request", return_value=error_response), \
             patch.object(bingx_client, "cancel_all_orders", return_value=True), \
             patch("src.exchanges.bingx_client.info"), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.close_position("BTC/USD", "pos_fail")

        assert result is False

    def test_close_position_partial(self, bingx_client):
        """Проверяет частичное закрытие позиции (50%)."""
        positions = {
            "BTCUSD": [{
                "type": "buy",
                "entry": 96500.0,
                "dealId": "pos_partial",
                "workingOrderId": "pos_partial",
                "created": None,
                "size": 0.10,
                "pnl": 200.0
            }]
        }
        close_response = {"code": 0, "msg": "", "data": {"orderId": "partial_123"}}

        with patch.object(bingx_client, "get_positions", return_value=positions), \
             patch.object(bingx_client, "make_request", return_value=close_response) as mock_req, \
             patch.object(bingx_client, "cancel_all_orders", return_value=True), \
             patch("src.exchanges.bingx_client.info"):
            result = bingx_client.close_position("BTC/USD", "pos_partial", percentage=0.5)

        assert result is True
        call_args = mock_req.call_args
        params = call_args[1].get("params") if call_args[1] else call_args[0][2]
        assert params["quantity"] == 0.05  # 0.10 * 0.5


# ============================================================
# Тесты: get_perpetual_balance — парсинг баланса
# ============================================================

class TestBalanceParsing:
    """Тесты парсинга баланса."""

    def test_balance_success(self, bingx_client, balance_response):
        """Проверяет успешный парсинг баланса."""
        with patch.object(bingx_client, "make_request", return_value=balance_response):
            result = bingx_client.get_perpetual_balance()

        assert result is not None
        assert result["balance"] == "10000.00"
        assert result["equity"] == "10150.50"

    def test_balance_api_error(self, bingx_client, api_error_response):
        """Проверяет обработку ошибки API при запросе баланса."""
        with patch.object(bingx_client, "make_request", return_value=api_error_response), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.get_perpetual_balance()

        assert result is None

    def test_balance_none_response(self, bingx_client):
        """Проверяет обработку None ответа."""
        with patch.object(bingx_client, "make_request", return_value=None), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.get_perpetual_balance()

        assert result is None

    def test_balance_cache(self, bingx_client, balance_response):
        """Проверяет кэширование баланса."""
        with patch.object(bingx_client, "make_request", return_value=balance_response) as mock_req:
            result1 = bingx_client.get_perpetual_balance()
            result2 = bingx_client.get_perpetual_balance()

        assert mock_req.call_count == 1
        assert result1 == result2


# ============================================================
# Тесты: _format_symbol — вспомогательная функция
# ============================================================

class TestSymbolFormatting:
    """Тесты форматирования символов."""

    def test_format_usd_suffix(self, bingx_client):
        """BTC/USD -> BTC-USDT"""
        assert bingx_client._format_symbol("BTC/USD") == "BTC-USDT"

    def test_format_usdt_suffix(self, bingx_client):
        """BTCUSDT -> BTC-USDT"""
        assert bingx_client._format_symbol("BTCUSDT") == "BTC-USDT"

    def test_format_already_correct(self, bingx_client):
        """BTC-USDT -> BTC-USDT (без изменений)"""
        assert bingx_client._format_symbol("BTC-USDT") == "BTC-USDT"

    def test_format_slash_pair(self, bingx_client):
        """ETH/USDT -> ETH-USDT"""
        assert bingx_client._format_symbol("ETH/USDT") == "ETH-USDT"

    def test_format_ethusdt(self, bingx_client):
        """ETHUSDT -> ETH-USDT"""
        assert bingx_client._format_symbol("ETHUSDT") == "ETH-USDT"

    def test_format_solusdt(self, bingx_client):
        """SOLUSDT -> SOL-USDT"""
        assert bingx_client._format_symbol("SOLUSDT") == "SOL-USDT"


# ============================================================
# Тесты: make_request — обработка ошибок
# ============================================================

class TestMakeRequest:
    """Тесты базового метода make_request."""

    def test_make_request_json_response(self, bingx_client):
        """Проверяет успешный JSON ответ."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"test": True}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = bingx_client.make_request("get", "/test/endpoint")

        assert result == {"code": 0, "data": {"test": True}}

    def test_make_request_network_retry(self, bingx_client):
        """Проверяет ретраи при сетевых ошибках."""
        import requests as req

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {}}
        mock_resp.raise_for_status = MagicMock()

        # Первые 2 попытки — ошибка, третья — успех
        with patch("requests.get", side_effect=[
            req.exceptions.ConnectionError("fail 1"),
            req.exceptions.Timeout("fail 2"),
            mock_resp
        ]), patch("time.sleep"):
            result = bingx_client.make_request("get", "/test/endpoint")

        assert result is not None
        assert result["code"] == 0

    def test_make_request_all_retries_exhausted(self, bingx_client):
        """Проверяет что после всех ретраев возвращается None."""
        import requests as req

        with patch("requests.get", side_effect=req.exceptions.ConnectionError("fail")), \
             patch("time.sleep"), \
             patch("src.exchanges.bingx_client.warning"), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.make_request("get", "/test/endpoint")

        assert result is None

    def test_make_request_post_method(self, bingx_client):
        """Проверяет POST запрос."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp):
            result = bingx_client.make_request("post", "/test/endpoint", {"key": "value"})

        assert result is not None

    def test_make_request_delete_method(self, bingx_client):
        """Проверяет DELETE запрос."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {}}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.delete", return_value=mock_resp):
            result = bingx_client.make_request("delete", "/test/endpoint")

        assert result is not None

    def test_make_request_unsupported_method(self, bingx_client):
        """Проверяет ошибку при неподдерживаемом HTTP методе."""
        with patch("src.exchanges.bingx_client.error"):
            result = bingx_client.make_request("patch", "/test/endpoint")

        assert result is None

    def test_make_request_malformed_json(self, bingx_client):
        """Проверяет обработку невалидного JSON в ответе."""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "not json"

        with patch("requests.get", return_value=mock_resp), \
             patch("src.exchanges.bingx_client.error"):
            result = bingx_client.make_request("get", "/test/endpoint")

        assert result is None


# ============================================================
# Тесты: get_kline_data — с учётом WebSocket кэша
# ============================================================

class TestGetKlineDataWithWsCache:
    """Тесты get_kline_data с логикой WS кэша."""

    def test_kline_data_falls_back_to_rest(self, bingx_client, klines_dict_response):
        """Проверяет fallback на REST когда WS кэш недоступен."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = klines_dict_response
        mock_resp.raise_for_status = MagicMock()

        # WS provider вызывает ImportError — fallback на REST
        with patch("requests.get", return_value=mock_resp), \
             patch.dict("sys.modules", {"src.exchanges.ws_data_provider": None}):
            result = bingx_client.get_kline_data("BTC/USD", "5m", 3)

        assert len(result) == 3

    def test_kline_data_uses_ws_cache(self, bingx_client):
        """Проверяет использование WS кэша когда данных достаточно."""
        ws_cached_data = [
            {"snapshotTimeUTC": "2024-01-01T00:00:00", "openPrice": 100, "closePrice": 101,
             "highPrice": 102, "lowPrice": 99, "volume": 10},
            {"snapshotTimeUTC": "2024-01-01T00:05:00", "openPrice": 101, "closePrice": 102,
             "highPrice": 103, "lowPrice": 100, "volume": 15},
        ]

        mock_ws = MagicMock()
        mock_ws.is_cache_ready = MagicMock(return_value=True)
        mock_ws.get_klines_from_shared_cache = MagicMock(return_value=ws_cached_data)

        with patch.dict("sys.modules", {"src.exchanges.ws_data_provider": mock_ws}):
            result = bingx_client.get_kline_data("BTC/USD", "5m", 2)

        # 80% от limit=2 = 1.6, у нас 2 записи >= 1.6 — используем кэш
        assert result == ws_cached_data
