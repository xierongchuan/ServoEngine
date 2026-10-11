"""Реальные coordinator/BingX методы, HTTP fake: очередь, резерв, fills, рестарт и ручные гонки."""

from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from src.core.execution_coordinator import attach_execution_safety
from src.core.execution_journal import ExecutionJournal
from src.exchanges.dto.models import OrderSide, OrderStatus, OrderType, PositionSide
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from test_bingx_safety_regressions import FakeExchange, Response, new_client

D = Decimal

# Эти регрессии моделируют очередь, созданную ДО запрета незащищённых входов.
pytestmark = pytest.mark.usefixtures("legacy_unprotected_grid")


@pytest.fixture
def legacy_unprotected_grid(monkeypatch):
    """Только эмуляция старой версии; production-запрет проверяется отдельно без этой fixture."""
    monkeypatch.setattr("src.core.strategies.grid.safety.grid_entry_block_reason", lambda settings: "")


class GridExchange(FakeExchange):
    def __init__(self, hedge=True):
        super().__init__(hedge)
        self.positions = {}
        self.ids = {"LONG": "pLONG", "SHORT": "pSHORT"}

    def direction_of(self, row):
        if row.get("positionSide") != "BOTH":
            return row["positionSide"]
        if str(row.get("reduceOnly")).lower() == "true":
            return "LONG" if row["side"] == "SELL" else "SHORT"
        return "LONG" if row["side"] == "BUY" else "SHORT"

    def fill(self, oid, quantity=None):
        row = self.orders[oid]
        qty = D(str(quantity)) if quantity is not None else D(row["origQty"]) - D(row["executedQty"])
        direction = self.direction_of(row)
        opening = row["side"] == ("BUY" if direction == "LONG" else "SELL")
        self.positions[direction] = self.positions.get(direction, D(0)) + (qty if opening else -qty)
        assert self.positions[direction] >= 0
        row["executedQty"] = str(D(row["executedQty"]) + qty)
        row["status"] = "FILLED" if D(row["executedQty"]) == D(row["origQty"]) else "PARTIALLY_FILLED"

    def add_order(self, params):
        row = super().add_order(params)
        if params["type"] == "MARKET" and not self.late_fill:
            row["executedQty"] = "0"
            self.fill(row["orderId"])
        return row

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        if path.endswith("/positions"):
            self.calls.append((method, path, {}))
            rows = []
            for direction, qty in self.positions.items():
                if not qty:
                    continue
                reserved = sum((D(o["origQty"]) - D(o["executedQty"]) for o in self.orders.values()
                                if o["type"] == "LIMIT" and o["status"] in {"NEW", "PARTIALLY_FILLED"}
                                and self.direction_of(o) == direction
                                and o["side"] == ("SELL" if direction == "LONG" else "BUY")), D(0))
                rows.append({"symbol": "BTC-USDT", "positionId": self.ids[direction],
                    "positionAmt": str(-qty if not self.hedge and direction == "SHORT" else qty),
                    "availableAmt": str(max(D(0), qty - reserved)),
                    "positionSide": direction if self.hedge else "BOTH", "isolated": True,
                    "avgPrice": "50000", "unrealizedProfit": "0", "leverage": self.leverage})
            return Response(rows)
        if path.endswith("/openOrders"):
            self.calls.append((method, path, {}))
            return Response({"orders": [o for o in self.orders.values() if o["status"] in {"NEW", "PARTIALLY_FILLED"}]})
        return super().request(method, url, **kwargs)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("BINGX_API_KEY", "grid-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "grid-fake-secret")
    fake = GridExchange()
    fake.install(monkeypatch)
    path = tmp_path / "execution.sqlite"

    def make(*, hedge=True, owner="grid-a"):
        fake.hedge = hedge
        settings = {"exchange": "bingx", "execution_mode": "demo",
                    "position_mode": "hedge" if hedge else "one_way",
                    "grid_mode": "hedged" if hedge else "inventory"}
        return attach_execution_safety(new_client(hedge=hedge), settings=settings,
                    owner_id=owner, account_id="grid-account", journal_path=path)

    return fake, make


def entry(client, cmd, quantity=".01", *, side="BUY", gross_limit=".1", price="50000"):
    return client.place_grid_order("BTC-USDT", side, quantity, price=price, leverage=10,
                                   command_id=cmd, gross_limit=gross_limit)


def reduce(client, cmd, quantity=".005", *, direction="LONG", **kwargs):
    return client.place_reduce_order("BTC-USDT", "SELL" if direction == "LONG" else "BUY", quantity,
        price="51000", position_side=direction if client.settings["position_mode"] == "hedge" else "BOTH",
        leverage=10, position_id="p" + direction, command_id=cmd, **kwargs)


def owned(client, direction="LONG"):
    return sum((D(r["quantity"]) for r in client.journal.owned(client.account_id, owner=client.owner_id)
                if r["side"] == direction), D(0))


def bootstrap(fake, client, quantity=".02"):
    oid = entry(client, "seed", quantity)
    fake.fill(oid)
    client.reconcile_grid("BTCUSDT")
    return oid


def test_same_owner_ack_queue_not_normal_trading_or_other_owner(setup):
    fake, make = setup
    client = make()
    one, two = entry(client, "one"), entry(client, "two", price="49900")
    assert one != two and len(fake.mutations) == 2
    assert client.is_symbol_blocked("BTCUSDT")
    assert not client.is_grid_symbol_blocked("BTCUSDT")
    assert client.journal.get(client.account_id, "two")["state"] == "ACK"
    assert owned(client) == 0
    with pytest.raises(ExchangeStateUnavailableError):
        client.place_order("BTCUSDT", "BUY", ".01", command_id="normal")
    with pytest.raises(ExchangeStateUnavailableError):
        entry(make(owner="different-grid"), "foreign-owner")
    assert len(fake.mutations) == 2


def test_gross_reserves_pending_and_does_not_free_close_ack(setup):
    fake, make = setup
    client = make()
    entry(client, "one", gross_limit=".02")
    entry(client, "two", gross_limit=".02")
    with pytest.raises(ExchangeStateUnavailableError):
        entry(client, "three", gross_limit=".02")
    assert len(fake.mutations) == 2
    fake.fill("1")
    fake.fill("2")
    client.reconcile_grid("BTCUSDT")
    reduce(client, "close", ".01")
    assert client.grid_gross_exposure("BTCUSDT") == D(".03")
    assert owned(client) == D(".02")
    with pytest.raises(ExchangeStateUnavailableError):
        entry(client, "over", gross_limit=".03")
    assert len(fake.mutations) == 3


def test_aggregate_simultaneous_fills_restart_scale_and_replay(setup):
    fake, make = setup
    client = make()
    a, b = entry(client, "a"), entry(client, "b", ".02")
    fake.fill(a)
    fake.fill(b)
    assert client.get_order("BTCUSDT", a).status == OrderStatus.FILLED
    assert owned(client) == D(".03")
    client.reconcile_grid("BTCUSDT")
    restored = make()
    restored.recover()
    assert owned(restored) == D(".03")
    assert entry(restored, "a") == a and len(fake.mutations) == 2
    c = entry(restored, "c")
    fake.fill(c)
    restored.reconcile_grid("BTCUSDT")
    assert owned(restored) == D(".04")
    assert restored.get_owned_positions("BTCUSDT")["BTCUSDT"][0].size == .04


@pytest.mark.parametrize("hedge", [True, False])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_reduce_limit_payload_reservation_aggregate_and_terminal_cancel(setup, hedge, direction):
    fake, make = setup
    client = make(hedge=hedge)
    oid = entry(client, "seed", ".02", side="BUY" if direction == "LONG" else "SELL")
    fake.fill(oid)
    client.reconcile_grid("BTCUSDT")
    a, b = reduce(client, "r1", ".01", direction=direction), reduce(client, "r2", ".01", direction=direction)
    assert owned(client, direction) == D(".02")
    assert fake.orders[a]["positionSide"] == (direction if hedge else "BOTH")
    assert ("reduceOnly" not in fake.orders[a]) if hedge else fake.orders[a]["reduceOnly"] == "true"
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client, "excess", ".001", direction=direction)
    assert len(fake.mutations) == 3
    fake.fill(a)
    fake.fill(b)
    client.reconcile_grid("BTCUSDT")
    assert owned(client, direction) == 0
    assert client.journal.get(client.account_id, "r1")["state"] == "FILLED"
    assert len(fake.mutations) == 3


def test_cancel_releases_reserve_after_readback_and_never_foreign(setup):
    fake, make = setup
    client = make()
    bootstrap(fake, client)
    a, b = reduce(client, "r1", ".01"), reduce(client, "r2", ".01")
    assert client.cancel_order("BTCUSDT", a, command_id="cancel")
    assert client.journal.get(client.account_id, "r1")["state"] == "RESOLVED"
    c = reduce(client, "r3", ".01")
    assert c != a
    with pytest.raises(UnsupportedCapabilityError):
        client.cancel_order("BTCUSDT", "manual")
    fake.fill(b)
    fake.fill(c)
    client.reconcile_grid("BTCUSDT")
    assert owned(client) == 0


def test_partial_blocks_queue_retains_owned_and_cancel_partial_not_full(setup):
    fake, make = setup
    client = make()
    a = entry(client, "a", ".02")
    fake.fill(a, ".005")
    client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".005")
    assert client.is_grid_symbol_blocked("BTCUSDT")
    with pytest.raises(ExchangeStateUnavailableError):
        entry(client, "b")
    assert client.cancel_order("BTCUSDT", a, command_id="cancel-partial")
    assert client.journal.get(client.account_id, "a")["state"] == "PARTIAL"
    assert owned(client) == D(".005")
    assert client.is_grid_symbol_blocked("BTCUSDT")


@pytest.mark.parametrize("change", ["quantity", "id", "side"])
def test_manual_change_pause_no_adoption_no_close(setup, change):
    fake, make = setup
    client = make()
    bootstrap(fake, client)
    if change == "quantity":
        fake.positions["LONG"] += D(".001")
    elif change == "id":
        fake.ids["LONG"] = "manual-replacement"
    else:
        fake.positions = {"SHORT": D(".02")}
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client, "r")
    assert len(fake.mutations) == 1 and owned(client) == D(".02")
    assert client.is_grid_symbol_blocked("BTCUSDT")


def test_unknown_lookup_restart_never_resends(setup):
    fake, make = setup
    client = make()
    a = entry(client, "a")
    fake.orders[a]["clientOrderId"] = "manual"
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert client.journal.get(client.account_id, "a")["state"] == "UNKNOWN"
    with pytest.raises(ExchangeStateUnavailableError):
        entry(make(), "b")
    assert len(fake.mutations) == 1 and owned(client) == 0


@pytest.mark.parametrize("state", ["PREPARED", "UNKNOWN", "PARTIAL"])
def test_journal_grid_queue_only_proven_same_owner_ack(tmp_path, state):
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    request = {"order_type": "LIMIT"}
    first, _ = journal.prepare("a", "o", "BTCUSDT", "grid_entry", request, command_id="a", grid_queue=True)
    journal.finish("a", first["command_id"], state, order_id="1")
    with pytest.raises(ExchangeStateUnavailableError):
        journal.prepare("a", "o", "BTCUSDT", "grid_entry", request, command_id="b", grid_queue=True)


def test_limit_readback_price_or_reduce_flag_mismatch_not_adopted(setup):
    fake, make = setup
    client = make(hedge=False)
    bootstrap(fake, client)
    oid = reduce(client, "r")
    fake.orders[oid]["reduceOnly"] = "false"
    with pytest.raises(ExchangeStateUnavailableError):
        client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".02") and client.is_grid_symbol_blocked("BTCUSDT")


def test_protection_block_never_bypassed_by_grid_queue(setup):
    fake, make = setup
    client = make()
    entry(client, "a")
    client.pause_symbol("BTCUSDT", "PROTECTION_FAILURE")
    with pytest.raises(ExchangeStateUnavailableError):
        entry(client, "b")
    assert len(fake.mutations) == 1


def test_limiter_accounts_every_leverage_checkpoint_and_runs_after_observer(setup):
    fake, make = setup
    client = make()
    raw = client._raw
    acquired = []
    class Budget:
        def acquire(self):
            acquired.append(len(fake.calls))
    raw._rate_limiter = Budget()
    raw.set_leverage("BTCUSDT", 10, PositionSide.LONG)
    assert len(acquired) == len(fake.calls)
    before = len(acquired)
    raw.set_leverage("BTCUSDT", 10, PositionSide.LONG)
    assert len(acquired) > before
    entry(client, "a", price="50000")
    assert len(acquired) == len(fake.calls)
    assert fake.orders["1"]["type"] == "LIMIT"
