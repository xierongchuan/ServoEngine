"""BingX redirects: настоящий requests, офлайн HTTPAdapter и durable UNKNOWN без resend."""

import io
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from src.core.execution_coordinator import attach_execution_safety
from src.exchanges.config.bingx_config import BingXConfig
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError
from src.exchanges.impl.bingx_client import BingXClient
from test_bingx_safety_regressions import FakeExchange


REDIRECTS = [301, 302, 303, 307, 308]
ORDER_PATH = "/openApi/swap/v2/trade/order"
CLOCK_PATH = "/openApi/swap/v2/server/time"
FAKE_KEY = "redirect-test-fake-key"
FAKE_SECRET = "redirect-test-fake-secret"
LOCATION = ("https://open-api.bingx.com" + ORDER_PATH
            + "?signature=redirect-location-secret&APIKEY=redirect-location-key")


def response_for(request, status=200, payload=None, headers=None):
    response = requests.Response()
    response.request, response.url = request, request.url
    response.status_code = status
    response.headers.update(headers or {})
    response._content = json.dumps(payload if payload is not None else {"code": 0, "data": {}}).encode()
    response.raw = io.BytesIO(response._content)
    return response


def redirect_response(request, status):
    response = response_for(request, status, headers={"Location": LOCATION})
    # Даже JSON с code=0 не превращает HTTP 3xx в ACK или результат чтения.
    response.json = lambda: pytest.fail("BingX не должен разбирать JSON ответа HTTP 3xx")
    return response


class OfflineAdapter(requests.adapters.BaseAdapter):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.handler = lambda request: pytest.fail("Непредусмотренный офлайн HTTP-запрос")

    def send(self, request, **kwargs):
        self.calls.append(request)
        return self.handler(request)

    def close(self):
        pass


@pytest.fixture
def transport(monkeypatch):
    adapter = OfflineAdapter()
    original_session = requests.sessions.Session

    class OfflineSession(original_session):
        def __init__(self):
            super().__init__()
            self.trust_env = False
            self.mount("https://", adapter)
            self.mount("http://", adapter)

    # Не подменяем requests.get/post/delete/send: redirects обрабатывает сама библиотека.
    monkeypatch.setattr(requests.sessions, "Session", OfflineSession)
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.sleep",
                        lambda _: pytest.fail("HTTP 3xx не должен запускать retry/backoff"))
    return adapter


def new_client(mode="demo", events=None):
    return BingXClient(BingXConfig(settings={"exchange": "bingx", "execution_mode": mode}),
        api_key=FAKE_KEY, secret_key=FAKE_SECRET,
        rate_limiter=SimpleNamespace(acquire=lambda: None),
        submission_observer=events.append if events is not None else None)


def assert_no_secrets(caplog, exc=None):
    text = caplog.text + (str(exc) if exc is not None else "")
    for secret in (LOCATION, "redirect-location-secret", "redirect-location-key",
                   FAKE_KEY, FAKE_SECRET, "signature=", "X-BX-APIKEY", "Location"):
        assert secret not in text


def test_adapter_exercises_real_requests_redirect_handling(transport):
    def handler(request):
        if len(transport.calls) == 1:
            return redirect_response(request, 307)
        return response_for(request)

    transport.handler = handler
    requests.post("https://open-api-vst.bingx.com" + ORDER_PATH,
                  data="test-body", headers={"X-BX-APIKEY": FAKE_KEY})
    assert len(transport.calls) == 2
    assert [r.method for r in transport.calls] == ["POST", "POST"]
    assert transport.calls[1].body == "test-body"
    assert transport.calls[1].headers["X-BX-APIKEY"] == FAKE_KEY
    assert urlsplit(transport.calls[1].url).hostname == "open-api.bingx.com"


@pytest.mark.parametrize("status", REDIRECTS + [300, 304, 399])
@pytest.mark.parametrize("method", ["get", "post", "delete"])
@pytest.mark.parametrize("mode", ["demo", "live"])
def test_private_redirect_is_one_send_typed_error_with_context(transport, caplog, status, method, mode):
    events = []
    client = new_client(mode, events)
    transport.handler = lambda request: redirect_response(request, status)
    params = {"symbol": "BTC-USDT", "clientOrderId": "redirect-command",
              "side": "SELL", "positionSide": "LONG", "type": "STOP_MARKET",
              "quantity": "0.01", "stopPrice": "49000", "workingType": "MARK_PRICE"}
    expected = dict(params)
    error_type = ExchangeStateUnavailableError if method == "get" else UnknownOrderStateError
    with pytest.raises(error_type) as caught:
        client._request(method, ORDER_PATH, params)
    exc = caught.value
    assert exc.method == method and exc.endpoint == ORDER_PATH and exc.http_status == status
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request.method == method.upper()
    assert urlsplit(request.url).hostname == urlsplit(client.base_url).hostname
    assert request.headers["X-BX-APIKEY"] == FAKE_KEY
    if method == "get":
        assert not events and client._generation == 0
    else:
        assert exc.submission_status == "UNKNOWN"
        assert exc.symbol == "BTC-USDT" and exc.client_order_id == "redirect-command"
        params["quantity"] = "99"
        assert exc.request_params == expected
        assert [event["status"] for event in events] == ["PREPARED"]
        assert events[0]["request_params"] == expected
        assert client._generation == 1
    assert_no_secrets(caplog, exc)


@pytest.mark.parametrize("status", REDIRECTS)
def test_public_get_redirect_is_not_followed_or_retried(transport, caplog, status):
    client = new_client()
    transport.handler = lambda request: redirect_response(request, status)
    with pytest.raises(ExchangeStateUnavailableError):
        client._public_request("/openApi/swap/v2/quote/ticker", {"symbol": "BTC-USDT"})
    assert len(transport.calls) == 1 and transport.calls[0].method == "GET"
    assert "X-BX-APIKEY" not in transport.calls[0].headers
    assert "signature=" not in transport.calls[0].url
    assert_no_secrets(caplog)


@pytest.mark.parametrize("status", REDIRECTS)
def test_server_time_redirect_cannot_change_clock_or_host(transport, caplog, status):
    client = new_client()
    client._time_offset_ms = 123
    transport.handler = lambda request: redirect_response(request, status)
    assert client._sync_server_clock() is False
    assert client._time_offset_ms == 123
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request.method == "GET" and request.url == client.base_url + CLOCK_PATH
    assert "X-BX-APIKEY" not in request.headers
    assert_no_secrets(caplog)


@pytest.mark.parametrize("status", REDIRECTS)
@pytest.mark.parametrize("method", ["post", "delete"])
def test_redirect_keeps_one_journal_wire_unknown_and_forbids_replay(transport, tmp_path, caplog, status, method):
    fake = FakeExchange()

    def normal_response(request):
        result = fake.request(request.method.lower(), request.url, data=request.body)
        return response_for(request, result.status_code, result.payload)

    transport.handler = normal_response
    raw = new_client()
    client = attach_execution_safety(raw, journal_path=tmp_path / "redirect.sqlite",
        settings={"exchange": "bingx", "execution_mode": "demo"}, owner_id="redirect-owner")
    oid = client.place_order("BTCUSDT", "BUY", ".01", command_id="entry",
        order_type="MARKET" if method == "post" else "LIMIT", price=49000)

    def redirected_mutation(request):
        if request.method == method.upper() and urlsplit(request.url).path == ORDER_PATH:
            return redirect_response(request, status)
        return normal_response(request)

    transport.handler = redirected_mutation
    before = len(transport.calls)

    def operation():
        if method == "post":
            return client.set_sl_tp("BTCUSDT", "LONG", sl=48000,
                                   position_id="p1", command_id="redirect-mutation")
        return client.cancel_order("BTCUSDT", oid, command_id="redirect-mutation")

    with pytest.raises(UnknownOrderStateError) as caught:
        operation()
    intent = client.journal.get(client.account_id, "redirect-mutation")
    assert intent["state"] == "UNKNOWN" and intent["order_id"] is None
    wires = client.journal.wires(client.account_id, "redirect-mutation")
    assert len(wires) == 1
    event = wires[0]["event"]
    assert event["status"] == "PREPARED" and event["method"] == method
    assert event["endpoint"] == ORDER_PATH
    if method == "post":
        assert event["request_params"] == caught.value.request_params
    else:
        assert event["request_params"] == {"symbol": "BTC-USDT", "orderId": oid}
        assert caught.value.request_params == intent["request"]
    assert event["request_params"]["symbol"] == "BTC-USDT"
    assert caught.value.command_id == "redirect-mutation"
    assert client.is_symbol_blocked("BTCUSDT")
    sent = transport.calls[before:]
    assert len([r for r in sent if r.method != "GET"]) == 1
    assert all(urlsplit(r.url).hostname == "open-api-vst.bingx.com"
               for r in sent if "X-BX-APIKEY" in r.headers)
    assert all(r.url != LOCATION for r in sent)
    assert "signature" not in json.dumps(event) and FAKE_KEY not in json.dumps(event)
    assert LOCATION not in json.dumps(event)
    with pytest.raises(UnknownOrderStateError):
        operation()
    # Replay может сверять позицию через GET, но не повторяет мутацию.
    assert all(r.method == "GET" for r in transport.calls[before + len(sent):])
    assert len([r for r in transport.calls[before:] if r.method != "GET"]) == 1
    assert len(client.journal.wires(client.account_id, "redirect-mutation")) == 1
    assert_no_secrets(caplog, caught.value)
