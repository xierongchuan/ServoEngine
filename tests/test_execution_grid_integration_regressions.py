"""GRID executor/coordinator/raw интеграция и сбои после адресной отмены, без сети."""

from decimal import Decimal

import pytest

from src.core.grid_executor import GridExecutor
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError
from test_execution_grid_regressions import setup, entry, reduce, bootstrap, owned, legacy_unprotected_grid

D = Decimal

# Legacy-очередь нужна для регрессий сверки/отмены/owned reduce после обновления.
pytestmark = pytest.mark.usefixtures("legacy_unprotected_grid")


def test_actual_grid_multi_ack_simultaneous_fills_and_inventory_limit_close(setup, tmp_path):
    fake, make = setup
    client = make(hedge=False)
    grid = GridExecutor("BTCUSDT", {"grid_levels": 2, "order_size_usdt": 500, "inventory_limit": .1},
        {"execution": client.settings, "STRATEGY_INSTANCE_ID": client.owner_id, "LEVERAGE": 10},
        client=client, state_dir=tmp_path)
    try:
        assert grid.sync_orders({"buy": [49000, 48000], "sell": []}, 50000)
        assert len(grid.active_orders) == 2 and len(fake.mutations) == 2
        for oid in list(grid.active_orders):
            fake.fill(oid)
        fills = grid.check_filled_orders()
        assert len(fills) == 2 and not grid.paused_reason
        assert owned(client) == D(".02")
        assert grid.owned_positions["pLONG"]["quantity"] == pytest.approx(.02)
        assert grid.sync_orders({"buy": [], "sell": [51000]}, 50000)
        oid = next(iter(grid.active_orders))
        assert fake.orders[oid]["reduceOnly"] == "true"
        assert owned(client) == D(".02")
        fake.fill(oid)
        assert grid.check_filled_orders()
        assert not grid.paused_reason
        assert owned(client) == D(".011")
        assert grid.owned_positions["pLONG"]["quantity"] == pytest.approx(.011)
    finally:
        grid.close()


def test_both_hedge_directions_and_foreign_orders_reserve_gross(setup):
    fake, make = setup
    client = make()
    fake.add_order({"symbol": "BTC-USDT", "side": "SELL", "positionSide": "SHORT", "type": "LIMIT",
                    "quantity": ".03", "price": "52000", "clientOrderId": "manual-order"})
    a = entry(client, "long", gross_limit=".05")
    assert client.grid_gross_exposure("BTCUSDT") == D(".04")
    with pytest.raises(ExchangeStateUnavailableError):
        entry(client, "over", ".02", gross_limit=".05")
    assert len(fake.mutations) == 1 and fake.orders["1"]["status"] == "NEW"
    fake.orders["1"]["status"] = "CANCELLED"
    b = entry(client, "short", side="SELL")
    fake.fill(a)
    fake.fill(b)
    client.reconcile_grid("BTCUSDT")
    assert owned(client, "LONG") == owned(client, "SHORT") == D(".01")
    assert client.grid_gross_exposure("BTCUSDT") == D(".02")


def test_protected_position_resize_is_paused_not_silently_unprotected(setup):
    fake, make = setup
    client = make()
    bootstrap(fake, client)
    assert client.set_sl_tp("BTCUSDT", "LONG", sl=49000, tp=52000, position_id="pLONG", command_id="protect")
    oid = reduce(client, "r")
    fake.fill(oid)
    client.reconcile_grid("BTCUSDT")
    assert owned(client) == D(".015")
    assert client.journal.blocked(client.account_id, "BTCUSDT") == "CLOSE_PROTECTION_PENDING"
    count = len(fake.mutations)
    with pytest.raises(ExchangeStateUnavailableError):
        reduce(client, "another")
    assert len(fake.mutations) == count


def test_cancel_aggregate_readback_failure_remains_durable_unknown(setup, monkeypatch):
    fake, make = setup
    client = make()
    oid = entry(client, "a")
    original = client._raw.get_positions
    def unavailable(*args, **kwargs):
        if fake.orders[oid]["status"] == "CANCELLED":
            raise ExchangeStateUnavailableError("Позиции после отмены недоступны")
        return original(*args, **kwargs)
    monkeypatch.setattr(client._raw, "get_positions", unavailable)
    with pytest.raises(UnknownOrderStateError):
        client.cancel_order("BTCUSDT", oid, command_id="cancel")
    assert client.journal.get(client.account_id, "cancel")["state"] == "UNKNOWN"
    assert client.is_grid_symbol_blocked("BTCUSDT")
    monkeypatch.setattr(client._raw, "get_positions", original)
    restarted = make()
    restarted.recover()
    assert restarted.journal.get(client.account_id, "cancel")["state"] == "RESOLVED"
    assert restarted.journal.get(client.account_id, "a")["state"] == "RESOLVED"
    assert len(fake.mutations) == 2


def test_manual_position_race_at_prepared_http_rejects_without_adoption(setup, monkeypatch):
    fake, make = setup
    client = make()
    original = client._raw._publish_submission
    def raced(event, **kwargs):
        if event["status"] == "PREPARED" and event.get("request_params", {}).get("type") == "LIMIT":
            fake.positions["LONG"] = D(".01")
        return original(event, **kwargs)
    monkeypatch.setattr(client._raw, "_publish_submission", raced)
    with pytest.raises(ExchangeStateUnavailableError):
        entry(client, "raced")
    assert not fake.mutations and owned(client) == 0
    assert client.journal.get(client.account_id, "raced")["state"] == "REJECTED"


def test_rate_budget_failure_is_not_sent_never_order_lookup_or_post(setup, monkeypatch):
    fake, make = setup
    client = make()
    def unavailable(*args, **kwargs):
        raise ExchangeStateUnavailableError("Limiter SQLite недоступен")
    monkeypatch.setattr("src.exchanges.impl.bingx_client.get_exchange_rate_limiters", unavailable)
    with pytest.raises(ExchangeStateUnavailableError) as caught:
        client._raw._make_request("post", "/openApi/swap/v2/trade/order", {"symbol": "BTC-USDT"})
    assert caught.value.submission_status == "NOT_SENT"
    assert not fake.calls
