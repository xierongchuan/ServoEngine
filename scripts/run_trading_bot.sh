#!/bin/bash
# Скрипт для автоматического запуска торгового бота
# Автор: Claude Code
# Дата: 2025-11-02

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
log_message() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

log_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

# Переходим в директорию со скриптом
# Переходим в корневую директорию проекта (на уровень выше скрипта)
cd "$(dirname "$0")/.."

# Загружаем переменные из .env файла если он существует
if [ -f .env ]; then
    log_message "Загрузка переменных из .env файла..."
    source .env
fi

# Проверка наличия Python
if ! command -v python3 &> /dev/null; then
    log_error "Python3 не найден! Установите Python 3.12+"
    exit 1
fi

# Проверка наличия переменных окружения - пропущена, так как все берется из .env

# Создаем директории если их нет
mkdir -p data/prices data/news charts

# Логируем начало
log_message "Запуск торгового бота OpenProducer..."
log_message "Биржа: $EXCHANGE"
log_message "Режим: $MODE"

# Проверка аргументов запуска
SINGLE_RUN=false
if [[ "$1" == "--once" ]]; then
    SINGLE_RUN=true
    log_message "Режим одиночного запуска (для CRON)"
fi

# Бесконечный цикл запуска (или один раз если SINGLE_RUN)
while true; do
    log_message "🚀 Запуск цикла торгового бота..."

    # Запускаем бота с использованием виртуального окружения
    if ./venv/bin/python3 run.py; then
        log_message "✅ Торговый цикл успешно завершен"
    else
        log_error "❌ Торговый цикл завершился с ошибкой"
        log_warning "Проверьте логи: data/steps.log"
    fi

    # Если это одиночный запуск, выходим из цикла
    if [ "$SINGLE_RUN" = true ]; then
        break
    fi

    log_message "⏳ Ожидание 30 секунд перед следующим запуском..."
    sleep 30
done

# Логируем завершение
echo "" >> data/cron.log 2>/dev/null || true
echo "=== $(date) ===" >> data/cron.log 2>/dev/null || true
echo "Trading bot cycle completed" >> data/cron.log 2>/dev/null || true
echo "" >> data/cron.log 2>/dev/null || true

log_message "Завершено. Логи сохранены в data/steps.log и data/trades.log"
