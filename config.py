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
USERNAME = os.getenv("DEMO_USERNAME", "")
PASSWORD = os.getenv("DEMO_PASSWORD", "")
CAP_API_KEY = os.getenv("CAP_API_KEY", "")  # API ключ из Settings > API Integrations

# Настройки трейдинга
SYMBOLS = ["SOL/USD", "BTC/USD"]     # Активы для торговли (макс 5)
POSITION_SIZE = 0.1                  # Размер ордера в лотах
TAKE_PROFIT_PERCENT = 1.5            # Take Profit в процентах
STOP_LOSS_PERCENT = 2.0              # Stop Loss в процентах

# DeepSeek API (обязательно установите DEEPSEEK_API_KEY)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Пути к данным
DATA_DIR = "data"
CHARTS_DIR = "charts"

# API Endpoint для демо и реального режима
# ВАЖНО: Демо-счет - это НЕ отдельный тип аккаунта!
# Демо режим определяется по URL endpoint'а, а тип аккаунта может быть CFD, SPREADBET и т.д.
# Демо: https://demo-api-capital.backend-capital.com/api/v1/
# Реальный: https://api-capital.backend-capital.com/api/v1/
API_BASE = "https://demo-api-capital.backend-capital.com/api/v1/" if MODE == "demo" else "https://api-capital.backend-capital.com/api/v1/"

# Логируем выбранный endpoint для отладки
print(f"🌐 Используется {'Demo' if MODE == 'demo' else 'Real'} API endpoint: {API_BASE}")