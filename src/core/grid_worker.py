"""Совместимый импорт единственного GRID-worker."""

from src.core.strategies.grid.worker import (
    _analyze_volatility, _extract_ticker_prices, _graceful_shutdown,
    run_grid_cycle, run_grid_worker,
)
from src.core.strategies.grid.adx import calculate_adx as _calculate_adx

_ai_analyze_volatility = _analyze_volatility

__all__ = ["run_grid_worker", "run_grid_cycle"]
