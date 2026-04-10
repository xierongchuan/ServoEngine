"""
Unified Trade Command DTO — единый формат команд от стратегии к ядру.

TradeCommand — это единственный способ, которым стратегия сообщает ядру о своём решении.
Стратегия не знает ни о бирже, ни об исполнителе — она только генерирует команды.

Формат JSON-сериализуемый, что позволяет:
- передавать команды между процессами
- логировать команды для анализа
- воспроизводить команды на движке бэктестов
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TradeAction(Enum):
    """Действие, которое стратегия хочет выполнить."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"
    CLOSE_PARTIAL = "close_partial"

    @property
    def is_entry(self) -> bool:
        return self in (TradeAction.BUY, TradeAction.SELL)

    @property
    def is_exit(self) -> bool:
        return self in (TradeAction.CLOSE, TradeAction.CLOSE_PARTIAL)

    @property
    def is_hold(self) -> bool:
        return self == TradeAction.HOLD


@dataclass
class TradeCommand:
    """
    Единая команда от стратегии к ядру.

    Стратегия заполняет этот объект, ядро (CommandExecutor) его исполняет.
    Один и тот же TradeCommand может быть исполнен на реальной бирже или
    на движке бэктестов — стратегия об этом не знает.

    Attributes:
        symbol: Торговая пара (e.g., "BTC-USDT")
        action: Действие (BUY/SELL/HOLD/CLOSE/CLOSE_PARTIAL)
        confidence: Уверенность стратегии в сигнале (0.0-1.0)
        current_price: Текущая цена инструмента
        reason: Человекочитаемое объяснение решения
        stop_loss: Рекомендуемый стоп-лосс (None если не задан)
        take_profit: Рекомендуемый тейк-профит (None если не задан)
        size_pct: Процент от баланса для позиции (None = дефолт из конфига)
        percentage: Процент закрытия позиции (1.0 = полное закрытие)
        score: Сырой балл сигнала от генератора
        max_score: Максимально возможный балл
        confirmations: Количество подтверждённых индикаторов
        regime: Текущий рыночный режим (TRENDING/RANGING/VOLATILE/QUIET)
        strategy: Имя стратегии, сгенерировавшей команду
        timestamp: Время генерации команды (UTC)
        metadata: Дополнительные данные (indicators_status и т.д.)
    """
    symbol: str
    action: TradeAction
    confidence: float
    current_price: float
    reason: str = ""

    # Risk management
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    size_pct: Optional[float] = None
    percentage: float = 1.0  # For partial close

    # Signal quality
    score: int = 0
    max_score: int = 0
    confirmations: int = 0

    # Context
    regime: str = "UNKNOWN"
    strategy: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Extra data (indicators_status, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в dict (совместимый с текущим prediction dict)."""
        result = {
            "symbol": self.symbol,
            "action": self.action.value,
            "confidence": self.confidence,
            "current_price": self.current_price,
            "reason": self.reason,
            "score": self.score,
            "max_score": self.max_score,
            "confirmations": self.confirmations,
            "regime": self.regime,
            "strategy": self.strategy,
            "timestamp": self.timestamp,
        }
        if self.stop_loss is not None:
            result["stop_loss"] = self.stop_loss
        if self.take_profit is not None:
            result["take_profit"] = self.take_profit
        if self.size_pct is not None:
            result["size_pct"] = self.size_pct
        if self.percentage != 1.0:
            result["percentage"] = self.percentage
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def to_json(self) -> str:
        """Сериализация в JSON строку."""
        import json
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeCommand":
        """Десериализация из dict (prediction dict или JSON)."""
        action_raw = data.get("action", "hold")
        try:
            action = TradeAction(action_raw)
        except ValueError:
            action = TradeAction.HOLD

        return cls(
            symbol=data.get("symbol", ""),
            action=action,
            confidence=float(data.get("confidence", 0.0)),
            current_price=float(data.get("current_price", 0.0)),
            reason=data.get("reason", ""),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
            size_pct=data.get("size_pct"),
            percentage=float(data.get("percentage", 1.0)),
            score=int(data.get("score", 0)),
            max_score=int(data.get("max_score", 0)),
            confirmations=int(data.get("confirmations", 0)),
            regime=data.get("regime", "UNKNOWN"),
            strategy=data.get("strategy", ""),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def hold(cls, symbol: str, current_price: float, reason: str = "",
             strategy: str = "", **kwargs) -> "TradeCommand":
        """Фабричный метод для создания HOLD команды."""
        return cls(
            symbol=symbol,
            action=TradeAction.HOLD,
            confidence=kwargs.get("confidence", 0.0),
            current_price=current_price,
            reason=reason,
            strategy=strategy,
            score=kwargs.get("score", 0),
            max_score=kwargs.get("max_score", 0),
            confirmations=kwargs.get("confirmations", 0),
            regime=kwargs.get("regime", "UNKNOWN"),
            metadata=kwargs.get("metadata", {}),
        )

    @classmethod
    def close(cls, symbol: str, current_price: float, reason: str = "",
              confidence: float = 0.9, strategy: str = "",
              percentage: float = 1.0, **kwargs) -> "TradeCommand":
        """Фабричный метод для создания CLOSE команды."""
        action = TradeAction.CLOSE if percentage >= 1.0 else TradeAction.CLOSE_PARTIAL
        return cls(
            symbol=symbol,
            action=action,
            confidence=confidence,
            current_price=current_price,
            reason=reason,
            percentage=percentage,
            strategy=strategy,
            score=kwargs.get("score", 0),
            confirmations=kwargs.get("confirmations", 0),
        )

    @classmethod
    def entry(cls, symbol: str, side: str, current_price: float,
              confidence: float, reason: str = "",
              stop_loss: Optional[float] = None,
              take_profit: Optional[float] = None,
              size_pct: Optional[float] = None,
              strategy: str = "", **kwargs) -> "TradeCommand":
        """Фабричный метод для создания BUY/SELL команды."""
        action = TradeAction.BUY if side.upper() == "BUY" else TradeAction.SELL
        return cls(
            symbol=symbol,
            action=action,
            confidence=confidence,
            current_price=current_price,
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            size_pct=size_pct,
            strategy=strategy,
            score=kwargs.get("score", 0),
            max_score=kwargs.get("max_score", 0),
            confirmations=kwargs.get("confirmations", 0),
            regime=kwargs.get("regime", "UNKNOWN"),
            metadata=kwargs.get("metadata", {}),
        )


@dataclass
class TradeResult:
    """
    Результат исполнения TradeCommand.

    Возвращается CommandExecutor-ом после обработки команды.
    Содержит информацию о том, что реально произошло.
    """
    success: bool
    command: TradeCommand
    order_id: Optional[str] = None
    executed_price: Optional[float] = None
    executed_quantity: Optional[float] = None
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "executed_price": self.executed_price,
            "executed_quantity": self.executed_quantity,
            "message": self.message,
            "command": self.command.to_dict(),
            "timestamp": self.timestamp,
        }
