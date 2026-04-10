from typing import Dict, List, Any, Optional
from src.backtest.metrics import PnLTracker, CommissionCalculator
from src.core.commands.models import TradeCommand, TradeResult, TradeAction
from src.core.commands.executor import BaseCommandExecutor
from src.utils.logger import info, warning


class BacktestSimulator(BaseCommandExecutor):
    """
    Симулятор бэктеста, реализующий BaseCommandExecutor.

    Принимает TradeCommand от стратегий и исполняет их на виртуальном балансе.
    Это позволяет запускать любую стратегию (MACDX, HYBRID, и т.д.)
    на исторических данных без изменения кода стратегии.
    """

    def __init__(self, initial_balance: float = 1000.0, leverage: float = 5.0,
                 position_size_percent: float = 0.1,
                 maker_rate: float = 0.0002, taker_rate: float = 0.0005,
                 default_sl_percent: float = 0.01, default_tp_percent: float = 0.03,
                 capital_mode: str = "isolated"):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.leverage = leverage
        self.position_size_percent = position_size_percent
        self.capital_mode = capital_mode
        self.default_sl_percent = default_sl_percent
        self.default_tp_percent = default_tp_percent
        self.positions: Dict[str, Dict[str, Any]] = {}  # symbol -> position
        self.pnl_tracker = PnLTracker()
        self.commission_calculator = CommissionCalculator(maker_rate=maker_rate, taker_rate=taker_rate)
        self.total_pnl_without_commissions = 0.0
        self.command_history: List[TradeCommand] = []

    # ── BaseCommandExecutor interface ──

    def execute(self, command: TradeCommand) -> TradeResult:
        """Исполняет TradeCommand на виртуальном балансе."""
        self.command_history.append(command)

        if command.action.is_hold:
            return TradeResult(success=True, command=command, message="Backtest: HOLD")

        if command.action.is_entry:
            side = "LONG" if command.action == TradeAction.BUY else "SHORT"
            size_pct = command.size_pct or (self.position_size_percent * 100)
            position_size = self.balance * (size_pct / 100.0) * self.leverage

            opened = self.open_position(
                command.symbol, side, command.current_price,
                sl_price=command.stop_loss,
                tp_price=command.take_profit,
                position_size=position_size,
                entry_time=command.timestamp,
            )
            if opened:
                return TradeResult(
                    success=True, command=command,
                    executed_price=command.current_price,
                    message=f"Backtest: Opened {side} at {command.current_price:.2f}",
                )
            return TradeResult(
                success=False, command=command,
                message=f"Backtest: Position already open for {command.symbol}",
            )

        if command.action.is_exit:
            result = self.close_position(command.symbol, command.current_price, command.reason or "command")
            if result:
                return TradeResult(
                    success=True, command=command,
                    executed_price=command.current_price,
                    message=f"Backtest: Closed at {command.current_price:.2f}, PnL {result['pnl']:.2f}",
                )
            return TradeResult(
                success=False, command=command,
                message=f"Backtest: No position to close for {command.symbol}",
            )

        return TradeResult(success=False, command=command, message=f"Unknown action: {command.action}")

    # ── SL/TP check producing TradeCommand ──

    def check_sl_tp_command(self, symbol: str, current_price: float) -> Optional[TradeCommand]:
        """Проверяет SL/TP и возвращает TradeCommand.close() если сработал."""
        if symbol not in self.positions:
            return None

        position = self.positions[symbol]
        side = position["side"]
        sl_price = position.get("sl_price")
        tp_price = position.get("tp_price")

        if sl_price is not None:
            if (side == "LONG" and current_price <= sl_price) or \
               (side == "SHORT" and current_price >= sl_price):
                return TradeCommand.close(symbol=symbol, current_price=current_price, reason="SL hit")

        if tp_price is not None:
            if (side == "LONG" and current_price >= tp_price) or \
               (side == "SHORT" and current_price <= tp_price):
                return TradeCommand.close(symbol=symbol, current_price=current_price, reason="TP hit")

        return None

    # ── Core position management ──

    def open_position(self, symbol: str, side: str, entry_price: float,
                      sl_percent: Optional[float] = None, tp_percent: Optional[float] = None,
                      sl_price: Optional[float] = None, tp_price: Optional[float] = None,
                      position_size: Optional[float] = None,
                      entry_time: Optional[str] = None) -> bool:
        """Открывает позицию. Если позиция уже открыта, не открывает новую."""
        if symbol in self.positions:
            warning(f"⚠️ Позиция для {symbol} уже открыта")
            return False

        if sl_percent is None:
            sl_percent = self.default_sl_percent
        if tp_percent is None:
            tp_percent = self.default_tp_percent

        if position_size is None:
            if self.capital_mode == "full_capital":
                position_size = self.balance * self.leverage
            else:  # isolated
                position_size = self.balance * self.position_size_percent * self.leverage

        # Комиссия на открытие (taker)
        commission = self.commission_calculator.calculate_commission(position_size)
        self.balance -= commission

        # SL/TP: использовать переданные цены или рассчитать по процентам
        if sl_price is None:
            sl_price = entry_price * (1 - sl_percent) if side == "LONG" else entry_price * (1 + sl_percent)
        if tp_price is None:
            tp_price = entry_price * (1 + tp_percent) if side == "LONG" else entry_price * (1 - tp_percent)

        position = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "size": position_size,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "unrealized_pnl": 0.0,
            "entry_time": entry_time,
        }
        self.positions[symbol] = position
        info(f"✅ Открыта позиция {side} для {symbol} по {entry_price:.2f}, размер {position_size:.2f}")
        return True

    def close_position(self, symbol: str, exit_price: float, reason: str = "manual") -> Optional[Dict[str, Any]]:
        """Закрывает позицию."""
        if symbol not in self.positions:
            warning(f"⚠️ Нет открытой позиции для {symbol}")
            return None

        position = self.positions.pop(symbol)
        side = position["side"]
        entry_price = position["entry_price"]
        size = position["size"]

        # Рассчитать P&L без комиссий
        if side == "LONG":
            pnl_without_comm = (exit_price - entry_price) * size / entry_price
        else:
            pnl_without_comm = (entry_price - exit_price) * size / entry_price

        # Комиссия на закрытие (maker, если limit)
        commission = self.commission_calculator.calculate_commission(size, is_maker=True)
        pnl = pnl_without_comm - commission

        self.total_pnl_without_commissions += pnl_without_comm

        self.balance += pnl
        position["pnl"] = pnl
        position["exit_price"] = exit_price
        position["exit_reason"] = reason

        self.pnl_tracker.add_trade(position)
        info(f"❌ Закрыта позиция {side} для {symbol} по {exit_price:.2f}, P&L {pnl:.2f}, причина: {reason}")
        return position

    def update_unrealized_pnl(self, current_prices: Dict[str, float]):
        """Обновляет unrealized P&L для открытых позиций (без SL/TP проверки)."""
        for symbol, position in self.positions.items():
            if symbol not in current_prices:
                continue
            current_price = current_prices[symbol]
            side = position["side"]
            entry_price = position["entry_price"]
            size = position["size"]

            if side == "LONG":
                unrealized_pnl = (current_price - entry_price) * size / entry_price
            else:
                unrealized_pnl = (entry_price - current_price) * size / entry_price
            position["unrealized_pnl"] = unrealized_pnl

    def update_positions(self, current_prices: Dict[str, float]):
        """
        Обновляет позиции: unrealized P&L + SL/TP проверка.

        Для нового TradeCommand-потока используйте check_sl_tp_command()
        и update_unrealized_pnl() по отдельности — это даёт контроль
        над тем, как именно обрабатываются выходы.
        """
        self.update_unrealized_pnl(current_prices)

        # SL/TP check (через check_sl_tp_command + execute для единообразия)
        to_close = []
        for symbol in list(self.positions.keys()):
            if symbol not in current_prices:
                continue
            cmd = self.check_sl_tp_command(symbol, current_prices[symbol])
            if cmd:
                to_close.append(cmd)

        for cmd in to_close:
            self.execute(cmd)

    def get_current_balance(self) -> float:
        """Текущий баланс."""
        return self.balance

    def get_positions(self) -> Dict[str, Dict[str, Any]]:
        """Открытые позиции."""
        return self.positions

    def get_metrics(self) -> Dict[str, Any]:
        """Метрики."""
        metrics = self.pnl_tracker.get_metrics()
        total_commission = self.commission_calculator.get_total_commission()
        metrics["total_pnl_without_commissions"] = self.total_pnl_without_commissions
        metrics["total_commission"] = total_commission
        return metrics
