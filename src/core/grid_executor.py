"""GRID: доказанное владение, durable-команды и ограничение полной экспозиции."""

import hashlib
import inspect
import json
import math
import os
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.core.signals.utils import OrderAdapter, PositionAdapter, calculate_pnl_pct
from src.exchanges.dto.models import OrderSide, OrderType, PositionSide
from src.exchanges.errors import ExchangeStateUnavailableError, UnsupportedCapabilityError
from src.exchanges.market_data import canonical_symbol
from src.trading_settings import get_trading_settings, validate_trading_settings
from src.utils.helpers import get_filename
from src.utils.logger import error, info, warning
from .strategies.grid.state import GridStateStore
from .strategies.grid.native_triggers import NativeGridTriggerMixin
from .strategies.grid import safety as grid_safety


def _value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _client_id(order):
    raw = _value(order, "raw_data", order if isinstance(order, dict) else {}) or {}
    return str(raw.get("clientOrderId", raw.get("clientOrderID", raw.get("externalOid", ""))) or "")


def get_grid_client(settings, runtime, instance_id):
    """Фабрика и публичный attach: контекст и стабильный account, без raw мутаций."""
    from src.exchanges.exchange_factory import get_trading_client
    from src.utils.log_context import set_log_context

    account_id = runtime.get("EXECUTION_ACCOUNT_ID") or f"default:{settings['exchange']}:{settings['execution_mode']}"
    set_log_context(service="bot", exchange=settings["exchange"], market_type=settings["market_type"],
                    instance_id=instance_id, strategy="GRID", category="strategy")
    parameters = inspect.signature(get_trading_client).parameters
    candidates = {"settings": settings, "owner_id": instance_id, "account_id": account_id,
                  "runtime_config": runtime}
    client = get_trading_client(**{key: value for key, value in candidates.items() if key in parameters})
    if settings["execution_mode"] == "read_only":
        return client
    if settings["market_type"] == "spot" and not callable(getattr(client, "validate_execution_context", None)):
        raise UnsupportedCapabilityError("Factory GRID Spot должна вернуть owned-only SpotTradingAdapter с supervisor")
    from src.core.execution_coordinator import attach_execution_safety
    return attach_execution_safety(client, settings=settings, owner_id=instance_id, account_id=account_id)


@dataclass
class GridLevel:
    price: float
    side: str
    quantity: float
    order_id: Optional[str] = None
    filled: bool = False
    client_order_id: str = ""
    position_side: str = "LONG"
    reduce_only: bool = False
    position_id: str = ""
    accounted_quantity: float = 0.0
    status: str = "PREPARED"
    accounted_pnl: float = 0.0
    accounted_fee: float = 0.0
    command_id: str = ""


@dataclass
class GridState:
    center_price: float = 0.0
    reference_price: float = 0.0
    reference_source: str = ""
    spacing_pct: float = 0.3
    inventory: float = 0.0
    long_inventory: float = 0.0
    short_inventory: float = 0.0
    gross_inventory: float = 0.0
    owned_long: float = 0.0
    owned_short: float = 0.0
    total_filled_buy: int = 0
    total_filled_sell: int = 0
    total_buy_value: float = 0.0
    total_sell_value: float = 0.0
    total_fees: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    last_sync_time: float = 0.0
    native_levels: dict = field(default_factory=dict)
    native_previous_price: float = 0.0


class GridExecutor(NativeGridTriggerMixin):
    def __init__(self, symbol: str, config: dict, runtime_config=None, *, client=None, state_dir=None):
        runtime = runtime_config or {}
        selected = runtime.get("execution") or config.get("execution") or get_trading_settings()
        self.settings = validate_trading_settings(selected)
        self.symbol = symbol
        self.instance_id = runtime.get("STRATEGY_INSTANCE_ID", f"{canonical_symbol(symbol).lower()}_grid")
        self.client = client
        if self.client is None:
            from src.utils.log_context import set_log_context
            set_log_context(symbol=symbol)
            self.client = get_grid_client(self.settings, runtime, self.instance_id)
        self.read_only = self.settings["execution_mode"] == "read_only"
        self.grid_mode = self.settings["grid_mode"]
        self.entry_execution = config.get("entry_execution", "gtc_limit")
        if self.entry_execution not in {"gtc_limit", "native_market"}:
            raise ValueError("GRID: неизвестная модель entry_execution")
        self.native_market = self.entry_execution == "native_market"
        self.num_levels = int(config.get("grid_levels", 5))
        self.spacing_pct = float(config.get("grid_spacing_pct", 0.3))
        self.order_size_usdt = float(config.get("order_size_usdt", 10))
        self.inventory_limit = float(config.get("inventory_limit", 100))
        self.emergency_stop_loss_pct = float(config.get("emergency_stop_loss_pct", 5))
        preset = runtime.get("STYLE_PRESETS", {}).get("GRID", {})
        self.leverage = runtime.get("LEVERAGE", config.get("preset", {}).get("leverage", preset.get("leverage", 5)))
        if self.settings["market_type"] == "spot":
            self.leverage = 1
        limits = runtime.get("POSITION_LIMITS", {})
        self.price_precision = int(limits.get("price_precision", 4))
        self.quantity_precision = int(limits.get("quantity_precision", 4))
        self.fee_rate = float(runtime.get("TRADING_FEE_TAKER", 0))
        if (self.num_levels < 1 or any(not math.isfinite(v) or v <= 0 for v in
                (self.spacing_pct, self.order_size_usdt, self.inventory_limit, self.emergency_stop_loss_pct))
                or type(self.leverage) is not int or self.leverage < 1):
            raise ValueError("Некорректные параметры GRID")
        self.state = GridState(spacing_pct=self.spacing_pct)
        self.active_orders: Dict[str, GridLevel] = {}
        self.known_order_ids = set()
        self.commands = {}
        self.owned_positions = {}
        self.paused_reason = ""
        self._positions = []
        self._store = None
        self._restored = set()
        self._recovery_attempts = 0
        self._pause_kind = "safety"
        self._rules = None
        self._recovery_limit = self.settings["protection"]["recovery_attempts"]
        account_id = (runtime.get("EXECUTION_ACCOUNT_ID") or getattr(self.client, "execution_account_id", None)
                      or getattr(self.client, "account_id", None) or getattr(self.client, "account", None))
        self.identity = {"account": account_id, "exchange": self.settings["exchange"],
                         "market": self.settings["market_type"], "mode": self.settings["execution_mode"],
                         "symbol": canonical_symbol(symbol), "instance": self.instance_id}
        self.signature = {"grid_mode": self.grid_mode, "position_mode": self.settings["position_mode"],
                          "margin_mode": self.settings["margin_mode"], "leverage": self.leverage}
        if self.native_market:
            self.signature["entry_execution"] = self.entry_execution
        if not self.read_only:
            owner_id = getattr(self.client, "owner_id", self.instance_id)
            if not isinstance(account_id, str) or not account_id:
                self._pause("Не задан durable account ID; торговля GRID запрещена")
            else:
                digest = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()
                directory = Path(state_dir or runtime.get("DATA_DIR", os.getenv("SERVO_DATA_DIR", "data")))
                path = directory / "grid" / f"{get_filename(canonical_symbol(symbol))}_{digest}.json"
                self._store = GridStateStore(path)
                saved = self._store.load()
                if saved:
                    if saved["identity"] != self.identity:
                        raise ExchangeStateUnavailableError("GRID: identity durable-состояния не совпадает")
                    self.state = GridState(**saved["state"])
                    self.commands = {key: GridLevel(**value) for key, value in saved["commands"].items()}
                    self.owned_positions = saved["owned_positions"]
                    self.paused_reason = saved.get("paused_reason", "")
                    self._pause_kind = saved.get("pause_kind", "safety")
                    self._recovery_attempts = saved.get("recovery_attempts", 0)
                    unsettled = any(v.status not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"} for v in self.commands.values())
                    if saved["signature"] != self.signature and (unsettled or self.owned_positions or
                                                self.native_market and self.commands and saved["signature"].get("entry_execution") != self.entry_execution):
                        self._pause("Смена режима/плеча требует остановки и сверки старого GRID")
                    self._refresh_active()
                if owner_id != self.instance_id:
                    self._pause("Coordinator закреплён за другим instance; требуется правильный контекст factory")
                self._persist()
        if self.entries_blocked_reason:
            warning(f"⚠️ [GRID] {symbol}: {self.entries_blocked_reason}")
        info(f"[GRID] Инициализирован {symbol}: режим={self.grid_mode}, плечо={self.leverage}")

    @property
    def entries_blocked_reason(self):
        if self.read_only:
            return ""
        guard = grid_safety.native_grid_entry_block_reason if self.native_market else grid_safety.grid_entry_block_reason
        return guard(self.settings)

    def _pause(self, reason, *, recoverable=False):
        if recoverable and self.paused_reason and self._pause_kind == "safety":
            self._persist()
            return
        self._pause_kind = "recovery" if recoverable else "safety"
        self.paused_reason = reason
        warning(f"⏸️ [GRID] {self.symbol}: {reason}")
        self._persist()

    def _persist(self):
        if self._store is not None:
            self._store.save({"version": 1, "identity": self.identity, "signature": self.signature,
                              "state": asdict(self.state), "commands": {k: asdict(v) for k, v in self.commands.items()},
                              "owned_positions": self.owned_positions, "paused_reason": self.paused_reason,
                              "pause_kind": self._pause_kind, "recovery_attempts": self._recovery_attempts})

    def close(self):
        if self._store is not None:
            self._store.close()

    def _refresh_active(self):
        self.active_orders = {str(v.order_id): v for v in self.commands.values() if v.order_id and
                              v.status not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}}
        self.known_order_ids = {str(v.order_id) for v in self.commands.values() if v.order_id}

    def calculate_grid_prices(self, center_price, spacing_mult=1.0, inventory_offset=0.0):
        if not math.isfinite(center_price) or center_price <= 0:
            raise ValueError("GRID: цена должна быть положительной")
        center = center_price * (1 + inventory_offset / 100)
        step = center * self.spacing_pct / 100 * spacing_mult
        return {side: [self._round_price(center + sign * step * n, side.upper())
                       for n in range(1, self.num_levels + 1)]
                for side, sign in (("buy", -1), ("sell", 1))}

    def _instrument_rules(self):
        if self._rules is None:
            reader = getattr(self.client, "get_instrument_rules", None)
            if callable(reader):
                self._rules = reader(self.symbol)
        return self._rules

    def _round_price(self, price, side):
        rules = self._instrument_rules()
        if rules is None:
            return round(price, self.price_precision)
        step = Decimal(str(rules.price_step))
        rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
        return float((Decimal(str(price)) / step).to_integral_value(rounding=rounding) * step)

    def calculate_inventory_offset(self):
        return -self.state.inventory / self.inventory_limit * self.spacing_pct

    def _symbol_positions(self):
        invalidate = getattr(self.client, "invalidate_cache", None)
        if callable(invalidate):
            invalidate("positions")
        reader = self.client.get_positions
        positions = reader(force_refresh=True) if "force_refresh" in inspect.signature(reader).parameters else reader()
        return [p for symbol, rows in positions.items() if canonical_symbol(symbol) == canonical_symbol(self.symbol) for p in rows]

    def update_inventory(self):
        if self.read_only:
            return self.state.inventory
        try:
            self._positions = self._symbol_positions()
            adapters = [PositionAdapter(p) for p in self._positions]
            if any(not math.isfinite(p.size) or p.size < 0 for p in adapters):
                raise ExchangeStateUnavailableError("GRID: некорректный объём позиции")
            self.state.long_inventory = sum(p.size for p in adapters if p.is_long)
            self.state.short_inventory = sum(p.size for p in adapters if not p.is_long)
            self.state.inventory = self.state.long_inventory - self.state.short_inventory
            self.state.gross_inventory = self.state.long_inventory + self.state.short_inventory
            self.state.owned_long = sum(p["quantity"] for p in self.owned_positions.values() if p["side"] == "LONG")
            self.state.owned_short = sum(p["quantity"] for p in self.owned_positions.values() if p["side"] == "SHORT")
            for pid, owned in self.owned_positions.items():
                target = next((p for p in adapters if p.position_id == pid), None)
                if (target is None or target.size + 1e-12 < owned["quantity"]
                        or not math.isclose(target.size, owned.get("observed_quantity", owned["quantity"]), rel_tol=1e-9)
                        or ("LONG" if target.is_long else "SHORT") != owned["side"]):
                    raise ExchangeStateUnavailableError("GRID: собственная позиция изменена вне журнала")
                register = getattr(self.client, "register_owned_position", None)
                if callable(register) and not getattr(self.client, "execution_safety_enabled", False):
                    register(self.symbol, pid, owned["quantity"], position_side=PositionSide(owned["side"]))
            self._persist()
            return self.state.inventory
        except Exception as exc:
            error(f"❌ [GRID] Не удалось получить inventory: {exc}")
            raise ExchangeStateUnavailableError("Не удалось обновить GRID inventory") from exc

    def _restore_order(self, level):
        if level.order_id in self._restored:
            return
        register = getattr(self.client, "register_owned_order", None)
        restore = getattr(self.client, "restore_order_ownership", None)
        if callable(register):
            register(self.symbol, level.order_id, client_order_id=level.client_order_id)
        elif callable(restore):
            restore(self.symbol, level.order_id)
        elif self.settings["market_type"] == "spot":
            # Адаптер проверяет точный ID в собственном durable ledger при get_order.
            query = getattr(self.client, "get_order", None)
            if not callable(query) or _client_id(query(self.symbol, level.order_id)) != level.client_order_id:
                raise UnsupportedCapabilityError("GRID Spot: ownership ордера не подтверждён ledger")
        else:
            raise UnsupportedCapabilityError("GRID: нет API восстановления ownership ордера")
        self._restored.add(level.order_id)

    def _lookup(self, level, orders):
        if level.order_id:
            order = next((o for o in orders if OrderAdapter(o).order_id == level.order_id), None)
        else:
            order = next((o for o in orders if _client_id(o) == level.client_order_id), None)
        query = getattr(self.client, "get_order", None)
        if order is None and callable(query):
            order = (query(self.symbol, level.order_id) if level.order_id else
                     query(self.symbol, client_order_id=level.client_order_id))
        if order is None:
            raise ExchangeStateUnavailableError("GRID: состояние durable-команды неизвестно")
        adapter = OrderAdapter(order)
        if (not adapter.order_id or (level.order_id and adapter.order_id != level.order_id)
                or _client_id(order) != level.client_order_id
                or canonical_symbol(_value(order, "symbol", self.symbol)) != canonical_symbol(self.symbol)
                or adapter.side != level.side
                or Decimal(str(adapter.price)) != Decimal(str(level.price))
                or not math.isclose(float(_value(order, "quantity", 0)), level.quantity, rel_tol=1e-10, abs_tol=1e-12)
                or (level.price > 0 and str(getattr(_value(order, "order_type"), "value", _value(order, "order_type", "LIMIT"))) != "LIMIT")):
            raise ExchangeStateUnavailableError("GRID: ответ не совпадает с durable-командой")
        return order

    def reconcile(self, open_orders=None, *, for_cancel=False):
        """Неизвестный результат не переотправляется; lookup ограничен числом попыток."""
        if self.native_market:
            return self._native_reconcile()
        if self.read_only or not any(v.status not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
                                     for v in self.commands.values()):
            return []
        if self.paused_reason and self._pause_kind == "safety" and not for_cancel:
            return []
        if self.paused_reason and self._recovery_attempts >= self._recovery_limit and not for_cancel:
            return []
        try:
            self._recovery_attempts += 1
            self._persist()
            grid_orders = {}
            if getattr(self.client, "execution_safety_enabled", False):
                reconcile_grid = getattr(self.client, "reconcile_grid", None)
                if callable(reconcile_grid):
                    grid_orders = reconcile_grid(self.symbol)
                else:
                    self.client.recover()
            opened = self.client.get_open_orders(self.symbol) if open_orders is None else open_orders
            recent = self.client.get_recent_orders(self.symbol, limit=100)
            filled = []
            resolved = []
            for level in self.commands.values():
                if level.status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                    continue
                verified = grid_orders.get(level.command_id)
                order = self._lookup(level, [verified] if verified is not None else list(recent) + list(opened))
                qty = float(_value(order, "filled_quantity", 0))
                if not math.isfinite(qty) or not 0 <= qty <= level.quantity or qty < level.accounted_quantity:
                    raise ExchangeStateUnavailableError("GRID: некорректный накопленный fill")
                resolved.append((level, order, qty))
            self._fill_expected = {side: sum(p["quantity"] for p in self.owned_positions.values() if p["side"] == side)
                                   + sum((qty - level.accounted_quantity) * (-1 if level.reduce_only else 1)
                                         for level, order, qty in resolved if level.position_side == side)
                                   for side in ("LONG", "SHORT")}
            self._expected_positions = {pid: p.get("observed_quantity", p["quantity"])
                                        for pid, p in self.owned_positions.items()}
            for level, order, qty in resolved:
                raw = _value(order, "raw_data", {}) or {}
                pid = level.position_id or str(raw.get("positionId", "") or "")
                if pid:
                    self._expected_positions[pid] = self._expected_positions.get(pid, 0) + (qty - level.accounted_quantity) * (-1 if level.reduce_only else 1)
            for level, order, qty in resolved:
                level.order_id = OrderAdapter(order).order_id
                self._restore_order(level)
                delta = qty - level.accounted_quantity
                if delta > 0:
                    self._attribute_fill(level, order, delta)
                    adapter = OrderAdapter(order)
                    self.record_fill(level.side, adapter.average_price, delta,
                                     realized_pnl=adapter.realized_pnl - level.accounted_pnl,
                                     commission=adapter.commission - level.accounted_fee)
                    level.accounted_quantity = qty
                    level.accounted_pnl = adapter.realized_pnl
                    level.accounted_fee = adapter.commission
                    filled.append({"side": level.side, "price": level.price, "qty": delta, "order_id": level.order_id})
                status = OrderAdapter(order).status
                if level.status == "CANCEL_UNKNOWN" and status in {"NEW", "PARTIALLY_FILLED"}:
                    raise ExchangeStateUnavailableError("GRID: отмена ещё не подтверждена; повтор запрещён")
                level.status = status
                if level.status == "FILLED" and qty != level.quantity:
                    raise ExchangeStateUnavailableError("GRID: FILLED без полного объёма")
                if level.status not in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                    raise ExchangeStateUnavailableError("GRID: неизвестный статус ордера")
            self._refresh_active()
            self._recovery_attempts = 0
            if self._pause_kind == "recovery":
                self.paused_reason = ""
            self._persist()
            return filled
        except Exception as exc:
            self._pause(f"Требуется сверка GRID; автоматическая переотправка запрещена: {exc}", recoverable=True)
            self._refresh_active()
            return []

    def _attribute_fill(self, level, order, delta):
        positions = self._symbol_positions()
        candidates = [PositionAdapter(p) for p in positions if
                      ("LONG" if PositionAdapter(p).is_long else "SHORT") == level.position_side]
        raw = _value(order, "raw_data", {}) or {}
        pid = level.position_id or str(raw.get("positionId", "") or "")
        if self.settings["market_type"] == "spot":
            # В Spot ledger ID LONG-лота равен точному clientOrderId BUY, не цене.
            pid = level.position_id if level.reduce_only else level.client_order_id
            target = next((p for p in candidates if p.position_id == pid), None)
            if level.reduce_only and pid not in self.owned_positions:
                raise ExchangeStateUnavailableError("GRID Spot: SELL относится к чужому лоту")
            if target is None:
                if not level.reduce_only:
                    raise ExchangeStateUnavailableError("GRID Spot: BUY lot ещё не подтверждён")
                self.owned_positions.pop(pid, None)
            else:
                # Ledger учитывает в том числе комиссию в base asset; gross fill не равен net lot.
                self.owned_positions[pid] = {"quantity": target.size, "side": "LONG", "observed_quantity": target.size}
            return
        if level.reduce_only:
            owned = self.owned_positions.get(pid)
            if owned is None or delta > owned["quantity"] + 1e-12:
                raise ExchangeStateUnavailableError("GRID: закрытие превышает доказанный собственный объём")
            target = next((p for p in candidates if p.position_id == pid), None)
            expected = self._expected_positions[pid]
            if not math.isclose(target.size if target else 0, expected, rel_tol=1e-9, abs_tol=1e-12):
                raise ExchangeStateUnavailableError("GRID: закрытие пересечено изменением позиции вне команды")
            owned["quantity"] = max(0.0, owned["quantity"] - delta)
            if target is not None:
                owned["observed_quantity"] = target.size
            if owned["quantity"] == 0:
                del self.owned_positions[pid]
            return
        if not pid:
            # Без positionId допустима лишь единственная позиция с ровно доказанным объёмом.
            expected = self._fill_expected[level.position_side]
            if len(candidates) != 1 or not math.isclose(candidates[0].size, expected, rel_tol=1e-9):
                raise ExchangeStateUnavailableError("GRID: fill нельзя однозначно связать с позицией")
            pid = candidates[0].position_id
        target = next((p for p in candidates if p.position_id == pid), None)
        previous = self.owned_positions.get(pid, {"quantity": 0, "side": level.position_side})
        quantity = previous["quantity"] + delta
        expected = self._expected_positions.get(pid, self._fill_expected[level.position_side])
        if (not pid or target is None or target.size + 1e-12 < quantity or previous["side"] != level.position_side
                or not math.isclose(target.size, expected, rel_tol=1e-9, abs_tol=1e-12)):
            raise ExchangeStateUnavailableError("GRID: позиция fill не подтверждена")
        register = getattr(self.client, "register_owned_position", None)
        if callable(register) and not getattr(self.client, "execution_safety_enabled", False):
            register(self.symbol, pid, quantity, position_side=PositionSide(level.position_side))
        self.owned_positions[pid] = {"quantity": quantity, "side": level.position_side,
                                     "observed_quantity": target.size}

    def sync_orders(self, target_prices, current_price):
        if self.native_market:
            return self.native_cycle(current_price, self.state.reference_price)
        self.state.center_price = current_price
        if self.read_only:
            return True
        if self.paused_reason:
            self.reconcile()
            return False
        try:
            open_orders = self.client.get_open_orders(self.symbol)
            self.reconcile(open_orders)
            if self.paused_reason:
                return False
            self.update_inventory()
            target_prices = {side: list(dict.fromkeys(self._round_price(p, side.upper()) for p in target_prices.get(side, [])))
                             for side in ("buy", "sell")}
            if any(not math.isfinite(p) or p <= 0 for prices in target_prices.values() for p in prices):
                raise ValueError("GRID: некорректный целевой уровень")
            targets = {(p, side.upper()) for side, prices in target_prices.items() for p in prices}
            canceled = False
            for level in list(self.active_orders.values()):
                if (level.price, level.side) not in targets:
                    if not self._cancel(level):
                        return False
                    canceled = True
            if canceled:
                self.update_inventory()
            # Чужой ордер на той же цене не является нашим уровнем и не усыновляется.
            existing = {(v.price, v.side) for v in self.active_orders.values()}
            pending = sum(max(0, v.quantity - v.accounted_quantity) for v in self.active_orders.values() if not v.reduce_only)
            for order in open_orders:
                if OrderAdapter(order).order_id in self.known_order_ids:
                    continue
                remaining = float(_value(order, "quantity", 0)) - float(_value(order, "filled_quantity", 0))
                if not math.isfinite(remaining) or remaining < 0:
                    raise ExchangeStateUnavailableError("GRID: объём чужой pending заявки неизвестен")
                pending += remaining
            entries_blocked = False
            for side in ("BUY", "SELL"):
                for price in target_prices.get(side.lower(), []):
                    if (price, side) in existing:
                        continue
                    quantity = self._calculate_order_quantity(price)
                    reducing = self.grid_mode == "inventory" and side == "SELL"
                    if not reducing and self.entries_blocked_reason:
                        # Это запрет входов, не safety-пауза сверки и локального emergency.
                        entries_blocked = True
                        continue
                    if not reducing and self.state.gross_inventory + pending + quantity > self.inventory_limit:
                        continue
                    if not reducing and self._has_unowned_conflict(side, open_orders):
                        continue
                    if self.settings["market_type"] == "spot" and self.active_orders:
                        # Spot adapter допускает только одну незавершённую команду на символ.
                        continue
                    blocked = getattr(self.client, "is_grid_symbol_blocked", None)
                    if not callable(blocked):
                        blocked = getattr(self.client, "is_symbol_blocked", None)
                    if callable(blocked) and blocked(self.symbol):
                        # Только coordinator вправе отличать наши ACK от UNKNOWN и чужих pending.
                        continue
                    oid = self._place_grid_order(side, price, current_price)
                    if self.paused_reason:
                        return False
                    if oid:
                        existing.add((price, side))
                        if not reducing:
                            pending += self.active_orders[oid].quantity
            self.state.last_sync_time = time.time()
            self._persist()
            return not entries_blocked
        except Exception as exc:
            self._pause(f"Синхронизация GRID остановлена: {exc}")
            return False

    def _has_unowned_conflict(self, side, orders):
        direction = "LONG" if side == "BUY" else "SHORT"
        for p in self._positions:
            adapter = PositionAdapter(p)
            if self.settings["position_mode"] == "hedge" and ("LONG" if adapter.is_long else "SHORT") != direction:
                continue
            owned = self.owned_positions.get(adapter.position_id)
            if not owned or not math.isclose(adapter.size, owned["quantity"], rel_tol=1e-9):
                return True
        for order in orders:
            if OrderAdapter(order).order_id in self.known_order_ids:
                continue
            side = _value(order, "position_side", _value(order, "positionSide", ""))
            side = str(getattr(side, "value", side) or "")
            if self.settings["position_mode"] == "one_way" or side not in {"LONG", "SHORT"} or side == direction:
                return True
        return False

    def _calculate_order_quantity(self, price):
        if not math.isfinite(price) or price <= 0:
            return 0.0
        return self._round_quantity(Decimal(str(self.order_size_usdt)) / Decimal(str(price)))

    def _round_quantity(self, quantity):
        rules = self._instrument_rules()
        step = (Decimal(str(rules.quantity_step)) * Decimal(str(rules.contract_size)) if rules is not None
                else Decimal(1).scaleb(-self.quantity_precision))
        return float((Decimal(str(quantity)) / step).to_integral_value(rounding=ROUND_FLOOR) * step)

    def _place_grid_order(self, side, price, current_price):
        if self.read_only or self.paused_reason or self._store is None:
            return None
        reducing = self.grid_mode == "inventory" and side == "SELL"
        if not reducing and self.entries_blocked_reason:
            warning(f"⚠️ [GRID] {self.symbol}: {self.entries_blocked_reason}")
            return None
        quantity = self._calculate_order_quantity(price)
        direction = "LONG" if reducing or side == "BUY" else "SHORT"
        pid = ""
        if reducing:
            reserved = {}
            for level in self.active_orders.values():
                if level.reduce_only:
                    reserved[level.position_id] = (reserved.get(level.position_id, Decimal(0))
                                                   + Decimal(str(level.quantity)) - Decimal(str(level.accounted_quantity)))
            candidate = next(((key, p) for key, p in self.owned_positions.items()
                              if p["side"] == "LONG" and Decimal(str(p["quantity"])) > reserved.get(key, Decimal(0))), None)
            if candidate is None:
                return None
            pid, owned = candidate
            available = Decimal(str(owned["quantity"])) - reserved.get(pid, Decimal(0))
            quantity = self._round_quantity(min(Decimal(str(quantity)), available))
            sellable = getattr(self.client, "get_sellable_quantity", None)
            if self.settings["market_type"] == "spot" and quantity > 0 and callable(sellable):
                # Ledger резервирует возможную base-комиссию, не используя ручные монеты.
                safe_quantity = Decimal(str(sellable(self.symbol, position_id=pid, quantity=quantity)))
                if not safe_quantity.is_finite() or not 0 <= safe_quantity <= Decimal(str(quantity)):
                    raise ExchangeStateUnavailableError("GRID Spot: некорректный доказанный sellable объём")
                quantity = self._round_quantity(safe_quantity)
        if quantity <= 0:
            return None
        command_id = "seg" + uuid.uuid4().hex[:28]
        cid = self._wire_client_id(command_id)
        level = GridLevel(price, side, quantity, client_order_id=cid, command_id=command_id, position_side=direction,
                          reduce_only=reducing, position_id=pid)
        self.commands[cid] = level
        self._persist()
        try:
            kwargs = dict(symbol=self.symbol, side=OrderSide(side), quantity=quantity, price=price,
                          order_type=OrderType.LIMIT, position_side=(PositionSide.BOTH if
                          self.settings["position_mode"] == "one_way" else PositionSide(direction)),
                          leverage=self.leverage, client_order_id=cid)
            if self.settings["market_type"] == "spot":
                method = getattr(self.client, "submit_order", None)
                if not callable(method):
                    raise UnsupportedCapabilityError("GRID Spot требует owned-only trading adapter")
                kwargs["position_side"] = PositionSide.LONG
                if reducing:
                    kwargs["position_id"] = pid
                else:
                    kwargs["sl"] = price * (1 - self.emergency_stop_loss_pct / 100)
            elif reducing:
                method = getattr(self.client, "place_reduce_order", None)
                if not callable(method):
                    raise UnsupportedCapabilityError("GRID inventory требует безопасный place_reduce_order для LIMIT")
                kwargs.update(position_id=pid, reduce_only=True)
            else:
                method = getattr(self.client, "place_grid_order", None)
                if not callable(method):
                    raise UnsupportedCapabilityError("GRID требует managed place_grid_order; raw entry запрещён")
                kwargs["gross_limit"] = self.inventory_limit
            if getattr(self.client, "execution_safety_enabled", False):
                kwargs["command_id"] = command_id
            result = method(**kwargs)
            oid = _value(result, "order_id", result)
            if not isinstance(oid, (str, int)) or isinstance(oid, bool) or not str(oid):
                raise ExchangeStateUnavailableError("GRID: ACK не содержит order ID")
            level.order_id = str(oid)
            level.status = "NEW"
            self._restored.add(level.order_id)
            self._refresh_active()
            self._persist()
            return level.order_id
        except Exception as exc:
            level.status = "UNKNOWN"
            if isinstance(exc, UnsupportedCapabilityError) or getattr(exc, "submission_status", "") in {"NOT_SENT", "REJECTED"}:
                level.status = "REJECTED"
            self._pause(f"Команда {cid} остановлена; повторная отправка запрещена: {exc}",
                        recoverable=level.status == "UNKNOWN")
            return None

    def _wire_client_id(self, command_id):
        if getattr(self.client, "execution_safety_enabled", False):
            from src.core.execution_journal import stable_client_id
            return stable_client_id(self.client.account_id, command_id)
        return command_id

    def _can_place_buy(self):
        return self.state.gross_inventory < self.inventory_limit and not self.paused_reason

    def _can_place_sell(self):
        return (self.state.owned_long > 0 if self.grid_mode == "inventory" else self._can_place_buy())

    def _cancel(self, level):
        if self.read_only or level.order_id not in self.known_order_ids or level.status == "CANCEL_UNKNOWN":
            return False
        try:
            self._restore_order(level)
            level.status = "CANCEL_UNKNOWN"
            self._persist()
            cancel = self.client.cancel_order
            kwargs = {}
            if "command_id" in inspect.signature(cancel).parameters:
                kwargs["command_id"] = (level.command_id or level.client_order_id) + ":cancel"
            cancel(self.symbol, level.order_id, **kwargs)
            # False может означать гонку с FILLED; решает адресный терминальный readback, не ACK.
            self.reconcile(for_cancel=True)
            if level.status not in {"CANCELED", "FILLED", "EXPIRED", "REJECTED"}:
                raise ExchangeStateUnavailableError("GRID: терминальный статус отмены не подтверждён")
            return True
        except Exception as exc:
            self._pause(f"Результат отмены {level.order_id} требует сверки: {exc}", recoverable=True)
            return False

    def cancel_managed_orders(self):
        if self.read_only:
            return True
        success = True
        for level in list(self.active_orders.values()):
            success = self._cancel(level) and success
        return success

    def check_filled_orders(self):
        return self.reconcile()

    def check_emergency_conditions(self, current_price):
        if self.read_only:
            return False
        self.update_inventory()
        if self.state.gross_inventory > self.inventory_limit * 1.5:
            return True
        return any(calculate_pnl_pct(PositionAdapter(p).entry_price, current_price, PositionAdapter(p).direction)
                   <= -self.emergency_stop_loss_pct for p in self._positions
                   if PositionAdapter(p).position_id in self.owned_positions)

    def emergency_close(self):
        if self.read_only:
            return False
        warning(f"⚠️ [GRID] Экстренное закрытие только собственного объёма {self.symbol}")
        if not self.cancel_managed_orders() or any(v.status in {"PREPARED", "UNKNOWN", "CANCEL_UNKNOWN"} for v in self.commands.values()):
            return False
        try:
            positions = {PositionAdapter(p).position_id: PositionAdapter(p) for p in self._symbol_positions()}
            for pid, owned in list(self.owned_positions.items()):
                target = positions.get(pid)
                if (target is None or target.size < owned["quantity"]
                        or not math.isclose(target.size, owned.get("observed_quantity", target.size), rel_tol=1e-9)
                        or ("LONG" if target.is_long else "SHORT") != owned["side"]):
                    raise ExchangeStateUnavailableError("GRID: собственный объём emergency не подтверждён")
                command_id = "seg" + uuid.uuid4().hex[:28]
                cid = self._wire_client_id(command_id)
                # Отдельная durable-команда не переотправляется после неизвестного close.
                close_quantity = self._round_quantity(owned["quantity"])
                if close_quantity <= 0:
                    raise ExchangeStateUnavailableError("GRID: собственный dust меньше шага; требуется ручная обработка")
                command = GridLevel(0, "SELL" if target.is_long else "BUY", close_quantity,
                                    client_order_id=cid, command_id=command_id, position_side=owned["side"], reduce_only=True,
                                    position_id=pid, status="UNKNOWN")
                self.commands[cid] = command
                self._persist()
                register = getattr(self.client, "register_owned_position", None)
                coordinated = getattr(self.client, "execution_safety_enabled", False)
                if callable(register) and not coordinated:
                    register(self.symbol, pid, owned["quantity"], position_side=PositionSide(owned["side"]))
                # Coordinator сам восстанавливает ownership; raw-процент без registry относится ко всей позиции.
                percentage = 1.0 if coordinated or callable(register) else owned["quantity"] / target.size
                close = self.client.close_position
                if (self.settings["market_type"] == "spot" and not getattr(self.client, "execution_safety_enabled", False)
                        and "command_id" in inspect.signature(close).parameters):
                    command.client_order_id = "se" + hashlib.sha256((self.client.ns + ":" + command_id).encode()).hexdigest()[:30]
                    self.commands.pop(cid)
                    self.commands[command.client_order_id] = command
                    self._persist()
                    result = close(self.symbol, pid, percentage=percentage, command_id=command_id)
                else:
                    kwargs = {"percentage": percentage, "client_order_id": cid}
                    if getattr(self.client, "execution_safety_enabled", False):
                        kwargs["command_id"] = command_id
                    result = close(self.symbol, pid, **kwargs)
                if result is not True:
                    raise ExchangeStateUnavailableError("GRID: закрытие не подтверждено")
                fresh = next((PositionAdapter(p) for p in self._symbol_positions() if PositionAdapter(p).position_id == pid), None)
                after = fresh.size if fresh else 0
                if not math.isclose(target.size - after, close_quantity, rel_tol=1e-9, abs_tol=1e-12):
                    raise ExchangeStateUnavailableError("GRID: фактическое emergency уменьшение не совпало с собственным объёмом")
                command.status = "FILLED"
                command.accounted_quantity = close_quantity
                remaining = max(0.0, owned["quantity"] - close_quantity)
                if remaining > 1e-12:
                    owned["quantity"] = remaining
                    owned["observed_quantity"] = after
                else:
                    del self.owned_positions[pid]
                self._persist()
            self.update_inventory()
            if self.owned_positions:
                self._pause("Собственный dust после emergency требует ручной обработки")
                return False
            return True
        except Exception as exc:
            self._pause(f"Emergency GRID остановлен без повторной отправки: {exc}")
            return False

    def record_fill(self, side, price, qty, *, realized_pnl=0.0, commission=None):
        value = price * qty
        fee = value * self.fee_rate / 100 if commission is None else commission
        self.state.total_fees += fee
        if side == "BUY":
            self.state.total_filled_buy += 1
            self.state.total_buy_value += value
        else:
            self.state.total_filled_sell += 1
            self.state.total_sell_value += value
        # Покупка inventory — не реализованный убыток; cashflow хранится отдельно.
        self.state.gross_pnl += realized_pnl
        self.state.net_pnl = self.state.gross_pnl - self.state.total_fees

    def get_stats(self):
        return {**asdict(self.state), "symbol": self.symbol, "inventory_limit": self.inventory_limit,
                "inventory_pct": self.state.inventory / self.inventory_limit * 100,
                "active_orders": len(self.active_orders), "last_sync": self.state.last_sync_time,
                "grid_mode": self.grid_mode, "paused_reason": self.paused_reason,
                "entries_blocked_reason": self.entries_blocked_reason,
                "exchange_protection_capabilities": grid_safety.grid_protection_capabilities(self.settings)}
