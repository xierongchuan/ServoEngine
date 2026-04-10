"""
Пример: BacktestCommandExecutor — движок бэктестов на базе TradeCommand.

Демонстрирует, как стратегия (например MACDX) может работать на движке
бэктестов без какой-либо адаптации кода стратегии.

Стратегия генерирует TradeCommand → BacktestCommandExecutor исполняет команды
на виртуальном балансе с историческими данными.

Использование:
    python examples/backtest_executor.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.core.commands.models import TradeCommand, TradeResult, TradeAction
from src.core.commands.executor import BaseCommandExecutor


@dataclass
class BacktestPosition:
    """Виртуальная позиция в бэктесте."""
    symbol: str
    side: str  # "buy" or "sell"
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    size_pct: float = 10.0


@dataclass
class BacktestStats:
    """Статистика бэктеста."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_balance: float = 0.0


class BacktestCommandExecutor(BaseCommandExecutor):
    """
    Движок бэктестов, реализующий BaseCommandExecutor.

    Принимает те же TradeCommand, что и реальный CommandExecutor,
    но вместо вызовов биржи обновляет виртуальный баланс.

    Это позволяет запускать любую стратегию (MACDX, HYBRID, и т.д.)
    на исторических данных без изменения кода стратегии.
    """

    def __init__(self, initial_balance: float = 10000.0, fee_pct: float = 0.05):
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.fee_pct = fee_pct
        self.positions: Dict[str, BacktestPosition] = {}
        self.command_history: List[TradeCommand] = []
        self.trade_log: List[Dict] = []
        self.stats = BacktestStats(peak_balance=initial_balance)

    def execute(self, command: TradeCommand) -> TradeResult:
        """Исполняет TradeCommand на виртуальном балансе."""
        self.command_history.append(command)

        if command.action.is_hold:
            return TradeResult(success=True, command=command, message="Backtest: HOLD")

        if command.action.is_entry:
            return self._open_position(command)

        if command.action.is_exit:
            return self._close_position(command)

        return TradeResult(success=False, command=command, message=f"Unknown action: {command.action}")

    def _open_position(self, command: TradeCommand) -> TradeResult:
        if command.symbol in self.positions:
            return TradeResult(
                success=False, command=command,
                message=f"Backtest: Position already open for {command.symbol}",
            )

        self.positions[command.symbol] = BacktestPosition(
            symbol=command.symbol,
            side=command.action.value,
            entry_price=command.current_price,
            stop_loss=command.stop_loss,
            take_profit=command.take_profit,
            size_pct=command.size_pct or 10.0,
        )

        fee = self.balance * (command.size_pct or 10.0) / 100.0 * self.fee_pct / 100.0
        self.balance -= fee

        return TradeResult(
            success=True, command=command,
            executed_price=command.current_price,
            message=f"Backtest: Opened {command.action.value} at {command.current_price:.2f} (fee: ${fee:.2f})",
        )

    def _close_position(self, command: TradeCommand) -> TradeResult:
        pos = self.positions.pop(command.symbol, None)
        if pos is None:
            return TradeResult(
                success=False, command=command,
                message=f"Backtest: No position to close for {command.symbol}",
            )

        # Calculate PnL
        position_value = self.balance * pos.size_pct / 100.0
        if pos.side == "buy":
            pnl_pct = (command.current_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - command.current_price) / pos.entry_price

        pnl = position_value * pnl_pct
        fee = position_value * self.fee_pct / 100.0
        net_pnl = pnl - fee
        self.balance += net_pnl

        # Update stats
        self.stats.total_trades += 1
        self.stats.total_pnl += net_pnl
        if net_pnl > 0:
            self.stats.winning_trades += 1
        else:
            self.stats.losing_trades += 1

        if self.balance > self.stats.peak_balance:
            self.stats.peak_balance = self.balance
        drawdown = (self.stats.peak_balance - self.balance) / self.stats.peak_balance
        if drawdown > self.stats.max_drawdown:
            self.stats.max_drawdown = drawdown

        self.trade_log.append({
            "symbol": command.symbol,
            "side": pos.side,
            "entry": pos.entry_price,
            "exit": command.current_price,
            "pnl": net_pnl,
            "pnl_pct": pnl_pct * 100,
            "balance": self.balance,
        })

        return TradeResult(
            success=True, command=command,
            executed_price=command.current_price,
            message=f"Backtest: Closed at {command.current_price:.2f}, PnL: ${net_pnl:.2f} ({pnl_pct*100:.2f}%)",
        )

    def check_sl_tp(self, symbol: str, current_price: float) -> Optional[TradeCommand]:
        """Проверяет SL/TP для открытых позиций (вызывается движком бэктеста на каждой свече)."""
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        if pos.side == "buy":
            if pos.stop_loss and current_price <= pos.stop_loss:
                return TradeCommand.close(symbol=symbol, current_price=current_price, reason="SL hit")
            if pos.take_profit and current_price >= pos.take_profit:
                return TradeCommand.close(symbol=symbol, current_price=current_price, reason="TP hit")
        else:
            if pos.stop_loss and current_price >= pos.stop_loss:
                return TradeCommand.close(symbol=symbol, current_price=current_price, reason="SL hit")
            if pos.take_profit and current_price <= pos.take_profit:
                return TradeCommand.close(symbol=symbol, current_price=current_price, reason="TP hit")

        return None

    def summary(self) -> str:
        """Итог бэктеста."""
        s = self.stats
        win_rate = (s.winning_trades / s.total_trades * 100) if s.total_trades > 0 else 0
        return (
            f"\n{'='*50}\n"
            f"Backtest Summary\n"
            f"{'='*50}\n"
            f"Initial balance:  ${self.initial_balance:.2f}\n"
            f"Final balance:    ${self.balance:.2f}\n"
            f"Total PnL:        ${s.total_pnl:.2f} ({s.total_pnl/self.initial_balance*100:.2f}%)\n"
            f"Total trades:     {s.total_trades}\n"
            f"Win rate:         {win_rate:.1f}%\n"
            f"Max drawdown:     {s.max_drawdown*100:.2f}%\n"
            f"Commands issued:  {len(self.command_history)}\n"
            f"{'='*50}"
        )


def demo():
    """
    Демо: имитация работы стратегии через TradeCommand.

    Показывает, как стратегия (любая) генерирует команды,
    а движок бэктестов их исполняет.
    """
    bt = BacktestCommandExecutor(initial_balance=10000.0, fee_pct=0.05)

    # Имитация последовательности команд, которые стратегия генерирует
    # (в реальности pipeline.generate_command() делает это автоматически)
    commands = [
        # Цикл 1: сигнал BUY
        TradeCommand.entry(
            symbol="BTC-USDT", side="BUY", current_price=50000.0,
            confidence=0.85, stop_loss=49000.0, take_profit=52000.0,
            size_pct=10.0, strategy="MACDX", score=7, max_score=9,
        ),
        # Цикл 2: удержание
        TradeCommand.hold(symbol="BTC-USDT", current_price=50500.0, strategy="MACDX"),
        # Цикл 3: закрытие с прибылью
        TradeCommand.close(
            symbol="BTC-USDT", current_price=51500.0,
            reason="[MACDX] Take profit signal", strategy="MACDX",
        ),
        # Цикл 4: новый вход SELL
        TradeCommand.entry(
            symbol="BTC-USDT", side="SELL", current_price=51500.0,
            confidence=0.80, stop_loss=52000.0, take_profit=50000.0,
            size_pct=8.0, strategy="MACDX", score=6, max_score=9,
        ),
        # Цикл 5: SL сработал
        TradeCommand.close(
            symbol="BTC-USDT", current_price=52100.0,
            reason="[MACDX] Stop loss hit", strategy="MACDX",
        ),
    ]

    print("Running backtest demo...")
    print(f"Initial balance: ${bt.balance:.2f}")
    print()

    for i, cmd in enumerate(commands, 1):
        result = bt.execute(cmd)
        print(f"  Cycle {i}: {cmd.action.value:>6} @ ${cmd.current_price:.2f} -> {result.message}")

    print(bt.summary())

    # Показываем, что все команды сериализуемы
    print("\nCommand history (JSON):")
    for cmd in bt.command_history:
        print(f"  {cmd.to_json()}")


if __name__ == "__main__":
    demo()
