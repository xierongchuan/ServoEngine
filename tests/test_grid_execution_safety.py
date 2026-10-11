"""Offline-регрессии GRID: ручные сделки, durable recovery и режимы исполнения."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.grid_executor import GridExecutor
from src.exchanges.dto.models import Order, OrderSide, OrderStatus, OrderType, Position, PositionSide
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError
from src.trading_settings import validate_trading_settings
from test_execution_grid_regressions import legacy_unprotected_grid

# Эмуляция входов старой версии, не доказательство допустимости новых perpetual entries.
pytestmark = pytest.mark.usefixtures("legacy_unprotected_grid")


def position(pid, side=PositionSide.LONG, quantity=0.1):
    return Position("BTCUSDT", side, quantity, 100, 0, leverage=7, position_id=pid)


def order(oid, cid="manual", side=OrderSide.BUY, price=100, quantity=0.1, status=OrderStatus.NEW,
          filled=0, position_side=PositionSide.LONG, pid=None):
    raw = {"clientOrderId": cid}
    if pid:
        raw["positionId"] = pid
    return Order(str(oid), "BTCUSDT", side, OrderType.LIMIT, status, price, quantity,
                 filled_quantity=filled, average_price=price, position_side=position_side, raw_data=raw)


class Client:
    execution_account_id = "offline-account"

    def __init__(self):
        self.orders = {}
        self.positions = []
        self.placed = []
        self.canceled = []
        self.closed = []
        self.restored = []
        self.registered = []
        self.fail_place = False
        self.lookup_calls = 0

    def get_ticker(self, symbol):
        return SimpleNamespace(bid_price=99, ask_price=101, last_price=100, mark_price=100)

    def get_open_orders(self, symbol):
        return [o for o in self.orders.values() if o.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}]

    def get_recent_orders(self, symbol, limit=100):
        return list(self.orders.values())

    def get_order(self, symbol, order_id=None, *, client_order_id=None):
        self.lookup_calls += 1
        result = self.orders.get(order_id) if order_id else next((o for o in self.orders.values() if o.raw_data["clientOrderId"] == client_order_id), None)
        if result is None:
            raise ExchangeStateUnavailableError("Нет подтверждённого состояния")
        return result

    def get_positions(self):
        return {"BTC-USDT": self.positions}

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        if self.fail_place:
            raise UnknownOrderStateError("ACK потерян")
        oid = str(len(self.orders) + 1)
        self.orders[oid] = order(oid, kwargs["client_order_id"], kwargs["side"], kwargs["price"],
                                 kwargs["quantity"], position_side=kwargs["position_side"])
        return oid

    def place_grid_order(self, *, gross_limit, **kwargs):
        return self.place_order(**kwargs)

    def place_reduce_order(self, **kwargs):
        assert kwargs["reduce_only"] is True
        assert kwargs["position_id"]
        return self.place_order(**kwargs)

    def register_owned_order(self, symbol, order_id, *, client_order_id):
        assert self.orders[order_id].raw_data["clientOrderId"] == client_order_id
        self.restored.append(order_id)

    def register_owned_position(self, symbol, position_id, quantity, *, position_side):
        self.registered.append((position_id, quantity, position_side))

    def cancel_order(self, symbol, order_id):
        self.canceled.append(order_id)
        self.orders[order_id] = replace(self.orders[order_id], status=OrderStatus.CANCELED)
        return True

    def close_position(self, symbol, pid, percentage=1.0, *, client_order_id=None):
        self.closed.append((pid, percentage, client_order_id))
        qty = next(q for p, q, side in reversed(self.registered) if p == pid) if callable(self.register_owned_position) else next(p.size for p in self.positions if p.position_id == pid) * percentage
        self.positions = [replace(p, size=p.size - qty) if p.position_id == pid else p
                          for p in self.positions if p.position_id != pid or p.size > qty]
        return True


@pytest.fixture
def build(tmp_path):
    instances = []

    def factory(client=None, *, mode="hedged", position_mode="hedge", execution_mode="live", account="offline-account", instance="btc_grid", exchange="bingx", market="perpetual", **config):
        settings = validate_trading_settings({"exchange": exchange, "market_type": market,
            "execution_mode": execution_mode, "grid_mode": mode, "position_mode": position_mode,
            "protection": {"trigger_price": "last" if market == "spot" else "mark", "recovery_attempts": 2}})
        runtime = {"execution": settings, "STRATEGY_INSTANCE_ID": instance,
                   "EXECUTION_ACCOUNT_ID": account, "LEVERAGE": 7}
        grid = GridExecutor("BTC-USDT", {"grid_levels": 2, "inventory_limit": 1, **config}, runtime,
                            client=client or Client(), state_dir=tmp_path)
        instances.append(grid)
        return grid

    yield factory
    for grid in instances:
        grid.close()


def test_all_imports_use_same_executor():
    from src.core.strategies.grid.executor import GridExecutor as other
    from src.core.grid_worker import run_grid_worker
    from src.core.strategies.grid.worker import run_grid_worker as other_worker
    assert other is GridExecutor
    assert run_grid_worker is other_worker


def test_sync_never_cancels_or_adopts_manual_orders(build):
    client = Client()
    client.orders["manual-1"] = order("manual-1", price=80, position_side=PositionSide.SHORT)
    client.orders["manual-2"] = order("manual-2", price=99, position_side=PositionSide.SHORT)
    grid = build(client)
    assert grid.sync_orders({"buy": [99], "sell": [101]}, 100)
    assert client.canceled == []
    assert "manual-1" not in grid.known_order_ids
    assert "manual-2" not in grid.known_order_ids
    assert any(p["side"] == OrderSide.BUY and p["price"] == 99 for p in client.placed)


def test_stale_cancel_only_managed_and_confirmed(build):
    grid = build()
    assert grid.sync_orders({"buy": [99], "sell": []}, 100)
    own_id = next(iter(grid.active_orders))
    grid.client.orders["manual"] = order("manual", price=80, position_side=PositionSide.SHORT)
    assert grid.sync_orders({"buy": [98], "sell": []}, 100)
    assert grid.client.canceled == [own_id]
    assert "manual" in grid.client.orders
    assert own_id not in grid.active_orders


def test_cancel_false_after_fill_race_requires_exact_terminal_readback(build):
    grid = build()
    assert grid.sync_orders({"buy": [100], "sell": []}, 100)
    oid, level = next(iter(grid.active_orders.items()))

    def cancel(symbol, order_id):
        assert order_id == oid
        grid.client.orders[oid] = replace(grid.client.orders[oid], status=OrderStatus.FILLED,
            filled_quantity=level.quantity, raw_data={"clientOrderId": level.client_order_id, "positionId": "own"})
        grid.client.positions = [position("own", quantity=level.quantity)]
        return False

    grid.client.cancel_order = Mock(side_effect=cancel)
    assert grid.cancel_managed_orders()
    assert level.status == "FILLED"
    assert grid.owned_positions["own"]["quantity"] == level.quantity
    assert not grid.paused_reason


def test_cancel_unknown_recovers_by_exact_id_without_second_cancel(build):
    grid = build()
    assert grid.sync_orders({"buy": [100], "sell": []}, 100)
    oid, level = next(iter(grid.active_orders.items()))
    cancel = Mock(side_effect=UnknownOrderStateError("ACK отмены потерян"))
    grid.client.cancel_order = cancel
    assert not grid.cancel_managed_orders()
    assert level.status == "CANCEL_UNKNOWN"
    assert grid._pause_kind == "recovery"
    grid.client.orders[oid] = replace(grid.client.orders[oid], status=OrderStatus.CANCELED)
    grid.check_filled_orders()
    assert level.status == "CANCELED"
    assert not grid.paused_reason
    assert grid.cancel_managed_orders()
    cancel.assert_called_once_with(grid.symbol, oid)


def test_runtime_leverage_and_hedged_directions(build):
    grid = build()
    assert grid.sync_orders({"buy": [99], "sell": [101]}, 100)
    assert [(p["side"], p["position_side"], p["leverage"]) for p in grid.client.placed] == [
        (OrderSide.BUY, PositionSide.LONG, 7), (OrderSide.SELL, PositionSide.SHORT, 7)]


def test_inventory_sell_never_opens_short_without_owned_long(build):
    grid = build(mode="inventory")
    grid.sync_orders({"buy": [], "sell": [101]}, 100)
    assert grid.client.placed == []
    assert grid.commands == {}


@pytest.mark.parametrize("position_mode,position_side", [("hedge", PositionSide.LONG), ("one_way", PositionSide.BOTH)])
def test_inventory_sell_closes_long_and_reserves_volume(build, position_mode, position_side):
    grid = build(mode="inventory", position_mode=position_mode)
    grid.client.positions = [position("owned", quantity=0.15)]
    grid.owned_positions["owned"] = {"quantity": 0.15, "side": "LONG", "observed_quantity": 0.15}
    assert grid.sync_orders({"buy": [], "sell": [101, 102, 103]}, 100)
    calls = grid.client.placed
    assert len(calls) == 2
    assert all(p["side"] == OrderSide.SELL and p["position_side"] == position_side and p["reduce_only"] for p in calls)
    assert sum(p["quantity"] for p in calls) == pytest.approx(0.15)
    assert all(p["position_id"] == "owned" for p in calls)


def test_missing_reduce_api_pauses_without_short_fallback(build):
    client = Client()
    client.place_reduce_order = None
    client.positions = [position("owned")]
    grid = build(client, mode="inventory")
    grid.owned_positions["owned"] = {"quantity": 0.1, "side": "LONG"}
    assert not grid.sync_orders({"buy": [], "sell": [101]}, 100)
    assert grid.paused_reason
    assert client.placed == []
    assert next(iter(grid.commands.values())).status == "REJECTED"


def test_inventory_uses_both_sides_and_gross_not_net(build):
    grid = build()
    grid.client.positions = [position("manual-long", quantity=0.8), position("manual-short", PositionSide.SHORT, 0.7)]
    assert grid.update_inventory() == pytest.approx(0.1)
    assert grid.state.gross_inventory == pytest.approx(1.5)
    assert grid.state.long_inventory == 0.8
    assert grid.state.short_inventory == 0.7
    grid.sync_orders({"buy": [99], "sell": [101]}, 100)
    assert grid.client.placed == []


def test_pending_full_quantity_limits_grid_risk(build):
    grid = build(inventory_limit=0.25)
    grid.sync_orders({"buy": [99, 98, 97], "sell": [101, 102]}, 100)
    assert sum(p["quantity"] for p in grid.client.placed) <= 0.25
    assert len(grid.client.placed) == 2


def test_emergency_never_closes_manual_positions(build):
    grid = build()
    grid.client.positions = [position("manual", quantity=2)]
    assert grid.check_emergency_conditions(80)
    assert grid.emergency_close()
    assert grid.client.closed == []
    assert grid.client.positions[0].size == 2


def test_emergency_bingx_only_registered_owned_volume(build):
    grid = build()
    grid.client.positions = [position("shared", quantity=0.9), position("manual-short", PositionSide.SHORT, 0.2)]
    grid.owned_positions["shared"] = {"quantity": 0.1, "side": "LONG", "observed_quantity": 0.9}
    assert grid.emergency_close()
    assert grid.client.closed[0][:2] == ("shared", 1.0)
    assert grid.client.positions[0].size == pytest.approx(0.8)
    assert grid.client.positions[1].size == 0.2
    assert grid.state.inventory == pytest.approx(0.6)


def test_emergency_mexc_raw_percentage_is_owned_fraction(build):
    client = Client()
    client.register_owned_position = None
    client.positions = [position("shared", quantity=0.9)]
    grid = build(client, exchange="mexc")
    grid.owned_positions["shared"] = {"quantity": 0.1, "side": "LONG", "observed_quantity": 0.9}
    assert grid.emergency_close()
    assert client.closed[0][1] == pytest.approx(1 / 9)
    assert client.positions[0].size == pytest.approx(0.8)


def test_external_position_reduction_blocks_emergency(build):
    grid = build()
    grid.client.positions = [position("shared", quantity=0.8)]
    grid.owned_positions["shared"] = {"quantity": 0.1, "side": "LONG", "observed_quantity": 0.9}
    assert not grid.emergency_close()
    assert grid.client.closed == []
    assert grid.paused_reason


def test_restart_restores_exact_ids_without_price_adoption(build):
    client = Client()
    original = build(client)
    original.sync_orders({"buy": [99], "sell": []}, 100)
    own_id = next(iter(original.active_orders))
    cid = original.active_orders[own_id].client_order_id
    original.close()
    client.orders["manual"] = order("manual", price=99, position_side=PositionSide.SHORT)
    restored = build(client)
    restored.check_filled_orders()
    assert own_id in restored.active_orders
    assert restored.active_orders[own_id].client_order_id == cid
    assert client.restored == [own_id]
    assert "manual" not in restored.known_order_ids
    assert len(client.placed) == 1


def test_restart_changed_mode_blocks_new_orders(build):
    original = build()
    original.sync_orders({"buy": [99], "sell": []}, 100)
    original.close()
    restored = build(original.client, mode="inventory")
    assert restored.paused_reason
    assert not restored.sync_orders({"buy": [98], "sell": []}, 100)
    assert len(original.client.placed) == 1


def test_state_keys_isolate_account_exchange_market_instance(build):
    grids = [build(), build(account="second"), build(exchange="mexc"), build(instance="second"),
             build(exchange="mexc", market="spot", mode="inventory", position_mode="one_way")]
    assert len({g._store.path for g in grids}) == 5


def test_duplicate_scope_writer_is_rejected(build):
    build()
    with pytest.raises(ExchangeStateUnavailableError, match="уже запущен"):
        build()


def test_unknown_submission_is_durable_and_never_reposted(build):
    client = Client()
    client.fail_place = True
    grid = build(client)
    assert not grid.sync_orders({"buy": [99], "sell": []}, 100)
    cid = next(iter(grid.commands))
    assert len(cid) <= 32
    assert json.loads(grid._store.path.read_text())["commands"][cid]["status"] == "UNKNOWN"
    for _ in range(10):
        grid.sync_orders({"buy": [99], "sell": []}, 100)
    assert len(client.placed) == 1
    assert client.lookup_calls == 2
    grid.close()
    restored = build(client)
    restored.sync_orders({"buy": [99], "sell": []}, 100)
    assert next(iter(restored.commands)) == cid
    assert len(client.placed) == 1


def test_prepared_written_before_submission(build):
    client = Client()
    grid = build(client)
    original = client.place_order

    def submit(**kwargs):
        saved = json.loads(grid._store.path.read_text())
        assert saved["commands"][kwargs["client_order_id"]]["status"] == "PREPARED"
        return original(**kwargs)

    client.place_order = submit
    grid.sync_orders({"buy": [99], "sell": []}, 100)


def test_partial_fill_accounted_once_and_owned_only(build):
    grid = build()
    grid.sync_orders({"buy": [100], "sell": []}, 100)
    oid, level = next(iter(grid.active_orders.items()))
    grid.client.positions = [position("own", quantity=0.04)]
    grid.client.orders[oid] = replace(grid.client.orders[oid], status=OrderStatus.PARTIALLY_FILLED, filled_quantity=0.04,
                                    realized_pnl=2, commission=0.01, raw_data={"clientOrderId": level.client_order_id, "positionId": "own"})
    assert grid.check_filled_orders()[0]["qty"] == 0.04
    assert grid.check_filled_orders() == []
    assert grid.owned_positions["own"]["quantity"] == 0.04
    grid.client.positions = [position("own", quantity=0.1)]
    grid.client.orders[oid] = replace(grid.client.orders[oid], status=OrderStatus.FILLED, filled_quantity=0.1, realized_pnl=3, commission=0.02)
    assert grid.check_filled_orders()[0]["qty"] == pytest.approx(0.06)
    assert grid.owned_positions["own"]["quantity"] == pytest.approx(0.1)
    assert grid.state.gross_pnl == 3
    assert grid.state.total_fees == 0.02
    assert oid not in grid.active_orders


def test_simultaneous_fills_without_position_id_are_reconciled_as_batch(build):
    grid = build()
    grid.sync_orders({"buy": [100, 99], "sell": []}, 100)
    total = sum(v.quantity for v in grid.active_orders.values())
    grid.client.positions = [position("own", quantity=total)]
    for oid, level in grid.active_orders.items():
        grid.client.orders[oid] = replace(grid.client.orders[oid], status=OrderStatus.FILLED, filled_quantity=level.quantity)
    assert len(grid.check_filled_orders()) == 2
    assert grid.owned_positions["own"]["quantity"] == pytest.approx(total)
    assert not grid.paused_reason


def test_missing_order_is_not_treated_as_fill(build):
    grid = build()
    grid.sync_orders({"buy": [99], "sell": []}, 100)
    grid.client.orders.clear()
    assert grid.check_filled_orders() == []
    assert grid.paused_reason
    assert grid.state.total_filled_buy == 0
    assert grid.owned_positions == {}


def test_client_id_mismatch_blocks_restoration(build):
    grid = build()
    grid.sync_orders({"buy": [99], "sell": []}, 100)
    oid = next(iter(grid.active_orders))
    grid.client.orders[oid] = replace(grid.client.orders[oid], raw_data={"clientOrderId": "manual"})
    grid._restored.clear()
    assert grid.check_filled_orders() == []
    assert grid.paused_reason
    assert grid.client.restored == []


def test_corrupt_state_never_falls_back_to_empty_registry(build):
    grid = build()
    path = grid._store.path
    grid.close()
    path.write_text("{broken")
    with pytest.raises(ExchangeStateUnavailableError, match="невозможно"):
        build()


def test_read_only_public_cycle_no_private_calls_or_files(build, tmp_path):
    from src.core.strategies.grid.worker import run_grid_cycle
    client = Client()
    for method in ("get_open_orders", "get_recent_orders", "get_positions", "place_order", "place_reduce_order",
                   "cancel_order", "close_position", "register_owned_order", "register_owned_position"):
        setattr(client, method, Mock(side_effect=AssertionError("private вызов в read_only")))
    grid = build(client, execution_mode="read_only")
    result = run_grid_cycle(grid)
    assert result["mid_price"] == 100
    assert result["reference_price"] == 100
    assert result["target_prices"]["buy"] == [99.7, 99.4]
    assert grid.update_inventory() == 0
    assert grid.check_filled_orders() == []
    assert not grid.check_emergency_conditions(90)
    assert not grid.emergency_close()
    assert grid.cancel_managed_orders()
    assert list(tmp_path.iterdir()) == []


def test_read_only_bingx_mark_reference_uses_public_endpoint(build, monkeypatch):
    from src.core.strategies.grid import worker
    grid = build(execution_mode="read_only")
    grid.client.get_ticker = lambda symbol: SimpleNamespace(symbol="BTCUSDT", bid_price=99, ask_price=101, last_price=103, mark_price=None)
    fetcher = SimpleNamespace(_get=Mock(return_value={"code": 0, "data": {"symbol": "BTC-USDT", "markPrice": "102"}}))
    monkeypatch.setattr(worker, "PublicRESTFetcher", Mock(return_value=fetcher))
    result = worker.run_grid_cycle(grid)
    assert result["mid_price"] == 100
    assert result["reference_price"] == 102
    assert grid.state.reference_source == "mark"
    assert fetcher._get.call_args.args[1:] == ("https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex", {"symbol": "BTC-USDT"})


def test_last_reference_is_not_mid_price(build):
    from src.core.strategies.grid.worker import run_grid_cycle
    grid = build(execution_mode="read_only")
    grid.settings["protection"]["trigger_price"] = "last"
    grid.client.get_ticker = lambda symbol: SimpleNamespace(bid_price=99, ask_price=101, last_price=103)
    result = run_grid_cycle(grid)
    assert result["mid_price"] == 100
    assert result["reference_price"] == 103


def test_ws_uses_exact_source_fresh_reader(monkeypatch):
    from src.core.strategies.grid import worker
    from src.exchanges.market_data import StreamKey
    settings = validate_trading_settings({"exchange": "mexc", "market_type": "spot"})
    reader = Mock(return_value={"candles": [{"timestamp": 0, "is_closed": True, "close": 100}]})
    monkeypatch.setattr(worker, "get_market_snapshot", reader)
    monkeypatch.setattr(worker, "closed_candles", lambda rows, interval: rows)
    assert worker._market_candles("BTC-USDT", settings, interval="1m", limit=1)[0]["close"] == 100
    reader.assert_called_once_with(StreamKey("mexc", "spot", "BTCUSDT", "1m"), max_age_seconds=30)


def test_unknown_resolved_by_exact_id_resumes_without_repost(build):
    client = Client()
    client.fail_place = True
    grid = build(client)
    grid.sync_orders({"buy": [100], "sell": []}, 100)
    level = next(iter(grid.commands.values()))
    client.orders["accepted"] = order("accepted", level.client_order_id)
    grid.check_filled_orders()
    assert not grid.paused_reason
    assert "accepted" in grid.active_orders
    grid.sync_orders({"buy": [100], "sell": []}, 100)
    assert len(client.placed) == 1


def test_mexc_restart_uses_restore_order_ownership(build):
    client = Client()
    client.register_owned_order = None
    client.restore_order_ownership = Mock()
    grid = build(client, exchange="mexc")
    grid.sync_orders({"buy": [100], "sell": []}, 100)
    grid.close()
    restored = build(client, exchange="mexc")
    restored.check_filled_orders()
    client.restore_order_ownership.assert_called_once_with("BTC-USDT", "1")


def test_native_contract_quantity_and_price_steps(build):
    from decimal import Decimal
    client = Client()
    client.get_instrument_rules = Mock(return_value=SimpleNamespace(
        price_step=Decimal("0.5"), quantity_step=Decimal("1"), contract_size=Decimal("0.03")))
    grid = build(client)
    assert grid._calculate_order_quantity(100) == 0.09
    assert grid.calculate_grid_prices(100) == {"buy": [99.5, 99.0], "sell": [100.5, 101.0]}


def test_real_coordinator_managed_limits_and_fill_ownership(build, tmp_path):
    from decimal import Decimal
    from src.core.execution_coordinator import ExecutionCoordinator
    from src.core.execution_journal import stable_client_id
    from src.exchanges.dto.models import InstrumentRules

    class RawClient(Client):
        _submission_observer = None
        _config = SimpleNamespace(trading_settings=validate_trading_settings({"execution_mode": "live"}))

        def invalidate_cache(self, cache_type=None):
            pass

        def get_instrument_rules(self, symbol):
            return InstrumentRules("BTCUSDT", "BTC-USDT", "BTC", "USDT", True,
                                   Decimal("0.01"), Decimal("0.0001"), Decimal("0.0001"), Decimal("100"))

        def place_order(self, symbol, side, quantity, order_type=OrderType.MARKET, position_side=None, **kwargs):
            return super().place_order(symbol=symbol, side=side, quantity=float(quantity),
                                       order_type=order_type, position_side=position_side, **kwargs)

    raw = RawClient()
    coordinator = ExecutionCoordinator(raw, journal_path=tmp_path / "execution.sqlite3",
        settings=validate_trading_settings({"execution_mode": "live"}), owner_id="btc_grid", account_id="offline-account")
    grid = build(coordinator)
    assert grid.sync_orders({"buy": [100, 99], "sell": [101]}, 100)
    assert len(raw.placed) == 3
    assert not coordinator.is_grid_symbol_blocked(grid.symbol)
    level = next(iter(grid.commands.values()))
    assert level.client_order_id == stable_client_id(coordinator.account_id, level.command_id)
    assert raw.placed[0]["client_order_id"] == level.client_order_id
    assert coordinator.journal.get(coordinator.account_id, level.command_id)["order_id"] == level.order_id
    assert grid.check_filled_orders() == []
    assert not grid.paused_reason
    raw.orders[level.order_id] = replace(raw.orders[level.order_id], status=OrderStatus.FILLED, filled_quantity=level.quantity)
    raw.positions = [position("own", quantity=level.quantity)]
    assert grid.check_filled_orders()[0]["qty"] == level.quantity
    assert coordinator.owns_position("BTCUSDT", "own")
    assert grid.owned_positions["own"]["quantity"] == level.quantity


@pytest.fixture
def factory_grid(tmp_path, monkeypatch):
    import copy
    from decimal import Decimal
    from urllib.parse import parse_qs, urlsplit
    from src.exchanges import exchange_factory
    from test_bingx_safety_regressions import FakeExchange, Response

    class GridExchange(FakeExchange):
        def __init__(self):
            super().__init__()
            self.holdings = {}
            self.manual_positions = []

        def fill(self, oid, quantity=None):
            row = self.orders[oid]
            amount = Decimal(str(quantity)) if quantity is not None else Decimal(row["origQty"]) - Decimal(row["executedQty"])
            direction = row.get("positionSide")
            if direction == "BOTH":
                direction = "LONG"
            opening = row["side"] == ("BUY" if direction == "LONG" else "SELL")
            self.holdings[direction] = self.holdings.get(direction, Decimal(0)) + (amount if opening else -amount)
            assert self.holdings[direction] >= 0
            row["executedQty"] = str(Decimal(row["executedQty"]) + amount)
            row["status"] = "FILLED" if Decimal(row["executedQty"]) == Decimal(row["origQty"]) else "PARTIALLY_FILLED"
            row["positionId"] = "grid-" + direction.lower()
            row["avgPrice"] = row["price"] if Decimal(row["price"]) else "50000"

        def request(self, method, url, **kwargs):
            path = urlsplit(url).path
            if path.endswith("/positions"):
                params = kwargs.get("params") or {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
                self.calls.append((method, path, params))
                rows = [{"symbol": "BTC-USDT", "positionId": "grid-" + side.lower(),
                         "positionAmt": str(qty if self.hedge or side == "LONG" else -qty),
                         "availableAmt": str(qty), "positionSide": side if self.hedge else "BOTH",
                         "isolated": True, "avgPrice": "50000", "unrealizedProfit": "0", "leverage": self.leverage}
                        for side, qty in self.holdings.items() if qty]
                return Response(rows + copy.deepcopy(self.manual_positions))
            if path.endswith("/allOrders"):
                self.calls.append((method, path, {}))
                return Response({"orders": list(self.orders.values())})
            response = super().request(method, url, **kwargs)
            if path.endswith("/order") and method == "post":
                oid = str(len(self.orders))
                row = self.orders[oid]
                if row["type"] == "MARKET":
                    row["executedQty"] = "0"
                    self.fill(oid)
            return response

    monkeypatch.setenv("BINGX_API_KEY", "grid-factory-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "grid-factory-fake-secret")
    monkeypatch.setenv("SERVO_DATA_DIR", str(tmp_path))
    fake = GridExchange()
    fake.install(monkeypatch)
    grids = []

    def create(*, mode="hedged", position_mode="hedge", instance="factory-grid", reset=False, inventory_limit=0.02):
        if reset:
            exchange_factory.reset_client()
        fake.hedge = position_mode == "hedge"
        settings = validate_trading_settings({"exchange": "bingx", "execution_mode": "demo",
            "grid_mode": mode, "position_mode": position_mode, "protection": {"trigger_price": "last"}})
        runtime = {"execution": settings, "STRATEGY_INSTANCE_ID": instance,
            "EXECUTION_ACCOUNT_ID": "default:bingx:demo", "LEVERAGE": 7, "DATA_DIR": str(tmp_path)}
        grid = GridExecutor("BTC-USDT", {"order_size_usdt": 100, "inventory_limit": inventory_limit},
                            runtime, state_dir=tmp_path)
        grids.append(grid)
        return grid

    exchange_factory.reset_client()
    yield fake, create
    for grid in grids:
        grid.close()
    exchange_factory.reset_client()
    from src.utils.log_context import clear_log_context
    clear_log_context()


def test_actual_trading_factory_owner_account_leverage_ack_and_restart(factory_grid):
    from src.core.execution_coordinator import ExecutionCoordinator
    fake, create = factory_grid
    grid = create()
    assert isinstance(grid.client, ExecutionCoordinator)
    assert grid.client.owner_id == "factory-grid"
    assert grid.client.account_id == "default:bingx:demo"
    assert grid.sync_orders({"buy": [49000], "sell": []}, 50000)
    level = next(iter(grid.commands.values()))
    assert grid.client.journal.get(grid.client.account_id, level.command_id)["state"] == "ACK"
    assert not grid.owned_positions
    assert fake.leverage == 7
    assert fake.orders[level.order_id]["clientOrderId"] == level.client_order_id
    fake.fill(level.order_id)
    assert grid.check_filled_orders()[0]["qty"] == level.quantity
    assert grid.client.owns_position("BTC-USDT", "grid-long")
    posts = len(fake.mutations)
    grid.close()
    restarted = create(reset=True)
    assert restarted.client.owner_id == "factory-grid"
    assert restarted.client.account_id == grid.client.account_id
    assert restarted.sync_orders({"buy": [], "sell": []}, 50000)
    assert len(fake.mutations) == posts
    assert restarted.owned_positions["grid-long"]["quantity"] == level.quantity


def test_actual_factory_hedged_simultaneous_limits_batch_fill_restart_and_cancel(factory_grid):
    fake, create = factory_grid
    grid = create()
    assert callable(getattr(grid.client, "place_grid_order", None))
    targets = {"buy": [49000, 48000], "sell": [51000, 52000]}
    assert grid.sync_orders(targets, 50000)
    assert len(grid.active_orders) == 4
    assert not grid.client.is_grid_symbol_blocked(grid.symbol)
    assert {r["operation"] for r in grid.client.journal.intents(grid.client.account_id)} == {"grid_entry"}
    for oid, level in grid.active_orders.items():
        if level.side == "BUY":
            fake.fill(oid)
    assert len(grid.check_filled_orders()) == 2
    assert grid.owned_positions["grid-long"]["quantity"] == pytest.approx(0.004)
    assert grid.client.journal.owned(grid.client.account_id)[0]["quantity"] == "0.004"
    ids = set(grid.active_orders)
    posts = len([call for call in fake.mutations if call[0] == "post"])
    grid.close()
    restarted = create(reset=True)
    assert restarted.sync_orders({"buy": [], "sell": targets["sell"]}, 50000)
    assert set(restarted.active_orders) == ids
    assert len([call for call in fake.mutations if call[0] == "post"]) == posts
    assert restarted.cancel_managed_orders()
    assert not restarted.active_orders
    assert restarted.owned_positions["grid-long"]["quantity"] == pytest.approx(0.004)


@pytest.mark.parametrize("position_mode", ["hedge", "one_way"])
def test_actual_factory_inventory_accumulates_and_limit_reduces_long(factory_grid, position_mode):
    fake, create = factory_grid
    grid = create(mode="inventory", position_mode=position_mode)
    assert callable(getattr(grid.client, "place_reduce_order", None))
    assert grid.sync_orders({"buy": [49000, 48000], "sell": []}, 50000)
    assert len(grid.active_orders) == 2
    for oid in list(grid.active_orders):
        fake.fill(oid)
    assert len(grid.check_filled_orders()) == 2
    assert grid.sync_orders({"buy": [], "sell": [51000, 52000]}, 50000)
    assert len(grid.active_orders) == 2
    for oid, level in grid.active_orders.items():
        raw = fake.orders[oid]
        assert raw["side"] == "SELL"
        assert raw["positionSide"] == ("BOTH" if position_mode == "one_way" else "LONG")
        if position_mode == "one_way":
            assert str(raw["reduceOnly"]).lower() == "true"
        assert level.reduce_only and level.position_id == "grid-long"
        assert grid.client.journal.get(grid.client.account_id, level.command_id)["operation"] == "grid_reduce"
        fake.fill(oid)
    assert len(grid.check_filled_orders()) == 2
    assert fake.holdings.get("SHORT", 0) == 0
    assert float(fake.holdings["LONG"]) == pytest.approx(0.002)
    assert grid.owned_positions["grid-long"]["quantity"] == pytest.approx(0.002)
    assert not grid.paused_reason


def test_actual_factory_emergency_closes_both_owned_directions(factory_grid):
    fake, create = factory_grid
    grid = create()
    assert grid.sync_orders({"buy": [49000], "sell": [51000]}, 50000)
    for oid in grid.active_orders:
        fake.fill(oid)
    assert len(grid.check_filled_orders()) == 2
    assert set(grid.owned_positions) == {"grid-long", "grid-short"}
    assert grid.emergency_close()
    assert not grid.owned_positions
    assert not any(fake.holdings.values())
    assert not grid.paused_reason


def test_actual_factory_fill_between_snapshot_and_reconcile_is_not_lost(factory_grid):
    import copy
    fake, create = factory_grid
    grid = create()
    targets = {"buy": [49000], "sell": []}
    assert grid.sync_orders(targets, 50000)
    stale = copy.deepcopy(grid.client.get_open_orders(grid.symbol))
    fake.fill(next(iter(grid.active_orders)))
    assert grid.reconcile(stale)[0]["qty"] == pytest.approx(0.002)
    assert not grid.active_orders
    assert grid.owned_positions["grid-long"]["quantity"] == pytest.approx(0.002)
    assert not grid.paused_reason


def test_actual_factory_pending_gross_budget_does_not_net_hedged_sides(factory_grid):
    from decimal import Decimal
    fake, create = factory_grid
    grid = create(inventory_limit=0.004)
    assert grid.sync_orders({"buy": [49000, 48000], "sell": [51000, 52000]}, 50000)
    assert len(grid.active_orders) == 2
    assert sum(Decimal(raw["quantity"]) for raw in fake.orders.values()) == Decimal("0.004")
    assert all(raw["side"] == "BUY" for raw in fake.orders.values())
    for oid in grid.active_orders:
        fake.fill(oid)
    grid.check_filled_orders()
    assert grid.sync_orders({"buy": [], "sell": [51000, 52000]}, 50000)
    assert len(fake.orders) == 2


def test_actual_factory_partial_fill_blocks_new_limits_until_resolved(factory_grid):
    fake, create = factory_grid
    grid = create()
    targets = {"buy": [49000, 48000], "sell": [51000, 52000]}
    assert grid.sync_orders(targets, 50000)
    oid = next(iter(grid.active_orders))
    fake.fill(oid, "0.001")
    assert grid.check_filled_orders()[0]["qty"] == 0.001
    assert grid.client.is_grid_symbol_blocked(grid.symbol)
    before = len(fake.orders)
    assert grid.sync_orders({"buy": [49000, 48000, 47000], "sell": targets["sell"]}, 50000)
    assert len(fake.orders) == before
    fake.fill(oid)
    assert grid.check_filled_orders()[0]["qty"] == 0.001
    assert not grid.client.is_grid_symbol_blocked(grid.symbol)
    assert not grid.paused_reason


def test_actual_factory_never_adopts_manual_order_matching_price(factory_grid):
    fake, create = factory_grid
    fake.add_order({"symbol": "BTC-USDT", "side": "BUY", "positionSide": "LONG", "type": "LIMIT",
                    "quantity": "0.001", "price": "49000", "clientOrderId": "manual-order"})
    grid = create()
    assert grid.sync_orders({"buy": [49000], "sell": []}, 50000)
    assert not grid.commands
    assert not grid.known_order_ids
    assert not fake.mutations
    assert fake.orders["1"]["status"] == "NEW"


def test_actual_factory_grid_cancel_never_touches_foreign_order(factory_grid):
    from decimal import Decimal
    fake, create = factory_grid
    grid = create()
    assert grid.sync_orders({"buy": [49000, 48000], "sell": []}, 50000)
    managed = set(grid.active_orders)
    fake.add_order({"symbol": "BTC-USDT", "side": "SELL", "positionSide": "SHORT", "type": "LIMIT",
                    "quantity": "0.001", "price": "55000", "clientOrderId": "manual-order"})
    manual_id = str(len(fake.orders))
    assert grid.cancel_managed_orders()
    canceled = {params["orderId"] for method, path, params in fake.mutations if method == "delete"}
    assert canceled == managed
    assert fake.orders[manual_id]["status"] == "NEW"
    assert grid.owned_positions == {}


def test_actual_trading_factory_read_only_never_authenticates(tmp_path, monkeypatch):
    from src.exchanges import exchange_factory
    from src.core.strategies.grid.worker import run_grid_cycle
    from test_bingx_safety_regressions import FakeExchange
    fake = FakeExchange()
    fake.install(monkeypatch)
    monkeypatch.setenv("SERVO_DATA_DIR", str(tmp_path))
    exchange_factory.reset_client()
    settings = validate_trading_settings({"protection": {"trigger_price": "last"}})
    grid = GridExecutor("BTC-USDT", {}, {"execution": settings, "STRATEGY_INSTANCE_ID": "readonly-grid"}, state_dir=tmp_path)
    try:
        assert run_grid_cycle(grid)["mid_price"] == 50000
        assert not any("/user/" in path or "/trade/" in path for method, path, params in fake.calls)
        assert not fake.mutations
        assert not (tmp_path / "execution.sqlite3").exists()
        assert not (tmp_path / "grid").exists()
    finally:
        grid.close()
        exchange_factory.reset_client()
        from src.utils.log_context import clear_log_context
        clear_log_context()


@pytest.fixture
def mexc_factory_grid(tmp_path, monkeypatch):
    import copy
    from decimal import Decimal
    from src.exchanges import exchange_factory
    from src.exchanges.impl.mexc_transport import MEXCFuturesTransport
    from test_mexc import FuturesTransport, futures_order, position as mexc_position

    class GridTransport(FuturesTransport):
        def __init__(self):
            super().__init__()
            self.orders = {}
            self.holdings = {1: Decimal(0), 2: Decimal(0)}
            self.position_mode = 1

        def fill(self, oid):
            row = self.orders[str(oid)]
            qty = Decimal(row["vol"]) - Decimal(row["dealVol"])
            direction = 1 if row["side"] in {1, 4} else 2
            self.holdings[direction] += qty if row["side"] in {1, 3} else -qty
            assert self.holdings[direction] >= 0
            row.update(dealVol=row["vol"], state=3, dealAvgPrice=row["price"], positionId=40 + direction)

        def request(self, method, endpoint, params=None, **kwargs):
            self.calls.append((method, endpoint, copy.deepcopy(params), kwargs))
            if endpoint == "/api/v1/private/position/position_mode":
                data = self.position_mode
            elif endpoint == "/api/v1/private/position/open_positions":
                data = [mexc_position(holdVol=str(qty), positionType=side, positionId=40 + side,
                                     leverage=7) for side, qty in self.holdings.items() if qty]
            elif endpoint == "/api/v1/private/order/list/open_orders":
                data = [copy.deepcopy(row) for row in self.orders.values() if row["state"] in {1, 2}]
            elif endpoint == "/api/v1/private/order/list/history_orders":
                data = copy.deepcopy(list(self.orders.values()))
            elif endpoint == "/api/v1/private/order/create":
                self.last_create = copy.deepcopy(params)
                oid = str(100 + len(self.orders))
                self.orders[oid] = futures_order(orderId=int(oid), externalOid=params["externalOid"],
                    vol=params["vol"], dealVol="0", dealAvgPrice="0", side=params["side"],
                    orderType=params["type"], price=params["price"], state=1,
                    positionId=params.get("positionId", 41 if params["side"] in {1, 4} else 42),
                    openType=1, leverage=params.get("leverage", 7))
                if params["type"] == 5:
                    self.fill(oid)
                data = {"orderId": int(oid)}
            elif "/order/external/" in endpoint:
                data = next((copy.deepcopy(row) for row in self.orders.values()
                             if row["externalOid"] == endpoint.rsplit("/", 1)[1]), None)
            elif "/order/get/" in endpoint:
                data = copy.deepcopy(self.orders[endpoint.rsplit("/", 1)[1]])
            elif endpoint == "/api/v1/private/order/cancel":
                data = []
                for oid in params["orderIds"]:
                    self.orders[str(oid)]["state"] = 4
                    data.append({"orderId": oid, "errorCode": 0})
            else:
                self.calls.pop()
                return super().request(method, endpoint, params, **kwargs)
            return {"success": True, "code": 0, "data": data}

    transport = GridTransport()
    monkeypatch.setattr(MEXCFuturesTransport, "request", lambda self, *args, **kwargs: transport.request(*args, **kwargs))
    monkeypatch.setenv("MEXC_API_KEY", "grid-factory-fake-key")
    monkeypatch.setenv("MEXC_SECRET_KEY", "grid-factory-fake-secret")
    monkeypatch.setenv("SERVO_DATA_DIR", str(tmp_path))
    grids = []

    def create(*, mode="hedged", position_mode="hedge", reset=False):
        if reset:
            exchange_factory.reset_client()
        transport.position_mode = 1 if position_mode == "hedge" else 2
        settings = validate_trading_settings({"exchange": "mexc", "execution_mode": "live",
            "grid_mode": mode, "position_mode": position_mode, "protection": {"trigger_price": "last"}})
        grid = GridExecutor("BTC-USDT", {"order_size_usdt": 100, "inventory_limit": 0.02},
            {"execution": settings, "STRATEGY_INSTANCE_ID": "mexc-grid", "LEVERAGE": 7,
             "EXECUTION_ACCOUNT_ID": "default:mexc:live", "DATA_DIR": str(tmp_path)}, state_dir=tmp_path)
        grids.append(grid)
        return grid

    exchange_factory.reset_client()
    yield transport, create
    for grid in grids:
        grid.close()
    exchange_factory.reset_client()
    from src.utils.log_context import clear_log_context
    clear_log_context()


def test_actual_mexc_factory_simultaneous_hedged_limits_restart_cancel(mexc_factory_grid):
    transport, create = mexc_factory_grid
    grid = create()
    targets = {"buy": [49000, 48000], "sell": [51000, 52000]}
    assert grid.sync_orders(targets, 50000)
    assert len(grid.active_orders) == 4
    assert {row["side"] for row in transport.orders.values()} == {1, 3}
    assert all(row["leverage"] == 7 and len(row["externalOid"]) <= 32 for row in transport.orders.values())
    for oid in grid.active_orders:
        transport.fill(oid)
    assert len(grid.check_filled_orders()) == 4
    assert grid.owned_positions["41"]["quantity"] == pytest.approx(0.004)
    assert grid.owned_positions["42"]["quantity"] == pytest.approx(0.002)
    grid.close()
    restarted = create(reset=True)
    assert restarted.sync_orders({"buy": [47000], "sell": [53000]}, 50000)
    assert restarted.cancel_managed_orders()
    assert not restarted.active_orders
    assert not restarted.paused_reason


@pytest.mark.parametrize("position_mode", ["hedge", "one_way"])
def test_actual_mexc_factory_inventory_limit_reduce_long(mexc_factory_grid, position_mode):
    transport, create = mexc_factory_grid
    grid = create(mode="inventory", position_mode=position_mode)
    assert grid.sync_orders({"buy": [49000, 48000], "sell": []}, 50000)
    for oid in grid.active_orders:
        transport.fill(oid)
    assert len(grid.check_filled_orders()) == 2
    assert grid.sync_orders({"buy": [], "sell": [51000, 52000]}, 50000)
    assert len(grid.active_orders) == 2
    for oid, level in grid.active_orders.items():
        assert level.reduce_only and level.position_id == "41"
        assert transport.orders[oid]["side"] == 4
    if position_mode == "one_way":
        assert transport.last_create["reduceOnly"] is True
    assert transport.last_create["positionId"] == 41
    for oid in grid.active_orders:
        transport.fill(oid)
    assert len(grid.check_filled_orders()) == 2
    assert not transport.holdings[2]
    assert grid.owned_positions["41"]["quantity"] == pytest.approx(0.002)
    assert not grid.paused_reason


def test_public_rest_fallback_normalizes_and_rejects_stale_history(build, monkeypatch):
    from src.core.strategies.grid import worker
    settings = validate_trading_settings({})
    now = 1_800_000_000
    rows = [{"timestamp": (now - 300 * i) * 1000, "openPrice": 99, "highPrice": 101,
             "lowPrice": 98, "closePrice": 100, "volume": 5} for i in range(50, 0, -1)]
    monkeypatch.setattr(worker.time, "time", lambda: now)
    monkeypatch.setattr(worker, "get_market_snapshot", Mock(return_value=None))
    fetch = Mock(return_value=rows)
    factory = Mock(return_value=fetch)
    monkeypatch.setattr(worker, "PublicRESTFetcher", factory)
    candles = worker._market_candles("BTC-USDT", settings)
    assert len(candles) == 50
    assert candles[-1]["closePrice"] == 100
    factory.assert_called_once_with(settings=settings["market_data"])
    multiplier, pause, adx = worker._analyze_volatility("BTC-USDT", None, {}, settings)
    assert multiplier > 0
    assert not pause
    assert adx["trend"] != "UNKNOWN"
    fetch.return_value = rows[:-1]
    with pytest.raises(ValueError, match="свежую непрерывную"):
        worker._market_candles("BTC-USDT", settings)


def test_emergency_dust_is_not_discarded_from_owned_registry(build):
    from decimal import Decimal, ROUND_FLOOR
    client = Client()
    client.get_instrument_rules = Mock(return_value=SimpleNamespace(
        price_step=Decimal("0.01"), quantity_step=Decimal("0.01"), contract_size=Decimal(1)))
    client.positions = [position("own", quantity=0.015)]

    def close(symbol, pid, percentage=1.0, *, client_order_id=None):
        qty = float((Decimal("0.015") / Decimal("0.01")).to_integral_value(rounding=ROUND_FLOOR) * Decimal("0.01"))
        client.closed.append((pid, percentage, client_order_id))
        client.positions = [position(pid, quantity=0.015 - qty)]
        return True

    client.close_position = close
    grid = build(client)
    grid.owned_positions["own"] = {"quantity": 0.015, "side": "LONG", "observed_quantity": 0.015}
    assert not grid.emergency_close()
    assert grid.owned_positions["own"]["quantity"] == pytest.approx(0.005)
    assert grid.paused_reason
    assert next(iter(grid.commands.values())).quantity == 0.01


def test_real_spot_adapter_net_lot_partial_sell_and_emergency_preserve_manual_coins(build, tmp_path):
    import time
    from decimal import Decimal
    from test_spot_trading_adapter import Clock, FakeSpot
    from src.exchanges.impl.spot_trading_adapter import SpotTradingAdapter

    spot = FakeSpot()
    spot.fee_asset = "BTC"
    spot.fee_rate = Decimal("0.01")
    adapter = SpotTradingAdapter(spot, ledger_path=tmp_path / "spot.sqlite", account_id="offline-account",
        instance_id="btc_grid", quote_budget="1000", execution_mode="live", local_protection=True,
        clock=Clock(), monitor_interval=60, snapshot_reader=lambda key: None)
    try:
        adapter.start_supervisor()
        deadline = time.monotonic() + 3
        while not adapter.supervisor_running and time.monotonic() < deadline:
            time.sleep(0.005)
        assert adapter.supervisor_running
        grid = build(adapter, exchange="mexc", market="spot", mode="inventory", position_mode="one_way")
        assert grid.sync_orders({"buy": [100], "sell": []}, 100)
        buy = next(iter(grid.commands.values()))
        spot.fill(buy.client_order_id, "0.1")
        assert grid.check_filled_orders()[0]["qty"] == 0.1
        assert grid.owned_positions[buy.client_order_id]["quantity"] == 0.099
        spot.fee_rate = Decimal(0)
        assert grid.sync_orders({"buy": [], "sell": [110]}, 100)
        sell = next(v for v in grid.commands.values() if v.reduce_only)
        spot.fill(sell.client_order_id, "0.04")
        assert grid.check_filled_orders()[0]["qty"] == 0.04
        assert grid.owned_positions[buy.client_order_id]["quantity"] == pytest.approx(0.059)
        assert grid.emergency_close()
        assert spot.wallet["BTC"][0] == Decimal("50")
        assert not grid.owned_positions
        assert not grid.paused_reason
    finally:
        adapter.shutdown()


def test_real_spot_grid_limit_sell_caps_quantity_for_base_fee(build, tmp_path):
    import time
    from decimal import Decimal
    from test_spot_trading_adapter import Clock, FakeSpot
    from src.exchanges.impl.spot_trading_adapter import SpotTradingAdapter
    spot = FakeSpot()
    spot.fee_asset, spot.fee_rate = "BTC", Decimal("0.01")
    adapter = SpotTradingAdapter(spot, ledger_path=tmp_path / "fee.sqlite", account_id="offline-account",
        owner_id="btc_grid", quote_budget=1000, execution_mode="live", local_protection=True,
        clock=Clock(), monitor_interval=60, snapshot_reader=lambda key: None)
    try:
        adapter.start_supervisor()
        deadline = time.monotonic() + 3
        while not adapter.supervisor_running and time.monotonic() < deadline:
            time.sleep(0.005)
        assert adapter.supervisor_running
        grid = build(adapter, exchange="mexc", market="spot", mode="inventory", position_mode="one_way")
        assert grid.sync_orders({"buy": [100], "sell": []}, 100)
        buy = next(iter(grid.commands.values()))
        spot.fill(buy.client_order_id, "0.1")
        grid.check_filled_orders()
        grid.order_size_usdt = 1000
        assert grid.sync_orders({"buy": [], "sell": [110]}, 100)
        sell = next(v for v in grid.commands.values() if v.reduce_only)
        assert sell.quantity == pytest.approx(0.098)
        spot.fill(sell.client_order_id, str(sell.quantity))
        grid.check_filled_orders()
        assert grid.owned_positions[buy.client_order_id]["quantity"] == pytest.approx(0.00002)
        assert spot.wallet["BTC"][0] == Decimal("50.00002")
        assert not grid.paused_reason
    finally:
        adapter.shutdown()


def test_spot_factory_facade_forwards_runtime_and_validates_adapter_identity(tmp_path, monkeypatch):
    from src.core.grid_executor import get_grid_client
    from src.exchanges import exchange_factory
    from src.exchanges.impl.spot_trading_adapter import SpotTradingAdapter
    from test_spot_trading_adapter import FakeSpot
    settings = validate_trading_settings({"exchange": "mexc", "market_type": "spot",
        "execution_mode": "live", "spot": {"quote_budget_usdt": 1000, "allow_local_protection": True}})
    adapter = SpotTradingAdapter(FakeSpot(), ledger_path=tmp_path / "facade.sqlite",
        account_id="default:mexc:live", owner_id="spot-grid", settings=settings)
    runtime = {"EXECUTION_ACCOUNT_ID": "default:mexc:live"}
    calls = []

    def factory(*, settings, owner_id, account_id, runtime_config):
        calls.append((settings, owner_id, account_id, runtime_config))
        return adapter

    monkeypatch.setattr(exchange_factory, "get_trading_client", factory)
    try:
        assert get_grid_client(settings, runtime, "spot-grid") is adapter
        assert calls == [(settings, "spot-grid", "default:mexc:live", runtime)]
        with pytest.raises(ExchangeStateUnavailableError):
            get_grid_client(settings, runtime, "another-grid")
    finally:
        adapter.shutdown()
        from src.utils.log_context import clear_log_context
        clear_log_context()


def test_spot_factory_raw_client_is_rejected_before_private_calls(monkeypatch):
    from src.core.grid_executor import get_grid_client
    from src.exchanges import exchange_factory
    from src.exchanges.errors import UnsupportedCapabilityError
    settings = validate_trading_settings({"exchange": "mexc", "market_type": "spot", "execution_mode": "live"})
    raw = SimpleNamespace(get_positions=Mock(), place_order=Mock())
    monkeypatch.setattr(exchange_factory, "get_trading_client", lambda settings: raw)
    try:
        with pytest.raises(UnsupportedCapabilityError, match="SpotTradingAdapter"):
            get_grid_client(settings, {}, "spot-grid")
        raw.get_positions.assert_not_called()
        raw.place_order.assert_not_called()
    finally:
        from src.utils.log_context import clear_log_context
        clear_log_context()


def test_registry_write_failure_never_sends_order(build, monkeypatch):
    grid = build()
    monkeypatch.setattr(grid._store, "save", Mock(side_effect=ExchangeStateUnavailableError("fsync недоступен")))
    with pytest.raises(ExchangeStateUnavailableError):
        grid.sync_orders({"buy": [100], "sell": []}, 100)
    assert grid.client.placed == []


def test_flat_restart_allows_grid_mode_transition(build):
    grid = build()
    grid.sync_orders({"buy": [100], "sell": []}, 100)
    assert grid.cancel_managed_orders()
    grid.close()
    restored = build(grid.client, mode="inventory")
    assert not restored.paused_reason


def test_external_position_change_cannot_be_adopted_from_fill_id(build):
    grid = build()
    grid.sync_orders({"buy": [100], "sell": []}, 100)
    oid, level = next(iter(grid.active_orders.items()))
    grid.client.orders[oid] = replace(grid.client.orders[oid], status=OrderStatus.FILLED, filled_quantity=0.1,
        raw_data={"clientOrderId": level.client_order_id, "positionId": "shared"})
    grid.client.positions = [position("shared", quantity=0.5)]
    assert grid.check_filled_orders() == []
    assert grid.paused_reason
    assert grid.owned_positions == {}
    assert grid.client.registered == []


def test_worker_binds_instance_before_client_factory(build, monkeypatch):
    from src.core.strategies.grid import worker
    from src.utils.log_context import clear_log_context, get_log_context
    grid = build(execution_mode="read_only")
    clear_log_context()

    def factory(settings, runtime, instance_id):
        assert instance_id == "btc_grid"
        assert get_log_context()["instance_id"] == "btc_grid"
        assert get_log_context()["exchange"] == "bingx"
        return grid.client

    monkeypatch.setattr(worker, "get_grid_client", factory)
    monkeypatch.setattr("src.utils.logger.setup_symbol_logger", lambda symbol: None)
    monkeypatch.setattr(worker, "_analyze_volatility", lambda *args: (1, False, {"adx": 0, "trend": "UNKNOWN"}))
    monkeypatch.setattr(worker, "run_grid_cycle", Mock(side_effect=KeyboardInterrupt))
    worker.run_grid_worker("BTC-USDT", {}, runtime_config={"execution": grid.settings, "STRATEGY_INSTANCE_ID": "btc_grid"})
    clear_log_context()


def test_worker_installs_shared_ws_proxies_before_factory(build, monkeypatch):
    from multiprocessing import Manager
    from src.core.strategies.grid import worker
    from src.utils.log_context import clear_log_context
    grid = build(execution_mode="read_only")
    installed = []
    monkeypatch.setattr(worker, "set_shared_cache", lambda cache, ready: installed.append((cache, ready)))
    monkeypatch.setattr("src.utils.logger.setup_symbol_logger", lambda symbol: None)
    monkeypatch.setattr(worker, "run_grid_cycle", Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(worker, "_analyze_volatility", lambda *args: (1, False, {"adx": 0, "trend": "UNKNOWN"}))
    with Manager() as manager:
        cache, ready = manager.dict(), manager.dict()

        def factory(settings, runtime, instance_id):
            assert installed == [(cache, ready)]
            assert runtime["EXECUTION_ACCOUNT_ID"] == "default:bingx:read_only"
            return grid.client

        monkeypatch.setattr(worker, "get_grid_client", factory)
        worker.run_grid_worker("BTC-USDT", {}, runtime_config={"execution": grid.settings,
            "STRATEGY_INSTANCE_ID": "btc_grid", "EXECUTION_ACCOUNT_ID": "default:bingx:read_only"},
            ws_cache=cache, ws_ready=ready)
    clear_log_context()


def test_spot_inventory_uses_owned_adapter_not_raw_short(build):
    client = Client()
    client.register_owned_order = None
    client.submit_order = Mock(side_effect=lambda **kwargs: SimpleNamespace(order_id=client.place_order(**kwargs)))
    client.positions = [position("own", quantity=0.1)]
    grid = build(client, exchange="mexc", market="spot", mode="inventory", position_mode="one_way")
    grid.owned_positions["own"] = {"quantity": 0.1, "side": "LONG"}
    assert grid.sync_orders({"buy": [], "sell": [101]}, 100)
    call = client.submit_order.call_args.kwargs
    assert call["position_side"] == PositionSide.LONG
    assert call["side"] == OrderSide.SELL
    assert call["position_id"] == "own"
    assert call["leverage"] == 1
    assert "reduce_only" not in call
