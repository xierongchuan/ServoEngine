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

# Логируем завершение
log_message "🚀 Запуск торгового бота..."

# Запускаем бота с использованием виртуального окружения
if ./venv/bin/python3 run.py; then
    log_message "✅ Торговый бот остановлен (штатно)"
else
    log_error "❌ Торговый бот упал с ошибкой"
    log_warning "Проверьте логи: data/steps.log"
fi
log_message "Завершено. Логи сохранены в data/steps.log и data/trades.log"

log_message "Завершено. Логи сохранены в data/steps.log и data/trades.log"
