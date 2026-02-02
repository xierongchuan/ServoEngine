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

# Переходим в корневую директорию проекта
cd "$(dirname "$0")/.."

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

# Собираем список доступных символов из логов
SYMBOLS=()
if [ -d "data/logs" ]; then
    for f in data/logs/*.log; do
        [ -f "$f" ] || continue
        sym=$(basename "$f" .log)
        SYMBOLS+=("$sym")
    done
fi

show_stats

# Выбор актива
select_symbol() {
    if [ ${#SYMBOLS[@]} -eq 0 ]; then
        echo -e "${RED}Логи активов не найдены в data/logs/${NC}"
        return 1
    fi
    echo -e "${GREEN}Доступные активы:${NC}"
    echo "  0) Все активы"
    for i in "${!SYMBOLS[@]}"; do
        local sym="${SYMBOLS[$i]}"
        local lines=$(wc -l < "data/logs/${sym}.log" 2>/dev/null || echo "0")
        local errors=$(grep -c "ERROR" "data/logs/${sym}.log" 2>/dev/null || echo "0")
        echo -e "  $((i+1))) ${YELLOW}${sym}${NC} — ${lines} строк, ${RED}${errors}${NC} ошибок"
    done
    read -p "Актив (0-${#SYMBOLS[@]}): " sym_choice
    if [ "$sym_choice" = "0" ]; then
        SELECTED_SYMBOL="ALL"
    elif [ "$sym_choice" -ge 1 ] 2>/dev/null && [ "$sym_choice" -le "${#SYMBOLS[@]}" ]; then
        SELECTED_SYMBOL="${SYMBOLS[$((sym_choice-1))]}"
    else
        echo -e "${RED}Неверный выбор${NC}"
        return 1
    fi
    return 0
}

echo -e "${GREEN}Выберите действие:${NC}"
echo "1) Просмотр логов системы"
echo "2) Просмотр логов сделок"
echo "3) Просмотр ошибок"
echo "4) Просмотр торговых операций"
echo "5) Просмотр логов актива"
echo "6) Следить за логами актива (tail -f)"
echo "7) Следить за всеми логами"
echo "0) Выход"

read -p "Ваш выбор (0-7): " choice

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
        select_symbol || exit 1
        if [ "$SELECTED_SYMBOL" = "ALL" ]; then
            grep "ERROR" data/logs/*.log data/steps.log 2>/dev/null | tail -n 30 || echo "Ошибок не найдено"
        else
            grep "ERROR" "data/logs/${SELECTED_SYMBOL}.log" 2>/dev/null | tail -n 20 || echo "Ошибок не найдено"
        fi
        ;;
    4)
        echo -e "${BLUE}=== Торговые операции ===${NC}"
        grep "📌\|✅\|❌\|⏰" data/trades.log 2>/dev/null | tail -n 20 || echo "Операций не найдено"
        ;;
    5)
        select_symbol || exit 1
        if [ "$SELECTED_SYMBOL" = "ALL" ]; then
            for sym in "${SYMBOLS[@]}"; do
                echo -e "${BLUE}=== ${sym} ===${NC}"
                tail -n 10 "data/logs/${sym}.log" 2>/dev/null
                echo ""
            done
        else
            echo -e "${BLUE}=== ${SELECTED_SYMBOL} ===${NC}"
            tail -n 30 "data/logs/${SELECTED_SYMBOL}.log" 2>/dev/null || echo "Файл не найден"
        fi
        ;;
    6)
        select_symbol || exit 1
        if [ "$SELECTED_SYMBOL" = "ALL" ]; then
            echo -e "${BLUE}=== Слежение за всеми активами (Ctrl+C для выхода) ===${NC}"
            tail -n 10 -f data/logs/*.log 2>/dev/null || echo "Файлы не найдены"
        else
            echo -e "${BLUE}=== Слежение за ${SELECTED_SYMBOL} (Ctrl+C для выхода) ===${NC}"
            tail -n 20 -f "data/logs/${SELECTED_SYMBOL}.log" 2>/dev/null || echo "Файл не найден"
        fi
        ;;
    7)
        echo -e "${BLUE}=== Слежение за всеми логами (Ctrl+C для выхода) ===${NC}"
        while true; do
            clear
            echo -e "${BLUE}=== Логи системы ===${NC}"
            tail -n 5 data/steps.log 2>/dev/null || echo "Файл не найден"
            echo ""
            for sym in "${SYMBOLS[@]}"; do
                echo -e "${BLUE}=== ${sym} ===${NC}"
                tail -n 5 "data/logs/${sym}.log" 2>/dev/null
                echo ""
            done
            echo -e "${BLUE}=== Сделки ===${NC}"
            tail -n 5 data/trades.log 2>/dev/null || echo "Файл не найден"
            sleep 5
        done
        ;;
    0)
        echo -e "${GREEN}До свидания!${NC}"
        exit 0
        ;;
    *)
        echo -e "${RED}Неверный выбор${NC}"
        ;;
esac
