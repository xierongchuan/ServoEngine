"""Офлайн-регрессии clock sync и снятия raw UNKNOWN по durable доказательствам."""

import copy
import hashlib
import hmac
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from src.core.execution_coordinator import attach_execution_safety
from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError
from test_bingx_safety_regressions import FakeExchange, Response, new_client


CLOCK_PATH = "/openApi/swap/v2/server/time"
LOCAL_MS = 1700000000000


def timestamp_error(code=109400, status=200):
    response = Response({}, code=code, status=status)
    response.payload["msg"] = "Timestamp for this request is outside of the recvWindow."
    return response


@pytest.fixture
def exchange(monkeypatch):
    monkeypatch.setenv("BINGX_API_KEY", "clock-recovery-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "clock-recovery-fake-secret")
    monkeypatch.setenv("SERVO_DATA_DIR", "/tmp/clock-recovery-data")
    fake = FakeExchange()
    fake.install(monkeypatch)
    return fake


def clock_transport(monkeypatch, client, responses):
    calls, budgets = [], []
    client._rate_limiter = SimpleNamespace(acquire=lambda: budgets.append(1))
    responses = iter(responses)
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.time", lambda: LOCAL_MS / 1000)
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.monotonic", lambda: 10)

    def request(url, **kwargs):
        calls.append((url, kwargs))
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.get", request)
    return calls, budgets


@pytest.mark.parametrize("code", [109400, 80014])
def test_timestamp_get_syncs_once_on_private_host_and_resigns(exchange, monkeypatch, code):
    client = new_client()
    calls, budgets = clock_transport(monkeypatch, client, [timestamp_error(code),
        Response({"serverTime": LOCAL_MS + 12000}), Response({"ok": True})])
    assert client._make_request("get", "/read")["data"] == {"ok": True}
    assert len(calls) == len(budgets) == 3
    assert calls[1] == (client.base_url + CLOCK_PATH, {"timeout": min(client._config.request_timeout, 5), "allow_redirects": False})
    assert urlsplit(calls[1][0]).hostname == "open-api-vst.bingx.com"
    assert "headers" not in calls[1][1] and not urlsplit(calls[1][0]).query
    assert parse_qs(urlsplit(calls[0][0]).query)["timestamp"] == [str(LOCAL_MS)]
    unsigned, signature = urlsplit(calls[2][0]).query.rsplit("&signature=", 1)
    assert unsigned == f"recvWindow=5000&timestamp={LOCAL_MS + 12000}"
    assert signature == hmac.new(b"clock-recovery-fake-secret", unsigned.encode(), hashlib.sha256).hexdigest()


@pytest.mark.parametrize("wall_jump", [False, True])
def test_clock_uses_roundtrip_midpoint_and_rejects_wall_jump(exchange, monkeypatch, wall_jump):
    client = new_client()
    clock = {"wall": LOCAL_MS / 1000, "mono": 10}
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.time", lambda: clock["wall"])
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.monotonic", lambda: clock["mono"])
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        if urlsplit(url).path == CLOCK_PATH:
            clock["mono"] += .4
            clock["wall"] += 5 if wall_jump else .4
            return Response({"serverTime": LOCAL_MS + 12000})
        return timestamp_error() if len(calls) == 1 else Response({})
    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.get", request)
    if wall_jump:
        with pytest.raises(ExchangeAPIError):
            client._make_request("get", "/read")
        assert len(calls) == 2 and client._time_offset_ms == 0
    else:
        client._make_request("get", "/read")
        assert client._time_offset_ms == 11800
        assert parse_qs(urlsplit(calls[-1]).query)["timestamp"] == [str(LOCAL_MS + 12200)]


def test_repeated_timestamp_rejection_does_not_loop(exchange, monkeypatch):
    client = new_client()
    calls, _ = clock_transport(monkeypatch, client, [timestamp_error(),
        Response({"serverTime": LOCAL_MS}), timestamp_error()])
    with pytest.raises(ExchangeAPIError) as caught:
        client._make_request("get", "/read")
    assert caught.value.code == 109400 and len(calls) == 3


@pytest.mark.parametrize("response", [Response(None), Response({}), Response({"serverTime": True}),
    Response({"serverTime": "NaN"}), Response({"serverTime": "1700000000000.5"}),
    Response({"serverTime": LOCAL_MS // 1000}), Response({"serverTime": LOCAL_MS}, status=503),
    requests.Timeout("сеть отключена")])
def test_failed_clock_sync_keeps_original_rejection_and_offset(exchange, monkeypatch, response):
    client = new_client()
    client._time_offset_ms = 123
    calls, _ = clock_transport(monkeypatch, client, [timestamp_error(), response])
    with pytest.raises(ExchangeAPIError) as caught:
        client._make_request("get", "/read")
    assert caught.value.code == 109400
    assert client._time_offset_ms == 123 and len(calls) == 2


@pytest.mark.parametrize("elapsed", [2.1, -1])
def test_unreliable_clock_roundtrip_is_not_used(exchange, monkeypatch, elapsed):
    client = new_client()
    calls, _ = clock_transport(monkeypatch, client, [timestamp_error(), Response({"serverTime": LOCAL_MS + 12000})])
    times = iter([10, 10 + elapsed])
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.monotonic", lambda: next(times))
    with pytest.raises(ExchangeAPIError):
        client._make_request("get", "/read")
    assert len(calls) == 2 and client._time_offset_ms == 0


@pytest.mark.parametrize("method", ["post", "delete"])
@pytest.mark.parametrize("status", [200, 400])
def test_timestamp_mutation_never_resends_even_after_successful_sync(exchange, monkeypatch, method, status):
    events, mutations = [], []
    client = new_client()
    client._submission_observer = events.append
    calls, budgets = clock_transport(monkeypatch, client, [Response({"serverTime": LOCAL_MS + 12000})])
    monkeypatch.setattr(f"src.exchanges.impl.bingx_client.requests.{method}",
        lambda *a, **k: mutations.append((a, k)) or timestamp_error(status=status))
    with pytest.raises(ExchangeAPIError) as caught:
        client._make_request(method, "/openApi/swap/v2/trade/order", {"symbol": "BTC-USDT"})
    assert caught.value.code == 109400
    assert len(mutations) == len(calls) == 1 and len(budgets) == 2
    assert [event["status"] for event in events] == ["PREPARED"]
    assert client._time_offset_ms == 12000 and client._generation == 1


@pytest.mark.parametrize("public,response", [(False, Response({})), (False, Response({}, code=109400)),
    (False, Response({}, code=80014)), (True, timestamp_error())])
def test_no_eager_sync_on_success_business_errors_or_public_get(exchange, monkeypatch, public, response):
    client = new_client()
    calls, _ = clock_transport(monkeypatch, client, [response])
    if response.payload["code"]:
        with pytest.raises(ExchangeAPIError):
            client._request("get", "/read", public=public)
    else:
        assert client._request("get", "/read", public=public)["code"] == 0
    assert len(calls) == 1 and client._time_offset_ms == 0


def wrap(tmp_path, owner="clock-owner"):
    return attach_execution_safety(new_client(), journal_path=tmp_path / "journal.sqlite",
        owner_id=owner, settings={"exchange": "bingx", "execution_mode": "demo"})


def unknown_entry(client, exchange):
    exchange.late_fill = True
    with pytest.raises(UnknownOrderStateError):
        client.place_order("BTCUSDT", "BUY", ".01", command_id="late")
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"}


def complete_entry(exchange):
    exchange.orders["1"].update(status="FILLED", executedQty="0.01")
    exchange.amount = Decimal(".01")
    exchange.late_fill = False


def test_same_client_recovery_clears_only_durable_terminal_fill(exchange, tmp_path):
    client = wrap(tmp_path)
    unknown_entry(client, exchange)
    assert client.recover(symbol="BTC-USDT") and client.is_symbol_blocked("BTCUSDT")
    complete_entry(exchange)
    assert client.recover(symbol="BTCUSDT") == {}
    assert not client.raw_client._uncertain_symbols and not client.raw_client._uncertain_requests
    assert client.journal.get(client.account_id, "late")["state"] == "FILLED"
    assert client.raw_client._owned_positions["p1"][1] == Decimal(".01")
    assert len(exchange.mutations) == 1
    assert client.place_order("BTCUSDT", "BUY", ".01", command_id="late") == "1"
    assert len(exchange.mutations) == 1
    assert client.place_order("BTCUSDT", "BUY", ".01", command_id="new") == "2"
    assert exchange.amount == Decimal(".02")


@pytest.mark.parametrize("block", ["pause", "foreign-pending", "own-pending"])
def test_terminal_fill_does_not_clear_raw_with_account_blocks_or_pending(exchange, tmp_path, block):
    client = wrap(tmp_path)
    unknown_entry(client, exchange)
    complete_entry(exchange)
    if block == "pause":
        client.journal.pause(client.account_id, "BTCUSDT", "MANUAL_PAUSE")
    else:
        owner = "foreign" if block == "foreign-pending" else client.owner_id
        intent, _ = client.journal.prepare(client.account_id, owner, "BTCUSDT", "protection",
            {"position_id": "p1", "sl": "49000"},
            command_id="pending", safety=True, allow_pending=("late",))
        client.journal.wire(client.account_id, intent["command_id"], {"status": "PREPARED", "request_params": {}})
    assert client.recover() and client.is_symbol_blocked("BTC-USDT")
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"}
    assert client.journal.get(client.account_id, "late")["state"] == "FILLED"
    assert len(exchange.mutations) == 1


@pytest.mark.parametrize("change", ["size", "side", "id", "duplicate", "foreign-side"])
def test_recovery_never_unlocks_manual_or_ambiguous_position(exchange, tmp_path, monkeypatch, change):
    client = wrap(tmp_path)
    unknown_entry(client, exchange)
    complete_entry(exchange)
    client.recover()
    params = copy.deepcopy(client.raw_client._intents[next(iter(client.raw_client._intents))]["params"])
    client.raw_client._mark_uncertain(params, order_id="1")
    if change == "size":
        exchange.amount += Decimal(".01")
    elif change == "side":
        exchange.direction = "SHORT"
    else:
        original = exchange.request
        def changed(method, url, **kwargs):
            response = original(method, url, **kwargs)
            if urlsplit(url).path.endswith("/positions"):
                rows = response.payload["data"]
                if change == "id":
                    rows[0]["positionId"] = "manual"
                else:
                    extra = dict(rows[0])
                    if change == "foreign-side":
                        extra.update(positionId="manual", positionSide="SHORT")
                    rows.append(extra)
            return response
        monkeypatch.setattr(exchange, "request", changed)
    if change == "duplicate":
        with pytest.raises(ExchangeStateUnavailableError):
            client.recover()
    else:
        client.recover()
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"}
    assert client.is_symbol_blocked("BTCUSDT")
    assert client.journal.owned(client.account_id)[0]["quantity"] == "0.01"
    assert len(exchange.mutations) == 1


def test_unjournaled_unknown_is_not_cleared_by_empty_or_historical_journal(exchange, tmp_path):
    client = wrap(tmp_path)
    client.raw_client._mark_uncertain({"symbol": "BTC-USDT", "clientOrderId": "unjournaled"})
    assert client.recover() == {"BTCUSDT": "RAW_UNCERTAINTY"}
    assert client.is_symbol_blocked("BTCUSDT")
    client.raw_client._uncertain_symbols.clear()
    client.raw_client._uncertain_requests.clear()
    client.place_order("BTCUSDT", "BUY", ".01", command_id="historical")
    client.raw_client._mark_uncertain({"symbol": "BTC-USDT", "clientOrderId": "unjournaled"})
    client.recover()
    assert client.is_symbol_blocked("BTCUSDT") and len(exchange.mutations) == 1


def test_bare_raw_flag_and_other_symbol_recovery_do_not_unlock(exchange, tmp_path):
    client = wrap(tmp_path)
    unknown_entry(client, exchange)
    complete_entry(exchange)
    client.recover(symbol="ETHUSDT")
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"}
    client.raw_client._uncertain_requests.clear()
    client.recover()
    assert client.is_symbol_blocked("BTCUSDT") and len(exchange.mutations) == 1


def test_foreign_coordinator_cannot_clear_proven_owner_uncertainty(exchange, tmp_path):
    owner = wrap(tmp_path)
    unknown_entry(owner, exchange)
    complete_entry(exchange)
    raw_evidence = copy.deepcopy(owner.raw_client._uncertain_requests)
    owner.recover()
    foreign = wrap(tmp_path, owner="foreign")
    foreign.raw_client._uncertain_symbols.add("BTC-USDT")
    foreign.raw_client._uncertain_requests = raw_evidence
    foreign.recover()
    assert foreign.is_symbol_blocked("BTCUSDT")
    assert not foreign.raw_client._owned_positions and len(exchange.mutations) == 1


def test_recovery_read_failure_retains_raw_uncertainty(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path)
    unknown_entry(client, exchange)
    complete_entry(exchange)
    client.recover()
    params = next(iter(client.raw_client._intents.values()))["params"]
    client.raw_client._mark_uncertain(params, order_id="1")
    def unavailable():
        raise ExchangeStateUnavailableError("Снимок недоступен")
    monkeypatch.setattr(client, "_positions", unavailable)
    with pytest.raises(ExchangeStateUnavailableError):
        client.recover()
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"} and len(exchange.mutations) == 1


def test_exact_conditional_ack_recovery_clears_raw_only_after_protection_readback(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path)
    client.place_order("BTCUSDT", "BUY", ".01", command_id="entry")
    original = client.raw_client.get_order
    unavailable = [True]
    def lookup(symbol, order_id=None, **kwargs):
        if unavailable[0] and order_id == "2":
            raise ExchangeStateUnavailableError("Readback защиты недоступен")
        return original(symbol, order_id, **kwargs)
    monkeypatch.setattr(client.raw_client, "get_order", lookup)
    with pytest.raises(UnknownOrderStateError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1", command_id="protect")
    client.recover()
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"}
    assert client.is_symbol_blocked("BTCUSDT")
    unavailable[0] = False
    assert client.recover() == {}
    assert not client.raw_client._uncertain_symbols
    assert client.journal.get(client.account_id, "protect")["state"] == "RESOLVED"
    assert client.journal.protections(client.account_id)[0]["order_id"] == "2"
    assert len(exchange.mutations) == 2


def test_identical_unjournaled_conditional_cannot_borrow_historical_ack(exchange, tmp_path):
    historical = wrap(tmp_path)
    historical.place_order("BTCUSDT", "BUY", ".01", command_id="entry")
    historical.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1", command_id="protect")
    params = historical.journal.protections(historical.account_id)[0]["snapshot"]["request_params"]
    raw = new_client()
    exchange.lost_response = True
    with pytest.raises(UnknownOrderStateError):
        raw._submit_order(params)
    client = attach_execution_safety(raw, journal_path=tmp_path / "journal.sqlite",
        owner_id=historical.owner_id, settings={"exchange": "bingx", "execution_mode": "demo"})
    assert client.recover() == {"BTCUSDT": "RAW_UNCERTAINTY"}
    assert client.is_symbol_blocked("BTCUSDT") and len(exchange.mutations) == 3
    assert [row["order_id"] for row in client.journal.protections(client.account_id)] == ["2"]


@pytest.mark.parametrize("race", ["pause", "position"])
def test_final_guard_does_not_unlock_on_late_change(exchange, tmp_path, monkeypatch, race):
    client = wrap(tmp_path)
    unknown_entry(client, exchange)
    complete_entry(exchange)
    client.recover()
    params = next(iter(client.raw_client._intents.values()))["params"]
    client.raw_client._mark_uncertain(params, order_id="1")
    original = client._positions
    reads = []
    def changed():
        reads.append(1)
        if len(reads) == 2:
            if race == "pause":
                client.journal.pause(client.account_id, "BTCUSDT", "LATE_PAUSE")
            else:
                exchange.amount += Decimal(".01")
        return original()
    monkeypatch.setattr(client, "_positions", changed)
    client._clear_reconciled_raw_uncertainty("BTCUSDT")
    assert len(reads) == 2 and client.is_symbol_blocked("BTCUSDT")
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"} and len(exchange.mutations) == 1


def test_zero_position_close_recovery_clears_without_mutation(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path)
    client.place_order("BTCUSDT", "BUY", ".01", command_id="entry")
    original = client.raw_client._confirm_fill
    def interrupted(symbol, oid, params):
        if params["side"] == "SELL":
            client.raw_client._unknown("Readback close недоступен", params, order_id=oid)
        return original(symbol, oid, params)
    monkeypatch.setattr(client.raw_client, "_confirm_fill", interrupted)
    with pytest.raises(UnknownOrderStateError):
        client.close_position("BTCUSDT", "p1", command_id="close")
    assert client.raw_client._uncertain_symbols == {"BTC-USDT"}
    assert client.recover() == {}
    assert client.closure_confirmed("BTCUSDT", "p1")
    assert not client.raw_client._uncertain_symbols and exchange.amount == 0
    assert len(exchange.mutations) == 2


def test_first_mutation_timestamp_rejection_is_durable_and_never_replayed(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path)
    original = exchange.request
    def rejected(method, url, **kwargs):
        if urlsplit(url).path == CLOCK_PATH:
            return Response({"serverTime": LOCAL_MS})
        if method == "post":
            exchange.calls.append((method, urlsplit(url).path, {}))
            return timestamp_error()
        return original(method, url, **kwargs)
    monkeypatch.setattr(exchange, "request", rejected)
    for _ in range(2):
        with pytest.raises(ExchangeAPIError):
            client.place_order("BTCUSDT", "BUY", ".01", command_id="timestamp")
    assert client.journal.get(client.account_id, "timestamp")["state"] == "REJECTED"
    assert not client.journal.owned(client.account_id) and exchange.amount == 0
    assert len(exchange.mutations) == 1


def test_timestamp_rejection_after_prior_ack_remains_unknown_without_resend(exchange, tmp_path, monkeypatch):
    client = wrap(tmp_path)
    original = exchange.request
    def rejected(method, url, **kwargs):
        if urlsplit(url).path == CLOCK_PATH:
            return Response({"serverTime": LOCAL_MS})
        if method == "post" and "type=STOP_MARKET" in kwargs.get("data", ""):
            exchange.calls.append((method, urlsplit(url).path, {}))
            return timestamp_error()
        return original(method, url, **kwargs)
    monkeypatch.setattr(exchange, "request", rejected)
    def submit(cid):
        client.raw_client._submit_order({"symbol": "BTC-USDT", "side": "BUY", "positionSide": "LONG",
            "type": "MARKET", "quantity": "0.01", "clientOrderId": cid})
        return client.raw_client._make_request("post", "/openApi/swap/v2/trade/order",
            {"symbol": "BTC-USDT", "side": "SELL", "positionSide": "LONG",
             "type": "STOP_MARKET", "quantity": "0.01", "stopPrice": "49000", "workingType": "MARK_PRICE"})
    with pytest.raises(UnknownOrderStateError):
        client.run_verified_operation("BTCUSDT", "clock_probe", {}, submit,
            lambda ack: {"state": "RESOLVED"}, command_id="probe")
    assert client.journal.get(client.account_id, "probe")["state"] == "UNKNOWN"
    assert len(exchange.mutations) == 2
    with pytest.raises(UnknownOrderStateError):
        client.run_verified_operation("BTCUSDT", "clock_probe", {}, submit,
            lambda ack: {"state": "RESOLVED"}, command_id="probe")
    assert len(exchange.mutations) == 2
