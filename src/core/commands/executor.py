"""
CommandExecutor — исполнитель TradeCommand.

BaseCommandExecutor — абстрактный интерфейс.
CommandExecutor — реализация для реальной/демо биржи (делегирует в существующий executor.py).

Для бэктестов достаточно реализовать свой BaseCommandExecutor, который вместо
вызовов биржи обновляет виртуальный баланс и позиции.
"""

from abc import ABC, abstractmethod
from typing import Optional

from .models import TradeCommand, TradeResult, TradeAction


class BaseCommandExecutor(ABC):
    """
    Абстрактный исполнитель торговых команд.

    Стратегия генерирует TradeCommand → CommandExecutor его исполняет.
    Для бэктестов: подменить этот класс на BacktestCommandExecutor.
    """

    @abstractmethod
    def execute(self, command: TradeCommand) -> TradeResult:
        """
        Исполнить торговую команду.

        Args:
            command: Единая команда от стратегии

        Returns:
            TradeResult с результатом исполнения
        """


class CommandExecutor(BaseCommandExecutor):
    """
    Исполнитель команд для реальной/демо биржи.

    Делегирует исполнение в существующий executor.execute_prediction(),
    обеспечивая обратную совместимость с текущей кодовой базой.
    """

    def execute(self, command: TradeCommand) -> TradeResult:
        """Исполняет TradeCommand через существующий executor."""
        from src.core import executor as legacy_executor
        from src.utils.logger import info, error

        if command.action.is_hold:
            info(f"[{command.symbol}] HOLD — no action needed")
            return TradeResult(
                success=True,
                command=command,
                message="HOLD — no execution needed",
            )

        # Конвертируем TradeCommand → prediction dict (обратная совместимость)
        prediction = command.to_dict()

        try:
            legacy_executor.execute_prediction(prediction)
            return TradeResult(
                success=True,
                command=command,
                executed_price=command.current_price,
                message=f"Executed {command.action.value} via legacy executor",
            )
        except Exception as e:
            error(f"[{command.symbol}] Command execution failed: {e}")
            return TradeResult(
                success=False,
                command=command,
                message=str(e),
            )
