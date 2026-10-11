"""Offline-регрессии публичного REST: явный allowlist и общий durable cooldown."""

import json
from unittest.mock import Mock, call

import pytest
import requests

from src.exchanges.errors import ExchangeStateUnavailableError
from src.exchanges.market_data import StreamKey
from src.exchanges.public_market_rest import PublicRESTFetcher
from src.exchanges import rate_limit


T = 1_735_689_600_000
BINGX = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
MEXC_SPOT = StreamKey("mexc", "spot", "BTCUSDT", "1m")
MEXC_FUTURES = StreamKey("mexc", "perpetual", "BTCUSDT", "1m")
BODY_CASES = [(BINGX, code) for code in (
    429, 500, 502, 503, 504, 100410, 100500, 100503, 109429, 109500,
)] + [(MEXC_SPOT, 429), (MEXC_FUTURES, 510)]


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Тест не должен отправлять реальные HTTP-запросы")
    monkeypatch.setattr(requests.sessions.Session, "request", fail)


def response(payload, status=200, headers=None):
    value = requests.Response()
    value.status_code = status
    value.headers.update(headers or {})
    value.url = "https://public.invalid/klines"
    value._content = json.dumps(payload).encode()
    return value


def successful_payload(key):
    candle = {"timestamp": T, "open": "10", "high": "12", "low": "9", "close": "11", "volume": "2"}
    if key.exchange == "bingx":
        return {"code": 0, "data": [candle]}
    if key.market_type == "spot":
        return [[T, "10", "12", "9", "11", "2"]]
    return {"success": True, "code": 0, "data": {
        "time": [T // 1000], "open": ["10"], "high": ["12"], "low": ["9"], "close": ["11"], "vol": ["2"],
    }}


def body_error(key, code):
    payload = {"code": code, "msg": "请稍后重试"}
    if key == MEXC_FUTURES:
        payload["success"] = False
    return payload


def fetch_page(fetcher, key=BINGX):
    return fetcher(key, start_ms=T, end_ms=T + 60_000, limit=1)


@pytest.mark.parametrize("key,code", BODY_CASES)
def test_documented_or_current_transient_body_retries_same_public_request(key, code):
    limiter, sleeper = Mock(), Mock()
    request = Mock(side_effect=[response(body_error(key, code)), response(successful_payload(key))])
    fetcher = PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={key.exchange: limiter})

    rows = fetch_page(fetcher, key)

    assert len(rows) == 1
    assert request.call_count == 2
    assert request.call_args_list[0] == request.call_args_list[1]
    kwargs = request.call_args.kwargs
    assert set(kwargs) == {"params", "timeout"}
    assert not {"signature", "timestamp", "apiKey", "secretKey"} & kwargs["params"].keys()
    if key.exchange == "bingx":
        assert kwargs["params"]["symbol"] == "BTC-USDT"
        assert request.call_args.args[0] == "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
    assert limiter.acquire.call_args_list == [call(weight=1), call(weight=1)]
    limiter.cooldown.assert_called_once_with(1.0)
    sleeper.assert_called_once_with(1)


@pytest.mark.parametrize("key,code", BODY_CASES)
@pytest.mark.parametrize("attempts", [1, 3])
def test_transient_body_exhaustion_is_bounded_and_final_cooldown_is_preserved(key, code, attempts):
    limiter, sleeper = Mock(), Mock()
    request = Mock(return_value=response(body_error(key, code)))
    fetcher = PublicRESTFetcher(settings={"rest_retries": attempts}, request=request, sleep=sleeper,
                                rate_limiters={key.exchange: limiter})

    with pytest.raises(ValueError, match=str(code)):
        fetch_page(fetcher, key)

    assert request.call_count == limiter.acquire.call_count == attempts
    assert limiter.cooldown.call_args_list == [call(float(2 ** i)) for i in range(attempts)]
    assert sleeper.call_args_list == [call(2 ** i) for i in range(attempts - 1)]


@pytest.mark.parametrize("key,code", [
    (BINGX, 100400), (BINGX, 100401), (BINGX, 100403), (BINGX, 100440), (BINGX, 80014),
    (BINGX, 999999), (MEXC_SPOT, -1003), (MEXC_SPOT, 510), (MEXC_SPOT, 10007),
    (MEXC_SPOT, 500), (MEXC_SPOT, 503), (MEXC_FUTURES, 429), (MEXC_FUTURES, 600),
    (MEXC_FUTURES, 500), (MEXC_FUTURES, 501), (MEXC_FUTURES, 801), (MEXC_FUTURES, 9999),
])
def test_permanent_unknown_and_other_market_body_codes_do_not_retry(key, code):
    limiter, sleeper = Mock(), Mock()
    request = Mock(return_value=response(body_error(key, code)))
    fetcher = PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={key.exchange: limiter})

    with pytest.raises(ValueError):
        fetch_page(fetcher, key)

    request.assert_called_once()
    limiter.acquire.assert_called_once_with(weight=1)
    limiter.cooldown.assert_not_called()
    sleeper.assert_not_called()


@pytest.mark.parametrize("key,payload", [
    (BINGX, []), (BINGX, {}), (BINGX, {"code": False, "data": []}),
    (BINGX, {"code": 0.0, "data": []}), (BINGX, {"code": "0", "data": []}),
    (BINGX, {"code": "100410"}), (BINGX, {"code": 100410.0}), (BINGX, {"code": [100410]}),
    (BINGX, {"code": 0}), (BINGX, {"code": 0, "data": {}}),
    (BINGX, {"code": 0, "data": [[T, "10"]]}),
    (MEXC_SPOT, {}), (MEXC_SPOT, {"code": "429"}), (MEXC_SPOT, {"code": 429.0}),
    (MEXC_FUTURES, {}), (MEXC_FUTURES, {"success": True}),
    (MEXC_FUTURES, {"success": True, "code": False, "data": {}}),
    (MEXC_FUTURES, {"success": False, "code": "510"}),
    (MEXC_FUTURES, {"success": True, "code": 600, "data": {}}),
    (MEXC_FUTURES, {"success": True, "data": []}),
    (MEXC_FUTURES, {"success": True, "data": {}}),
    (MEXC_FUTURES, {"success": True, "data": {
        "time": [T // 1000], "open": [10], "high": [12], "low": [9], "close": [], "vol": [2],
    }}),
])
def test_payload_schema_stays_strict_and_malformed_codes_are_not_coerced(key, payload):
    limiter, sleeper = Mock(), Mock()
    request = Mock(return_value=response(payload))
    fetcher = PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={key.exchange: limiter})

    with pytest.raises(ValueError):
        fetch_page(fetcher, key)

    request.assert_called_once()
    limiter.cooldown.assert_not_called()
    sleeper.assert_not_called()


@pytest.mark.parametrize("key", [BINGX, MEXC_SPOT, MEXC_FUTURES])
def test_valid_empty_payload_remains_empty_without_retries(key):
    payload = successful_payload(key)
    if key.exchange == "bingx":
        payload["data"] = []
    elif key.market_type == "spot":
        payload = []
    else:
        payload["data"] = {name: [] for name in payload["data"]}
    request, limiter = Mock(return_value=response(payload)), Mock()
    assert fetch_page(PublicRESTFetcher(request=request, rate_limiters={key.exchange: limiter}), key) == []
    request.assert_called_once()
    limiter.cooldown.assert_not_called()


@pytest.mark.parametrize("header,expected", [
    ("7", 7.0), ("0", 1.0), ("broken", 1.0), ("-1", 1.0),
    ("nan", 1.0), ("inf", 1.0), ("31536001", 1.0),
])
def test_body_cooldown_honors_retry_after_or_safe_fallback(header, expected):
    limiter, sleeper = Mock(), Mock()
    request = Mock(side_effect=[response(body_error(BINGX, 100410), headers={"Retry-After": header}),
                                response(successful_payload(BINGX))])
    assert fetch_page(PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={"bingx": limiter}))
    limiter.cooldown.assert_called_once_with(expected)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_http_still_uses_shared_cooldown(status):
    limiter, sleeper = Mock(), Mock()
    request = Mock(side_effect=[response({}, status, {"Retry-After": "5"}), response(successful_payload(BINGX))])
    assert fetch_page(PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={"bingx": limiter}))
    assert request.call_count == limiter.acquire.call_count == 2
    limiter.cooldown.assert_called_once_with(5.0)


@pytest.mark.parametrize("key", [BINGX, MEXC_SPOT, MEXC_FUTURES])
@pytest.mark.parametrize("header,expected", [(None, 60.0), ("120", 120.0), ("nan", 60.0)])
def test_http_418_does_not_retry_even_with_transient_body_but_keeps_cooldown(key, header, expected):
    headers = {} if header is None else {"Retry-After": header}
    code = 100410 if key.exchange == "bingx" else (429 if key.market_type == "spot" else 510)
    limiter, sleeper = Mock(), Mock()
    request = Mock(return_value=response(body_error(key, code), 418, headers))
    fetcher = PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={key.exchange: limiter})
    with pytest.raises(requests.HTTPError) as caught:
        fetch_page(fetcher, key)
    assert caught.value.response.status_code == 418
    request.assert_called_once()
    limiter.cooldown.assert_called_once_with(expected)
    sleeper.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_http_does_not_retry_or_classify_transient_body(status):
    limiter, sleeper = Mock(), Mock()
    request = Mock(return_value=response(body_error(BINGX, 100410), status))
    with pytest.raises(requests.HTTPError):
        fetch_page(PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={"bingx": limiter}))
    request.assert_called_once()
    limiter.cooldown.assert_not_called()
    sleeper.assert_not_called()


def test_transport_and_body_errors_share_one_attempt_budget():
    limiter, sleeper = Mock(), Mock()
    request = Mock(side_effect=[requests.Timeout("Таймаут"), response(body_error(BINGX, 100410)),
                                response(body_error(BINGX, 100500)), response(successful_payload(BINGX))])
    with pytest.raises(ValueError, match="100500"):
        fetch_page(PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={"bingx": limiter}))
    assert request.call_count == limiter.acquire.call_count == 3
    assert sleeper.call_args_list == [call(1), call(2)]
    assert limiter.cooldown.call_args_list == [call(2.0), call(4.0)]


@pytest.mark.parametrize("key,code", [(BINGX, 100410), (MEXC_SPOT, 429), (MEXC_FUTURES, 510)])
@pytest.mark.parametrize("status", [200, 418])
def test_exhausted_body_or_ban_blocks_recreated_reader_via_durable_limiter(tmp_path, monkeypatch, key, code, status):
    tick = [1_000_000_000]
    monkeypatch.setattr(rate_limit, "_clock_sample", lambda: ("test-boot", tick[0]))
    monkeypatch.setenv("SERVO_RATE_LIMIT_MAX_WAIT", "0")
    request = Mock(return_value=response(body_error(key, code), status, {"Retry-After": "7"}))
    fetcher = PublicRESTFetcher(settings={"rest_retries": 1, "data_dir": str(tmp_path)}, request=request)
    with pytest.raises(requests.HTTPError if status == 418 else ValueError):
        fetch_page(fetcher, key)
    request.assert_called_once()

    # Новый reader, другой символ/market и другой объект limiter разделяют host/IP cooldown.
    other_key = StreamKey(key.exchange, "spot" if key == MEXC_FUTURES else key.market_type, "ETHUSDT", "1m")
    other_request = Mock(return_value=response(successful_payload(other_key)))
    other = PublicRESTFetcher(settings={"data_dir": str(tmp_path)}, request=other_request)
    with pytest.raises(ExchangeStateUnavailableError):
        fetch_page(other, other_key)
    other_request.assert_not_called()
    tick[0] += 7_000_000_000
    assert fetch_page(other, other_key)
    other_request.assert_called_once()
