#!/bin/bash
# Скрипт для запуска бэктеста в контейнере
# Использование: ./run_backtest.sh [symbol] [strategy] [balance]

SYMBOL=${1:-BTCUSDT}
STRATEGY=${2:-MACDX}
BALANCE=${3:-1000}

echo "🚀 Запуск бэктеста для $SYMBOL стратегии $STRATEGY с балансом $BALANCE"

# Запуск в podman контейнере с pytest окружением
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c "
pip install -q requests pandas matplotlib &&
python -m scripts.run_backtest --symbol $SYMBOL --strategy $STRATEGY --balance $BALANCE
"
