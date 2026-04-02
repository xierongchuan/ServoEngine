"""Фабрика пайплайнов стратегий."""

from typing import Dict

from .base import StrategyPipeline


def create_pipeline(strategy: str, config: Dict) -> StrategyPipeline:
    """Фабрика пайплайнов стратегий."""
    from .hybrid import HybridPipeline
    from .aiscalp import AiscalpPipeline
    from .macdx import MacdxPipeline
    from .swing import SwingPipeline

    pipelines = {
        "HYBRID": lambda: HybridPipeline(config),
        "AISCALP": lambda: AiscalpPipeline(config),
        "MACDX": lambda: MacdxPipeline(config),
        "SWING": lambda: SwingPipeline(config),
    }

    factory = pipelines.get(strategy)
    if not factory:
        raise ValueError(f"Unknown pipeline strategy: {strategy}")
    return factory()
