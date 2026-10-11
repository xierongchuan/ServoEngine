"""Поведенческие тесты единого контракта позиций в торговых runtime-путях."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
from inspect import signature
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.signals.utils import OrderAdapter, PositionAdapter
from src.exchanges.dto.models import (
    Order, OrderSide, OrderStatus, OrderType, Position, PositionSide,
)
from src.exchanges.errors import ExchangeStateUnavailableError, UnsupportedCapabilityError
from test_grid_execution_safety import factory_grid


@pytest.fixture
def managed_runtime(factory_grid, tmp_path, monkeypatch):
    """Настоящие factory/coordinator/journal; подменена только граница HTTP биржи."""
    from src import config
    from src.core import scalp_engine
    fake, create = factory_grid
    grid = create(inventory_limit=1)
    monkeypatch.setattr(scalp_engine, "DATA_DIR", str(tmp_path))
    monkeypatch.setitem(config.BOT_CONFIG, "STRATEGY_INSTANCE_ID", grid.instance_id)

    def open_owned(side=PositionSide.LONG, *, quantity=0.02):
        direction = OrderSide.BUY if side == PositionSide.LONG else OrderSide.SELL
        sl, tp = (49_000, 52_000) if side == PositionSide.LONG else (51_000, 48_000)
        command_id = "contract-entry-" + side.value.lower()
        grid.client.place_order("BTC-USDT", direction, quantity, price=50_000,
            position_side=side, leverage=grid.leverage, sl=sl, tp=tp, command_id=command_id)
        assert grid.client.journal.get(grid.client.account_id, command_id)["state"] == "FILLED"
        rows = grid.client.get_owned_positions("BTC-USDT")["BTCUSDT"]
        owned = next(p for p in rows if p.side == side)
        snapshot = grid.client.get_position_execution_snapshot("BTC-USDT", owned.position_id)
        assert snapshot["owner"] == grid.instance_id
        assert snapshot["snapshot"]["client_order_id"]
        assert len(snapshot["protections"]) == 2
        return owned

    def preload_legacy_grid_orders(prices, *, side=OrderSide.BUY, quantity=0.02):
        """Снимок ACK-сетки до запрета entries: fake биржа + оба durable-реестра, без POST/обхода guard."""
        nonlocal grid
        from src.core.grid_executor import GridLevel
        from src.core.execution_journal import stable_client_id

        assert grid.entries_blocked_reason
        assert not grid.commands and not grid.owned_positions
        saved = grid._store.load()
        direction = "LONG" if side == OrderSide.BUY else "SHORT"
        before = list(fake.mutations)
        fake.leverage = grid.leverage
        order_ids = []
        for index, price in enumerate(prices):
            command_id = f"legacy-contract-grid-{side.value.lower()}-{index}"
            cid = stable_client_id(grid.client.account_id, command_id)
            request = {"side": side.value, "quantity": str(quantity), "price": str(price),
                "order_type": "LIMIT", "position_side": direction, "owned_side": direction,
                "leverage": grid.leverage, "gross_limit": str(grid.inventory_limit)}
            intent, fresh = grid.client.journal.prepare(grid.client.account_id, grid.instance_id,
                grid.symbol, "grid_entry", request, {"before": [], "expected_quantity": str(quantity)},
                command_id=command_id, client_id=cid, grid_queue=True)
            assert fresh and intent["client_id"] == cid
            params = {"symbol": grid.symbol, "side": side.value, "quantity": str(quantity),
                "price": str(price), "type": "LIMIT", "positionSide": direction, "clientOrderId": cid}
            oid = fake.add_order(params)["orderId"]
            grid.client.journal.wire(grid.client.account_id, command_id,
                {"status": "ACKNOWLEDGED", "order_id": oid, "request_params": params})
            grid.client.journal.finish(grid.client.account_id, command_id, "ACK", order_id=oid,
                result={"order_id": oid, "filled_quantity": "0", "terminal": False,
                        "entry_complete": False, "close_confirmed": False})
            saved["commands"][cid] = asdict(GridLevel(price, side.value, quantity,
                order_id=oid, client_order_id=cid, command_id=command_id,
                position_side=direction, status="NEW"))
            order_ids.append(oid)
        grid._store.save(saved)
        grid.close()
        grid = create(reset=True, inventory_limit=1)
        runtime.grid, runtime.client = grid, grid.client
        assert grid.entries_blocked_reason and not grid.paused_reason
        assert set(grid.active_orders) == set(order_ids)
        assert grid.known_order_ids == set(order_ids)
        assert fake.mutations == before
        return order_ids

    def legacy_grid_fill(side=OrderSide.BUY, *, quantity=0.02, price=50_000):
        """Исполнение заранее загруженного legacy LIMIT при действующем запрете новых входов."""
        oid, = preload_legacy_grid_orders([price], side=side, quantity=quantity)
        fake.fill(oid)
        assert grid.check_filled_orders()[0]["order_id"] == oid
        owned = grid.client.get_grid_owned_positions("BTC-USDT")["BTCUSDT"][0]
        assert isinstance(owned, Position)
        assert owned.position_id in grid.owned_positions
        assert not grid.client.journal.protections(grid.client.account_id,
                                                  owner=grid.instance_id, symbol=grid.symbol)
        assert grid.entries_blocked_reason
        return owned

    runtime = SimpleNamespace(grid=grid, client=grid.client, fake=fake, create=create,
        open_owned=open_owned, legacy_grid_fill=legacy_grid_fill,
        preload_legacy_grid_orders=preload_legacy_grid_orders)
    return runtime


def dto_position(side=PositionSide.LONG, **overrides):
    values = {
        "symbol": "BTCUSDT",
        "side": side,
        "size": 0.02,
        "entry_price": 50_000,
        "unrealized_pnl": 20,
        "position_id": "position-42",
        "mark_price": 51_000,
        "leverage": 10,
        "created_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return Position(**values)


def dto_order(order_id="order-1", side=OrderSide.SELL, status=OrderStatus.FILLED, **overrides):
    values = {
        "order_id": order_id,
        "symbol": "BTCUSDT",
        "side": side,
        "order_type": OrderType.LIMIT,
        "status": status,
        "price": 51_000,
        "quantity": 0.02,
        "filled_quantity": 0.02,
        "average_price": 50_900,
        "commission": 1.5,
        "realized_pnl": 18,
        "updated_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return Order(**values)


@pytest.mark.parametrize(
    ("position", "direction", "position_id", "entry", "size"),
    [
        (dto_position(), "BUY", "position-42", 50_000, 0.02),
        ({"type": "sell", "dealId": "legacy-7", "entry": "60000", "size": "0.1"},
         "SELL", "legacy-7", 60_000, 0.1),
        ({"side": "SHORT", "positionId": 8, "avgPrice": 70_000, "positionAmt": -0.3},
         "SELL", "8", 70_000, 0.3),
    ],
)
def test_position_adapter_preserves_business_semantics(position, direction, position_id, entry, size):
    adapter = PositionAdapter(position)
    assert adapter.direction == direction
    assert adapter.position_id == position_id
    assert adapter.entry_price == entry
    assert adapter.size == size


def test_position_adapter_rejects_unknown_contract():
    with pytest.raises(TypeError, match="Неподдерживаемый контракт"):
        PositionAdapter(object())


def test_order_adapter_preserves_dto_semantics():
    adapter = OrderAdapter(dto_order())
    assert adapter.order_id == "order-1"
    assert adapter.side == "SELL"
    assert adapter.status == "FILLED"
    assert adapter.average_price == 50_900
    assert adapter.realized_pnl == 18


def test_scalp_manage_position_accepts_dto_and_checks_exit():
    from src.core.scalp_engine import ScalpEngine

    engine = ScalpEngine("BTCUSDT")
    engine._position_open_time = 10**20  # Time-exit не должен сработать.
    engine._partial_tp_enabled = False
    engine._signal_gen = MagicMock()
    engine._signal_gen.check_exit.return_value = {"should_close": False}
    engine._update_sl_on_exchange = MagicMock()

    engine._manage_position({"current_price": 51_000, "atr": 100}, dto_position())

    engine._signal_gen.check_exit.assert_called_once()
    assert engine._signal_gen.check_exit.call_args.args[1].position_id == "position-42"


def test_scalp_close_uses_dto_position_id_and_real_side(managed_runtime, monkeypatch):
    from src.core.scalp_engine import ScalpEngine
    runtime = managed_runtime
    position = runtime.open_owned(PositionSide.SHORT)
    engine = ScalpEngine("BTCUSDT")
    engine._client = runtime.client
    engine._position = position
    engine._perf_tracker = None
    command_id = runtime.client.position_command_id(engine.symbol, position.position_id, "scalp:full-close")
    close = Mock(wraps=runtime.client.close_position)
    monkeypatch.setattr(runtime.client, "close_position", close)

    engine._close_position(position, "test close")

    close.assert_called_once_with("BTCUSDT", position.position_id, percentage=1.0, command_id=command_id)
    record = runtime.client.journal.get(runtime.client.account_id, command_id)
    assert record["state"] == "FILLED" and record["result"]["close_confirmed"] is True
    raw = runtime.fake.orders[record["order_id"]]
    assert raw["side"] == "BUY" and raw["positionSide"] == "SHORT"
    assert runtime.client.closure_confirmed(engine.symbol, position.position_id)
    assert engine._position is None


def test_scalp_sync_initializes_dto_position_and_normalizes_symbol(managed_runtime):
    from src.core.scalp_engine import ScalpEngine
    runtime = managed_runtime
    expected = runtime.open_owned()
    engine = ScalpEngine("BTC-USDT")
    engine._client = runtime.client
    engine._analyzer = MagicMock()
    engine._analyzer.get_snapshot.return_value = {"atr": 120}
    engine._tracker = MagicMock()

    engine._sync_position()

    position = engine._position
    assert isinstance(position, Position)
    assert position.position_id == expected.position_id
    assert position.side == expected.side and position.size == expected.size
    assert position.entry_price == expected.entry_price
    assert engine._position_state_available is True
    assert engine._trailing._pos_side == "BUY"
    assert engine._trailing._initial_sl == 49_000
    assert engine._trailing._initial_tp == 52_000
    assert engine._position_open_time > 0
    engine._tracker.sync_position.assert_called_once_with(
        "BTC-USDT", position, exchange_client=engine._client,
    )


def test_scalp_sync_failure_is_fail_closed_and_preserves_known_position(managed_runtime, monkeypatch):
    from src.core.scalp_engine import ScalpEngine
    runtime = managed_runtime
    known = runtime.open_owned()
    engine = ScalpEngine("BTCUSDT")
    engine._position = known
    engine._position_state_available = True
    engine._client = runtime.client
    request = runtime.fake.request

    def unavailable(method, url, **kwargs):
        if "/positions" in url:
            raise ExchangeStateUnavailableError("API unavailable")
        return request(method, url, **kwargs)

    monkeypatch.setattr(runtime.fake, "request", unavailable)
    with pytest.raises(ExchangeStateUnavailableError):
        engine._sync_position()

    assert engine._position is known
    assert engine._position_state_available is False
    assert runtime.client.journal.owned(runtime.client.account_id)[0]["quantity"] == "0.02"


def test_scalp_rest_fallback_is_throttled(managed_runtime, tmp_path, monkeypatch):
    from src.core import scalp_engine
    from src.exchanges.market_data import StreamKey, StreamStore
    from src.exchanges.market_data_storage import publish_market_snapshot, publish_strategy_view
    from test_market_data_layer import Clock, T, raw
    runtime = managed_runtime
    clock = Clock(now=(T + 10 * 60_000) / 1000 + 15)
    monkeypatch.setattr(scalp_engine.time, "time", clock)
    monkeypatch.setattr("src.exchanges.market_data.time.time", clock)
    monkeypatch.setattr("src.exchanges.ws_provider_factory.get_market_snapshot", lambda *args, **kwargs: None)
    engine = scalp_engine.ScalpEngine("BTCUSDT")
    engine._client = runtime.client
    key = StreamKey(runtime.client.settings["exchange"], runtime.client.settings["market_type"], engine.symbol, "1m")
    store = StreamStore(settings={"min_history": 2, "history_limit": 20}, clock=clock)
    snapshot = store.merge(key, [raw(T + i * 60_000, 10 + i) for i in range(11)], status="rest")
    publish_market_snapshot(tmp_path, snapshot)
    publish_strategy_view(tmp_path, snapshot, instance_id=engine._owner_id, closed_only=True, legacy=False)
    expected = snapshot["candles"][:-1][-5:]
    # REST принадлежит owner market-data: worker повторно читает fresh snapshot без HTTP.
    with patch.object(runtime.client, "get_kline_data", wraps=runtime.client.get_kline_data) as private_rest, \
            patch("src.exchanges.public_market_rest.PublicRESTFetcher.__call__", side_effect=AssertionError("Повторный REST worker")) as public_rest:
        first = engine._get_candles(5)
        second = engine._get_candles(5)
        assert first == expected and second == expected
        assert engine._get_candles(5, closed_only=False) == snapshot["candles"][-5:]
        clock.now += runtime.client.settings["market_data"]["max_age_seconds"] + 1
        assert engine._get_candles(5) == []
        assert engine._get_candles(5, closed_only=False) == []
        private_rest.assert_not_called()
        public_rest.assert_not_called()


def test_scalp_limit_timeout_cancels_only_its_order(managed_runtime, monkeypatch):
    from src.core.scalp_engine import ScalpEngine
    runtime = managed_runtime
    client = runtime.client
    oid = client.place_order("BTCUSDT", OrderSide.BUY, 0.02, OrderType.LIMIT, price=49_000,
        position_side=PositionSide.LONG, leverage=7, command_id="scalp-limit-entry")
    runtime.fake.add_order({"symbol": "BTC-USDT", "side": "SELL", "positionSide": "SHORT",
        "type": "LIMIT", "quantity": "0.01", "price": "52000", "clientOrderId": "manual-limit"})
    manual_id = str(len(runtime.fake.orders))
    engine = ScalpEngine("BTCUSDT")
    engine._limit_timeout_sec = 0
    engine._client = client
    cancel = Mock(wraps=client.cancel_order)
    wait = Mock(wraps=client.wait_for_entry)
    bulk = Mock(side_effect=AssertionError("Нельзя отменять весь аккаунт"))
    monkeypatch.setattr(client, "cancel_order", cancel)
    monkeypatch.setattr(client, "wait_for_entry", wait)
    monkeypatch.setattr(client, "cancel_all_orders", bulk)
    assert engine._wait_for_fill(oid) is False
    wait.assert_called_once_with("BTCUSDT", oid, timeout=0)
    assert cancel.call_count == 1
    assert cancel.call_args.args == ("BTCUSDT", oid)
    assert cancel.call_args.kwargs["command_id"]
    assert runtime.fake.orders[oid]["status"] == "CANCELLED"
    assert client.get_order(engine.symbol, oid).status == OrderStatus.CANCELED
    assert runtime.fake.orders[manual_id]["status"] == "NEW"
    bulk.assert_not_called()


def test_scalp_signal_exit_accepts_short_dto():
    from src.core.scalp_signal import ScalpSignalGenerator

    generator = ScalpSignalGenerator()
    result = generator.check_exit(
        {"current_price": 49_000, "rsi": 15, "ema_fast": 1, "ema_med": 2,
         "macd_hist": 0, "volume_ratio": 1},
        dto_position(PositionSide.SHORT),
    )
    assert result["should_close"] is True
    assert "RSI" in result["reason"]


@pytest.mark.parametrize(
    "module_path",
    ["src.core.grid_executor", "src.core.strategies.grid.executor"],
)
def test_grid_inventory_supports_dto_short(module_path, managed_runtime):
    module = __import__(module_path, fromlist=["GridExecutor"])
    owned = managed_runtime.legacy_grid_fill(OrderSide.SELL, quantity=0.25)
    grid = managed_runtime.grid
    assert isinstance(grid, module.GridExecutor)
    assert grid.entries_blocked_reason
    assert owned.side == PositionSide.SHORT
    assert grid.update_inventory() == -0.25
    assert grid.state.short_inventory == 0.25
    assert grid.state.gross_inventory == 0.25


@pytest.mark.parametrize(
    "side,current_price,expected",
    [
        (PositionSide.LONG, 46_000, True),
        (PositionSide.SHORT, 54_000, True),
        (PositionSide.LONG, 54_000, False),
        (PositionSide.SHORT, 46_000, False),
    ],
)
def test_grid_emergency_stop_triggers_only_on_directional_loss(side, current_price, expected, managed_runtime):
    owned = managed_runtime.legacy_grid_fill(OrderSide.BUY if side == PositionSide.LONG else OrderSide.SELL)
    grid = managed_runtime.grid
    grid.emergency_stop_loss_pct = 5
    assert owned.side == side and owned.entry_price == 50_000
    assert grid.entries_blocked_reason and not grid.paused_reason
    assert grid.check_emergency_conditions(current_price) is expected
    before = list(managed_runtime.fake.mutations)
    if expected:
        assert grid.emergency_close() is True
        assert not grid.owned_positions
        # Журнал сохраняет historical ownership с нулевым объёмом после полного close.
        assert all(float(row["quantity"]) == 0 for row in
                   grid.client.journal.owned(grid.client.account_id, owner=grid.instance_id))
        assert all(not rows for rows in grid.client.get_positions().values())
        close, = [level for level in grid.commands.values() if level.reduce_only]
        assert close.position_id == owned.position_id
        assert close.quantity == pytest.approx(owned.size)
        assert close.status == "FILLED"
        assert grid.client.journal.get(grid.client.account_id, close.command_id)["state"] == "FILLED"
    else:
        assert list(grid.owned_positions) == [owned.position_id]
        assert all(not level.reduce_only for level in grid.commands.values())
        assert managed_runtime.fake.mutations == before


def test_grid_position_api_failure_is_fail_closed(managed_runtime, monkeypatch):
    grid = managed_runtime.grid
    monkeypatch.setattr(grid.client.raw_client, "get_positions", Mock(side_effect=ExchangeStateUnavailableError("API unavailable")))
    with pytest.raises(ExchangeStateUnavailableError):
        grid.update_inventory()


def test_grid_rejects_unprotected_limit_entry_through_exchange_contract(managed_runtime, monkeypatch):
    grid = managed_runtime.grid
    place = Mock(wraps=grid.client.place_grid_order)
    monkeypatch.setattr(grid.client, "place_grid_order", place)
    before = list(managed_runtime.fake.calls)
    assert grid._place_grid_order("BUY", 50_000, 50_000) is None
    assert grid._place_grid_order("SELL", 50_000, 50_000) is None
    place.assert_not_called()
    with pytest.raises(UnsupportedCapabilityError, match="initial LIMIT fills не устанавливают биржевой SL/TP"):
        grid.client.place_grid_order(grid.symbol, OrderSide.BUY, 0.002, price=50_000,
            position_side=PositionSide.LONG, leverage=7, gross_limit=grid.inventory_limit,
            command_id="blocked-contract-entry")
    assert managed_runtime.fake.calls == before
    assert not grid.commands and not grid.active_orders
    assert not grid.client.journal.intents(grid.client.account_id)
    saved = json.loads(grid._store.path.read_text())
    assert saved["commands"] == {}
    assert saved["identity"]["account"] == grid.client.account_id
    assert grid.entries_blocked_reason and not grid.paused_reason


def test_grid_does_not_treat_canceled_order_as_fill(managed_runtime):
    runtime = managed_runtime
    filled_id, canceled_id, unknown_id = runtime.preload_legacy_grid_orders([49_000, 48_000, 47_000])
    grid = runtime.grid
    runtime.fake.fill(filled_id)
    runtime.fake.orders[canceled_id]["status"] = "CANCELED"
    filled = grid.check_filled_orders()

    assert [item["order_id"] for item in filled] == [filled_id]
    assert filled_id not in grid.active_orders
    assert canceled_id not in grid.active_orders
    assert unknown_id in grid.active_orders
    assert grid.state.total_filled_buy == 1
    grid.update_inventory()
    owned_quantity = grid.state.owned_long
    assert owned_quantity == pytest.approx(filled[0]["qty"])
    # Missing exact-ID readback не считается новым fill и не уничтожает pending-команду.
    runtime.fake.orders.pop(unknown_id)
    assert grid.check_filled_orders() == []
    assert unknown_id in grid.active_orders
    assert grid.paused_reason
    assert grid.state.owned_long == owned_quantity


def test_grid_cancels_only_managed_orders_and_preserves_failures(managed_runtime, monkeypatch):
    runtime = managed_runtime
    owned_ok, owned_failed = runtime.preload_legacy_grid_orders([49_000, 48_000])
    grid = runtime.grid
    runtime.fake.add_order({"symbol": "BTC-USDT", "side": "SELL", "positionSide": "SHORT",
        "type": "LIMIT", "quantity": "0.01", "price": "52000", "clientOrderId": "manual-grid-order"})
    manual_id = str(len(runtime.fake.orders))
    cancel = grid.client.cancel_order

    def cancel_one(symbol, oid, **kwargs):
        if oid == owned_failed:
            return False
        return cancel(symbol, oid, **kwargs)

    addressed = Mock(side_effect=cancel_one)
    addressed.__signature__ = signature(cancel)
    bulk = Mock(side_effect=AssertionError("Нельзя отменять чужие заявки"))
    monkeypatch.setattr(grid.client, "cancel_order", addressed)
    monkeypatch.setattr(grid.client, "cancel_all_orders", bulk)
    assert grid.cancel_managed_orders() is False
    assert owned_ok not in grid.active_orders
    assert owned_failed in grid.active_orders
    assert grid.active_orders[owned_failed].status == "CANCEL_UNKNOWN"
    assert runtime.fake.orders[owned_ok]["status"] == "CANCELLED"
    assert grid.client.get_order(grid.symbol, owned_ok).status == OrderStatus.CANCELED
    assert runtime.fake.orders[owned_failed]["status"] == "NEW"
    assert runtime.fake.orders[manual_id]["status"] == "NEW"
    assert {call.args[1] for call in addressed.call_args_list} == {owned_ok, owned_failed}
    commands = {level.order_id: level.command_id for level in grid.commands.values()}
    for call in addressed.call_args_list:
        assert call.kwargs["command_id"] == commands[call.args[1]] + ":cancel"
    bulk.assert_not_called()


def test_trade_tracker_enriches_closed_trade_from_order_dto(tmp_path, monkeypatch):
    import src.core.trade_tracker as tracker_module

    monkeypatch.setattr(tracker_module, "HISTORY_FILE", str(tmp_path / "history.json"))
    monkeypatch.setattr(tracker_module, "ACTIVE_TRADES_FILE", str(tmp_path / "active.json"))
    tracker = tracker_module.TradeTracker()
    stored = {
        "symbol": "BTCUSDT", "side": "LONG", "dealId": "position-42",
        "estimated_entry_fee": 1.0, "last_pnl": 0,
    }
    tracker.active_trades["BTCUSDT"] = stored
    client = MagicMock()
    client.get_recent_orders.return_value = [dto_order()]

    tracker._handle_closed_trade("BTCUSDT", stored, client)

    history = tracker._load_json(str(tmp_path / "history.json"))
    assert history[0]["close_order_id"] == "order-1"
    assert history[0]["close_price"] == 50_900
    assert history[0]["realized_pnl"] == 18
    assert history[0]["net_pnl"] == pytest.approx(15.5)


def test_monitor_formats_dto_position_without_attribute_errors():
    from src.core.monitor import _format_pnl_with_fees

    result = _format_pnl_with_fees(20.0, dto_position())
    assert "PnL: 20.00" in result
    assert "fee:" in result
