#!/bin/bash
# Скрипт для автоматического запуска торгового бота в контейнере
# Автор: Claude Code

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_message() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

log_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

# Переходим в корневую директорию проекта (на уровень выше скрипта)
cd "$(dirname "$0")/.."

# Загружаем переменные из .env файла если он существует
if [ -f .env ]; then
    log_message "Загрузка переменных из .env файла..."
    source .env
fi

# Создаем директории если их нет
mkdir -p data/prices data/news data/logs charts

# Логируем начало
log_message "Запуск торгового бота OpenProducer..."
log_message "Биржа: $EXCHANGE"
log_message "Режим: $MODE"
log_message "🚀 Запуск торгового бота в контейнере..."

# Собираем образ с зависимостями (пересобирается только при изменении requirements.txt)
IMAGE_NAME="openproducer-bot"
log_message "Проверка/сборка образа $IMAGE_NAME..."
podman build -q -t "$IMAGE_NAME" .

# Исправляем права на data/ если файлы принадлежат root (от предыдущих запусков без --user)
if [ -d data ] && [ "$(stat -c '%U' data 2>/dev/null)" = "root" ]; then
    log_warning "Обнаружены файлы с правами root в data/, исправляю..."
    sudo chown -R "$(id -u):$(id -g)" data/ charts/ 2>/dev/null || true
fi

# Запускаем бота в контейнере с --init для корректной обработки Ctrl+C
# --userns=keep-id: маппинг UID хоста → тот же UID внутри контейнера (решает проблемы с правами)
if podman run --rm -it --init \
    --userns=keep-id \
    --env-file .env \
    -v .:/app:z \
    -w /app \
    "$IMAGE_NAME" \
    python3 run.py; then
    log_message "✅ Торговый бот остановлен (штатно)"
else
    log_error "❌ Торговый бот упал с ошибкой"
    log_warning "Проверьте логи: data/steps.log"
fi

log_message "Завершено. Логи сохранены в data/steps.log и data/trades.log"
