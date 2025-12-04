#!/bin/bash
# Скрипт для настройки автоматического запуска через cron
# Автор: Claude Code
# Дата: 2025-11-02

# Цвета
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Настройка cron для OpenProducer Bot  ${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Определяем директорию скриптов
SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPT_PATH="$SCRIPTS_DIR/run_trading_bot.sh"

if [ ! -f "$SCRIPT_PATH" ]; then
    echo -e "${RED}❌ Ошибка: Скрипт не найден!${NC}"
    exit 1
fi

chmod +x "$SCRIPT_PATH"
echo -e "${GREEN}✅ Скрипт сделан исполняемым${NC}"

echo ""
echo -e "${YELLOW}Выберите интервал запуска бота:${NC}"
echo "1) Каждые 30 секунд"
echo "2) Каждую минуту"
echo "3) Каждые 5 минут"
echo "4) Каждые 10 минут"
echo "5) Каждый час"
echo "6) Каждые 2 часа"
echo "7) Отменить"

read -p "Введите номер (1-7): " choice

case $choice in
    1)
        CRON_SCHEDULE="* * * * *"
        CRON_DESC="каждые 30 секунд"
        IS_30_SEC=true
        ;;
    2)
        CRON_SCHEDULE="* * * * *"
        CRON_DESC="каждую минуту"
        IS_30_SEC=false
        ;;
    3) CRON_SCHEDULE="*/5 * * * *"; CRON_DESC="каждые 5 минут"; IS_30_SEC=false;;
    4) CRON_SCHEDULE="*/10 * * * *"; CRON_DESC="каждые 10 минут"; IS_30_SEC=false;;
    5) CRON_SCHEDULE="0 * * * *"; CRON_DESC="каждый час"; IS_30_SEC=false;;
    6) CRON_SCHEDULE="0 */2 * * *"; CRON_DESC="каждые 2 часа"; IS_30_SEC=false;;
    7) echo -e "${YELLOW}Отменено${NC}"; exit 0;;
    *) echo -e "${RED}❌ Неверный выбор${NC}"; exit 1;;
esac

if [ "$IS_30_SEC" = true ]; then
    CRON_LINE_1="* * * * * $SCRIPT_PATH --once"
    CRON_LINE_2="* * * * * sleep 30; $SCRIPT_PATH --once"
else
    CRON_LINE="$CRON_SCHEDULE $SCRIPT_PATH --once"
fi
TEMP_CRON=$(mktemp)
crontab -l 2>/dev/null > "$TEMP_CRON" || true

if grep -Fq "$SCRIPT_PATH" "$TEMP_CRON"; then
    sed -i "\|$SCRIPT_PATH|d" "$TEMP_CRON"
fi

if [ "$IS_30_SEC" = true ]; then
    echo "$CRON_LINE_1" >> "$TEMP_CRON"
    echo "$CRON_LINE_2" >> "$TEMP_CRON"
else
    echo "$CRON_LINE" >> "$TEMP_CRON"
fi
crontab "$TEMP_CRON"
rm "$TEMP_CRON"

echo ""
echo -e "${GREEN}✅ Настройка завершена!${NC}"
echo -e "  📅 Расписание: $CRON_DESC"
echo ""
echo -e "${GREEN}Управление:${NC}"
echo -e "  ${YELLOW}Просмотр:${NC} crontab -l"
echo -e "  ${YELLOW}Удаление:${NC} crontab -r"
