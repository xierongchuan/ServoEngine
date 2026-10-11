"""Отдельная очередь GRID: только ACK LIMIT; агрегирование доказанных fills без adoption."""

import copy
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from src.exchanges.dto.models import OrderSide, OrderStatus, OrderType, PositionSide
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from .execution_journal import canonical_symbol, PENDING
from .strategies.grid import safety as grid_safety
from src.utils.logger import error
from .strategies.grid.native_execution import NativeGridExecutionMixin

GRID_OPERATIONS = {"grid_entry", "grid_reduce"}
TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}


def number(value, **kwargs):
    from .execution_coordinator import decimal
    return decimal(value, **kwargs)


def enum_value(value):
    return getattr(value, "value", value)


class GridExecutionMixin(NativeGridExecutionMixin):
    def _grid_records(self, symbol, *, pending=True):
        return [r for r in self.journal.intents(self.account_id, symbol=symbol, pending_only=pending)
                if r["owner"] == self.owner_id and r["operation"] in GRID_OPERATIONS]

    def get_grid_entry_capabilities(self):
        return grid_safety.grid_protection_capabilities(self.settings)

    @staticmethod
    def _grid_attached(intent):
        return intent["operation"] == "grid_entry" and intent["request"].get("attached_protection") == "exchange"

    def _grid_attachment_receipt(self, intent, order):
        import json
        spec = intent["request"]
        if spec.get("sl") is None or spec.get("tp") is None:
            raise ExchangeStateUnavailableError("GRID attached: требуются обе стороны SL/TP")
        rules = self.get_instrument_rules(intent["symbol"])
        expected_side = spec["owned_side"] if self.settings["position_mode"] == "hedge" else "BOTH"
        if (spec.get("order_type") not in ({"MARKET"} if self._grid_native(intent) else {"LIMIT"}) or spec.get("position_side") != expected_side
                or spec.get("side") != ("BUY" if spec["owned_side"] == "LONG" else "SELL")):
            raise ExchangeStateUnavailableError("GRID attached: исходная команда имеет другой тип/сторону/режим")
        proven = False
        for wire in self.journal.wires(self.account_id, intent["command_id"]):
            event = wire["event"]
            params = event.get("prepared_request_params", event.get("request_params", {}))
            cid = params.get("clientOrderId", params.get("externalOid"))
            if cid != intent["client_id"] or str(event.get("method", "")).upper() != "POST":
                continue
            if self.settings["exchange"] == "mexc":
                if (event.get("endpoint") != "/api/v1/private/order/create" or params.get("type") != 1
                        or params.get("symbol") != rules.exchange_symbol
                        or params.get("side") != (1 if spec["owned_side"] == "LONG" else 3)
                        or params.get("openType") != 1 or self.settings["margin_mode"] != "isolated"
                        or params.get("positionMode") != (1 if self.settings["position_mode"] == "hedge" else 2)
                        or params.get("reduceOnly", False) is not False or params.get("positionId") not in (None, 0)
                        or number(params.get("vol"), positive=True) * rules.contract_size != number(spec["quantity"])
                        or number(params.get("price"), positive=True) != number(spec["price"])
                        or spec.get("leverage") is not None and number(params.get("leverage")) != number(spec["leverage"])):
                    continue
                proven = all(number(params.get(field), positive=True) == number(spec[key], positive=True)
                             for key, field in (("sl", "stopLossPrice"), ("tp", "takeProfitPrice")))
                trend = 2 if self.settings["protection"]["trigger_price"] == "mark" else 1
                proven = proven and params.get("lossTrend") == params.get("profitTrend") == trend
            else:
                if (event.get("endpoint") != "/openApi/swap/v2/trade/order" or params.get("type") != spec["order_type"]
                        or params.get("symbol") != rules.exchange_symbol or params.get("side") != spec["side"]
                        or params.get("positionSide") != expected_side
                        or str(params.get("reduceOnly", False)).lower() != "false"
                        or str(params.get("closePosition", False)).lower() != "false"
                        or number(params.get("quantity"), positive=True) != number(spec["quantity"])
                        or spec["order_type"] == "LIMIT" and number(params.get("price"), positive=True) != number(spec["price"])):
                    continue
                proven = True
                for key, field, kind in (("sl", "stopLoss", "STOP_MARKET"), ("tp", "takeProfit", "TAKE_PROFIT_MARKET")):
                    attachment = params.get(field)
                    if isinstance(attachment, str):
                        attachment = json.loads(attachment)
                    working = "MARK_PRICE" if self.settings["protection"]["trigger_price"] == "mark" else "CONTRACT_PRICE"
                    proven = proven and isinstance(attachment, dict) and attachment.get("type") == kind
                    if proven:
                        proven = number(attachment.get("stopPrice")) == number(spec[key]) and attachment.get("workingType") == working
                        if self._grid_native(intent):
                            proven = proven and number(attachment.get("quantity"), positive=True) == number(spec["quantity"])
            if proven:
                break
        if not proven:
            raise ExchangeStateUnavailableError("GRID attached: durable wire не доказывает SL/TP в исходном entry POST")
        method = getattr(self._raw, "get_grid_attached_protection", None)
        if not callable(method):
            raise UnsupportedCapabilityError("GRID attached: read-only сверка не поддерживается клиентом")
        if self._grid_native(intent):
            spec = {**spec, "native_links": intent["context"].get("native_links", [])}
            close_command = (intent.get("result") or {}).get("native_close_command")
            if close_command:
                close = self.journal.get(self.account_id, close_command)
                if not close or close["state"] != "FILLED" or not close["result"].get("close_confirmed"):
                    raise ExchangeStateUnavailableError("GRID native: durable full close не подтверждён")
                spec["native_close"] = {"order_id": close["order_id"], "client_id": close["client_id"]}
        return method(intent["symbol"], order, spec, previous=(intent.get("result") or {}).get("attachment"))

    def _grid_has_attached_exposure(self, symbol):
        # Ограничение действует на весь символ аккаунта, а не только текущего владельца.
        if any(r["snapshot"].get("grid_attached") and number(r["quantity"]) > 0
               for r in self.journal.owned(self.account_id, symbol=symbol)):
            return True
        return any(self._grid_attached(r) and (r["state"] in PENDING
                   or number((r.get("result") or {}).get("filled_quantity", 0))
                   > number((r.get("result") or {}).get("attachment", {}).get("closed_quantity", 0)))
                   for r in self.journal.intents(self.account_id, symbol=symbol))

    def is_grid_symbol_blocked(self, symbol):
        self._ensure_recovered()
        ignore = self.journal.grid_ack_ids(self.account_id, self.owner_id, symbol)
        return bool(self.journal.blocked(self.account_id, symbol, ignore=ignore))

    def _grid_reconcile_locked(self, symbol, *, exclude=()):
        records = [r for r in self._grid_records(symbol, pending=False) if r["command_id"] not in exclude
                   and (r["state"] in PENDING or self._grid_attached(r) and r.get("order_id"))
                   and not self._native_completed(r)]
        orders, updates = {}, []
        attachment_error = None
        deltas = {"LONG": Decimal(0), "SHORT": Decimal(0)}
        for intent in records:
            rejection = self._native_rejected_wire(intent)
            if rejection:
                updates.append({"command_id": intent["command_id"], "state": "REJECTED", "result": rejection})
                continue
            if intent["state"] == "PREPARED" and not self.journal.wires(self.account_id, intent["command_id"]):
                updates.append({"command_id": intent["command_id"], "state": "REJECTED", "result": {"not_sent": True}})
                continue
            order = self._query(intent)
            filled = self._fill_view(order, intent)
            previous = number((intent.get("result") or {}).get("filled_quantity", 0))
            direction = intent["request"]["owned_side"]
            delta = filled - previous
            deltas[direction] += delta if intent["operation"] == "grid_entry" else -delta
            terminal = order.status in TERMINAL
            state = "FILLED" if order.status == OrderStatus.FILLED else "RESOLVED" if terminal else "PARTIAL" if filled else "ACK"
            result = {**{k: v for k, v in (intent.get("result") or {}).items() if k == "native_close_command"},
                      "order_id": str(order.order_id), "filled_quantity": str(filled), "terminal": terminal,
                      "average_price": str(order.average_price),
                      "entry_complete": intent["operation"] == "grid_entry" and order.status == OrderStatus.FILLED,
                      "close_confirmed": intent["operation"] == "grid_reduce" and order.status == OrderStatus.FILLED}
            if terminal and filled and filled != number(intent["request"]["quantity"]):
                # Даже терминальный partial остаётся явной паузой; это не полное исполнение команды.
                state = "PARTIAL"
            if self._grid_attached(intent):
                previous_attachment = (intent.get("result") or {}).get("attachment") or {}
                try:
                    receipt = self._grid_attachment_receipt(intent, order)
                    closed = number(receipt["closed_quantity"])
                    if not number(previous_attachment.get("closed_quantity", 0)) <= closed <= filled:
                        raise ExchangeStateUnavailableError("GRID attached: close fill не монотонен или превышает entry")
                    deltas[direction] -= closed - number(previous_attachment.get("closed_quantity", 0))
                    result["attachment"] = receipt
                except Exception as exc:
                    attachment_error = exc
                    result["attachment"] = {**previous_attachment, "state": "UNKNOWN", "confirmed": False}
            updates.append({"command_id": intent["command_id"], "state": state, "result": result, "order_id": str(order.order_id)})
            orders[intent["command_id"]] = order
        positions = self._positions()
        views = [self._view(p, symbol) for p in self._symbol_positions(positions, symbol)]
        if (self.settings["position_mode"] == "one_way" and any(self._grid_attached(r) for r in records)
                and len({r["side"] for r in views if number(r["quantity"]) > 0}) > 1):
            raise ExchangeStateUnavailableError("GRID attached: противоположные позиции в One-way не подтверждают контракт аккаунта")
        owned = self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol)
        protections = self.journal.protections(self.account_id, owner=self.owner_id, symbol=symbol)
        changed = []
        for direction in deltas:
            previous = [r for r in owned if r["side"] == direction and number(r["quantity"]) > 0]
            targets = [r for r in views if r["side"] == direction]
            if not previous and not deltas[direction]:
                continue
            expected = sum((number(r["quantity"]) for r in previous), Decimal(0)) + deltas[direction]
            if (len(previous) > 1 or expected < 0 or len(targets) > 1
                    or sum((number(r["quantity"]) for r in targets), Decimal(0)) != expected
                    or (previous and targets and previous[0]["position_id"] != targets[0]["position_id"])):
                self.pause_symbol(symbol, "GRID: aggregate size/side/ID не совпал; ручное присвоение запрещено")
                raise ExchangeStateUnavailableError("GRID: fills не подтверждены точным агрегированным снимком позиции")
            if not targets and previous:
                changed.append(dict(previous[0], quantity="0"))
            elif targets:
                old = previous[0] if previous else None
                snapshot = copy.deepcopy(old["snapshot"]) if old else {}
                if not old:
                    first = next(r for r in records if r["operation"] == "grid_entry" and r["request"]["owned_side"] == direction
                                 and number(next(u["result"]["filled_quantity"] for u in updates if u["command_id"] == r["command_id"])) > 0)
                    snapshot.update(client_order_id=first["client_id"], entry_order_id=orders[first["command_id"]].order_id,
                                    opened_at=time.time(), protection={})
                attached = [r for r in records if self._grid_attached(r) and r["request"]["owned_side"] == direction]
                if attached:
                    covered = sum((number(u["result"]["filled_quantity"]) - number(u["result"].get("attachment", {}).get("closed_quantity", 0))
                                   for u in updates if any(r["command_id"] == u["command_id"] for r in attached)), Decimal(0))
                    if (covered != expected or any(p["position_id"] == targets[0]["position_id"] for p in protections)
                            or any(r["operation"] == "grid_reduce" and r["request"]["owned_side"] == direction for r in records)):
                        attachment_error = ExchangeStateUnavailableError("GRID attached: смешанный legacy/attached aggregate или reduce не поддерживается")
                        for update in updates:
                            if any(r["command_id"] == update["command_id"] for r in attached):
                                update["result"]["attachment"].update(state="UNKNOWN", confirmed=False)
                    snapshot["grid_attached"] = True
                    if any(self._grid_native(r) for r in attached):
                        snapshot["grid_native"] = True
                    snapshot["protection"] = {k: attached[0]["request"].get(k) for k in ("sl", "tp")}
                changed.append(dict(targets[0], owner=self.owner_id, snapshot=snapshot))
        protection_changed = any(deltas[r["side"]] != 0 and any(p["position_id"] == r["position_id"] for p in self.journal.protections(self.account_id, owner=self.owner_id, symbol=symbol)) for r in owned)
        cleanup_pending = any(u["result"].get("attachment", {}).get("cleanup_pending") for u in updates)
        self.journal.finish_many(self.account_id, updates, ownership=changed,
                                 pause_reason="CLOSE_PROTECTION_PENDING" if protection_changed else None,
                                 block=(symbol, "GRID_ATTACHED_PROTECTION_UNKNOWN") if attachment_error or cleanup_pending else None)
        for row in changed:
            self._restore_position(row)
        if attachment_error is not None:
            raise ExchangeStateUnavailableError(f"GRID: биржевая attached защита не доказана: {attachment_error}") from attachment_error
        if cleanup_pending:
            raise ExchangeStateUnavailableError("GRID native: close доказан, требуется exact-ID cleanup оставшейся защиты")
        if any(self._grid_attached(r) for r in records):
            self.journal.resolve_block(self.account_id, symbol, expected_reason="GRID_ATTACHED_PROTECTION_UNKNOWN")
        return orders, positions

    def reconcile_grid(self, symbol):
        """Одна read-only сверка всей сетки; fills и ownership коммитятся атомарно."""
        self._ensure_recovered()
        with self._lock, self.journal.symbol_lock(self.account_id, symbol):
            try:
                orders, _ = self._grid_reconcile_locked(symbol)
                return orders
            except Exception as exc:
                error(f"❌ [GRID] {symbol}: адресная сверка/защита не подтверждена: {exc}")
                # Сохраняем неизвестность, но никогда не обнуляем ранее доказанный fill/owned quantity.
                unresolved = self._grid_records(symbol)
                attached = [r for r in self._grid_records(symbol, pending=False) if self._grid_attached(r)
                            and not self._native_completed(r)
                                                        and (r.get("order_id") or self.journal.wires(self.account_id, r["command_id"]))]
                if attached:
                    self.journal.pause(self.account_id, symbol, "GRID_ATTACHED_PROTECTION_UNKNOWN", preserve_existing=True)
                    for row in attached:
                        result = copy.deepcopy(row.get("result") or {})
                        result["attachment"] = {**result.get("attachment", {}), "state": "UNKNOWN", "confirmed": False}
                        self.journal.finish(self.account_id, row["command_id"], row["state"], result=result)
                for row in unresolved:
                    if row["state"] in {"PREPARED", "ACK"}:
                        self.journal.finish(self.account_id, row["command_id"], "UNKNOWN")
                failure = exc if isinstance(exc, (ExchangeStateUnavailableError, UnknownOrderStateError)) else ExchangeStateUnavailableError("GRID: адресная сверка недоступна")
                if unresolved:
                    self._tag_error(failure, unresolved[0])
                    failure.command_ids = tuple(r["command_id"] for r in unresolved)
                raise failure from (None if failure is exc else exc)

    def get_order(self, symbol, order_id=None, *, client_order_id=None):
        """GRID lookup обновляет сразу aggregate ownership, а не присваивает одиночный fill."""
        self._ensure_recovered()
        records = [r for r in self._grid_records(symbol, pending=False)
                   if (order_id is not None and r.get("order_id") == str(order_id))
                   or (order_id is None and client_order_id is not None and r["client_id"] == str(client_order_id))]
        if records:
            intent = records[0]
            orders = self.reconcile_grid(symbol)
            order = orders.get(intent["command_id"])
            if order is None:
                order = self._query(intent)
                self._fill_view(order, intent)
            return order
        from .execution_coordinator import _call
        return _call(self._raw.get_order, symbol, order_id, client_order_id=client_order_id)

    def get_grid_owned_positions(self, symbol):
        self.reconcile_grid(symbol)
        if self.is_grid_symbol_blocked(symbol):
            raise ExchangeStateUnavailableError("GRID: ownership недоступен для управления при unresolved исполнении")
        rows = self._validated_owned(symbol)
        self.verify_protection(symbol)
        return {canonical_symbol(symbol): [p for _, p in rows]}

    def _grid_quantity(self, symbol, quantity):
        rules = self.get_instrument_rules(symbol)
        step = rules.quantity_step * rules.contract_size
        qty = (number(quantity, positive=True) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if qty <= 0:
            raise ExchangeStateUnavailableError("GRID: quantity меньше шага")
        return qty

    def _grid_price(self, symbol, side, price):
        step = self.get_instrument_rules(symbol).price_step
        result = (number(price, positive=True) / step).to_integral_value(
            rounding=ROUND_FLOOR if side == OrderSide.BUY else ROUND_CEILING) * step
        return number(result, positive=True)

    def _grid_reserved(self, symbol, position_id=None, *, exclude=()):
        total = Decimal(0)
        for row in self._grid_records(symbol):
            if row["command_id"] in exclude or row["state"] not in PENDING:
                continue
            if position_id is not None and (row["operation"] != "grid_reduce" or row["request"]["position_id"] != str(position_id)):
                continue
            if (row.get("result") or {}).get("terminal"):
                continue
            total += number(row["request"]["quantity"]) - number((row.get("result") or {}).get("filled_quantity", 0))
        return total

    def grid_gross_exposure(self, symbol, *, positions=None):
        positions = self._positions() if positions is None else positions
        gross = sum((number(self._view(p, symbol)["quantity"]) for p in self._symbol_positions(positions, symbol)), Decimal(0))
        records = self._grid_records(symbol)
        managed = {r["order_id"] for r in records if r.get("order_id")}
        gross += self._grid_reserved(symbol)
        # Чужие открытые заявки не отменяются, но полностью резервируют gross budget.
        for order in self.raw_client.get_open_orders(symbol):
            if str(order.order_id) in managed:
                continue
            raw = order.raw_data
            if "origQty" in raw:
                qty, filled = number(raw["origQty"]), number(raw.get("executedQty", 0))
            elif "vol" in raw and "dealVol" in raw:
                multiplier = self.get_instrument_rules(symbol).contract_size
                qty, filled = number(raw["vol"]) * multiplier, number(raw["dealVol"]) * multiplier
            else:
                qty, filled = number(order.quantity), number(order.filled_quantity)
            if qty <= 0 or not 0 <= filled <= qty:
                raise ExchangeStateUnavailableError("GRID: неизвестен gross объём внешней заявки")
            gross += qty - filled
        return gross

    def _grid_replay(self, intent):
        if self._grid_attached(intent):
            self.reconcile_grid(intent["symbol"])
            intent = self.journal.get(self.account_id, intent["command_id"])
        if intent["state"] in {"ACK", "FILLED", "RESOLVED"} and intent.get("order_id"):
            self.last_execution_result = intent.get("result")
            return intent["order_id"]
        return self._replay(intent)

    def _grid_validate_entry(self, symbol, direction, positions):
        before = [self._view(p, symbol) for p in self._symbol_positions(positions, symbol)]
        for row in before:
            if row["side"] == direction or self.settings["position_mode"] == "one_way":
                own = self._validated_owned(symbol, row["position_id"], positions=positions)
                if len(own) != 1 or row["side"] != direction or self.journal.protections(self.account_id, owner=self.owner_id, symbol=symbol):
                    raise UnsupportedCapabilityError("GRID: вход затронул бы ручную/противоположную/защищённую позицию")
        return before

    def place_grid_order(self, symbol, side, quantity, order_type=OrderType.LIMIT, price=None,
                         position_side=None, leverage=None, *, client_order_id=None, command_id=None, gross_limit, sl=None, tp=None):
        # Отказ до private reads, PREPARED и POST; старые команды сверяются через reconcile_grid.
        reason = grid_safety.grid_entry_block_reason(self.settings)
        if reason:
            gaps = "; ".join(grid_safety.grid_protection_capabilities(self.settings)["unresolved"])
            error(f"❌ [GRID] {symbol}: {reason} {gaps}")
            raise UnsupportedCapabilityError(f"{reason} {gaps}")
        side = OrderSide(enum_value(side))
        if OrderType(enum_value(order_type)) != OrderType.LIMIT:
            raise UnsupportedCapabilityError("Очередь GRID поддерживает только LIMIT")
        qty = self._grid_quantity(symbol, quantity)
        direction = "LONG" if side == OrderSide.BUY else "SHORT"
        pos_side = direction if self.settings["position_mode"] == "hedge" else "BOTH"
        if position_side is not None and enum_value(position_side) != pos_side:
            raise UnsupportedCapabilityError("GRID: неверная сторона входа для режима аккаунта")
        request = {"side": side.value, "quantity": str(qty), "price": str(self._grid_price(symbol, side, price)),
                   "order_type": "LIMIT", "position_side": pos_side, "owned_side": direction,
                   "leverage": leverage, "gross_limit": str(number(gross_limit, positive=True))}
        if sl is not None or tp is not None:
            raise UnsupportedCapabilityError("GRID attached: атомарный entry/partial protection не доказан; raw LIMIT fallback запрещён")
        self.reconcile_grid(symbol)
        with self._operation(symbol, "grid_entry", request, command_id=command_id or client_order_id,
                             client_id=client_order_id, grid_queue=True) as (intent, fresh):
            if not fresh:
                return self._grid_replay(intent)
            _, positions = self._grid_reconcile_locked(symbol, exclude=(intent["command_id"],))
            before = self._grid_validate_entry(symbol, direction, positions)
            if self.settings["position_mode"] == "one_way" and any(r["request"]["owned_side"] != direction for r in self._grid_records(symbol) if r["command_id"] != intent["command_id"]):
                raise UnsupportedCapabilityError("GRID: One-way очередь не допускает противоположные входы")
            self.journal.finish(self.account_id, intent["command_id"], "PREPARED", context={"before": before, "expected_quantity": str(qty)})
            # Новый PREPARED уже резервирует полный объём, в том числе при crash до ACK.
            if self.grid_gross_exposure(symbol, positions=positions) > number(gross_limit):
                raise ExchangeStateUnavailableError("GRID: pending gross exposure превышает inventory limit")
            from .execution_coordinator import _call
            for row in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol):
                self._restore_position(row)
            for queued in self._grid_records(symbol):
                self._restore_order(queued)
            oid = _call(self._raw.place_order, symbol, side, qty, order_type=OrderType.LIMIT, price=number(request["price"]),
                        position_side=PositionSide(pos_side), leverage=leverage, client_order_id=intent["client_id"])
            if not oid or isinstance(oid, bool):
                self._unknown(intent, "GRID: ACK без order ID")
            result = {"order_id": str(oid), "filled_quantity": "0", "terminal": False,
                      "entry_complete": False, "close_confirmed": False}
            self.journal.finish(self.account_id, intent["command_id"], "ACK", order_id=str(oid), result=result)
            self.last_execution_result = result
            return str(oid)

    def place_reduce_order(self, symbol, side, quantity, order_type=OrderType.LIMIT, price=None,
                           position_side=None, leverage=None, client_order_id=None, position_id=None,
                           reduce_only=True, *, command_id=None):
        if reduce_only is not True or position_id is None:
            raise UnsupportedCapabilityError("GRID reduce требует явные reduce_only=True и position_id")
        if OrderType(enum_value(order_type)) != OrderType.LIMIT:
            raise UnsupportedCapabilityError("GRID reduce поддерживает LIMIT; MARKET закрывайте через close_position")
        method = getattr(self._raw, "place_reduce_order", None)
        if not callable(method):
            raise UnsupportedCapabilityError("REST-клиент не реализует безопасный place_reduce_order")
        self.reconcile_grid(symbol)
        if self._grid_has_attached_exposure(symbol):
            raise UnsupportedCapabilityError("GRID attached: ручной reduce/resize без атомарного изменения обеих защит запрещён")
        qty = self._grid_quantity(symbol, quantity)
        side = OrderSide(enum_value(side))
        direction = "LONG" if side == OrderSide.SELL else "SHORT"
        pos_side = direction if self.settings["position_mode"] == "hedge" else "BOTH"
        if position_side is not None and enum_value(position_side) != pos_side:
            raise UnsupportedCapabilityError("GRID reduce: неверная исходная сторона позиции")
        request = {"side": side.value, "quantity": str(qty), "price": str(self._grid_price(symbol, side, price)), "order_type": "LIMIT",
                   "position_side": pos_side, "owned_side": direction, "position_id": str(position_id), "reduce_only": True, "leverage": leverage}
        with self._operation(symbol, "grid_reduce", request, command_id=command_id or client_order_id,
                             client_id=client_order_id, grid_queue=True) as (intent, fresh):
            if not fresh:
                return self._grid_replay(intent)
            self._grid_reconcile_locked(symbol, exclude=(intent["command_id"],))
            own = self._validated_owned(symbol, position_id)
            if len(own) != 1 or own[0][0]["side"] != direction:
                raise UnsupportedCapabilityError("GRID reduce: нет доказанного собственного side/ID")
            owned = own[0][0]
            if self._grid_reserved(symbol, position_id) > number(owned["quantity"]):
                raise ExchangeStateUnavailableError("GRID reduce: собственный объём уже зарезервирован другими LIMIT")
            self.journal.finish(self.account_id, intent["command_id"], "PREPARED", context={"before": owned, "expected_quantity": str(qty)})
            self._restore_position(owned)
            for queued in self._grid_records(symbol):
                self._restore_order(queued)
            oid = method(symbol=symbol, side=side, quantity=qty, order_type=OrderType.LIMIT, price=number(request["price"]),
                         position_side=PositionSide(pos_side), leverage=leverage, client_order_id=intent["client_id"],
                         position_id=str(position_id), reduce_only=True)
            if not oid or isinstance(oid, bool):
                self._unknown(intent, "GRID reduce: ACK без order ID")
            result = {"order_id": str(oid), "filled_quantity": "0", "terminal": False,
                      "entry_complete": False, "close_confirmed": False}
            self.journal.finish(self.account_id, intent["command_id"], "ACK", order_id=str(oid), result=result)
            self.last_execution_result = result
            return str(oid)
