#!/bin/bash
# Скрипт для мониторинга логов в реальном времени
# Автор: Claude Code

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  OpenProducer - Мониторинг логов      ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

show_stats() {
    echo -e "${GREEN}📊 СТАТИСТИКА:${NC}"
    echo ""
    
    if [ -f "data/steps.log" ]; then
        TOTAL_EVENTS=$(wc -l < data/steps.log)
        ERRORS=$(grep -c "ERROR" data/steps.log 2>/dev/null || echo "0")
        echo -e "  📄 Лог системы: ${YELLOW}$TOTAL_EVENTS${NC} событий, ${RED}$ERRORS${NC} ошибок"
    fi
    
    if [ -f "data/trades.log" ]; then
        TOTAL_TRADES=$(wc -l < data/trades.log)
        OPENED=$(grep -c "открыт ордер" data/trades.log 2>/dev/null || echo "0")
        echo -e "  💰 Лог сделок: ${YELLOW}$TOTAL_TRADES${NC} записей, ${GREEN}$OPENED${NC} открыто"
    fi
    echo ""
}

show_stats

echo -e "${GREEN}Выберите действие:${NC}"
echo "1) Просмотр логов системы"
echo "2) Просмотр логов сделок"
echo "3) Просмотр ошибок"
echo "4) Просмотр торговых операций"
echo "5) Следить за всеми логами"
echo "0) Выход"

read -p "Ваш выбор (0-5): " choice

case $choice in
    1)
        echo -e "${BLUE}=== Логи системы ===${NC}"
        tail -n 20 -f data/steps.log 2>/dev/null || echo "Файл не найден"
        ;;
    2)
        echo -e "${BLUE}=== Логи сделок ===${NC}"
        tail -n 20 -f data/trades.log 2>/dev/null || echo "Файл не найден"
        ;;
    3)
        echo -e "${BLUE}=== Ошибки ===${NC}"
        grep "ERROR" data/steps.log 2>/dev/null | tail -n 20 || echo "Ошибок не найдено"
        ;;
    4)
        echo -e "${BLUE}=== Торговые операции ===${NC}"
        grep "📌\|✅\|❌\|⏰" data/trades.log 2>/dev/null | tail -n 20 || echo "Операций не найдено"
        ;;
    5)
        echo -e "${BLUE}=== Слежение за всеми логами (Ctrl+C для выхода) ===${NC}"
        while true; do
            clear
            echo -e "${BLUE}=== Логи системы ===${NC}"
            tail -n 10 data/steps.log 2>/dev/null || echo "Файл не найден"
            echo ""
            echo -e "${BLUE}=== Логи сделок ===${NC}"
            tail -n 10 data/trades.log 2>/dev/null || echo "Файл не найден"
            sleep 5
        done
        ;;
    0)
        echo -e "${GREEN}До свидания!${NC}"
        exit 0
        ;;
    *)
        echo -e "${RED}❌ Неверный выбор${NC}"
        ;;
esac
