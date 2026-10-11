"""Native GRID MARKET: настоящие coordinator/raw методы, только fake HTTP и private WS."""

import copy
import json
from decimal import Decimal
from urllib.parse import parse_qs

import pytest

from src.core.execution_coordinator import attach_execution_safety
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from test_bingx_safety_regressions import FakeExchange, Response, new_client

D = Decimal


class NativeExchange(FakeExchange):
    def __init__(self, hedge=True):
        super().__init__(hedge)
        self.links = []
        self.no_links = False
        self.reject_entry = False
        self.stream_ready = False
        self.reference = 50000

    def add_order(self, params):
        row = super().add_order(params)
        if params.get("stopLoss") and params.get("takeProfit"):
            assert self.stream_ready, "Private stream обязан быть готов до entry POST"
            for field in ("stopLoss", "takeProfit"):
                spec = json.loads(params[field])
                protection = dict(symbol=params["symbol"], side="SELL" if params["side"] == "BUY" else "BUY",
                    positionSide=params["positionSide"], type=spec["type"], quantity=spec["quantity"],
                    stopPrice=spec["stopPrice"], workingType=spec["workingType"], reduceOnly="true")
                child = super().add_order(protection)
                self.links.append(dict(li=row["orderId"], i=child["orderId"], s=params["symbol"], S=child["side"],
                    o=child["type"], ps=child["positionSide"], q=child["origQty"], sp=child["stopPrice"], wt=child["workingType"], X="NEW", z="0"))
        return row

    def request(self, method, url, **kwargs):
        if self.reject_entry and method == "post" and url.split("?", 1)[0].endswith("/order"):
            params = {k: v[0] for k, v in parse_qs(kwargs["data"]).items()
                      if k not in {"signature", "timestamp", "recvWindow"}}
            self.calls.append((method, "/openApi/swap/v2/trade/order", params))
            return Response({}, code=109400)
        if url.split("?", 1)[0].endswith("/userDataStream"):
            from types import SimpleNamespace
            self.calls.append((method, "/openApi/user/auth/userDataStream", {}))
            return SimpleNamespace(status_code=200, headers={}, json=lambda: {"listenKey": "FAKE-LISTEN-SECRET"})
        if url.split("?", 1)[0].endswith("/premiumIndex"):
            self.calls.append((method, "/openApi/swap/v2/quote/premiumIndex", {}))
            return Response({"markPrice": str(self.reference), "lastFundingRate": "0.001"})
        if url.split("?", 1)[0].endswith("/allOrders"):
            self.calls.append((method, "/openApi/swap/v2/trade/allOrders", {}))
            return Response({"orders": list(self.orders.values())})
        return super().request(method, url, **kwargs)

    def stream(self, timeout):
        self.stream_ready = True
        fake = self
        class Stream:
            def collect(self, oid):
                return [] if fake.no_links else copy.deepcopy([e for e in fake.links if str(e["li"]) == str(oid)])
            def close(self):
                fake.stream_ready = False
        return Stream()


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGX_API_KEY", "native-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "native-fake-secret")
    # Только эмуляция lifecycle; fake HTTP/WS не разрешает capability в runtime.
    monkeypatch.setattr("src.core.strategies.grid.safety.BINGX_NATIVE_MARKET_VERIFIED", True)
    fake = NativeExchange()
    fake.install(monkeypatch)
    def make(*, policy="pause", hedge=True):
        fake.hedge = hedge
        raw = new_client(hedge=hedge)
        monkeypatch.setattr(raw, "open_grid_attachment_stream", fake.stream)
        settings = {"exchange": "bingx", "execution_mode": "demo", "position_mode": "hedge" if hedge else "one_way",
            "grid_mode": "hedged" if hedge else "inventory", "protection": {"failure_policy": policy}}
        return attach_execution_safety(raw, journal_path=tmp_path / "native.sqlite", owner_id="grid",
                                       account_id="offline-native", settings=settings)
    return fake, make


def enter(client, *, side="BUY", command="entry"):
    return client.place_native_grid_entry("BTC-USDT", side, ".012", sl=48000 if side == "BUY" else 52000,
        tp=52000 if side == "BUY" else 48000, gross_limit=".1", leverage=10, command_id=command)


def owned(client):
    return sum((D(r["quantity"]) for r in client.journal.owned(client.account_id, owner=client.owner_id)), D(0))


@pytest.mark.parametrize("hedge", [True, False])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_market_original_post_has_both_protections_and_exact_owned_ids(native, hedge, side):
    fake, make = native
    client = make(hedge=hedge)
    oid = enter(client, side=side)
    posts = [c for c in fake.mutations if c[1].endswith("/order")]
    assert len(posts) == 1 and posts[0][2]["type"] == "MARKET"
    assert "stopLoss" in posts[0][2] and "takeProfit" in posts[0][2]
    record = client.journal.get(client.account_id, "entry")
    assert record["state"] == "FILLED" and record["result"]["attachment"]["confirmed"]
    assert record["result"]["attachment"]["plan_ids"] == ["2", "3"]
    assert owned(client) == D(".012") and client.verify_protection("BTCUSDT")
    before = list(fake.mutations)
    restarted = make(hedge=hedge)
    restarted.recover()
    assert owned(restarted) == D(".012") and fake.mutations == before


def test_second_entry_and_partial_reduce_are_rejected_before_new_post(native):
    fake, make = native
    client = make()
    enter(client)
    before = list(fake.mutations)
    with pytest.raises(UnsupportedCapabilityError):
        enter(client, command="more")
    with pytest.raises(UnsupportedCapabilityError):
        client.close_position("BTCUSDT", "p1", percentage=.5)
    assert fake.mutations == before


def test_unknown_replay_never_posts_again(native):
    fake, make = native
    fake.late_fill = True
    client = make()
    with pytest.raises((ExchangeStateUnavailableError, UnknownOrderStateError)):
        enter(client)
    assert len([c for c in fake.mutations if c[1].endswith("/order")]) == 1
    fake.orders["1"].update(executedQty=".012", status="FILLED")
    fake.amount = D(".012")
    restarted = make()
    restarted.recover()
    assert restarted.verify_protection("BTCUSDT") and owned(restarted) == D(".012")
    assert len([c for c in fake.mutations if c[1].endswith("/order")]) == 1


@pytest.mark.parametrize("policy", ["pause", "close"])
def test_missing_exact_links_applies_failure_policy_without_postfill_sl(native, policy):
    fake, make = native
    fake.no_links = True
    client = make(policy=policy)
    with pytest.raises(ExchangeStateUnavailableError):
        enter(client)
    assert owned(client) == (0 if policy == "close" else D(".012"))
    assert fake.amount == (0 if policy == "close" else D(".012"))
    assert client.is_grid_symbol_blocked("BTCUSDT")
    assert all(c[2]["type"] == "MARKET" for c in fake.mutations if c[1].endswith("/order"))


def test_full_close_keeps_protection_until_fill_then_cleans_exact_ids(native):
    fake, make = native
    client = make()
    enter(client)
    assert client.close_position("BTCUSDT", "p1", command_id="full-close")
    assert owned(client) == 0
    assert {c[2]["orderId"] for c in fake.mutations if c[0] == "delete"} == {"2", "3"}
    assert not client.is_grid_symbol_blocked("BTCUSDT")
    assert enter(client, command="next") == "5"


@pytest.mark.parametrize("change", [{"stopPrice": "47000"}, {"origQty": ".011"}, {"positionSide": "SHORT"}, {"workingType": "CONTRACT_PRICE"}, {"status": "CANCELLED"}])
def test_manual_protection_drift_pauses_without_resize_or_adoption(native, change):
    fake, make = native
    client = make()
    enter(client)
    fake.orders["2"].update(change)
    before = list(fake.mutations)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".012") and fake.mutations == before
    assert client.is_grid_symbol_blocked("BTCUSDT")


def make_grid(client, tmp_path, *, mode=None):
    from src.core.grid_executor import GridExecutor
    settings = copy.deepcopy(client.settings)
    if mode:
        settings["grid_mode"] = mode
    return GridExecutor("BTC-USDT", {"entry_execution": "native_market", "order_size_usdt": 600,
        "inventory_limit": .1, "grid_levels": 3}, {"execution": settings, "STRATEGY_INSTANCE_ID": "grid",
        "EXECUTION_ACCOUNT_ID": client.account_id, "LEVERAGE": 10}, client=client, state_dir=tmp_path / "grid-state")


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_anchored_touch_one_market_and_no_local_stop(native, tmp_path, side, monkeypatch):
    fake, make = native
    client = make()
    grid = make_grid(client, tmp_path)
    try:
        assert grid.native_cycle(50000, 50000)
        targets = copy.deepcopy(grid.state.native_levels)
        assert not fake.mutations
        near = 49950 if side == "BUY" else 50050
        assert grid.native_cycle(near, near)
        assert grid.state.native_levels == targets
        assert not fake.mutations
        touch = 49800 if side == "BUY" else 50200
        fake.reference = touch
        assert grid.native_cycle(touch, touch)
        assert len([c for c in fake.mutations if c[1].endswith("/order")]) == 1
        assert fake.orders["1"]["side"] == side
        assert grid.owned_positions and not grid.state.native_levels
        before = list(fake.mutations)
        assert grid.native_cycle(47000, 47000)
        assert fake.mutations == before
        fake.orders["2"]["stopPrice"] = "44000"
        grid.reconcile()
        assert grid.paused_reason and fake.mutations == before
    finally:
        grid.close()


def test_anchored_levels_survive_restart_and_inventory_never_shorts(native, tmp_path):
    fake, make = native
    client = make(hedge=False)
    grid = make_grid(client, tmp_path)
    assert grid.native_cycle(50000, 50000)
    saved = copy.deepcopy(grid.state.native_levels)
    grid.close()
    restored = make_grid(make(hedge=False), tmp_path)
    try:
        assert restored.state.native_levels == saved and saved["sell"] == []
        assert restored.native_cycle(50200, 50200)
        assert not fake.mutations
        fake.reference = 49800
        assert restored.native_cycle(49800, 49800)
        assert fake.orders["1"]["side"] == "BUY"
        assert restored.native_cycle(50200, 50200)
        assert len([c for c in fake.mutations if c[1].endswith("/order")]) == 1
    finally:
        restored.close()


def test_native_worker_does_not_run_legacy_local_emergency(native, tmp_path, monkeypatch):
    from src.core.strategies.grid.worker import run_grid_cycle
    fake, make = native
    client = make()
    grid = make_grid(client, tmp_path)
    price = [50000]
    monkeypatch.setattr(client, "get_ticker", lambda _: {"symbol": "BTC-USDT", "last_price": price[0], "mark_price": price[0]})
    monkeypatch.setattr(grid, "check_emergency_conditions", lambda *_: pytest.fail("Local stop запрещён для native модели"))
    try:
        run_grid_cycle(grid)
        price[0] = 49800
        run_grid_cycle(grid, should_pause=True)
        assert not fake.mutations
        price[0] = 50000
        run_grid_cycle(grid)
        price[0] = 49800
        fake.reference = 49800
        assert not run_grid_cycle(grid)["paused"]
        price[0] = 40000
        run_grid_cycle(grid)
        assert len([c for c in fake.mutations if c[1].endswith("/order")]) == 1
    finally:
        grid.close()


def trigger(fake, plan_id="2"):
    plan = fake.orders[plan_id]
    plan.update(status="FILLED", executedQty=plan["origQty"])
    child = fake.add_order(dict(symbol="BTC-USDT", side=plan["side"], positionSide=plan["positionSide"],
        type="MARKET", quantity=plan["origQty"], reduceOnly="true", triggerOrderId=plan_id))
    fake.amount = D(0)
    return child


def test_native_trigger_full_fill_exact_child_cleanup_rearms_grid(native, tmp_path):
    fake, make = native
    client = make()
    grid = make_grid(client, tmp_path)
    try:
        grid.native_cycle(50000, 50000)
        fake.reference = 49800
        assert grid.native_cycle(49800, 49800)
        trigger(fake)
        assert grid.native_cycle(49000, 49000)
        assert owned(client) == 0 and grid.owned_positions == {}
        assert {c[2]["orderId"] for c in fake.mutations if c[0] == "delete"} == {"3"}
        assert grid.state.native_levels
        fake.reference = 48800
        assert grid.native_cycle(48800, 48800)
        assert len([c for c in fake.mutations if c[0] == "post" and c[1].endswith("/order")]) == 2
    finally:
        grid.close()


@pytest.mark.parametrize("drift", ["position", "child", "parent"])
def test_trigger_or_manual_flat_without_exact_proof_does_not_cleanup(native, drift):
    fake, make = native
    client = make()
    enter(client)
    if drift == "position":
        fake.amount = D(0)
    else:
        child = trigger(fake)
        child.update(executedQty=".011" if drift == "child" else ".012", triggerOrderId="99" if drift == "parent" else "2")
    before = list(fake.mutations)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert fake.mutations == before and owned(client) == D(".012")


def test_lost_entry_ack_exact_lookup_not_second_post(native):
    fake, make = native
    fake.lost_response = True
    client = make()
    assert enter(client) == "1"
    before = list(fake.mutations)
    make().recover()
    assert fake.mutations == before


def test_crash_after_raw_close_recovers_parent_atomically(native, monkeypatch):
    fake, make = native
    client = make()
    enter(client)

    monkeypatch.setattr(client, "_settle_close", lambda *_: (_ for _ in ()).throw(ExchangeStateUnavailableError("Сбой до commit")))
    with pytest.raises(UnknownOrderStateError):
        client.close_position("BTCUSDT", "p1", command_id="crash-close")
    assert fake.amount == 0 and owned(client) == D(".012")
    before = list(fake.mutations)
    restarted = make()
    restarted.recover()
    assert fake.mutations == before and owned(restarted) == 0
    assert restarted.journal.get(restarted.account_id, "entry")["result"]["attachment"]["state"] == "CLOSED"


def test_unknown_cleanup_recovery_get_only_and_never_repeat_delete(native, monkeypatch):
    fake, make = native
    client = make()
    enter(client)
    trigger(fake)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    cancel = client._raw.cancel_order
    def lost(symbol, oid):
        cancel(symbol, oid)
        raise UnknownOrderStateError("Потерян ответ DELETE")
    monkeypatch.setattr(client._raw, "cancel_order", lost)
    with pytest.raises(UnknownOrderStateError):
        client.cleanup_native_grid("BTCUSDT", command_id="cleanup")
    before = list(fake.mutations)
    restarted = make()
    restarted.recover()
    assert fake.mutations == before
    assert restarted.journal.get(restarted.account_id, "cleanup")["state"] == "RESOLVED"
    assert not restarted.is_grid_symbol_blocked("BTCUSDT")


@pytest.mark.parametrize("change", [{"li": "99"}, {"q": ".02"}, {"i": "3"}, {"S": "BUY"}, {"wt": "CONTRACT_PRICE"}])
def test_stream_links_cannot_adopt_similar_manual_orders(native, monkeypatch, change):
    fake, make = native
    client = make()
    base = fake.stream
    def bad_stream(timeout):
        stream = base(timeout)
        collect = stream.collect
        def bad(oid):
            links = collect(oid)
            links[0].update(change)
            return links
        stream.collect = bad
        return stream
    monkeypatch.setattr(client._raw, "open_grid_attachment_stream", bad_stream)
    with pytest.raises(ExchangeStateUnavailableError):
        enter(client)
    assert owned(client) == D(".012") and client.is_grid_symbol_blocked("BTCUSDT")


def test_private_stream_failure_pre_post_no_trade_and_generated_id_failure_policy(native, monkeypatch):
    fake, make = native
    client = make()
    monkeypatch.setattr(client._raw, "open_grid_attachment_stream", lambda _: (_ for _ in ()).throw(ExchangeStateUnavailableError("WS недоступен")))
    with pytest.raises(ExchangeStateUnavailableError):
        client.place_native_grid_entry("BTCUSDT", "BUY", ".012", sl=48000, tp=52000, gross_limit=".1")
    assert not fake.mutations and owned(client) == 0
    assert client.journal.intents(client.account_id)[0]["state"] == "REJECTED"
    fake.no_links = True
    monkeypatch.setattr(client._raw, "open_grid_attachment_stream", fake.stream)
    client.settings["protection"]["failure_policy"] = "close"
    with pytest.raises(ExchangeStateUnavailableError):
        client.place_native_grid_entry("BTCUSDT", "BUY", ".012", sl=48000, tp=52000, gross_limit=".1")
    assert fake.amount == 0 and owned(client) == 0


def test_rejected_entry_after_listen_ack_is_durable_rejected_not_unknown(native, monkeypatch):
    from src.exchanges.errors import ExchangeAPIError
    fake, make = native
    client = make()
    def stream(timeout):
        client._raw.create_grid_listen_key()
        return fake.stream(timeout)
    monkeypatch.setattr(client._raw, "open_grid_attachment_stream", stream)
    fake.reject_entry = True
    with pytest.raises(ExchangeAPIError):
        enter(client)
    record = client.journal.get(client.account_id, "entry")
    assert record["state"] == "REJECTED" and record["result"]["entry_rejected"]
    assert owned(client) == 0 and not client.is_grid_symbol_blocked("BTCUSDT")
    wires = client.journal.wires(client.account_id, "entry")
    assert any(w["event"].get("listen_schema") for w in wires)
    rejected = [w for w in wires if w["event"].get("status") == "REJECTED"]
    assert len(rejected) == 1 and rejected[0]["event"]["api_code"]
    before = list(fake.mutations)
    with pytest.raises(ExchangeAPIError):
        enter(client)
    assert fake.mutations == before


def test_crash_after_durable_entry_rejection_recovers_without_second_post(native, monkeypatch):
    fake, make = native
    client = make()
    fake.reject_entry = True
    finish = client.journal.finish
    def crash(account, command, state, **kwargs):
        if state == "REJECTED":
            raise RuntimeError("Сбой после durable отказа wire")
        return finish(account, command, state, **kwargs)
    monkeypatch.setattr(client.journal, "finish", crash)
    with pytest.raises(RuntimeError):
        enter(client)
    assert client.journal.get(client.account_id, "entry")["state"] == "PREPARED"
    before = list(fake.mutations)
    restarted = make()
    restarted.recover()
    assert restarted.journal.get(client.account_id, "entry")["state"] == "REJECTED"
    assert fake.mutations == before and owned(restarted) == 0


def test_gross_cap_and_foreign_flat_order_block_entry_before_post(native):
    fake, make = native
    client = make()
    with pytest.raises(ExchangeStateUnavailableError):
        client.place_native_grid_entry("BTCUSDT", "BUY", ".012", sl=48000, tp=52000, gross_limit=".01")
    assert not fake.mutations
    fake.add_order(dict(symbol="BTC-USDT", side="SELL", positionSide="SHORT", type="LIMIT", quantity=".001", price="52000"))
    with pytest.raises(UnsupportedCapabilityError):
        enter(client)
    assert not fake.mutations


def test_new_manual_pending_order_invalidates_native_coverage_without_adoption(native):
    fake, make = native
    client = make()
    enter(client)
    foreign = fake.add_order(dict(symbol="BTC-USDT", side="BUY", positionSide="LONG", type="LIMIT", quantity=".001", price="47000"))
    before = list(fake.mutations)
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert foreign["orderId"] not in client._raw._owned_orders
    assert fake.mutations == before and owned(client) == D(".012")


@pytest.mark.parametrize("manual_size", [False, True])
def test_worker_close_policy_only_closes_exact_own_size(native, tmp_path, manual_size):
    fake, make = native
    client = make(policy="close")
    grid = make_grid(client, tmp_path)
    try:
        grid.native_cycle(50000, 50000)
        fake.reference = 49800
        assert grid.native_cycle(49800, 49800)
        fake.orders["2"]["status"] = "CANCELLED"
        if manual_size:
            fake.amount += D(".001")
        before = list(fake.mutations)
        grid.reconcile()
        assert grid.paused_reason
        if manual_size:
            assert fake.mutations == before and owned(client) == D(".012")
        else:
            assert fake.amount == 0 and owned(client) == 0
            assert len([c for c in fake.mutations if c[0] == "post" and c[1].endswith("/order")]) == 2
    finally:
        grid.close()


def test_prepared_without_wire_and_unknown_cleanup_still_block_no_blind_post(native):
    fake, make = native
    client = make()
    # PREPARED сам по себе не является разрешением отправить entry после рестарта.
    request = {"side": "BUY", "owned_side": "LONG", "quantity": ".012", "price": None,
        "position_side": "LONG", "order_type": "MARKET", "sl": "48000", "tp": "52000", "leverage": 10,
        "gross_limit": ".1", "attached_protection": "exchange", "execution_path": "native_market"}
    client.journal.prepare(client.account_id, client.owner_id, "BTCUSDT", "grid_entry", request, command_id="prepared")
    restarted = make()
    restarted.recover()
    assert restarted.journal.get(client.account_id, "prepared")["state"] == "REJECTED"
    assert not fake.mutations
    enter(restarted)
    trigger(fake)
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.reconcile_grid("BTCUSDT")
    receipt = restarted.journal.get(client.account_id, "entry")["result"]["attachment"]
    cleanup, _ = restarted.journal.prepare(client.account_id, client.owner_id, "BTCUSDT", "grid_native_cleanup",
        {"order_ids": receipt["plan_ids"], "entry_command_id": "entry"}, command_id="lost-cleanup", safety=True)
    restarted.journal.finish(client.account_id, cleanup["command_id"], "UNKNOWN")
    before = list(fake.mutations)
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.cleanup_native_grid("BTCUSDT", command_id="lost-cleanup")
    with pytest.raises(ExchangeStateUnavailableError):
        enter(restarted, command="blocked")
    assert fake.mutations == before


@pytest.mark.parametrize("explicit", [None, "gtc_limit"])
def test_worker_selects_native_default_without_changing_config(native, monkeypatch, explicit):
    from types import SimpleNamespace
    from src.core.strategies.grid import worker
    fake, make = native
    client = make()
    captured = []
    config = {"grid_levels": 3}
    if explicit:
        config["entry_execution"] = explicit
    original = copy.deepcopy(config)
    def executor(symbol, config, runtime, **kwargs):
        captured.append(copy.deepcopy(config))
        return SimpleNamespace(symbol=symbol, close=lambda: None)
    monkeypatch.setattr(worker, "GridExecutor", executor)
    monkeypatch.setattr(worker, "get_grid_client", lambda *a: client)
    monkeypatch.setattr(worker, "_analyze_volatility", lambda *a: (1, False, {"adx": 0}))
    monkeypatch.setattr(worker, "run_grid_cycle", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(worker, "_graceful_shutdown", lambda *_: None)
    monkeypatch.setattr("src.utils.logger.setup_symbol_logger", lambda *_: None)
    worker.run_grid_worker("BTC-USDT", config, {"execution": client.settings, "STRATEGY_INSTANCE_ID": "grid"})
    assert captured[0]["entry_execution"] == (explicit or "native_market")
    assert config == original and not fake.mutations


def test_listen_ack_does_not_resolve_real_unknown_trade(native, monkeypatch):
    fake, make = native
    client = make()
    def authenticated(timeout):
        client._raw.create_grid_listen_key()
        return fake.stream(timeout)
    monkeypatch.setattr(client._raw, "open_grid_attachment_stream", authenticated)
    fake.late_fill = True
    with pytest.raises((UnknownOrderStateError, ExchangeStateUnavailableError)):
        enter(client)
    assert client.journal.get(client.account_id, "entry")["state"] in {"UNKNOWN", "PARTIAL"}
    assert len([m for m in fake.mutations if m[1].endswith("/order")]) == 1
    wires = client.journal.wires(client.account_id, "entry")
    assert all("FAKE-LISTEN-SECRET" not in json.dumps(w) for w in wires)


def test_unknown_listen_without_trade_wire_rejects_only_unsent_entry(native, monkeypatch):
    from types import SimpleNamespace
    fake, make = native
    client = make()
    calls = []
    def bad(url, **kwargs):
        calls.append(url)
        assert url.endswith("/openApi/user/auth/userDataStream")
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {})
    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.post", bad)
    monkeypatch.setattr(client._raw, "open_grid_attachment_stream", lambda _: client._raw.create_grid_listen_key())
    with pytest.raises(UnknownOrderStateError):
        enter(client)
    record = client.journal.get(client.account_id, "entry")
    assert record["state"] == "REJECTED" and record["result"]["not_sent"]
    assert record["result"]["auxiliary_unknown"] and not client.is_grid_symbol_blocked("BTCUSDT")
    assert len(calls) == 1 and fake.amount == 0


def test_listen_ack_before_known_leverage_refusal_is_not_unknown_entry(native, monkeypatch):
    from src.exchanges.errors import ExchangeAPIError
    fake, make = native
    client = make()
    def authenticated(timeout):
        client._raw.create_grid_listen_key()
        return fake.stream(timeout)
    monkeypatch.setattr(client._raw, "open_grid_attachment_stream", authenticated)
    fake.leverage, fake.leverage_error = 7, 110424
    with pytest.raises(ExchangeAPIError):
        enter(client)
    record = client.journal.get(client.account_id, "entry")
    assert record["state"] == "REJECTED" and record["result"]["not_sent"]
    assert not any(m[1].endswith("/order") for m in fake.mutations)
    assert fake.amount == 0 and not client.is_grid_symbol_blocked("BTCUSDT")


def test_native_executor_has_no_raw_fallback(native, tmp_path):
    from types import SimpleNamespace
    fake, make = native
    coordinator = make()
    raw = SimpleNamespace(settings=coordinator.settings, account_id=coordinator.account_id, owner_id="grid",
        place_order=lambda *a, **k: pytest.fail("Raw entry запрещён"))
    grid = make_grid(raw, tmp_path)
    try:
        assert not grid.native_cycle(50000, 50000)
        assert grid.paused_reason and not fake.mutations
    finally:
        grid.close()
