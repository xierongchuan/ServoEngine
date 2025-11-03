import os

# Загружаем переменные из .env файла если он существует
# (небезопасно загружать .env в продакшене, используйте переменные окружения)
try:
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

    # Инициализируем логгер только после загрузки конфигурации
    try:
        import logger
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

# Настройки трейдинга
SYMBOLS = ["SOL/USD", "BTC/USD", "EUR/USD"]     # Активы для торговли (макс 5)
POSITION_SIZE = 0.1                  # Размер ордера в лотах
TAKE_PROFIT_PERCENT = 1.5            # Take Profit в процентах
STOP_LOSS_PERCENT = 2.0              # Stop Loss в процентах
MIN_CONFIDENCE_THRESHOLD = 0.6       # Минимальная уверенность AI для открытия позиции (0.0-1.0)
DEFAULT_HOLD_TIME_MINUTES = 60       # Время удержания позиции по умолчанию (в минутах)

# Пороговые значения для AI-анализа
AI_THRESHOLDS = {
    "RSI_OVERSOLD": 30,              # RSI перепроданность (сигнал BUY)
    "RSI_OVERBOUGHT": 70,            # RSI перекупленность (сигнал SELL)
    "MIN_POSITIVE_NEWS": 3,          # Минимум позитивных новостей для умеренного BUY
    "MIN_NEGATIVE_NEWS": 3,          # Минимум негативных новостей для умеренного SELL
    "STRONG_SIGNAL_CONFIDENCE": 0.8, # Порог для "сильного сигнала" (0.8+)
    "SMA_PERIOD": 20,                # Период SMA для анализа тренда
    "RSI_PERIOD": 14,                # Период RSI для анализа
    "HOLD_TIMES": [10, 15, 30, 45, 60, 90, 120], # Время удержания в минутах
}

# DeepSeek API (обязательно установите DEEPSEEK_API_KEY)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Пути к данным
DATA_DIR = "data"
CHARTS_DIR = "charts"

# Настройки графиков
CHART_RANGES = {
    "1D": {"days": 1, "candles": 288},   # 288 свечей * 5 минут = 24 часа
    "3D": {"days": 3, "candles": 864},   # 3 дня
    "1W": {"days": 7, "candles": 2016},  # 1 неделя
}
DEFAULT_CHART_RANGE = "1D"  # По умолчанию показываем 1 день

# Настройки очистки старых файлов
CLEANUP_SETTINGS = {
    "cleanup_old_charts": True,     # Удалять старые графики
    "charts_retention_days": 7,     # Хранить графики 7 дней
    "cleanup_old_data": True,       # Удалять старые данные
    "data_retention_days": 30,      # Хранить данные 30 дней
}

# News API настройки (для получения реальных новостей)
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")

# Настройки новостей
NEWS_SETTINGS = {
    "use_real_news": True,               # Использовать реальные новости (нужны API ключи)
    "provider": "newsapi",          # newsapi, alphavantage, finnhub
    "max_news_items": 10,                # Максимум новостей для анализа
    "news_timeout_seconds": 30,          # Таймаут запроса новостей
}

# API Endpoint для демо и реального режима
# ВАЖНО: Демо-счет - это НЕ отдельный тип аккаунта!
# Демо режим определяется по URL endpoint'а, а тип аккаунта может быть CFD, SPREADBET и т.д.
# Демо: https://demo-api-capital.backend-capital.com/api/v1/
# Реальный: https://api-capital.backend-capital.com/api/v1/
API_BASE = "https://demo-api-capital.backend-capital.com/api/v1/" if MODE == "demo" else "https://api-capital.backend-capital.com/api/v1/"

# Логируем выбранный endpoint для отладки
print(f"🌐 Используется {'Demo' if MODE == 'demo' else 'Real'} API endpoint: {API_BASE}")