#!/usr/bin/env bash
set -euo pipefail

# OpenProducerBot: остановка панели.
# 1. Останавливает контейнер
# 2. Останавливает SSH tunnel + cloudflared на VPS

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Остановка панели ==="

echo "Останавливаю контейнер..."
cd "$PROJECT_ROOT"
podman-compose down 2>/dev/null || true

echo "Останавливаю туннель..."
"$PROJECT_ROOT/scripts/tunnel.sh" stop 2>/dev/null || true

echo ""
echo "Панель остановлена."
