"""Поведенческие тесты единого контракта позиций в торговых runtime-путях."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.core.signals.utils import OrderAdapter, PositionAdapter
from src.exchanges.dto.models import (
    Order, OrderSide, OrderStatus, OrderType, Position, PositionSide,
)
from src.exchanges.errors import ExchangeStateUnavailableError


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


def test_scalp_close_uses_dto_position_id_and_real_side():
    from src.core.scalp_engine import ScalpEngine

    engine = ScalpEngine("BTCUSDT")
    engine._client = MagicMock()
    engine._client.close_position.return_value = True
    engine._perf_tracker = None

    engine._close_position(dto_position(PositionSide.SHORT), "test close")

    engine._client.close_position.assert_called_once_with("BTCUSDT", "position-42", percentage=1.0)
    assert engine._position is None


def test_scalp_sync_initializes_dto_position_and_normalizes_symbol():
    from src.core.scalp_engine import ScalpEngine

    position = dto_position()
    engine = ScalpEngine("BTC-USDT")
    engine._client = MagicMock()
    engine._client.get_positions.return_value = {"BTC_USDT": [position]}
    engine._analyzer = MagicMock()
    engine._analyzer.get_snapshot.return_value = {"atr": 120}
    engine._tracker = MagicMock()

    engine._sync_position()

    assert engine._position is position
    assert engine._position_state_available is True
    assert engine._trailing._pos_side == "BUY"
    engine._tracker.sync_position.assert_called_once_with(
        "BTC-USDT", position, exchange_client=engine._client,
    )


def test_scalp_sync_failure_is_fail_closed_and_preserves_known_position():
    from src.core.scalp_engine import ScalpEngine

    known = dto_position()
    engine = ScalpEngine("BTCUSDT")
    engine._position = known
    engine._position_state_available = True
    engine._client = MagicMock()
    engine._client.get_positions.side_effect = RuntimeError("API unavailable")

    engine._sync_position()

    assert engine._position is known
    assert engine._position_state_available is False


def test_scalp_rest_fallback_is_throttled():
    from src.core.scalp_engine import ScalpEngine

    candles = [{"closePrice": index} for index in range(10)]
    engine = ScalpEngine("BTCUSDT")
    engine._client = MagicMock()
    engine._client.get_kline_data.return_value = candles

    with patch("src.exchanges.ws_provider_factory.is_cache_ready", return_value=False):
        first = engine._get_candles(5)
        second = engine._get_candles(5)

    assert first == candles[-5:]
    assert second == candles[-5:]
    engine._client.get_kline_data.assert_called_once()


def test_scalp_limit_timeout_cancels_only_its_order(monkeypatch):
    from src.core.scalp_engine import ScalpEngine

    engine = ScalpEngine("BTCUSDT")
    engine._limit_timeout_sec = 0
    engine._client = MagicMock()
    assert engine._wait_for_fill("owned-order") is False
    engine._client.cancel_order.assert_called_once_with("BTCUSDT", "owned-order")
    engine._client.cancel_all_orders.assert_not_called()


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
def test_grid_inventory_supports_dto_short(module_path):
    module = __import__(module_path, fromlist=["GridExecutor"])
    grid = module.GridExecutor("BTCUSDT", {})
    grid.client = MagicMock()
    grid.client.get_positions.return_value = {"BTCUSDT": [dto_position(PositionSide.SHORT, size=0.25)]}
    assert grid.update_inventory() == -0.25


@pytest.mark.parametrize(
    "side,current_price,expected",
    [
        (PositionSide.LONG, 46_000, True),
        (PositionSide.SHORT, 54_000, True),
        (PositionSide.LONG, 54_000, False),
        (PositionSide.SHORT, 46_000, False),
    ],
)
def test_grid_emergency_stop_triggers_only_on_directional_loss(side, current_price, expected):
    from src.core.grid_executor import GridExecutor

    grid = GridExecutor("BTCUSDT", {"emergency_stop_loss_pct": 5})
    grid.client = MagicMock()
    grid.client.get_positions.return_value = {"BTCUSDT": [dto_position(side)]}
    assert grid.check_emergency_conditions(current_price) is expected


def test_grid_position_api_failure_is_fail_closed():
    from src.core.grid_executor import GridExecutor

    grid = GridExecutor("BTCUSDT", {})
    grid.client = MagicMock()
    grid.client.get_positions.side_effect = RuntimeError("API unavailable")
    with pytest.raises(ExchangeStateUnavailableError):
        grid.update_inventory()


def test_grid_places_limit_order_through_exchange_contract():
    from src.core.grid_executor import GridExecutor

    grid = GridExecutor("BTCUSDT", {"order_size_usdt": 100})
    grid.client = MagicMock()
    grid.client.place_order.return_value = "grid-1"
    assert grid._place_grid_order("BUY", 50_000, 50_000) == "grid-1"
    grid.client.place_order.assert_called_once_with(
        symbol="BTCUSDT", side=OrderSide.BUY, price=50_000,
        quantity=pytest.approx(0.002), order_type=OrderType.LIMIT,
        position_side=PositionSide.LONG,
    )


def test_grid_does_not_treat_canceled_order_as_fill():
    from src.core.grid_executor import GridExecutor, GridLevel

    grid = GridExecutor("BTCUSDT", {})
    grid.client = MagicMock()
    grid.active_orders = {
        "filled": GridLevel(49_000, "BUY", 0.01, "filled"),
        "canceled": GridLevel(48_000, "BUY", 0.01, "canceled"),
        "unknown": GridLevel(47_000, "BUY", 0.01, "unknown"),
    }
    grid.client.get_open_orders.return_value = []
    grid.client.get_recent_orders.return_value = [
        dto_order("filled", OrderSide.BUY, OrderStatus.FILLED),
        dto_order("canceled", OrderSide.BUY, OrderStatus.CANCELED),
    ]

    filled = grid.check_filled_orders()

    assert [item["order_id"] for item in filled] == ["filled"]
    assert "filled" not in grid.active_orders
    assert "canceled" not in grid.active_orders
    assert "unknown" in grid.active_orders


def test_grid_cancels_only_managed_orders_and_preserves_failures():
    from src.core.grid_executor import GridExecutor, GridLevel

    grid = GridExecutor("BTCUSDT", {})
    grid.client = MagicMock()
    grid.active_orders = {
        "owned-ok": GridLevel(49_000, "BUY", 0.01, "owned-ok"),
        "owned-failed": GridLevel(48_000, "BUY", 0.01, "owned-failed"),
    }
    grid.client.cancel_order.side_effect = [True, False]

    assert grid.cancel_managed_orders() is False
    assert "owned-ok" not in grid.active_orders
    assert "owned-failed" in grid.active_orders
    grid.client.cancel_all_orders.assert_not_called()


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
