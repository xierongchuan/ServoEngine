"""HTTP transport и две независимые схемы подписи MEXC."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from ..errors import ExchangeAPIError, ExchangeStateUnavailableError


_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
_RETRYABLE_FUTURES_CODES = {500, 501, 510, 801}


class _MEXCTransportBase:
    def __init__(self, config, session: Optional[requests.Session] = None):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key or ""
        self.secret_key = config.secret_key or ""
        self.timeout = config.request_timeout
        self.session = session or requests.Session()
        self.time_offset_ms = 0
        self._time_synced = False

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self.time_offset_ms

    def _assert_credentials(self) -> None:
        if not self.api_key or not self.secret_key:
            raise ExchangeStateUnavailableError("MEXC API ключи не настроены")

    def assert_private_allowed(self) -> None:
        self._assert_credentials()
        if self.config.is_demo:
            raise ExchangeAPIError("MEXC при MODE=demo разрешает только публичные данные")

    def assert_mutation_allowed(self) -> None:
        self._assert_credentials()
        if self.config.is_demo:
            raise ExchangeAPIError("MEXC API не имеет sandbox: мутации запрещены при MODE=demo")
        if not self.config.live_trading_enabled:
            raise ExchangeAPIError("MEXC live trading выключен; задайте MEXC_ENABLE_LIVE_TRADING=true")

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        if response is not None:
            try:
                return min(float(response.headers.get("Retry-After", 0)), 30.0) or float(2 ** attempt)
            except (TypeError, ValueError):
                pass
        return float(2 ** attempt)


class MEXCSpotTransport(_MEXCTransportBase):
    """Spot V3: HMAC от точной query string."""

    def sync_time(self) -> None:
        try:
            response = self.session.get(f"{self.base_url}/api/v3/time", timeout=self.timeout)
            response.raise_for_status()
            self.time_offset_ms = int(response.json()["serverTime"]) - int(time.time() * 1000)
            self._time_synced = True
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise ExchangeStateUnavailableError("Не удалось синхронизировать время MEXC Spot") from exc

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        private: bool = False,
        mutation: bool = False,
        max_attempts: Optional[int] = None,
    ) -> Any:
        method = method.upper()
        if mutation:
            self.assert_mutation_allowed()
        elif private:
            self.assert_private_allowed()
        if private and not self._time_synced:
            self.sync_time()

        attempts = max_attempts or (3 if method == "GET" else 1)
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            if isinstance(params, dict):
                payload = {k: v for k, v in params.items() if v is not None}
            else:
                payload = params or []
            headers: Dict[str, str] = {}
            request_params = payload
            if private:
                payload.setdefault("recvWindow", 5000)
                payload["timestamp"] = self._timestamp()
                query = urlencode(sorted(payload.items()), doseq=True)
                signature = hmac.new(
                    self.secret_key.encode(), query.encode(), hashlib.sha256
                ).hexdigest().lower()
                request_params = sorted(payload.items()) + [("signature", signature)]
                headers["X-MEXC-APIKEY"] = self.api_key
            response = None
            try:
                response = self.session.request(
                    method, f"{self.base_url}{endpoint}", params=request_params,
                    headers=headers, timeout=self.timeout,
                )
                if response.status_code in _RETRYABLE_HTTP and attempt + 1 < attempts:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and "code" in data and data.get("code") not in (0, 200, None):
                    raise ExchangeAPIError(
                        f"MEXC Spot API error {data.get('code')}: {data.get('msg', '')}",
                        code=data.get("code"),
                    )
                return data
            except requests.HTTPError as exc:
                status = response.status_code if response is not None else None
                if status in _RETRYABLE_HTTP:
                    raise ExchangeStateUnavailableError(
                        f"MEXC Spot временно недоступен (HTTP {status})"
                    ) from exc
                raise ExchangeAPIError(f"MEXC Spot HTTP error {status}", code=status) from exc
            except (requests.Timeout, requests.ConnectionError, requests.ChunkedEncodingError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                raise ExchangeStateUnavailableError(f"MEXC Spot network error: {exc}") from exc
            except ValueError as exc:
                raise ExchangeStateUnavailableError("MEXC Spot вернул невалидный JSON") from exc
        raise ExchangeStateUnavailableError(f"MEXC Spot request failed: {last_error}")


class MEXCFuturesTransport(_MEXCTransportBase):
    """Futures V1: header signature, GET query и точный JSON POST."""

    def sync_time(self) -> None:
        try:
            response = self.session.get(f"{self.base_url}/api/v1/contract/ping", timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            self.time_offset_ms = int(data["data"]) - int(time.time() * 1000)
            self._time_synced = True
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise ExchangeStateUnavailableError("Не удалось синхронизировать время MEXC Futures") from exc

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        private: bool = False,
        mutation: bool = False,
        max_attempts: Optional[int] = None,
    ) -> Any:
        method = method.upper()
        if mutation:
            self.assert_mutation_allowed()
        elif private:
            self.assert_private_allowed()
        if private and not self._time_synced:
            self.sync_time()

        attempts = max_attempts or (3 if method == "GET" else 1)
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            if isinstance(params, dict):
                payload = {k: v for k, v in params.items() if v is not None}
            else:
                payload = params or []
            headers: Dict[str, str] = {"Content-Type": "application/json"}
            query_params = None
            body = None
            if method in {"GET", "DELETE"}:
                if not isinstance(payload, dict):
                    raise ValueError("GET/DELETE параметры MEXC Futures должны быть словарём")
                parameter_string = urlencode(sorted(payload.items()), doseq=True)
                query_params = sorted(payload.items())
            else:
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                parameter_string = body
            if private:
                timestamp = str(self._timestamp())
                target = f"{self.api_key}{timestamp}{parameter_string}"
                headers.update({
                    "ApiKey": self.api_key,
                    "Request-Time": timestamp,
                    "Recv-Window": "10",
                    "Signature": hmac.new(
                        self.secret_key.encode(), target.encode(), hashlib.sha256
                    ).hexdigest(),
                })
            response = None
            try:
                response = self.session.request(
                    method, f"{self.base_url}{endpoint}", params=query_params,
                    data=body, headers=headers, timeout=self.timeout,
                )
                if response.status_code in _RETRYABLE_HTTP and attempt + 1 < attempts:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and data.get("success") is False:
                    code = data.get("code")
                    retryable = code in _RETRYABLE_FUTURES_CODES
                    if retryable and attempt + 1 < attempts:
                        time.sleep(self._retry_delay(response, attempt))
                        continue
                    raise ExchangeAPIError(
                        f"MEXC Futures API error {code}: {data.get('message', '')}",
                        code=code, retryable=retryable,
                    )
                return data
            except requests.HTTPError as exc:
                status = response.status_code if response is not None else None
                if status in _RETRYABLE_HTTP:
                    raise ExchangeStateUnavailableError(
                        f"MEXC Futures временно недоступен (HTTP {status})"
                    ) from exc
                raise ExchangeAPIError(f"MEXC Futures HTTP error {status}", code=status) from exc
            except (requests.Timeout, requests.ConnectionError, requests.ChunkedEncodingError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                raise ExchangeStateUnavailableError(f"MEXC Futures network error: {exc}") from exc
            except ValueError as exc:
                raise ExchangeStateUnavailableError("MEXC Futures вернул невалидный JSON") from exc
        raise ExchangeStateUnavailableError(f"MEXC Futures request failed: {last_error}")
