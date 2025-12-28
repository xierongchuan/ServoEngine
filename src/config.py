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

        "AI_THRESHOLDS": {
            "RSI_OVERSOLD": 30,
            "RSI_OVERBOUGHT": 70,
            "MIN_POSITIVE_NEWS": 3,
            "MIN_NEGATIVE_NEWS": 3,
            "STRONG_SIGNAL_CONFIDENCE": 0.8,
            "SMA_PERIOD": 20,
            "RSI_PERIOD": 14,
            "RSI_NEUTRAL_MIN": 45,
            "RSI_NEUTRAL_MAX": 55,

        },
        "CHART_RANGES": {
            "1D": {"days": 1, "candles": 288},
            "3D": {"days": 3, "candles": 864},
            "1W": {"days": 7, "candles": 2016},
            "14D": {"days": 14, "candles": 336, "interval": "1h"}
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
        },
        "AGGRESSIVE_MODE": False,
        "AGGRESSIVE_SETTINGS": {
            "RSI_BUY_COND": 60,
            "RSI_BUY_FORBIDDEN": 80,
            "RSI_SELL_COND": 40,
            "RSI_SELL_FORBIDDEN": 20
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
EXCHANGE_FEES = BOT_CONFIG.get("EXCHANGE_FEES", {"capital": 0.0, "bingx": 0.05})
TRADING_FEE = EXCHANGE_FEES.get(EXCHANGE, 0.05)

POSITION_SIZE_PERCENT = BOT_CONFIG.get("POSITION_SIZE_PERCENT", 5.0)
MIN_TRADE_AMOUNT_USDT = BOT_CONFIG.get("MIN_TRADE_AMOUNT_USDT", 10.0)
# LEVERAGE is now dynamic based on style, but we initialize a default here
# It will be overwritten by style settings below
LEVERAGE = 10
TAKE_PROFIT_PERCENT = BOT_CONFIG.get("TAKE_PROFIT_PERCENT", 1.5)
STOP_LOSS_PERCENT = BOT_CONFIG.get("STOP_LOSS_PERCENT", 1.5)
MIN_CONFIDENCE_THRESHOLD = BOT_CONFIG.get("MIN_CONFIDENCE_THRESHOLD", 0.7)

MIN_RISK_REWARD_RATIO = BOT_CONFIG.get("MIN_RISK_REWARD_RATIO", 1.5)
MIN_PARTIAL_CLOSE_PNL = BOT_CONFIG.get("MIN_PARTIAL_CLOSE_PNL", 0.5)

# Пороговые значения для AI-анализа
AI_THRESHOLDS = BOT_CONFIG.get("AI_THRESHOLDS", {})

# Пути к данным
DATA_DIR = "data"
CHARTS_DIR = "charts"

# Настройки графиков
CHART_RANGES = BOT_CONFIG.get("CHART_RANGES", {})
DEFAULT_CHART_RANGE = BOT_CONFIG.get("DEFAULT_CHART_RANGE", "1D")

# Настройки для генератора картинок (plotter.py)
PLOTTER_RANGES = BOT_CONFIG.get("PLOTTER_RANGES", {})
DEFAULT_PLOTTER_RANGE = BOT_CONFIG.get("DEFAULT_PLOTTER_RANGE", "1D")

# Настройки очистки старых файлов
CLEANUP_SETTINGS = BOT_CONFIG.get("CLEANUP_SETTINGS", {})

# News API настройки (для получения реальных новостей)
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")

# Настройки новостей
ENABLE_NEWS = BOT_CONFIG.get("ENABLE_NEWS", True)
ENABLE_ADVANCED_ANALYSIS = BOT_CONFIG.get("ENABLE_ADVANCED_ANALYSIS", True)
ENABLE_PARALLEL_MODE = BOT_CONFIG.get("ENABLE_PARALLEL_MODE", True)
ENABLE_PARALLEL_PROCESSING = ENABLE_PARALLEL_MODE
ENABLE_PARALLEL_COLLECTION = ENABLE_PARALLEL_MODE
AGGRESSIVE_MODE = BOT_CONFIG.get("AGGRESSIVE_MODE", False)
AGGRESSIVE_SETTINGS = BOT_CONFIG.get("AGGRESSIVE_SETTINGS", {})
NEWS_SETTINGS = BOT_CONFIG.get("NEWS_SETTINGS", {})
SMART_SAMPLING = BOT_CONFIG.get("SMART_SAMPLING", {"enabled": True, "recent_candles": 30, "history_step": 10})
MIN_RISK_REWARD_RATIO = BOT_CONFIG.get("MIN_RISK_REWARD_RATIO", 1.5)
ENABLE_AI_SKIP_ON_RSI = BOT_CONFIG.get("ENABLE_AI_SKIP_ON_RSI", True)
MOMENTUM_STRATEGY = BOT_CONFIG.get("MOMENTUM_STRATEGY", {
    "enabled": True,
    "atr_sl_multiplier": 1.5,
    "atr_tp_multiplier": 2.5,
    "min_volume_ratio": 0.7,
    "max_candles_in_prompt": 50,
    "trend_consensus_required": False,
    "momentum_entry_enabled": True,
    "momentum_consecutive_candles": 3
})

# Trading Style Settings (Scalp / Intraday / Swing)
STRATEGY_STYLE = BOT_CONFIG.get("STRATEGY_STYLE", "INTRADAY")  # Default to INTRADAY

# Presets for different styles (Can be overridden by bot_config.json if keys exist there)
# Presets for different styles (Can be overridden by bot_config.json if keys exist there)
DEFAULT_STYLE_PRESETS = {
    "SCALP": {
        "timeframe": "1m",
        "chart_period": "6h",
        "plotter_period": "4h",
        "loop_interval": 3, # Fast reaction search
        "position_check_interval": 3, # Quick check (3s)
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 2.0,
        "description": "High frequency, small moves, strict exits."
    },
    "INTRADAY": {
        "timeframe": "5m",
        "chart_period": "1D",
        "plotter_period": "12h",
        "loop_interval": 60, # Standard search
        "position_check_interval": 10, # Balanced monitoring (10s)
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.0,
        "description": "Day trading, capturing daily trends."
    },
    "SWING": {
        "timeframe": "1h", # True Swing base timeframe
        "chart_period": "14D", # 2 weeks of context
        "plotter_period": "3D", # Visualise last 3 days
        "loop_interval": 300, # Check every 5 minutes (sufficient for hourly closes)
        "position_check_interval": 60, # Check positions every minute
        "atr_sl_mult": 3.0,
        "atr_tp_mult": 6.0, # Target large moves
        "description": "Multi-day holding (Days/Weeks), wide stops, ignoring intraday noise."
    }
}
STYLE_PRESETS = BOT_CONFIG.get("STYLE_PRESETS", DEFAULT_STYLE_PRESETS)

# Apply style preset if values are missing in specific configs
current_preset = STYLE_PRESETS.get(STRATEGY_STYLE, STYLE_PRESETS["INTRADAY"])
if "atr_sl_multiplier" not in MOMENTUM_STRATEGY:
    MOMENTUM_STRATEGY["atr_sl_multiplier"] = current_preset["atr_sl_mult"]
if "atr_tp_multiplier" not in MOMENTUM_STRATEGY:
    MOMENTUM_STRATEGY["atr_tp_multiplier"] = current_preset["atr_tp_mult"]

# Set LEVERAGE from the current style preset
LEVERAGE = current_preset.get("leverage", 10)
print(f"🔧 Strategy: {STRATEGY_STYLE}, Leverage: {LEVERAGE}x")

# --- DYNAMIC CONFIGURATION LOGIC ---
def parse_interval_minutes(interval_str):
    """Converts '1m', '5m', '1h', '1d' to minutes"""
    if not interval_str: return 1
    unit = interval_str[-1].lower()
    value = int(interval_str[:-1])
    if unit == 'm': return value
    if unit == 'h': return value * 60
    if unit == 'd': return value * 1440
    return value

# 1. Override DEFAULT_CHART_RANGE based on Style Preset
target_chart_period = current_preset.get("chart_period")
if target_chart_period and target_chart_period in CHART_RANGES:
    if DEFAULT_CHART_RANGE != target_chart_period:
        print(f"🔄 Auto-adjusted CHART_RANGE to {target_chart_period} for {STRATEGY_STYLE} style")
        DEFAULT_CHART_RANGE = target_chart_period
        BOT_CONFIG["DEFAULT_CHART_RANGE"] = target_chart_period

# 2. Override DEFAULT_PLOTTER_RANGE base on Style Preset
target_plotter_period = current_preset.get("plotter_period")
if target_plotter_period and target_plotter_period in PLOTTER_RANGES:
     if DEFAULT_PLOTTER_RANGE != target_plotter_period:
        print(f"🔄 Auto-adjusted PLOTTER_RANGE to {target_plotter_period} for {STRATEGY_STYLE} style")
        DEFAULT_PLOTTER_RANGE = target_plotter_period
        BOT_CONFIG["DEFAULT_PLOTTER_RANGE"] = target_plotter_period

# 3. Auto-calculate Smart Sampling Step
# We want the AI to see 'target_timeframe' candles (e.g. 15m), but we download 'base_interval' candles (e.g. 5m)
chart_config = CHART_RANGES.get(DEFAULT_CHART_RANGE, {})
base_interval_str = chart_config.get("interval", "1m")
target_timeframe_str = current_preset.get("timeframe", "1m")

base_minutes = parse_interval_minutes(base_interval_str)
target_minutes = parse_interval_minutes(target_timeframe_str)

# Force enable Smart Sampling if we need it for aggregation
if target_minutes > base_minutes:
     if not SMART_SAMPLING.get("enabled", True):
          print(f"🔄 Force-enabling Smart Sampling for aggregation ({base_interval_str} -> {target_timeframe_str})")
          SMART_SAMPLING["enabled"] = True

if SMART_SAMPLING.get("enabled", True):
    # Calculate optimal step to simulate target timeframe
    optimal_step = max(1, target_minutes // base_minutes)

    current_step = SMART_SAMPLING.get("history_step", 1)
    # Always overwrite with optimal step to ensure correctness
    if current_step != optimal_step:
         print(f"🔄 Auto-adjusted Smart Sampling step to {optimal_step} (Target: {target_timeframe_str}, Base: {base_interval_str})")
    SMART_SAMPLING["history_step"] = optimal_step


# DeepSeek / AI API Settings
AI_SETTINGS = BOT_CONFIG.get("AI_SETTINGS", {})
AI_PROVIDER = AI_SETTINGS.get("provider", "deepseek_official")
AI_MODEL = AI_SETTINGS.get("model", "deepseek-chat")

# Load API Keys
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Determine API Key and Base URL based on provider
if AI_PROVIDER == "siliconflow":
    AI_API_KEY = SILICONFLOW_API_KEY
    # Default SiliconFlow OpenAI-compatible endpoint
    AI_BASE_URL = AI_SETTINGS.get("base_url") or "https://api.siliconflow.cn/v1/chat/completions"
    print(f"🤖 AI Provider: SiliconFlow ({AI_MODEL})")
elif AI_PROVIDER == "openrouter":
    AI_API_KEY = OPENROUTER_API_KEY
    # Default OpenRouter OpenAI-compatible endpoint
    AI_BASE_URL = AI_SETTINGS.get("base_url") or "https://openrouter.ai/api/v1/chat/completions"
    print(f"🤖 AI Provider: OpenRouter ({AI_MODEL})")
else:
    # Default to DeepSeek Official
    AI_API_KEY = DEEPSEEK_API_KEY
    AI_BASE_URL = AI_SETTINGS.get("base_url") or "https://api.deepseek.com/v1/chat/completions"
    print(f"🤖 AI Provider: DeepSeek Official ({AI_MODEL})")

if not AI_API_KEY:
    print(f"⚠️ WARNING: API Key for provider '{AI_PROVIDER}' is missing!")

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
