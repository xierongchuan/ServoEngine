"""Единый fail-closed слой исполнения над REST-клиентами, без повторной отправки UNKNOWN."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

from src.exchanges.dto.models import OrderSide, OrderStatus, OrderType, PositionSide
from src.exchanges.errors import ExchangeAPIError, ExchangeError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from .execution_journal import ExecutionJournal, PENDING, canonical_symbol
from .execution_grid import GridExecutionMixin, GRID_OPERATIONS


def decimal(value, name="число", *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or (positive and result <= 0):
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation):
        raise ExchangeStateUnavailableError(f"Некорректное {name}") from None


def value(item):
    return getattr(item, "value", item)


def _call(method, *args, **kwargs):
    parameters = inspect.signature(method).parameters
    if not any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in parameters}
    return method(*args, **kwargs)


def _unwrap(client):
    return getattr(client, "_client", client)


class _JournalTransport:
    def __init__(self, transport, coordinator):
        self.transport, self.coordinator = transport, coordinator

    def __getattr__(self, name):
        return getattr(self.transport, name)

    def request(self, method, endpoint, params=None, **kwargs):
        if not kwargs.get("mutation", str(method).upper() != "GET"):
            return self.transport.request(method, endpoint, params, **kwargs)
        event = {"status": "PREPARED", "method": method, "endpoint": endpoint,
                 "request_params": copy.deepcopy(params or {})}
        self.coordinator._observe(event)
        response = self.transport.request(method, endpoint, params, **kwargs)
        data = response.get("data") if isinstance(response, dict) else None
        order_id = data.get("orderId", data.get("id")) if isinstance(data, dict) else data if isinstance(data, (str, int)) else None
        self.coordinator._observe({**event, "status": "ACKNOWLEDGED", "order_id": str(order_id) if order_id is not None else None})
        return response


class ExecutionCoordinator(GridExecutionMixin):
    execution_safety_enabled = True

    def __init__(self, client, *, journal_path=None, settings=None, owner_id=None, account_id=None):
        from src.trading_settings import get_trading_settings, validate_trading_settings
        self.raw_client = client
        self._raw = _unwrap(client)
        self.settings = validate_trading_settings(settings if settings is not None else get_trading_settings())
        self.owner_id = str(owner_id or "default")
        self._check_execution_identity()
        key = getattr(self._raw, "api_key", None) or getattr(getattr(self._raw, "_config", None), "api_key", "") or "public"
        namespace = f"{self.settings['exchange']}:{self.settings['market_type']}:{self.settings['execution_mode']}:{key}"
        self.account_id = str(account_id or hashlib.sha256(namespace.encode()).hexdigest())
        self.execution_account_id = self.account_id
        self.journal = ExecutionJournal(journal_path or Path(os.getenv("SERVO_DATA_DIR", "data")) / "execution.sqlite3")
        self._lock = threading.RLock()
        self._local = threading.local()
        self._recovered = False
        self._reconciliation_at = {}
        self.last_execution_result = None
        if hasattr(self._raw, "_submission_observer"):
            previous = self._raw._submission_observer
            def observer(event):
                self._observe(event)
                if previous is not None:
                    previous(event)
            self._raw._submission_observer = observer
        elif hasattr(self._raw, "_transport"):
            self._raw._transport = _JournalTransport(self._raw._transport, self)
        else:
            raise UnsupportedCapabilityError("Клиент не предоставляет перехват PREPARED до HTTP")

    def _check_execution_identity(self):
        config = getattr(self._raw, "_config", None)
        supplied = self._raw._settings() if hasattr(self._raw, "_settings") and callable(self._raw._settings) else getattr(config, "trading_settings", None)
        actual = dict(supplied) if isinstance(supplied, dict) else {}
        for key in ("execution_mode", "margin_mode", "position_mode", "settle_asset"):
            if key not in actual and getattr(config, key, None) is not None:
                actual[key] = getattr(config, key)
        actual.setdefault("exchange", str(getattr(self._raw, "EXCHANGE_NAME", self.settings["exchange"])).lower())
        capabilities = getattr(self._raw, "capabilities", None)
        if capabilities is not None:
            actual.setdefault("market_type", value(capabilities.market_type))
        for key in ("exchange", "market_type", "execution_mode", "margin_mode", "position_mode", "settle_asset"):
            if actual.get(key) != self.settings[key]:
                raise ExchangeStateUnavailableError(f"Raw client и coordinator расходятся: {key}")
        protection = actual.get("protection", getattr(config, "protection", None))
        if isinstance(protection, dict) and protection.get("trigger_price", "mark") != self.settings["protection"]["trigger_price"]:
            raise ExchangeStateUnavailableError("Raw client и coordinator используют разные trigger_price")

    def __getattr__(self, name):
        # Эти raw escape hatches обошли бы journal/ownership.
        if name in {"make_request", "_make_request", "_send", "_request", "submit_spot_order", "_transport"}:
            raise UnsupportedCapabilityError("Raw mutation в обход координатора запрещена")
        return getattr(self.raw_client, name)

    def _observe(self, event):
        current = getattr(self._local, "intent", None)
        if current is None:
            raise ExchangeStateUnavailableError("Мутация вне durable execution intent запрещена")
        current = self.journal.get(self.account_id, current["command_id"])
        event = copy.deepcopy(event)
        if event.get("status") == "PREPARED":
            params = event.get("request_params", {})
            if current["operation"] in GRID_OPERATIONS:
                _, positions = self._grid_reconcile_locked(current["symbol"], exclude=(current["command_id"],))
                ignore = (current["command_id"], *self.journal.grid_ack_ids(self.account_id, self.owner_id, current["symbol"]))
                if self.journal.blocked(self.account_id, current["symbol"], ignore=ignore):
                    raise ExchangeStateUnavailableError("GRID: новая мутация запрещена после pending/partial/protection")
                if current["operation"] == "grid_entry":
                    if self._grid_native(current):
                        self._native_flat(current["symbol"])
                    self._grid_validate_entry(current["symbol"], current["request"]["owned_side"], positions)
                    before = [self._view(p, current["symbol"]) for p in self._symbol_positions(positions, current["symbol"])]
                    if self.grid_gross_exposure(current["symbol"], positions=positions) > decimal(current["request"]["gross_limit"]):
                        raise ExchangeStateUnavailableError("GRID: gross limit изменился перед HTTP")
                else:
                    own = self._validated_owned(current["symbol"], current["request"]["position_id"], positions=positions)
                    if len(own) != 1 or own[0][0]["side"] != current["request"]["owned_side"]:
                        raise ExchangeStateUnavailableError("GRID reduce: собственный ID/side изменён перед HTTP")
                    before = own[0][0]
                    if self._grid_reserved(current["symbol"], before["position_id"]) > decimal(before["quantity"]):
                        raise ExchangeStateUnavailableError("GRID reduce: превышен резерв собственного объёма")
                self.journal.finish(self.account_id, current["command_id"], "PREPARED", context={**current["context"], "before": before})
                current = self.journal.get(self.account_id, current["command_id"])
            leverage_wire = event.get("endpoint", "").endswith(("/leverage", "/change_leverage"))
            if (current["operation"] in GRID_OPERATIONS and str(event.get("method")).lower() == "post"
                    and event.get("endpoint", "").endswith(("/order", "/order/create"))):
                cid = params.get("clientOrderId", params.get("externalOid"))
                native = self._grid_native(current)
                if (cid != current["client_id"] or params.get("type") not in ({"MARKET"} if native else {"LIMIT", 1})
                        or not native and decimal(params.get("price")) != decimal(current["request"]["price"])):
                    raise ExchangeStateUnavailableError("GRID HTTP не совпадает с точным intent/client ID/type/price")
                if native:
                    for key, field, kind in (("sl", "stopLoss", "STOP_MARKET"), ("tp", "takeProfit", "TAKE_PROFIT_MARKET")):
                        attachment = json.loads(params.get(field, "{}"))
                        working = "MARK_PRICE" if self.settings["protection"]["trigger_price"] == "mark" else "CONTRACT_PRICE"
                        if (attachment.get("type") != kind or decimal(attachment.get("stopPrice")) != decimal(current["request"][key])
                                or decimal(attachment.get("quantity")) != decimal(current["request"]["quantity"])
                                or attachment.get("workingType") != working):
                            raise ExchangeStateUnavailableError("GRID native HTTP: attached SL/TP не совпадают с intent")
                if params.get("positionSide") is not None and params["positionSide"] != current["request"]["position_side"]:
                    raise ExchangeStateUnavailableError("GRID HTTP направлен на другую сторону позиции")
                if params.get("positionMode") is not None and params["positionMode"] != (1 if self.settings["position_mode"] == "hedge" else 2):
                    raise ExchangeStateUnavailableError("GRID HTTP имеет другой position mode")
            if leverage_wire and current["operation"] not in {"leverage", "entry", "grid_entry"}:
                raise UnsupportedCapabilityError("Изменение плеча внутри закрытия/защиты запрещено")
            if current["operation"] == "leverage" or leverage_wire:
                observed = [self._view(p, current["symbol"]) for p in self._symbol_positions(self._positions(), current["symbol"])]
                if sorted(observed, key=lambda p: p["position_id"]) != sorted(current["context"].get("before", []), key=lambda p: p["position_id"]):
                    self.pause_symbol(current["symbol"], "Позиция изменилась перед HTTP leverage")
                    raise ExchangeStateUnavailableError("HTTP leverage запрещён: снимок позиции изменился")
            if current["operation"] in {"entry", "close", "protection", "grid_entry", "grid_reduce"} and not leverage_wire:
                if current["operation"] in {"entry", "grid_entry"}:
                    observed = [self._view(p, current["symbol"]) for p in self._symbol_positions(self._positions(), current["symbol"])]
                    before = current["context"].get("before", [])
                    if sorted(observed, key=lambda p: p["position_id"]) != sorted(before, key=lambda p: p["position_id"]):
                        self.pause_symbol(current["symbol"], "Позиция изменилась перед HTTP entry")
                        raise ExchangeStateUnavailableError("HTTP entry запрещён: снимок позиции изменился")
                elif current["operation"] == "protection" or any(k in params for k in ("quantity", "vol")):
                    before = current["context"].get("before")
                    if before:
                        expected_position = decimal(before["quantity"])
                        if current["operation"] == "close" and params.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                            expected_position -= decimal(current["context"]["expected_quantity"])
                        observed = [self._view(p, current["symbol"]) for p in self._symbol_positions(self._positions(), current["symbol"])]
                        target = next((p for p in observed if p["position_id"] == before["position_id"]), None)
                        if target is None or target["side"] != before["side"] or decimal(target["quantity"]) != expected_position:
                            self.pause_symbol(current["symbol"], "Размер/сторона/ID изменились перед HTTP")
                            raise ExchangeStateUnavailableError("HTTP запрещён: собственный snapshot изменился")
                expected = current["context"].get("expected_quantity", current["request"].get("quantity"))
                if current["operation"] == "close" and params.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                    expected = decimal(current["context"]["before"]["quantity"]) - decimal(expected)
                multiplier = self.get_instrument_rules(current["symbol"]).contract_size
                amounts = [(decimal(params[k]) * (multiplier if k != "quantity" else 1)) for k in ("quantity", "vol", "stopLossVol", "takeProfitVol") if k in params and params[k] is not None]
                if expected is not None and any(q != decimal(expected) for q in amounts):
                    self.pause_symbol(current["symbol"], "Mutation quantity изменилась между preflight и HTTP")
                    exc = ExchangeStateUnavailableError("HTTP запрещён: mutation quantity не равна доказанному объёму")
                    exc.submission_status = "NOT_SENT"
                    raise exc
                if current["operation"] in {"close", "protection", "grid_reduce"} and params.get("positionId") is not None and str(params["positionId"]) != current["request"]["position_id"]:
                    raise ExchangeStateUnavailableError("HTTP запрещён: мутация относится к другому positionId")
                if params.get("side") is not None:
                    expected_side = current["request"].get("side") if current["operation"] in {"entry", "grid_entry"} else ("SELL" if current["context"]["before"]["side"] == "LONG" else "BUY")
                    native_side = (1 if expected_side == "BUY" else 3) if current["operation"] in {"entry", "grid_entry"} else (4 if expected_side == "SELL" else 2)
                    if params["side"] not in {expected_side, native_side}:
                        raise ExchangeStateUnavailableError("HTTP запрещён: направление мутации не совпадает с intent")
                if (current["operation"] in {"close", "grid_reduce"} and str(event.get("method")).lower() == "post"
                        and event.get("endpoint", "").endswith(("/order", "/order/create"))
                        and self.settings["position_mode"] == "one_way" and str(params.get("reduceOnly")).lower() != "true"):
                    raise ExchangeStateUnavailableError("HTTP запрещён: One-way close без reduceOnly")
        if event.get("status") == "PREPARED" and (current["operation"] == "grid_native_cleanup"
                or current["operation"] == "close" and current["request"].get("native_entry_command")):
            if str(event.get("method")).lower() == "delete":
                params = event.get("request_params", {})
                if (self._symbol_positions(self._positions(), current["symbol"])
                        or current["operation"] == "grid_native_cleanup" and str(params.get("orderId")) not in current["request"]["order_ids"]):
                    raise ExchangeStateUnavailableError("GRID native: отмена защиты запрещена без fresh flat/exact ID")
        event.setdefault("symbol", current["symbol"])
        event.setdefault("intent_client_id", current["client_id"])
        params = event.get("request_params", {})
        event.setdefault("client_order_id", params.get("clientOrderId", params.get("externalOid")))
        event.setdefault("position_id", current["request"].get("position_id"))
        wire_id = None if event["status"] == "PREPARED" else getattr(self._local, "wire_id", None)
        if wire_id is not None:
            prior = next((w["event"] for w in self.journal.wires(self.account_id, current["command_id"]) if w["id"] == wire_id), {})
            event.setdefault("prepared_request_params", prior.get("prepared_request_params", prior.get("request_params", {})))
            event.setdefault("endpoint", prior.get("endpoint"))
            event.setdefault("method", prior.get("method"))
            event.setdefault("submission_id", prior.get("submission_id"))
        self._local.wire_id = self.journal.wire(self.account_id, current["command_id"], event, wire_id=wire_id)

    def run_verified_operation(self, symbol, operation, request, submit, verify, *, command_id=None,
                               client_order_id=None, context=None):
        """Контракт Spot/grid adapter: submit(cid), verify(ack)->{state,result,order_id}.

        Adapter отвечает за доменные доказательства fill/inventory; truthy ACK не допускается.
        Этот путь не снимает блокировки и не присваивает futures ownership.
        """
        with self._operation(symbol, operation, request, command_id=command_id or client_order_id,
                             client_id=client_order_id, context=context) as (intent, fresh):
            if not fresh:
                self._replay(intent)
                return {"state": intent["state"], "result": intent["result"], "order_id": intent.get("order_id")}
            ack = submit(intent["client_id"])
            receipt = verify(ack)
            if not isinstance(receipt, dict) or receipt.get("state") not in {"ACK", "UNKNOWN", "PARTIAL", "FILLED", "REJECTED", "RESOLVED"}:
                self._unknown(intent, "Adapter не предоставил явное доказанное состояние")
            state = receipt["state"]
            result = receipt.get("result", {})
            self.journal.finish(self.account_id, intent["command_id"], state, result=result, order_id=receipt.get("order_id"))
            self.last_execution_result = result
            if state in {"UNKNOWN", "PARTIAL"}:
                self._unknown(intent, "Adapter оставил неразрешённое исполнение", state=state, order_id=receipt.get("order_id"))
            return {"state": state, "result": result, "order_id": receipt.get("order_id")}

    def reconcile_verified_operation(self, command_id, verify):
        """Read-only контракт adapter: verify(intent)->receipt; callback не получает mutation intent."""
        intent = self.journal.get(self.account_id, str(command_id))
        if intent is None or intent["owner"] != self.owner_id or intent["operation"] in {"entry", "close", "cancel", "protection", "leverage"}:
            raise UnsupportedCapabilityError("Адресная сверка требует собственной domain-команды adapter")
        if getattr(self._local, "intent", None) is not None:
            raise ExchangeStateUnavailableError("Domain reconciliation нельзя вызывать внутри HTTP mutation")
        with self._lock, self.journal.symbol_lock(self.account_id, intent["symbol"]):
            intent = self.journal.get(self.account_id, str(command_id))
            receipt = verify(copy.deepcopy(intent))
            if not isinstance(receipt, dict) or receipt.get("state") not in {"ACK", "UNKNOWN", "PARTIAL", "FILLED", "REJECTED", "RESOLVED"}:
                self._unknown(intent, "Domain reconciliation не предоставил доказанное состояние")
            state, result = receipt["state"], receipt.get("result", {})
            self.journal.finish(self.account_id, intent["command_id"], state, result=result, order_id=receipt.get("order_id"))
            self.last_execution_result = result
            if state in {"UNKNOWN", "PARTIAL"}:
                self._unknown(intent, "Domain-команда остаётся неразрешённой", state=state)
            return {"state": state, "result": result, "order_id": receipt.get("order_id", intent.get("order_id"))}

    def _raw_symbol_uncertain(self, symbol):
        uncertain = getattr(self._raw, "_uncertain_symbols", ())
        return isinstance(uncertain, set) and any(canonical_symbol(key) == canonical_symbol(symbol) for key in uncertain)

    def is_symbol_blocked(self, symbol):
        self._ensure_recovered()
        return bool(self.journal.blocked(self.account_id, symbol) or self._raw_symbol_uncertain(symbol))

    def reconcile_if_due(self, symbol):
        """Ограниченная сверка на границе worker-цикла, не побочный эффект getter."""
        self._ensure_recovered()
        key = canonical_symbol(symbol)
        now = time.monotonic()
        interval = max(10, self.settings["market_data"]["reconcile_interval_seconds"])
        if (self.is_symbol_blocked(key)
                and now - self._reconciliation_at.get(key, float("-inf")) >= interval):
            self._reconciliation_at[key] = now
            try:
                self.reconcile_pending(symbol=key)
            except ExchangeError:
                pass
        return self.is_symbol_blocked(key)

    def pause_symbol(self, symbol, reason):
        from src.utils.logger import warning, log_context
        previous = self.journal.blocked(self.account_id, symbol)
        self.journal.pause(self.account_id, symbol, reason)
        if previous != reason:
            with log_context(category="risk", symbol=symbol, instance_id=self.owner_id):
                warning(f"⏸️ Торговля приостановлена: {reason}")

    def _tag_error(self, exc, intent):
        exc.command_id, exc.intent_client_id = intent["command_id"], intent["client_id"]
        if intent["operation"] in {"entry", "close", "grid_entry", "grid_reduce"}:
            exc.client_order_id = intent["client_id"]
        else:
            exc.client_order_id = getattr(exc, "client_order_id", None)
        exc.account_id, exc.owner_id = self.account_id, self.owner_id
        exc.symbol, exc.operation, exc.journal_path = intent["symbol"], intent["operation"], str(self.journal.path)
        if not getattr(exc, "_execution_logged", False):
            from src.utils.logger import error
            error(f"❌ {intent['symbol']}: исполнение не подтверждено ({type(exc).__name__}), команда {intent['command_id']}")
            exc._execution_logged = True
        return exc

    def _unknown(self, intent, message, *, state="UNKNOWN", **context):
        current = self.journal.get(self.account_id, intent["command_id"])
        if current["state"] in PENDING:
            result = dict(current.get("result") or {})
            result.update(context)
            self.journal.finish(self.account_id, intent["command_id"], state, result=result)
        exc = self._tag_error(UnknownOrderStateError(message), intent)
        exc.submission_status, exc.request_params = state, intent["request"]
        for key, item in context.items():
            setattr(exc, key, item)
        raise exc

    @contextmanager
    def _operation(self, symbol, operation, request, *, command_id=None, client_id=None, context=None, allow_pending=(), safety=False, grid_queue=False):
        self._check_execution_identity()
        if self.settings["execution_mode"] == "read_only":
            raise UnsupportedCapabilityError("Исполнение запрещено режимом read_only")
        self._ensure_recovered()
        if client_id is not None and not re.fullmatch(r"[a-z0-9_-]{1,32}", str(client_id)):
            raise ExchangeStateUnavailableError("client_order_id должен быть lowercase длиной до 32")
        with self._lock, self.journal.symbol_lock(self.account_id, symbol):
            replay_native = operation == "grid_entry" and request.get("execution_path") == "native_market" and command_id and self.journal.get(self.account_id, command_id)
            if (operation in {"close", "protection", "leverage", "grid_reduce", "entry", "grid_entry"}
                    and self._grid_has_attached_exposure(symbol) and not replay_native
                    and not self._native_close_allowed(symbol, operation, request, safety=safety)):
                raise UnsupportedCapabilityError("GRID attached: мутация объёма/защиты без атомарного lifecycle запрещена; доступна GET-сверка")
            intent, fresh = self.journal.prepare(self.account_id, self.owner_id, symbol, operation, request,
                                                 context, command_id=command_id, client_id=client_id, allow_pending=allow_pending, safety=safety, grid_queue=grid_queue)
            old = getattr(self._local, "intent", None)
            self._local.intent, self._local.wire_id = intent, None
            from src.utils.logger import log_context, info
            context_scope = log_context(category="order", symbol=symbol, instance_id=self.owner_id,
                                        request_id=intent["command_id"])
            context_scope.__enter__()
            try:
                if fresh:
                    info(f"📝 Подготовлена команда {operation}; ID={intent['command_id']}")
                yield intent, fresh
            except ExchangeAPIError as exc:
                self._tag_error(exc, intent)
                # Отказ после ACK другой мутации не доказывает отсутствие позиции.
                current = self.journal.get(self.account_id, intent["command_id"])
                wires = self.journal.wires(self.account_id, intent["command_id"])
                acknowledged = any(w["event"].get("status") == "ACKNOWLEDGED" for w in wires)
                if (self._grid_native(current) and current["state"] in PENDING
                        and getattr(exc, "submission_status", None) == "NOT_SENT"
                        and not any(w["event"].get("endpoint") == "/openApi/swap/v2/trade/order" for w in wires)):
                    self.journal.finish(self.account_id, intent["command_id"], "REJECTED",
                                        result={"not_sent": True, "code": exc.code})
                    raise
                if current["state"] in PENDING:
                    rejection = self._native_rejected_wire(current)
                    if rejection:
                        self.journal.finish(self.account_id, intent["command_id"], "REJECTED", result=rejection)
                        raise
                    if acknowledged:
                        self._unknown(intent, "Ошибка после ACK; нужна адресная сверка", order_id=current.get("order_id"))
                    self.journal.finish(self.account_id, intent["command_id"], "REJECTED", result={"code": exc.code})
                raise
            except UnknownOrderStateError as exc:
                self._tag_error(exc, intent)
                current = self.journal.get(self.account_id, intent["command_id"])
                wires = self.journal.wires(self.account_id, intent["command_id"])
                if (self._grid_native(current) and current["state"] in PENDING
                        and getattr(exc, "submission_status", None) == "NOT_SENT"
                        and not any(w["event"].get("endpoint") == "/openApi/swap/v2/trade/order" for w in wires)):
                    # UNKNOWN listenKey не означает UNKNOWN entry; trading PREPARED сюда никогда не попадает.
                    self.journal.finish(self.account_id, intent["command_id"], "REJECTED",
                                        result={"not_sent": True, "auxiliary_unknown": True})
                    raise
                if current["state"] in PENDING and not wires:
                    self.journal.finish(self.account_id, intent["command_id"], "REJECTED", result={"not_sent": True})
                    self.pause_symbol(symbol, "Pre-HTTP состояние собственной команды не подтверждено")
                    raise self._tag_error(ExchangeStateUnavailableError("Мутация не отправлена; символ приостановлен"), intent) from exc
                if current["state"] in {"PREPARED", "ACK"}:
                    self.journal.finish(self.account_id, intent["command_id"], "UNKNOWN")
                raise
            except Exception as exc:
                if isinstance(exc, ExchangeError):
                    self._tag_error(exc, intent)
                current = self.journal.get(self.account_id, intent["command_id"])
                if current["state"] in PENDING:
                    wires = self.journal.wires(self.account_id, intent["command_id"])
                    if getattr(exc, "submission_status", None) == "NOT_SENT" or not wires:
                        self.journal.finish(self.account_id, intent["command_id"], "REJECTED", result={"not_sent": True})
                        if isinstance(exc, ExchangeStateUnavailableError) and operation in {"close", "protection", "leverage"}:
                            self.pause_symbol(symbol, "Pre-HTTP состояние собственной команды не подтверждено")
                    else:
                        self._unknown(intent, "Результат операции после PREPARED неизвестен", order_id=current.get("order_id"))
                raise
            finally:
                context_scope.__exit__(None, None, None)
                self._local.intent = old
                self._local.wire_id = None

    def _replay(self, intent):
        if intent["state"] in {"FILLED", "RESOLVED"}:
            if intent["operation"] == "entry" and self.is_symbol_blocked(intent["symbol"]):
                raise ExchangeStateUnavailableError("Fill подтверждён, но символ/защита ещё заблокированы")
            self.last_execution_result = intent["result"]
            receipt = intent.get("result") or {}
            if intent["operation"] == "entry":
                return intent.get("order_id") if intent["state"] == "FILLED" else False
            if intent["operation"] == "close":
                if receipt.get("close_confirmed") is True and self.is_symbol_blocked(intent["symbol"]):
                    raise ExchangeStateUnavailableError("Close fill доказан, но безопасность/защита ещё не сверены")
                return receipt.get("close_confirmed") is True
            if intent["operation"] == "cancel":
                return receipt.get("cancelled") is True
            if intent["operation"] == "protection":
                self.verify_protection(intent["symbol"])
                self.journal.resolve_block(self.account_id, intent["symbol"], expected_reason="PROTECTION_PENDING")
                if self.is_symbol_blocked(intent["symbol"]):
                    raise ExchangeStateUnavailableError("Защита доказана, но символ остаётся приостановлен")
                return receipt.get("protection_confirmed") is True
            if intent["operation"] == "leverage":
                return receipt.get("leverage_confirmed") == intent["request"]["leverage"]
            return receipt
        if intent["state"] == "REJECTED":
            raise ExchangeAPIError("Команда уже отклонена; повторная отправка запрещена")
        self._unknown(intent, "Команда уже отправлялась; повтор запрещён, используйте recover/reconcile")

    def _positions(self):
        self.raw_client.invalidate_cache("positions")
        positions = _call(self.raw_client.get_positions, force_refresh=True)
        if not isinstance(positions, dict) or any(not isinstance(rows, list) for rows in positions.values()):
            raise ExchangeStateUnavailableError("Невалидный снимок позиций")
        return positions

    def _view(self, position, symbol):
        if isinstance(position, dict):
            pid = str(position.get("position_id", position.get("dealId", position.get("positionId", ""))))
            side = str(value(position.get("side", position.get("type", "")))).upper()
            quantity = position.get("size", position.get("amount"))
        else:
            pid, side, quantity = str(position.position_id), value(position.side), position.size
        side = {"BUY": "LONG", "SELL": "SHORT"}.get(side, side)
        if not pid or side not in {"LONG", "SHORT"}:
            raise ExchangeStateUnavailableError("Позиция без достоверного ID/направления")
        detail = getattr(self._raw, "_position_details", {}).get(pid, {})
        if "quantity" in detail:
            quantity = detail["quantity"]
        elif "positionAmt" in detail:
            quantity = abs(decimal(detail["positionAmt"]))
        elif "holdVol" in detail:
            quantity = decimal(detail["holdVol"]) * self.get_instrument_rules(symbol).contract_size
        return {"position_id": pid, "side": side, "quantity": str(decimal(quantity, "размер позиции", positive=True)), "symbol": canonical_symbol(symbol)}

    def _symbol_positions(self, positions, symbol):
        return [p for key, rows in positions.items() if canonical_symbol(key) == canonical_symbol(symbol) for p in rows]

    def _validated_owned(self, symbol, position_id=None, *, positions=None):
        positions = self._positions() if positions is None else positions
        owned = [r for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol)
                 if decimal(r["quantity"]) > 0 and (position_id is None or r["position_id"] == str(position_id))]
        views = {self._view(p, symbol)["position_id"]: (p, self._view(p, symbol)) for p in self._symbol_positions(positions, symbol)}
        result = []
        for row in owned:
            found = views.get(row["position_id"])
            if found is None or found[1]["side"] != row["side"] or decimal(found[1]["quantity"]) != decimal(row["quantity"]):
                self.pause_symbol(symbol, "Изменение размера/стороны/ID собственной позиции; ручное присвоение запрещено")
                raise ExchangeStateUnavailableError("Собственная позиция отличается от durable snapshot")
            result.append((row, found[0]))
        return result

    def owns_position(self, symbol, position_id):
        return any(r["position_id"] == str(position_id) and decimal(r["quantity"]) > 0
                   for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol))

    def get_position_execution_snapshot(self, symbol, position_id):
        rows = [r for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol) if r["position_id"] == str(position_id)]
        if len(rows) != 1:
            raise ExchangeStateUnavailableError("Нет однозначного snapshot собственной позиции")
        return {**copy.deepcopy(rows[0]), "protections": self.journal.protections(self.account_id, owner=self.owner_id, symbol=symbol)}

    def position_command_id(self, symbol, position_id, operation):
        """Повтор SCALP/monitor после рестарта привязан к lifecycle entry, не к текущему размеру."""
        rows = [r for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol) if r["position_id"] == str(position_id)]
        if len(rows) != 1 or not rows[0]["snapshot"].get("client_order_id"):
            raise ExchangeStateUnavailableError("Неизвестен lifecycle собственной позиции")
        token = f"{self.account_id}:{self.owner_id}:{canonical_symbol(symbol)}:{position_id}:{rows[0]['snapshot']['client_order_id']}:{operation}"
        return hashlib.sha256(token.encode()).hexdigest()

    def closure_confirmed(self, symbol, position_id):
        rows = self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol)
        return not self.is_symbol_blocked(symbol) and any(r["position_id"] == str(position_id) and decimal(r["quantity"]) == 0 for r in rows)

    def get_owned_positions(self, symbol=None):
        self._ensure_recovered()
        grid_symbols = {r["symbol"] for r in self.journal.intents(self.account_id, symbol=symbol, pending_only=True)
                        if r["owner"] == self.owner_id and r["operation"] in GRID_OPERATIONS}
        for key in grid_symbols:
            self.reconcile_grid(key)
        positions = self._positions()
        symbols = {r["symbol"] for r in self.journal.owned(self.account_id, owner=self.owner_id) if decimal(r["quantity"]) > 0}
        result = {}
        for key in symbols:
            if symbol is not None and canonical_symbol(symbol) != key:
                continue
            blocked = self.is_grid_symbol_blocked(key) if key in grid_symbols else self.is_symbol_blocked(key)
            if blocked:
                raise ExchangeStateUnavailableError("Символ заблокирован неразрешённой командой")
            rows = self._validated_owned(key, positions=positions)
            self.verify_protection(key)
            result[key] = [p for _, p in rows]
        return result

    def get_positions(self, **kwargs):
        # Общий снимок нужен лимитам; права на управление проверяются отдельным owned reader.
        return self._positions()

    def _query(self, intent):
        oid = intent.get("order_id")
        if hasattr(self._raw, "get_order"):
            return _call(self._raw.get_order, intent["symbol"], oid, client_order_id=None if oid else intent["client_id"])
        if hasattr(self._raw, "_query_external"):
            rules = self.get_instrument_rules(intent["symbol"])
            return self._raw._query_external(rules.exchange_symbol, intent["client_id"])
        raise UnsupportedCapabilityError("Клиент не предоставляет адресный order lookup")

    def _fill_view(self, order, intent):
        if canonical_symbol(order.symbol) != intent["symbol"]:
            raise ExchangeStateUnavailableError("Lookup вернул другой символ")
        raw = order.raw_data
        client_id = raw.get("clientOrderId", raw.get("clientOrderID", raw.get("externalOid")))
        if client_id != intent["client_id"] or (intent.get("order_id") and str(order.order_id) != intent["order_id"]):
            raise ExchangeStateUnavailableError("Lookup не подтверждает client/order ID")
        quantity, filled = decimal(order.quantity), decimal(order.filled_quantity)
        if "origQty" in raw:
            quantity, filled = decimal(raw["origQty"]), decimal(raw.get("executedQty", 0))
        elif "vol" in raw and "dealVol" in raw:
            multiplier = self.get_instrument_rules(intent["symbol"]).contract_size
            quantity, filled = decimal(raw["vol"]) * multiplier, decimal(raw["dealVol"]) * multiplier
        if self._grid_attached(intent):
            spec = intent["request"]
            if self.settings["exchange"] == "mexc":
                if (raw.get("side") != (1 if spec["owned_side"] == "LONG" else 3)
                        or raw.get("openType") != 1 or value(order.position_side) != spec["owned_side"]):
                    raise ExchangeStateUnavailableError("GRID attached: lookup не доказывает исходный opening side/isolated")
            elif (raw.get("positionSide") != spec["position_side"] or "executedQty" not in raw
                  or str(raw.get("reduceOnly", False)).lower() != "false"):
                raise ExchangeStateUnavailableError("GRID attached: lookup не доказывает сторону/entry fill")
        expected = decimal(intent["context"].get("expected_quantity", intent["request"].get("quantity")))
        previous = self.journal.get(self.account_id, intent["command_id"])
        proven_fill = decimal((previous.get("result") or {}).get("filled_quantity", 0))
        if quantity != expected or not proven_fill <= filled <= quantity or (order.status == OrderStatus.FILLED and filled != quantity):
            raise ExchangeStateUnavailableError("Объём/status lookup не совпадает с командой либо fill уменьшился")
        side = intent["request"].get("side")
        if side is not None and value(order.side) != side:
            raise ExchangeStateUnavailableError("Сторона lookup не совпадает с командой")
        if intent["operation"] in {"entry", "grid_entry", "grid_reduce"} and value(order.order_type) != intent["request"]["order_type"]:
            raise ExchangeStateUnavailableError("Тип lookup не совпадает с командой")
        if intent["operation"] == "close":
            if order.order_type != OrderType.MARKET or (raw.get("positionId") is not None and str(raw["positionId"]) != intent["request"]["position_id"]):
                raise ExchangeStateUnavailableError("Close lookup вернул другой тип/positionId")
        if intent["operation"] in GRID_OPERATIONS and not self._grid_native(intent):
            from decimal import ROUND_CEILING, ROUND_FLOOR
            step = self.get_instrument_rules(intent["symbol"]).price_step
            expected_price = (decimal(intent["request"]["price"]) / step).to_integral_value(
                rounding=ROUND_FLOOR if side == "BUY" else ROUND_CEILING) * step
            if decimal(raw.get("price", order.price)) != expected_price:
                raise ExchangeStateUnavailableError("GRID lookup вернул другую LIMIT цену")
        if intent["operation"] == "grid_reduce":
            if raw.get("positionId") is not None and str(raw["positionId"]) != intent["request"]["position_id"]:
                raise ExchangeStateUnavailableError("GRID reduce lookup вернул другой position ID")
            if self.settings["position_mode"] == "one_way":
                proven = str(raw.get("reduceOnly")).lower() == "true"
                if "reduceOnly" not in raw:
                    # Некоторые ответы MEXC не повторяют флаг; нужен сохранённый точный wire.
                    proven = any(str(w["event"].get("request_params", {}).get("reduceOnly")).lower() == "true"
                                 and w["event"].get("request_params", {}).get("externalOid") == intent["client_id"]
                                 for w in self.journal.wires(self.account_id, intent["command_id"]))
                if not proven:
                    raise ExchangeStateUnavailableError("GRID reduce lookup не подтверждает reduce-only")
        if raw.get("positionSide") is not None:
            expected_side = intent["request"]["owned_side"] if intent["operation"] in GRID_OPERATIONS else ("LONG" if intent["request"]["side"] == "BUY" else "SHORT") if intent["operation"] == "entry" else intent["context"]["before"]["side"]
            if raw["positionSide"] != (expected_side if self.settings["position_mode"] == "hedge" else "BOTH"):
                raise ExchangeStateUnavailableError("Lookup относится к другой стороне позиции")
        return filled

    def _restore_position(self, owned):
        if hasattr(self._raw, "register_owned_position") and decimal(owned["quantity"]) > 0:
            self._raw.register_owned_position(owned["symbol"], owned["position_id"], owned["quantity"], position_side=owned["side"])

    def _settle_entry(self, intent, order):
        filled = self._fill_view(order, intent)
        terminal = order.status in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
        if filled == 0 and terminal:
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", order_id=order.order_id, result={"filled_quantity": "0", "cancelled": True})
            return False
        if filled == 0:
            self.journal.finish(self.account_id, intent["command_id"], "ACK", order_id=order.order_id)
            return None
        before = intent["context"]["before"]
        direction = "LONG" if intent["request"]["side"] == "BUY" else "SHORT"
        targets = [p for p in self._symbol_positions(self._positions(), intent["symbol"]) if self._view(p, intent["symbol"])["side"] == direction]
        old = [p for p in before if p["side"] == direction]
        base = sum((decimal(p["quantity"]) for p in old), Decimal(0))
        if len(targets) != 1:
            self._unknown(intent, "Fill не связан с единственной позицией", order_id=order.order_id)
        current = self._view(targets[0], intent["symbol"])
        if decimal(current["quantity"]) != base + filled or (old and (len(old) != 1 or old[0]["position_id"] != current["position_id"])):
            self._unknown(intent, "Позиция изменена вне команды; автоматическое присвоение запрещено", order_id=order.order_id)
        previous_owned = next((r for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=intent["symbol"]) if r["position_id"] == current["position_id"] and decimal(r["quantity"]) > 0), None)
        opened_at = previous_owned["snapshot"].get("opened_at", time.time()) if previous_owned else time.time()
        owned = dict(current, owner=self.owner_id, snapshot={"entry_order_id": order.order_id, "client_order_id": intent["client_id"],
                     "opened_at": opened_at, "protection": {"sl": intent["request"].get("sl"), "tp": intent["request"].get("tp")}})
        complete = terminal and order.status == OrderStatus.FILLED and filled == decimal(intent["request"]["quantity"])
        result = {"order_id": str(order.order_id), "filled_quantity": str(filled), "average_price": str(order.average_price),
                  "position_id": current["position_id"], "terminal": terminal, "entry_complete": complete}
        self.journal.finish(self.account_id, intent["command_id"], "FILLED" if complete else "PARTIAL", result=result, order_id=order.order_id, ownership=[owned],
                                    pause_reason="PROTECTION_PENDING" if intent["request"].get("sl") is not None or intent["request"].get("tp") is not None else None)
        self._restore_position(owned)
        self.last_execution_result = result
        return True if complete else None

    def get_instrument_rules(self, symbol):
        rules = self.raw_client.get_instrument_rules(symbol)
        if rules is None or rules.quantity_step <= 0 or rules.contract_size <= 0:
            raise ExchangeStateUnavailableError("Нет точных торговых правил инструмента")
        return rules

    def place_order(self, symbol, side, quantity, order_type=OrderType.MARKET, price=None, sl=None, tp=None,
                    position_side=None, leverage=None, *, client_order_id=None, command_id=None):
        side = OrderSide(str(value(side)).upper())
        order_type = OrderType(str(value(order_type)).upper())
        if order_type not in {OrderType.MARKET, OrderType.LIMIT}:
            raise UnsupportedCapabilityError("Условные заявки создаются адресно через защиту")
        rules = self.get_instrument_rules(symbol)
        step = rules.quantity_step * rules.contract_size
        quantity = (decimal(quantity, "quantity", positive=True) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if quantity <= 0:
            raise ExchangeStateUnavailableError("Entry quantity меньше шага")
        for name, number in (("price", price), ("SL", sl), ("TP", tp)):
            if number is not None:
                decimal(number, name, positive=True)
        if price is not None and ((sl is not None and not (decimal(sl) < decimal(price) if side == OrderSide.BUY else decimal(sl) > decimal(price)))
                                  or (tp is not None and not (decimal(tp) > decimal(price) if side == OrderSide.BUY else decimal(tp) < decimal(price)))):
            raise ExchangeStateUnavailableError("Некорректные стороны SL/TP до entry")
        request = {"side": side.value, "quantity": str(quantity), "order_type": order_type.value,
                   "price": str(price) if price is not None else None, "sl": str(sl) if sl is not None else None,
                   "tp": str(tp) if tp is not None else None, "leverage": leverage,
                   "position_side": str(value(position_side)) if position_side is not None else None}
        direction = "LONG" if side == OrderSide.BUY else "SHORT"
        with self._operation(symbol, "entry", request, command_id=command_id or client_order_id, client_id=client_order_id) as (intent, fresh):
            if not fresh:
                return self._replay(intent)
            positions = self._positions()
            before = [self._view(p, symbol) for p in self._symbol_positions(positions, symbol)]
            for existing in before:
                if self.settings["position_mode"] == "one_way" or existing["side"] == direction:
                    owned = self._validated_owned(symbol, existing["position_id"], positions=positions)
                    if len(owned) != 1 or existing["side"] != direction:
                        raise UnsupportedCapabilityError("Вход затронул бы ручную/противоположную позицию")
                    planned = owned[0][0]["snapshot"].get("protection", {})
                    if any(planned.get(key) is not None and request.get(key) is None for key in ("sl", "tp")):
                        raise UnsupportedCapabilityError("Увеличение защищённой позиции требует явных SL/TP для нового полного объёма")
            self.journal.finish(self.account_id, intent["command_id"], "PREPARED",
                                context={"before": before, "expected_quantity": str(quantity)})
            intent = self.journal.get(self.account_id, intent["command_id"])
            params = inspect.signature(self.raw_client.place_order).parameters
            type_key = "order_type" if "order_type" in params else "type"
            position_key = "position_side" if "position_side" in params else "positionSide"
            oid = _call(self.raw_client.place_order, symbol=symbol, side=side, quantity=quantity,
                        price=price, sl=None, tp=None, leverage=leverage, client_order_id=intent["client_id"],
                        **{type_key: order_type if type_key == "order_type" else order_type.value, position_key: position_side})
            if not oid or isinstance(oid, bool):
                self._unknown(intent, "Entry не вернул адресный order ID")
            self.journal.finish(self.account_id, intent["command_id"], "ACK", order_id=oid)
            intent = self.journal.get(self.account_id, intent["command_id"])
            if order_type == OrderType.LIMIT:
                return str(oid)
            settled = self._settle_entry(intent, self._query(intent))
            if settled is not True:
                self._unknown(self.journal.get(self.account_id, intent["command_id"]), "Entry не исполнен полностью", state="PARTIAL" if settled is None and decimal((self.last_execution_result or {}).get("filled_quantity", 0)) > 0 else "UNKNOWN", order_id=str(oid))
        if sl is not None or tp is not None:
            self.set_sl_tp(symbol, PositionSide(direction), sl=sl, tp=tp, position_id=self.last_execution_result["position_id"], command_id=intent["command_id"] + ":protection", _safety=True)
        return str(oid)

    def _entry_for_order(self, symbol, order_id):
        records = [r for r in self.journal.intents(self.account_id, symbol=symbol) if r["owner"] == self.owner_id and r["operation"] == "entry" and r["order_id"] == str(order_id)]
        if len(records) != 1:
            raise UnsupportedCapabilityError("Нет доказанного собственного entry order")
        return records[0]

    def _protect_terminal_partial(self, intent, order):
        if order.status not in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
            return
        current = self.journal.get(self.account_id, intent["command_id"])
        if current["state"] != "PARTIAL" or not current["result"].get("terminal"):
            return
        spec = current["request"]
        if spec.get("sl") is None and spec.get("tp") is None:
            return
        command_id = current["command_id"] + ":partial-protection"
        prior = self.journal.get(self.account_id, command_id)
        if prior and prior["state"] == "RESOLVED":
            self.verify_protection(current["symbol"])
            return
        self.set_sl_tp(current["symbol"], PositionSide.LONG if spec["side"] == "BUY" else PositionSide.SHORT,
                       sl=spec["sl"], tp=spec["tp"], position_id=current["result"]["position_id"], command_id=command_id,
                       _safety=True, _allow_pending=(current["command_id"],))

    def wait_for_entry(self, symbol, order_id, *, timeout=None, cancel_on_timeout=True):
        intent = self._entry_for_order(symbol, order_id)
        if intent["state"] == "FILLED":
            spec = intent["request"]
            if spec.get("sl") is not None or spec.get("tp") is not None:
                result = intent["result"]
                self.set_sl_tp(symbol, PositionSide.LONG if spec["side"] == "BUY" else PositionSide.SHORT,
                               sl=spec["sl"], tp=spec["tp"], position_id=result["position_id"],
                               command_id=intent["command_id"] + ":protection", _safety=True)
            return True
        attempts = max(1, self.settings["protection"]["recovery_attempts"])
        duration = min(float(timeout if timeout is not None else self.settings["protection"]["verification_timeout_seconds"]), self.settings["protection"]["verification_timeout_seconds"])
        for attempt in range(attempts):
            with self._lock, self.journal.symbol_lock(self.account_id, symbol):
                order = self._query(intent)
                settled = self._settle_entry(intent, order)
            if settled is None and order.status in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                self._protect_terminal_partial(intent, order)
                self._unknown(self.journal.get(self.account_id, intent["command_id"]), "Терминальный partial fill: собственный объём сохранён, команда не исполнена полностью", state="PARTIAL", order_id=str(order_id))
            if settled is True:
                spec = intent["request"]
                if spec.get("sl") is not None or spec.get("tp") is not None:
                    self.set_sl_tp(symbol, PositionSide.LONG if spec["side"] == "BUY" else PositionSide.SHORT,
                                   sl=spec["sl"], tp=spec["tp"], position_id=self.last_execution_result["position_id"], command_id=intent["command_id"] + ":protection", _safety=True)
                return True
            if settled is False:
                return False
            if attempt < attempts - 1:
                time.sleep(max(0, duration) / max(1, attempts - 1))
        if cancel_on_timeout:
            self.cancel_order(symbol, order_id, command_id=intent["command_id"] + ":cancel")
            intent = self.journal.get(self.account_id, intent["command_id"])
            order = self._query(intent)
            settled = self._settle_entry(intent, order)
            if settled is False:
                return False
            if settled is True:
                return self.wait_for_entry(symbol, order_id, timeout=0, cancel_on_timeout=False)
            self._protect_terminal_partial(intent, order)
        self._unknown(self.journal.get(self.account_id, intent["command_id"]), "LIMIT pending/partial; владение сохраняется", state="PARTIAL" if decimal((self.last_execution_result or {}).get("filled_quantity", 0)) > 0 else "UNKNOWN", order_id=str(order_id))

    def _settle_close(self, intent, order):
        owned = intent["context"]["before"]
        quantity = decimal(intent["context"]["expected_quantity"])
        checked = copy.deepcopy(intent)
        checked["request"]["side"] = "SELL" if owned["side"] == "LONG" else "BUY"
        filled = self._fill_view(order, checked)
        terminal = order.status in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
        if terminal and filled == 0:
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", order_id=order.order_id,
                                result={"filled_quantity": "0", "close_confirmed": False})
            return False
        if filled == 0:
            self._unknown(intent, "Close принят, но fill отсутствует", order_id=order.order_id)
        positions = self._symbol_positions(self._positions(), intent["symbol"])
        remaining = [self._view(p, intent["symbol"]) for p in positions if self._view(p, intent["symbol"])["position_id"] == owned["position_id"]]
        after = decimal(remaining[0]["quantity"]) if len(remaining) == 1 else Decimal(0) if not remaining else Decimal(-1)
        if (after != decimal(owned["quantity"]) - filled or (remaining and remaining[0]["side"] != owned["side"])
                or (not remaining and any(self._view(p, intent["symbol"])["side"] == owned["side"] for p in positions))):
            self._unknown(intent, "Close пересечён ручным изменением размера/стороны/ID", order_id=order.order_id)
        updated = dict(owned, quantity=str(after))
        complete = order.status == OrderStatus.FILLED and filled == quantity
        result = {"order_id": str(order.order_id), "filled_quantity": str(filled), "position_id": owned["position_id"], "remaining_quantity": str(after), "close_confirmed": complete}
        has_protection = any(r["position_id"] == owned["position_id"] for r in self.journal.protections(self.account_id, owner=self.owner_id, symbol=intent["symbol"]))
        parent_id = intent["request"].get("native_entry_command")
        if parent_id and complete and after == 0:
            parent = self.journal.get(self.account_id, parent_id)
            if (not parent or not self._grid_native(parent) or parent["owner"] != self.owner_id
                    or str(parent["order_id"]) != str(owned["snapshot"].get("entry_order_id"))):
                raise ExchangeStateUnavailableError("GRID native: close не связан с собственным entry")
            parent_result = copy.deepcopy(parent["result"])
            parent_result["native_close_command"] = intent["command_id"]
            parent_result.setdefault("attachment", {}).update(closed_quantity=parent_result["filled_quantity"],
                confirmed=False, state="CLOSED", cleanup_pending=True)
            # Close, нулевой owned и связь parent коммитятся вместе — без окна resurrection.
            self.journal.finish_many(self.account_id, [
                dict(command_id=intent["command_id"], state="FILLED", result=result, order_id=str(order.order_id)),
                dict(command_id=parent_id, state=parent["state"], result=parent_result)], ownership=[updated],
                block=(intent["symbol"], "GRID_ATTACHED_PROTECTION_UNKNOWN"))
        else:
            self.journal.finish(self.account_id, intent["command_id"], "FILLED" if complete else "PARTIAL", result=result, order_id=order.order_id,
                                ownership=[updated], pause_reason=(intent["context"].get("prior_block") or "CLOSE_PROTECTION_PENDING") if has_protection else None)
        self.last_execution_result = result
        return complete

    def close_position(self, symbol, position_id, percentage=1.0, *, client_order_id=None, command_id=None, _safety=False, _allow_pending=(), _native_entry=None):
        fraction = decimal(percentage, "percentage", positive=True)
        if fraction > 1:
            raise ExchangeStateUnavailableError("percentage должен быть в (0,1]")
        native_owned = [r for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol)
                        if r["position_id"] == str(position_id) and r["snapshot"].get("grid_native")]
        if native_owned and not _native_entry and not _safety:
            self.reconcile_grid(symbol)
            parents = [r for r in self._grid_records(symbol, pending=False) if self._grid_native(r)
                       and str(r.get("order_id")) == str(native_owned[0]["snapshot"].get("entry_order_id"))]
            _native_entry = parents[0]["command_id"] if len(parents) == 1 else None
        request = {"position_id": str(position_id), "percentage": str(fraction)}
        if _native_entry:
            request["native_entry_command"] = _native_entry
        with self._operation(symbol, "close", request, command_id=command_id or client_order_id, client_id=client_order_id, allow_pending=_allow_pending, safety=_safety) as (intent, fresh):
            if not fresh:
                return bool(self._replay(intent))
            candidates = self._validated_owned(symbol, position_id)
            if len(candidates) != 1:
                raise UnsupportedCapabilityError("Нет доказанного собственного объёма для close")
            owned, position = candidates[0]
            rules = self.get_instrument_rules(symbol)
            step = rules.quantity_step * rules.contract_size
            quantity = (decimal(owned["quantity"]) * fraction / step).to_integral_value(rounding=ROUND_FLOOR) * step
            if quantity <= 0:
                raise ExchangeStateUnavailableError("Close quantity меньше шага")
            context = {"before": owned, "expected_quantity": str(quantity),
                       "prior_block": self.journal.blocked(self.account_id, symbol, ignore=(intent["command_id"], *_allow_pending))}
            self.journal.finish(self.account_id, intent["command_id"], "PREPARED", context=context)
            self._restore_position(owned)
            result = _call(self.raw_client.close_position, symbol, str(position_id), percentage=fraction, client_order_id=intent["client_id"])
            if result is not True:
                self._unknown(intent, "ACK close не является подтверждённым исполнением")
            intent = self.journal.get(self.account_id, intent["command_id"])
            confirmed = self._settle_close(intent, self._query(intent))
            if not confirmed:
                current = self.journal.get(self.account_id, intent["command_id"])
                if current["state"] == "RESOLVED":
                    self.last_execution_result = current["result"]
                    return False
                self._unknown(intent, "Close не исполнен полностью", state="PARTIAL")
            remaining = decimal(self.last_execution_result["remaining_quantity"])
            if remaining > 0:
                self._refresh_bingx_protection(symbol, position_id)
                self.verify_protection(symbol)
            else:
                self._verify_closed_protection(symbol, position_id)
            if remaining == 0 and _native_entry:
                try:
                    self._grid_reconcile_locked(symbol)
                except ExchangeError:
                    self.journal.pause(self.account_id, symbol, "GRID_ATTACHED_PROTECTION_UNKNOWN", preserve_existing=True)
            self.journal.resolve_block(self.account_id, symbol, expected_reason="CLOSE_PROTECTION_PENDING")
            return True

    def _refresh_bingx_protection(self, symbol, position_id):
        if not hasattr(self._raw, "_owned_order_positions"):
            return
        for order_id, pid in list(self._raw._owned_order_positions.items()):
            if str(pid) != str(position_id):
                continue
            order = self._raw.get_order(symbol, order_id)
            if order.status in {OrderStatus.CANCELED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                self.journal.remove_protection(self.account_id, order_id)
                continue
            if order.status != OrderStatus.NEW:
                raise ExchangeStateUnavailableError("Защита pending/partial; очистка владения запрещена")
            raw = order.raw_data
            params = {k: raw[k] for k in ("symbol", "side", "positionSide", "type", "stopPrice", "workingType", "reduceOnly", "positionId") if k in raw}
            params["quantity"] = str(raw.get("origQty", raw.get("quantity")))
            self._raw._verify_protection(params, order_id)
            self.journal.save_protection(self.account_id, self.owner_id, symbol, order_id, position_id, {"request_params": params})

    def _verify_closed_protection(self, symbol, position_id):
        rows = [r for r in self.journal.protections(self.account_id, owner=self.owner_id, symbol=symbol) if r["position_id"] == str(position_id)]
        for row in rows:
            if hasattr(self._raw, "get_order"):
                active = self._raw.get_order(symbol, row["order_id"]).status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL}
            else:
                rules = self.get_instrument_rules(symbol)
                active = any(str(r.get("id")) == row["order_id"] for r in self._raw._get_position_stop_orders(rules.exchange_symbol, position_id))
            if active:
                self.pause_symbol(symbol, "После close собственная защита ещё активна")
                raise ExchangeStateUnavailableError("Close fill подтверждён, но отмена защиты не подтверждена")
            self.journal.remove_protection(self.account_id, row["order_id"])

    def _capture_protection(self, symbol, position_id, command_id):
        if hasattr(self._raw, "get_owned_protection_snapshots"):
            for snapshot in self._raw.get_owned_protection_snapshots():
                if str(snapshot.get("positionId")) == str(position_id):
                    self.journal.save_protection(self.account_id, self.owner_id, symbol, snapshot["id"], position_id, snapshot)
            return
        for wire in self.journal.wires(self.account_id, command_id):
            event = wire["event"]
            params = event.get("request_params", {})
            if event.get("order_id") and params.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                order = self._raw.get_order(symbol, event["order_id"])
                if order.status == OrderStatus.NEW:
                    clean = {k: v for k, v in params.items() if k not in {"cancelOrderId", "cancelReplaceMode"}}
                    self.journal.save_protection(self.account_id, self.owner_id, symbol, order.order_id, position_id, {"request_params": clean})
                old_id = event.get("prepared_request_params", params).get("cancelOrderId")
                if old_id:
                    self.journal.remove_protection(self.account_id, old_id)

    def set_sl_tp(self, symbol, position_side, sl=None, tp=None, quantity=None, *, position_id=None, command_id=None, _safety=False, _allow_pending=()):
        self._ensure_recovered()
        candidates = self._validated_owned(symbol, position_id)
        side = str(value(position_side)).upper()
        candidates = [(r, p) for r, p in candidates if side == "BOTH" or r["side"] == side]
        if len(candidates) != 1:
            raise UnsupportedCapabilityError("Защита требует единственную доказанную собственную позицию")
        owned, _ = candidates[0]
        if quantity is not None and decimal(quantity) != decimal(owned["quantity"]):
            raise UnsupportedCapabilityError("Защита должна покрывать точный собственный объём")
        request = {"position_id": owned["position_id"], "side": owned["side"], "sl": str(sl) if sl is not None else None, "tp": str(tp) if tp is not None else None}
        try:
            with self._operation(symbol, "protection", request, command_id=command_id, safety=_safety, allow_pending=_allow_pending,
                                 context={"expected_quantity": owned["quantity"], "before": owned}) as (intent, fresh):
                if not fresh:
                    return bool(self._replay(intent))
                self._restore_position(owned)
                result = _call(self.raw_client.set_sl_tp, symbol, PositionSide(owned["side"]), sl=sl, tp=tp,
                               position_id=owned["position_id"], quantity=decimal(owned["quantity"]))
                if result is not True or getattr(self.raw_client, "last_protection_confirmed", False) is not True:
                    self._unknown(intent, "Полная защита не подтверждена readback")
                self._capture_protection(symbol, owned["position_id"], intent["command_id"])
                self._refresh_bingx_protection(symbol, owned["position_id"])
                protected = self.journal.protections(self.account_id, owner=self.owner_id, symbol=symbol)
                if not any(row["position_id"] == owned["position_id"] for row in protected):
                    raise ExchangeStateUnavailableError("Защита совпала с ручной, но владение не доказано")
                self.verify_protection(symbol, _apply_policy=False)
                snapshot = copy.deepcopy(owned.get("snapshot") or {})
                planned = snapshot.setdefault("protection", {})
                planned.update({k: v for k, v in request.items() if k in {"sl", "tp"} and v is not None})
                self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"protection_confirmed": True},
                                    ownership=[dict(owned, snapshot=snapshot)])
                self.journal.resolve_block(self.account_id, symbol, expected_reason="PROTECTION_PENDING")
                return True
        except ExchangeError as exc:
            self._protection_failure(symbol, owned["position_id"], exc)
            raise

    def _protection_failure(self, symbol, position_id, cause):
        self.pause_symbol(symbol, "PROTECTION_FAILURE")
        cause.failure_policy = self.settings["protection"]["failure_policy"]
        if cause.failure_policy != "close":
            return
        pending = self.journal.intents(self.account_id, symbol=symbol, pending_only=True)
        if any(r["owner"] != self.owner_id or r["operation"] != "protection" for r in pending):
            return
        # UNKNOWN entry/close не допускает flatten: точный объём и отсутствие новых fills обязательны.
        if len(self._validated_owned(symbol, position_id)) != 1:
            return
        try:
            token = pending[-1]["command_id"] if pending else f"{position_id}:external-protection"
            cause.emergency_close_confirmed = self.close_position(symbol, position_id, command_id=token + ":emergency-close",
                                                                  _safety=True, _allow_pending=tuple(r["command_id"] for r in pending))
        except ExchangeError:
            cause.emergency_close_confirmed = False

    def _owned_order(self, symbol, order_id):
        for intent in self.journal.intents(self.account_id, symbol=symbol):
            if intent["owner"] == self.owner_id and intent["operation"] in {"entry", "grid_entry", "grid_reduce"} and intent["order_id"] == str(order_id):
                return intent
        raise UnsupportedCapabilityError("Отмена чужой/недоказанной заявки запрещена")

    def cancel_order(self, symbol, order_id, *, command_id=None):
        target = self._owned_order(symbol, order_id)
        if self._grid_attached(target):
            raise UnsupportedCapabilityError("GRID attached: cancel entry не гарантирует сохранение защиты при concurrent partial fill; доступна GET-сверка внешней отмены")
        request = {"order_id": str(order_id), "entry_command_id": target["command_id"]}
        allowed = (target["command_id"], *self.journal.grid_ack_ids(self.account_id, self.owner_id, symbol)) if target["operation"] in GRID_OPERATIONS else (target["command_id"],)
        with self._operation(symbol, "cancel", request, command_id=command_id, allow_pending=allowed, safety=True) as (intent, fresh):
            if not fresh:
                return bool(self._replay(intent))
            self._restore_order(target)
            try:
                self.raw_client.cancel_order(symbol, str(order_id))
            except UnknownOrderStateError:
                pass
            order = self._query(target)
            if order.status not in {OrderStatus.CANCELED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                self._unknown(intent, "Конечное состояние отмены не подтверждено", order_id=str(order_id))
            if target["operation"] in GRID_OPERATIONS:
                self._grid_reconcile_locked(symbol)
            elif target["operation"] == "entry":
                self._settle_entry(target, order)
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", order_id=order_id, result={"cancelled": order.status == OrderStatus.CANCELED})
            return order.status == OrderStatus.CANCELED

    def cancel_all_orders(self, symbol):
        # Никогда не используем биржевой bulk cancel.
        active = {str(getattr(o, "order_id", o.get("orderId") if isinstance(o, dict) else "")) for o in self.raw_client.get_open_orders(symbol)}
        for intent in self.journal.intents(self.account_id, symbol=symbol):
            if intent["owner"] == self.owner_id and intent.get("order_id") in active and intent["operation"] in {"entry", "grid_entry", "grid_reduce"}:
                self.cancel_order(symbol, intent["order_id"], command_id=intent["command_id"] + ":cancel")
        return True

    def set_leverage(self, symbol, leverage, position_side=PositionSide.BOTH, *, command_id=None):
        lev = decimal(leverage, "leverage", positive=True)
        if lev != lev.to_integral_value():
            raise ExchangeStateUnavailableError("Плечо должно быть целым")
        leverage = int(lev)
        request = {"leverage": leverage, "position_side": str(value(position_side))}
        with self._operation(symbol, "leverage", request, command_id=command_id) as (intent, fresh):
            if not fresh:
                return bool(self._replay(intent))
            existing = self._symbol_positions(self._positions(), symbol)
            if existing and len(self._validated_owned(symbol)) != len(existing):
                raise UnsupportedCapabilityError("Плечо ручной позиции не изменяется")
            self.journal.finish(self.account_id, intent["command_id"], "PREPARED",
                                context={"before": [self._view(p, symbol) for p in existing]})
            result = self.raw_client.set_leverage(symbol, leverage, position_side)
            if result is not True:
                self._unknown(intent, "Плечо не подтверждено")
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"leverage_confirmed": leverage})
            return True

    def _restore_order(self, intent):
        if not intent.get("order_id"):
            return
        if hasattr(self._raw, "register_owned_order"):
            self._raw.register_owned_order(intent["symbol"], intent["order_id"], client_order_id=intent["client_id"])
        elif hasattr(self._raw, "restore_order_ownership"):
            self._raw.restore_order_ownership(intent["symbol"], intent["order_id"])

    def verify_protection(self, symbol, *, _apply_policy=True):
        attached_owned = [r for r in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol)
                          if r["snapshot"].get("grid_attached") and decimal(r["quantity"]) > 0]
        if attached_owned:
            self.reconcile_grid(symbol)
        protections = self.journal.protections(self.account_id, owner=self.owner_id, symbol=symbol)
        for owned in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol):
            if decimal(owned["quantity"]) <= 0:
                continue
            if owned["snapshot"].get("grid_attached"):
                records = [r for r in self._grid_records(symbol, pending=False) if self._grid_attached(r)
                           and (r.get("result") or {}).get("attachment", {}).get("position_id") == owned["position_id"]]
                covered = Decimal(0)
                for record in records:
                    receipt = record["result"]["attachment"]
                    remaining = decimal(record["result"]["filled_quantity"]) - decimal(receipt.get("closed_quantity", 0))
                    if remaining and receipt.get("confirmed") is not True:
                        raise ExchangeStateUnavailableError("GRID attached: активное покрытие собственного остатка не доказано")
                    covered += remaining
                if covered != decimal(owned["quantity"]):
                    raise ExchangeStateUnavailableError("GRID attached: сумма покрытий не равна собственному объёму")
                continue
            rows = [r for r in protections if r["position_id"] == owned["position_id"]]
            planned = owned["snapshot"].get("protection", {})
            if hasattr(self._raw, "register_owned_protection"):
                totals = {kind: sum((decimal(r["snapshot"]["request_params"]["quantity"]) for r in rows if r["snapshot"]["request_params"].get("type") == kind), Decimal(0))
                          for kind in ("STOP_MARKET", "TAKE_PROFIT_MARKET")}
                valid = all((totals[kind] == decimal(owned["quantity"]) if planned.get(key) is not None else totals[kind] in {0, decimal(owned["quantity"])})
                            for kind, key in (("STOP_MARKET", "sl"), ("TAKE_PROFIT_MARKET", "tp")))
            else:
                valid = all(any(decimal(r["snapshot"].get(field, 0)) > 0 for r in rows)
                            for key, field in (("sl", "stopLossPrice"), ("tp", "takeProfitPrice")) if planned.get(key) is not None)
            if not valid:
                exc = ExchangeStateUnavailableError("Нет durable доказательства полного покрытия SL/TP")
                if _apply_policy:
                    self._protection_failure(symbol, owned["position_id"], exc)
                raise exc
        for row in protections:
            try:
                if hasattr(self._raw, "register_owned_protection"):
                    self._raw.register_owned_protection(symbol, row["order_id"], request_params=row["snapshot"]["request_params"], position_id=row["position_id"])
                elif hasattr(self._raw, "restore_protection_ownership"):
                    self._raw.restore_protection_ownership(row["snapshot"])
                    rules = self.get_instrument_rules(symbol)
                    snapshots = self._raw._get_position_stop_orders(rules.exchange_symbol, row["position_id"])
                    found = next((s for s in snapshots if str(s.get("id")) == row["order_id"]), None)
                    if found is None or self._raw._stop_fingerprint(found) != self._raw._stop_fingerprint(row["snapshot"]):
                        raise ExchangeStateUnavailableError("Защита отменена/изменена вне команды")
                    owned = self._validated_owned(symbol, row["position_id"])
                    if len(owned) != 1 or not self._raw._coverage(owned[0][1], [found], found.get("stopLossPrice") or None, found.get("takeProfitPrice") or None):
                        raise ExchangeStateUnavailableError("Защита не покрывает точный собственный объём")
            except ExchangeError as exc:
                if _apply_policy:
                    self._protection_failure(symbol, row["position_id"], exc)
                raise
        return True

    def _ensure_recovered(self):
        if not self._recovered:
            self.recover()

    def _recover_generic(self, intent):
        if intent["operation"] == "grid_native_cleanup":
            self._recover_native_cleanup(intent)
            return
        if intent["operation"] == "close" and intent["context"].get("before"):
            self._settle_close(intent, self._query(intent))
            if self.journal.get(self.account_id, intent["command_id"])["state"] == "FILLED":
                pid = intent["request"]["position_id"]
                if decimal(self.last_execution_result["remaining_quantity"]) == 0:
                    self._verify_closed_protection(intent["symbol"], pid)
                    if intent["request"].get("native_entry_command"):
                        self._grid_reconcile_locked(intent["symbol"])
                else:
                    self._capture_protection(intent["symbol"], pid, intent["command_id"])
                    self.verify_protection(intent["symbol"], _apply_policy=False)
                self.journal.resolve_block(self.account_id, intent["symbol"], expected_reason="CLOSE_PROTECTION_PENDING")
            return
        if intent["operation"] == "cancel":
            target = self.journal.get(self.account_id, intent["request"]["entry_command_id"])
            order = self._query(target)
            if order.status not in {OrderStatus.CANCELED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
                self._unknown(intent, "Cancel после рестарта ещё не терминальный")
            if target["operation"] in GRID_OPERATIONS:
                self._grid_reconcile_locked(intent["symbol"])
            elif target["operation"] == "entry":
                self._settle_entry(target, order)
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"cancelled": order.status == OrderStatus.CANCELED})
            return
        if intent["operation"] == "protection" and hasattr(self._raw, "restore_protection_ownership"):
            pid = intent["request"]["position_id"]
            owned = self._validated_owned(intent["symbol"], pid)
            if len(owned) != 1:
                raise ExchangeStateUnavailableError("MEXC protection: собственный объём не доказан")
            rules = self.get_instrument_rules(intent["symbol"])
            plans = self._raw._get_position_stop_orders(rules.exchange_symbol, pid)
            proven_ids = set()
            old = {r["order_id"]: r["snapshot"] for r in self.journal.protections(self.account_id, owner=self.owner_id, symbol=intent["symbol"]) if r["position_id"] == pid}
            for wire in self.journal.wires(self.account_id, intent["command_id"]):
                event, params = wire["event"], wire["event"].get("request_params", {})
                if event.get("status") != "ACKNOWLEDGED":
                    continue
                if event.get("endpoint", "").endswith("/stoporder/place") and event.get("order_id"):
                    proven_ids.add(str(event["order_id"]))
                elif event.get("endpoint", "").endswith("/stoporder/change_plan_price") and str(params.get("stopPlanOrderId")) in old:
                    proven_ids.add(str(params["stopPlanOrderId"]))
            if len(proven_ids) != 1:
                self._unknown(intent, "MEXC protection: точный ACK ID не доказан; похожие ручные планы не присваиваются")
            sid = next(iter(proven_ids))
            found = next((r for r in plans if str(r.get("id")) == sid), None)
            previous = old.get(sid, {})
            sl = intent["request"].get("sl") or previous.get("stopLossPrice") or None
            tp = intent["request"].get("tp") or previous.get("takeProfitPrice") or None
            if (found is None or found.get("symbol") != rules.exchange_symbol or str(found.get("positionId")) != pid
                    or not self._raw._coverage(owned[0][1], [found], sl, tp)):
                self._unknown(intent, "MEXC protection: ACK не подтверждён точным readback")
            self._raw.restore_protection_ownership(found)
            self.journal.save_protection(self.account_id, self.owner_id, intent["symbol"], sid, pid, found)
            self.verify_protection(intent["symbol"], _apply_policy=False)
            snapshot = copy.deepcopy(owned[0][0].get("snapshot") or {})
            snapshot.setdefault("protection", {}).update({k: v for k, v in intent["request"].items() if k in {"sl", "tp"} and v is not None})
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"protection_confirmed": True}, ownership=[dict(owned[0][0], snapshot=snapshot)])
            self.journal.resolve_block(self.account_id, intent["symbol"], expected_reason="PROTECTION_FAILURE")
            self.journal.resolve_block(self.account_id, intent["symbol"], expected_reason="PROTECTION_PENDING")
            return
        if intent["operation"] == "protection" and hasattr(self._raw, "register_owned_protection"):
            pid = intent["request"]["position_id"]
            owned = self._validated_owned(intent["symbol"], pid)
            if len(owned) != 1:
                raise ExchangeStateUnavailableError("Владение защиты при recovery не доказано")
            self._restore_position(owned[0][0])
            known = []
            for wire in self.journal.wires(self.account_id, intent["command_id"]):
                event, params = wire["event"], wire["event"].get("request_params", {})
                if event.get("order_id") and params.get("type") in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                    clean = {k: v for k, v in params.items() if k not in {"cancelOrderId", "cancelReplaceMode"}}
                    self._raw.register_owned_protection(intent["symbol"], event["order_id"], request_params=clean, position_id=pid)
                    self.journal.save_protection(self.account_id, self.owner_id, intent["symbol"], event["order_id"], pid, {"request_params": clean})
                    known.append(params["type"])
                    old = event.get("prepared_request_params", {}).get("cancelOrderId")
                    if old:
                        self.journal.remove_protection(self.account_id, old)
            needed = {kind for kind, key in (("STOP_MARKET", "sl"), ("TAKE_PROFIT_MARKET", "tp")) if intent["request"].get(key) is not None}
            if not needed.issubset(known):
                self._unknown(intent, "Не все conditional IDs сохранены; поиск похожей ручной защиты запрещён")
            self.verify_protection(intent["symbol"], _apply_policy=False)
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"protection_confirmed": True})
            self.journal.resolve_block(self.account_id, intent["symbol"], expected_reason="PROTECTION_FAILURE")
            self.journal.resolve_block(self.account_id, intent["symbol"], expected_reason="PROTECTION_PENDING")
            return
        if intent["operation"] == "leverage" and hasattr(self._raw, "_query_external"):
            rules = self.get_instrument_rules(intent["symbol"])
            before = intent["context"].get("before", [])
            side = intent["request"]["position_side"]
            if before:
                positions = self._symbol_positions(self._positions(), intent["symbol"])
                observed = [self._view(p, intent["symbol"]) for p in positions]
                if sorted(observed, key=lambda p: p["position_id"]) != sorted(before, key=lambda p: p["position_id"]):
                    self._unknown(intent, "MEXC leverage: позиция изменена вне команды")
                matches = [p for p in positions if side == "BOTH" or value(p.side) == side]
                verified = len(matches) == 1 and decimal(matches[0].leverage) == decimal(intent["request"]["leverage"])
            else:
                rows = self._raw._data(self._raw._transport.request("GET", "/api/v1/private/position/leverage", {"symbol": rules.exchange_symbol}, private=True, max_attempts=1))
                if not isinstance(rows, list):
                    raise ExchangeStateUnavailableError("MEXC leverage: неверная схема readback")
                matches = [r for r in rows if r.get("openType") == 1 and r.get("positionType") == (1 if side == "LONG" else 2)]
                verified = side in {"LONG", "SHORT"} and len(matches) == 1 and decimal(matches[0].get("leverage")) == decimal(intent["request"]["leverage"])
            if not verified:
                self._unknown(intent, "MEXC leverage: readback не совпал")
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"leverage_confirmed": intent["request"]["leverage"]})
            return
        if intent["operation"] == "leverage" and hasattr(self._raw, "_make_request"):
            params = {"symbol": self._raw._format_symbol(intent["symbol"])}
            data = self._raw._data(self._raw._make_request("get", "/openApi/swap/v2/trade/leverage", params))
            side = intent["request"]["position_side"]
            keys = ("longLeverage", "shortLeverage") if side == "BOTH" else (("longLeverage",) if side == "LONG" else ("shortLeverage",))
            if not all(decimal(data.get(key)) == decimal(intent["request"]["leverage"]) for key in keys):
                self._unknown(intent, "Leverage readback не совпал")
            self.journal.finish(self.account_id, intent["command_id"], "RESOLVED", result={"leverage_confirmed": intent["request"]["leverage"]})
            return
        self.journal.finish(self.account_id, intent["command_id"], "UNKNOWN")

    def _clear_reconciled_raw_uncertainty(self, symbol):
        """Raw UNKNOWN снимается только для точно связанных собственных durable wires."""
        if not self._raw_symbol_uncertain(symbol) or not isinstance(getattr(self._raw, "_uncertain_requests", None), dict):
            return
        with self.journal.symbol_lock(self.account_id, symbol), self._raw._trade_lock:
            if self.journal.blocked(self.account_id, symbol):
                return
            key = self._raw._format_symbol(symbol)
            evidence = self._raw._uncertain_requests.get(key)
            if not evidence:
                # Голый raw-флаг или UNKNOWN вне журнала не разрешается историческим fill.
                return
            intents = self.journal.intents(self.account_id, symbol=symbol)
            for unknown in evidence:
                if not unknown.get("submission_id"):
                    return
                matches = []
                for intent in intents:
                    for wire in self.journal.wires(self.account_id, intent["command_id"]):
                        event = wire["event"]
                        params = event.get("request_params", {})
                        prepared = event.get("prepared_request_params", params)
                        oid = unknown["order_id"]
                        if (event.get("submission_id") == unknown["submission_id"]
                                and unknown["request_params"] in (params, prepared)
                                and (oid is None or oid == str(event.get("order_id", params.get("orderId", ""))))):
                            matches.append(intent)
                            break
                if (len(matches) != 1 or matches[0]["owner"] != self.owner_id
                        or matches[0]["state"] not in {"FILLED", "RESOLVED"} or not matches[0].get("result")):
                    return
            # Проверяем весь символ: чужая сторона, дубликаты ID и лишний объём не присваиваются.
            owned = [r for r in self.journal.owned(self.account_id, symbol=symbol) if decimal(r["quantity"]) > 0]
            if any(r["owner"] != self.owner_id for r in owned):
                return
            positions = self._positions()
            views = [self._view(p, symbol) for p in self._symbol_positions(positions, symbol)]
            if len(views) != len(owned) or len({p["position_id"] for p in views}) != len(views):
                return
            self._validated_owned(symbol, positions=positions)
            for row in owned:
                self._restore_position(row)
            self.verify_protection(symbol, _apply_policy=False)
            for row in self.journal.owned(self.account_id, owner=self.owner_id, symbol=symbol):
                if decimal(row["quantity"]) == 0:
                    self._verify_closed_protection(symbol, row["position_id"])
            # Последнее чтение и повторная проверка blocks/pending — уже под обоими lock.
            final = self._positions()
            final_views = [self._view(p, symbol) for p in self._symbol_positions(final, symbol)]
            if sorted(final_views, key=lambda p: p["position_id"]) != sorted(views, key=lambda p: p["position_id"]):
                return
            if self.journal.blocked(self.account_id, symbol) or self.journal.intents(self.account_id, symbol=symbol, pending_only=True):
                return
            self._raw._uncertain_symbols.discard(key)
            self._raw._uncertain_requests.pop(key, None)

    def reconcile_pending(self, symbol=None):
        """Ограниченная read-only сверка для worker/grid, без отправки новой мутации."""
        return self.recover(symbol=symbol)

    def recover(self, *, symbol=None):
        """Только адресные чтения и восстановление доказанного владения, НИКОГДА trade resend."""
        with self._lock:
            self._recovered = True
            # Native close/cleanup читаются до entry: позиция уже могла стать flat.
            for pending in self.journal.intents(self.account_id, symbol=symbol, pending_only=True):
                if (pending["owner"] == self.owner_id and (pending["operation"] == "grid_native_cleanup"
                        or pending["operation"] == "close" and pending["request"].get("native_entry_command"))):
                    try:
                        with self.journal.symbol_lock(self.account_id, pending["symbol"]):
                            if pending["state"] == "PREPARED" and not self.journal.wires(self.account_id, pending["command_id"]):
                                self.journal.finish(self.account_id, pending["command_id"], "REJECTED", result={"not_sent": True})
                            else:
                                self._recover_generic(pending)
                    except ExchangeError:
                        pass
            grid_symbols = {r["symbol"] for r in self.journal.intents(self.account_id, symbol=symbol)
                            if r["owner"] == self.owner_id and r["operation"] in GRID_OPERATIONS
                            and (r["state"] in PENDING or self._grid_attached(r) and r.get("order_id"))}
            for key in grid_symbols:
                self.reconcile_grid(key)
            for intent in self.journal.intents(self.account_id, pending_only=True):
                if intent["operation"] in GRID_OPERATIONS:
                    continue
                if intent["owner"] != self.owner_id or (symbol is not None and intent["symbol"] != canonical_symbol(symbol)):
                    continue
                try:
                    with self.journal.symbol_lock(self.account_id, intent["symbol"]):
                        if intent["state"] == "PREPARED" and not self.journal.wires(self.account_id, intent["command_id"]):
                            self.journal.finish(self.account_id, intent["command_id"], "REJECTED", result={"not_sent": True})
                            continue
                        if intent["operation"] == "entry":
                            order = self._query(intent)
                            self._settle_entry(intent, order)
                        else:
                            self._recover_generic(intent)
                except ExchangeError:
                    if self.journal.get(self.account_id, intent["command_id"])["state"] in {"PREPARED", "ACK"}:
                        self.journal.finish(self.account_id, intent["command_id"], "UNKNOWN")
            for completed in self.journal.intents(self.account_id, symbol=symbol):
                if (completed["owner"] == self.owner_id and completed["operation"] == "close" and completed["state"] == "FILLED"
                        and self.journal.blocked(self.account_id, completed["symbol"]) == "CLOSE_PROTECTION_PENDING"):
                    try:
                        with self.journal.symbol_lock(self.account_id, completed["symbol"]):
                            self._recover_generic(completed)
                    except ExchangeError:
                        pass
            positions = self._positions() if self.journal.owned(self.account_id, owner=self.owner_id) else {}
            for owned in self.journal.owned(self.account_id, owner=self.owner_id):
                if decimal(owned["quantity"]) == 0:
                    continue
                try:
                    self._validated_owned(owned["symbol"], owned["position_id"], positions=positions)
                    self._restore_position(owned)
                except ExchangeError:
                    self.pause_symbol(owned["symbol"], "Владение после рестарта не подтверждено")
            for intent in self.journal.intents(self.account_id):
                if intent["owner"] == self.owner_id and intent["operation"] in {"entry", "close", "grid_entry", "grid_reduce"} and intent.get("order_id"):
                    self._restore_order(intent)
            for key in {r["symbol"] for r in self.journal.owned(self.account_id, owner=self.owner_id) if decimal(r["quantity"]) > 0}:
                try:
                    self.verify_protection(key, _apply_policy=False)
                except ExchangeError:
                    if self.journal.blocked(self.account_id, key) not in {"PROTECTION_PENDING", "CLOSE_PROTECTION_PENDING"}:
                        self.pause_symbol(key, "PROTECTION_FAILURE")
            uncertain = getattr(self._raw, "_uncertain_symbols", ())
            raw_symbols = {canonical_symbol(key) for key in uncertain} if isinstance(uncertain, set) else set()
            for key in raw_symbols:
                if symbol is not None and key != canonical_symbol(symbol):
                    continue
                try:
                    self._clear_reconciled_raw_uncertainty(key)
                except ExchangeError:
                    # Неудачная read-only сверка не даёт разрешения снять raw UNKNOWN.
                    pass
            symbols = {r["symbol"] for r in self.journal.intents(self.account_id)} | {r["symbol"] for r in self.journal.owned(self.account_id)} | raw_symbols
            return {key: self.journal.blocked(self.account_id, key) or "RAW_UNCERTAINTY"
                    for key in symbols if self.journal.blocked(self.account_id, key) or self._raw_symbol_uncertain(key)}


def attach_execution_safety(client, *, journal_path=None, settings=None, owner_id=None, account_id=None):
    from src.exchanges.impl.spot_trading_adapter import SpotTradingAdapter
    if isinstance(client, SpotTradingAdapter):
        client.validate_execution_context(owner_id=owner_id, account_id=account_id, settings=settings, journal_path=journal_path)
        return client
    if isinstance(client, ExecutionCoordinator):
        if owner_id is not None and client.owner_id != str(owner_id):
            raise ExchangeStateUnavailableError("Координатор уже закреплён за другим instance")
        if account_id is not None and client.account_id != str(account_id):
            raise ExchangeStateUnavailableError("Нельзя менять account_id подключённого координатора")
        if journal_path is not None and client.journal.path.resolve() != Path(journal_path).resolve():
            raise ExchangeStateUnavailableError("Нельзя менять durable journal подключённого координатора")
        if settings is not None:
            from src.trading_settings import validate_trading_settings
            if client.settings != validate_trading_settings(settings):
                raise ExchangeStateUnavailableError("Изменение execution settings требует нового клиента")
        return client
    existing = getattr(client, "_execution_coordinator", None)
    if isinstance(existing, ExecutionCoordinator):
        return attach_execution_safety(existing, owner_id=owner_id, settings=settings, journal_path=journal_path, account_id=account_id)
    if settings is None:
        settings = getattr(getattr(_unwrap(client), "_config", None), "trading_settings", None)
    if owner_id is None:
        from src import config
        owner_id = config.BOT_CONFIG.get("STRATEGY_INSTANCE_ID", "default")
    coordinator = ExecutionCoordinator(client, journal_path=journal_path, settings=settings, owner_id=owner_id, account_id=account_id)
    client._execution_coordinator = coordinator
    return coordinator
