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
cd "$(dirname "$0")"

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

# Проверка наличия переменных окружения
EXCHANGE=${EXCHANGE:-capital}

if [ -z "$DEEPSEEK_API_KEY" ]; then
    log_error "DEEPSEEK_API_KEY не настроен!"
    exit 1
fi

if [ "$EXCHANGE" == "capital" ]; then
    if [ -z "$CAP_API_USERNAME" ] || [ -z "$CAP_API_PASSWORD" ] || [ -z "$CAP_API_KEY" ]; then
        log_error "Capital.com credentials missing!"
        log_warning "Required: CAP_API_USERNAME, CAP_API_PASSWORD, CAP_API_KEY"
        exit 1
    fi
elif [ "$EXCHANGE" == "bingx" ]; then
    if [ -z "$BINGX_API_KEY" ] || [ -z "$BINGX_SECRET_KEY" ]; then
        log_error "BingX credentials missing!"
        log_warning "Required: BINGX_API_KEY, BINGX_SECRET_KEY"
        exit 1
    fi
fi

# Создаем директории если их нет
mkdir -p data/prices data/news charts

# Логируем начало
log_message "Запуск торгового бота OpenProducer..."
log_message "Биржа: $EXCHANGE"
log_message "Режим: $MODE"

# Запускаем бота с использованием виртуального окружения
if ./venv/bin/python3 run.py; then
    log_message "✅ Торговый бот успешно завершил работу"
else
    log_error "❌ Торговый бот завершился с ошибкой"
    log_warning "Проверьте логи: data/steps.log"
    exit 1
fi

# Логируем завершение
echo "" >> data/cron.log 2>/dev/null || true
echo "=== $(date) ===" >> data/cron.log 2>/dev/null || true
echo "Trading bot cycle completed" >> data/cron.log 2>/dev/null || true
echo "" >> data/cron.log 2>/dev/null || true

log_message "Завершено. Логи сохранены в data/steps.log и data/trades.log"
