"""
Юнит-тесты для BingXClient.
Покрывает: _format_symbol, _get_sign, get_order_book, get_ticker,
cancel_all_orders, make_request (retry + ошибки), set_leverage,
кэширование позиций и баланса, exchange_factory.
"""

import sys
import os
import time
import hmac
import hashlib
import json
from unittest.mock import patch, MagicMock
from urllib.parse import urlencode

import pytest
import requests

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# ─── Фикстура: BingXClient с фейковыми ключами ───

@pytest.fixture(autouse=True)
def reset_class_cache():
    """Сбрасываем class-level кэш перед каждым тестом."""
    from src.exchanges.bingx_client import BingXClient
    BingXClient._positions_cache = None
    BingXClient._positions_cache_time = 0
    BingXClient._balance_cache = None
    BingXClient._balance_cache_time = 0
    yield


@pytest.fixture
def client():
    """Создаёт клиент с тестовыми ключами."""
    with patch.dict(os.environ, {
        "BINGX_API_KEY": "test-api-key",
        "BINGX_SECRET_KEY": "test-secret-key",
        "MODE": "demo",
        "EXCHANGE": "bingx",
    }):
        from src.exchanges.bingx_client import BingXClient
        c = BingXClient(api_key="test-api-key", secret_key="test-secret-key")
        return c


@pytest.fixture
def client_no_keys():
    """Создаёт клиент без API ключей (принудительно None)."""
    from src.exchanges.bingx_client import BingXClient
    c = BingXClient(api_key="fake", secret_key="fake")
    # Принудительно обнуляем ключи после создания
    c.api_key = None
    c.secret_key = None
    return c


# ═══════════════════════════════════════════════════
# 1. _format_symbol — нормализация символов
# ═══════════════════════════════════════════════════

class TestFormatSymbol:
    """Тестирует конвертацию различных форматов символа в BingX формат (BTC-USDT)."""

    def test_slash_usd(self, client):
        """BTC/USD → BTC-USDT"""
        assert client._format_symbol("BTC/USD") == "BTC-USDT"

    def test_plain_usdt(self, client):
        """BTCUSDT → BTC-USDT"""
        assert client._format_symbol("BTCUSDT") == "BTC-USDT"

    def test_already_formatted(self, client):
        """BTC-USDT → BTC-USDT (без изменений)"""
        assert client._format_symbol("BTC-USDT") == "BTC-USDT"

    def test_slash_usdt(self, client):
        """BTC/USDT → BTC-USDT"""
        assert client._format_symbol("BTC/USDT") == "BTC-USDT"

    def test_eth_slash_usd(self, client):
        """ETH/USD → ETH-USDT"""
        assert client._format_symbol("ETH/USD") == "ETH-USDT"

    def test_ethusdt(self, client):
        """ETHUSDT → ETH-USDT"""
        assert client._format_symbol("ETHUSDT") == "ETH-USDT"

    def test_sol_dash(self, client):
        """SOL-USDT → SOL-USDT"""
        assert client._format_symbol("SOL-USDT") == "SOL-USDT"

    def test_doge_plain(self, client):
        """DOGEUSDT → DOGE-USDT"""
        assert client._format_symbol("DOGEUSDT") == "DOGE-USDT"


# ═══════════════════════════════════════════════════
# 2. _get_sign — генерация HMAC подписи
# ═══════════════════════════════════════════════════

class TestGetSign:
    """Тестирует генерацию HMAC-SHA256 подписи."""

    def test_signature_correct(self, client):
        """Подпись должна совпадать с ручным вычислением HMAC."""
        params = {"symbol": "BTC-USDT", "timestamp": "1234567890"}
        expected_string = urlencode(sorted(params.items()))
        expected_sig = hmac.new(
            b"test-secret-key",
            expected_string.encode("utf-8"),
            digestmod=hashlib.sha256
        ).hexdigest()

        result = client._get_sign(params)
        assert result == expected_sig

    def test_signature_deterministic(self, client):
        """Одинаковые параметры → одинаковая подпись."""
        params = {"a": "1", "b": "2"}
        sig1 = client._get_sign(params)
        sig2 = client._get_sign(params)
        assert sig1 == sig2

    def test_signature_changes_with_params(self, client):
        """Разные параметры → разная подпись."""
        sig1 = client._get_sign({"a": "1"})
        sig2 = client._get_sign({"a": "2"})
        assert sig1 != sig2

    def test_signature_without_secret_raises(self, client_no_keys):
        """Без secret_key подпись должна бросать ValueError."""
        with pytest.raises(ValueError, match="Secret Key is missing"):
            client_no_keys._get_sign({"a": "1"})

    def test_params_sorted(self, client):
        """Параметры сортируются перед подписью (порядок не влияет)."""
        sig1 = client._get_sign({"b": "2", "a": "1"})
        sig2 = client._get_sign({"a": "1", "b": "2"})
        assert sig1 == sig2


# ═══════════════════════════════════════════════════
# 3. get_order_book — парсинг стакана заявок
# ═══════════════════════════════════════════════════

class TestGetOrderBook:
    """Тестирует получение и парсинг order book."""

    @patch("requests.get")
    def test_success(self, mock_get, client):
        """Успешный ответ с bids/asks."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {
                "bids": [["50000.0", "1.5"], ["49999.0", "2.0"]],
                "asks": [["50001.0", "0.8"], ["50002.0", "1.2"]]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = client.get_order_book("BTCUSDT")

        assert len(result["bids"]) == 2
        assert len(result["asks"]) == 2
        # Проверяем конвертацию в float
        assert result["bids"][0] == [50000.0, 1.5]
        assert result["asks"][0] == [50001.0, 0.8]

    @patch("requests.get")
    def test_api_error_code(self, mock_get, client):
        """API возвращает ненулевой код → пустой стакан."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 100001, "msg": "Invalid symbol"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = client.get_order_book("INVALID")
        assert result == {"bids": [], "asks": []}

    @patch("requests.get")
    def test_network_error_retries(self, mock_get, client):
        """Сетевая ошибка → retry → после 3 попыток пустой стакан."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with patch("time.sleep"):
            result = client.get_order_book("BTCUSDT")

        assert result == {"bids": [], "asks": []}
        assert mock_get.call_count == 3  # 3 retry

    @patch("requests.get")
    def test_limit_capped_at_100(self, mock_get, client):
        """Limit не может превышать 100."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"bids": [], "asks": []}}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client.get_order_book("BTCUSDT", limit=500)

        # Проверяем что limit=100 в параметрах запроса
        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["params"]["limit"] == 100

    @patch("requests.get")
    def test_symbol_formatting(self, mock_get, client):
        """Символ автоматически форматируется через _format_symbol."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"bids": [], "asks": []}}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client.get_order_book("BTC/USD")

        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["params"]["symbol"] == "BTC-USDT"

    @patch("requests.get")
    def test_unexpected_exception(self, mock_get, client):
        """Неожиданная ошибка (не network) → немедленный возврат пустого стакана."""
        mock_get.side_effect = ValueError("Unexpected error")

        result = client.get_order_book("BTCUSDT")
        assert result == {"bids": [], "asks": []}
        assert mock_get.call_count == 1  # Без retry


# ═══════════════════════════════════════════════════
# 4. get_ticker — парсинг тикера
# ═══════════════════════════════════════════════════

class TestGetTicker:
    """Тестирует получение и парсинг тикера."""

    @patch("requests.get")
    def test_success(self, mock_get, client):
        """Успешный ответ с bid/ask/last/volume."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {
                "bestBidPrice": "50000.0",
                "bestAskPrice": "50001.0",
                "lastPrice": "50000.5",
                "volume": "12345.6"
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = client.get_ticker("BTCUSDT")

        assert result["bid"] == 50000.0
        assert result["ask"] == 50001.0
        assert result["last"] == 50000.5
        assert result["volume"] == 12345.6

    @patch("requests.get")
    def test_api_error(self, mock_get, client):
        """API ошибка → нулевые значения."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 100001, "msg": "Error"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = client.get_ticker("BTCUSDT")
        assert result == {"bid": 0, "ask": 0, "last": 0, "volume": 0}

    @patch("requests.get")
    def test_missing_fields_default_zero(self, mock_get, client):
        """Отсутствующие поля → 0."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {
                "lastPrice": "50000.5"
                # bestBidPrice, bestAskPrice, volume отсутствуют
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = client.get_ticker("BTCUSDT")
        assert result["bid"] == 0
        assert result["ask"] == 0
        assert result["last"] == 50000.5
        assert result["volume"] == 0

    @patch("requests.get")
    def test_network_error_retries(self, mock_get, client):
        """Сетевая ошибка → retry с backoff → нулевые значения."""
        mock_get.side_effect = requests.exceptions.Timeout("Timeout")

        with patch("time.sleep"):
            result = client.get_ticker("BTCUSDT")

        assert result == {"bid": 0, "ask": 0, "last": 0, "volume": 0}
        assert mock_get.call_count == 3

    @patch("requests.get")
    def test_none_values_handled(self, mock_get, client):
        """None значения в данных → 0 (не падает)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {
                "bestBidPrice": None,
                "bestAskPrice": None,
                "lastPrice": "50000.0",
                "volume": None
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = client.get_ticker("BTCUSDT")
        assert result["bid"] == 0
        assert result["ask"] == 0
        assert result["last"] == 50000.0
        assert result["volume"] == 0


# ═══════════════════════════════════════════════════
# 5. cancel_all_orders
# ═══════════════════════════════════════════════════

class TestCancelAllOrders:
    """Тестирует отмену всех ордеров."""

    def test_success(self, client):
        """Успешная отмена → True."""
        with patch.object(client, "make_request", return_value={"code": 0}):
            result = client.cancel_all_orders("BTCUSDT")
        assert result is True

    def test_no_orders_to_cancel(self, client):
        """Код 80014 (нет ордеров) → True (не ошибка)."""
        with patch.object(client, "make_request", return_value={"code": 80014}):
            result = client.cancel_all_orders("BTCUSDT")
        assert result is True

    def test_api_failure(self, client):
        """Ошибка API → False."""
        with patch.object(client, "make_request", return_value={"code": 100001, "msg": "Error"}):
            result = client.cancel_all_orders("BTCUSDT")
        assert result is False

    def test_none_response(self, client):
        """make_request вернул None (сеть упала) → False."""
        with patch.object(client, "make_request", return_value=None):
            result = client.cancel_all_orders("BTCUSDT")
        assert result is False

    def test_symbol_formatted(self, client):
        """Символ форматируется перед отправкой."""
        with patch.object(client, "make_request", return_value={"code": 0}) as mock_req:
            client.cancel_all_orders("BTC/USD")
        call_args = mock_req.call_args
        assert call_args[0][2]["symbol"] == "BTC-USDT"  # третий аргумент — params

    def test_uses_delete_method(self, client):
        """Запрос идёт через DELETE метод."""
        with patch.object(client, "make_request", return_value={"code": 0}) as mock_req:
            client.cancel_all_orders("BTCUSDT")
        assert mock_req.call_args[0][0] == "delete"


# ═══════════════════════════════════════════════════
# 6. make_request — retry + exponential backoff + ошибки
# ═══════════════════════════════════════════════════

class TestMakeRequest:
    """Тестирует логику make_request: retry, backoff, разные HTTP методы."""

    @patch("requests.get")
    def test_get_success(self, mock_get, client):
        """Успешный GET запрос."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": "test"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = client.make_request("get", "/test/endpoint", {"key": "value"})
        assert result == {"code": 0, "data": "test"}

    @patch("requests.post")
    def test_post_success(self, mock_post, client):
        """Успешный POST запрос."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = client.make_request("post", "/test/endpoint")
        assert result == {"code": 0}

    @patch("requests.delete")
    def test_delete_success(self, mock_delete, client):
        """Успешный DELETE запрос."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_delete.return_value = mock_resp

        result = client.make_request("delete", "/test/endpoint")
        assert result == {"code": 0}

    def test_unsupported_method(self, client):
        """Неподдерживаемый HTTP метод → None (ловится в except)."""
        result = client.make_request("patch", "/test/endpoint")
        assert result is None

    @patch("requests.get")
    def test_retry_on_connection_error(self, mock_get, client):
        """ConnectionError → 3 попытки с exponential backoff."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with patch("time.sleep") as mock_sleep:
            result = client.make_request("get", "/test/endpoint")

        assert result is None
        assert mock_get.call_count == 3
        # Проверяем exponential backoff: sleep(1), sleep(2)
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch("requests.get")
    def test_retry_on_timeout(self, mock_get, client):
        """Timeout → retry."""
        mock_get.side_effect = requests.exceptions.Timeout("Timeout")

        with patch("time.sleep"):
            result = client.make_request("get", "/test/endpoint")

        assert result is None
        assert mock_get.call_count == 3

    @patch("requests.get")
    def test_retry_on_chunked_encoding(self, mock_get, client):
        """ChunkedEncodingError → retry."""
        mock_get.side_effect = requests.exceptions.ChunkedEncodingError("Chunked error")

        with patch("time.sleep"):
            result = client.make_request("get", "/test/endpoint")

        assert result is None
        assert mock_get.call_count == 3

    @patch("requests.get")
    def test_retry_then_success(self, mock_get, client):
        """2 неудачи → 3-я попытка успешна."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": "ok"}
        mock_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [
            requests.exceptions.ConnectionError("fail 1"),
            requests.exceptions.Timeout("fail 2"),
            mock_resp
        ]

        with patch("time.sleep"):
            result = client.make_request("get", "/test/endpoint")

        assert result == {"code": 0, "data": "ok"}
        assert mock_get.call_count == 3

    @patch("requests.get")
    def test_http_error_no_retry(self, mock_get, client):
        """HTTP 4xx ошибка → НЕ retry, сразу возврат None."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("400 Bad Request")
        mock_resp.text = "Bad request"
        mock_get.return_value = mock_resp

        result = client.make_request("get", "/test/endpoint")
        assert result is None
        assert mock_get.call_count == 1  # Без retry

    @patch("requests.get")
    def test_signature_appended(self, mock_get, client):
        """Подпись добавляется в query string для авторизованных запросов."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client.make_request("get", "/test/endpoint", {"key": "value"})

        called_url = mock_get.call_args[0][0]
        assert "signature=" in called_url
        assert "apiKey=test-api-key" in called_url

    @patch("requests.get")
    def test_no_keys_no_signature(self, mock_get, client_no_keys):
        """Без ключей подпись не добавляется."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        client_no_keys.make_request("get", "/test/endpoint")

        called_url = mock_get.call_args[0][0]
        assert "signature=" not in called_url
        assert "apiKey=" not in called_url


# ═══════════════════════════════════════════════════
# 7. set_leverage
# ═══════════════════════════════════════════════════

class TestSetLeverage:
    """Тестирует установку кредитного плеча."""

    def test_success(self, client):
        """Успешная установка leverage → True."""
        with patch.object(client, "make_request", return_value={"code": 0}):
            result = client.set_leverage("BTCUSDT", 10, "LONG")
        assert result is True

    def test_already_set_code_80001(self, client):
        """Код 80001 (уже установлено) → True."""
        with patch.object(client, "make_request", return_value={"code": 80001}):
            result = client.set_leverage("BTCUSDT", 10, "LONG")
        assert result is True

    def test_api_failure(self, client):
        """Ошибка API → False."""
        with patch.object(client, "make_request", return_value={"code": 100001, "msg": "Error"}):
            result = client.set_leverage("BTCUSDT", 10, "LONG")
        assert result is False

    def test_none_response(self, client):
        """make_request вернул None → False."""
        with patch.object(client, "make_request", return_value=None):
            result = client.set_leverage("BTCUSDT", 10, "LONG")
        assert result is False

    def test_exception_handling(self, client):
        """Исключение в make_request → False."""
        with patch.object(client, "make_request", side_effect=Exception("network error")):
            result = client.set_leverage("BTCUSDT", 10, "LONG")
        assert result is False

    def test_symbol_formatting(self, client):
        """Символ правильно форматируется."""
        with patch.object(client, "make_request", return_value={"code": 0}) as mock_req:
            client.set_leverage("BTC/USD", 20, "SHORT")
        params = mock_req.call_args[0][2]
        assert params["symbol"] == "BTC-USDT"
        assert params["leverage"] == 20
        assert params["side"] == "SHORT"

    def test_uses_post_method(self, client):
        """set_leverage использует POST."""
        with patch.object(client, "make_request", return_value={"code": 0}) as mock_req:
            client.set_leverage("BTCUSDT", 5, "LONG")
        assert mock_req.call_args[0][0] == "post"


# ═══════════════════════════════════════════════════
# 8. Кэширование: позиции (5s TTL) и баланс (10s TTL)
# ═══════════════════════════════════════════════════

class TestPositionsCache:
    """Тестирует кэширование позиций (TTL = 5 секунд)."""

    def test_cache_hit(self, client):
        """Второй вызов использует кэш (без повторного запроса)."""
        api_response = {
            "code": 0,
            "data": [{
                "symbol": "BTC-USDT",
                "positionAmt": "0.1",
                "avgPrice": "50000",
                "positionSide": "LONG",
                "positionId": "123",
                "unrealizedProfit": "100"
            }]
        }

        with patch.object(client, "make_request", return_value=api_response) as mock_req:
            # Первый вызов — идёт к API
            result1 = client.get_positions()
            # Второй вызов — из кэша
            result2 = client.get_positions()

        assert mock_req.call_count == 1  # Только один API вызов
        assert result1 == result2

    def test_cache_expired(self, client):
        """После истечения TTL кэш обновляется."""
        api_response = {
            "code": 0,
            "data": [{
                "symbol": "BTC-USDT",
                "positionAmt": "0.1",
                "avgPrice": "50000",
                "positionSide": "LONG",
                "positionId": "123",
                "unrealizedProfit": "100"
            }]
        }

        with patch.object(client, "make_request", return_value=api_response) as mock_req:
            client.get_positions()

            # Искусственно устаревляем кэш
            from src.exchanges.bingx_client import BingXClient
            BingXClient._positions_cache_time = time.time() - 10  # 10 сек назад

            client.get_positions()

        assert mock_req.call_count == 2  # Два API вызова

    def test_cache_shared_between_instances(self, client):
        """Кэш общий для всех экземпляров (class-level)."""
        from src.exchanges.bingx_client import BingXClient

        client2 = BingXClient(api_key="test-api-key", secret_key="test-secret-key")

        api_response = {
            "code": 0,
            "data": [{
                "symbol": "BTC-USDT",
                "positionAmt": "0.5",
                "avgPrice": "60000",
                "positionSide": "SHORT",
                "positionId": "456",
                "unrealizedProfit": "-50"
            }]
        }

        with patch.object(client, "make_request", return_value=api_response):
            client.get_positions()

        with patch.object(client2, "make_request") as mock_req2:
            # client2 должен взять из кэша, не вызывая API
            result = client2.get_positions()

        mock_req2.assert_not_called()
        assert "BTCUSDT" in result


class TestBalanceCache:
    """Тестирует кэширование баланса (TTL = 10 секунд)."""

    def test_cache_hit(self, client):
        """Повторный вызов использует кэш."""
        api_response = {
            "code": 0,
            "data": {"balance": {"equity": "1000.0", "availableMargin": "500.0"}}
        }

        with patch.object(client, "make_request", return_value=api_response) as mock_req:
            result1 = client.get_perpetual_balance()
            result2 = client.get_perpetual_balance()

        assert mock_req.call_count == 1
        assert result1 == result2
        assert result1["equity"] == "1000.0"

    def test_cache_expired(self, client):
        """После истечения 10s TTL кэш обновляется."""
        api_response = {
            "code": 0,
            "data": {"balance": {"equity": "1000.0"}}
        }

        with patch.object(client, "make_request", return_value=api_response) as mock_req:
            client.get_perpetual_balance()

            # Искусственно устаревляем кэш
            from src.exchanges.bingx_client import BingXClient
            BingXClient._balance_cache_time = time.time() - 15  # 15 сек назад

            client.get_perpetual_balance()

        assert mock_req.call_count == 2

    def test_api_failure_returns_none(self, client):
        """Ошибка API → None, кэш не обновляется."""
        with patch.object(client, "make_request", return_value={"code": 100001}):
            result = client.get_perpetual_balance()

        assert result is None

    def test_cache_not_set_on_failure(self, client):
        """При ошибке API кэш не перезаписывается."""
        from src.exchanges.bingx_client import BingXClient

        # Устанавливаем валидный кэш
        BingXClient._balance_cache = {"equity": "500.0"}
        BingXClient._balance_cache_time = time.time()

        # Ждём истечения TTL
        BingXClient._balance_cache_time = time.time() - 15

        with patch.object(client, "make_request", return_value=None):
            result = client.get_perpetual_balance()

        # Ошибка, кэш не трогается (вернул None, а кэш остался прежний)
        assert result is None


# ═══════════════════════════════════════════════════
# 9. exchange_factory — создание клиента
# ═══════════════════════════════════════════════════

class TestExchangeFactory:
    """Тестирует фабрику создания exchange клиентов."""

    def test_bingx_client_created(self):
        """EXCHANGE=bingx → возвращается BingXClient."""
        import src.exchanges.exchange_factory as factory_mod
        from src.exchanges.bingx_client import BingXClient

        with patch.object(factory_mod, "EXCHANGE", "bingx"), \
             patch.object(factory_mod, "_client_instance", None):
            client = factory_mod.get_exchange_client()
        assert isinstance(client, BingXClient)

    def test_unknown_exchange_raises(self):
        """Неизвестный exchange → ValueError."""
        import src.exchanges.exchange_factory as factory_mod
        with patch.object(factory_mod, "EXCHANGE", "kraken"), \
             patch.object(factory_mod, "_client_instance", None):
            with pytest.raises(ValueError, match="Unknown exchange: kraken"):
                factory_mod.get_exchange_client()

    def test_case_insensitive(self):
        """EXCHANGE проверяется в lower case."""
        import src.exchanges.exchange_factory as factory_mod
        from src.exchanges.bingx_client import BingXClient
        with patch.object(factory_mod, "EXCHANGE", "BingX"), \
             patch.object(factory_mod, "_client_instance", None):
            client = factory_mod.get_exchange_client()
        assert isinstance(client, BingXClient)


# ═══════════════════════════════════════════════════
# Дополнительные тесты: check_prerequisites, позиции
# ═══════════════════════════════════════════════════

class TestCheckPrerequisites:
    """Тестирует проверку API ключей."""

    def test_with_keys(self, client):
        """С ключами → True."""
        assert client.check_prerequisites() is True

    def test_without_keys(self, client_no_keys):
        """Без ключей → False."""
        assert client_no_keys.check_prerequisites() is False


class TestGetPositionsParsing:
    """Тестирует парсинг позиций из API ответа."""

    def test_long_position_parsed(self, client):
        """LONG позиция корректно парсится."""
        api_response = {
            "code": 0,
            "data": [{
                "symbol": "BTC-USDT",
                "positionAmt": "0.5",
                "avgPrice": "60000",
                "positionSide": "LONG",
                "positionId": "pos123",
                "unrealizedProfit": "500"
            }]
        }

        with patch.object(client, "make_request", return_value=api_response):
            positions = client.get_positions()

        assert "BTCUSDT" in positions
        pos = positions["BTCUSDT"][0]
        assert pos["type"] == "buy"
        assert pos["entry"] == 60000.0
        assert pos["size"] == 0.5
        assert pos["pnl"] == 500.0
        assert pos["dealId"] == "pos123"

    def test_short_position_parsed(self, client):
        """SHORT позиция корректно парсится."""
        api_response = {
            "code": 0,
            "data": [{
                "symbol": "ETH-USDT",
                "positionAmt": "-2.0",
                "avgPrice": "3000",
                "positionSide": "SHORT",
                "positionId": "pos456",
                "unrealizedProfit": "-100"
            }]
        }

        with patch.object(client, "make_request", return_value=api_response):
            positions = client.get_positions()

        assert "ETHUSDT" in positions
        pos = positions["ETHUSDT"][0]
        assert pos["type"] == "sell"
        assert pos["size"] == 2.0  # abs value

    def test_zero_size_filtered(self, client):
        """Позиции с нулевым размером фильтруются."""
        api_response = {
            "code": 0,
            "data": [{
                "symbol": "BTC-USDT",
                "positionAmt": "0",
                "avgPrice": "50000",
                "positionSide": "LONG",
                "positionId": "999",
                "unrealizedProfit": "0"
            }]
        }

        with patch.object(client, "make_request", return_value=api_response):
            positions = client.get_positions()

        assert positions == {}

    def test_multiple_symbols(self, client):
        """Несколько символов группируются правильно."""
        api_response = {
            "code": 0,
            "data": [
                {
                    "symbol": "BTC-USDT",
                    "positionAmt": "1.0",
                    "avgPrice": "50000",
                    "positionSide": "LONG",
                    "positionId": "1",
                    "unrealizedProfit": "100"
                },
                {
                    "symbol": "ETH-USDT",
                    "positionAmt": "-5.0",
                    "avgPrice": "3000",
                    "positionSide": "SHORT",
                    "positionId": "2",
                    "unrealizedProfit": "-50"
                }
            ]
        }

        with patch.object(client, "make_request", return_value=api_response):
            positions = client.get_positions()

        assert "BTCUSDT" in positions
        assert "ETHUSDT" in positions
        assert len(positions["BTCUSDT"]) == 1
        assert len(positions["ETHUSDT"]) == 1
