"""Утилиты для работы с позициями — адаптеры, PnL расчёты, SL/TP management."""

from typing import Any, Dict

from src.core.signals.utils import PositionAdapter, calculate_pnl_pct


def get_position_side(position: Any) -> str:
    """Определяет сторону позиции (LONG/SHORT)."""
    adapter = PositionAdapter(position)
    return "LONG" if adapter.is_long else "SHORT"


def get_position_entry(position: Any) -> float:
    """Извлекает цену входа из позиции."""
    adapter = PositionAdapter(position)
    return adapter.entry_price


def get_position_deal_id(position: Any) -> str:
    """Извлекает ID сделки из позиции."""
    return PositionAdapter(position).position_id


def calculate_position_pnl(position: Any, current_price: float) -> float:
    """Рассчитывает PnL в процентах для позиции."""
    adapter = PositionAdapter(position)
    return calculate_pnl_pct(adapter.entry_price, current_price, adapter.direction)


def extract_sl_tp_from_prediction(prediction: Dict) -> tuple:
    """Извлекает SL/TP из предсказания."""
    return prediction.get("stop_loss"), prediction.get("take_profit")
