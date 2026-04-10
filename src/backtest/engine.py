import os
import json
import logging
import datetime
from typing import Dict, Any, Optional
from .data_loader import DataLoader
from .signals import SignalGenerator
from .simulator import BacktestSimulator
from ..config_loader import resolve_symbol_config, load_backtest_config
from ..core.commands.models import TradeCommand, TradeAction
from ..utils.logger import info, error

class BacktestEngine:
    """
    Основной движок бэктеста, объединяет все компоненты.

    Работает через единый TradeCommand DTO:
    SignalGenerator → TradeCommand → BacktestSimulator.execute()

    BacktestSimulator реализует BaseCommandExecutor, поэтому
    тот же интерфейс исполнения используется и в реальной торговле,
    и в бэктестах (подмена executor — единственная разница).
    """

    def __init__(self, symbol: str, strategy: str = "MACDX",
                 initial_balance: Optional[float] = None):
        self.symbol = symbol
        self.strategy = strategy
        self.backtest_config = load_backtest_config()
        self.config = self._load_config()
        preset = self.config.get("preset", {})
        self.timeframe = preset.get("timeframe", "15m")
        self.data_loader = DataLoader(symbol, self.timeframe)
        self.signal_generator = SignalGenerator(strategy, self.config)

        # Создаём MacdxSignalGenerator для использования should_close()
        # чтобы бэктест использовал ту же логику выходов, что и live
        self._macdx_signal_gen = None
        self._exit_context: Dict[str, Any] = {}
        if strategy.upper() == "MACDX":
            try:
                from ..core.signals.macdx import MacdxSignalGenerator
                self._macdx_signal_gen = MacdxSignalGenerator(self.config)
            except ImportError:
                pass

        # Capital: CLI arg > config/backtest.json > 1000.0
        capital_config = self.backtest_config.get("capital", {})
        balance = initial_balance or capital_config.get("initial_balance", 1000.0)
        capital_mode = capital_config.get("mode", "isolated")

        # Commission rates from config
        commission_config = self.backtest_config.get("commission", {})
        maker_rate = commission_config.get("maker_rate", 0.0002)
        taker_rate = commission_config.get("taker_rate", 0.0005)

        # Default SL/TP from config
        defaults_config = self.backtest_config.get("defaults", {})
        default_sl = defaults_config.get("sl_percent", 0.01)
        default_tp = defaults_config.get("tp_percent", 0.03)

        self.simulator = BacktestSimulator(
            initial_balance=balance,
            leverage=preset.get("leverage", 5.0),
            position_size_percent=self.config.get("position", {}).get("size_percent", 10) / 100.0,
            maker_rate=maker_rate,
            taker_rate=taker_rate,
            default_sl_percent=default_sl,
            default_tp_percent=default_tp,
            capital_mode=capital_mode,
        )
        self._setup_logging()

    def _load_config(self) -> Dict[str, Any]:
        """Загружает конфигурацию для символа и стратегии."""
        try:
            return resolve_symbol_config(self.symbol, self.strategy)
        except Exception as e:
            error(f"❌ Ошибка загрузки конфигурации: {e}")
            return {}

    def _setup_logging(self):
        """Настраивает логирование для бэктеста."""
        log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "logs", f"backtest_{self.symbol}.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    def run(self) -> Dict[str, Any]:
        """
        Запускает бэктест через единый TradeCommand поток.

        Для каждой свечи:
        1. Проверить SL/TP → TradeCommand.close()
        2. Обновить unrealized P&L
        3. Сгенерировать сигнал → TradeCommand
        4. Исполнить через BacktestSimulator.execute()
        5. Проверить стратегические условия выхода
        """
        try:
            info(f"🚀 Запуск бэктеста для {self.symbol} стратегии {self.strategy}")

            klines = self.data_loader.load_data(fetch_if_missing=True)
            if not klines:
                error("❌ Нет данных для бэктеста")
                return {}

            info(f"📊 Данные загружены: {len(klines)} свечей")

            for i, kline in enumerate(klines):
                current_price = kline["closePrice"]

                # 1. Проверить SL/TP через TradeCommand
                sl_tp_cmd = self.simulator.check_sl_tp_command(self.symbol, current_price)
                if sl_tp_cmd:
                    self.simulator.execute(sl_tp_cmd)
                    self._exit_context.clear()
                    continue

                # 2. Обновить unrealized P&L (без SL/TP — уже проверено выше)
                self.simulator.update_unrealized_pnl({self.symbol: current_price})

                # 3. Сгенерировать сигнал и конвертировать в TradeCommand
                try:
                    signal = self.signal_generator.generate_signal(klines, i)
                    command = self._signal_to_command(signal, current_price)
                    result = self.simulator.execute(command)

                    if command.action.is_entry:
                        self._exit_context.clear()
                        info(f"📈 {command.action.value.upper()} на {self.symbol} по {current_price:.2f}")
                except Exception as e:
                    error(f"Ошибка на индексе {i}: {e}")
                    continue

                # 4. Проверить стратегические условия выхода → TradeCommand
                #    Используем кэшированные индикаторы из generate_signal,
                #    чтобы не пересчитывать их дважды
                exit_cmd = self._check_exit_command(klines, i, current_price,
                                                     cached_indicators=self.signal_generator.last_indicators)
                if exit_cmd:
                    self.simulator.execute(exit_cmd)

            # Закрыть все открытые позиции
            for symbol in list(self.simulator.positions.keys()):
                close_cmd = TradeCommand.close(
                    symbol=symbol,
                    current_price=klines[-1]["closePrice"] if klines else 0,
                    reason="end_of_data",
                    strategy=self.strategy,
                )
                self.simulator.execute(close_cmd)

            return self._build_result(klines)

        except Exception as e:
            error(f"❌ Ошибка в бэктесте: {e}")
            return {}

    def _signal_to_command(self, signal: Dict[str, Any], current_price: float) -> TradeCommand:
        """Конвертирует сигнал от SignalGenerator в TradeCommand."""
        action = signal.get("action", "HOLD")

        if action in ("BUY", "SELL"):
            return TradeCommand.entry(
                symbol=self.symbol,
                side=action,
                current_price=current_price,
                confidence=signal.get("score", 0) / 10.0,
                reason=signal.get("reason", ""),
                strategy=self.strategy,
                score=signal.get("score", 0),
            )
        else:
            return TradeCommand.hold(
                symbol=self.symbol,
                current_price=current_price,
                reason=signal.get("reason", ""),
                strategy=self.strategy,
            )

    def _check_exit_command(self, klines, index: int, current_price: float,
                            cached_indicators: Optional[Dict[str, Any]] = None) -> Optional[TradeCommand]:
        """Проверяет стратегические условия выхода и возвращает TradeCommand.close() или None.

        Для MACDX стратегии делегирует в MacdxSignalGenerator.should_close(),
        чтобы бэктест использовал ту же 7-уровневую систему выходов, что и live.
        """
        if self.symbol not in self.simulator.positions:
            return None

        position = self.simulator.positions[self.symbol]
        side = position["side"]

        # Для MACDX используем полноценную систему выходов из live-кода
        if self._macdx_signal_gen is not None:
            indicators = cached_indicators or self.signal_generator.calculate_indicators(klines, index)

            # Формируем analysis dict совместимый с should_close()
            analysis = dict(indicators)
            analysis["current_price"] = current_price

            # Добавляем open_prices если отсутствуют (нужны для impulse candle detection)
            if "open_prices" not in analysis:
                analysis["open_prices"] = [k["openPrice"] for k in klines[:index + 1]]

            # Формируем position dict совместимый с should_close()
            bt_position = {
                "type": side,
                "entry": position["entry_price"],
                "avgPrice": position["entry_price"],
            }

            close_signal = self._macdx_signal_gen.should_close(
                analysis, bt_position, exit_context=self._exit_context
            )

            if close_signal.get("should_close"):
                reason = close_signal.get("reason", "Strategy exit")
                self._exit_context.clear()
                return TradeCommand.close(
                    symbol=self.symbol, current_price=current_price,
                    reason=reason, strategy=self.strategy,
                )
            return None

        # Fallback для не-MACDX стратегий: простые правила
        indicators = cached_indicators or self.signal_generator.calculate_indicators(klines, index)
        rsi = indicators.get("rsi", 50)
        macd_hist = indicators.get("macd_hist", 0)

        exit_rules = self.backtest_config.get("exit_rules", {})

        # MACD reversal
        macd_rules = exit_rules.get("macd_reversal", {})
        if macd_rules.get("enabled", True):
            profit_threshold = macd_rules.get("profit_threshold", 0.005)
            loss_threshold = macd_rules.get("loss_threshold", -0.01)
            macd_hist_prev = position.get("macd_hist_prev", 0)
            if (side == "LONG" and macd_hist < 0 and macd_hist_prev >= 0) or \
               (side == "SHORT" and macd_hist > 0 and macd_hist_prev <= 0):
                if position["unrealized_pnl"] >= profit_threshold or position["unrealized_pnl"] < loss_threshold:
                    return TradeCommand.close(
                        symbol=self.symbol, current_price=current_price,
                        reason="MACD reversal", strategy=self.strategy,
                    )

        # RSI extreme
        rsi_rules = exit_rules.get("rsi_extreme", {})
        if rsi_rules.get("enabled", True):
            long_exit = rsi_rules.get("long_exit_above", 80)
            short_exit = rsi_rules.get("short_exit_below", 20)
            if (side == "LONG" and rsi > long_exit) or (side == "SHORT" and rsi < short_exit):
                return TradeCommand.close(
                    symbol=self.symbol, current_price=current_price,
                    reason="RSI extreme", strategy=self.strategy,
                )

        return None

    def _build_result(self, klines) -> Dict[str, Any]:
        """Формирует результат бэктеста."""
        metrics = self.simulator.get_metrics()
        trades = self.simulator.pnl_tracker.trades
        winning_trades = sum(1 for t in trades if t["pnl"] > 0)
        losing_trades = len(trades) - winning_trades
        buy_trades = sum(1 for t in trades if t["side"] == "LONG")
        sell_trades = sum(1 for t in trades if t["side"] == "SHORT")
        period = f"{klines[0]['snapshotTimeUTC']} - {klines[-1]['snapshotTimeUTC']}" if klines else "N/A"
        timestamp = datetime.datetime.now().isoformat()

        result = {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "timestamp": timestamp,
            "data_period": period,
            "pnl": round(metrics["total_pnl_without_commissions"], 2),
            "net_pnl": round(metrics["total_pnl"], 2),
            "total_commission": round(metrics["total_commission"], 2),
            "win_rate": round(metrics["win_rate"], 2),
            "total_trades": metrics["total_trades"],
            "profitable_trades": winning_trades,
            "losing_trades": losing_trades,
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "sharpe_ratio": round(metrics["sharpe_ratio"], 2),
            "max_drawdown": round(metrics["max_drawdown"], 2),
            "commands_issued": len(self.simulator.command_history),
        }
        result["description"] = self._generate_description(result)
        self._save_report(result)
        return result

    def _generate_description(self, result: Dict[str, Any]) -> str:
        """Генерирует текстовое описание результатов бэктеста."""
        desc = f"""
## Результаты бэктеста

**Символ:** {result.get('symbol', 'N/A')}
**Стратегия:** {result.get('strategy', 'N/A')}
**Время выполнения:** {result.get('timestamp', 'N/A')}
**Период данных:** {result.get('data_period', 'N/A')}

### Финансовые результаты
- **P&L (без комиссий):** {result.get('pnl', 0):.2f} USDT
- **Net P&L (с комиссиями):** {result.get('net_pnl', 0):.2f} USDT
- **Общие комиссии:** {result.get('total_commission', 0):.2f} USDT

### Статистика сделок
- **Всего сделок:** {result.get('total_trades', 0)}
- **Прибыльных сделок:** {result.get('profitable_trades', 0)}
- **Убыточных сделок:** {result.get('losing_trades', 0)}
- **Процент побед:** {result.get('win_rate', 0):.1%}
- **BUY сделок:** {result.get('buy_trades', 0)}
- **SELL сделок:** {result.get('sell_trades', 0)}

### Риск-метрики
- **Sharpe Ratio:** {result.get('sharpe_ratio', 0):.2f}
- **Максимальная просадка:** {result.get('max_drawdown', 0):.2f} USDT
- **TradeCommand команд:** {result.get('commands_issued', 0)}

### Оценка
{f"Стратегия показала {'хорошие' if result.get('win_rate', 0) > 0.6 and result.get('sharpe_ratio', 0) > 1 else 'средние' if result.get('win_rate', 0) > 0.5 else 'плохие'} результаты. " +
 "Рекомендуется дальнейшее тестирование." if result.get('total_trades', 0) > 0 else "Нет сделок для анализа."}
"""
        return desc.strip()

    def _save_report(self, result: Dict[str, Any]):
        """Сохраняет отчет в data/backtest_result.json, добавляя в массив."""
        try:
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "backtest_result.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        data.append(result)
                    else:
                        data = [data, result]
                except json.JSONDecodeError:
                    data = [result]
            else:
                data = [result]
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
            print(f"📄 Отчет добавлен в {path} (всего {len(data)} результатов)")
        except Exception as e:
            print(f"❌ Ошибка сохранения отчета: {e}")
