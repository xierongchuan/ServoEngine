"""Генераторы торговых сигналов."""

from .base import BaseSignalGenerator
from .factory import create_signal_generator
from .hybrid import HybridSignalGenerator
from .aiscalp import AiscalpSignalGenerator
from .macdx import MacdxSignalGenerator
from .scalp import ScalpSignalGenerator
from .utils import (
    detect_rsi_divergence,
    map_quality_to_confidence,
    calculate_pnl_pct,
    PositionAdapter,
)

__all__ = [
    "BaseSignalGenerator",
    "create_signal_generator",
    "HybridSignalGenerator",
    "AiscalpSignalGenerator",
    "MacdxSignalGenerator",
    "ScalpSignalGenerator",
    "detect_rsi_divergence",
    "map_quality_to_confidence",
    "calculate_pnl_pct",
    "PositionAdapter",
]
