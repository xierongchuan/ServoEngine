"""Один native MARKET entry на flat символ; durable POST и exact-ID attached lifecycle."""

from decimal import Decimal

from src.exchanges.dto.models import OrderSide, OrderType, PositionSide
from src.exchanges.errors import ExchangeError, ExchangeStateUnavailableError, UnsupportedCapabilityError
from .safety import native_grid_entry_block_reason


class NativeGridExecutionMixin:
    @staticmethod
    def _grid_native(intent):
        return intent["operation"] == "grid_entry" and intent["request"].get("execution_path") == "native_market"

    def _native_rejected_wire(self, intent):
        """ACK listenKey/плеча не отменяет явный отказ единственного entry POST."""
        if not self._grid_native(intent) or intent.get("order_id"):
            return None
        wires = [w["event"] for w in self.journal.wires(self.account_id, intent["command_id"])
                 if w["event"].get("endpoint") == "/openApi/swap/v2/trade/order"]
        if len(wires) != 1:
            return None
        event = wires[0]
        params = event.get("request_params", {})
        if (event.get("status") != "REJECTED" or event.get("method") != "post"
                or event.get("client_order_id") != intent["client_id"]
                or params.get("clientOrderId") != intent["client_id"]
                or params != event.get("prepared_request_params")
                or type(event.get("api_code")) is not int or not event["api_code"]
                or event.get("api_retryable") is not False):
            return None
        return {"code": event["api_code"], "message": event.get("api_message", ""),
                "entry_rejected": True}

    def _native_completed(self, intent):
        result = intent.get("result") or {}
        receipt = result.get("attachment", {})
        return (self._grid_native(intent) and receipt.get("state") == "CLOSED" and not receipt.get("cleanup_pending")
                and Decimal(receipt.get("closed_quantity", "0")) == Decimal(intent["request"]["quantity"]))

    def _native_close_allowed(self, symbol, operation, request, *, safety=False):
        if operation != "close" or Decimal(request.get("percentage", "0")) != 1:
            return False
        owned = [r for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol)
                 if r["position_id"] == request.get("position_id") and r["snapshot"].get("grid_native") and Decimal(r["quantity"]) > 0]
        if len(owned) != 1:
            return False
        parents = [r for r in self._grid_records(symbol, pending=False) if self._grid_native(r)
                   and str(r.get("order_id")) == str(owned[0]["snapshot"].get("entry_order_id"))]
        if len(parents) != 1 or parents[0]["state"] != "FILLED" or not parents[0]["result"].get("entry_complete"):
            return False
        receipt = parents[0]["result"].get("attachment", {})
        return receipt.get("confirmed") is True or (safety and request.get("native_entry_command") == parents[0]["command_id"])

    def _native_flat(self, symbol):
        if self._symbol_positions(self._positions(), symbol) or self._raw.get_open_orders(symbol):
            raise UnsupportedCapabilityError("GRID native: требуется flat символ без любых открытых заявок")
        if any(Decimal(r["quantity"]) > 0 for r in self.journal.owned(self.account_id, symbol=symbol)):
            raise ExchangeStateUnavailableError("GRID native: durable ownership ещё не закрыт")

    def place_native_grid_entry(self, symbol, side, quantity, *, sl, tp, gross_limit,
                                leverage=None, client_order_id=None, command_id=None):
        reason = native_grid_entry_block_reason(self.settings)
        if reason:
            raise UnsupportedCapabilityError(reason)
        side = OrderSide(side)
        direction = "LONG" if side == OrderSide.BUY else "SHORT"
        qty = self._grid_quantity(symbol, quantity)
        sl, tp = self._grid_price(symbol, OrderSide.SELL, sl), self._grid_price(symbol, OrderSide.BUY, tp)
        from src.core.execution_coordinator import decimal
        if qty > decimal(gross_limit, positive=True) or not (sl < tp if side == OrderSide.BUY else tp < sl):
            raise ExchangeStateUnavailableError("GRID native: неверные SL/TP или gross limit")
        request = {"side": side.value, "owned_side": direction, "quantity": str(qty), "price": None,
                   "position_side": direction if self.settings["position_mode"] == "hedge" else "BOTH",
                   "order_type": "MARKET", "sl": str(sl), "tp": str(tp), "leverage": leverage,
                   "gross_limit": str(gross_limit), "attached_protection": "exchange", "execution_path": "native_market"}
        entry_command = command_id or client_order_id
        try:
            with self._operation(symbol, "grid_entry", request, command_id=command_id or client_order_id,
                                 client_id=client_order_id) as (intent, fresh):
                entry_command = intent["command_id"]
                if not fresh:
                    if intent["state"] == "REJECTED":
                        self._replay(intent)
                    self._grid_reconcile_locked(symbol)
                    current = self.journal.get(self.account_id, intent["command_id"])
                    if not (current.get("result") or {}).get("attachment", {}).get("confirmed"):
                        raise ExchangeStateUnavailableError("GRID native: повтор не доказывает активную защиту")
                    self.last_execution_result = current["result"]
                    return current["order_id"]
                self._native_flat(symbol)
                self.journal.finish(self.account_id, intent["command_id"], "PREPARED",
                                    context={"before": [], "expected_quantity": str(qty)})
                stream = None
                oid = None
                try:
                    stream = self._raw.open_grid_attachment_stream(self.settings["protection"]["verification_timeout_seconds"])
                    try:
                        oid = self._raw.place_order(symbol, side, qty, OrderType.MARKET, sl=sl, tp=tp,
                            leverage=leverage, position_side=PositionSide(request["position_side"]),
                            client_order_id=intent["client_id"], native_attached=True)
                    except ExchangeError:
                        # ACK мог быть записан до ошибки fill; новая отправка не допускается.
                        oid = next((w["event"]["order_id"] for w in reversed(self.journal.wires(self.account_id, intent["command_id"]))
                                    if w["event"].get("order_id") and w["event"].get("client_order_id") == intent["client_id"]), None)
                        if not oid:
                            raise
                    links = stream.collect(oid)
                    links = [{k: e[k] for k in ("li", "i", "s", "S", "o", "ps", "q", "sp", "wt", "X", "z") if k in e}
                             for e in links]
                    self.journal.finish(self.account_id, intent["command_id"], "ACK", order_id=str(oid),
                        context={"before": [], "expected_quantity": str(qty), "native_links": links})
                except Exception as exc:
                    if not oid and not any(w["event"].get("endpoint") == "/openApi/swap/v2/trade/order"
                                           for w in self.journal.wires(self.account_id, intent["command_id"])):
                        exc.submission_status = "NOT_SENT"
                    raise
                finally:
                    if stream is not None:
                        stream.close()
                self._grid_reconcile_locked(symbol)
                current = self.journal.get(self.account_id, intent["command_id"])
                receipt = current["result"].get("attachment", {})
                if current["state"] != "FILLED" or not receipt.get("confirmed"):
                    raise ExchangeStateUnavailableError("GRID native: full fill и обе активные защиты не подтверждены")
                self.last_execution_result = current["result"]
                return str(oid)
        except ExchangeError as exc:
            current = self.journal.get(self.account_id, entry_command) if entry_command else None
            trade_wires = [w for w in self.journal.wires(self.account_id, entry_command) if
                           w["event"].get("endpoint") == "/openApi/swap/v2/trade/order"] if entry_command else []
            if current and (current.get("order_id") or trade_wires) and current["state"] != "REJECTED":
                self.journal.pause(self.account_id, symbol, "GRID_ATTACHED_PROTECTION_UNKNOWN", preserve_existing=True)
                exc.failure_policy = self.settings["protection"]["failure_policy"]
                if exc.failure_policy == "close" and (current.get("result") or {}).get("entry_complete"):
                    pid = (current.get("result") or {}).get("attachment", {}).get("position_id")
                    if not pid:
                        rows = self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol)
                        pid = rows[0]["position_id"] if len(rows) == 1 else None
                    if pid:
                        try:
                            exc.emergency_close_confirmed = self.close_position(symbol, pid,
                                command_id=current["command_id"] + ":native-failure-close", _safety=True,
                                _native_entry=current["command_id"])
                        except ExchangeError:
                            exc.emergency_close_confirmed = False
            raise

    def apply_native_grid_failure_policy(self, symbol, cause):
        """Worker применяет policy отдельно от GET-сверки; чужой drift не разрешает close."""
        if self.settings["protection"]["failure_policy"] != "close":
            return False
        parents = [r for r in self._grid_records(symbol, pending=False) if self._grid_native(r)
                   and r["state"] == "FILLED" and (r.get("result") or {}).get("entry_complete")
                   and Decimal(r["result"].get("filled_quantity", "0")) >
                       Decimal(r["result"].get("attachment", {}).get("closed_quantity", "0"))]
        if len(parents) != 1 or self.journal.intents(self.account_id, symbol=symbol, pending_only=True):
            return False
        parent = parents[0]
        try:
            own = self._validated_owned(symbol)
            if len(own) != 1 or not own[0][0]["snapshot"].get("grid_native"):
                return False
            return self.close_position(symbol, own[0][0]["position_id"],
                command_id=parent["command_id"] + ":native-failure-close", _safety=True,
                _native_entry=parent["command_id"])
        except ExchangeError:
            return False

    def _recover_native_cleanup(self, intent):
        # UNKNOWN DELETE никогда не повторяется; только terminal readback всех exact IDs.
        from src.exchanges.dto.models import OrderStatus
        if self._symbol_positions(self._positions(), intent["symbol"]):
            raise ExchangeStateUnavailableError("GRID native: cleanup recovery требует flat")
        parent = self.journal.get(self.account_id, intent["request"]["entry_command_id"])
        receipt = (parent.get("result") or {}).get("attachment", {}) if parent else {}
        if (not parent or not self._grid_native(parent) or parent["owner"] != self.owner_id
                or intent["request"]["order_ids"] != receipt.get("plan_ids")
                or Decimal(receipt.get("closed_quantity", "0")) != Decimal(parent["request"]["quantity"])):
            raise ExchangeStateUnavailableError("GRID native: cleanup не связан с доказанным full close")
        terminal = {OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.FILLED}
        if any(self._raw.get_order(intent["symbol"], oid).status not in terminal for oid in intent["request"]["order_ids"]):
            raise ExchangeStateUnavailableError("GRID native: UNKNOWN cleanup ещё не terminal; повтор DELETE запрещён")
        self._grid_reconcile_locked(intent["symbol"])
        self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"cleanup_confirmed": True})

    def cleanup_native_grid(self, symbol, *, command_id):
        existing = self.journal.get(self.account_id, command_id)
        if existing:
            if existing["operation"] != "grid_native_cleanup":
                raise ExchangeStateUnavailableError("GRID native: cleanup command ID уже занят")
            with self._operation(symbol, "grid_native_cleanup", existing["request"], command_id=command_id, safety=True) as (intent, fresh):
                if intent["state"] in {"PREPARED", "ACK", "UNKNOWN", "PARTIAL"}:
                    self._recover_native_cleanup(intent)
                    return True
                return bool(self._replay(intent))
        records = [r for r in self._grid_records(symbol, pending=False) if self._grid_native(r)
                   and (r.get("result") or {}).get("attachment", {}).get("cleanup_pending")]
        if len(records) != 1:
            raise ExchangeStateUnavailableError("GRID native: нет единственного доказанного cleanup")
        parent = records[0]
        receipt = parent["result"]["attachment"]
        if (parent["state"] != "FILLED" or not parent["result"].get("entry_complete")
                or Decimal(receipt.get("closed_quantity", "0")) != Decimal(parent["request"]["quantity"])):
            raise ExchangeStateUnavailableError("GRID native: cleanup требует ранее доказанного full close")
        if not receipt.get("plan_ids"):
            raise UnsupportedCapabilityError("GRID native: неизвестные orphan IDs нельзя отменять подбором")
        request = {"order_ids": receipt["plan_ids"], "entry_command_id": parent["command_id"]}
        with self._operation(symbol, "grid_native_cleanup", request, command_id=command_id, safety=True) as (intent, fresh):
            if not fresh:
                return bool(self._replay(intent))
            if self._symbol_positions(self._positions(), symbol):
                raise ExchangeStateUnavailableError("GRID native: cleanup разрешён только после доказанного flat")
            for oid in receipt["plan_ids"]:
                order = self._raw.get_order(symbol, oid)
                if order.status.value == "NEW":
                    self._raw._owned_orders[str(oid)] = self._raw._format_symbol(symbol)
                    self._raw.cancel_order(symbol, oid)
            self._recover_native_cleanup(intent)
            return True
