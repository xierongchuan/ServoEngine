from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """Базовый класс стратегии для генерации промпт-секций."""

    @abstractmethod
    def get_role(self) -> str:
        """Описание роли трейдера."""
        ...

    @abstractmethod
    def get_objective(self) -> str:
        """Цель стратегии."""
        ...

    @abstractmethod
    def get_time_horizon(self) -> str:
        """Горизонт удержания позиции."""
        ...

    @abstractmethod
    def get_strategy_section(self, ctx: dict) -> str:
        """Полная секция стратегии (## 3. СТРАТЕГИЯ)."""
        ...

    # --- Override методы для замены общих блоков ---

    def get_position_management(self, ctx: dict) -> str | None:
        """Override для замены блока position_management. None = дефолтный."""
        return None

    def get_special_situations(self, ctx: dict) -> str | None:
        """Override для замены блока special_situations. None = дефолтный."""
        return None

    def get_risk_table(self, ctx: dict) -> str | None:
        """Override для замены блока risk_table. None = дефолтный."""
        return None
