"""Абстрактный базовый класс для всех генераторов сигналов."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .utils import map_quality_to_confidence


class BaseSignalGenerator(ABC):
    """Базовый класс для всех генераторов сигналов.

    Реализует Dependency Injection через конструктор вместо глобального конфига.
    Предоставляет общие методы: _map_quality, _hold_result.
    """

    def __init__(self, settings: Dict):
        self.settings = settings

    @abstractmethod
    def generate(self, analysis: Dict, **kwargs) -> Dict:
        """
        Генерирует торговый сигнал.
        Returns: {signal, score, max_score, quality, confidence, reasons, details, regime}
        """

    @abstractmethod
    def should_close(self, analysis: Dict, position: Any, **kwargs) -> Dict:
        """
        Проверяет условия закрытия позиции.
        Returns: {should_close, reason, urgency}
        """

    # --- Общие методы ---

    def _map_quality(self, quality: float, has_signal: bool) -> float:
        """Маппинг quality → confidence."""
        return map_quality_to_confidence(quality, has_signal)

    def _hold_result(
        self,
        max_score: int,
        reasons: list,
        details: dict,
        regime: Optional[Dict] = None,
    ) -> Dict:
        """Стандартный HOLD результат."""
        return {
            "signal": "HOLD",
            "score": 0,
            "max_score": max_score,
            "quality": 0.0,
            "confidence": 0.0,
            "reasons": reasons,
            "filters_passed": False,
            "details": details,
            "regime": regime.get("regime", "UNKNOWN") if regime else "NO_REGIME",
        }
