#!/usr/bin/env bash
set -euo pipefail

# OpenProducerBot: туннель для Telegram Panel.
#
# Схема:
#   localhost:PANEL_PORT → SSH tunnel → VPS:VPS_PORT → cloudflared → Cloudflare (HTTPS)
#   Telegram Mini App получает валидный HTTPS URL от Cloudflare.
#
# nginx НЕ нужен — cloudflared сам обеспечивает HTTPS.
# Все порты проекта на VPS изолированы (по умолчанию 9080, настраивается через VPS_PORT).
#
# Использование: ./scripts/tunnel.sh [start|stop|status|restart]

# --- Настройки ---
SSH_PID_FILE="/tmp/opb-ssh-tunnel.pid"
CF_URL_FILE="/tmp/opb-cloudflare-url.txt"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

# Уникальный тег для поиска наших процессов на VPS
OPB_CF_TAG="opb-panel-cf"

_env_val() {
    local key="$1"
    if [ -f "$ENV_FILE" ]; then
        grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" | xargs || true
    fi
}

# Загрузка настроек из .env
REMOTE_HOST="${VPS_HOST:-$(_env_val VPS_HOST)}"
REMOTE_USER="${VPS_USER:-$(_env_val VPS_USER)}"
REMOTE_PORT="${VPS_PORT:-$(_env_val VPS_PORT)}"
SSH_KEY="${VPS_SSH_KEY:-$(_env_val VPS_SSH_KEY)}"
LOCAL_PORT="${PANEL_PORT:-$(_env_val PANEL_PORT)}"

REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_PORT="${REMOTE_PORT:-9080}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/dev_tunnel}"
LOCAL_PORT="${LOCAL_PORT:-8080}"

SSH_KEY="${SSH_KEY/#\~/$HOME}"

# --- SSH-команда ---
_ssh() {
    ssh -i "$SSH_KEY" -o "ConnectTimeout=10" -o "BatchMode=yes" \
        -o "StrictHostKeyChecking=accept-new" \
        "${REMOTE_USER}@${REMOTE_HOST}" "$@"
}

# --- URL в .env ---
_update_panel_url() {
    local url="$1"
    if [ -f "$ENV_FILE" ]; then
        if grep -q "^TELEGRAM_PANEL_URL=" "$ENV_FILE"; then
            sed -i "s|^TELEGRAM_PANEL_URL=.*|TELEGRAM_PANEL_URL=${url}|" "$ENV_FILE"
        else
            echo "TELEGRAM_PANEL_URL=${url}" >> "$ENV_FILE"
        fi
    fi
}

# --- Проверки ---
_check_prereqs() {
    if [ -z "$REMOTE_HOST" ]; then
        echo "ОШИБКА: VPS_HOST не задан."
        echo "Укажи в .env: VPS_HOST=1.2.3.4"
        return 1
    fi
    if [ ! -f "$SSH_KEY" ]; then
        echo "ОШИБКА: SSH-ключ не найден: $SSH_KEY"
        echo "Создай: ssh-keygen -t ed25519 -f $SSH_KEY -N ''"
        echo "Скопируй: ssh-copy-id -i $SSH_KEY ${REMOTE_USER}@${REMOTE_HOST}"
        return 1
    fi
}

# --- Запуск ---
start_tunnel() {
    _check_prereqs

    # Проверяем, не запущен ли уже
    if [ -f "$SSH_PID_FILE" ] && kill -0 "$(cat "$SSH_PID_FILE")" 2>/dev/null; then
        local existing_url=""
        [ -f "$CF_URL_FILE" ] && existing_url=$(cat "$CF_URL_FILE")
        echo "Туннель уже запущен (SSH PID: $(cat "$SSH_PID_FILE"))"
        [ -n "$existing_url" ] && echo "  Панель: $existing_url"
        return 0
    fi

    echo "=== OpenProducerBot Tunnel ==="
    echo "  Локально:  localhost:${LOCAL_PORT}"
    echo "  VPS:       ${REMOTE_HOST}:${REMOTE_PORT}"
    echo ""

    # 1. Очистка на VPS: старый cloudflared + занятый порт
    echo "[1/3] Очистка на VPS..."
    _ssh "pkill -f '${OPB_CF_TAG}' 2>/dev/null; fuser -k ${REMOTE_PORT}/tcp 2>/dev/null; true" 2>/dev/null || true
    sleep 1

    # 2. Запуск SSH reverse tunnel
    echo "[2/3] SSH tunnel: localhost:${LOCAL_PORT} → VPS:${REMOTE_PORT}..."
    if ! ssh -f -N \
        -R "127.0.0.1:${REMOTE_PORT}:localhost:${LOCAL_PORT}" \
        -i "$SSH_KEY" \
        -o "ConnectTimeout=10" \
        -o "BatchMode=yes" \
        -o "ServerAliveInterval=30" \
        -o "ServerAliveCountMax=3" \
        -o "ExitOnForwardFailure=yes" \
        -o "StrictHostKeyChecking=accept-new" \
        -o "ConnectionAttempts=3" \
        "${REMOTE_USER}@${REMOTE_HOST}" 2>&1; then
        echo "ОШИБКА: SSH-подключение не удалось"
        return 1
    fi

    sleep 1
    local ssh_pid
    ssh_pid=$(pgrep -f "ssh.*-R.*${REMOTE_PORT}:localhost:${LOCAL_PORT}.*${REMOTE_HOST}" | head -1)
    if [ -z "$ssh_pid" ]; then
        echo "ОШИБКА: SSH tunnel не запустился"
        return 1
    fi
    echo "$ssh_pid" > "$SSH_PID_FILE"
    echo "  SSH tunnel запущен (PID: $ssh_pid)"

    # 3. Запуск cloudflared на VPS
    echo "[3/3] Cloudflare tunnel → VPS:${REMOTE_PORT}..."

    # Запускаем cloudflared с уникальным тегом в имени процесса
    # (bash -c с exec позволяет pkill по тегу)
    local cf_url
    cf_url=$(_ssh "
        nohup bash -c 'exec -a ${OPB_CF_TAG} cloudflared tunnel --url http://127.0.0.1:${REMOTE_PORT} --no-autoupdate' \
            > /tmp/${OPB_CF_TAG}.log 2>&1 &
        # Ждём появления URL (до 15 секунд)
        for i in \$(seq 1 15); do
            sleep 1
            url=\$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/${OPB_CF_TAG}.log 2>/dev/null | head -1)
            if [ -n \"\$url\" ]; then
                echo \"\$url\"
                exit 0
            fi
        done
        echo 'TIMEOUT'
    " 2>/dev/null)

    if [ -z "$cf_url" ] || [ "$cf_url" = "TIMEOUT" ]; then
        echo "ОШИБКА: cloudflared не выдал URL за 15 секунд"
        echo "Проверь: ssh ... 'cat /tmp/${OPB_CF_TAG}.log'"
        # Откатываем SSH tunnel
        kill "$ssh_pid" 2>/dev/null || true
        rm -f "$SSH_PID_FILE"
        return 1
    fi

    echo "$cf_url" > "$CF_URL_FILE"

    # 4. Обновляем .env
    _update_panel_url "$cf_url"

    echo ""
    echo "=== Туннель запущен ==="
    echo "  SSH:    localhost:${LOCAL_PORT} → VPS:${REMOTE_PORT} (PID: $ssh_pid)"
    echo "  HTTPS:  $cf_url"
    echo "  .env:   TELEGRAM_PANEL_URL обновлён"
    echo ""
    echo "Запусти панель: ./scripts/start.sh"
}

# --- Остановка ---
stop_tunnel() {
    local stopped=false

    # Останавливаем cloudflared на VPS
    if [ -n "$REMOTE_HOST" ] && [ -f "$SSH_KEY" ]; then
        echo "Останавливаю cloudflared на VPS..."
        _ssh "pkill -f '${OPB_CF_TAG}' 2>/dev/null; true" 2>/dev/null || true
        stopped=true
    fi

    # Останавливаем SSH tunnel
    if [ -f "$SSH_PID_FILE" ]; then
        local pid
        pid=$(cat "$SSH_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Останавливаю SSH tunnel (PID: $pid)..."
            kill "$pid"
            stopped=true
        fi
        rm -f "$SSH_PID_FILE"
    fi

    # Убиваем зависшие SSH-процессы
    local stale_pids
    stale_pids=$(pgrep -f "ssh.*-R.*${REMOTE_PORT}:localhost:${LOCAL_PORT}.*${REMOTE_HOST}" 2>/dev/null || true)
    if [ -n "$stale_pids" ]; then
        echo "Убиваю зависшие SSH-процессы: $stale_pids"
        echo "$stale_pids" | xargs kill 2>/dev/null || true
        stopped=true
    fi

    # Очищаем URL
    _update_panel_url ""
    rm -f "$CF_URL_FILE"

    if $stopped; then
        echo "Туннель остановлен."
    else
        echo "Туннель не запущен."
    fi
}

# --- Статус ---
status_tunnel() {
    local ssh_ok=false
    local cf_ok=false

    # SSH tunnel
    if [ -f "$SSH_PID_FILE" ] && kill -0 "$(cat "$SSH_PID_FILE")" 2>/dev/null; then
        echo "SSH tunnel:  ЗАПУЩЕН (PID: $(cat "$SSH_PID_FILE"))"
        echo "  localhost:${LOCAL_PORT} → ${REMOTE_HOST}:${REMOTE_PORT}"
        ssh_ok=true
    else
        echo "SSH tunnel:  НЕ запущен"
        [ -f "$SSH_PID_FILE" ] && rm -f "$SSH_PID_FILE"
    fi

    # Cloudflared
    if [ -n "$REMOTE_HOST" ] && [ -f "$SSH_KEY" ]; then
        local cf_pid
        cf_pid=$(_ssh "pgrep -f '${OPB_CF_TAG}' 2>/dev/null | head -1" 2>/dev/null || true)
        if [ -n "$cf_pid" ]; then
            echo "Cloudflared: ЗАПУЩЕН на VPS (PID: $cf_pid)"
            cf_ok=true
        else
            echo "Cloudflared: НЕ запущен на VPS"
        fi
    fi

    # URL
    if [ -f "$CF_URL_FILE" ]; then
        echo "  Панель: $(cat "$CF_URL_FILE")"
    fi

    if $ssh_ok && $cf_ok; then
        return 0
    else
        return 1
    fi
}

# --- Точка входа ---
case "${1:-start}" in
    start)   start_tunnel ;;
    stop)    stop_tunnel ;;
    status)  status_tunnel ;;
    restart)
        stop_tunnel
        sleep 2
        start_tunnel
        ;;
    *)
        echo "Использование: $0 {start|stop|status|restart}"
        echo ""
        echo "  start   — SSH tunnel + cloudflared → HTTPS URL"
        echo "  stop    — остановить всё"
        echo "  status  — проверить состояние"
        echo "  restart — перезапустить"
        exit 1
        ;;
esac
