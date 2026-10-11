"""Общие sizing/command helpers: только доступный бюджет и Decimal-правила инструмента."""

import hashlib
import uuid
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from src.exchanges.dto.models import Balance, OrderSide, OrderType
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from .execution_coordinator import attach_execution_safety, decimal, value


def prediction_command_id(prediction, owner_id):
    metadata = prediction.get("metadata") or {}
    explicit = prediction.get("command_id") or metadata.get("command_id") or prediction.get("_execution_command_id")
    if explicit:
        return str(explicit)
    timestamp = prediction.get("timestamp") or metadata.get("candle_timestamp")
    token = f"{owner_id}:{prediction['symbol']}:{prediction['action']}:{timestamp}" if timestamp else uuid.uuid4().hex
    command_id = hashlib.sha256(token.encode()).hexdigest()
    prediction["_execution_command_id"] = command_id
    return command_id


def available_balance(balance):
    if isinstance(balance, Balance):
        return decimal(balance.available_balance, "available balance")
    if isinstance(balance, dict):
        for key in ("available", "available_balance", "availableBalance", "availableMargin", "free"):
            if key in balance:
                return decimal(balance[key], "available balance")
    raise ExchangeStateUnavailableError("Нет достоверного available balance; equity fallback запрещён")


def size_from_budget(client, symbol, price, size_pct, leverage, *, fee_pct=0, safety_margin="0.95", minimum_margin=0):
    price = decimal(price, "price", positive=True)
    fraction = decimal(size_pct, "size_pct", positive=True) / 100
    lev = decimal(leverage, "leverage", positive=True)
    safety = decimal(safety_margin, "safety margin", positive=True)
    fee = decimal(fee_pct, "fee_pct") / 100
    if fraction > 1 or safety > 1 or lev != lev.to_integral_value() or fee < 0:
        raise ExchangeStateUnavailableError("Некорректные бюджет/плечо/комиссия")
    available = available_balance(client.get_balance())
    if available <= 0:
        raise ExchangeStateUnavailableError("Доступный бюджет равен нулю; вход запрещён")
    budget = available * min(fraction, safety)
    margin = budget / (1 + lev * fee * 2)
    if margin < decimal(minimum_margin):
        raise ExchangeStateUnavailableError("Минимальная margin превышает выбранный риск-бюджет")
    rules = client.get_instrument_rules(symbol)
    if rules is None or rules.quantity_step <= 0 or rules.contract_size <= 0:
        raise ExchangeStateUnavailableError("Нет точных правил инструмента")
    if lev < rules.min_leverage or (rules.max_leverage > 0 and lev > rules.max_leverage):
        raise ExchangeStateUnavailableError("Плечо вне правил инструмента")
    step = rules.quantity_step * rules.contract_size
    qty = ((margin * lev / price) / step).to_integral_value(rounding=ROUND_FLOOR) * step
    if qty <= 0 or qty < rules.min_quantity * rules.contract_size or (rules.max_quantity > 0 and qty > rules.max_quantity * rules.contract_size) or qty * price < rules.min_notional:
        raise ExchangeStateUnavailableError("Биржевой minimum/step не укладывается в риск-бюджет; увеличение запрещено")
    return qty


def create_protected_order(client, symbol, direction, price, *, sl=None, tp=None, size_pct=None,
                           order_type="MARKET", command_id=None, owner_id=None):
    from src import config
    client = attach_execution_safety(client, owner_id=owner_id)
    side = OrderSide(str(value(direction)).upper())
    kind = OrderType(str(value(order_type)).upper())
    entry = decimal(price, "price", positive=True)
    native = client.capabilities.native_protection is True
    local = not native
    if local and not (client.settings["market_type"] == "spot" and client.settings.get("spot", {}).get("allow_local_protection") is True
                      and getattr(client, "local_protection", False) is True and getattr(client, "supervisor_running", False) is True):
        raise UnsupportedCapabilityError("Entry без native protection требует явного разрешения local protection и живого Spot supervisor")
    if local and side != OrderSide.BUY:
        raise UnsupportedCapabilityError("Spot entry не открывает SHORT; SELL выполняется через owned close")
    leverage = 1 if local else config.LEVERAGE
    command_id = command_id or uuid.uuid4().hex
    pct = config.POSITION_SIZE_PERCENT if size_pct is None else size_pct
    previous = client.journal.get(client.account_id, command_id)
    if previous is not None:
        from .execution_journal import canonical_symbol
        if (previous["owner"] != client.owner_id or previous["operation"] != "entry"
                or canonical_symbol(previous["symbol"]) != canonical_symbol(symbol)):
            raise ExchangeStateUnavailableError("command_id уже связан с другим владельцем/символом/операцией")
        # Replay использует исходный intent, а не новый баланс после предыдущего fill.
        # Остальные параметры повторно сравнит durable клиент; UNKNOWN не переотправляется.
        qty = decimal(previous["request"]["quantity"], "intent quantity", positive=True)
    else:
        qty = size_from_budget(client, symbol, entry, pct, leverage,
                               fee_pct=config.TRADING_FEE_TAKER,
                               safety_margin=config.POSITION_LIMITS.get("balance_safety_margin", .95),
                               minimum_margin=config.MIN_TRADE_AMOUNT_USDT)
    rules = client.get_instrument_rules(symbol)
    is_buy = side == OrderSide.BUY
    target_sl = decimal(sl, "SL", positive=True) if sl is not None else entry * (1 - decimal(config.STOP_LOSS_PERCENT) / 100 if is_buy else 1 + decimal(config.STOP_LOSS_PERCENT) / 100)
    target_tp = decimal(tp, "TP", positive=True) if tp is not None else entry * (1 + decimal(config.TAKE_PROFIT_PERCENT) / 100 if is_buy else 1 - decimal(config.TAKE_PROFIT_PERCENT) / 100)
    def quantize(number, upward):
        return (number / rules.price_step).to_integral_value(rounding=ROUND_CEILING if upward else ROUND_FLOOR) * rules.price_step
    target_sl, target_tp = quantize(target_sl, is_buy), quantize(target_tp, not is_buy)
    if not (target_sl < entry < target_tp if is_buy else target_tp < entry < target_sl):
        raise ExchangeStateUnavailableError("Некорректные стороны SL/TP до entry")
    oid = client.place_order(symbol, side, qty, order_type=kind, price=entry, sl=target_sl, tp=target_tp,
                             leverage=leverage, command_id=command_id)
    oid = getattr(oid, "order_id", oid)
    if not oid or isinstance(oid, bool):
        raise UnknownOrderStateError("Entry ACK не содержит адресного order ID")
    if kind == OrderType.MARKET:
        if client.is_symbol_blocked(symbol):
            raise UnknownOrderStateError("Entry fill/защита не разрешены журналом")
        if local:
            record = client.journal.get(client.account_id, command_id)
            if not record or record["state"] != "FILLED" or record["result"].get("entry_complete") is not True or not client.check_protection(symbol, position_id=record["result"]["position_id"]):
                raise UnknownOrderStateError("Spot: полный fill и локальная регистрация SL/TP не доказаны")
    return str(oid)
