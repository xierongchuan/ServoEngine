"""Базовый интерфейс для всех торговых пайплайнов."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from src.core.commands.models import TradeCommand


class StrategyPipeline(ABC):
    """
    Базовый интерфейс для всех торговых пайплайнов.

    Стратегия генерирует TradeCommand через generate_command().
    Для обратной совместимости run_cycle() продолжает возвращать dict.
    """

    def __init__(self, config: Dict):
        self.config = config

    def generate_command(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[TradeCommand]:
        """
        Генерирует торговую команду (TradeCommand).

        Это основной метод для интеграции с бэктестами и новыми движками.
        По умолчанию делегирует в run_cycle() и конвертирует результат.
        Стратегии могут переопределить этот метод для нативной генерации TradeCommand.

        Returns: TradeCommand или None
        """
        prediction = self.run_cycle(symbol, ws_cache, ws_ready)
        if prediction is None:
            return None
        return TradeCommand.from_dict(prediction)

    @abstractmethod
    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        """
        Один полный цикл торговли.
        Returns: prediction dict или None
        """
