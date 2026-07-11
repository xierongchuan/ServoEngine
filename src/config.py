"""
Configuration Module

This module provides configuration loading with support for both:
- New modular config system (config/*.json files)
- Legacy single-file config (bot_config.json)

The module automatically detects which system to use and maintains
backward compatibility with all existing code.
"""

import os
import json

# Load environment variables from .env file
try:
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    if key not in os.environ:
                        os.environ[key] = value.strip().strip('"').strip("'")

    try:
        pass
    except Exception:
        pass
except Exception:
    pass

# System settings
MODE = os.getenv("MODE", "demo")

# --- Hot-Reload State ---
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bot_config.json')
_NEW_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
_config_mtime: float = 0.0
_config_callbacks: list = []


def _use_new_config_system() -> bool:
    """Check if new config system is available."""
    return os.path.isdir(_NEW_CONFIG_DIR) and os.path.exists(os.path.join(_NEW_CONFIG_DIR, 'active.json'))


def load_bot_config():
    """
    Load configuration from the appropriate source.

    If the new config system (config/ directory) is available, uses it.
    Otherwise falls back to legacy bot_config.json.
    """
    if _use_new_config_system():
        try:
            from src.config_loader import get_legacy_compatible_config
            config = get_legacy_compatible_config()
            print("✅ Loaded configuration from config/ directory (new system)")
            return config
        except Exception as e:
            print(f"⚠️ New config system failed: {e}, falling back to legacy")

    # Legacy loading
    config_path = _CONFIG_PATH
    default_config = {
        "EXCHANGE_SYMBOLS": {"bingx": ["BTC-USDT"]},
        "POSITION_SIZE_PERCENT": 5.0,
        "MIN_TRADE_AMOUNT_USDT": 10.0,
        "TAKE_PROFIT_PERCENT": 1.5,
        "STOP_LOSS_PERCENT": 1.5,
        "MIN_CONFIDENCE_THRESHOLD": 0.7,
        "AI_THRESHOLDS": {
            "RSI_OVERSOLD": 30, "RSI_OVERBOUGHT": 70,
            "MIN_POSITIVE_NEWS": 3, "MIN_NEGATIVE_NEWS": 3,
            "STRONG_SIGNAL_CONFIDENCE": 0.8, "SMA_PERIOD": 20, "RSI_PERIOD": 14,
            "RSI_NEUTRAL_MIN": 45, "RSI_NEUTRAL_MAX": 55,
        },
        "CHART_RANGES": {
            "1D": {"days": 1, "candles": 288},
            "3D": {"days": 3, "candles": 864},
            "1W": {"days": 7, "candles": 2016},
            "14D": {"days": 14, "candles": 336, "interval": "1h"}
        },
        "DEFAULT_CHART_RANGE": "1D",
        "CLEANUP_SETTINGS": {
            "cleanup_old_charts": True, "charts_retention_days": 7,
            "cleanup_old_data": True, "data_retention_days": 30
        },
        "NEWS_SETTINGS": {
            "use_real_news": True, "provider": "newsapi",
            "max_news_items": 10, "news_timeout_seconds": 30, "extract_full_content": True
        },
        "AGGRESSIVE_MODE": False,
        "AGGRESSIVE_SETTINGS": {
            "RSI_BUY_COND": 60, "RSI_BUY_FORBIDDEN": 80,
            "RSI_SELL_COND": 40, "RSI_SELL_FORBIDDEN": 20
        }
    }

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                user_config = json.load(f)
                default_config.update(user_config)
                print(f"✅ Загружена конфигурация из {config_path}")
        except Exception as e:
            print(f"⚠️ Ошибка загрузки {config_path}: {e}. Используются настройки по умолчанию.")

    return default_config


def get_config_mtime() -> float:
    """Get modification time of config file(s)."""
    if _use_new_config_system():
        active_path = os.path.join(_NEW_CONFIG_DIR, 'active.json')
        try:
            return os.path.getmtime(active_path)
        except OSError:
            pass
    try:
        return os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return 0.0


def should_reload_config() -> bool:
    """Check if config file has been modified since last load."""
    current_mtime = get_config_mtime()
    return current_mtime > _config_mtime


def register_config_callback(callback):
    """Register a function to be called when config is reloaded."""
    _config_callbacks.append(callback)


def reload_bot_config() -> dict:
    """Reload configuration from file and update all module-level variables."""
    global BOT_CONFIG, _config_mtime
    global DISABLED_SYMBOLS, POSITION_SIZE_PERCENT, MIN_TRADE_AMOUNT_USDT
    global LEVERAGE, TAKE_PROFIT_PERCENT, STOP_LOSS_PERCENT
    global MIN_CONFIDENCE_THRESHOLD, MIN_RISK_REWARD_RATIO, MIN_PARTIAL_CLOSE_PNL
    global AI_THRESHOLDS
    global ENABLE_NEWS, ENABLE_ADVANCED_ANALYSIS
    global AGGRESSIVE_MODE, AGGRESSIVE_SETTINGS, NEWS_SETTINGS
    global SMART_SAMPLING, ENABLE_AI_SKIP_ON_RSI
    global DECISION_JOURNAL, POSITION_LIMITS, VALIDATION
    global TECHNICAL_ANALYSIS, CHART_SETTINGS, ERROR_HANDLING, MOMENTUM_STRATEGY
    global STRATEGY_STYLE, STYLE_PRESETS, SCALP_SETTINGS, MACDX_SETTINGS
    global AI_SETTINGS, AI_MODEL, AI_TEMPERATURE, AI_MAX_TOKENS
    global AI_REASONING, AI_RETRY_COUNT, AI_PROVIDER_ROUTING
    global AI_FALLBACK_MODELS, AI_REQUEST_TIMEOUT, AI_RETRY_BACKOFF_BASE, AI_BASE_URL
    global TRADING_FEE, TRADING_FEE_MAKER, TRADING_FEE_TAKER

    old_config = BOT_CONFIG.copy()

    try:
        BOT_CONFIG = load_bot_config()
    except Exception as e:
        print(f"⚠️ Config reload failed: {e}. Keeping previous config.")
        return BOT_CONFIG

    _config_mtime = get_config_mtime()

    # Update all derived variables
    DISABLED_SYMBOLS = BOT_CONFIG.get("DISABLED_SYMBOLS", [])
    POSITION_SIZE_PERCENT = BOT_CONFIG.get("POSITION_SIZE_PERCENT", 5.0)
    MIN_TRADE_AMOUNT_USDT = BOT_CONFIG.get("MIN_TRADE_AMOUNT_USDT", 10.0)
    TAKE_PROFIT_PERCENT = BOT_CONFIG.get("TAKE_PROFIT_PERCENT", 1.5)
    STOP_LOSS_PERCENT = BOT_CONFIG.get("STOP_LOSS_PERCENT", 1.5)
    MIN_CONFIDENCE_THRESHOLD = BOT_CONFIG.get("MIN_CONFIDENCE_THRESHOLD", 0.7)
    MIN_RISK_REWARD_RATIO = BOT_CONFIG.get("MIN_RISK_REWARD_RATIO", 1.5)
    MIN_PARTIAL_CLOSE_PNL = BOT_CONFIG.get("MIN_PARTIAL_CLOSE_PNL", 0.5)
    AI_THRESHOLDS = BOT_CONFIG.get("AI_THRESHOLDS", {})
    ENABLE_NEWS = BOT_CONFIG.get("ENABLE_NEWS", True)
    ENABLE_ADVANCED_ANALYSIS = BOT_CONFIG.get("ENABLE_ADVANCED_ANALYSIS", True)
    AGGRESSIVE_MODE = BOT_CONFIG.get("AGGRESSIVE_MODE", False)
    AGGRESSIVE_SETTINGS = BOT_CONFIG.get("AGGRESSIVE_SETTINGS", {})
    NEWS_SETTINGS = BOT_CONFIG.get("NEWS_SETTINGS", {})
    SMART_SAMPLING = BOT_CONFIG.get("SMART_SAMPLING", {"enabled": True, "recent_candles": 30, "history_step": 10})
    ENABLE_AI_SKIP_ON_RSI = BOT_CONFIG.get("ENABLE_AI_SKIP_ON_RSI", True)
    DECISION_JOURNAL = BOT_CONFIG.get("DECISION_JOURNAL", {"enabled": True, "max_entries": {"SCALP": 5, "AISCALP": 10, "SWING": 10}})
    POSITION_LIMITS = BOT_CONFIG.get("POSITION_LIMITS", {"max_positions": 5, "price_precision": 4, "quantity_precision": 4, "balance_safety_margin": 0.95, "position_sync_wait": 1.0})
    VALIDATION = BOT_CONFIG.get("VALIDATION", {"rr_soft_limit": 0.5})
    TECHNICAL_ANALYSIS = BOT_CONFIG.get("TECHNICAL_ANALYSIS", {"sr_window": 20, "ema_periods": [9, 21], "trend_candle_count": 5, "volume_avg_window": 20, "volume_thresholds": {"anomaly": 2.0, "elevated": 1.2, "low": 0.5}, "seb_length": 20, "seb_multiplier": 2.0, "momentum_volume_threshold": 1.2, "momentum_trend_volume_threshold": 1.0, "news_items_in_prompt": 5})
    CHART_SETTINGS = BOT_CONFIG.get("CHART_SETTINGS", {"enabled": True, "update_interval": 10, "min_sleep": 0.5, "sma_periods": [10, 20, 50, 100, 200], "chart_height": 13.5, "dpi": 200})
    ERROR_HANDLING = BOT_CONFIG.get("ERROR_HANDLING", {"cycle_error_fallback_sleep": 5})
    MOMENTUM_STRATEGY = BOT_CONFIG.get("MOMENTUM_STRATEGY", {
        "enabled": True, "min_volume_ratio": 0.7, "max_candles_in_prompt": 50,
        "trend_consensus_required": False, "momentum_entry_enabled": True,
        "momentum_consecutive_candles": 3
    })

    STRATEGY_STYLE = BOT_CONFIG.get("STRATEGY_STYLE", "AISCALP")
    STYLE_PRESETS = BOT_CONFIG.get("STYLE_PRESETS", DEFAULT_STYLE_PRESETS)
    SCALP_SETTINGS = BOT_CONFIG.get("SCALP_SETTINGS", SCALP_SETTINGS)
    MACDX_SETTINGS = BOT_CONFIG.get("MACDX_SETTINGS", MACDX_SETTINGS)

    # Reload fee rates
    _exchange_fees = BOT_CONFIG.get("EXCHANGE_FEES", {"bingx": {"maker": 0.02, "taker": 0.05}})
    _fee_val = _exchange_fees.get(EXCHANGE, 0.05)
    if isinstance(_fee_val, dict):
        TRADING_FEE_MAKER = _fee_val.get("maker", 0.02)
        TRADING_FEE_TAKER = _fee_val.get("taker", 0.05)
    else:
        TRADING_FEE_MAKER = _fee_val
        TRADING_FEE_TAKER = _fee_val
    TRADING_FEE = TRADING_FEE_TAKER

    # Reapply style preset
    current_preset = STYLE_PRESETS.get(STRATEGY_STYLE, STYLE_PRESETS.get("AISCALP", {}))
    LEVERAGE = current_preset.get("leverage", 10)

    # AI Settings
    AI_SETTINGS = BOT_CONFIG.get("AI_SETTINGS", {})
    AI_MODEL = AI_SETTINGS.get("model", "x-ai/grok-4.1-fast")
    AI_TEMPERATURE = AI_SETTINGS.get("temperature", 0.3)
    AI_MAX_TOKENS = AI_SETTINGS.get("max_tokens", 512)
    AI_REASONING = AI_SETTINGS.get("reasoning", {})
    AI_RETRY_COUNT = AI_SETTINGS.get("retry_count", 3)
    AI_PROVIDER_ROUTING = AI_SETTINGS.get("provider_routing", {})
    AI_FALLBACK_MODELS = AI_SETTINGS.get("fallback_models", [])
    AI_REQUEST_TIMEOUT = AI_SETTINGS.get("request_timeout", 60)
    AI_RETRY_BACKOFF_BASE = AI_SETTINGS.get("retry_backoff_base", 2)
    AI_BASE_URL = AI_SETTINGS.get("base_url") or "https://openrouter.ai/api/v1/chat/completions"

    # Notify callbacks
    for callback in _config_callbacks:
        try:
            callback(old_config, BOT_CONFIG)
        except Exception as e:
            print(f"⚠️ Config callback error: {e}")

    return BOT_CONFIG


# --- Load Configuration ---
BOT_CONFIG = load_bot_config()
_config_mtime = get_config_mtime()

# Exchange selection
EXCHANGE = os.getenv("EXCHANGE", "bingx")
MARKET_TYPE = os.getenv("MARKET_TYPE", "perpetual").lower()
if EXCHANGE.lower() == "bingx":
    MARKET_TYPE = "perpetual"

# Trading settings from config
EXCHANGE_SYMBOLS = BOT_CONFIG.get("EXCHANGE_SYMBOLS", {})
_symbols_value = EXCHANGE_SYMBOLS.get(EXCHANGE, ["BTC/USD"])
if isinstance(_symbols_value, dict):
    SYMBOLS = _symbols_value.get(MARKET_TYPE, ["BTC/USD"])
else:
    SYMBOLS = _symbols_value
EXCHANGE_FEES = BOT_CONFIG.get("EXCHANGE_FEES", {"bingx": {"maker": 0.02, "taker": 0.05}})
_fee_value = EXCHANGE_FEES.get(EXCHANGE, 0.05)
if isinstance(_fee_value, dict) and MARKET_TYPE in _fee_value:
    _fee_value = _fee_value[MARKET_TYPE]
if isinstance(_fee_value, dict):
    TRADING_FEE_MAKER = _fee_value.get("maker", 0.02)
    TRADING_FEE_TAKER = _fee_value.get("taker", 0.05)
else:
    TRADING_FEE_MAKER = _fee_value
    TRADING_FEE_TAKER = _fee_value
TRADING_FEE = TRADING_FEE_TAKER


def update_fee_rates(maker: float, taker: float):
    """Update fee rates from exchange API."""
    global TRADING_FEE_MAKER, TRADING_FEE_TAKER, TRADING_FEE
    TRADING_FEE_MAKER = maker
    TRADING_FEE_TAKER = taker
    TRADING_FEE = TRADING_FEE_TAKER


# Disabled symbols
DISABLED_SYMBOLS = BOT_CONFIG.get("DISABLED_SYMBOLS", [])

# Trading parameters
POSITION_SIZE_PERCENT = BOT_CONFIG.get("POSITION_SIZE_PERCENT", 5.0)
MIN_TRADE_AMOUNT_USDT = BOT_CONFIG.get("MIN_TRADE_AMOUNT_USDT", 10.0)
LEVERAGE = 10  # Will be overwritten by style preset
TAKE_PROFIT_PERCENT = BOT_CONFIG.get("TAKE_PROFIT_PERCENT", 1.5)
STOP_LOSS_PERCENT = BOT_CONFIG.get("STOP_LOSS_PERCENT", 1.5)
MIN_CONFIDENCE_THRESHOLD = BOT_CONFIG.get("MIN_CONFIDENCE_THRESHOLD", 0.7)
MIN_RISK_REWARD_RATIO = BOT_CONFIG.get("MIN_RISK_REWARD_RATIO", 1.5)
MIN_PARTIAL_CLOSE_PNL = BOT_CONFIG.get("MIN_PARTIAL_CLOSE_PNL", 0.5)

# AI thresholds
AI_THRESHOLDS = BOT_CONFIG.get("AI_THRESHOLDS", {})

# Data paths
DATA_DIR = "data"
CHARTS_DIR = "charts"

# Chart settings
CHART_RANGES = BOT_CONFIG.get("CHART_RANGES", {})
DEFAULT_CHART_RANGE = BOT_CONFIG.get("DEFAULT_CHART_RANGE", "1D")
PLOTTER_RANGES = BOT_CONFIG.get("PLOTTER_RANGES") or CHART_RANGES
DEFAULT_PLOTTER_RANGE = BOT_CONFIG.get("DEFAULT_PLOTTER_RANGE", "1D")
CLEANUP_SETTINGS = BOT_CONFIG.get("CLEANUP_SETTINGS", {
    "cleanup_old_charts": True, "charts_retention_days": 7,
    "cleanup_old_data": True, "data_retention_days": 30
})

# News API settings
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
ALPHAVANTAGE_KEY = os.getenv("ALPHAVANTAGE_KEY", "")
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")

# Feature flags
ENABLE_NEWS = BOT_CONFIG.get("ENABLE_NEWS", True)
ENABLE_ADVANCED_ANALYSIS = BOT_CONFIG.get("ENABLE_ADVANCED_ANALYSIS", True)
ENABLE_PARALLEL_MODE = BOT_CONFIG.get("ENABLE_PARALLEL_MODE", True)
ENABLE_PARALLEL_PROCESSING = ENABLE_PARALLEL_MODE
ENABLE_PARALLEL_COLLECTION = ENABLE_PARALLEL_MODE
AGGRESSIVE_MODE = BOT_CONFIG.get("AGGRESSIVE_MODE", False)
AGGRESSIVE_SETTINGS = BOT_CONFIG.get("AGGRESSIVE_SETTINGS", {})
NEWS_SETTINGS = BOT_CONFIG.get("NEWS_SETTINGS", {})
SMART_SAMPLING = BOT_CONFIG.get("SMART_SAMPLING", {"enabled": True, "recent_candles": 30, "history_step": 10})
ENABLE_AI_SKIP_ON_RSI = BOT_CONFIG.get("ENABLE_AI_SKIP_ON_RSI", True)
DECISION_JOURNAL = BOT_CONFIG.get("DECISION_JOURNAL", {"enabled": True, "max_entries": {"SCALP": 5, "AISCALP": 10, "SWING": 10}})
POSITION_LIMITS = BOT_CONFIG.get("POSITION_LIMITS", {"max_positions": 5, "price_precision": 4, "quantity_precision": 4, "balance_safety_margin": 0.95, "position_sync_wait": 1.0})
VALIDATION = BOT_CONFIG.get("VALIDATION", {"rr_soft_limit": 0.5})
TECHNICAL_ANALYSIS = BOT_CONFIG.get("TECHNICAL_ANALYSIS", {"sr_window": 20, "ema_periods": [9, 21], "trend_candle_count": 5, "volume_avg_window": 20, "volume_thresholds": {"anomaly": 2.0, "elevated": 1.2, "low": 0.5}, "seb_length": 20, "seb_multiplier": 2.0, "momentum_volume_threshold": 1.2, "momentum_trend_volume_threshold": 1.0, "news_items_in_prompt": 5})
CHART_SETTINGS = BOT_CONFIG.get("CHART_SETTINGS", {"enabled": True, "update_interval": 10, "min_sleep": 0.5, "sma_periods": [10, 20, 50, 100, 200], "chart_height": 13.5, "dpi": 200})
ERROR_HANDLING = BOT_CONFIG.get("ERROR_HANDLING", {"cycle_error_fallback_sleep": 5})
MOMENTUM_STRATEGY = BOT_CONFIG.get("MOMENTUM_STRATEGY", {
    "enabled": True, "min_volume_ratio": 0.7, "max_candles_in_prompt": 50,
    "trend_consensus_required": False, "momentum_entry_enabled": True,
    "momentum_consecutive_candles": 3
})

# SCALP Settings
SCALP_SETTINGS = BOT_CONFIG.get("SCALP_SETTINGS", {
    "enabled": True, "signal_rules": {},
    "sl_tp": {"sl_atr_mult": 1.0, "tp_atr_mult": 3.0, "trailing_activation_mult": 1.5, "trailing_distance_mult": 0.5},
    "breakeven": {"enabled": True, "trigger_pct": 0.3, "fee_buffer_pct": 0.05},
    "time_exit": {"max_hold_minutes": 15, "breakeven_timeout_minutes": 8},
    "risk_limits": {"base_position_pct": 5.0, "max_consecutive_losses": 5, "daily_loss_limit_pct": 3.0},
    "loops": {"fast_interval": 1.5, "slow_interval": 45},
    "regime_overrides": {}, "interaction_rules": {},
    "ai_integration": {"regime_enabled": True, "veto_enabled": True}
})

# MACDX Settings
MACDX_SETTINGS = BOT_CONFIG.get("MACDX_SETTINGS", {
    "enabled": True,
    "signal_rules": {
        "macd_cross_weight": 2, "rsi_zone_weight": 2, "ema_alignment_weight": 2,
        "not_sideways_weight": 1, "no_exhaustion_weight": 1, "volume_weight": 1,
        "min_score_for_signal": 4, "min_confirmations": 3, "min_volume_ratio": 0.5,
        "min_atr_ratio": 0.3, "rsi_long_max": 65, "rsi_long_min": 25,
        "rsi_short_max": 75, "rsi_short_min": 35, "bb_width_threshold": 0.5, "adx_threshold": 20
    }
})

# Strategy Style
STRATEGY_STYLE = BOT_CONFIG.get("STRATEGY_STYLE", "AISCALP")

# Style Presets
DEFAULT_STYLE_PRESETS = {
    "SCALP": {
        "timeframe": "1m", "chart_period": "6h", "plotter_period": "4h",
        "loop_interval": 3, "position_check_interval": 3,
        "atr_sl_mult": 1.5, "atr_tp_mult": 2.0, "leverage": 15,
        "description": "High frequency, small moves, strict exits."
    },
    "AISCALP": {
        "timeframe": "1m", "chart_period": "1D", "plotter_period": "12h",
        "loop_interval": 60, "position_check_interval": 10,
        "atr_sl_mult": 5.0, "atr_tp_mult": 7.0, "leverage": 10,
        "description": "Day trading, capturing daily trends."
    },
    "SWING": {
        "timeframe": "1h", "chart_period": "14D", "plotter_period": "3D",
        "loop_interval": 300, "position_check_interval": 60,
        "atr_sl_mult": 3.0, "atr_tp_mult": 6.0, "leverage": 5,
        "description": "Multi-day holding, wide stops, ignoring short-term noise."
    },
    "GRID": {
        "timeframe": "1m", "chart_period": "1D", "plotter_period": "6h",
        "loop_interval": 5, "position_check_interval": 5, "leverage": 5,
        "description": "Grid trading with limit order grid."
    },
    "HYBRID": {
        "timeframe": "5m", "chart_period": "1D", "plotter_period": "6h",
        "loop_interval": 60, "position_check_interval": 15,
        "atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "leverage": 10,
        "description": "5m swing with deterministic signals."
    },
    "MACDX": {
        "timeframe": "1m", "chart_period": "1D", "plotter_period": "1D",
        "loop_interval": 45, "position_check_interval": 15,
        "atr_sl_mult": 1.5, "atr_tp_mult": 3.0, "leverage": 10,
        "description": "No-AI MACD crossover strategy with 3-5 confirmations."
    }
}
STYLE_PRESETS = BOT_CONFIG.get("STYLE_PRESETS", DEFAULT_STYLE_PRESETS)

# Apply style preset
current_preset = STYLE_PRESETS.get(STRATEGY_STYLE, STYLE_PRESETS.get("AISCALP", {}))

# Get leverage from strategy config first, fallback to STYLE_PRESETS
_strategy_config = BOT_CONFIG.get(f"{STRATEGY_STYLE}_SETTINGS", {})
_strategy_leverage = _strategy_config.get("preset", {}).get("leverage")
if _strategy_leverage is not None:
    LEVERAGE = _strategy_leverage
    print(f"🔧 Strategy: {STRATEGY_STYLE}, Leverage: {LEVERAGE}x (from strategy config)")
else:
    LEVERAGE = current_preset.get("leverage", 10)
    print(f"🔧 Strategy: {STRATEGY_STYLE}, Leverage: {LEVERAGE}x (from STYLE_PRESETS)")


def parse_interval_minutes(interval_str):
    """Converts '1m', '5m', '1h', '1d' to minutes."""
    if not interval_str:
        return 1
    unit = interval_str[-1].lower()
    value = int(interval_str[:-1])
    if unit == 'm':
        return value
    if unit == 'h':
        return value * 60
    if unit == 'd':
        return value * 1440
    return value


# Dynamic config adjustment based on style
target_chart_period = current_preset.get("chart_period")
if target_chart_period and target_chart_period in CHART_RANGES:
    if DEFAULT_CHART_RANGE != target_chart_period:
        print(f"🔄 Auto-adjusted CHART_RANGE to {target_chart_period} for {STRATEGY_STYLE} style")
        DEFAULT_CHART_RANGE = target_chart_period
        BOT_CONFIG["DEFAULT_CHART_RANGE"] = target_chart_period

target_plotter_period = current_preset.get("plotter_period")
if target_plotter_period and target_plotter_period in PLOTTER_RANGES:
    if DEFAULT_PLOTTER_RANGE != target_plotter_period:
        print(f"🔄 Auto-adjusted PLOTTER_RANGE to {target_plotter_period} for {STRATEGY_STYLE} style")
        DEFAULT_PLOTTER_RANGE = target_plotter_period
        BOT_CONFIG["DEFAULT_PLOTTER_RANGE"] = target_plotter_period

# Smart sampling step calculation
chart_config = CHART_RANGES.get(DEFAULT_CHART_RANGE, {})
target_timeframe_str = current_preset.get("timeframe", "1m")
target_minutes = parse_interval_minutes(target_timeframe_str)

chart_days = chart_config.get("days", 0)
chart_hours = chart_config.get("hours", 0)
chart_minutes_duration = chart_config.get("minutes", 0)
total_duration_minutes = (chart_days * 1440) + (chart_hours * 60) + chart_minutes_duration

if target_minutes > 0 and total_duration_minutes > 0:
    fetched_candles = total_duration_minutes // target_minutes
else:
    fetched_candles = chart_config.get("candles", 720)

max_ai_candles = MOMENTUM_STRATEGY.get("max_candles_in_prompt", 50)
recent_candles = SMART_SAMPLING.get("recent_candles", 30)
history_candles = max(0, fetched_candles - recent_candles)
ai_history_budget = max(1, max_ai_candles - recent_candles)

if SMART_SAMPLING.get("enabled", True) and history_candles > ai_history_budget:
    import math
    optimal_step = max(1, math.ceil(history_candles / ai_history_budget))
    expected_total = (history_candles // optimal_step) + recent_candles
    print(f"🔄 Smart Sampling: step={optimal_step} | {fetched_candles} candles → ~{expected_total} for AI")
    SMART_SAMPLING["history_step"] = optimal_step
else:
    SMART_SAMPLING["history_step"] = 1

# AI API Settings
AI_SETTINGS = BOT_CONFIG.get("AI_SETTINGS", {})
AI_PROVIDER = "openrouter"
AI_MODEL = AI_SETTINGS.get("model", "x-ai/grok-4.1-fast")
AI_TEMPERATURE = AI_SETTINGS.get("temperature", 0.3)
AI_MAX_TOKENS = AI_SETTINGS.get("max_tokens", 512)
AI_REASONING = AI_SETTINGS.get("reasoning", {})
AI_RETRY_COUNT = AI_SETTINGS.get("retry_count", 3)
AI_PROVIDER_ROUTING = AI_SETTINGS.get("provider_routing", {})
AI_FALLBACK_MODELS = AI_SETTINGS.get("fallback_models", [])
AI_REQUEST_TIMEOUT = AI_SETTINGS.get("request_timeout", 60)
AI_RETRY_BACKOFF_BASE = AI_SETTINGS.get("retry_backoff_base", 2)

# AI Overrides
_ai_overrides = AI_SETTINGS.get("overrides", {})
AI_VETO_OVERRIDE = _ai_overrides.get("veto", {})
AI_REGIME_OVERRIDE = _ai_overrides.get("regime", {})

# API Keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
AI_API_KEY = OPENROUTER_API_KEY
AI_BASE_URL = AI_SETTINGS.get("base_url") or "https://openrouter.ai/api/v1/chat/completions"
print(f"🤖 AI Provider: OpenRouter ({AI_MODEL})")

if not AI_API_KEY:
    print(f"⚠️ WARNING: API Key for provider '{AI_PROVIDER}' is missing!")

# Exchange API
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_API_URL = "https://open-api-vst.bingx.com" if MODE == "demo" else "https://open-api.bingx.com"
MEXC_API_KEY = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET_KEY = os.getenv("MEXC_SECRET_KEY", "")
if EXCHANGE.lower() == "mexc":
    print(f"🌐 MEXC API endpoint: https://api.mexc.com ({MARKET_TYPE}, live API; MODE={MODE})")
else:
    print(f"🌐 BingX API endpoint: {BINGX_API_URL} ({'Demo (VST)' if MODE == 'demo' else 'Real'})")
print(f"📰 Новости: {'ВКЛЮЧЕНЫ' if ENABLE_NEWS else 'ОТКЛЮЧЕНЫ'}")


# --- Per-Symbol Config Access (New System) ---

def get_symbol_config(symbol: str) -> dict:
    """
    Get resolved configuration for a specific symbol.

    If new config system is available, returns symbol-specific config
    with profile overrides applied. Otherwise returns global BOT_CONFIG.
    """
    if _use_new_config_system():
        try:
            from src.config_loader import resolve_symbol_config
            return resolve_symbol_config(symbol)
        except Exception as e:
            print(f"⚠️ Symbol config resolution failed for {symbol}: {e}")
    return BOT_CONFIG


def get_strategy_preset(strategy: str = None) -> dict:
    """Get preset for a strategy."""
    strategy = strategy or STRATEGY_STYLE
    return STYLE_PRESETS.get(strategy, STYLE_PRESETS.get("AISCALP", {}))
