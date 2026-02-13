#!/usr/bin/env bash
set -euo pipefail

# OpenProducerBot: запуск панели одной командой.
# 1. Поднимает SSH tunnel + cloudflared (→ HTTPS URL)
# 2. Запускает/перезапускает контейнер с панелью (подхватывает новый URL)
#
# Использование: ./scripts/start.sh

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== OpenProducerBot Panel ==="
echo ""

# --- Туннель ---
"$PROJECT_ROOT/scripts/tunnel.sh" start || {
    echo ""
    echo "ВНИМАНИЕ: Туннель не запущен. Панель будет доступна только локально."
    echo ""
}

# --- Контейнер (пересоздаём чтобы подхватить обновлённый TELEGRAM_PANEL_URL) ---
echo ""
echo "Запуск контейнера..."
cd "$PROJECT_ROOT"
podman-compose down 2>/dev/null || true
podman-compose up --build -d

echo ""
echo "=== Панель запущена ==="
echo "  Локально: http://localhost:${PANEL_PORT:-8080}"

# Показываем Cloudflare URL
CF_URL_FILE="/tmp/opb-cloudflare-url.txt"
if [ -f "$CF_URL_FILE" ]; then
    echo "  Панель:   $(cat "$CF_URL_FILE")"
fi
echo ""
echo "Логи: podman-compose logs -f"
echo "Стоп: ./scripts/stop.sh"
