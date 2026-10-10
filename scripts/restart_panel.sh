#!/usr/bin/env bash
set -euo pipefail

# Servo Engine: перезапуск панели.
# 1. Останавливает контейнер
# 2. Запускает ngrok если нужно
# 3. Пересобирает и запускает контейнер с новым билдом
# 4. Передаёт URL в контейнер

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
NGROK_PID_FILE="/tmp/opb-ngrok.pid"
NGROK_BINARY="$PROJECT_ROOT/ngrok"

_env_val() {
    local key="$1"
    if [ -f "$ENV_FILE" ]; then
        grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" | xargs || true
    fi
}

echo "=== Перезапуск панели ==="

# Определяем режим
MODE="${1:-}"

if [ -z "$MODE" ]; then
    env_url=$(_env_val TELEGRAM_PANEL_URL)
    if [ -n "$env_url" ]; then
        MODE="prod"
    elif [ -f "$NGROK_PID_FILE" ]; then
        MODE="ngrok"
    else
        MODE="prod"
    fi
fi

echo "  Режим: $MODE"

# Запускаем ngrok если нужен
start_ngrok_if_needed() {
    if [ "$MODE" != "ngrok" ]; then
        return
    fi

    # Проверяем что ngrok бинарник существует
    if [ ! -f "$NGROK_BINARY" ]; then
        echo "ОШИБКА: ngrok бинарник не найден: $NGROK_BINARY"
        exit 1
    fi

    chmod +x "$NGROK_BINARY"

    local port="${PANEL_PORT:-$(_env_val PANEL_PORT)}"
    port="${port:-8080}"

    # Убиваем старый ngrok если есть
    if [ -f "$NGROK_PID_FILE" ]; then
        local old_pid
        old_pid=$(cat "$NGROK_PID_FILE")
        if kill -0 "$old_pid" 2>/dev/null; then
            echo "Останавливаю старый ngrok (PID: $old_pid)..."
            kill "$old_pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$NGROK_PID_FILE"
    fi

    # Запускаем ngrok в фоне
    echo "Запускаю ngrok http $port..."
    "$NGROK_BINARY" http "$port" --log=stdout > /tmp/opb-ngrok.log 2>&1 &
    local ngrok_pid=$!
    echo "$ngrok_pid" > "$NGROK_PID_FILE"

    # Ждём URL через ngrok API (до 10 секунд)
    echo "Жду URL от ngrok..."
    local url=""
    for i in $(seq 1 10); do
        sleep 1
        url=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
            | grep -oP '"public_url"\s*:\s*"https://[^"]+' \
            | head -1 \
            | sed 's/"public_url"\s*:\s*"//' || true)
        if [ -n "$url" ]; then
            break
        fi
    done

    if [ -z "$url" ]; then
        echo "ОШИБКА: ngrok не выдал URL за 10 секунд"
        kill "$ngrok_pid" 2>/dev/null || true
        rm -f "$NGROK_PID_FILE"
        exit 1
    fi

    echo "  ngrok URL: $url"
    export TELEGRAM_PANEL_URL="$url"
}

# Останавливаем контейнер
echo "Останавливаю контейнер..."
cd "$PROJECT_ROOT"
podman compose --profile panel stop panel

# Запускаем ngrok если нужен
start_ngrok_if_needed

# Пересобираем и запускаем контейнер
echo "Пересобираю и запускаю контейнер..."
mkdir -p data/prices data/news data/logs charts
podman compose --profile panel up --build --force-recreate -d --no-deps panel

echo ""
echo "=== Панель перезапущена ==="
echo "  Локально: http://localhost:${PANEL_PORT:-8080}"

if [ -n "${TELEGRAM_PANEL_URL:-}" ]; then
    echo "  Панель:   $TELEGRAM_PANEL_URL"
fi
echo ""
echo "Логи: podman compose logs -f panel"
echo "Стоп: ./scripts/stop_panel.sh"
