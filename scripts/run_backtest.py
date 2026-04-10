#!/usr/bin/env python3
"""
Скрипт для запуска бэктеста.
Использование: python scripts/run_backtest.py --symbol BTCUSDT --strategy MACDX --balance 1000

Бэктест работает через единый TradeCommand DTO:
SignalGenerator → TradeCommand → BacktestSimulator.execute()
"""

import argparse
import os
import sys

# Добавить src в путь
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.backtest.engine import BacktestEngine
from src.config_loader import load_backtest_config
from src.utils.logger import error, info


def main():
    backtest_config = load_backtest_config()
    default_balance = backtest_config.get("capital", {}).get("initial_balance", 1000.0)

    parser = argparse.ArgumentParser(description="Запуск бэктеста")
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Символ для бэктеста (по умолчанию первый из конфига)",
    )
    parser.add_argument(
        "--strategy", default="MACDX", help="Стратегия (по умолчанию MACDX)"
    )
    parser.add_argument(
        "--balance", type=float, default=None,
        help=f"Начальный баланс (по умолчанию {default_balance} из config/backtest.json)",
    )
    args = parser.parse_args()

    try:
        # Определить символ: если не указан, взять первый из конфига
        if args.symbol == "BTCUSDT":  # Заглушка, в реале из config
            from src.config_loader import load_active_config

            config = load_active_config()
            symbols = config.get("symbols", {}).get("bingx", [])
            if symbols:
                args.symbol = symbols[0]

        info(
            f"Запуск бэктеста для {args.symbol} стратегии {args.strategy} с балансом {args.balance}"
        )

        try:
            engine = BacktestEngine(args.symbol, args.strategy, args.balance)
            result = engine.run()
        except Exception as e:
            error(f"Exception in backtest: {e}")
            result = {}

        if result and result.get("total_trades", 0) > 0:
            print("\nРезультаты бэктеста:")
            print(f"Символ: {result.get('symbol', 'N/A')}")
            print(f"Стратегия: {result.get('strategy', 'N/A')}")
            print(f"Total P&L: {result.get('pnl', 0):.2f}")
            print(f"Net P&L: {result.get('net_pnl', 0):.2f}")
            print(f"Total Commission: {result.get('total_commission', 0):.2f}")
            print(f"Win Rate: {result.get('win_rate', 0):.2%}")
            print(f"Total Trades: {result.get('total_trades', 0)}")
            print(f"Profitable: {result.get('profitable_trades', 0)}")
            print(f"Losing: {result.get('losing_trades', 0)}")
            print(f"Sharpe Ratio: {result.get('sharpe_ratio', 0):.2f}")
            print(f"Max Drawdown: {result.get('max_drawdown', 0):.2f}")
            print(f"Commands Issued: {result.get('commands_issued', 0)}")
            print(f"Отчет сохранен в data/backtest_result.json")
            print("✅ Бэктест завершен")
        elif result:
            print(f"\nБэктест завершен: 0 сделок за период {result.get('data_period', 'N/A')}")
        else:
            print("❌ Бэктест не выполнен (нет данных или ошибка)")

    except Exception as e:
        error(f"Ошибка запуска бэктеста: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
