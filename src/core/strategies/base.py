"""Базовый интерфейс для всех торговых пайплайнов."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class StrategyPipeline(ABC):
    """Базовый интерфейс для всех торговых пайплайнов."""

    def __init__(self, config: Dict):
        self.config = config

    @abstractmethod
    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        """
        Один полный цикл торговли.
        Returns: prediction dict или None
        """
