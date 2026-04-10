"""
Unified Trade Command DTO system.

Стратегии генерируют TradeCommand, ядро исполняет их через CommandExecutor.
Это позволяет запускать стратегии как на реальной бирже, так и на движке бэктестов
без изменения кода стратегии.
"""

from .models import (
    TradeAction,
    TradeCommand,
    TradeResult,
)
from .executor import CommandExecutor, BaseCommandExecutor

__all__ = [
    "TradeAction",
    "TradeCommand",
    "TradeResult",
    "CommandExecutor",
    "BaseCommandExecutor",
]
