"""Фабрика генераторов сигналов."""

from typing import Dict

from .base import BaseSignalGenerator


def create_signal_generator(strategy: str, config: Dict) -> BaseSignalGenerator:
    """Фабрика генераторов сигналов."""
    from .hybrid import HybridSignalGenerator
    from .aiscalp import AiscalpSignalGenerator
    from .macdx import MacdxSignalGenerator
    from .scalp import ScalpSignalGenerator

    generators = {
        "HYBRID": lambda: HybridSignalGenerator(config.get("HYBRID_SETTINGS", {})),
        "AISCALP": lambda: AiscalpSignalGenerator(config.get("AISCALP_SETTINGS", {})),
        "MACDX": lambda: MacdxSignalGenerator(config.get("MACDX_SETTINGS", {})),
        "SCALP": lambda: ScalpSignalGenerator(config.get("SCALP_SETTINGS", {})),
    }

    factory = generators.get(strategy)
    if not factory:
        raise ValueError(f"Unknown signal generator strategy: {strategy}")
    return factory()
