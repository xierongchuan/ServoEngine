import os

# Загружаем переменные из .env файла если он существует
# (небезопасно загружать .env в продакшене, используйте переменные окружения)
try:
    # Look for .env in the project root (one level up from src)
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    # Don't override if already set in environment
                    if key not in os.environ:
                        os.environ[key] = value.strip().strip('"').strip("'")

    # Инициализируем логгер только после загрузки конфигурации
    try:
        from src.utils import logger
    except:
        pass  # Логгер может быть еще не готов
except Exception as e:
    pass  # Молча игнорируем ошибки загрузки .env

# Настройки системы
MODE = os.getenv("MODE", "demo")  # "demo" для тестов, "real" для реальных денег

# Ваши учетные данные Capital.com (обязательно установите переменные окружения)
# НЕ используйте fallback значения в коде - это небезопасно!
USERNAME = os.getenv("CAP_API_USERNAME", "")
PASSWORD = os.getenv("CAP_API_PASSWORD", "")
CAP_API_KEY = os.getenv("CAP_API_KEY", "")  # API ключ из Settings > API Integrations

import json

# Функция для загрузки конфигурации
def load_bot_config():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bot_config.json')
    default_config = {
        "EXCHANGE_SYMBOLS": {
            "capital": ["BTC/USD"],
            "bingx": ["BTC-USDT"]
        },
        "POSITION_SIZE_PERCENT": 5.0,
        "MIN_TRADE_AMOUNT_USDT": 10.0,
        "TAKE_PROFIT_PERCENT": 1.5,
        "STOP_LOSS_PERCENT": 1.5,
        "MIN_CONFIDENCE_THRESHOLD": 0.7,
        "DEFAULT_HOLD_TIME_MINUTES": 60,
        "AI_THRESHOLDS": {
            "RSI_OVERSOLD": 30,
            "RSI_OVERBOUGHT": 70,
            "MIN_POSITIVE_NEWS": 3,
            "MIN_NEGATIVE_NEWS": 3,
            "STRONG_SIGNAL_CONFIDENCE": 0.8,
            "SMA_PERIOD": 20,
            "RSI_PERIOD": 14,
            "HOLD_TIMES": [10, 15, 30, 45, 60, 90, 120]
        },
        "CHART_RANGES": {
            "1D": {"days": 1, "candles": 288},
            "3D": {"days": 3, "candles": 864},
            "1W": {"days": 7, "candles": 2016}
        },
        "DEFAULT_CHART_RANGE": "1D",
        "CLEANUP_SETTINGS": {
            "cleanup_old_charts": True,
            "charts_retention_days": 7,
            "cleanup_old_data": True,
            "data_retention_days": 30
        },
        "NEWS_SETTINGS": {
            "use_real_news": True,
            "provider": "newsapi",
            "max_news_items": 10,
            "news_timeout_seconds": 30,
            "extract_full_content": True
        }
    }
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                user_config = json.load(f)
                # Обновляем дефолтный конфиг пользовательским (глубокое слияние не делаем, просто верхний уровень)
                default_config.update(user_config)
                print(f"✅ Загружена конфигурация из {config_path}")
        except Exception as e:
            print(f"⚠️ Ошибка загрузки {config_path}: {e}. Используются настройки по умолчанию.")
    
    return default_config

# Загружаем конфиг
BOT_CONFIG = load_bot_config()

# Выбор биржи (нужен для определения списка символов)
EXCHANGE = os.getenv("EXCHANGE", "capital")  # "capital" или "bingx"

# Настройки трейдинга из конфига
TRADING_SETTINGS = BOT_CONFIG.get("TRADING_SETTINGS", {})
EXCHANGE_SYMBOLS = BOT_CONFIG.get("EXCHANGE_SYMBOLS", {})
SYMBOLS = EXCHANGE_SYMBOLS.get(EXCHANGE, ["BTC/USD"])

POSITION_SIZE_PERCENT = TRADING_SETTINGS.get("POSITION_SIZE_PERCENT", 5.0)
MIN_TRADE_AMOUNT_USDT = TRADING_SETTINGS.get("MIN_TRADE_AMOUNT_USDT", 10.0)
LEVERAGE = TRADING_SETTINGS.get("LEVERAGE", 5)
TAKE_PROFIT_PERCENT = TRADING_SETTINGS.get("TAKE_PROFIT_PERCENT", 1.5)
STOP_LOSS_PERCENT = BOT_CONFIG.get("STOP_LOSS_PERCENT", 1.5)
MIN_CONFIDENCE_THRESHOLD = BOT_CONFIG.get("MIN_CONFIDENCE_THRESHOLD", 0.7)
DEFAULT_HOLD_TIME_MINUTES = BOT_CONFIG.get("DEFAULT_HOLD_TIME_MINUTES", 60)

# Пороговые значения для AI-анализа
AI_THRESHOLDS = BOT_CONFIG.get("AI_THRESHOLDS", {})

# Пути к данным
DATA_DIR = "data"
CHARTS_DIR = "charts"

# Настройки графиков
CHART_RANGES = BOT_CONFIG.get("CHART_RANGES", {})
DEFAULT_CHART_RANGE = BOT_CONFIG.get("DEFAULT_CHART_RANGE", "1D")

# Настройки очистки старых файлов
CLEANUP_SETTINGS = BOT_CONFIG.get("CLEANUP_SETTINGS", {})

# News API настройки (для получения реальных новостей)
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")

# Настройки новостей
ENABLE_NEWS = os.getenv("ENABLE_NEWS", "true").lower() == "true"

NEWS_SETTINGS = BOT_CONFIG.get("NEWS_SETTINGS", {})

# DeepSeek API (обязательно установите DEEPSEEK_API_KEY)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# API Endpoint для демо и реального режима
# ВАЖНО: Демо-счет - это НЕ отдельный тип аккаунта!
# Демо режим определяется по URL endpoint'а, а тип аккаунта может быть CFD, SPREADBET и т.д.
# Демо: https://demo-api-capital.backend-capital.com/api/v1/
# Реальный: https://api-capital.backend-capital.com/api/v1/
API_BASE = "https://demo-api-capital.backend-capital.com/api/v1/" if MODE == "demo" else "https://api-capital.backend-capital.com/api/v1/"

# Выбор биржи

# BingX API настройки
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

# BingX API URLs
# Standard Futures: https://open-api.bingx.com
# VST Futures (Demo): https://open-api-vst.bingx.com
BINGX_API_URL = "https://open-api-vst.bingx.com" if MODE == "demo" else "https://open-api.bingx.com"

# Логируем выбранный endpoint для отладки
print(f"🌐 Используется биржа: {EXCHANGE}")
if EXCHANGE == "capital":
    print(f"🌐 Capital.com API endpoint: {API_BASE} ({'Demo' if MODE == 'demo' else 'Real'})")
else:
    print(f"🌐 BingX API endpoint: {BINGX_API_URL} ({'Demo (VST)' if MODE == 'demo' else 'Real'})")

print(f"📰 Новости: {'ВКЛЮЧЕНЫ' if ENABLE_NEWS else 'ОТКЛЮЧЕНЫ'}")