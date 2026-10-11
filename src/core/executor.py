"""Исполнение prediction через единый durable coordinator; ACK не равен fill."""

import json
import os

from src.config import DATA_DIR, POSITION_LIMITS, MIN_CONFIDENCE_THRESHOLD, AGGRESSIVE_MODE, AGGRESSIVE_SETTINGS
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client
from src.exchanges.dto.models import OrderSide, PositionSide
from src.exchanges.errors import ExchangeError, ExchangeStateUnavailableError, UnsupportedCapabilityError
from src.core.signals.utils import PositionAdapter
from src.core.execution_coordinator import attach_execution_safety
from src.core.execution_helpers import create_protected_order, prediction_command_id
from src.core.execution_journal import canonical_symbol


def _save_sl_tp(symbol, sl, tp):
    """UI-зеркало не служит доказательством защиты/владения."""
    from src.exchanges.market_data_storage import AtomicJsonStore
    store = AtomicJsonStore(os.path.join(DATA_DIR, "active_trades.json"))
    with store._lock(exclusive=True):
        data = store._read({})
        key = canonical_symbol(symbol)
        if key in data:
            data[key].update(sl=str(sl) if sl is not None else None, tp=str(tp) if tp is not None else None)
            store._write(data)


def get_open_positions():
    client = attach_execution_safety(get_exchange_client())
    try:
        return client.get_positions()
    except ExchangeError:
        error("❌ Не удалось достоверно загрузить позиции")
        raise


def _wait_for_position(client, symbol, position_side, timeout=4.0):
    client = attach_execution_safety(client)
    rows = client.get_owned_positions(symbol).get(canonical_symbol(symbol), [])
    expected = "BUY" if position_side == PositionSide.LONG else "SELL"
    matches = [p for p in rows if PositionAdapter(p).direction == expected]
    if len(matches) > 1:
        raise ExchangeStateUnavailableError("Собственная позиция неоднозначна")
    return matches[0] if matches else None


def _emergency_close_unprotected(client, symbol, position_side):
    client = attach_execution_safety(client)
    if client.settings["protection"]["failure_policy"] != "close":
        client.pause_symbol(symbol, "Защита не подтверждена; policy=pause")
        return False
    position = _wait_for_position(client, symbol, position_side)
    if position is None:
        raise ExchangeStateUnavailableError("Нет доказанного собственного объёма для аварийного close")
    return client.close_position(symbol, PositionAdapter(position).position_id)


def create_order(symbol, direction, price, ai_sl=None, ai_tp=None, reason="", confidence=0.0,
                 size_pct=None, order_type="MARKET", *, command_id=None, owner_id=None):
    client = attach_execution_safety(get_exchange_client(), owner_id=owner_id)
    try:
        oid = create_protected_order(client, symbol, direction, price, sl=ai_sl, tp=ai_tp,
                                     size_pct=size_pct, order_type=order_type, command_id=command_id, owner_id=owner_id)
        if order_type == "MARKET":
            scope = "биржевая защита" if client.capabilities.native_protection else "локальная регистрация SL/TP (не биржевая защита)"
            log_trade(f"✅ {symbol}: подтверждены fill и {scope}, ID={oid} | {reason}")
        else:
            info(f"⏳ {symbol}: LIMIT принят, fill ещё не подтверждён, ID={oid}")
        return oid
    except ExchangeError:
        error(f"❌ {symbol}: исполнение не подтверждено; журнал и владение сохраняются")
        raise


def execute_prediction(prediction, all_positions=None, owner_id=None, strategy_id=None):
    symbol = prediction["symbol"]
    owner_id = owner_id or prediction.get("strategy_instance_id")
    client = attach_execution_safety(get_exchange_client(), owner_id=owner_id)
    owner_id = client.owner_id
    strategy_id = strategy_id or prediction.get("strategy") or "UNKNOWN"
    action = str(prediction["action"]).lower()
    if client.is_symbol_blocked(symbol):
        raise ExchangeStateUnavailableError("Символ заблокирован журналом исполнения")
    if not client.check_prerequisites():
        return False
    # Перед решением читаем свежие owned positions, а не снимок предыдущего worker cycle.
    own = client.get_owned_positions(symbol).get(canonical_symbol(symbol), [])
    from src.core.position_ownership import get_position_ownership_store
    ownership = get_position_ownership_store()
    current_owner = ownership.get_owner(symbol)
    if current_owner and current_owner.owner_id != owner_id:
        return False
    threshold = AGGRESSIVE_SETTINGS.get("MIN_CONFIDENCE", MIN_CONFIDENCE_THRESHOLD) if AGGRESSIVE_MODE else MIN_CONFIDENCE_THRESHOLD
    if action == "hold" and not own:
        return True
    if action in {"buy", "sell"} and not own:
        if prediction.get("confidence", 0) < threshold:
            return False
        positions = client.get_positions()
        if sum(len(rows) for rows in positions.values()) >= POSITION_LIMITS.get("max_positions", 5):
            return False
        acquired, _ = ownership.try_acquire(symbol, owner_id, strategy_id)
        if not acquired:
            return False
        command_id = prediction_command_id(prediction, owner_id)
        try:
            oid = create_order(symbol, OrderSide(action.upper()), prediction["current_price"],
                               ai_sl=prediction.get("stop_loss"), ai_tp=prediction.get("take_profit"),
                               reason=prediction.get("reason", ""), confidence=prediction.get("confidence", 0),
                               size_pct=prediction.get("size_pct"), order_type=prediction.get("order_type", "MARKET"),
                               command_id=command_id, owner_id=owner_id)
        except ExchangeError:
            record = client.journal.get(client.account_id, command_id)
            if record is not None and record["state"] == "REJECTED" and not client.is_symbol_blocked(symbol):
                ownership.release_if_owner(symbol, owner_id)
            raise
        if prediction.get("order_type", "MARKET") == "LIMIT":
            # Command success только после fill + защиты, не по непустому order ID.
            filled = client.wait_for_entry(symbol, oid)
            if not filled and not client.is_symbol_blocked(symbol):
                ownership.release_if_owner(symbol, owner_id)
            return filled is True
        return bool(oid) and not client.is_symbol_blocked(symbol)
    if not own:
        return False
    metadata = prediction.get("metadata") or {}
    target_id = prediction.get("position_id") or metadata.get("position_id")
    target_side = prediction.get("position_side") or metadata.get("position_side")
    matches = [p for p in own if (target_id is None or PositionAdapter(p).position_id == str(target_id))
               and (target_side is None or ("LONG" if PositionAdapter(p).is_long else "SHORT") == str(target_side).upper())]
    if len(matches) != 1:
        raise ExchangeStateUnavailableError("Нельзя выбрать первую Hedge-позицию: нужен точный ID/side")
    adapter = PositionAdapter(matches[0])
    command_id = prediction_command_id(prediction, owner_id)
    if action in {"close", "close_partial"}:
        if prediction.get("confidence", 0) < threshold:
            return False
        pct = 1 if action == "close" else prediction.get("percentage", 1)
        result = client.close_position(symbol, adapter.position_id, pct, command_id=command_id)
        if result is True and client.closure_confirmed(symbol, adapter.position_id):
            ownership.release_if_owner(symbol, owner_id)
        return result is True
    if action == "hold":
        sl, tp = prediction.get("stop_loss"), prediction.get("take_profit")
        if sl is None and tp is None:
            return True
        return client.set_sl_tp(symbol, PositionSide.LONG if adapter.is_long else PositionSide.SHORT,
                                sl=sl, tp=tp, position_id=adapter.position_id, command_id=command_id) is True
    return False


def main(predictions):
    results = []
    for prediction in predictions:
        results.append(execute_prediction(prediction))
    return results


if __name__ == "__main__":
    import sys
    main(json.load(sys.stdin))
