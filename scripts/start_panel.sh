#!/usr/bin/env bash
set -euo pipefail

# OpenProducerBot: запуск панели одной командой.
#
# Режимы:
#   ngrok  (default) — локальная разработка, ngrok для HTTPS
#   tunnel           — VPS tunnel (SSH + cloudflared)
#   prod             — продакшен, URL уже в .env
#
# Автодетект (без аргумента):
#   TELEGRAM_PANEL_URL задан в .env → prod
#   Не задан → ngrok
#
# Принцип: .env — конфигурация пользователя. Скрипты НИКОГДА не пишут в .env.
# URL туннеля передаётся через shell environment → docker-compose environment override.
#
# Использование: ./scripts/start_panel.sh [ngrok|tunnel|prod]

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
NGROK_PID_FILE="/tmp/opb-ngrok.pid"
CF_URL_FILE="/tmp/opb-cloudflare-url.txt"
NGROK_BINARY="$PROJECT_ROOT/ngrok"

_env_val() {
    local key="$1"
    if [ -f "$ENV_FILE" ]; then
        grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" | xargs || true
    fi
}

# --- Определяем режим ---
MODE="${1:-}"

if [ -z "$MODE" ]; then
    # Автодетект: если TELEGRAM_PANEL_URL задан в .env → prod
    env_url=$(_env_val TELEGRAM_PANEL_URL)
    if [ -n "$env_url" ]; then
        MODE="prod"
    else
        echo "Выбери режим запуска:"
        echo "  1) ngrok   — локальная разработка, ngrok для HTTPS"
        echo "  2) tunnel  — VPS tunnel (SSH + cloudflared)"
        echo "  3) prod    — URL уже задан в .env"
        echo ""
        read -rp "Режим [1/2/3]: " choice
        case "$choice" in
            1|ngrok)  MODE="ngrok" ;;
            2|tunnel) MODE="tunnel" ;;
            3|prod)   MODE="prod" ;;
            *)
                echo "ОШИБКА: неизвестный выбор: $choice"
                exit 1
                ;;
        esac
    fi
fi

echo "=== OpenProducerBot Panel ==="
echo "  Режим: $MODE"
echo ""

# --- Функции для каждого режима ---

start_ngrok() {
    # Проверяем что ngrok бинарник существует
    if [ ! -f "$NGROK_BINARY" ]; then
        echo "ОШИБКА: ngrok бинарник не найден: $NGROK_BINARY"
        exit 1
    fi

    # Делаем бинарник исполняемым
    chmod +x "$NGROK_BINARY"

    local port="${PANEL_PORT:-$(_env_val PANEL_PORT)}"
    port="${port:-8080}"

    # Всегда запрашиваем токен (не сохраняем в .env)
    echo "Введи ngrok authtoken (получи на https://dashboard.ngrok.com/auth):"
    read -rsp "Authtoken: " ngrok_token
    echo ""

    if [ -z "$ngrok_token" ]; then
        echo "ОШИБКА: токен не введён."
        exit 1
    fi

    # Авторизуем (токен сохраняется в ~/.ngrok2/ngrok.yml, но это локально для пользователя)
    "$NGROK_BINARY" config add-authtoken "$ngrok_token" 2>/dev/null || true

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
        echo "Проверь логи: cat /tmp/opb-ngrok.log"
        kill "$ngrok_pid" 2>/dev/null || true
        rm -f "$NGROK_PID_FILE"
        exit 1
    fi

    echo "  ngrok URL: $url"
    export TELEGRAM_PANEL_URL="$url"
}

start_tunnel() {
    # Спрашиваем IP и пароль VPS
    read -rp "VPS IP or Domain: " input_host
    if [ -z "$input_host" ]; then
        echo "ОШИБКА: адрес не указан."
        exit 1
    fi

    read -rsp "VPS пароль: " input_pass
    echo ""

    if [ -z "$input_pass" ]; then
        echo "ОШИБКА: пароль не указан."
        exit 1
    fi

    export VPS_HOST="$input_host"
    export VPS_PASSWORD="$input_pass"

    "$PROJECT_ROOT/scripts/tunnel.sh" start || {
        echo ""
        echo "ОШИБКА: Туннель не запущен."
        exit 1
    }

    # Читаем URL из файла, оставленного tunnel.sh
    if [ -f "$CF_URL_FILE" ]; then
        local url
        url=$(cat "$CF_URL_FILE")
        export TELEGRAM_PANEL_URL="$url"
        echo "  Tunnel URL: $url"
    else
        echo "ВНИМАНИЕ: Файл URL туннеля не найден ($CF_URL_FILE)"
    fi
}

start_prod() {
    local url="${TELEGRAM_PANEL_URL:-$(_env_val TELEGRAM_PANEL_URL)}"
    if [ -z "$url" ]; then
        echo "ВНИМАНИЕ: TELEGRAM_PANEL_URL не задан в .env"
        echo "  Кнопка 'Open Panel' в Telegram не будет работать."
        echo "  Задай URL в .env или используй другой режим."
        echo ""
    else
        echo "  URL: $url"
    fi
    # В prod URL берётся из .env через docker-compose env_file — export не нужен
}

# --- Запуск ---

case "$MODE" in
    ngrok)
        start_ngrok
        ;;
    tunnel)
        start_tunnel
        ;;
    prod)
        start_prod
        ;;
    *)
        echo "Использование: $0 [ngrok|tunnel|prod]"
        echo ""
        echo "  ngrok   — локальная разработка, ngrok для HTTPS (default)"
        echo "  tunnel  — VPS tunnel (SSH + cloudflared)"
        echo "  prod    — продакшен, URL уже в .env"
        exit 1
        ;;
esac

# --- Контейнер ---
echo ""
echo "Запуск контейнера..."
cd "$PROJECT_ROOT"
podman-compose down 2>/dev/null || true
podman-compose up --build -d

echo ""
echo "=== Панель запущена ==="
echo "  Локально: http://localhost:${PANEL_PORT:-8080}"

if [ -n "${TELEGRAM_PANEL_URL:-}" ]; then
    echo "  Панель:   $TELEGRAM_PANEL_URL"
fi
echo ""
echo "Логи: podman-compose logs -f"
echo "Стоп: ./scripts/stop_panel.sh"
