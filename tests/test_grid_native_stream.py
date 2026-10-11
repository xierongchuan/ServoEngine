"""Private WS доказательства: bounded frames, exact parent, фиксированные hosts, без сети."""

import gzip
import json
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from src.core.strategies.grid.native_stream import BingXAttachmentStream, BingXListenKey
from src.exchanges.errors import ExchangeStateUnavailableError


def stream_with_frames(frames):
    stream = BingXAttachmentStream.__new__(BingXAttachmentStream)
    stream.timeout = .01
    stream.ready = threading.Event()
    stream.stopped = threading.Event()
    stream.condition = threading.Condition()
    stream.events = deque(maxlen=256)
    stream.failed = False
    stream.thread = None
    frames = iter(frames)
    sent = []
    def recv():
        try:
            return next(frames)
        except StopIteration:
            stream.stopped.set()
            return ""
    stream.socket = SimpleNamespace(recv=recv, send=sent.append, close=lambda: None)
    return stream, sent


def frame(kind, parent="1", oid="2"):
    return json.dumps({"e": "TRADE_UPDATE", "o": {"li": parent, "i": oid, "o": kind}})


def test_gzip_ping_and_only_exact_parent_links():
    stream, sent = stream_with_frames(["Ping", frame("STOP_MARKET", parent="99"),
        gzip.compress(frame("STOP_MARKET").encode()), frame("TAKE_PROFIT_MARKET", oid="3")])
    stream._receive()
    assert sent == ["Pong"] and stream.ready.is_set() and not stream.failed
    links = stream.collect("1")
    assert {e["i"] for e in links} == {"2", "3"} and len(links) == 2


@pytest.mark.parametrize("invalid", ["broken-json", "[]", '{"code": 1}', b"\x1f\x8bBAD", "x" * 1_000_001,
                                     gzip.compress(b"x" * 1_000_001), ""])
def test_lost_or_malformed_stream_invalidates_even_previously_received_links(invalid):
    stream, _ = stream_with_frames([frame("STOP_MARKET"), frame("TAKE_PROFIT_MARKET", oid="3"), invalid])
    stream._receive()
    assert stream.failed and stream.collect("1") == []


def test_overflow_does_not_silently_drop_parent_evidence():
    stream, _ = stream_with_frames([frame("STOP_MARKET", oid=str(n)) for n in range(257)])
    stream._receive()
    assert stream.failed and len(stream.events) == 256 and not stream.collect("1")


@pytest.mark.parametrize("mode,host", [("demo", "vst-open-api-ws.bingx.com"), ("live", "open-api-swap.bingx.com")])
@pytest.mark.parametrize("nested", [False, True])
def test_hardcoded_ws_host_no_redirect_and_no_startup_ack_dependency(monkeypatch, mode, host, nested):
    connections = []
    token = "secret-token/?"
    payload = {"code": 0, "data": {"listenKey": token}} if nested else {"listenKey": token}
    client = SimpleNamespace(_allow_mutation=lambda: None, _settings=lambda: {"execution_mode": mode},
        create_grid_listen_key=lambda: BingXListenKey.from_response(payload))
    socket = SimpleNamespace(settimeout=lambda _: None, close=lambda: None)
    monkeypatch.setattr("src.core.strategies.grid.native_stream.websocket.create_connection",
                        lambda url, **kw: connections.append((url, kw)) or socket)
    monkeypatch.setattr(BingXAttachmentStream, "_receive", lambda self: self.ready.set())
    stream = BingXAttachmentStream(client, 100)
    try:
        assert stream.ready.is_set() and stream.timeout == 30
        assert connections == [(f"wss://{host}/swap-market?listenKey=secret-token%2F%3F", {"timeout": 30, "redirect_limit": 0})]
    finally:
        stream.close()


def test_connection_failure_does_not_expose_listenkey(monkeypatch):
    token = "SECRET-WS-TOKEN"
    client = SimpleNamespace(_allow_mutation=lambda: None, _settings=lambda: {"execution_mode": "demo"},
        create_grid_listen_key=lambda: BingXListenKey.from_response({"listenKey": token}))
    def fail(*a, **k):
        raise RuntimeError(token)
    monkeypatch.setattr("src.core.strategies.grid.native_stream.websocket.create_connection", fail)
    with pytest.raises(ExchangeStateUnavailableError) as caught:
        BingXAttachmentStream(client, 1)
    assert token not in str(caught.value) and caught.value.__suppress_context__


@pytest.mark.parametrize("payload,schema", [({"listenKey": "fake-secret"}, "top_level"),
    ({"code": 0, "listenKey": "fake-secret"}, "top_level"),
    ({"code": 0, "data": {"listenKey": "fake-secret"}}, "data")])
def test_typed_listen_response_keeps_secret_out_of_repr(payload, schema):
    receipt = BingXListenKey.from_response(payload)
    assert receipt.token == "fake-secret" and receipt.schema == schema
    assert "fake-secret" not in repr(receipt)


@pytest.mark.parametrize("payload", [None, [], {}, {"listenKey": None}, {"listenKey": 12}, {"listenKey": ""},
    {"listenKey": "bad\nkey"}, {"code": True, "listenKey": "SECRET"}, {"code": 1, "listenKey": "SECRET"},
    {"data": {"listenKey": "SECRET"}}, {"listenKey": "SECRET", "data": {}},
    {"code": 0, "listenKey": "SECRET", "data": {"listenKey": "another"}}])
def test_unknown_or_ambiguous_listen_schema_does_not_infer_token(payload):
    with pytest.raises(ExchangeStateUnavailableError) as caught:
        BingXListenKey.from_response(payload)
    assert "SECRET" not in str(caught.value)


def test_actual_top_level_vst_response_is_endpoint_specific_and_ack_is_sanitized(monkeypatch):
    from test_bingx_safety_regressions import FakeExchange, new_client
    fake = FakeExchange()
    fake.install(monkeypatch)
    monkeypatch.setenv("BINGX_API_KEY", "fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "fake-secret")
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {"listenKey": "SECRET-LISTEN"})
    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.post", post)
    client = new_client()
    receipt = client.create_grid_listen_key()
    assert receipt.schema == "top_level" and receipt.token == "SECRET-LISTEN"
    assert client.last_submission["status"] == "ACKNOWLEDGED"
    assert "SECRET-LISTEN" not in json.dumps(client.last_submission)
    assert len(calls) == 1 and calls[0][1]["allow_redirects"] is False
    # Торговый endpoint с таким body по-прежнему UNKNOWN, не успешный ACK.
    from src.exchanges.errors import UnknownOrderStateError
    with pytest.raises(UnknownOrderStateError):
        client._make_request("post", "/openApi/swap/v2/trade/order", {"symbol": "BTC-USDT"})
