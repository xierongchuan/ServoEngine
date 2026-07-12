import os
import json
import logging
import datetime
import traceback
from typing import Dict, Any, Optional, List

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from .data_loader import DataLoader
from .signals import SignalGenerator
from .simulator import BacktestSimulator
from ..config_loader import resolve_symbol_config, load_backtest_config
from ..core.commands.models import TradeCommand, TradeAction
from ..utils.logger import info, error

class BacktestEngine:
    """
    Основной движок бэктеста, объединяет все компоненты.

    Работает через единый TradeCommand DTO — strategy-agnostic:
    SignalGenerator.generate_signal(position=...) → TradeCommand → BacktestSimulator.execute()

    SignalGenerator является адаптером, который:
    - Генерирует сигналы входа через стратегию (generate)
    - Проверяет условия выхода через стратегию (should_close)
    - Возвращает единый dict {action: BUY/SELL/HOLD/CLOSE, ...}

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

        # Capital: CLI arg > config/backtest.json > 1000.0
        capital_config = self.backtest_config.get("capital", {})
        balance = initial_balance or capital_config.get("initial_balance", 1000.0)
        capital_mode = capital_config.get("mode", "isolated")

        # Commission rates from config
        commission_config = self.backtest_config.get("commission", {})
        maker_rate = commission_config.get("maker_rate", 0.0002)
        taker_rate = commission_config.get("taker_rate", 0.0005)
        if commission_config.get("use_exchange_rates", True):
            exchange_name = os.getenv("EXCHANGE", "bingx").lower()
            market_type = os.getenv("MARKET_TYPE", "perpetual").lower()
            fee_cfg = self.config.get("exchange", {}).get("fees", {}).get(exchange_name, {})
            if isinstance(fee_cfg, dict) and market_type in fee_cfg:
                fee_cfg = fee_cfg[market_type]
            if isinstance(fee_cfg, dict):
                maker_rate = float(fee_cfg.get("maker", maker_rate * 100)) / 100
                taker_rate = float(fee_cfg.get("taker", taker_rate * 100)) / 100

        # Default SL/TP: приоритет - preset стратегии → backtest.json
        defaults_config = self.backtest_config.get("defaults", {})
        sl_from_config = preset.get("sl_percent")
        tp_from_config = preset.get("tp_percent")
        default_sl = sl_from_config if sl_from_config is not None else defaults_config.get("sl_percent", 1.0)
        default_tp = tp_from_config if tp_from_config is not None else defaults_config.get("tp_percent", 5.0)

        self.simulator = BacktestSimulator(
            initial_balance=balance,
            leverage=preset.get("leverage", 5.0),
            position_size_percent=self.config.get("position", {}).get("size_percent", 10) / 100.0,
            maker_rate=maker_rate,
            taker_rate=taker_rate,
            default_sl_percent=default_sl / 100,  # Конвертируем % в долю
            default_tp_percent=default_tp / 100,
            capital_mode=capital_mode,
            slippage_bps=self.backtest_config.get("execution", {}).get("slippage_bps", 2.0),
        )
        # Equity tracking for chart generation
        self._equity_curve: List[Dict[str, Any]] = []
        self._trade_markers: List[Dict[str, Any]] = []
        self._setup_logging()

    def _load_config(self) -> Dict[str, Any]:
        """Загружает конфигурацию для символа и стратегии."""
        try:
            # CLI --strategy должен тестировать его базовый конфиг, а не
            # несовместимый активный профиль другой стратегии.
            return resolve_symbol_config(self.symbol, self.strategy, profile="default")
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
        Запускает бэктест через единый strategy-agnostic TradeCommand поток.

        Для каждой свечи:
        1. Проверить SL/TP → TradeCommand.close()
        2. Обновить unrealized P&L
        3. Сгенерировать сигнал (entry + exit через единый generate_signal) → TradeCommand
        4. Исполнить через BacktestSimulator.execute()

        Выходы из позиции обрабатываются ВНУТРИ SignalGenerator.generate_signal():
        если есть открытая позиция, SignalGenerator сначала проверяет should_close()
        стратегии и возвращает {action: "CLOSE"} если нужно закрыть.
        Движок не знает о деталях стратегии — только о TradeCommand DTO.
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
                kline_time = kline.get("snapshotTimeUTC", "")

                # 1. Проверить SL/TP через TradeCommand
                management_cmd = self._update_scalp_position(i, current_price)
                sl_tp_cmd = management_cmd or self.simulator.check_sl_tp_command(self.symbol, current_price)
                if sl_tp_cmd:
                    self.simulator.execute(sl_tp_cmd)
                    self.signal_generator.reset_exit_context()
                    self.signal_generator.set_last_close_time(kline_time)
                    self._record_equity(kline_time, current_price)
                    self._record_trade_marker(kline_time, current_price, "close", sl_tp_cmd.reason or "SL/TP")
                    continue

                # 2. Обновить unrealized P&L (без SL/TP — уже проверено выше)
                self.simulator.update_unrealized_pnl({self.symbol: current_price})

                # 3. Сгенерировать сигнал (entry + exit) через единый интерфейс
                #    SignalGenerator сам проверит should_close() если есть позиция
                try:
                    position = self.simulator.positions.get(self.symbol)
                    signal = self.signal_generator.generate_signal(klines, i, position=position)
                    command = self._signal_to_command(signal, current_price)
                    result = self.simulator.execute(command)

                    if command.action.is_entry:
                        if self.strategy.upper() == "SCALP" and self.symbol in self.simulator.positions:
                            position_state = self.simulator.positions[self.symbol]
                            position_state["entry_index"] = i
                            position_state["entry_atr"] = self.signal_generator.last_indicators.get("atr", 0)
                            position_state["best_price"] = current_price
                        self.signal_generator.reset_exit_context()
                        side = "BUY" if command.action == TradeAction.BUY else "SELL"
                        self._record_trade_marker(kline_time, current_price, side.lower(), signal.get("reason", ""))
                        info(f"📈 {command.action.value.upper()} на {self.symbol} по {current_price:.2f}")
                    elif command.action.is_exit:
                        self.signal_generator.reset_exit_context()
                        self.signal_generator.set_last_close_time(kline_time)
                        self._record_trade_marker(kline_time, current_price, "close", signal.get("reason", "strategy"))
                        # После закрытия — сразу проверить сигнал для новой позиции (если включено в конфиге)
                        reentry_enabled = self.config.get("features", {}).get("enable_immediate_reentry_after_exit", False)
                        if reentry_enabled:
                            position = None
                            signal2 = self.signal_generator.generate_signal(klines, i, position=position)
                            command2 = self._signal_to_command(signal2, current_price)
                            if command2.action.is_entry:
                                result2 = self.simulator.execute(command2)
                                side = "BUY" if command2.action == TradeAction.BUY else "SELL"
                                self.signal_generator.reset_exit_context()
                                self._record_trade_marker(kline_time, current_price, side.lower(), signal2.get("reason", ""))
                                info(f"📈 {command2.action.value.upper()} на {self.symbol} по {current_price:.2f} (after exit)")
                except Exception as e:
                    error(f"Ошибка на индексе {i}: {e}")
                    continue

                self._record_equity(kline_time, current_price)

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
            error(f"❌ Ошибка в бэктесте: {e}\n{traceback.format_exc()}")
            print(f"❌ Ошибка в бэктесте: {e}")
            traceback.print_exc()
            return {}

    def _update_scalp_position(self, index: int, current_price: float) -> Optional[TradeCommand]:
        """Свечной replay trailing/breakeven/time-exit реального SCALP."""
        if self.strategy.upper() != "SCALP" or self.symbol not in self.simulator.positions:
            return None
        position = self.simulator.positions[self.symbol]
        side = position["side"]
        entry = float(position["entry_price"])
        atr = float(position.get("entry_atr", 0) or 0)
        best = float(position.get("best_price", entry))
        best = max(best, current_price) if side == "LONG" else min(best, current_price)
        position["best_price"] = best
        pnl_pct = ((current_price - entry) / entry if side == "LONG" else (entry - current_price) / entry) * 100

        sltp = self.config.get("sl_tp", {})
        breakeven = self.config.get("breakeven", {})
        if breakeven.get("enabled", True) and pnl_pct >= float(breakeven.get("trigger_pct", 0.3)):
            fee_buffer = max(
                float(breakeven.get("fee_buffer_pct", 0.05)),
                self.simulator.commission_calculator.taker_rate * 2 * 100 + self.simulator.slippage_bps / 100,
            )
            be_price = entry * (1 + fee_buffer / 100 if side == "LONG" else 1 - fee_buffer / 100)
            if side == "LONG":
                position["sl_price"] = max(float(position.get("sl_price", 0)), be_price)
            else:
                position["sl_price"] = min(float(position.get("sl_price", float("inf"))), be_price)

        if atr > 0:
            activation = float(sltp.get("trailing_activation_mult", 1.5)) * atr
            distance = float(sltp.get("trailing_distance_mult", 0.5)) * atr
            if side == "LONG" and best - entry >= activation:
                position["sl_price"] = max(float(position.get("sl_price", 0)), best - distance)
            elif side == "SHORT" and entry - best >= activation:
                position["sl_price"] = min(float(position.get("sl_price", float("inf"))), best + distance)

        held_bars = index - int(position.get("entry_index", index))
        time_cfg = self.config.get("time_exit", {})
        max_hold = int(time_cfg.get("max_hold_minutes", 15))
        be_timeout = int(time_cfg.get("breakeven_timeout_minutes", 8))
        if max_hold > 0 and held_bars >= max_hold:
            return TradeCommand.close(self.symbol, current_price, reason="SCALP time exit", strategy="SCALP")
        if be_timeout > 0 and held_bars >= be_timeout and pnl_pct < float(breakeven.get("trigger_pct", 0.3)):
            return TradeCommand.close(self.symbol, current_price, reason="SCALP breakeven timeout", strategy="SCALP")
        return None

    def _signal_to_command(self, signal: Dict[str, Any], current_price: float) -> TradeCommand:
        """
        Конвертирует сигнал от SignalGenerator в TradeCommand.

        Поддерживает все типы действий:
        - BUY/SELL → TradeCommand.entry()
        - CLOSE → TradeCommand.close()
        - HOLD → TradeCommand.hold()
        """
        action = signal.get("action", "HOLD")

        if action == "CLOSE":
            return TradeCommand.close(
                symbol=self.symbol,
                current_price=current_price,
                reason=signal.get("reason", "Strategy exit"),
                strategy=self.strategy,
            )
        elif action in ("BUY", "SELL"):
            return TradeCommand.entry(
                symbol=self.symbol,
                side=action,
                current_price=current_price,
                confidence=signal.get("score", 0) / 10.0,
                reason=signal.get("reason", ""),
                stop_loss=signal.get("stop_loss"),
                take_profit=signal.get("take_profit"),
                size_pct=signal.get("size_pct"),
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

    def _record_equity(self, time_str: str, current_price: float):
        """Записывает точку кривой эквити."""
        unrealized = 0.0
        for pos in self.simulator.positions.values():
            unrealized += pos.get("unrealized_pnl", 0.0)
        self._equity_curve.append({
            "time": time_str,
            "balance": self.simulator.balance + unrealized,
            "price": current_price,
        })

    def _record_trade_marker(self, time_str: str, price: float, action: str, reason: str):
        """Записывает маркер сделки для графика."""
        self._trade_markers.append({
            "time": time_str,
            "price": price,
            "action": action,
            "reason": reason,
        })

    def _generate_charts(self, klines, result: Dict[str, Any]) -> Optional[str]:
        """Генерирует графики бэктеста: эквити + цена с маркерами сделок."""
        if not self._equity_curve:
            return None

        try:
            charts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                      "data", "charts")
            os.makedirs(charts_dir, exist_ok=True)

            # Парсинг времени
            def parse_time(t):
                for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        return datetime.datetime.strptime(t, fmt)
                    except ValueError:
                        continue
                return None

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={"height_ratios": [1, 1]})
            fig.suptitle(f"Backtest: {self.symbol} / {self.strategy}", fontsize=14, fontweight="bold")

            # --- Верхний график: цена + сделки ---
            prices_t = []
            prices_v = []
            for k in klines:
                t = parse_time(k.get("snapshotTimeUTC", ""))
                if t:
                    prices_t.append(t)
                    prices_v.append(k["closePrice"])

            if prices_t:
                ax1.plot(prices_t, prices_v, color="#555555", linewidth=0.8, alpha=0.8, label="Price")

            # Маркеры сделок
            buy_t, buy_p = [], []
            sell_t, sell_p = [], []
            close_t, close_p = [], []
            for m in self._trade_markers:
                t = parse_time(m["time"])
                if not t:
                    continue
                if m["action"] == "buy":
                    buy_t.append(t)
                    buy_p.append(m["price"])
                elif m["action"] == "sell":
                    sell_t.append(t)
                    sell_p.append(m["price"])
                elif m["action"] == "close":
                    close_t.append(t)
                    close_p.append(m["price"])

            if buy_t:
                ax1.scatter(buy_t, buy_p, marker="^", color="green", s=50, zorder=5, label="BUY")
            if sell_t:
                ax1.scatter(sell_t, sell_p, marker="v", color="red", s=50, zorder=5, label="SELL")
            if close_t:
                ax1.scatter(close_t, close_p, marker="x", color="orange", s=40, zorder=5, label="CLOSE")

            ax1.set_ylabel("Price (USDT)")
            ax1.legend(loc="upper left", fontsize=8)
            ax1.grid(True, alpha=0.3)
            ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

            # --- Нижний график: кривая эквити ---
            eq_t = []
            eq_v = []
            for pt in self._equity_curve:
                t = parse_time(pt["time"])
                if t:
                    eq_t.append(t)
                    eq_v.append(pt["balance"])

            if eq_t:
                initial = self.simulator.initial_balance
                colors = ["green" if v >= initial else "red" for v in eq_v]
                ax2.plot(eq_t, eq_v, color="#2196F3", linewidth=1.0, label="Equity")
                ax2.axhline(y=initial, color="gray", linestyle="--", linewidth=0.8, label=f"Initial ({initial:.0f})")
                ax2.fill_between(eq_t, eq_v, initial, where=[v >= initial for v in eq_v],
                                 color="green", alpha=0.1)
                ax2.fill_between(eq_t, eq_v, initial, where=[v < initial for v in eq_v],
                                 color="red", alpha=0.1)

            ax2.set_ylabel("Balance (USDT)")
            ax2.set_xlabel("Date")
            ax2.legend(loc="upper left", fontsize=8)
            ax2.grid(True, alpha=0.3)
            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

            # Статистика на графике
            stats_text = (
                f"Trades: {result.get('total_trades', 0)} | "
                f"Win: {result.get('win_rate', 0):.0%} | "
                f"Net P&L: {result.get('net_pnl', 0):.2f} | "
                f"MaxDD: {result.get('max_drawdown', 0):.2f}"
            )
            fig.text(0.5, 0.01, stats_text, ha="center", fontsize=9, color="#666666")

            plt.tight_layout(rect=[0, 0.03, 1, 0.96])
            chart_path = os.path.join(charts_dir, f"backtest_{self.symbol}_{self.strategy}.png")
            fig.savefig(chart_path, dpi=120, bbox_inches="tight")
            plt.close(fig)

            print(f"📊 График сохранен: {chart_path}")
            return chart_path

        except Exception as e:
            error(f"Ошибка генерации графиков: {e}")
            print(f"⚠️ Не удалось создать график: {e}")
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

        # Генерация графиков
        chart_path = self._generate_charts(klines, result)
        if chart_path:
            result["chart_path"] = chart_path

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
