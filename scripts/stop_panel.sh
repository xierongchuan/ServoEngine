#!/usr/bin/env bash
set -euo pipefail

# OpenProducerBot: остановка панели.
# 1. Останавливает контейнер
# 2. Останавливает ngrok (если был)
# 3. Останавливает SSH tunnel + cloudflared на VPS (если был)
#
# НЕ трогает .env — файл конфигурации пользователя.

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NGROK_PID_FILE="/tmp/opb-ngrok.pid"

echo "=== Остановка панели ==="

echo "Останавливаю контейнер..."
cd "$PROJECT_ROOT"
podman-compose down 2>/dev/null || true

# Останавливаем ngrok (если был запущен)
if [ -f "$NGROK_PID_FILE" ]; then
    pid=$(cat "$NGROK_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        echo "Останавливаю ngrok (PID: $pid)..."
        kill "$pid" 2>/dev/null || true
    fi
    rm -f "$NGROK_PID_FILE"
fi

# Останавливаем VPS tunnel (если был)
echo "Останавливаю туннель..."
"$PROJECT_ROOT/scripts/tunnel.sh" stop 2>/dev/null || true

echo ""
echo "Панель остановлена."
