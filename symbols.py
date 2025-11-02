# -*- coding: utf-8 -*-
"""
Модуль для управления EPIC кодами торговых инструментов Capital.com
Все EPIC коды должны быть здесь в одном месте для избежания дублирования
"""

# Прямое преобразование: символ → EPIC код
SYMBOL_TO_EPIC = {
    "EUR/USD": "EURUSD",
    "BTC/USD": "BTCUSD",
    "SOL/USD": "SOLUSD",
    "AAPL": "AAPL",
    "GOLD": "GOLD",
    "OIL": "BRENTOIL"
}

# Обратное преобразование: EPIC код → символ
EPIC_TO_SYMBOL = {v: k for k, v in SYMBOL_TO_EPIC.items()}

def get_epic(symbol):
    """Получить EPIC код для символа"""
    return SYMBOL_TO_EPIC.get(symbol)

def get_symbol(epic):
    """Получить символ для EPIC кода"""
    return EPIC_TO_SYMBOL.get(epic)

def is_supported(symbol):
    """Проверить, поддерживается ли символ"""
    return symbol in SYMBOL_TO_EPIC

def get_filename(symbol):
    """Получить безопасное имя файла для символа (с подчеркиваниями)"""
    return symbol.replace('/', '_')
