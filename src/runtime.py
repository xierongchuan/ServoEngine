"""Runtime model for strategy instances."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class StrategyInstance:
    """Конфигурация одного запущенного инстанса стратегии."""

    id: str
    symbol: str
    strategy: str
    profile: str = "default"
    enabled: bool = True

    @property
    def normalized_symbol(self) -> str:
        """Символ в формате ключа биржи/локального состояния."""
        return normalize_symbol_key(self.symbol)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyInstance":
        strategy = str(data.get("strategy", "")).upper()
        symbol = str(data.get("symbol", "")).upper()
        instance_id = str(data.get("id") or f"{symbol}_{strategy}").lower()
        return cls(
            id=instance_id,
            symbol=symbol,
            strategy=strategy,
            profile=str(data.get("profile", "default") or "default"),
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "profile": self.profile,
            "enabled": self.enabled,
        }


def normalize_symbol_key(symbol: str) -> str:
    """Нормализует символ для ключей состояния: BTC-USDT/BTCUSDT -> BTCUSDT."""
    return str(symbol or "").replace("-", "").replace("/", "").upper()


def build_legacy_instances(
    symbols: Iterable[str],
    strategy: str,
    symbol_profiles: Optional[Dict[str, str]] = None,
) -> List[StrategyInstance]:
    """Создает strategy instances из старой схемы active.json."""
    profiles = symbol_profiles or {}
    result: List[StrategyInstance] = []
    for symbol in symbols:
        normalized_symbol = str(symbol).upper()
        result.append(
            StrategyInstance(
                id=f"{normalize_symbol_key(normalized_symbol)}_{strategy.upper()}".lower(),
                symbol=normalized_symbol,
                strategy=strategy.upper(),
                profile=profiles.get(normalized_symbol, "default"),
                enabled=True,
            )
        )
    return result


def validate_runtime_config_source(bot_config: Dict[str, Any]) -> None:
    """Instance/profile не вправе переключать общий снимок исполнения runtime."""
    from src.trading_settings import execution_account_label, get_trading_settings, validate_trading_settings
    settings = get_trading_settings()
    aliases = {"EXCHANGE": "exchange", "MARKET_TYPE": "market_type", "EXECUTION_MODE": "execution_mode", "ACCOUNT_ID": "account_id"}
    sources = [bot_config]
    instance = bot_config.get("STRATEGY_INSTANCE")
    if isinstance(instance, dict):
        sources.append(instance)
    for source in sources:
        for key in ("execution", "TRADING_SETTINGS"):
            if key in source and validate_trading_settings(source[key]) != settings:
                from src.utils.logger import error
                error(f"❌ {key} в конфигурации instance не совпадает со снимком runtime")
                raise ValueError("Конфигурация instance не может менять execution; остановите весь runtime")
        for key, field in aliases.items():
            if key in source and source[key] != settings[field]:
                from src.utils.logger import error
                error(f"❌ {key} не совпадает со снимком runtime")
                raise ValueError(f"Instance не может переопределять {key}")
        if "EXECUTION_ACCOUNT_ID" in source and source["EXECUTION_ACCOUNT_ID"] != execution_account_label(settings):
            from src.utils.logger import error
            error("❌ EXECUTION_ACCOUNT_ID не совпадает со стабильным namespace runtime")
            raise ValueError("Instance не может переопределять EXECUTION_ACCOUNT_ID")
        if "MODE" in source and source["MODE"] != ("real" if settings["execution_mode"] == "live" else settings["execution_mode"]):
            from src.utils.logger import error
            error("❌ MODE не совпадает со снимком runtime")
            raise ValueError("Instance не может переопределять MODE")


def apply_trading_settings(config_module: Any, settings: Dict[str, Any]) -> None:
    """Обновляет operational globals только перед запуском, без env и callbacks hot-reload."""
    from src.trading_settings import execution_account_label, validate_trading_settings
    selected = validate_trading_settings(settings)
    bot_config = getattr(config_module, "BOT_CONFIG", {})
    symbols = bot_config.get("EXCHANGE_SYMBOLS", {}).get(selected["exchange"], [])
    if isinstance(symbols, dict):
        symbols = symbols.get(selected["market_type"], [])
    if bot_config.get("STRATEGY_INSTANCE"):
        symbols = bot_config.get("SYMBOLS", symbols)
    fees = bot_config.get("EXCHANGE_FEES", {}).get(selected["exchange"], {})
    if isinstance(fees, dict) and selected["market_type"] in fees:
        fees = fees[selected["market_type"]]
    maker = fees.get("maker", 0.02) if isinstance(fees, dict) else fees
    taker = fees.get("taker", 0.05) if isinstance(fees, dict) else fees
    updates = {
        "TRADING_SETTINGS": deepcopy(selected),
        "EXCHANGE": selected["exchange"], "MARKET_TYPE": selected["market_type"],
        "EXECUTION_MODE": selected["execution_mode"],
        "ACCOUNT_ID": selected["account_id"],
        "EXECUTION_ACCOUNT_ID": execution_account_label(selected),
        "MODE": "real" if selected["execution_mode"] == "live" else selected["execution_mode"],
        "BINGX_API_URL": "https://open-api-vst.bingx.com" if selected["execution_mode"] == "demo" else "https://open-api.bingx.com",
        "SYMBOLS": deepcopy(symbols),
        "TRADING_FEE_MAKER": maker, "TRADING_FEE_TAKER": taker, "TRADING_FEE": taker,
    }
    config_module.__dict__.update(updates)


def prepare_runtime_config(settings: Dict[str, Any]) -> None:
    """Подготавливает parent или новый child до main; текущие instances не hot-switch."""
    from src.trading_settings import install_trading_settings_snapshot
    selected = install_trading_settings_snapshot(settings)
    from src import config as config_module
    from src.config_loader import clear_config_cache, get_legacy_compatible_config
    clear_config_cache()
    bot_config = get_legacy_compatible_config()
    validate_runtime_config_source(bot_config)
    # Сначала выбираем общую биржу/рынок; legacy apply использует их для symbols/fees.
    apply_trading_settings(config_module, selected)
    apply_legacy_runtime_config(config_module, bot_config)
    apply_trading_settings(config_module, selected)


def run_runtime_chart_worker(settings: Dict[str, Any], config: Dict[str, Any], stop_event=None) -> None:
    """Spawn получает утверждённый снимок, а не staged execution из trading.json."""
    prepare_runtime_config(settings)
    from src.trading_settings import get_trading_settings, execution_account_label
    from src.core.chart_worker import run_chart_worker
    selected = get_trading_settings()
    chart_config = deepcopy(config)
    chart_config.update(
        execution=selected, TRADING_SETTINGS=selected,
        EXCHANGE=selected["exchange"], MARKET_TYPE=selected["market_type"],
        EXECUTION_MODE=selected["execution_mode"], EXECUTION_ACCOUNT_ID=execution_account_label(selected),
    )
    run_chart_worker(chart_config, stop_event)


def apply_legacy_runtime_config(config_module: Any, bot_config: Dict[str, Any]) -> None:
    """Применяет legacy BOT_CONFIG, но не позволяет instance менять общий execution."""
    validate_runtime_config_source(bot_config)
    config_module.BOT_CONFIG = bot_config

    config_module.DISABLED_SYMBOLS = bot_config.get("DISABLED_SYMBOLS", [])
    config_module.EXCHANGE_SYMBOLS = bot_config.get("EXCHANGE_SYMBOLS", {})
    exchange_symbols = bot_config.get("EXCHANGE_SYMBOLS", {}).get(config_module.EXCHANGE, [])
    if isinstance(exchange_symbols, dict):
        exchange_symbols = exchange_symbols.get(getattr(config_module, "MARKET_TYPE", "perpetual"), [])
    config_module.SYMBOLS = bot_config.get("SYMBOLS", exchange_symbols)

    config_module.POSITION_SIZE_PERCENT = bot_config.get("POSITION_SIZE_PERCENT", 5.0)
    config_module.MIN_TRADE_AMOUNT_USDT = bot_config.get("MIN_TRADE_AMOUNT_USDT", 10.0)
    config_module.TAKE_PROFIT_PERCENT = bot_config.get("TAKE_PROFIT_PERCENT", 1.5)
    config_module.STOP_LOSS_PERCENT = bot_config.get("STOP_LOSS_PERCENT", 1.5)
    config_module.MIN_CONFIDENCE_THRESHOLD = bot_config.get("MIN_CONFIDENCE_THRESHOLD", 0.7)
    config_module.MIN_RISK_REWARD_RATIO = bot_config.get("MIN_RISK_REWARD_RATIO", 1.5)
    config_module.MIN_PARTIAL_CLOSE_PNL = bot_config.get("MIN_PARTIAL_CLOSE_PNL", 0.5)

    config_module.AI_THRESHOLDS = bot_config.get("AI_THRESHOLDS", {})
    config_module.ENABLE_NEWS = bot_config.get("ENABLE_NEWS", True)
    config_module.ENABLE_ADVANCED_ANALYSIS = bot_config.get("ENABLE_ADVANCED_ANALYSIS", True)
    config_module.ENABLE_PARALLEL_MODE = bot_config.get("ENABLE_PARALLEL_MODE", True)
    config_module.ENABLE_PARALLEL_PROCESSING = config_module.ENABLE_PARALLEL_MODE
    config_module.ENABLE_PARALLEL_COLLECTION = config_module.ENABLE_PARALLEL_MODE
    config_module.AGGRESSIVE_MODE = bot_config.get("AGGRESSIVE_MODE", False)
    config_module.AGGRESSIVE_SETTINGS = bot_config.get("AGGRESSIVE_SETTINGS", {})
    config_module.NEWS_SETTINGS = bot_config.get("NEWS_SETTINGS", {})
    config_module.SMART_SAMPLING = bot_config.get("SMART_SAMPLING", {"enabled": True, "recent_candles": 30, "history_step": 10})
    config_module.ENABLE_AI_SKIP_ON_RSI = bot_config.get("ENABLE_AI_SKIP_ON_RSI", True)
    config_module.DECISION_JOURNAL = bot_config.get("DECISION_JOURNAL", {"enabled": True, "max_entries": {}})
    config_module.POSITION_LIMITS = bot_config.get("POSITION_LIMITS", {})
    config_module.VALIDATION = bot_config.get("VALIDATION", {"rr_soft_limit": 0.5})
    config_module.TECHNICAL_ANALYSIS = bot_config.get("TECHNICAL_ANALYSIS", {})
    config_module.CHART_SETTINGS = bot_config.get("CHART_SETTINGS", {})
    config_module.CHART_RANGES = bot_config.get("CHART_RANGES", {})
    config_module.DEFAULT_CHART_RANGE = bot_config.get("DEFAULT_CHART_RANGE", config_module.DEFAULT_CHART_RANGE)
    config_module.PLOTTER_RANGES = bot_config.get("PLOTTER_RANGES") or bot_config.get(
        "CHART_RANGES", config_module.CHART_RANGES
    )
    config_module.DEFAULT_PLOTTER_RANGE = bot_config.get("DEFAULT_PLOTTER_RANGE", config_module.DEFAULT_PLOTTER_RANGE)
    config_module.ERROR_HANDLING = bot_config.get("ERROR_HANDLING", {"cycle_error_fallback_sleep": 5})
    config_module.MOMENTUM_STRATEGY = bot_config.get("MOMENTUM_STRATEGY", {})

    config_module.STRATEGY_STYLE = bot_config.get("STRATEGY_STYLE", "AISCALP")
    config_module.STYLE_PRESETS = bot_config.get("STYLE_PRESETS", config_module.DEFAULT_STYLE_PRESETS)
    config_module.SCALP_SETTINGS = bot_config.get("SCALP_SETTINGS", config_module.SCALP_SETTINGS)
    config_module.MACDX_SETTINGS = bot_config.get("MACDX_SETTINGS", config_module.MACDX_SETTINGS)
    config_module.HYBRID_SETTINGS = bot_config.get("HYBRID_SETTINGS", {})
    config_module.AISCALP_SETTINGS = bot_config.get("AISCALP_SETTINGS", {})
    config_module.GRID_SETTINGS = bot_config.get("GRID_SETTINGS", {})

    exchange_fees = bot_config.get("EXCHANGE_FEES", {"bingx": {"maker": 0.02, "taker": 0.05}})
    fee_value = exchange_fees.get(config_module.EXCHANGE, 0.05)
    market_type = getattr(config_module, "MARKET_TYPE", "perpetual")
    if isinstance(fee_value, dict) and market_type in fee_value:
        fee_value = fee_value[market_type]
    if isinstance(fee_value, dict):
        config_module.TRADING_FEE_MAKER = fee_value.get("maker", 0.02)
        config_module.TRADING_FEE_TAKER = fee_value.get("taker", 0.05)
    else:
        config_module.TRADING_FEE_MAKER = fee_value
        config_module.TRADING_FEE_TAKER = fee_value
    config_module.TRADING_FEE = config_module.TRADING_FEE_TAKER

    current_preset = config_module.STYLE_PRESETS.get(config_module.STRATEGY_STYLE, {})
    strategy_config = bot_config.get(f"{config_module.STRATEGY_STYLE}_SETTINGS", {})
    config_module.LEVERAGE = strategy_config.get("preset", {}).get("leverage", current_preset.get("leverage", 10))

    ai_settings = bot_config.get("AI_SETTINGS", {})
    config_module.AI_SETTINGS = ai_settings
    config_module.AI_MODEL = ai_settings.get("model", config_module.AI_MODEL)
    config_module.AI_TEMPERATURE = ai_settings.get("temperature", config_module.AI_TEMPERATURE)
    config_module.AI_MAX_TOKENS = ai_settings.get("max_tokens", config_module.AI_MAX_TOKENS)
    config_module.AI_REASONING = ai_settings.get("reasoning", config_module.AI_REASONING)
    config_module.AI_RETRY_COUNT = ai_settings.get("retry_count", config_module.AI_RETRY_COUNT)
    config_module.AI_PROVIDER_ROUTING = ai_settings.get("provider_routing", config_module.AI_PROVIDER_ROUTING)
    config_module.AI_FALLBACK_MODELS = ai_settings.get("fallback_models", config_module.AI_FALLBACK_MODELS)
    config_module.AI_REQUEST_TIMEOUT = ai_settings.get("request_timeout", config_module.AI_REQUEST_TIMEOUT)
    config_module.AI_RETRY_BACKOFF_BASE = ai_settings.get("retry_backoff_base", config_module.AI_RETRY_BACKOFF_BASE)
    config_module.AI_BASE_URL = ai_settings.get("base_url") or config_module.AI_BASE_URL
