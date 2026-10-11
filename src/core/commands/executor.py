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

        if command.action.is_hold and command.stop_loss is None and command.take_profit is None:
            info(f"[{command.symbol}] HOLD — нет команды на изменение защиты")
            return TradeResult(success=True, command=command, message="Нет торговой мутации")

        # Конвертируем TradeCommand → prediction dict (обратная совместимость)
        prediction = command.to_dict()

        try:
            from src.core.execution_helpers import prediction_command_id
            owner_id = command.metadata.get("strategy_instance_id")
            prediction["command_id"] = prediction_command_id(prediction, owner_id or command.strategy)
            actual = legacy_executor.execute_prediction(prediction, owner_id=owner_id)
            return TradeResult(
                success=actual is True,
                command=command,
                message="Исполнение подтверждено" if actual is True else "Команда не исполнена",
            )
        except Exception as e:
            from src.exchanges.errors import UnknownOrderStateError, ExchangeStateUnavailableError
            error(f"[{command.symbol}] Исполнение команды не подтверждено: {e}")
            if isinstance(e, (UnknownOrderStateError, ExchangeStateUnavailableError)):
                raise
            return TradeResult(
                success=False,
                command=command,
                message=str(e),
            )
