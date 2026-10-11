"""BingX Swap: строгие ответы, isolated-only и явное владение торговыми объектами."""

import copy
import hashlib
import hmac

import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Optional
from urllib.parse import quote, urlencode

import requests

from src.utils.logger import error

from ..config.base import ExchangeConfig
from ..exchange_client import ExchangeClient
from ..rate_limit import get_exchange_rate_limiters
from ..dto.models import (
    Balance, CommissionRate, ExchangeCapabilities, FundingRate, InstrumentRules,
    Kline, MarketType, Order, OrderBook, OrderSide, OrderStatus, OrderType,
    Position, PositionSide, Ticker,
)
from ..errors import (
    ExchangeAPIError, ExchangeError, ExchangeStateUnavailableError,
    UnknownOrderStateError, UnsupportedCapabilityError,
)


_RETRY_HTTP = {429, 500, 502, 503, 504}
_RETRY_API = _RETRY_HTTP | {100500, 109429, 109500}
_ACTIVE = {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL}
_INTERVALS = {"MINUTE_1": "1m", "MINUTE_3": "3m", "MINUTE_5": "5m",
              "MINUTE_15": "15m", "MINUTE_30": "30m", "HOUR_1": "1h",
              "HOUR_2": "2h", "HOUR_4": "4h", "HOUR_6": "6h",
              "HOUR_8": "8h", "HOUR_12": "12h", "DAY_1": "1d",
              "DAY_3": "3d", "WEEK_1": "1w", "MONTH_1": "1M"}


def _fail(cls, message, **context):
    # Контекст нужен журналу UNKNOWN; ключи и подписи сюда никогда не попадают.
    error("❌ %s", message)
    exc = cls(message)
    for key, value in context.items():
        setattr(exc, key, value)
    raise exc


def _decimal(value, field="число", positive=False):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal("1e308") or (positive and result <= 0):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError):
        _fail(ExchangeStateUnavailableError, f"BingX: невалидное {field}")


def _integer(value, field):
    number = _decimal(value, field)
    if number != number.to_integral_value():
        _fail(ExchangeStateUnavailableError, f"BingX: {field} должно быть целым")
    return int(number)


def _boolean(value, field):
    if value is True or str(value).lower() in {"true", "1"}:
        return True
    if value is False or str(value).lower() in {"false", "0"}:
        return False
    _fail(ExchangeStateUnavailableError, f"BingX: неизвестное {field}")


def _step(value, step, rounding=ROUND_FLOOR):
    return (value / step).to_integral_value(rounding=rounding) * step


def _timestamp(value):
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(float(_decimal(value, "timestamp")) / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        _fail(ExchangeStateUnavailableError, "BingX: timestamp вне допустимого диапазона")


def _enum(cls, value):
    try:
        return value if isinstance(value, cls) else cls(str(value).upper())
    except ValueError:
        _fail(ExchangeStateUnavailableError, f"BingX: неизвестное {cls.__name__}")



class BingXClient(ExchangeClient):
    EXCHANGE_NAME = "bingx"

    def __init__(self, config: Optional[ExchangeConfig] = None, *, trading_settings=None,
                 submission_observer=None, api_key=None, secret_key=None, rate_limiter=None):
        if config is None:
            from ..config.bingx_config import BingXConfig
            config = BingXConfig(trading_settings=trading_settings)
        self._config = config
        self._settings_override = copy.deepcopy(trading_settings)
        self._initial_settings = self._settings()
        self.api_key = config.api_key if api_key is None else api_key
        self.secret_key = config.secret_key if secret_key is None else secret_key
        self._credentials = (self.api_key, self.secret_key)
        self.base_url = ("https://open-api-vst.bingx.com"
                         if self._initial_settings["execution_mode"] == "demo"
                         else "https://open-api.bingx.com")
        self._private_base_url = self.base_url
        self.market_base_url = os.getenv("BINGX_MARKET_API_URL", "https://open-api.bingx.com")
        self._cache_lock = threading.RLock()
        self._trade_lock = threading.RLock()
        self._generation = 0
        self._positions_cache = None
        self._positions_cache_time = 0.0
        self._positions_cache_ttl = config.positions_cache_ttl
        self._balance_cache = None
        self._balance_cache_time = 0.0
        self._position_details = {}
        self._instrument_cache = {}
        self._instrument_details = {}
        self._owned_orders = {}
        self._owned_order_positions = {}
        self._submission_observer = submission_observer
        self._owned_positions = {}
        self._owned_position_sides = {}
        self._intents = {}
        self._commands = {}
        self._uncertain_symbols = set()
        self._uncertain_requests = {}
        self._prepared_submissions = []
        self.last_protection_confirmed = False
        self.last_submission = None
        self._time_offset_ms = 0
        self._clock_lock = threading.RLock()
        self._rate_limiter = rate_limiter

    def _settings(self):
        supplied = getattr(self, "_settings_override", None)
        if supplied is None:
            supplied = getattr(self._config, "trading_settings", None)
        if supplied is None:
            supplied = {"execution_mode": "demo" if self._config.is_demo else "read_only"}
        if not isinstance(supplied, dict):
            _fail(ExchangeStateUnavailableError, "BingX: trading_settings должен быть словарём")
        settings = {"exchange": "bingx", "market_type": "perpetual", "execution_mode": "read_only",
                    "margin_mode": "isolated", "position_mode": "hedge", "settle_asset": "USDT",
                    "protection": {"trigger_price": "mark", "failure_policy": "pause"}}
        settings.update(copy.deepcopy(supplied))
        if settings["exchange"] != "bingx":
            _fail(UnsupportedCapabilityError, "BingX: настройки принадлежат другой бирже")
        if settings["margin_mode"] != "isolated":
            _fail(UnsupportedCapabilityError, "BingX: Cross запрещён")
        if settings["position_mode"] not in {"hedge", "one_way"}:
            _fail(UnsupportedCapabilityError, "BingX: неизвестный position_mode")
        if settings["execution_mode"] not in {"read_only", "demo", "live"}:
            _fail(UnsupportedCapabilityError, "BingX: неизвестный execution_mode")
        if settings["market_type"] != "perpetual" or settings["settle_asset"] not in {"USDT", "USDC"}:
            _fail(UnsupportedCapabilityError, "BingX Swap поддерживает только USDT/USDC perpetual")
        protection = settings.get("protection")
        if (not isinstance(protection, dict)
                or protection.get("trigger_price", "mark") not in {"last", "mark"}
                or protection.get("failure_policy", "pause") not in {"close", "pause"}):
            _fail(UnsupportedCapabilityError, "BingX: неизвестный источник цены защиты")
        return settings

    @property
    def capabilities(self):
        # Защита создаётся отдельными адресными ордерами, а не угадывается среди ручных children.
        return ExchangeCapabilities(market_type=MarketType.PERPETUAL, positions=True,
                                    shorting=True, leverage=True, funding=True,
                                    native_protection=True, attached_protection=True,
                                    automated_strategy=True)

    def _allow_mutation(self):
        current = self._settings()
        if current["execution_mode"] == "read_only" or current["exchange"] != "bingx":
            _fail(UnsupportedCapabilityError, "BingX: торговые мутации запрещены настройками")
        for key in ("exchange", "market_type", "execution_mode", "position_mode", "settle_asset"):
            if current[key] != self._initial_settings[key]:
                _fail(ExchangeStateUnavailableError, f"BingX: изменилось {key}; пересоздайте клиент")
        expected = "https://open-api-vst.bingx.com" if current["execution_mode"] == "demo" else "https://open-api.bingx.com"
        if self.base_url != expected:
            _fail(UnsupportedCapabilityError, "BingX: endpoint не соответствует execution_mode")

    @staticmethod
    def _envelope(response):
        if not isinstance(response, dict) or "code" not in response or isinstance(response["code"], bool):
            _fail(ExchangeStateUnavailableError, "BingX: повреждён envelope ответа")
        code = _integer(response["code"], "code")
        if code != 0:
            from src.utils.log_context import SecretRedactor
            message = SecretRedactor().text(str(response.get("msg", "")))[:1024]
            error("❌ BingX: API отклонил запрос, code=%s причина=%s", code, message)
            raise ExchangeAPIError(f"BingX API: {message}", code=code,
                                   retryable=code in _RETRY_API)
        return response

    @classmethod
    def _data(cls, response, expected=dict):
        cls._envelope(response)
        data = response.get("data")
        if not isinstance(data, expected):
            _fail(ExchangeStateUnavailableError, "BingX: отсутствует или невалиден data ответа")
        return data

    def _signed_payload(self, method, params):
        payload = {k: v for k, v in params.items() if k not in {"apiKey", "signature", "timestamp"}}
        payload.setdefault("recvWindow", 5000)
        payload["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        values = [(k, format(v, "f") if isinstance(v, Decimal) else str(v)) for k, v in sorted(payload.items())]
        if method == "post":
            unsigned = urlencode(values)
            wire = unsigned
        else:
            # Query подписывается до экранирования, form-body — после него.
            unsigned = "&".join(f"{k}={v}" for k, v in values)
            wire = "&".join(f"{k}={quote(v, safe='')}" for k, v in values)
        signature = hmac.new(self.secret_key.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
        return f"{wire}&signature={signature}", {"X-BX-APIKEY": self.api_key}

    def _publish_submission(self, event, *, submitted=False):
        if event.get("status") == "PREPARED":
            self._prepared_submissions.append(copy.deepcopy(event))
        self.last_submission = copy.deepcopy(event)
        if self._submission_observer is None:
            return
        try:
            self._submission_observer(copy.deepcopy(event))
        except Exception:
            if submitted:
                self._unknown("BingX: отправленная команда не записана в durable журнал",
                              event["request_params"], order_id=event.get("order_id"))
            _fail(ExchangeStateUnavailableError, "BingX: durable журнал не подтвердил PREPARED",
                  submission_status="NOT_SENT", request_params=copy.deepcopy(event["request_params"]),
                  client_order_id=event.get("client_order_id"), symbol=event.get("symbol"))

    @staticmethod
    def _timestamp_rejection(exc):
        # 109400/80014 используются и для других отказов: одного кода недостаточно.
        message = str(exc).lower()
        return (exc.code in {109400, 80014} and "timestamp" in message
                and any(text in message for text in ("recvwindow", "ahead", "behind", "expired")))

    def _sync_server_clock(self):
        """Один unsigned GET к тому же private host, без retries и торговых мутаций."""
        with self._clock_lock:
            try:
                self._rate_limiter.acquire()
                started = time.monotonic()
                local_ms = time.time() * 1000
                response = requests.get(self.base_url + "/openApi/swap/v2/server/time",
                                        timeout=min(self._config.request_timeout, 5), allow_redirects=False)
                elapsed_ms = (time.monotonic() - started) * 1000
                if response.status_code != 200:
                    raise ExchangeStateUnavailableError("BingX: время сервера недоступно")
                server_ms = _integer(self._data(response.json()).get("serverTime"), "serverTime")
                if (not 0 <= elapsed_ms <= 2000 or not 10 ** 12 <= server_ms < 253402300800000
                        or abs(time.time() * 1000 - local_ms - elapsed_ms) > 1000):
                    raise ExchangeStateUnavailableError("BingX: ненадёжный снимок времени сервера")
                self._time_offset_ms = round(server_ms - local_ms - elapsed_ms / 2)
                return True
            except (ExchangeError, requests.RequestException, ValueError):
                # Исходный timestamp-отказ остаётся отказом даже при ошибке синхронизации.
                error("❌ BingX: синхронизация времени не подтверждена")
                return False

    def _request(self, method, endpoint, params=None, *, public=False):
        method = method.lower()
        if method not in {"get", "post", "delete"}:
            _fail(UnsupportedCapabilityError, "BingX: неподдерживаемый HTTP метод")
        mutation = method != "get"
        if public and mutation:
            _fail(UnsupportedCapabilityError, "BingX: публичный транспорт допускает только GET")
        if mutation:
            self._allow_mutation()
            if (method, endpoint) not in {("post", "/openApi/swap/v2/trade/order"),
                                          ("delete", "/openApi/swap/v2/trade/order"),
                                          ("post", "/openApi/swap/v2/trade/leverage"),
                                          ("post", "/openApi/swap/v1/trade/cancelReplace"),
                                          ("post", "/openApi/user/auth/userDataStream")}:
                _fail(UnsupportedCapabilityError, "BingX: смена режима и массовые мутации запрещены")
        if not public:
            if self.base_url != self._private_base_url or (self.api_key, self.secret_key) != self._credentials:
                _fail(ExchangeStateUnavailableError, "BingX: endpoint/ключи изменились; пересоздайте клиент")
            if not self.api_key or not self.secret_key:
                _fail(ExchangeStateUnavailableError, "BingX: API ключи отсутствуют")
        params = dict(params or {})
        attempts = 1 if mutation else 3
        timeout = self._config.request_timeout
        clock_sync_attempted = False
        for attempt in range(attempts):
            response = None
            retry_delay = min(2 ** attempt, 30)
            try:
                if self._rate_limiter is None:
                    try:
                        self._rate_limiter = get_exchange_rate_limiters("bingx")["private"]
                    except ExchangeStateUnavailableError as exc:
                        exc.submission_status = "NOT_SENT"
                        raise
                if mutation:
                    event = {"status": "PREPARED", "submission_id": uuid.uuid4().hex,
                             "method": method, "endpoint": endpoint,
                             "request_params": copy.deepcopy(params), "symbol": params.get("symbol"),
                             "client_order_id": params.get("clientOrderId"),
                             "execution_mode": self._initial_settings["execution_mode"]}
                    # Durable проверка может сама читать API; её GET тоже расходуют budget.
                    self._publish_submission(event)
                try:
                    # Резервируем именно отправляемый HTTP после observer, включая все leverage checkpoints.
                    self._rate_limiter.acquire()
                except ExchangeStateUnavailableError as exc:
                    exc.submission_status = "NOT_SENT"
                    raise
                url = (self.market_base_url if public else self.base_url) + endpoint
                if public:
                    response = requests.get(url, params=params, timeout=timeout, allow_redirects=False)
                else:
                    wire, headers = self._signed_payload(method, params)
                    if method == "post":
                        headers["Content-Type"] = "application/x-www-form-urlencoded"
                        response = requests.post(url, data=wire, headers=headers, timeout=timeout, allow_redirects=False)
                    else:
                        response = getattr(requests, method)(f"{url}?{wire}", headers=headers, timeout=timeout, allow_redirects=False)
                status = response.status_code
                if 300 <= status < 400:
                    # Redirect не доказывает отказ мутации; Location и подписанный URL не логируем.
                    if mutation:
                        _fail(UnknownOrderStateError, f"BingX: результат мутации неизвестен, HTTP {status}",
                              submission_status="UNKNOWN", method=method, endpoint=endpoint,
                              symbol=params.get("symbol"), client_order_id=params.get("clientOrderId"),
                              request_params=copy.deepcopy(params), http_status=status)
                    _fail(ExchangeStateUnavailableError, f"BingX: перенаправление чтения запрещено, HTTP {status}",
                          method=method, endpoint=endpoint, http_status=status)
                if status in _RETRY_HTTP:
                    if status == 429 and callable(getattr(self._rate_limiter, "cooldown", None)):
                        try:
                            cooldown = _decimal(response.headers.get("Retry-After", retry_delay), "Retry-After")
                            if not 0 <= cooldown <= 31536000:
                                cooldown = Decimal(retry_delay)
                        except ExchangeStateUnavailableError:
                            cooldown = Decimal(retry_delay)
                        self._rate_limiter.cooldown(float(cooldown))
                    if mutation:
                        _fail(UnknownOrderStateError, f"BingX: результат мутации неизвестен, HTTP {status}",
                                                      submission_status="UNKNOWN", method=method, endpoint=endpoint,
                                                      symbol=params.get("symbol"), client_order_id=params.get("clientOrderId"),
                                                      request_params=copy.deepcopy(params))
                    try:
                        retry_delay = max(0, min(30, float(response.headers.get("Retry-After", retry_delay))))
                    except (ValueError, TypeError):
                        pass
                    raise ExchangeAPIError(f"BingX: HTTP {status}", code=status, retryable=True)
                if status >= 400:
                    # Даже HTTP 4xx может содержать полезную бизнес-ошибку.
                    try:
                        self._envelope(response.json())
                    except (ValueError, ExchangeStateUnavailableError):
                        pass
                    raise ExchangeAPIError(f"BingX: HTTP {status}", code=status)
                try:
                    result = response.json()
                    if method == "post" and endpoint == "/openApi/user/auth/userDataStream":
                        from src.core.strategies.grid.native_stream import BingXListenKey
                        # VST возвращает top-level listenKey без code/data; исключение только для этого endpoint.
                        if isinstance(result, dict) and "code" in result:
                            self._envelope({"code": result["code"]})
                        return BingXListenKey.from_response(result)
                    return self._envelope(result)
                except (ValueError, ExchangeStateUnavailableError) as exc:
                    if mutation:
                        _fail(UnknownOrderStateError, "BingX: мутация получила невалидный ответ",
                                                      submission_status="UNKNOWN", method=method, endpoint=endpoint,
                                                      symbol=params.get("symbol"), client_order_id=params.get("clientOrderId"),
                                                      request_params=copy.deepcopy(params))
                    raise ExchangeStateUnavailableError("BingX: невалидный ответ чтения") from exc
            except ExchangeAPIError as exc:
                if not public and not clock_sync_attempted and self._timestamp_rejection(exc):
                    clock_sync_attempted = True
                    synced = self._sync_server_clock()
                    if synced and not mutation and attempt < attempts - 1:
                        continue
                if mutation and exc.retryable:
                    _fail(UnknownOrderStateError, f"BingX: неоднозначная ошибка мутации, code={exc.code}",
                                              submission_status="UNKNOWN", method=method, endpoint=endpoint,
                                              symbol=params.get("symbol"), client_order_id=params.get("clientOrderId"),
                                              request_params=copy.deepcopy(params))
                if not exc.retryable or attempt == attempts - 1:
                    error("❌ BingX: запрос завершился ошибкой, endpoint=%s code=%s", endpoint, exc.code)
                    raise
            except (requests.RequestException, ValueError) as exc:
                # Не логируем str(exc): requests может включать в него подписанный URL.
                if mutation:
                    _fail(UnknownOrderStateError, "BingX: ответ торговой мутации потерян",
                                              submission_status="UNKNOWN", method=method, endpoint=endpoint,
                                              symbol=params.get("symbol"), client_order_id=params.get("clientOrderId"),
                                              request_params=copy.deepcopy(params))
                if attempt == attempts - 1:
                    _fail(ExchangeStateUnavailableError, f"BingX: чтение {endpoint} недоступно")
            finally:
                if mutation:
                    self.invalidate_cache()
            time.sleep(retry_delay)
        _fail(ExchangeStateUnavailableError, "BingX: чтение не завершилось")

    def _make_request(self, method, endpoint, params=None):
        # Внутренний транспорт; legacy raw mutation намеренно не экспортируется.
        return self._request(method, endpoint, params)

    def _public_request(self, endpoint, params=None):
        return self._request("get", endpoint, params, public=True)

    def invalidate_cache(self, cache_type=None):
        with self._cache_lock:
            self._generation += 1
            self._positions_cache = None
            self._positions_cache_time = 0
            # Метаданные последнего снимка нужны текущей команде; следующий reader обязан обновить позиции.
            self._balance_cache = None
            self._balance_cache_time = 0

    def normalize_symbol(self, symbol):
        return self._format_symbol(symbol).replace("-", "")

    def denormalize_symbol(self, symbol):
        return self.normalize_symbol(symbol)

    def _format_symbol(self, symbol):
        if not isinstance(symbol, str):
            _fail(ExchangeStateUnavailableError, "BingX: symbol должен быть строкой")
        value = symbol.upper().replace("/", "-").replace("_", "-")
        if "-" not in value:
            for suffix in ("USDT", "USDC"):
                if value.endswith(suffix):
                    value = value[:-len(suffix)] + "-" + suffix
                    break
        if not re.fullmatch(r"[A-Z0-9]+-(USDT|USDC)", value):
            _fail(UnsupportedCapabilityError, "BingX: нужен символ вида BTC-USDT или BTC-USDC")
        return value

    def get_instrument_rules(self, symbol):
        exchange_symbol = self._format_symbol(symbol)
        with self._cache_lock:
            cached = self._instrument_cache.get(exchange_symbol)
            if cached and time.monotonic() - cached[0] < 60:
                return cached[1]
        items = self._data(self._public_request("/openApi/swap/v2/quote/contracts", {"symbol": exchange_symbol}), list)
        item = next((i for i in items if isinstance(i, dict) and i.get("symbol") == exchange_symbol), None)
        required = {"pricePrecision", "quantityPrecision", "tradeMinQuantity", "tradeMinUSDT", "status", "apiStateOpen", "apiStateClose"}
        if item is None or not required.issubset(item):
            _fail(ExchangeStateUnavailableError, f"BingX: нет торговых правил {exchange_symbol}")
        price_precision = _integer(item["pricePrecision"], "pricePrecision")
        quantity_precision = _integer(item["quantityPrecision"], "quantityPrecision")
        if not (0 <= price_precision <= 18 and 0 <= quantity_precision <= 18):
            _fail(ExchangeStateUnavailableError, "BingX: неверная precision инструмента")
        rules = InstrumentRules(
            symbol=exchange_symbol.replace("-", ""), exchange_symbol=exchange_symbol,
            base_asset=exchange_symbol.rsplit("-", 1)[0], quote_asset=exchange_symbol.rsplit("-", 1)[1],
            tradable=_integer(item["status"], "status") == 1 and _boolean(item["apiStateOpen"], "apiStateOpen"),
            price_step=Decimal(1).scaleb(-price_precision),
            quantity_step=Decimal(1).scaleb(-quantity_precision),
            min_quantity=_decimal(item["tradeMinQuantity"], "tradeMinQuantity"),
            max_quantity=_decimal(item.get("tradeMaxQuantity", 0), "tradeMaxQuantity"),
            min_notional=_decimal(item["tradeMinUSDT"], "tradeMinUSDT"),
            max_leverage=0, order_types=("MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"),
        )
        if rules.min_quantity < 0 or rules.min_notional < 0 or rules.max_quantity < 0:
            _fail(ExchangeStateUnavailableError, "BingX: отрицательные ограничения инструмента")
        with self._cache_lock:
            self._instrument_cache[exchange_symbol] = (time.monotonic(), rules)
            self._instrument_details[exchange_symbol] = copy.deepcopy(item)
        return rules

    def _quantity(self, rules, value, *, closing=False):
        qty = _step(_decimal(value, "quantity", True), rules.quantity_step)
        if qty <= 0 or qty < rules.min_quantity or (rules.max_quantity and qty > rules.max_quantity):
            _fail(ExchangeStateUnavailableError, "BingX: quantity вне биржевых ограничений")
        if not closing and not rules.tradable:
            _fail(UnsupportedCapabilityError, "BingX: открытие по инструменту запрещено")
        if closing and not _boolean(self._instrument_details[rules.exchange_symbol]["apiStateClose"], "apiStateClose"):
            _fail(UnsupportedCapabilityError, "BingX: закрытие по инструменту запрещено биржей")
        return qty

    def get_balance(self):
        asset = self._settings()["settle_asset"]
        data = self._data(self._make_request("get", "/openApi/swap/v2/user/balance", {"currency": asset}))
        raw = data.get("balance", data)
        actual_asset = raw.get("asset", asset) if isinstance(raw, dict) else None
        allowed_assets = {asset}
        if self._initial_settings["execution_mode"] == "demo":
            # Sandbox BingX возвращает виртуальную валюту VST для USDT-контрактов.
            allowed_assets.add("VST")
        if not isinstance(raw, dict) or actual_asset not in allowed_assets:
            _fail(ExchangeStateUnavailableError, "BingX: невалидная валюта баланса")
        def field(names, default=None):
            for key in names:
                if key in raw:
                    return _decimal(raw[key], key)
            if default is not None:
                return default
            _fail(ExchangeStateUnavailableError, f"BingX: отсутствует поле баланса {names[0]}")
        pnl = field(("unrealizedProfit",), Decimal(0))
        # Balance.total_with_pnl уже прибавляет PnL; equity не кладём в wallet повторно.
        total = field(("balance", "walletBalance", "totalWalletBalance"),
                      _decimal(raw["equity"], "equity") - pnl if "equity" in raw else None)
        available = field(("availableBalance", "availableMargin", "available", "free"))
        return Balance(total_balance=float(total), available_balance=float(available),
                       unrealized_pnl=float(pnl), locked_balance=float(max(total - available, Decimal(0))), asset=actual_asset)

    def get_positions(self, *, force_refresh=False):
        with self._trade_lock:
            return self._get_positions_snapshot(force_refresh=force_refresh)

    def _get_positions_snapshot(self, *, force_refresh=False):
        for _ in range(2):
            with self._cache_lock:
                generation = self._generation
                if (not force_refresh and self._positions_cache is not None
                        and time.monotonic() - self._positions_cache_time < self._positions_cache_ttl):
                    return {k: list(v) for k, v in self._positions_cache.items()}
            raw_items = self._data(self._make_request("get", "/openApi/swap/v2/user/positions"), list)
            positions, details = {}, {}
            for raw in raw_items:
                if not isinstance(raw, dict) or not {"symbol", "positionAmt", "positionSide"}.issubset(raw):
                    _fail(ExchangeStateUnavailableError, "BingX: повреждена запись позиции")
                amount = _decimal(raw["positionAmt"], "positionAmt")
                if amount == 0:
                    continue
                if not {"positionId", "avgPrice", "unrealizedProfit", "isolated"}.issubset(raw) or not raw["positionId"]:
                    _fail(ExchangeStateUnavailableError, "BingX: неполная открытая позиция")
                isolated = _boolean(raw["isolated"], "isolated")
                exchange_side = str(raw["positionSide"]).upper()
                if exchange_side not in {"LONG", "SHORT", "BOTH"}:
                    _fail(ExchangeStateUnavailableError, "BingX: неизвестная сторона позиции")
                side = PositionSide(exchange_side) if exchange_side != "BOTH" else (PositionSide.LONG if amount > 0 else PositionSide.SHORT)
                symbol = self.normalize_symbol(raw["symbol"])
                position_id = str(raw["positionId"])
                size = abs(amount)
                entry = _decimal(raw["avgPrice"], "avgPrice", True)
                available = _decimal(raw.get("availableAmt", size), "availableAmt")
                if not 0 <= available <= size or position_id in details:
                    _fail(ExchangeStateUnavailableError, "BingX: невалидный availableAmt или повтор positionId")
                details[position_id] = dict(raw, isolated=isolated, availableAmt=available, quantity=size)
                positions.setdefault(symbol, []).append(Position(
                    symbol=symbol, side=side, size=float(size), entry_price=float(entry),
                    unrealized_pnl=float(_decimal(raw["unrealizedProfit"], "unrealizedProfit")),
                    position_id=position_id,
                    leverage=_integer(raw["leverage"], "leverage") if "leverage" in raw else None,
                    mark_price=float(_decimal(raw["markPrice"], "markPrice")) if raw.get("markPrice") is not None else None,
                    updated_at=_timestamp(raw.get("updateTime")), created_at=_timestamp(raw.get("createTime")),
                    exchange_quantity=float(size), contract_size=1.0,
                ))
            with self._cache_lock:
                if generation != self._generation:
                    force_refresh = True
                    continue
                self._positions_cache, self._position_details = positions, details
                self._positions_cache_time = time.monotonic()
                return {k: list(v) for k, v in positions.items()}
        _fail(ExchangeStateUnavailableError, "BingX: снимок позиции устарел во время чтения")

    def _account_mode(self, symbol):
        dual = self._data(self._make_request("get", "/openApi/swap/v1/positionSide/dual"))
        hedge = _boolean(dual.get("dualSidePosition"), "dualSidePosition")
        if hedge != (self._settings()["position_mode"] == "hedge"):
            _fail(UnsupportedCapabilityError, "BingX: position mode аккаунта не совпадает с настройками")
        margin = self._data(self._make_request("get", "/openApi/swap/v2/trade/marginType", {"symbol": self._format_symbol(symbol)}))
        margin_type = str(margin.get("marginType", "")).upper()
        if margin_type not in {"ISOLATED", "SEPARATE_ISOLATED"}:
            _fail(UnsupportedCapabilityError, "BingX: разрешена только isolated margin; режим не переключён")
        return hedge, margin_type

    def check_prerequisites(self):
        if self._settings()["execution_mode"] == "read_only":
            return True
        if not self.api_key or not self.secret_key:
            _fail(ExchangeStateUnavailableError, "BingX: API ключи отсутствуют")
        dual = self._data(self._make_request("get", "/openApi/swap/v1/positionSide/dual"))
        if _boolean(dual.get("dualSidePosition"), "dualSidePosition") != (self._settings()["position_mode"] == "hedge"):
            _fail(UnsupportedCapabilityError, "BingX: нужно вручную согласовать position mode аккаунта")
        return True

    def get_order(self, symbol, order_id=None, *, client_order_id=None):
        if not order_id and not client_order_id:
            _fail(ExchangeStateUnavailableError, "BingX: нужен orderId или clientOrderID")
        params = {"symbol": self._format_symbol(symbol)}
        params.update({"orderId": str(order_id)} if order_id else {"clientOrderID": client_order_id})
        data = self._data(self._make_request("get", "/openApi/swap/v2/trade/order", params))
        order = self._parse_orders([data.get("order", data)])[0]
        if order.symbol != params["symbol"] or (order_id and order.order_id != str(order_id)):
            _fail(ExchangeStateUnavailableError, "BingX: ответ query относится к другому ордеру")
        return order

    def _orders(self, endpoint, symbol=None, limit=None):
        params = {"symbol": self._format_symbol(symbol)} if symbol else {}
        if limit is not None:
            limit = _integer(limit, "limit")
            if not 1 <= limit <= 1000:
                _fail(ExchangeStateUnavailableError, "BingX: limit вне диапазона")
            params["limit"] = limit
        data = self._data(self._make_request("get", endpoint, params))
        orders = data.get("orders")
        if not isinstance(orders, list):
            _fail(ExchangeStateUnavailableError, "BingX: orders не является списком")
        return self._parse_orders(orders)

    def get_open_orders(self, symbol=None):
        return self._orders("/openApi/swap/v2/trade/openOrders", symbol)

    def get_recent_orders(self, symbol, limit=10):
        return self._orders("/openApi/swap/v2/trade/allOrders", symbol, limit)

    def _parse_orders(self, orders_data):
        if not isinstance(orders_data, list):
            _fail(ExchangeStateUnavailableError, "BingX: невалидный список ордеров")
        statuses = {"PENDING": OrderStatus.NEW, "PARTIALLYFILLED": OrderStatus.PARTIALLY_FILLED,
                    "CANCELLED": OrderStatus.CANCELED, "FAILED": OrderStatus.REJECTED}
        statuses.update({s.value: s for s in OrderStatus})
        result = []
        for raw in orders_data:
            if not isinstance(raw, dict) or not {"symbol", "side", "type", "status", "positionSide"}.issubset(raw):
                _fail(ExchangeStateUnavailableError, "BingX: неполная запись ордера")
            try:
                side = OrderSide(str(raw["side"]).upper())
                order_type = OrderType(str(raw["type"]).upper())
                position_side = PositionSide(str(raw["positionSide"]).upper())
                status = statuses[str(raw["status"]).upper()]
            except (ValueError, KeyError):
                _fail(ExchangeStateUnavailableError, "BingX: неизвестные enum ордера; нужен расширенный DTO")
            order_id = raw.get("orderId", raw.get("orderID"))
            quantity = raw.get("origQty", raw.get("quantity"))
            if order_id is None or str(order_id) == "" or quantity is None:
                _fail(ExchangeStateUnavailableError, "BingX: отсутствует orderId/quantity")
            qty = _decimal(quantity, "origQty", True)
            filled = _decimal(raw.get("executedQty", 0), "executedQty")
            if not 0 <= filled <= qty:
                _fail(ExchangeStateUnavailableError, "BingX: невалидный executedQty")
            result.append(Order(
                order_id=str(order_id), symbol=self._format_symbol(raw["symbol"]), side=side,
                order_type=order_type, position_side=position_side, status=status,
                quantity=float(qty), filled_quantity=float(filled),
                price=float(_decimal(raw.get("price", 0) or 0, "price")),
                average_price=float(_decimal(raw.get("avgPrice", 0) or 0, "avgPrice")),
                commission=float(_decimal(raw.get("commission", 0) or 0, "commission")),
                realized_pnl=float(_decimal(raw.get("profit", 0) or 0, "profit")),
                stop_price=float(_decimal(raw["stopPrice"], "stopPrice")) if raw.get("stopPrice") not in (None, "") else None,
                created_at=_timestamp(raw.get("time")), updated_at=_timestamp(raw.get("updateTime")),
                raw_data=copy.deepcopy(raw),
            ))
        return result

    def create_grid_listen_key(self):
        from src.core.strategies.grid.native_stream import BingXListenKey
        receipt = self._make_request("post", "/openApi/user/auth/userDataStream")
        if not isinstance(receipt, BingXListenKey):
            _fail(ExchangeStateUnavailableError, "BingX GRID: typed listenKey не подтверждён")
        # Ни token, ни response body не входят в wire/ACK.
        self._publish_submission({"status": "ACKNOWLEDGED", "method": "post",
            "endpoint": "/openApi/user/auth/userDataStream", "request_params": {},
            "listen_schema": receipt.schema}, submitted=True)
        return receipt

    def open_grid_attachment_stream(self, timeout):
        from src.core.strategies.grid.native_stream import BingXAttachmentStream
        return BingXAttachmentStream(self, timeout)

    def _native_grid_protection(self, symbol, order, spec, previous):
        qty = _decimal(spec["quantity"], "quantity", True)
        if order.status != OrderStatus.FILLED or _decimal(order.raw_data.get("executedQty")) != qty:
            _fail(ExchangeStateUnavailableError, "BingX GRID: native MARKET не подтверждён полным fill")
        expected_side = "SELL" if spec["owned_side"] == "LONG" else "BUY"
        links = {}
        for event in spec.get("native_links", []):
            kind = event.get("o")
            if kind not in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                continue
            key = "sl" if kind == "STOP_MARKET" else "tp"
            oid = str(event.get("i", ""))
            if (str(event.get("li")) != str(order.order_id) or not oid.isdigit() or int(oid) <= 0
                    or event.get("s") != self._format_symbol(symbol) or event.get("S") != expected_side
                    or event.get("ps") != spec["position_side"] or _decimal(event.get("q")) != qty
                    or _decimal(event.get("sp")) != _decimal(spec[key]) or event.get("wt") != self._working_type()
                    or kind in links and links[kind] != oid):
                _fail(ExchangeStateUnavailableError, "BingX GRID: WS li/ID/параметры attached связи не совпадают")
            links[kind] = oid
        if len(links) != 2 or len(set(links.values())) != 2:
            _fail(ExchangeStateUnavailableError, "BingX GRID: обе exact-ID WS li связи не сохранены; adoption по цене запрещён")
        ids = [links[k] for k in ("STOP_MARKET", "TAKE_PROFIT_MARKET")]
        if previous.get("plan_ids") and ids != previous["plan_ids"]:
            _fail(ExchangeStateUnavailableError, "BingX GRID: сохранённые attached IDs изменились")
        if any(str(active.order_id) not in ids for active in self.get_open_orders(symbol)):
            _fail(ExchangeStateUnavailableError, "BingX GRID: посторонняя pending заявка может изменить защищённый объём")
        positions = self.get_positions(force_refresh=True).get(self.normalize_symbol(symbol), [])
        pid = previous.get("position_id")
        if positions:
            if (len(positions) != 1 or positions[0].side.value != spec["owned_side"]
                    or self._position_details[positions[0].position_id]["quantity"] != qty
                    or pid and pid != positions[0].position_id):
                _fail(ExchangeStateUnavailableError, "BingX GRID: native position size/side/ID изменены вручную")
            pid = positions[0].position_id
        plans, closed = [], Decimal(0)
        closing = spec.get("native_close")
        for kind, oid in links.items():
            plan = self.get_order(symbol, oid)
            raw = plan.raw_data
            key = "sl" if kind == "STOP_MARKET" else "tp"
            if (str(plan.order_id) != oid or plan.symbol != self._format_symbol(symbol)
                    or plan.side.value != expected_side or plan.position_side.value != spec["position_side"]
                    or plan.order_type.value != kind or _decimal(raw.get("origQty", raw.get("quantity"))) != qty
                    or _decimal(raw.get("stopPrice")) != _decimal(spec[key]) or raw.get("workingType") != self._working_type()
                    or self._settings()["position_mode"] == "one_way"
                    and not (str(raw.get("reduceOnly")).lower() == "true" or str(raw.get("closePosition")).lower() == "true")):
                _fail(ExchangeStateUnavailableError, "BingX GRID: exact attached REST readback изменён/не закрывающий")
            self._owned_orders[oid] = self._format_symbol(symbol)
            if pid:
                self._owned_order_positions[oid] = pid
            plans.append(copy.deepcopy(raw))
            if plan.status == OrderStatus.FILLED:
                candidates = [o for o in self.get_recent_orders(symbol, limit=100)
                              if str(o.raw_data.get("triggerOrderId")) == oid and o.order_type == OrderType.MARKET]
                if len(candidates) != 1:
                    _fail(ExchangeStateUnavailableError, "BingX GRID: exact trigger execution child не найден однозначно")
                closing = {"order_id": candidates[0].order_id, "trigger_id": oid}
            elif plan.status != OrderStatus.NEW and plan.status not in {OrderStatus.CANCELED, OrderStatus.EXPIRED}:
                _fail(ExchangeStateUnavailableError, "BingX GRID: неизвестное состояние attached защиты")
        if closing:
            child = self.get_order(symbol, closing["order_id"])
            raw = child.raw_data
            if (child.order_type != OrderType.MARKET or child.status != OrderStatus.FILLED
                    or child.side.value != expected_side or child.position_side.value != spec["position_side"]
                    or _decimal(raw.get("origQty", raw.get("quantity"))) != qty or _decimal(raw.get("executedQty")) != qty
                    or closing.get("client_id") and raw.get("clientOrderId") != closing["client_id"]
                    or closing.get("trigger_id") and str(raw.get("triggerOrderId")) != closing["trigger_id"]
                    or positions):
                _fail(ExchangeStateUnavailableError, "BingX GRID: full close не доказан exact-ID child и flat снимком")
            closed = qty
        elif not positions or any(str(p.get("status")).upper() not in {"NEW", "PENDING"} for p in plans):
            _fail(ExchangeStateUnavailableError, "BingX GRID: обе активные SL/TP и позиция не подтверждены")
        cleanup = bool(closed and any(str(p.get("status")).upper() in {"NEW", "PENDING"} for p in plans))
        return {"state": "CLOSED" if closed else "ACTIVE", "confirmed": not closed,
                "plan_ids": ids, "position_id": pid, "entry_filled_quantity": str(qty),
                "closed_quantity": str(closed), "plans": plans, "cleanup_pending": cleanup,
                "close_order_id": closing["order_id"] if closing else None}

    def get_grid_attached_protection(self, symbol, order, spec, *, previous=None):
        """REST echo pending-параметров не доказывает активную защиту или parent-child связь."""
        self._account_mode(symbol)
        if spec.get("execution_path") == "native_market":
            return self._native_grid_protection(symbol, order, spec, previous or {})
        import json
        for key, field, kind in (("sl", "stopLoss", "STOP_MARKET"), ("tp", "takeProfit", "TAKE_PROFIT_MARKET")):
            if spec.get(key) is None:
                continue
            attachment = order.raw_data.get(field)
            if isinstance(attachment, str):
                try:
                    attachment = json.loads(attachment)
                except (TypeError, ValueError):
                    _fail(ExchangeStateUnavailableError, "BingX GRID: повреждён attached readback")
            if (not isinstance(attachment, dict) or attachment.get("type") != kind
                    or _decimal(attachment.get("stopPrice")) != _decimal(spec[key])
                    or attachment.get("workingType") != self._working_type()):
                _fail(ExchangeStateUnavailableError, "BingX GRID: attached SL/TP отсутствует или изменён")
        if _decimal(order.raw_data.get("executedQty", 0)) > 0:
            _fail(ExchangeStateUnavailableError, "BingX GRID: fill есть, durable REST связь SL/TP с parent не доказана; WS li нельзя угадать")
        if order.status != OrderStatus.NEW:
            _fail(ExchangeStateUnavailableError, "BingX GRID: terminal entry не доказывает cleanup attached защиты без durable parent-child readback")
        return {"state": "PENDING", "confirmed": False,
                "plan_ids": [], "closed_quantity": "0", "plans": []}

    def register_owned_order(self, symbol, order_id, *, client_order_id):
        """Восстановить владение из журнала только после сверки ID с биржей."""
        order = self.get_order(symbol, order_id)
        if (not client_order_id or str(order.raw_data.get("clientOrderId", order.raw_data.get("clientOrderID", ""))) != self._client_id(client_order_id)):
            _fail(UnsupportedCapabilityError, "BingX: clientOrderId не подтверждает владение")
        with self._trade_lock:
            self._owned_orders[str(order_id)] = self._format_symbol(symbol)
        return order

    def register_owned_protection(self, symbol, order_id, *, request_params, position_id):
        """Точный ID и исходные параметры conditional должны происходить из durable журнала."""
        params = copy.deepcopy(request_params)
        required = {"symbol", "side", "positionSide", "type", "quantity", "stopPrice", "workingType"}
        if (not required.issubset(params) or params.get("symbol") != self._format_symbol(symbol)
                or params.get("type") not in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
                or params.get("workingType") not in {"MARK_PRICE", "CONTRACT_PRICE"}):
            _fail(UnsupportedCapabilityError, "BingX: некорректная защита в журнале")
        with self._trade_lock:
            target = self._target(symbol, position_id)
            if params.get("positionId") and str(params["positionId"]) != target.position_id:
                _fail(UnsupportedCapabilityError, "BingX: защита журнала относится к другой позиции")
            if params.get("side") != ("SELL" if target.is_long else "BUY"):
                _fail(UnsupportedCapabilityError, "BingX: защита журнала имеет другую сторону")
            if _decimal(params["quantity"], "quantity", True) > self._owned_positions[target.position_id][1]:
                _fail(UnsupportedCapabilityError, "BingX: защита журнала превышает собственный объём")
            self._verify_protection(params, str(order_id))
            self._owned_orders[str(order_id)] = params["symbol"]
            self._owned_order_positions[str(order_id)] = target.position_id
        return self.get_order(symbol, order_id)

    def register_owned_position(self, symbol, position_id, quantity, *, position_side=None):
        """Журнал передаёт собственный объём и исходное направление, не текущую ручную сторону."""
        if position_side is None or _enum(PositionSide, position_side) == PositionSide.BOTH:
            _fail(UnsupportedCapabilityError, "BingX: журнал должен указать исходное направление LONG/SHORT")
        with self._trade_lock:
            return self._register_owned_position(symbol, position_id, quantity, _enum(PositionSide, position_side))

    def _register_owned_position(self, symbol, position_id, quantity, position_side):
        positions = self.get_positions(force_refresh=True).get(self.normalize_symbol(symbol), [])
        target = next((p for p in positions if p.position_id == str(position_id)), None)
        qty = _decimal(quantity, "собственный объём", True)
        if (target is None or target.side != position_side or qty > self._position_details[str(position_id)]["quantity"]
                or not self._position_details[str(position_id)]["isolated"]):
            _fail(ExchangeStateUnavailableError, "BingX: владение позицией не подтверждено снимком")
        with self._trade_lock:
            self._owned_positions[str(position_id)] = (self._format_symbol(symbol), qty)
            self._owned_position_sides[str(position_id)] = target.side

    def _mark_uncertain(self, params, *, order_id=None):
        if params.get("symbol"):
            with self._trade_lock:
                symbol = self._format_symbol(params["symbol"])
                self._uncertain_symbols.add(symbol)
                # Уникальный PREPARED отличает новый потерянный запрос от похожего старого ACK.
                submission_id = next((event["submission_id"] for event in reversed(self._prepared_submissions)
                    if params in (event["request_params"],
                                  {k: v for k, v in event["request_params"].items() if k not in {"cancelOrderId", "cancelReplaceMode"}})), None)
                evidence = {"request_params": copy.deepcopy(params), "submission_id": submission_id,
                            "order_id": str(order_id) if order_id is not None else None}
                requests = self._uncertain_requests.setdefault(symbol, [])
                if evidence not in requests:
                    requests.append(evidence)

    def _unknown(self, message, params, **context):
        self._mark_uncertain(params, order_id=context.get("order_id"))
        _fail(UnknownOrderStateError, message, symbol=params.get("symbol"),
              client_order_id=params.get("clientOrderId"), request_params=copy.deepcopy(params),
              submission_status="UNKNOWN", **context)

    @staticmethod
    def _client_id(value=None):
        value = "sebx" + uuid.uuid4().hex if value is None else str(value)
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", value):
            _fail(ExchangeStateUnavailableError, "BingX: невалидный clientOrderId")
        return value

    def _submit_order(self, params):
        client_id = params.get("clientOrderId")
        if client_id:
            intent = self._intents.get(client_id)
            if intent is not None and intent["params"] != params:
                _fail(UnsupportedCapabilityError, "BingX: clientOrderId уже связан с другой командой")
            if intent is not None:
                if "rejection" in intent:
                    raise intent["rejection"]
                return self._reconcile_order(params)
            self._intents[client_id] = {"params": copy.deepcopy(params)}
        try:
            response = self._make_request("post", "/openApi/swap/v2/trade/order", params)
            data = self._data(response)
            raw = data.get("order", data)
            order_id = raw.get("orderId", raw.get("orderID")) if isinstance(raw, dict) else None
            if not order_id:
                raise UnknownOrderStateError("BingX: ответ мутации не содержит orderId")
        except ExchangeAPIError as exc:
            if client_id:
                self._intents[client_id]["rejection"] = exc
            exc.symbol = params["symbol"]
            exc.client_order_id = client_id
            exc.request_params = copy.deepcopy(params)
            exc.submission_status = "REJECTED"
            from src.utils.log_context import SecretRedactor
            self._publish_submission({"status": "REJECTED", "client_order_id": client_id,
                "symbol": params["symbol"], "request_params": copy.deepcopy(params),
                "api_code": exc.code, "api_retryable": exc.retryable,
                                "api_message": SecretRedactor().text(str(exc))[:1024]}, submitted=True)
            raise
        except (UnknownOrderStateError, ExchangeStateUnavailableError) as exc:
            if getattr(exc, "submission_status", None) == "NOT_SENT":
                if client_id:
                    self._intents[client_id]["rejection"] = exc
                raise
            if client_id:
                return self._reconcile_order(params)
            self._unknown("BingX: состояние защитного ордера неизвестно; повтор запрещён", params)
        order_id = str(order_id)
        self._owned_orders[order_id] = params["symbol"]
        self.last_submission = {"status": "ACKNOWLEDGED", "order_id": order_id, "client_order_id": client_id,
                                "request_params": copy.deepcopy(params)}
        if client_id:
            self._intents[client_id]["order_id"] = order_id
        self.invalidate_cache()
        self._publish_submission(self.last_submission, submitted=True)
        return order_id

    def _reconcile_order(self, params):
        for attempt in range(3):
            try:
                order = self.get_order(params["symbol"], client_order_id=params["clientOrderId"])
                raw_client = order.raw_data.get("clientOrderId", order.raw_data.get("clientOrderID", ""))
                if not self._matches_command(order, params):
                    self._unknown("BingX: найденный ордер не совпал с торговой командой", params)
                self._owned_orders[order.order_id] = params["symbol"]
                self._intents[params["clientOrderId"]]["order_id"] = order.order_id
                self.last_submission = {"status": "ACKNOWLEDGED", "order_id": order.order_id,
                                        "client_order_id": params["clientOrderId"], "request_params": copy.deepcopy(params)}
                self._publish_submission(self.last_submission, submitted=True)
                return order.order_id
            except UnknownOrderStateError:
                raise
            except ExchangeError:
                if attempt < 2:
                    time.sleep(0.1)
        self._unknown("BingX: состояние entry/close не удалось сверить; повтор запрещён", params)

    def _matches_command(self, order, params):
        raw = order.raw_data
        client = raw.get("clientOrderId", raw.get("clientOrderID", ""))
        valid = (order.symbol == params["symbol"] and order.side.value == params["side"]
                 and order.order_type.value == params["type"]
                 and order.position_side.value == params["positionSide"]
                 and _decimal(raw.get("origQty", raw.get("quantity"))) == _decimal(params["quantity"]))
        if params.get("clientOrderId"):
            valid = valid and str(client) == params["clientOrderId"]
        if params.get("price"):
            valid = valid and _decimal(raw.get("price")) == _decimal(params["price"])
        if params.get("reduceOnly"):
            valid = valid and _boolean(raw.get("reduceOnly"), "reduceOnly")
        if params.get("positionId"):
            valid = valid and str(raw.get("positionId")) == params["positionId"]
        return valid

    def reconcile_submission(self, request_params):
        """Журнал передаёт точную исходную команду; этот метод НИКОГДА не отправляет POST."""
        params = copy.deepcopy(request_params)
        if not params.get("clientOrderId"):
            self._unknown("BingX: conditional без clientOrderId нельзя восстановить поиском", params)
        with self._trade_lock:
            existing = self._intents.get(params["clientOrderId"])
            if existing and existing["params"] != params:
                _fail(UnsupportedCapabilityError, "BingX: конфликт команды журнала")
            self._intents.setdefault(params["clientOrderId"], {"params": params})
            order_id = self._reconcile_order(params)
            return self.get_order(params["symbol"], order_id)

    def _replay_command(self, client_id, fingerprint):
        saved = self._commands.get(client_id)
        if saved is None:
            return None
        if saved["fingerprint"] != fingerprint:
            _fail(UnsupportedCapabilityError, "BingX: clientOrderId повторно использован другой командой")
        if "result" in saved:
            return saved["result"]
        if "rejection" in saved:
            raise saved["rejection"]
        self._unknown("BingX: команда уже отправлялась; требуется журнал/reconciliation", saved["params"],
                      order_id=saved.get("order_id"))

    def _confirm_fill(self, symbol, order_id, params):
        try:
            order = self.get_order(symbol, order_id)
            confirmed = (self._matches_command(order, params) and order.status == OrderStatus.FILLED
                         and _decimal(order.raw_data.get("executedQty", 0)) == _decimal(params["quantity"]))
        except ExchangeError:
            self._unknown("BingX: исполнение ордера не подтверждено", params, order_id=order_id)
        if not confirmed:
            self._unknown("BingX: ордер принят, но полное исполнение не подтверждено", params,
                          order_id=order_id, executed_quantity=order.filled_quantity)
        return order

    def _target(self, symbol, position_id=None, position_side=None):
        positions = self.get_positions(force_refresh=True).get(self.normalize_symbol(symbol), [])
        candidates = [p for p in positions if (position_id is None or p.position_id == str(position_id))
                      and (position_side is None or position_side == PositionSide.BOTH or p.side == position_side)]
        if len(candidates) != 1:
            _fail(ExchangeStateUnavailableError, "BingX: целевая позиция отсутствует или неоднозначна")
        target = candidates[0]
        if self._owned_positions.get(target.position_id, (None,))[0] != self._format_symbol(symbol):
            _fail(UnsupportedCapabilityError, "BingX: нет явного владения позицией из журнала core")
        if self._owned_position_sides.get(target.position_id) != target.side:
            _fail(ExchangeStateUnavailableError, "BingX: направление собственной позиции изменилось вручную")
        if self._owned_positions[target.position_id][1] > self._position_details[target.position_id]["quantity"]:
            _fail(ExchangeStateUnavailableError, "BingX: собственный объём больше позиции; нужна сверка журнала")
        if not self._position_details[target.position_id]["isolated"]:
            _fail(UnsupportedCapabilityError, "BingX: Cross-позицией управлять запрещено")
        return target

    def set_leverage(self, symbol, leverage, position_side=PositionSide.BOTH):
        self._allow_mutation()
        lev = _integer(leverage, "leverage")
        if lev < 1:
            _fail(ExchangeStateUnavailableError, "BingX: leverage должен быть положительным")
        with self._trade_lock:
            hedge, _ = self._account_mode(symbol)
            pos_side = _enum(PositionSide, position_side)
            if not hedge:
                pos_side = PositionSide.BOTH
            elif pos_side == PositionSide.BOTH:
                _fail(UnsupportedCapabilityError, "BingX: в Hedge нужен LONG или SHORT")
            params = {"symbol": self._format_symbol(symbol)}
            current = self._data(self._make_request("get", "/openApi/swap/v2/trade/leverage", params))
            keys = ("longLeverage", "shortLeverage") if pos_side == PositionSide.BOTH else (("longLeverage",) if pos_side == PositionSide.LONG else ("shortLeverage",))
            max_keys = ("maxLongLeverage", "maxShortLeverage") if pos_side == PositionSide.BOTH else (("maxLongLeverage",) if pos_side == PositionSide.LONG else ("maxShortLeverage",))
            if any(lev > _integer(current.get(k), k) for k in max_keys):
                _fail(UnsupportedCapabilityError, "BingX: плечо превышает биржевой предел")
            if all(_integer(current.get(k), k) == lev for k in keys):
                return True
            positions = self.get_positions(force_refresh=True).get(self.normalize_symbol(symbol), [])
            if any(pos_side == PositionSide.BOTH or p.side == pos_side for p in positions):
                _fail(UnsupportedCapabilityError, "BingX: плечо существующей позиции не меняется автоматически")
            self._envelope(self._make_request("post", "/openApi/swap/v2/trade/leverage",
                                            dict(params, side=pos_side.value, leverage=lev)))
            readback = self._data(self._make_request("get", "/openApi/swap/v2/trade/leverage", params))
            if not all(_integer(readback.get(k), k) == lev for k in keys):
                _fail(ExchangeStateUnavailableError, "BingX: новое плечо не подтверждено")
            return True

    def place_order(self, symbol, side, quantity, order_type=OrderType.MARKET, price=None,
                    sl=None, tp=None, position_side=None, leverage=None, *, client_order_id=None, native_attached=False):
        self._allow_mutation()
        side, order_type = _enum(OrderSide, side), _enum(OrderType, order_type)
        command_id = self._client_id(client_order_id)
        if native_attached and (order_type != OrderType.MARKET or sl is None or tp is None):
            _fail(UnsupportedCapabilityError, "BingX GRID: native entry требует MARKET и обе SL/TP")
        fingerprint = ("entry", self._format_symbol(symbol), side.value, str(_decimal(quantity)),
                       order_type.value, str(price), str(sl), str(tp), str(position_side), str(leverage), native_attached)
        _decimal(quantity, "quantity", True)
        for value in (price, sl, tp):
            if value is not None:
                _decimal(value, "цена", True)
        if order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            _fail(UnsupportedCapabilityError, "BingX: условные ордера создаются через set_sl_tp")
        if order_type == OrderType.LIMIT and (price is None or sl is not None or tp is not None):
            _fail(UnsupportedCapabilityError, "BingX: LIMIT требует price; защиту ставьте после подтверждения fill")
        with self._trade_lock:
            replay = self._replay_command(command_id, fingerprint)
            if replay is not None:
                return replay
            if self._format_symbol(symbol) in self._uncertain_symbols:
                _fail(ExchangeStateUnavailableError, "BingX: символ приостановлен до сверки UNKNOWN")
            hedge, margin = self._account_mode(symbol)
            direction = PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT
            pos_side = _enum(PositionSide, position_side) if position_side is not None else direction
            if hedge and pos_side != direction:
                _fail(UnsupportedCapabilityError, "BingX: закрывающие команды используйте через close_position")
            if not hedge:
                if pos_side not in {direction, PositionSide.BOTH}:
                    _fail(UnsupportedCapabilityError, "BingX: сторона не соответствует One-way входу")
                pos_side = PositionSide.BOTH
            rules = self.get_instrument_rules(symbol)
            if rules.quote_asset != self._settings()["settle_asset"]:
                _fail(UnsupportedCapabilityError, "BingX: settlement инструмента не совпадает с настройками")
            qty = self._quantity(rules, quantity)
            entry_price = (_step(_decimal(price, "price", True), rules.price_step,
                                  ROUND_FLOOR if side == OrderSide.BUY else ROUND_CEILING)
                           if order_type == OrderType.LIMIT else _decimal(self.get_ticker(symbol).last_price, "lastPrice", True))
            if entry_price <= 0 or qty * entry_price < rules.min_notional:
                _fail(ExchangeStateUnavailableError, "BingX: ордер меньше min notional")
            lev = _integer(leverage if leverage is not None else self._config.default_leverage, "leverage")
            balance = self.get_balance()
            if lev < 1 or qty * entry_price / lev > _decimal(balance.available_balance):
                _fail(ExchangeStateUnavailableError, "BingX: недостаточно доступной margin")
            before = self.get_positions(force_refresh=True).get(rules.symbol, [])
            before_details = copy.deepcopy(self._position_details)
            if margin == "SEPARATE_ISOLATED" and before:
                _fail(UnsupportedCapabilityError, "BingX: Separate Isolated entry с существующими позициями неоднозначен")
            if any((not hedge or p.side == direction) and
                   (p.position_id not in self._owned_positions
                    or self._owned_positions[p.position_id][1] != before_details[p.position_id]["quantity"])
                   for p in before):
                _fail(UnsupportedCapabilityError, "BingX: вход затронул бы ручную позицию")
            if not hedge and any(p.side != direction for p in before):
                _fail(UnsupportedCapabilityError, "BingX: One-way вход не должен закрывать другую сторону")
            if native_attached and (before or self.get_open_orders(symbol)):
                _fail(UnsupportedCapabilityError, "BingX GRID: native entry разрешён только на полностью flat символе без заявок")
            if sl is not None or tp is not None:
                reference = self._protection_reference(symbol, rules)
                for kind, value in (("STOP_MARKET", sl), ("TAKE_PROFIT_MARKET", tp)):
                    if value is not None:
                        self._protection_stop(rules, direction == PositionSide.LONG, kind, value, reference)
            active_orders = self.get_open_orders(symbol)
            if any((not hedge or o.position_side == direction) and o.order_id not in self._owned_orders
                   for o in active_orders):
                _fail(UnsupportedCapabilityError, "BingX: ручные заявки могут изменить целевую позицию; вход приостановлен")
            self.set_leverage(symbol, lev, pos_side)
            params = {"symbol": rules.exchange_symbol, "side": side.value, "positionSide": pos_side.value,
                      "type": order_type.value, "quantity": format(qty, "f"),
                      "clientOrderId": command_id}
            if order_type == OrderType.LIMIT:
                params.update(price=format(entry_price, "f"), timeInForce="GTC")
            if native_attached:
                import json
                for field, kind, stop in (("stopLoss", "STOP_MARKET", sl), ("takeProfit", "TAKE_PROFIT_MARKET", tp)):
                    stop = self._protection_stop(rules, direction == PositionSide.LONG, kind, stop, reference)
                    params[field] = json.dumps({"type": kind, "stopPrice": format(stop, "f"),
                        "quantity": format(qty, "f"), "workingType": self._working_type()}, separators=(",", ":"))
            self.last_protection_confirmed = False
            self._commands[command_id] = {"fingerprint": fingerprint, "params": copy.deepcopy(params)}
            try:
                order_id = self._submit_order(params)
            except ExchangeError as exc:
                if getattr(exc, "submission_status", None) in {"NOT_SENT", "REJECTED"}:
                    self._commands[command_id]["rejection"] = exc
                raise
            self._commands[command_id]["order_id"] = order_id
            if order_type == OrderType.LIMIT:
                self._commands[command_id]["result"] = order_id
                return order_id
            self._confirm_fill(symbol, order_id, params)
            try:
                after = self.get_positions(force_refresh=True).get(rules.symbol, [])
            except ExchangeError:
                self._unknown("BingX: после fill снимок позиции недоступен", params, order_id=order_id)
            targets = [p for p in after if p.side == direction]
            if len(targets) != 1:
                self._unknown("BingX: исполнение не связано с единственной позицией", params, order_id=order_id)
            target = targets[0]
            same_before = [p for p in before if p.side == direction]
            previous = sum((before_details[p.position_id]["quantity"] for p in same_before), Decimal(0))
            details = self._position_details[target.position_id]
            if (not details["isolated"] or details["quantity"] != previous + qty
                    or (same_before and (len(same_before) != 1 or same_before[0].position_id != target.position_id))):
                self._unknown("BingX: размер/ID позиции изменился вне команды", params, order_id=order_id)
            old_owned = self._owned_positions.get(target.position_id, (rules.exchange_symbol, Decimal(0)))[1]
            self._owned_positions[target.position_id] = (rules.exchange_symbol, old_owned + qty)
            self._owned_position_sides[target.position_id] = direction
            if (sl is not None or tp is not None) and not native_attached:
                try:
                    self.set_sl_tp(symbol, direction, sl=sl, tp=tp, position_id=target.position_id)
                except ExchangeError as exc:
                    exc.entry_order_id = order_id
                    exc.entry_client_order_id = command_id
                    exc.position_id = target.position_id
                    exc.owned_quantity = format(old_owned + qty, "f")
                    exc.failure_policy = self._settings()["protection"].get("failure_policy", "pause")
                    self._mark_uncertain(params, order_id=order_id)
                    raise
            self._commands[command_id]["result"] = order_id
            return order_id

    def place_reduce_order(self, symbol, side, quantity, order_type=OrderType.LIMIT, price=None,
                           position_side=None, leverage=None, client_order_id=None, position_id=None,
                           reduce_only=True, *, command_id=None):
        """Закрывающий LIMIT: точный own ID/side/quantity; возвращается ACK, не fill."""
        self._allow_mutation()
        if reduce_only is not True or position_id is None:
            _fail(UnsupportedCapabilityError, "BingX: reduce LIMIT требует reduce_only=True и position_id")
        side, kind = _enum(OrderSide, side), _enum(OrderType, order_type)
        if kind != OrderType.LIMIT:
            _fail(UnsupportedCapabilityError, "BingX: reduce MARKET выполняется через close_position")
        cid = self._client_id(client_order_id or command_id)
        fingerprint = ("reduce_limit", self._format_symbol(symbol), side.value, str(_decimal(quantity)),
                       str(price), str(position_id), str(position_side), str(leverage))
        with self._trade_lock:
            replay = self._replay_command(cid, fingerprint)
            if replay is not None:
                return replay
            if self._format_symbol(symbol) in self._uncertain_symbols:
                _fail(ExchangeStateUnavailableError, "BingX: reduce LIMIT запрещён до сверки UNKNOWN")
            hedge, margin = self._account_mode(symbol)
            target = self._target(symbol, position_id)
            detail = self._position_details[target.position_id]
            owned = self._owned_positions[target.position_id][1]
            expected_side = OrderSide.SELL if target.is_long else OrderSide.BUY
            pos_side = target.side if hedge else PositionSide.BOTH
            if side != expected_side or (position_side is not None and _enum(PositionSide, position_side) != pos_side):
                _fail(UnsupportedCapabilityError, "BingX: reduce LIMIT имеет неверную исходную сторону")
            if detail["quantity"] != owned:
                _fail(ExchangeStateUnavailableError, "BingX: own quantity изменена вручную; reduce LIMIT запрещён")
            if leverage is not None and (target.leverage is None or _integer(leverage, "leverage") != target.leverage):
                _fail(UnsupportedCapabilityError, "BingX: reduce LIMIT не изменяет плечо позиции")
            rules = self.get_instrument_rules(symbol)
            if rules.quote_asset != self._settings()["settle_asset"]:
                _fail(UnsupportedCapabilityError, "BingX: неверный settlement reduce LIMIT")
            qty = self._quantity(rules, quantity, closing=True)
            if qty > owned or qty > detail["availableAmt"]:
                _fail(ExchangeStateUnavailableError, "BingX: reduce LIMIT превышает собственный/доступный объём")
            limit = _step(_decimal(price, "price", True), rules.price_step,
                          ROUND_FLOOR if side == OrderSide.BUY else ROUND_CEILING)
            if limit <= 0:
                _fail(ExchangeStateUnavailableError, "BingX: неверная цена reduce LIMIT")
            if any((not hedge or order.position_side == target.side) and order.order_id not in self._owned_orders
                   for order in self.get_open_orders(symbol)):
                _fail(UnsupportedCapabilityError, "BingX: ручные заявки могут изменить закрываемую позицию")
            params = {"symbol": rules.exchange_symbol, "side": side.value, "positionSide": pos_side.value,
                      "type": "LIMIT", "quantity": format(qty, "f"), "price": format(limit, "f"),
                      "timeInForce": "GTC", "clientOrderId": cid}
            if not hedge:
                params["reduceOnly"] = "true"
            if margin == "SEPARATE_ISOLATED":
                params["positionId"] = target.position_id
            self._commands[cid] = {"fingerprint": fingerprint, "params": copy.deepcopy(params)}
            try:
                oid = self._submit_order(params)
            except ExchangeError as exc:
                if getattr(exc, "submission_status", None) in {"NOT_SENT", "REJECTED"}:
                    self._commands[cid]["rejection"] = exc
                raise
            self._commands[cid].update(order_id=oid, result=oid)
            return oid

    def close_position(self, symbol, position_id, percentage=1.0, *, client_order_id=None):
        command_id = self._client_id(client_order_id)
        try:
            return self._close_position(symbol, position_id, percentage, client_order_id=command_id)
        except ExchangeError as exc:
            exc.close_position_id = str(position_id)
            saved = self._commands.get(command_id)
            if saved is not None:
                exc.close_order_id = saved.get("order_id")
                exc.close_client_order_id = command_id
                exc.close_fill_confirmed = saved.get("fill_confirmed", False)
                exc.close_request_params = copy.deepcopy(saved["params"])
            raise

    def _close_position(self, symbol, position_id, percentage=1.0, *, client_order_id=None):
        self._allow_mutation()
        pct = _decimal(percentage, "percentage", True)
        if pct > 1:
            _fail(ExchangeStateUnavailableError, "BingX: percentage должен быть в (0, 1]")
        command_id = self._client_id(client_order_id)
        fingerprint = ("close", self._format_symbol(symbol), str(position_id), str(pct))
        with self._trade_lock:
            replay = self._replay_command(command_id, fingerprint)
            if replay is not None:
                return replay
            hedge, margin = self._account_mode(symbol)
            target = self._target(symbol, position_id)
            details = copy.deepcopy(self._position_details[target.position_id])
            owned = self._owned_positions[target.position_id][1]
            base_qty = min(owned, details["quantity"], details["availableAmt"])
            rules = self.get_instrument_rules(symbol)
            qty = self._quantity(rules, base_qty * pct, closing=True)
            params = {"symbol": rules.exchange_symbol, "side": "SELL" if target.is_long else "BUY",
                      "positionSide": target.side.value if hedge else "BOTH", "type": "MARKET",
                      "quantity": format(qty, "f"), "clientOrderId": command_id}
            if not hedge:
                params["reduceOnly"] = "true"
            if margin == "SEPARATE_ISOLATED":
                params["positionId"] = target.position_id
            self._commands[command_id] = {"fingerprint": fingerprint, "params": copy.deepcopy(params)}
            try:
                order_id = self._submit_order(params)
            except ExchangeError as exc:
                if getattr(exc, "submission_status", None) in {"NOT_SENT", "REJECTED"}:
                    self._commands[command_id]["rejection"] = exc
                raise
            self._commands[command_id]["order_id"] = order_id
            self._confirm_fill(symbol, order_id, params)
            try:
                after = self.get_positions(force_refresh=True).get(rules.symbol, [])
            except ExchangeError:
                self._unknown("BingX: после close снимок позиции недоступен", params, order_id=order_id)
            remaining = next((p for p in after if p.position_id == target.position_id), None)
            remaining_qty = self._position_details[remaining.position_id]["quantity"] if remaining else Decimal(0)
            if remaining_qty != details["quantity"] - qty or (remaining and remaining.side != target.side):
                self._unknown("BingX: позиция изменена вручную или close ещё не отражён", params, order_id=order_id)
            self._commands[command_id]["fill_confirmed"] = True
            owned_left = max(Decimal(0), min(owned - qty, remaining_qty))
            self._owned_positions[target.position_id] = (rules.exchange_symbol, owned_left)
            if not owned_left:
                self._owned_positions.pop(target.position_id, None)
                self._owned_position_sides.pop(target.position_id, None)
                for order in self.get_open_orders(symbol):
                    if (self._owned_order_positions.get(order.order_id) == target.position_id
                            and order.order_type in {OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET}):
                        self.cancel_order(symbol, order.order_id)
            else:
                for order in self.get_open_orders(symbol):
                    if (self._owned_order_positions.get(order.order_id) == target.position_id
                            and order.order_type in {OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET}):
                        kwargs = {"sl" if order.order_type == OrderType.STOP_MARKET else "tp": order.stop_price}
                        self.set_sl_tp(symbol, target.side, position_id=target.position_id, **kwargs)
            self._commands[command_id]["result"] = True
            return True

    def _protection_reference(self, symbol, rules):
        if self._working_type() == "MARK_PRICE":
            raw = self._data(self._public_request("/openApi/swap/v2/quote/premiumIndex", {"symbol": rules.exchange_symbol}))
            return _decimal(raw.get("markPrice"), "markPrice", True)
        return _decimal(self.get_ticker(symbol).last_price, "lastPrice", True)

    @staticmethod
    def _protection_stop(rules, is_long, kind, value, reference):
        lower = (kind == "STOP_MARKET") == is_long
        stop = _step(_decimal(value, "stopPrice", True), rules.price_step,
                     ROUND_CEILING if lower else ROUND_FLOOR)
        if stop <= 0 or (stop >= reference if lower else stop <= reference):
            _fail(ExchangeStateUnavailableError, "BingX: защита сработает немедленно или имеет неверную цену")
        return stop

    def _working_type(self):
        return "MARK_PRICE" if self._settings()["protection"].get("trigger_price", "mark") == "mark" else "CONTRACT_PRICE"

    def _verify_protection(self, params, order_id):
        try:
            order = self.get_order(params["symbol"], order_id)
            valid = (order.status == OrderStatus.NEW and _decimal(order.raw_data.get("executedQty", 0)) == 0
                     and order.symbol == params["symbol"] and order.order_type.value == params["type"]
                     and order.side.value == params["side"] and order.position_side.value == params["positionSide"]
                     and _decimal(order.raw_data.get("origQty", order.raw_data.get("quantity"))) == _decimal(params["quantity"])
                     and _decimal(order.raw_data.get("stopPrice")) == _decimal(params["stopPrice"])
                     and order.raw_data.get("workingType") == params["workingType"])
            if params.get("reduceOnly") == "true":
                valid = valid and _boolean(order.raw_data.get("reduceOnly"), "reduceOnly")
            if params.get("positionId"):
                valid = valid and str(order.raw_data.get("positionId")) == params["positionId"]
            if not valid:
                self._unknown("BingX: защита не подтверждена readback", params, order_id=order_id)
        except UnknownOrderStateError:
            raise
        except ExchangeError:
            self._unknown("BingX: состояние защиты недоступно", params, order_id=order_id)
        return True

    def set_sl_tp(self, symbol, position_side, sl=None, tp=None, quantity=None, *, position_id=None):
        self.last_protection_confirmed = False
        try:
            return self._set_sl_tp(symbol, position_side, sl, tp, quantity, position_id=position_id)
        except ExchangeError as exc:
            exc.protection_position_id = str(position_id) if position_id is not None else None
            exc.failure_policy = self._initial_settings["protection"].get("failure_policy", "pause")
            raise

    def _set_sl_tp(self, symbol, position_side, sl=None, tp=None, quantity=None, *, position_id=None):
        self._allow_mutation()
        pos_side = _enum(PositionSide, position_side)
        if sl is None and tp is None:
            _fail(ExchangeStateUnavailableError, "BingX: нужен SL или TP")
        for value in (sl, tp, quantity):
            if value is not None:
                _decimal(value, "параметр защиты", True)
        with self._trade_lock:
            self.last_protection_confirmed = False
            hedge, margin = self._account_mode(symbol)
            target = self._target(symbol, position_id, pos_side)
            owned = self._owned_positions[target.position_id][1]
            cap = min(owned, self._position_details[target.position_id]["quantity"])
            if quantity is not None and _decimal(quantity) > cap:
                _fail(UnsupportedCapabilityError, "BingX: защита превышает собственный объём")
            rules = self.get_instrument_rules(symbol)
            qty = self._quantity(rules, quantity if quantity is not None else cap, closing=True)
            closing_side = "SELL" if target.is_long else "BUY"
            exchange_side = target.side.value if hedge else "BOTH"
            working = self._working_type()
            reference = self._protection_reference(symbol, rules)
            if rules.exchange_symbol in self._uncertain_symbols:
                _fail(ExchangeStateUnavailableError, "BingX: UNKNOWN защиты требует сверки журнала")
            planned = []
            open_orders = self.get_open_orders(symbol)
            for kind, value in (("STOP_MARKET", sl), ("TAKE_PROFIT_MARKET", tp)):
                if value is None:
                    continue
                stop = self._protection_stop(rules, target.is_long, kind, value, reference)
                params = {"symbol": rules.exchange_symbol, "side": closing_side, "positionSide": exchange_side,
                          "type": kind, "quantity": format(qty, "f"), "stopPrice": format(stop, "f"), "workingType": working}
                if not hedge:
                    params["reduceOnly"] = "true"
                if margin == "SEPARATE_ISOLATED":
                    params["positionId"] = target.position_id
                matches = [o for o in open_orders if o.position_side.value == exchange_side and o.order_type.value == kind
                           and (margin != "SEPARATE_ISOLATED" or str(o.raw_data.get("positionId")) == target.position_id)]
                if any(o.order_id not in self._owned_orders for o in matches) or len(matches) > 1:
                    _fail(UnsupportedCapabilityError, "BingX: ручная/неоднозначная защита сохранена; автоматическая замена запрещена")
                planned.append((params, matches, stop))
            # Весь запрос валидируется до первой мутации: неверный TP не оставляет только новый SL.
            for params, matches, stop in planned:
                if matches:
                    old = matches[0]
                    if (old.stop_price is not None and _decimal(old.raw_data.get("stopPrice")) == stop
                            and _decimal(old.raw_data.get("origQty", old.raw_data.get("quantity"))) == qty
                            and old.raw_data.get("workingType") == working):
                        self._verify_protection(params, old.order_id)
                        continue
                    replacement = dict(params, cancelOrderId=old.order_id, cancelReplaceMode="STOP_ON_FAILURE")
                    try:
                        data = self._data(self._make_request("post", "/openApi/swap/v1/trade/cancelReplace", replacement))
                        cancelled = _boolean(data.get("cancelResult"), "cancelResult")
                        replaced = _boolean(data.get("replaceResult"), "replaceResult")
                        new_id = (data.get("newOrderResponse") or {}).get("orderId")
                        if not cancelled or not replaced or not new_id:
                            self._unknown("BingX: cancelReplace частично выполнен; атомарность не гарантирована", replacement,
                                          cancel_result=cancelled, replace_result=replaced,
                                          order_id=str(new_id) if new_id else None, old_order_id=old.order_id)
                    except UnknownOrderStateError as exc:
                        self._unknown("BingX: результат cancelReplace неизвестен", replacement,
                                      cancel_result=getattr(exc, "cancel_result", None), replace_result=getattr(exc, "replace_result", None),
                                      order_id=getattr(exc, "order_id", None), old_order_id=old.order_id)
                    except (ExchangeStateUnavailableError, ExchangeAPIError):
                        self._unknown("BingX: ответ cancelReplace не подтверждает сохранность защиты", replacement,
                                      old_order_id=old.order_id)
                    order_id = str(new_id)
                    self._owned_orders[order_id] = rules.exchange_symbol
                else:
                    # BingX не документирует clientOrderId для conditional. Потерянный ответ — только UNKNOWN.
                    order_id = self._submit_order(params)
                self._owned_order_positions[order_id] = target.position_id
                self.last_submission = {"status": "ACKNOWLEDGED", "order_id": order_id,
                                        "position_id": target.position_id, "request_params": copy.deepcopy(params)}
                self._publish_submission(self.last_submission, submitted=True)
                self._verify_protection(params, order_id)
            self.last_protection_confirmed = True
            return True

    def cancel_order(self, symbol, order_id):
        self._allow_mutation()
        order_id = str(order_id)
        with self._trade_lock:
            if self._owned_orders.get(order_id) != self._format_symbol(symbol):
                _fail(UnsupportedCapabilityError, "BingX: отмена чужого ордера запрещена")
            order = self.get_order(symbol, order_id)
            if order.status == OrderStatus.CANCELED:
                return True
            if order.status not in _ACTIVE:
                _fail(ExchangeStateUnavailableError, "BingX: ордер уже терминальный, отмена не подтверждает закрытие")
            params = {"symbol": self._format_symbol(symbol), "orderId": order_id}
            try:
                self._envelope(self._make_request("delete", "/openApi/swap/v2/trade/order", params))
            except UnknownOrderStateError:
                pass
            try:
                if self.get_order(symbol, order_id).status == OrderStatus.CANCELED:
                    return True
            except ExchangeError:
                pass
            self._unknown("BingX: отмена не подтверждена", params, order_id=order_id)

    def cancel_all_orders(self, symbol):
        self._allow_mutation()
        # Имя оставлено для совместимости; биржевой allOpenOrders никогда не вызывается.
        for order in self.get_open_orders(symbol):
            if self._owned_orders.get(order.order_id) == self._format_symbol(symbol):
                self.cancel_order(symbol, order.order_id)
        return True

    def get_klines(self, symbol, interval="5m", limit=288):
        # Старый WS hook не гарантирует interval/freshness; до общего reader используем REST.
        return self._fetch_klines_rest(symbol, interval, limit)

    def _fetch_klines_rest(self, symbol, interval, limit):
        interval = _INTERVALS.get(interval, interval)
        if interval not in self._config.supported_intervals or not 1 <= _integer(limit, "limit") <= 1440:
            _fail(UnsupportedCapabilityError, "BingX: неверный interval/limit свечей")
        items = self._data(self._public_request("/openApi/swap/v3/quote/klines",
                           {"symbol": self._format_symbol(symbol), "interval": interval, "limit": limit}), list)
        result = []
        for item in items:
            if isinstance(item, dict):
                values = [item.get(k) for k in ("time", "open", "high", "low", "close", "volume")]
            elif isinstance(item, list) and len(item) >= 6:
                values = item[:6]
            else:
                _fail(ExchangeStateUnavailableError, "BingX: невалидная свеча")
            ts, opening, high, low, close, volume = values
            opening, high, low, close = [_decimal(v, "OHLC", True) for v in (opening, high, low, close)]
            volume = _decimal(volume, "volume")
            timestamp = _timestamp(ts)
            if timestamp is None or volume < 0 or not low <= min(opening, close) <= max(opening, close) <= high:
                _fail(ExchangeStateUnavailableError, "BingX: невалидные OHLCV/time свечи")
            if interval == "1M":
                end = timestamp.replace(year=timestamp.year + (timestamp.month == 12),
                                        month=1 if timestamp.month == 12 else timestamp.month + 1)
                closed = datetime.now(timezone.utc) >= end
            else:
                closed = int(time.time() * 1000) >= _integer(ts, "time") + self._interval_ms(interval)
            result.append(Kline(timestamp=timestamp.isoformat(), open=float(opening), high=float(high),
                                low=float(low), close=float(close), volume=float(volume), closed=closed))
        return sorted(result, key=lambda k: k.timestamp)[-limit:]

    @staticmethod
    def _interval_ms(interval):
        amount, unit = int(interval[:-1]), interval[-1]
        return amount * {"m": 60000, "h": 3600000, "d": 86400000, "w": 604800000, "M": 2678400000}[unit]

    def get_kline_data(self, symbol, interval="5m", limit=288):
        return [{"snapshotTimeUTC": str(k.timestamp), "openPrice": k.open, "highPrice": k.high,
                 "lowPrice": k.low, "closePrice": k.close, "volume": k.volume} for k in self.get_klines(symbol, interval, limit)]

    def get_ticker(self, symbol):
        raw = self._data(self._public_request("/openApi/swap/v2/quote/ticker", {"symbol": self._format_symbol(symbol)}))
        return Ticker(symbol=self._format_symbol(symbol), last_price=float(_decimal(raw.get("lastPrice"), "lastPrice", True)),
                      bid_price=float(_decimal(raw.get("bestBidPrice", 0), "bestBidPrice")),
                      ask_price=float(_decimal(raw.get("bestAskPrice", 0), "bestAskPrice")),
                      volume_24h=float(_decimal(raw.get("volume", 0), "volume")),
                      quote_volume_24h=float(_decimal(raw.get("quoteVolume", 0), "quoteVolume")))

    def get_order_book(self, symbol, limit=20):
        limit = _integer(limit, "limit")
        if not 1 <= limit <= 100:
            _fail(ExchangeStateUnavailableError, "BingX: depth limit вне диапазона")
        raw = self._data(self._public_request("/openApi/swap/v2/quote/depth", {"symbol": self._format_symbol(symbol), "limit": limit}))
        if not isinstance(raw.get("bids"), list) or not isinstance(raw.get("asks"), list):
            _fail(ExchangeStateUnavailableError, "BingX: невалидный стакан")
        def levels(items):
            result = []
            for item in items:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    _fail(ExchangeStateUnavailableError, "BingX: невалидный уровень стакана")
                price, qty = _decimal(item[0], "price", True), _decimal(item[1], "quantity")
                if qty < 0:
                    _fail(ExchangeStateUnavailableError, "BingX: отрицательный объём стакана")
                result.append([float(price), float(qty)])
            return result
        return OrderBook(symbol=self._format_symbol(symbol), bids=levels(raw["bids"]), asks=levels(raw["asks"]))

    def get_commission_rate(self, symbol):
        raw = self._data(self._make_request("get", "/openApi/swap/v2/user/commissionRate", {"symbol": self._format_symbol(symbol)}))
        return CommissionRate(maker=float(_decimal(raw.get("makerCommissionRate"), "makerCommissionRate") * 100),
                              taker=float(_decimal(raw.get("takerCommissionRate"), "takerCommissionRate") * 100))

    def get_funding_rate(self, symbol):
        raw = self._data(self._public_request("/openApi/swap/v2/quote/premiumIndex", {"symbol": self._format_symbol(symbol)}))
        rate = _decimal(raw.get("lastFundingRate"), "lastFundingRate")
        return FundingRate(funding_rate=float(rate), funding_rate_pct=float(rate * 100),
                           next_funding_time=_timestamp(raw.get("nextFundingTime")))


__all__ = ["BingXClient"]
