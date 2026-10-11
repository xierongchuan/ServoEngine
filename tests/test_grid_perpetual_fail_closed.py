"""Production-запрет новых perpetual GRID entries; legacy seed только в узком контексте."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core.execution_grid import GridExecutionMixin
from src.core.strategies.grid import safety
from src.core.strategies.grid.worker import run_grid_cycle
from src.exchanges.dto.models import OrderStatus, PositionSide
from src.exchanges.errors import UnsupportedCapabilityError
from src.trading_settings import validate_trading_settings
from test_execution_grid_regressions import setup, entry, bootstrap, reduce, owned
from test_grid_execution_safety import Client, build, position, factory_grid, mexc_factory_grid


@pytest.mark.parametrize("execution_mode", ["demo", "live"], ids=["sandbox", "production-simulated"])
def test_unverified_native_capability_cannot_be_enabled_by_settings(execution_mode):
    from src.core.strategies.grid.native_execution import NativeGridExecutionMixin
    settings = validate_trading_settings({"exchange": "bingx", "execution_mode": execution_mode})
    client = SimpleNamespace(settings=settings)
    with pytest.raises(UnsupportedCapabilityError, match="ещё не подтверждены"):
        NativeGridExecutionMixin.place_native_grid_entry(client, "BTC-USDT", "BUY", ".01",
            sl=48000, tp=52000, gross_limit=".1")
    capabilities = safety.grid_protection_capabilities(settings)
    assert not capabilities["native_market_entry_allowed"] and capabilities["native_market_parallelism"] == 0


@pytest.mark.parametrize("exchange", ["bingx", "mexc"])
@pytest.mark.parametrize("execution_mode", ["demo", "live"])
@pytest.mark.parametrize("position_mode,grid_mode", [("hedge", "hedged"), ("hedge", "inventory"), ("one_way", "inventory")])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_coordinator_guard_precedes_all_io_and_prepared(exchange, execution_mode, position_mode, grid_mode, side):
    settings = validate_trading_settings({"exchange": exchange, "execution_mode": execution_mode,
        "position_mode": position_mode, "grid_mode": grid_mode})
    # Нет raw/journal/rules: любое обращение к ним до отказа сломает тест.
    client = SimpleNamespace(settings=settings)
    with pytest.raises(UnsupportedCapabilityError, match="initial LIMIT fills не устанавливают биржевой SL/TP"):
        GridExecutionMixin.place_grid_order(client, "BTC-USDT", side, ".01", price="50000", gross_limit=".1")


@pytest.mark.parametrize("hedge", [True, False])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_real_coordinator_rejects_flat_entry_without_reads_or_journal(setup, hedge, side):
    fake, make = setup
    client = make(hedge=hedge)
    before = list(fake.calls)
    with pytest.raises(UnsupportedCapabilityError, match="Локальный emergency stop не гарантирует биржевую защиту"):
        entry(client, "blocked", side=side)
    assert fake.calls == before
    assert not fake.mutations
    assert client.journal.get(client.account_id, "blocked") is None
    assert not client.journal.protections(client.account_id, owner=client.owner_id, symbol="BTCUSDT")


@pytest.mark.parametrize("exchange,execution_mode", [("bingx", "demo"), ("bingx", "live"), ("mexc", "live")])
@pytest.mark.parametrize("mode,position_mode", [("hedged", "hedge"), ("inventory", "hedge"), ("inventory", "one_way")])
def test_executor_no_new_entries_no_raw_fallback_or_durable_unknown(build, exchange, execution_mode, mode, position_mode):
    client = Client()
    client.place_grid_order = None
    grid = build(client, exchange=exchange, execution_mode=execution_mode, mode=mode, position_mode=position_mode)
    assert grid._place_grid_order("BUY", 99, 100) is None
    if mode == "hedged":
        assert grid._place_grid_order("SELL", 101, 100) is None
    assert not grid.sync_orders({"buy": [99], "sell": [101]}, 100)
    assert not client.placed
    assert not grid.commands
    assert not grid.paused_reason
    assert grid.get_stats()["entries_blocked_reason"] == safety.PERPETUAL_ENTRY_BLOCK_REASON
    result = run_grid_cycle(grid)
    assert not result["paused"]
    assert result["entries_blocked_reason"] == safety.PERPETUAL_ENTRY_BLOCK_REASON
    assert not client.placed


@pytest.mark.parametrize("position_mode", ["hedge", "one_way"])
def test_entry_block_does_not_block_owned_inventory_reduce(build, position_mode):
    client = Client()
    client.positions = [position("own", quantity=.15), position("manual-short", PositionSide.SHORT, .2)]
    grid = build(client, mode="inventory", position_mode=position_mode)
    grid.owned_positions["own"] = {"quantity": .15, "side": "LONG", "observed_quantity": .15}
    assert not grid.sync_orders({"buy": [99], "sell": [101]}, 100)
    assert not grid.paused_reason
    assert len(client.placed) == 1
    assert client.placed[0]["reduce_only"] is True
    assert client.placed[0]["position_id"] == "own"
    assert client.placed[0]["side"].value == "SELL"
    assert all(level.reduce_only for level in grid.commands.values())
    assert grid.entries_blocked_reason


def test_entry_block_keeps_worker_local_emergency_owned_only(build):
    client = Client()
    client.positions = [position("own"), position("manual", quantity=.2)]
    client.get_ticker = lambda symbol: SimpleNamespace(bid_price=79, ask_price=81, last_price=80, mark_price=80)
    grid = build(client)
    grid.owned_positions["own"] = {"quantity": .1, "side": "LONG", "observed_quantity": .1}
    assert not grid.paused_reason
    result = run_grid_cycle(grid)
    assert result["paused"]
    assert [row[0] for row in client.closed] == ["own"]
    assert [p.position_id for p in client.positions] == ["manual"]
    assert not grid.owned_positions
    assert not client.placed
    assert grid.entries_blocked_reason


def test_legacy_pending_restart_fill_and_cancel_without_new_entries(build, monkeypatch):
    client = Client()
    grid = build(client)
    # Только подготовка состояния старой версии; далее используется production-запрет.
    with monkeypatch.context() as legacy:
        legacy.setattr(safety, "grid_entry_block_reason", lambda settings: "")
        assert grid.sync_orders({"buy": [99, 98], "sell": []}, 100)
    levels = list(grid.active_orders.values())
    grid.close()
    restarted = build(client)
    assert restarted.entries_blocked_reason
    filled, pending = levels
    client.orders[filled.order_id] = replace(client.orders[filled.order_id], status=OrderStatus.FILLED,
        filled_quantity=filled.quantity, raw_data={"clientOrderId": filled.client_order_id, "positionId": "own"})
    client.positions = [position("own", quantity=filled.quantity)]
    assert restarted.check_filled_orders()[0]["qty"] == filled.quantity
    assert not restarted.paused_reason
    assert restarted.owned_positions["own"]["quantity"] == filled.quantity
    assert restarted.cancel_managed_orders()
    assert client.canceled == [pending.order_id]
    assert not restarted.sync_orders({"buy": [97], "sell": [101]}, 100)
    assert len(client.placed) == 2
    assert restarted.entries_blocked_reason


def test_real_legacy_fill_has_no_exchange_protection_and_cannot_scale(setup, monkeypatch):
    fake, make = setup
    client = make()
    with monkeypatch.context() as legacy:
        legacy.setattr(safety, "grid_entry_block_reason", lambda settings: "")
        bootstrap(fake, client)
    assert owned(client) > 0
    assert not client.journal.protections(client.account_id, owner=client.owner_id, symbol="BTCUSDT")
    assert client.journal.owned(client.account_id, owner=client.owner_id)[0]["snapshot"]["protection"] == {}
    count = len(fake.mutations)
    with pytest.raises(UnsupportedCapabilityError, match="Новые входы perpetual GRID запрещены"):
        entry(client, "scale")
    assert len(fake.mutations) == count
    assert client.journal.get(client.account_id, "scale") is None
    oid = reduce(client, "reduce-existing")
    fake.fill(oid)
    client.reconcile_grid("BTCUSDT")
    assert owned(client) > 0


def test_protected_legacy_position_still_rejects_entry_validation(setup, monkeypatch):
    fake, make = setup
    client = make()
    with monkeypatch.context() as legacy:
        legacy.setattr(safety, "grid_entry_block_reason", lambda settings: "")
        bootstrap(fake, client)
    assert client.set_sl_tp("BTCUSDT", "LONG", sl=49000, tp=52000, position_id="pLONG", command_id="protect")
    with pytest.raises(UnsupportedCapabilityError, match="защищённую позицию"):
        client._grid_validate_entry("BTCUSDT", "LONG", client._positions())
    count = len(fake.mutations)
    with pytest.raises(UnsupportedCapabilityError, match="Новые входы perpetual GRID запрещены"):
        entry(client, "protected-scale")
    assert len(fake.mutations) == count


def test_read_only_perpetual_keeps_public_preview_without_registry(build):
    grid = build(execution_mode="read_only")
    for method in ("get_positions", "get_open_orders", "get_recent_orders", "place_order", "place_grid_order"):
        setattr(grid.client, method, lambda *args, **kwargs: pytest.fail("Private-вызов в read_only"))
    result = run_grid_cycle(grid)
    assert result["target_prices"]["buy"]
    assert not result["entries_blocked_reason"]
    assert grid._store is None
    assert not grid.commands


@pytest.mark.parametrize("factory_name", ["factory_grid", "mexc_factory_grid"])
@pytest.mark.parametrize("mode,position_mode", [("hedged", "hedge"), ("inventory", "hedge"), ("inventory", "one_way")])
def test_actual_factory_guard_survives_restart_without_any_order(request, factory_name, mode, position_mode):
    exchange, create = request.getfixturevalue(factory_name)
    grid = create(mode=mode, position_mode=position_mode)
    before = list(exchange.calls)
    with pytest.raises(UnsupportedCapabilityError, match="Новые входы perpetual GRID запрещены"):
        entry(grid.client, "blocked-factory")
    assert exchange.calls == before
    assert not grid.sync_orders({"buy": [49000], "sell": [51000]}, 50000)
    assert not grid.commands
    assert not exchange.orders
    assert not grid.client.journal.intents(grid.client.account_id)
    grid.close()
    restarted = create(mode=mode, position_mode=position_mode, reset=True)
    assert restarted.entries_blocked_reason
    assert not restarted.sync_orders({"buy": [48000], "sell": [52000]}, 50000)
    assert not restarted.commands
    assert not exchange.orders


@pytest.mark.parametrize("allow_local_protection", [True, False])
def test_spot_local_opt_in_cannot_enable_perpetual_entries(allow_local_protection):
    settings = validate_trading_settings({"execution_mode": "live",
        "spot": {"allow_local_protection": allow_local_protection}})
    with pytest.raises(UnsupportedCapabilityError, match="Локальный emergency stop не гарантирует биржевую защиту"):
        GridExecutionMixin.place_grid_order(SimpleNamespace(settings=settings), "BTC-USDT", "BUY", ".01",
                                          price="50000", gross_limit=".1")


def test_spot_is_not_disabled_by_perpetual_guard():
    settings = validate_trading_settings({"exchange": "mexc", "market_type": "spot", "execution_mode": "live",
        "grid_mode": "inventory", "protection": {"trigger_price": "last"}})
    assert safety.grid_entry_block_reason(settings) == ""
