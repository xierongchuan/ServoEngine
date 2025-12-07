"""
Система логирования для торговой системы.
Все логи пишутся в data/steps.log
Логи сделок пишутся в data/trades.log
"""

import logging
import os
from datetime import datetime

# Создаем директорию для логов
LOG_DIR = "data"
os.makedirs(LOG_DIR, exist_ok=True)

# Настройка логгера для кода
code_logger = logging.getLogger('steps')
code_logger.setLevel(logging.INFO)
code_logger.handlers.clear()  # Убираем дубликаты
code_logger.propagate = False  # Не распространять на родительские логгеры

# Форматтер для логов
formatter = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Хэндлер для файла steps.log (по умолчанию)
# В multiprocessing режиме этот хэндлер будет заменен на специфичный для символа
default_log_file = os.path.join(LOG_DIR, 'steps.log')

# Функция для настройки логгера под конкретный символ
def setup_symbol_logger(symbol):
    """Перенастраивает логгер для записи в отдельный файл символа"""
    global code_logger

    # Создаем папку для логов если нет
    logs_dir = os.path.join(LOG_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # Очищаем старые хэндлеры
    code_logger.handlers.clear()

    # Путь к логу символа: data/logs/BTCUSDT.log
    # Очищаем символ от слешей для имени файла (BTC/USD -> BTCUSD)
    safe_symbol = symbol.replace("/", "").replace("-", "")
    log_file = os.path.join(logs_dir, f"{safe_symbol}.log")

    new_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    new_handler.setFormatter(formatter)
    code_logger.addHandler(new_handler)

# Инициализация дефолтного хэндлера
code_file_handler = logging.FileHandler(default_log_file, mode='a', encoding='utf-8')
code_file_handler.setFormatter(formatter)
code_logger.addHandler(code_file_handler)

# Функция для логирования сделок (записывает в trades.log)
def log_trade(message, level='INFO'):
    """Записывает лог сделки в trades.log"""
    trades_logger = logging.getLogger('trades')
    trades_logger.setLevel(logging.INFO)

    # Отключаем наследование от родительского логгера
    trades_logger.propagate = False

    # Проверяем, есть ли уже хэндлер
    if not trades_logger.handlers:
        trades_file_handler = logging.FileHandler(
            os.path.join(LOG_DIR, 'trades.log'),
            mode='a',
            encoding='utf-8'
        )
        trades_formatter = logging.Formatter('%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        trades_file_handler.setFormatter(trades_formatter)
        trades_logger.addHandler(trades_file_handler)

    # Логируем сообщение
    if level == 'ERROR':
        trades_logger.error(message)
    elif level == 'WARNING':
        trades_logger.warning(message)
    else:
        trades_logger.info(message)

# Функция для безопасного логирования (при ошибке не падаем)
def safe_log(func, *args, **kwargs):
    """Безопасно выполняет логирование"""
    try:
        func(*args, **kwargs)
    except Exception as e:
        print(f"Ошибка логирования: {e}")

# Создаем красивые эмодзи для разных уровней
def info(msg):
    safe_log(code_logger.info, msg)

def warning(msg):
    safe_log(code_logger.warning, msg)

def error(msg):
    safe_log(code_logger.error, msg)

def debug(msg):
    safe_log(code_logger.debug, msg)
