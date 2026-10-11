"""HTTP transport MEXC: точная подпись, fail-closed и однократные мутации."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import json
import math
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlencode, quote

import requests

from src.utils.logger import error
from ..errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError
from ..rate_limit import ExchangeRateLimiter, acquire_rate_limits, get_exchange_rate_limiters

_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
_RETRYABLE_FUTURES_CODES = {500, 501, 510, 801, 9999}
_AMBIGUOUS_CODES = (_RETRYABLE_FUTURES_CODES | {-1006, -1007, 503, 504, 603, 2042, 2056,
    3016, 3017, 3018, 3019, 6008, 9901, 9908, 9909, 9910, 9915, 9916})


def state_error(message: str):
    error(f"❌ {message}")
    raise ExchangeStateUnavailableError(message) from None


def mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        state_error(f"MEXC: {label} должен быть объектом")
    return value


def records(value, label: str) -> list:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        state_error(f"MEXC: {label} должен быть списком объектов")
    return value


def number(value, label: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    try:
        if isinstance(value, bool) or value in (None, ""):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        state_error(f"MEXC: некорректное число {label}")


def integer(value, label: str, *, positive: bool = False) -> int:
    result = number(value, label, positive=positive)
    if result != result.to_integral_value():
        state_error(f"MEXC: {label} должен быть целым числом")
    return int(result)


def unknown(message: str, *, symbol=None, client_order_id=None, order_id=None, operation=None):
    # Метаданные доступны журналу исполнения без изменения общего класса ошибок.
    exc = UnknownOrderStateError(message)
    exc.symbol = symbol
    exc.client_order_id = client_order_id
    exc.external_oid = client_order_id
    exc.order_id = order_id
    exc.operation = operation
    error(f"❌ {message}")
    return exc


def ambiguous_code(code: int) -> bool:
    return code in _AMBIGUOUS_CODES or code == 0


def execution_mode(config) -> str:
    mode = getattr(config, "execution_mode", "read_only")
    if mode not in {"read_only", "demo", "live"}:
        raise ExchangeAPIError("Неизвестный MEXC execution_mode")
    return mode


def assert_live_allowed(config) -> None:
    if execution_mode(config) != "live" or getattr(config, "is_demo", False):
        raise ExchangeAPIError("Мутации MEXC разрешены только в execution_mode=live; sandbox отсутствует")
    if not config.api_key or not config.secret_key:
        state_error("MEXC API ключи не настроены")


class _MEXCTransportBase:
    def __init__(self, config, session: Optional[requests.Session] = None, *, limiters=None, rate_limiters=None):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key or ""
        self.secret_key = config.secret_key or ""
        self.timeout = config.request_timeout
        self.session = session or requests.Session()
        if isinstance(self.session, requests.Session):
            # Ретраи HTTPAdapter не должны повторить POST ниже нашего уровня.
            self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            self.session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
        self.time_offset_ms = 0
        self._time_synced = False
        self._time_synced_at = 0.0
        self._cooldown_until = 0.0
        if limiters is not None and rate_limiters is not None:
            raise ValueError("Укажите только limiters или rate_limiters")
        supplied = rate_limiters if rate_limiters is not None else limiters
        if supplied is not None and not isinstance(supplied, Mapping):
            raise ValueError("MEXC rate_limiters должен быть mapping")
        if supplied is not None and any(not isinstance(key, str) or not callable(getattr(limiter, "acquire", None)) or not callable(getattr(limiter, "cooldown", None)) for key, limiter in supplied.items()):
            raise ValueError("MEXC: некорректный limiter adapter")
        # Пустая/частичная injection не отключает обязательные IP/UID/orders budgets.
        defaults = {} if supplied and all(supplied.get(key) is not None for key in ("public", "private", "orders")) else get_exchange_rate_limiters(
            "mexc", data_dir=getattr(config, "rate_limit_data_dir", None),
            db_path=getattr(config, "rate_limit_db_path", None),
            account_id=getattr(config, "rate_limit_account_id", None),
            ip_id=getattr(config, "rate_limit_ip_id", None),
            max_wait=getattr(config, "rate_limit_max_wait", None),
        )
        self.limiters = {**defaults, **(supplied or {})}
        limits = [getattr(limiter, "max_wait", 5.0) for limiter in self.limiters.values()]
        configured_wait = getattr(config, "rate_limit_max_wait", None)
        self._limiter_max_wait = float(min(limits) if configured_wait is None else configured_wait)
        if not math.isfinite(self._limiter_max_wait) or self._limiter_max_wait < 0:
            raise ValueError("MEXC: некорректный max_wait limiter")

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self.time_offset_ms

    def assert_private_allowed(self) -> None:
        if not self.api_key or not self.secret_key:
            state_error("MEXC API ключи не настроены")
        if execution_mode(self.config) == "demo" or getattr(self.config, "is_demo", False):
            raise ExchangeAPIError("MEXC в demo использует только публичные данные")

    def assert_mutation_allowed(self) -> None:
        assert_live_allowed(self.config)

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw is not None:
                try:
                    seconds = float(raw)
                except (TypeError, ValueError):
                    try:
                        seconds = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        seconds = -1
                if math.isfinite(seconds) and seconds >= 0:
                    return seconds
        return float(2 ** attempt)

    def _limits(self, method: str, endpoint: str, private: bool):
        keys = ["public"]
        if private:
            keys.append("private")
        if private and method != "GET" and ("order" in endpoint or "stoporder" in endpoint):
            keys.append("orders")
        keys.append(endpoint)
        keys.extend(key for key in self.limiters if key.endswith("*") and endpoint.startswith(key[:-1]))
        result = []
        for key in keys:
            limiter = self.limiters.get(key)
            if limiter is not None and all(limiter is not item for item in result):
                result.append(limiter)
        return result

    def _acquire(self, method: str, endpoint: str, private: bool):
        deadline = time.monotonic() + self._limiter_max_wait
        wait = self._cooldown_until - time.monotonic()
        if wait > 0:
            if wait > self._limiter_max_wait:
                state_error("MEXC: cooldown превышает max_wait; HTTP не отправлен")
            time.sleep(wait)
        selected = self._limits(method, endpoint, private)
        if not selected:
            state_error("MEXC: обязательный rate limiter отсутствует")
        shared, adapters = [], []
        for limiter in selected:
            # Вес IP/UID отличается от счётчика запросов orders/endpoint.
            weighted = limiter is self.limiters.get("public") or limiter is self.limiters.get("private")
            item = (limiter, self._weight(method, endpoint, private) if weighted else 1)
            (shared if isinstance(limiter, ExchangeRateLimiter) else adapters).append(item)
        if shared:
            acquire_rate_limits(shared, max_wait=max(0.0, deadline - time.monotonic()))
        for limiter, weight in adapters:
            limiter.acquire(weight=weight)
        if self._limiter_max_wait > 0 and time.monotonic() > deadline:
            state_error("MEXC: истёк max_wait limiter; HTTP не отправлен")

    def _weight(self, method: str, endpoint: str, private: bool) -> int:
        return 1

    def _cooldown(self, response, attempt: int, endpoint: str, private: bool):
        delay = self._retry_delay(response, attempt)
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay)
        # Бан касается всего host, а не только запроса, который получил 429.
        seen = set()
        for limiter in self.limiters.values():
            if id(limiter) not in seen:
                limiter.cooldown(delay)
                seen.add(id(limiter))

    def _prepare(self, method, endpoint, params, private):
        raise NotImplementedError

    def _response_error(self, data):
        raise NotImplementedError

    def request(self, method: str, endpoint: str, params=None, *, private=False, mutation=False, max_attempts=None):
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ValueError("Неподдерживаемый HTTP метод MEXC")
        if endpoint.startswith("/api/v1/private/") or endpoint in {
            "/api/v3/order", "/api/v3/order/test", "/api/v3/account", "/api/v3/tradeFee",
            "/api/v3/openOrders", "/api/v3/allOrders", "/api/v3/myTrades",
        }:
            private = True
        # Нельзя обойти предохранитель, забыв mutation=True для приватного POST/DELETE.
        test_order = isinstance(self, MEXCSpotTransport) and endpoint == "/api/v3/order/test"
        mutation = bool(mutation or (private and method != "GET" and not test_order))
        if mutation:
            private = True
            self.assert_mutation_allowed()
        elif private:
            self.assert_private_allowed()
        if mutation and method == "GET":
            raise ValueError("Мутации MEXC не могут использовать GET")
        if private and (not self._time_synced or (self._time_synced_at and time.monotonic() - self._time_synced_at > 30)):
            self.sync_time()
        requested_attempts = 3 if max_attempts is None else max_attempts
        if isinstance(requested_attempts, bool) or not isinstance(requested_attempts, int) or requested_attempts < 1:
            raise ValueError("max_attempts должен быть положительным целым числом")
        attempts = 1 if method != "GET" else min(3, requested_attempts)
        for attempt in range(attempts):
            self._acquire(method, endpoint, private)
            # Подпись формируется после ожидания, иначе timestamp может устареть.
            kwargs = self._prepare(method, endpoint, params, private)
            response = None
            try:
                response = self.session.request(
                    method, f"{self.base_url}{endpoint}", timeout=self.timeout,
                    allow_redirects=False, **kwargs,
                )
            except requests.exceptions.RequestException as exc:
                if mutation:
                    raise unknown("Результат отправки MEXC неизвестен после сетевого сбоя", operation=endpoint) from None
                if attempt + 1 < attempts:
                    time.sleep(self._retry_delay(None, attempt))
                    continue
                state_error("MEXC недоступен после сетевого сбоя")
            status = response.status_code
            if status in {418, 429}:
                self._cooldown(response, attempt, endpoint, private)
                if not mutation and status == 429 and attempt + 1 < attempts:
                    continue
                raise ExchangeAPIError(f"MEXC ограничил запросы (HTTP {status})", code=status, retryable=True)
            if status >= 500 or 300 <= status < 400:
                if "Retry-After" in response.headers:
                    try:
                        self._cooldown(response, attempt, endpoint, private)
                    except ExchangeStateUnavailableError:
                        if mutation:
                            raise unknown("Результат MEXC неизвестен; durable cooldown не записан", operation=endpoint)
                        raise
                if mutation:
                    raise unknown(f"Результат отправки MEXC неизвестен (HTTP {status})", operation=endpoint)
                if status in _RETRYABLE_HTTP and attempt + 1 < attempts:
                    if "Retry-After" not in response.headers:
                        time.sleep(self._retry_delay(response, attempt))
                    continue
                state_error(f"MEXC недоступен (HTTP {status})")
            try:
                data = response.json()
                code = self._response_error(data)
            except (ValueError, ExchangeStateUnavailableError) as exc:
                if mutation:
                    raise unknown("Результат MEXC неизвестен: некорректная схема ответа", operation=endpoint) from exc
                state_error("MEXC вернул некорректный JSON/схему ответа")
            if code is not None:
                if code == 510:
                    self._cooldown(response, attempt, endpoint, private)
                if mutation and ambiguous_code(code):
                    raise unknown(f"Результат MEXC неизвестен (код {code})", operation=endpoint)
                if not mutation and code in _RETRYABLE_FUTURES_CODES and attempt + 1 < attempts:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                if not mutation and code == 700003 and private and attempt + 1 < attempts:
                    self.sync_time()
                    continue
                error(f"❌ MEXC отклонил запрос: код {code}")
                raise ExchangeAPIError(f"MEXC отклонил запрос: код {code}", code=code, retryable=code in _RETRYABLE_FUTURES_CODES)
            if status >= 400:
                if status in _RETRYABLE_HTTP and not mutation and attempt + 1 < attempts:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                if status in _RETRYABLE_HTTP and not mutation:
                    state_error(f"MEXC недоступен (HTTP {status})")
                raise ExchangeAPIError(f"MEXC отклонил запрос (HTTP {status})", code=status)
            return data
        state_error("MEXC исчерпал попытки чтения")

    def sync_time(self):
        before = int(time.time() * 1000)
        data = self.request("GET", self.TIME_ENDPOINT)
        server = self._server_time(data)
        self.time_offset_ms = server - (before + int(time.time() * 1000)) // 2
        self._time_synced = True
        self._time_synced_at = time.monotonic()


class MEXCSpotTransport(_MEXCTransportBase):
    TIME_ENDPOINT = "/api/v3/time"

    def _weight(self, method, endpoint, private):
        # /api-docs/spot-v3/market-data-endpoints/order-book: Weight(IP)=3.
        return {"/api/v3/depth": 3, "/api/v3/exchangeInfo": 10, "/api/v3/account": 10, "/api/v3/tradeFee": 20,
                "/api/v3/allOrders": 10, "/api/v3/openOrders": 3}.get(endpoint, 2 if endpoint == "/api/v3/order" and method == "GET" else 1)

    def _server_time(self, data):
        return integer(mapping(data, "время").get("serverTime"), "serverTime", positive=True)

    def _response_error(self, data):
        if not isinstance(data, (dict, list)):
            state_error("MEXC Spot: ответ не является объектом/списком")
        if isinstance(data, dict) and "code" in data:
            code = integer(data["code"], "code")
            return None if code in {0, 200} else code
        return None

    def _prepare(self, method, endpoint, params, private):
        if params is not None and not isinstance(params, dict):
            raise ValueError("Параметры MEXC Spot должны быть объектом")
        payload = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if private:
            payload.setdefault("recvWindow", 5000)
            payload["timestamp"] = self._timestamp()
            unsigned = sorted(payload.items())
            query = urlencode(unsigned, doseq=True)
            signed = hmac.new(self.secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()
            payload = unsigned + [("signature", signed)]
            headers["X-MEXC-APIKEY"] = self.api_key
        return {"params": payload, "headers": headers}


class MEXCFuturesTransport(_MEXCTransportBase):
    TIME_ENDPOINT = "/api/v1/contract/ping"

    def _server_time(self, data):
        return integer(data.get("data"), "время сервера", positive=True)

    def _response_error(self, data):
        mapping(data, "ответ Futures")
        if not isinstance(data.get("success"), bool):
            state_error("MEXC Futures: отсутствует success")
        code = integer(data.get("code", 0), "code")
        if not data["success"] or code != 0:
            return code
        return None

    def _prepare(self, method, endpoint, params, private):
        payload = {k: v for k, v in (params or {}).items() if v is not None} if isinstance(params, dict) or params is None else params
        headers = {"Content-Type": "application/json"}
        query_params, body = None, None
        if method in {"GET", "DELETE"}:
            if not isinstance(payload, dict):
                raise ValueError("GET/DELETE параметры MEXC Futures должны быть объектом")
            query_params = sorted(payload.items())
            parameter_string = urlencode(query_params, doseq=True, quote_via=quote)
            # Подписанная строка и отправленная строка должны совпадать даже для пробелов.
            query_params = parameter_string
        else:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
            parameter_string = body
        if private:
            timestamp = str(self._timestamp())
            target = f"{self.api_key}{timestamp}{parameter_string}"
            headers.update({"ApiKey": self.api_key, "Request-Time": timestamp, "Recv-Window": "10",
                            "Signature": hmac.new(self.secret_key.encode(), target.encode(), hashlib.sha256).hexdigest()})
        return {"params": query_params, "data": body, "headers": headers}
