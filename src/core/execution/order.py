"""Совместимые entry helpers; единственный путь исполнения находится в core.executor."""

from src.exchanges.exchange_factory import get_exchange_client
from src.core.execution_coordinator import attach_execution_safety


def _save_sl_tp(symbol, sl, tp):
    from src.core.executor import _save_sl_tp as save
    return save(symbol, sl, tp)


def get_open_positions():
    return attach_execution_safety(get_exchange_client()).get_positions()


def create_order(symbol, direction, price, ai_sl=None, ai_tp=None, reason="", confidence=0.0,
                 size_pct=None, order_type="MARKET", *, command_id=None, owner_id=None):
    from src.core.executor import create_order as execute
    return execute(symbol, direction, price, ai_sl, ai_tp, reason, confidence, size_pct, order_type,
                   command_id=command_id, owner_id=owner_id)
