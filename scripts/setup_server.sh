#!/usr/bin/env bash
set -euo pipefail

# OpenProducerBot: одноразовая настройка VPS для Telegram Panel.
# Устанавливает nginx + self-signed SSL, настраивает проксирование HTTPS → localhost.
#
# Использование: ./scripts/setup_server.sh
#
# Скрипт идемпотентен — можно запускать повторно.

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

# --- Загрузка переменных из .env ---

_env_val() {
    # Извлекает значение переменной из .env
    local key="$1"
    if [ -f "$ENV_FILE" ]; then
        grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" | xargs || true
    fi
}

VPS_HOST="${VPS_HOST:-$(_env_val VPS_HOST)}"
VPS_USER="${VPS_USER:-$(_env_val VPS_USER)}"
VPS_PORT="${VPS_PORT:-$(_env_val VPS_PORT)}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$(_env_val VPS_SSH_KEY)}"

# Значения по умолчанию
VPS_USER="${VPS_USER:-root}"
VPS_PORT="${VPS_PORT:-9080}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/dev_tunnel}"

# Раскрываем ~ в пути к ключу
VPS_SSH_KEY="${VPS_SSH_KEY/#\~/$HOME}"

# --- Проверки ---

if [ -z "$VPS_HOST" ]; then
    echo "ОШИБКА: VPS_HOST не задан."
    echo "Укажи в .env или через переменную окружения: VPS_HOST=1.2.3.4 ./scripts/setup_server.sh"
    exit 1
fi

if [ ! -f "$VPS_SSH_KEY" ]; then
    echo "ОШИБКА: SSH-ключ не найден: $VPS_SSH_KEY"
    echo "Создай ключ: ssh-keygen -t ed25519 -f $VPS_SSH_KEY -N ''"
    echo "И скопируй на сервер: ssh-copy-id -i $VPS_SSH_KEY ${VPS_USER}@${VPS_HOST}"
    exit 1
fi

echo "=== Настройка VPS для Telegram Panel ==="
echo "  Сервер:  ${VPS_USER}@${VPS_HOST}"
echo "  Порт:    ${VPS_PORT}"
echo "  SSH-ключ: ${VPS_SSH_KEY}"
echo ""

# --- SSH-команда ---

_ssh() {
    ssh -i "$VPS_SSH_KEY" -o "ConnectTimeout=10" -o "BatchMode=yes" \
        -o "StrictHostKeyChecking=accept-new" \
        "${VPS_USER}@${VPS_HOST}" "$@"
}

# --- Установка и настройка на сервере ---

echo "[1/4] Установка nginx..."
_ssh "which nginx >/dev/null 2>&1 && echo 'nginx уже установлен' || (apt-get update -qq && apt-get install -y -qq nginx)"

echo "[2/4] Генерация SSL-сертификата..."
_ssh "
if [ -f /etc/ssl/certs/panel.crt ] && [ -f /etc/ssl/private/panel.key ]; then
    echo 'SSL-сертификат уже существует'
else
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout /etc/ssl/private/panel.key \
        -out /etc/ssl/certs/panel.crt \
        -subj '/CN=${VPS_HOST}' 2>/dev/null
    echo 'SSL-сертификат создан'
fi
"

echo "[3/4] Настройка nginx..."
_ssh "
cat > /etc/nginx/sites-available/panel <<'NGINX'
server {
    listen 443 ssl;
    server_name ${VPS_HOST};

    ssl_certificate     /etc/ssl/certs/panel.crt;
    ssl_certificate_key /etc/ssl/private/panel.key;

    location / {
        proxy_pass http://127.0.0.1:${VPS_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \"upgrade\";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
NGINX

# Symlink (идемпотентно)
ln -sf /etc/nginx/sites-available/panel /etc/nginx/sites-enabled/panel

# Удаляем default если есть (не мешает, но чище)
rm -f /etc/nginx/sites-enabled/default

echo 'Конфиг nginx обновлён'
"

echo "[4/4] Открытие порта 443 и перезагрузка nginx..."
_ssh "
# ufw — если активен, открываем 443
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then
    ufw allow 443/tcp >/dev/null 2>&1 || true
    echo 'ufw: порт 443 открыт'
else
    echo 'ufw не активен — пропускаем'
fi

# Проверяем конфиг и перезагружаем
nginx -t 2>&1
systemctl reload nginx
echo 'nginx перезагружен'
"

echo ""
echo "=== Готово! ==="
echo "  HTTPS: https://${VPS_HOST}"
echo "  Nginx проксирует 443 → 127.0.0.1:${VPS_PORT}"
echo ""
echo "Теперь запусти туннель: ./scripts/tunnel.sh start"
