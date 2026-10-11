"""Локальные уровни открывают MARKET; вся защита открытой позиции остаётся на бирже."""

import math
import time
import uuid

from src.exchanges.errors import ExchangeStateUnavailableError, UnsupportedCapabilityError


class NativeGridTriggerMixin:
    def _native_reconcile(self):
        if self.read_only:
            return []
        try:
            if not getattr(self.client, "execution_safety_enabled", False):
                raise UnsupportedCapabilityError("GRID native требует durable coordinator; raw fallback запрещён")
            try:
                self.client.reconcile_grid(self.symbol)
            except ExchangeStateUnavailableError:
                pending = [r for r in self.client._grid_records(self.symbol, pending=False)
                           if self.client._grid_native(r) and
                           (r.get("result") or {}).get("attachment", {}).get("cleanup_pending")]
                if len(pending) != 1:
                    raise
                # Только exact IDs после доказанного full close; UNKNOWN не повторяет DELETE.
                self.client.cleanup_native_grid(self.symbol, command_id=pending[0]["command_id"] + ":cleanup")
                self.client.reconcile_grid(self.symbol)
            filled = []
            for level in self.commands.values():
                record = self.client.journal.get(self.client.account_id, level.command_id)
                if record is None:
                    if level.status == "PREPARED":
                        level.status = "REJECTED"
                    continue
                result = record.get("result") or {}
                quantity = float(result.get("filled_quantity", 0))
                if quantity > level.accounted_quantity:
                    delta = quantity - level.accounted_quantity
                    self.record_fill(level.side, float(result.get("average_price", level.price)), delta)
                    level.accounted_quantity = quantity
                    filled.append({"side": level.side, "qty": delta, "order_id": record.get("order_id")})
                level.order_id = record.get("order_id")
                level.status = "FILLED" if record["state"] == "FILLED" else "REJECTED" if record["state"] == "REJECTED" else "UNKNOWN"
                level.filled = level.status == "FILLED"
            self.owned_positions = {r["position_id"]: {"quantity": float(r["quantity"]), "side": r["side"],
                                    "observed_quantity": float(r["quantity"])}
                                    for r in self.client.journal.owned(self.client.account_id,
                                        owner=self.client.owner_id, symbol=self.symbol) if float(r["quantity"]) > 0}
            self.update_inventory()
            if self.client.is_grid_symbol_blocked(self.symbol):
                raise ExchangeStateUnavailableError("GRID native: journal всё ещё блокирует символ")
            if self._pause_kind == "recovery":
                self.paused_reason = ""
            self._refresh_active()
            self._persist()
            return filled
        except Exception as exc:
            if getattr(self.client, "execution_safety_enabled", False):
                self.client.apply_native_grid_failure_policy(self.symbol, exc)
            self._pause(f"Native lifecycle требует сверки; повтор входа запрещён: {exc}", recoverable=True)
            return []

    def native_cycle(self, current_price, reference, *, spacing_mult=1.0, should_pause=False):
        from src.core.grid_executor import GridLevel

        self._native_reconcile()
        if self.paused_reason or self.entries_blocked_reason or self.read_only:
            return False
        if not all(math.isfinite(p) and p > 0 for p in (current_price, reference)):
            self._pause("GRID native: нет достоверной public цены/reference")
            return False
        if self.owned_positions or self.active_orders:
            return True
        # Нет pending собственного entry, но чужой объём/заявка также запрещает arm/POST.
        if self.state.gross_inventory or self.client.get_open_orders(self.symbol):
            return False
        if not self.state.native_levels:
            if should_pause:
                return False
            self.state.native_levels = self.calculate_grid_prices(current_price, spacing_mult)
            if self.grid_mode == "inventory":
                self.state.native_levels["sell"] = []
            self.state.native_previous_price = current_price
            self._persist()
            return True
        previous = self.state.native_previous_price
        self.state.native_previous_price = current_price
        self._persist()
        if should_pause:
            return False
        crossings = [("BUY", p) for p in self.state.native_levels.get("buy", []) if previous > p >= current_price]
        crossings += [("SELL", p) for p in self.state.native_levels.get("sell", []) if previous < p <= current_price]
        if not crossings:
            return True
        side, price = sorted(crossings, key=lambda pair: abs(pair[1] - previous))[0]
        quantity = self._calculate_order_quantity(current_price)
        if quantity <= 0 or quantity > self.inventory_limit:
            self._pause("GRID native: объём входа вне instrument/gross limit")
            return False
        sign = 1 if side == "BUY" else -1
        sl = reference * (1 - sign * self.emergency_stop_loss_pct / 100)
        tp = reference * (1 + sign * self.spacing_pct * spacing_mult / 100)
        command_id = "seg" + uuid.uuid4().hex[:28]
        cid = self._wire_client_id(command_id)
        level = GridLevel(price, side, quantity, client_order_id=cid, command_id=command_id,
                          position_side="LONG" if side == "BUY" else "SHORT")
        self.commands[cid] = level
        # Один touch поглощает всю сетку; новый arm только после полного closed+cleanup.
        self.state.native_levels = {}
        self._persist()
        try:
            level.order_id = self.client.place_native_grid_entry(self.symbol, side, quantity, sl=sl, tp=tp,
                gross_limit=self.inventory_limit, leverage=self.leverage, client_order_id=cid, command_id=command_id)
            level.status = "FILLED"
            self._persist()
            self._native_reconcile()
            self.state.last_sync_time = time.time()
            self._persist()
            return not bool(self.paused_reason)
        except Exception as exc:
            level.status = "UNKNOWN"
            self._pause(f"GRID native entry не подтверждён; повтор запрещён: {exc}", recoverable=True)
            return False
