"""
Configuration API routes for the modular config system.

Provides endpoints for:
- Config read/write (merged from config/ directory)
- Strategy management (config/strategies/)
- Profile management per symbol (config/profiles/)
- Active config (config/active.json)
- Trading config (config/trading.json)
"""

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..services.auth import get_current_user
from ..services.data_reader import DataReader
from ..config import CONFIG_PATH

logger = logging.getLogger("panel.config")

router = APIRouter(prefix="/api/config", tags=["config"])
reader = DataReader()

# Paths for config system
# Derive from CONFIG_PATH parent to work in container (/app/config/)
CONFIG_DIR = CONFIG_PATH.parent
STRATEGIES_DIR = CONFIG_DIR / "strategies"
PROFILES_DIR = CONFIG_DIR / "profiles"

# Settings that can be applied without restarting workers
HOT_RELOADABLE_KEYS = {
    "DISABLED_SYMBOLS", "POSITION_SIZE_PERCENT", "MIN_TRADE_AMOUNT_USDT",
    "MIN_CONFIDENCE_THRESHOLD", "MIN_RISK_REWARD_RATIO",
    "STRATEGY_STYLE", "STYLE_PRESETS", "LEVERAGE",
    "AI_SETTINGS", "HYBRID_SETTINGS", "REGIME_SETTINGS",
    "AGGRESSIVE_MODE", "AGGRESSIVE_SETTINGS",
    "DYNAMIC_SIZING", "PERFORMANCE_TRACKING",
    "TAKE_PROFIT_PERCENT", "STOP_LOSS_PERCENT",
    "ENABLE_NEWS", "ENABLE_AI_SKIP_ON_RSI",
    "DECISION_JOURNAL", "MOMENTUM_STRATEGY",
    "TECHNICAL_ANALYSIS", "SMART_SAMPLING",
    # New config system keys
    "strategy", "disabled_symbols", "symbol_profiles",
}

# Settings that require process restart
RESTART_REQUIRED_KEYS = {
    "EXCHANGE_SYMBOLS", "CHART_RANGES", "ENABLE_PARALLEL_MODE", "GRID_SETTINGS",
    "symbols", "strategy_instances",
}

# Validation rules: (key, type, min, max)
VALIDATION_RULES = {
    "POSITION_SIZE_PERCENT": {"type": (int, float), "min": 0.1, "max": 100},
    "MIN_CONFIDENCE_THRESHOLD": {"type": (int, float), "min": 0.0, "max": 1.0},
    "MIN_RISK_REWARD_RATIO": {"type": (int, float), "min": 0.1, "max": 100},
    "MIN_TRADE_AMOUNT_USDT": {"type": (int, float), "min": 1, "max": 100000},
    "TAKE_PROFIT_PERCENT": {"type": (int, float), "min": 0.1, "max": 100},
    "STOP_LOSS_PERCENT": {"type": (int, float), "min": 0.1, "max": 100},
    "STRATEGY_STYLE": {"type": str, "values": ["SCALP", "AISCALP", "SWING", "GRID", "HYBRID", "MACDX"]},
    "strategy": {"type": str, "values": ["SCALP", "AISCALP", "SWING", "GRID", "HYBRID", "MACDX"]},
}

# Available strategies
AVAILABLE_STRATEGIES = ["SCALP", "AISCALP", "SWING", "GRID", "HYBRID", "MACDX"]
INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def _use_new_config_system() -> bool:
    """Check if new config system is available."""
    config_dir_exists = CONFIG_DIR.is_dir()
    active_json_exists = (CONFIG_DIR / "active.json").exists()
    result = config_dir_exists and active_json_exists
    logger.debug(
        "_use_new_config_system: CONFIG_DIR=%s (exists=%s), active.json exists=%s, result=%s",
        CONFIG_DIR, config_dir_exists, active_json_exists, result
    )
    return result


def _load_json(path: Path) -> dict:
    """Load JSON file safely."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
    return {}


def _save_json(path: Path, data: dict) -> None:
    """Save JSON file with pretty formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _normalize_symbol_key(symbol: str) -> str:
    """Normalize symbol for config identity: BTC-USDT/BTCUSDT -> BTCUSDT."""
    return str(symbol or "").replace("-", "").replace("/", "").upper()


def _default_instance_id(symbol: str, strategy: str) -> str:
    return f"{_normalize_symbol_key(symbol)}_{strategy.upper()}".lower()


def _profile_exists(profile: str) -> bool:
    return profile == "default" or (PROFILES_DIR / f"{profile}.json").exists()


def _validate_strategy_instance(raw: dict) -> dict:
    """Validate and normalize one strategy instance from active.json/UI."""
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="strategy instance must be an object")

    symbol = _normalize_symbol_key(raw.get("symbol", ""))
    strategy = str(raw.get("strategy", "")).upper()
    profile = str(raw.get("profile", "default") or "default")
    enabled = bool(raw.get("enabled", True))
    instance_id = str(raw.get("id") or _default_instance_id(symbol, strategy)).lower()

    if not symbol:
        raise HTTPException(status_code=400, detail="strategy instance symbol is required")
    if strategy not in AVAILABLE_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Invalid strategy for instance {instance_id}: {strategy}")
    if not INSTANCE_ID_RE.match(instance_id):
        raise HTTPException(status_code=400, detail=f"Invalid strategy instance id: {instance_id}")
    if not _profile_exists(profile):
        raise HTTPException(status_code=400, detail=f"Profile not found: {profile}")

    return {
        "id": instance_id,
        "symbol": symbol,
        "strategy": strategy,
        "profile": profile,
        "enabled": enabled,
    }


def _get_strategy_instances(active: dict, exchange: str = "bingx") -> list[dict]:
    """Return normalized strategy instances, with fallback from legacy active fields."""
    raw_instances = active.get("strategy_instances") or []
    if raw_instances:
        return [_validate_strategy_instance(item) for item in raw_instances]

    symbols = active.get("symbols", {}).get(exchange, [])
    strategy = str(active.get("strategy", "MACDX")).upper()
    symbol_profiles = active.get("symbol_profiles", {})
    return [
        _validate_strategy_instance({
            "id": _default_instance_id(symbol, strategy),
            "symbol": symbol,
            "strategy": strategy,
            "profile": symbol_profiles.get(_normalize_symbol_key(symbol), "default"),
            "enabled": True,
        })
        for symbol in symbols
    ]


def _sync_active_legacy_fields(active: dict, exchange: str = "bingx") -> dict:
    """
    Keep legacy active fields in sync with strategy_instances.

    Старые части панели и бота всё ещё читают active.strategy/symbols/symbol_profiles.
    При нескольких инстансах одного символа symbol_profiles хранит только первый профиль,
    а точная настройка остаётся в strategy_instances.
    """
    instances = _get_strategy_instances(active, exchange)
    if not instances:
        return active

    active["strategy_instances"] = instances
    enabled_instances = [item for item in instances if item.get("enabled", True)]
    visible_instances = enabled_instances or instances
    symbols = sorted({_normalize_symbol_key(item["symbol"]) for item in visible_instances})

    active["symbols"] = {**active.get("symbols", {}), exchange: symbols}
    active["strategy"] = visible_instances[0]["strategy"]

    symbol_profiles = dict(active.get("symbol_profiles", {}))
    for item in visible_instances:
        symbol_profiles.setdefault(item["symbol"], item.get("profile", "default"))
    active["symbol_profiles"] = symbol_profiles
    active.setdefault("disabled_symbols", [])
    return active


def validate_config_values(config: dict) -> list[str]:
    """Validate config values against rules. Returns list of error messages."""
    errors = []
    for key, rules in VALIDATION_RULES.items():
        if key not in config:
            continue
        value = config[key]

        # Type check
        expected_type = rules["type"]
        if not isinstance(value, expected_type):
            type_name = expected_type.__name__ if isinstance(expected_type, type) else str(expected_type)
            errors.append(f"{key}: expected {type_name}, got {type(value).__name__}")
            continue

        # Range check
        if "min" in rules and value < rules["min"]:
            errors.append(f"{key}: value {value} below minimum {rules['min']}")
        if "max" in rules and value > rules["max"]:
            errors.append(f"{key}: value {value} above maximum {rules['max']}")

        # Allowed values check
        if "values" in rules and value not in rules["values"]:
            errors.append(f"{key}: '{value}' not in {rules['values']}")

    # Validate leverage in STYLE_PRESETS
    presets = config.get("STYLE_PRESETS")
    if isinstance(presets, dict):
        for style, preset in presets.items():
            if isinstance(preset, dict) and "leverage" in preset:
                lev = preset["leverage"]
                if not isinstance(lev, (int, float)) or lev < 1 or lev > 125:
                    errors.append(f"STYLE_PRESETS.{style}.leverage: must be between 1 and 125")

    # Validate AI_SETTINGS sub-fields
    ai = config.get("AI_SETTINGS")
    if isinstance(ai, dict):
        temp = ai.get("temperature")
        if temp is not None and (not isinstance(temp, (int, float)) or temp < 0 or temp > 2):
            errors.append("AI_SETTINGS.temperature: must be between 0 and 2")
        max_tokens = ai.get("max_tokens")
        if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens < 1):
            errors.append("AI_SETTINGS.max_tokens: must be a positive integer")

    return errors


def classify_changes(old_config: dict, new_config: dict) -> dict:
    """Classify which keys changed and whether they need restart."""
    changed_keys = set()
    for key in set(list(old_config.keys()) + list(new_config.keys())):
        if old_config.get(key) != new_config.get(key):
            changed_keys.add(key)

    return {
        "hot_reloadable": sorted(changed_keys & HOT_RELOADABLE_KEYS),
        "restart_required": sorted(changed_keys & RESTART_REQUIRED_KEYS),
        "other": sorted(changed_keys - HOT_RELOADABLE_KEYS - RESTART_REQUIRED_KEYS),
    }


# ============================================================================
# Config Endpoints (merged from config/ directory)
# ============================================================================

def _build_merged_config() -> dict:
    """Build a merged config dict from new config system files."""
    if _use_new_config_system():
        merged = {}
        # Load base, trading, active configs
        base = _load_json(CONFIG_DIR / "base.json")
        trading = _load_json(CONFIG_DIR / "trading.json")
        active = _load_json(CONFIG_DIR / "active.json")

        instances = _get_strategy_instances(active)
        symbols = sorted({item["symbol"] for item in instances if item.get("enabled", True)})

        # Map new config keys to legacy format for backward compat
        merged["STRATEGY_STYLE"] = active.get("strategy") or (instances[0]["strategy"] if instances else "MACDX")
        merged["EXCHANGE_SYMBOLS"] = active.get("symbols") or {"bingx": symbols}
        merged["DISABLED_SYMBOLS"] = active.get("disabled_symbols", [])
        merged["STRATEGY_INSTANCES"] = instances

        # Trading params
        pos = trading.get("position", {})
        risk = trading.get("risk", {})
        features = trading.get("features", {})
        merged["POSITION_SIZE_PERCENT"] = pos.get("size_percent", 10)
        merged["MIN_TRADE_AMOUNT_USDT"] = pos.get("min_trade_amount_usdt", 10)
        merged["MIN_CONFIDENCE_THRESHOLD"] = risk.get("min_confidence_threshold", 0.55)
        merged["MIN_RISK_REWARD_RATIO"] = risk.get("min_risk_reward_ratio", 1.2)
        merged["TAKE_PROFIT_PERCENT"] = risk.get("take_profit_percent", 2.5)
        merged["STOP_LOSS_PERCENT"] = risk.get("stop_loss_percent", 1.0)
        merged["ENABLE_NEWS"] = features.get("enable_news", False)
        merged["AGGRESSIVE_MODE"] = features.get("aggressive_mode", False)

        # Base config sections
        merged["EXCHANGE_FEES"] = base.get("exchange", {}).get("fees", {})
        merged["AI_SETTINGS"] = base.get("ai", {})
        merged["CHART_RANGES"] = base.get("chart_ranges", {})
        merged["PLOTTER_RANGES"] = base.get("plotter_ranges", {})
        merged["TECHNICAL_ANALYSIS"] = base.get("technical_analysis", {})
        merged["CHART_SETTINGS"] = base.get("chart_settings", {})
        merged["POSITION_LIMITS"] = base.get("position_limits", {})
        merged["NEWS_SETTINGS"] = base.get("news", {})
        merged["CLEANUP_SETTINGS"] = base.get("cleanup_settings", {})
        merged["DECISION_JOURNAL"] = base.get("decision_journal", {})

        # Build style presets from strategy files
        presets = {}
        if STRATEGIES_DIR.exists():
            for path in STRATEGIES_DIR.glob("*.json"):
                name = path.stem.upper()
                strat = _load_json(path)
                presets[name] = strat.get("preset", {})
                presets[name]["description"] = strat.get("_description", "")
        merged["STYLE_PRESETS"] = presets

        return merged

    # Legacy fallback
    return reader.read_config()


@router.get("")
async def get_config(_user: dict = Depends(get_current_user)) -> dict:
    """Get merged config from config/ directory."""
    return _build_merged_config()


@router.get("/meta")
async def get_config_meta(_user: dict = Depends(get_current_user)) -> dict:
    """Return metadata about config fields (hot-reloadable vs restart-required)."""
    return {
        "hot_reloadable": sorted(HOT_RELOADABLE_KEYS),
        "restart_required": sorted(RESTART_REQUIRED_KEYS),
        "use_new_system": _use_new_config_system(),
    }


@router.post("/validate")
async def validate_config(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Validate config without saving."""
    try:
        body = await request.body()
        new_config = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(new_config, dict):
        raise HTTPException(status_code=400, detail="Config must be a JSON object")

    errors = validate_config_values(new_config)
    current_config = _build_merged_config()
    changes = classify_changes(current_config, new_config)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "changes": changes,
    }


@router.put("")
async def update_config(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Update config — routes to appropriate config file in new system."""
    try:
        body = await request.body()
        new_config = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(new_config, dict):
        raise HTTPException(status_code=400, detail="Config must be a JSON object")

    errors = validate_config_values(new_config)
    if errors:
        logger.warning("Config validation failed: %s", errors)
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation failed", "validation_errors": errors},
        )

    current_config = _build_merged_config()
    changes = classify_changes(current_config, new_config)

    if _use_new_config_system():
        try:
            # Route changes to appropriate config files
            active_keys = {"STRATEGY_STYLE", "EXCHANGE_SYMBOLS", "DISABLED_SYMBOLS", "STRATEGY_INSTANCES"}
            trading_keys = {
                "POSITION_SIZE_PERCENT", "MIN_TRADE_AMOUNT_USDT",
                "MIN_CONFIDENCE_THRESHOLD", "MIN_RISK_REWARD_RATIO",
                "TAKE_PROFIT_PERCENT", "STOP_LOSS_PERCENT",
                "ENABLE_NEWS", "AGGRESSIVE_MODE",
            }

            # Update active.json
            active_changes = {k: v for k, v in new_config.items() if k in active_keys}
            if active_changes:
                active = _load_json(CONFIG_DIR / "active.json")
                if "STRATEGY_STYLE" in active_changes:
                    active["strategy"] = active_changes["STRATEGY_STYLE"]
                if "EXCHANGE_SYMBOLS" in active_changes:
                    active["symbols"] = active_changes["EXCHANGE_SYMBOLS"]
                if "DISABLED_SYMBOLS" in active_changes:
                    active["disabled_symbols"] = active_changes["DISABLED_SYMBOLS"]
                if "STRATEGY_INSTANCES" in active_changes:
                    active["strategy_instances"] = [
                        _validate_strategy_instance(item)
                        for item in active_changes["STRATEGY_INSTANCES"]
                    ]
                    active = _sync_active_legacy_fields(active)
                _save_json(CONFIG_DIR / "active.json", active)

            # Update trading.json
            trading_changes = {k: v for k, v in new_config.items() if k in trading_keys}
            if trading_changes:
                trading = _load_json(CONFIG_DIR / "trading.json")
                pos = trading.setdefault("position", {})
                risk = trading.setdefault("risk", {})
                features = trading.setdefault("features", {})
                if "POSITION_SIZE_PERCENT" in trading_changes:
                    pos["size_percent"] = trading_changes["POSITION_SIZE_PERCENT"]
                if "MIN_TRADE_AMOUNT_USDT" in trading_changes:
                    pos["min_trade_amount_usdt"] = trading_changes["MIN_TRADE_AMOUNT_USDT"]
                if "MIN_CONFIDENCE_THRESHOLD" in trading_changes:
                    risk["min_confidence_threshold"] = trading_changes["MIN_CONFIDENCE_THRESHOLD"]
                if "MIN_RISK_REWARD_RATIO" in trading_changes:
                    risk["min_risk_reward_ratio"] = trading_changes["MIN_RISK_REWARD_RATIO"]
                if "TAKE_PROFIT_PERCENT" in trading_changes:
                    risk["take_profit_percent"] = trading_changes["TAKE_PROFIT_PERCENT"]
                if "STOP_LOSS_PERCENT" in trading_changes:
                    risk["stop_loss_percent"] = trading_changes["STOP_LOSS_PERCENT"]
                if "ENABLE_NEWS" in trading_changes:
                    features["enable_news"] = trading_changes["ENABLE_NEWS"]
                if "AGGRESSIVE_MODE" in trading_changes:
                    features["aggressive_mode"] = trading_changes["AGGRESSIVE_MODE"]
                _save_json(CONFIG_DIR / "trading.json", trading)

            logger.info("Config saved to new config system. Changes: %s", changes)
        except OSError as e:
            logger.error("Failed to write config: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to write config: {e}")
    else:
        try:
            reader.write_config(new_config)
            logger.info("Config saved to legacy file. Changes: %s", changes)
        except OSError as e:
            logger.error("Failed to write config: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to write config: {e}")

    return {
        "status": "ok",
        "changes": changes,
        "needs_restart": len(changes["restart_required"]) > 0,
    }


# ============================================================================
# New Config System Endpoints (config/ directory)
# ============================================================================

@router.get("/system")
async def get_config_system_info(_user: dict = Depends(get_current_user)) -> dict:
    """Get info about the configuration system in use."""
    use_new = _use_new_config_system()

    # Diagnostic info for debugging strategy loading issues
    strategy_files = []
    if STRATEGIES_DIR.exists():
        strategy_files = [f.name for f in STRATEGIES_DIR.glob("*.json")]

    legacy_strategies = []
    try:
        legacy = reader.read_config()
        legacy_strategies = list(legacy.get("STYLE_PRESETS", {}).keys())
    except Exception as e:
        logger.warning("Failed to read legacy config for diagnostics: %s", e)

    return {
        "use_new_system": use_new,
        "config_dir": str(CONFIG_DIR) if use_new else None,
        "config_dir_exists": CONFIG_DIR.is_dir(),
        "active_json_exists": (CONFIG_DIR / "active.json").exists(),
        "strategies_dir_exists": STRATEGIES_DIR.exists(),
        "strategy_files": strategy_files,
        "legacy_strategies": legacy_strategies,
        "available_strategies": AVAILABLE_STRATEGIES,
        "legacy_config_path": str(reader.config_path),
    }


@router.get("/active")
async def get_active_config(_user: dict = Depends(get_current_user)) -> dict:
    """Get active.json configuration (strategy, symbols, profiles)."""
    if not _use_new_config_system():
        # Return data from legacy config
        legacy = reader.read_config()
        legacy_active = {
            "strategy": legacy.get("STRATEGY_STYLE", "HYBRID"),
            "symbols": legacy.get("EXCHANGE_SYMBOLS", {}),
            "symbol_profiles": {},
            "disabled_symbols": legacy.get("DISABLED_SYMBOLS", []),
        }
        legacy_active["strategy_instances"] = _get_strategy_instances(legacy_active)
        return legacy_active

    active = _load_json(CONFIG_DIR / "active.json")
    active["strategy_instances"] = _get_strategy_instances(active)
    return active


@router.put("/active")
async def update_active_config(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Update active.json configuration."""
    try:
        body = await request.body()
        new_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    active_path = CONFIG_DIR / "active.json"
    current = _load_json(active_path)

    # Validate strategy
    if "strategy" in new_data:
        strategy = new_data["strategy"].upper()
        if strategy not in AVAILABLE_STRATEGIES:
            raise HTTPException(status_code=400, detail=f"Invalid strategy: {strategy}")
        new_data["strategy"] = strategy

    if "strategy_instances" in new_data:
        raw_instances = new_data.get("strategy_instances") or []
        if not isinstance(raw_instances, list):
            raise HTTPException(status_code=400, detail="strategy_instances must be a list")
        normalized_instances = [_validate_strategy_instance(item) for item in raw_instances]
        ids = [item["id"] for item in normalized_instances]
        if len(ids) != len(set(ids)):
            raise HTTPException(status_code=400, detail="strategy instance ids must be unique")
        new_data["strategy_instances"] = normalized_instances

    # Merge with current
    current.update(new_data)
    current = _sync_active_legacy_fields(current)

    try:
        _save_json(active_path, current)
        logger.info("Active config updated: %s", list(new_data.keys()))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "config": current}


@router.get("/trading")
async def get_trading_config(_user: dict = Depends(get_current_user)) -> dict:
    """Get trading.json configuration (position, risk, features)."""
    if not _use_new_config_system():
        return {}
    return _load_json(CONFIG_DIR / "trading.json")


@router.put("/trading")
async def update_trading_config(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Update trading.json configuration."""
    try:
        body = await request.body()
        new_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    trading_path = CONFIG_DIR / "trading.json"
    current = _load_json(trading_path)

    # Deep merge
    def deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for key, value in override.items():
            if key.startswith("_"):
                continue
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    current = deep_merge(current, new_data)

    try:
        _save_json(trading_path, current)
        logger.info("Trading config updated")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "config": current}


@router.get("/base")
async def get_base_config(_user: dict = Depends(get_current_user)) -> dict:
    """Get base.json configuration (infrastructure, AI, etc.)."""
    if not _use_new_config_system():
        return {}
    return _load_json(CONFIG_DIR / "base.json")


@router.put("/base")
async def update_base_config(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Update base.json configuration."""
    try:
        body = await request.body()
        new_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    base_path = CONFIG_DIR / "base.json"
    current = _load_json(base_path)

    # Deep merge
    def deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for key, value in override.items():
            if key.startswith("_"):
                continue
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    current = deep_merge(current, new_data)

    try:
        _save_json(base_path, current)
        logger.info("Base config updated")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "config": current}


@router.post("/active/symbol")
async def add_active_symbol(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Add a symbol to the active configuration."""
    try:
        body = await request.body()
        data = json.loads(body)
        symbol = data.get("symbol")
        exchange = data.get("exchange", "bingx")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    active_path = CONFIG_DIR / "active.json"
    active = _load_json(active_path)

    symbols = active.get("symbols", {})
    exchange_symbols = symbols.get(exchange, [])

    symbol_upper = symbol.upper()
    if symbol_upper not in exchange_symbols:
        exchange_symbols.append(symbol_upper)
        symbols[exchange] = exchange_symbols
        active["symbols"] = symbols

    instances = _get_strategy_instances(active, exchange)
    strategy = str(data.get("strategy") or active.get("strategy") or "HYBRID").upper()
    profile = str(data.get("profile") or active.get("symbol_profiles", {}).get(_normalize_symbol_key(symbol_upper), "default"))
    new_instance = _validate_strategy_instance({
        "id": data.get("id") or _default_instance_id(symbol_upper, strategy),
        "symbol": symbol_upper,
        "strategy": strategy,
        "profile": profile,
        "enabled": bool(data.get("enabled", True)),
    })
    if not any(item["id"] == new_instance["id"] for item in instances):
        instances.append(new_instance)
    active["strategy_instances"] = instances
    active = _sync_active_legacy_fields(active, exchange)

    try:
        _save_json(active_path, active)
        logger.info("Symbol %s added to %s", symbol_upper, exchange)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "symbols": symbols}


@router.delete("/active/symbol/{symbol}")
async def remove_active_symbol(symbol: str, exchange: str = "bingx", _user: dict = Depends(get_current_user)) -> dict:
    """Remove a symbol from the active configuration."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    active_path = CONFIG_DIR / "active.json"
    active = _load_json(active_path)

    symbols = active.get("symbols", {})
    exchange_symbols = symbols.get(exchange, [])

    symbol_upper = symbol.upper()
    if symbol_upper in exchange_symbols:
        exchange_symbols.remove(symbol_upper)
        symbols[exchange] = exchange_symbols
        active["symbols"] = symbols

    instances = [
        item for item in _get_strategy_instances(active, exchange)
        if _normalize_symbol_key(item["symbol"]) != _normalize_symbol_key(symbol_upper)
    ]
    active["strategy_instances"] = instances
    symbol_profiles = active.get("symbol_profiles", {})
    symbol_profiles.pop(_normalize_symbol_key(symbol_upper), None)
    active["symbol_profiles"] = symbol_profiles
    active = _sync_active_legacy_fields(active, exchange) if instances else active

    try:
        _save_json(active_path, active)
        logger.info("Symbol %s removed from %s", symbol_upper, exchange)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "symbols": symbols}


@router.get("/strategy-instances")
async def list_strategy_instances(_user: dict = Depends(get_current_user)) -> dict:
    """List configured strategy instances."""
    if not _use_new_config_system():
        legacy = reader.read_config()
        active = {
            "strategy": legacy.get("STRATEGY_STYLE", "HYBRID"),
            "symbols": legacy.get("EXCHANGE_SYMBOLS", {}),
            "symbol_profiles": {},
            "disabled_symbols": legacy.get("DISABLED_SYMBOLS", []),
        }
        instances = _get_strategy_instances(active)
    else:
        active = _load_json(CONFIG_DIR / "active.json")
        instances = _get_strategy_instances(active)

    return {
        "strategy_instances": instances,
        "available": [item["id"] for item in instances],
    }


@router.post("/strategy-instances")
async def create_strategy_instance(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Create one strategy instance in active.json."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    try:
        body = await request.body()
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    new_instance = _validate_strategy_instance(data)
    active_path = CONFIG_DIR / "active.json"
    active = _load_json(active_path)
    instances = _get_strategy_instances(active)

    if any(item["id"] == new_instance["id"] for item in instances):
        raise HTTPException(status_code=400, detail=f"Strategy instance already exists: {new_instance['id']}")

    instances.append(new_instance)
    active["strategy_instances"] = instances
    active = _sync_active_legacy_fields(active)

    try:
        _save_json(active_path, active)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "instance": new_instance, "config": active}


@router.put("/strategy-instances/{instance_id}")
async def update_strategy_instance(instance_id: str, request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Update one strategy instance."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    try:
        body = await request.body()
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    active_path = CONFIG_DIR / "active.json"
    active = _load_json(active_path)
    instances = _get_strategy_instances(active)
    target_id = instance_id.lower()

    found = False
    updated_instances = []
    for item in instances:
        if item["id"] != target_id:
            updated_instances.append(item)
            continue
        merged = {**item, **data, "id": data.get("id", item["id"])}
        updated = _validate_strategy_instance(merged)
        updated_instances.append(updated)
        found = True

    if not found:
        raise HTTPException(status_code=404, detail=f"Strategy instance not found: {instance_id}")

    ids = [item["id"] for item in updated_instances]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="strategy instance ids must be unique")

    active["strategy_instances"] = updated_instances
    active = _sync_active_legacy_fields(active)

    try:
        _save_json(active_path, active)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "instances": updated_instances, "config": active}


@router.delete("/strategy-instances/{instance_id}")
async def delete_strategy_instance(instance_id: str, _user: dict = Depends(get_current_user)) -> dict:
    """Delete one strategy instance."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    active_path = CONFIG_DIR / "active.json"
    active = _load_json(active_path)
    instances = _get_strategy_instances(active)
    target_id = instance_id.lower()
    updated_instances = [item for item in instances if item["id"] != target_id]

    if len(updated_instances) == len(instances):
        raise HTTPException(status_code=404, detail=f"Strategy instance not found: {instance_id}")

    active["strategy_instances"] = updated_instances
    if updated_instances:
        active = _sync_active_legacy_fields(active)
    else:
        active["symbols"] = {"bingx": []}
        active["symbol_profiles"] = {}

    try:
        _save_json(active_path, active)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "instances": updated_instances, "config": active}


# ============================================================================
# Strategies Endpoints
# ============================================================================

@router.get("/strategies")
async def list_strategies(_user: dict = Depends(get_current_user)) -> dict:
    """List all available strategies with their presets."""
    strategies = {}
    use_new = _use_new_config_system()
    strategies_dir_exists = STRATEGIES_DIR.exists()

    logger.info(
        "list_strategies: use_new_config=%s, STRATEGIES_DIR=%s, exists=%s",
        use_new, STRATEGIES_DIR, strategies_dir_exists
    )

    if use_new and strategies_dir_exists:
        json_files = list(STRATEGIES_DIR.glob("*.json"))
        logger.info("list_strategies: found %d json files: %s", len(json_files), [f.name for f in json_files])
        for path in json_files:
            name = path.stem.upper()
            config = _load_json(path)
            strategies[name] = {
                "name": name,
                "description": config.get("_description", ""),
                "preset": config.get("preset", {}),
                "has_ai": name not in ["MACDX", "GRID"],
            }
    else:
        # Fallback to legacy STYLE_PRESETS
        legacy = reader.read_config()
        presets = legacy.get("STYLE_PRESETS", {})
        logger.info("list_strategies: fallback to legacy, found %d presets: %s", len(presets), list(presets.keys()))
        for name, preset in presets.items():
            strategies[name] = {
                "name": name,
                "description": preset.get("description", ""),
                "preset": preset,
                "has_ai": name not in ["MACDX", "GRID"],
            }

    if "MACDX" not in strategies:
        strategies["MACDX"] = {
            "name": "MACDX",
            "description": "No-AI MACD crossover strategy with 3-5 confirmations.",
            "preset": {},
            "has_ai": False,
        }

    available = sorted(list(strategies.keys()))
    logger.info("list_strategies: returning %d strategies: %s", len(strategies), available)
    return {"strategies": strategies, "available": available}


@router.get("/strategies/{name}")
async def get_strategy(name: str, _user: dict = Depends(get_current_user)) -> dict:
    """Get full strategy configuration."""
    name_upper = name.upper()

    if _use_new_config_system():
        path = STRATEGIES_DIR / f"{name.lower()}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Strategy not found: {name}")
        return _load_json(path)
    else:
        # Return from legacy config
        legacy = reader.read_config()
        settings_key = f"{name_upper}_SETTINGS"
        preset = legacy.get("STYLE_PRESETS", {}).get(name_upper, {})
        settings = legacy.get(settings_key, {})
        return {
            "preset": preset,
            **settings,
        }


@router.put("/strategies/{name}")
async def update_strategy(name: str, request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Update strategy configuration."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    try:
        body = await request.body()
        new_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    path = STRATEGIES_DIR / f"{name.lower()}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Strategy not found: {name}")

    current = _load_json(path)

    # Deep merge (preserve _description, _version)
    def deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for key, value in override.items():
            if key.startswith("_"):
                continue
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    current = deep_merge(current, new_data)

    try:
        _save_json(path, current)
        logger.info("Strategy %s updated", name)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "strategy": current}


# ============================================================================
# Profiles Endpoints
# ============================================================================

def _get_profile_schema() -> dict:
    """
    Get schema of valid profile parameters by reading all strategy files.
    Returns a dict with strategy names as keys and valid parameter sections/keys as values.
    """
    schema = {}

    if not STRATEGIES_DIR.exists():
        return schema

    # Read all strategy files and extract valid keys
    for strategy_file in STRATEGIES_DIR.glob("*.json"):
        strategy_name = strategy_file.stem.upper()
        try:
            strategy_config = _load_json(strategy_file)

            # Extract all valid keys from strategy config (these can be overridden in profile)
            valid_sections = {}
            for key, value in strategy_config.items():
                if key.startswith("_"):
                    continue  # Skip metadata keys
                if isinstance(value, dict):
                    # This is a section like "preset", "position", "signal_rules"
                    # All keys in this section are valid for profiles
                    valid_sections[key] = list(value.keys())
                elif key in ("preset", "position", "signal_rules"):
                    # These sections can also be in profiles
                    valid_sections[key] = []

            # Add special sections that can be in profiles
            valid_sections["preset"] = valid_sections.get("preset", [])
            valid_sections["position"] = valid_sections.get("position", [])
            valid_sections["signal_rules"] = valid_sections.get("signal_rules", [])

            # Add other common profile sections
            valid_sections["sl_tp"] = []
            valid_sections["breakeven"] = []
            valid_sections["time_exit"] = []
            valid_sections["risk_limits"] = []
            valid_sections["loops"] = []
            valid_sections["regime_overrides"] = []
            valid_sections["interaction_rules"] = []
            valid_sections["ai_integration"] = []
            valid_sections["ai_filter"] = []
            valid_sections["sessions"] = []
            valid_sections["multi_timeframe"] = []
            valid_sections["pre_filter"] = []
            valid_sections["grid_settings"] = []

            schema[strategy_name] = valid_sections
        except Exception as e:
            logger.warning(f"Failed to read strategy {strategy_file}: {e}")

    return schema


def _validate_profile_keys(profile_data: dict, strategy: str = None) -> tuple[bool, list[str]]:
    """
    Validate that profile only contains valid keys for the given strategy.
    Returns (is_valid, list_of_invalid_keys).
    """
    schema = _get_profile_schema()

    # Get strategy to validate against
    profile_strategy = profile_data.get("_strategy", strategy)
    if not profile_strategy:
        # Default profile - allow any keys
        return True, []

    profile_strategy = profile_strategy.upper()
    valid_sections = schema.get(profile_strategy, {})

    if not valid_sections:
        # Unknown strategy - be permissive but warn
        logger.warning(f"Unknown strategy for profile validation: {profile_strategy}")
        return True, []

    invalid_keys = []

    for section_name, section_data in profile_data.items():
        if section_name.startswith("_"):
            continue  # Metadata keys are always allowed

        if section_name not in valid_sections:
            # Unknown section
            invalid_keys.append(section_name)
            continue

        # Check if section is a dict with keys
        if isinstance(section_data, dict):
            valid_keys = valid_sections[section_name]
            if valid_keys:  # If we have a list of valid keys
                for key in section_data.keys():
                    if key not in valid_keys:
                        invalid_keys.append(f"{section_name}.{key}")

    return len(invalid_keys) == 0, invalid_keys


@router.get("/profiles/schema")
async def get_profile_schema(_user: dict = Depends(get_current_user)) -> dict:
    """
    Get schema of valid profile parameters for all strategies.
    This defines which keys are allowed in profile configuration.
    """
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    schema = _get_profile_schema()

    # Add default profile schema (works with any strategy)
    default_schema = {}
    for strategy_schema in schema.values():
        for section, keys in strategy_schema.items():
            if section not in default_schema:
                default_schema[section] = set()
            default_schema[section].update(keys)

    # Convert sets to lists for JSON
    default_schema = {k: list(v) for k, v in default_schema.items()}

    return {
        "schemas": schema,
        "default": default_schema,
    }

@router.get("/profiles")
async def list_profiles(_user: dict = Depends(get_current_user)) -> dict:
    """List all available profiles."""
    profiles = {}

    if _use_new_config_system() and PROFILES_DIR.exists():
        for path in PROFILES_DIR.glob("*.json"):
            name = path.stem
            config = _load_json(path)
            profiles[name] = config  # Return full JSON

    return {"profiles": profiles, "available": list(profiles.keys())}


@router.get("/profiles/{name}")
async def get_profile(name: str, _user: dict = Depends(get_current_user)) -> dict:
    """Get profile configuration."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile not found: {name}")

    return _load_json(path)


@router.put("/profiles/{name}")
async def update_profile(name: str, request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """
    Update or create profile configuration.
    Validates that only allowed keys are modified (based on strategy schema).
    """
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    try:
        body = await request.body()
        new_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate profile keys against schema
    is_valid, invalid_keys = _validate_profile_keys(new_data)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid profile keys: {', '.join(invalid_keys)}. These parameters are not allowed for this strategy."
        )

    # Ensure profiles directory exists
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    path = PROFILES_DIR / f"{name}.json"
    current = _load_json(path) if path.exists() else {
        "_description": f"Profile: {name}",
        "_version": "1.0.0",
        "_inherits": "default",
    }

    # Merge - only update values, don't add new keys to sections
    for key, value in new_data.items():
        if key.startswith("_") and key not in ("_description", "_inherits", "_strategy"):
            continue
        if key in current and isinstance(current[key], dict) and isinstance(value, dict):
            # For dict sections, merge but don't add new keys
            current[key] = {**current[key], **value}
        else:
            current[key] = value

    try:
        _save_json(path, current)
        logger.info("Profile %s updated", name)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "profile": current}


@router.delete("/profiles/{name}")
async def delete_profile(name: str, _user: dict = Depends(get_current_user)) -> dict:
    """Delete a profile (except 'default')."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    if name == "default":
        raise HTTPException(status_code=400, detail="Cannot delete default profile")

    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Profile not found: {name}")

    try:
        path.unlink()
        logger.info("Profile %s deleted", name)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete: {e}")

    return {"status": "ok"}


# ============================================================================
# Profile Clone, Auto-create, and Usage
# ============================================================================

@router.post("/profiles/{name}/clone")
async def clone_profile(
    name: str,
    request: Request,
    _user: dict = Depends(get_current_user)
) -> dict:
    """Clone a profile with a new name."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    # Get source profile
    source_path = PROFILES_DIR / f"{name}.json"
    if not source_path.exists():
        raise HTTPException(status_code=404, detail=f"Source profile not found: {name}")

    try:
        body = await request.body()
        data = json.loads(body)
        new_name = data.get("new_name")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not new_name:
        raise HTTPException(status_code=400, detail="new_name is required")

    if new_name == "default":
        raise HTTPException(status_code=400, detail="Cannot clone to 'default'")

    # Check if target already exists
    target_path = PROFILES_DIR / f"{new_name}.json"
    if target_path.exists():
        raise HTTPException(status_code=400, detail=f"Profile already exists: {new_name}")

    # Load and clone
    source = _load_json(source_path)
    source["_description"] = f"Cloned from '{name}'"

    try:
        _save_json(target_path, source)
        logger.info("Profile %s cloned to %s", name, new_name)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "profile": new_name, "source": name}


@router.post("/profiles/auto-create")
async def auto_create_profile(
    request: Request,
    _user: dict = Depends(get_current_user)
) -> dict:
    """
    Auto-create profile from strategy settings.

    When user modifies strategy settings via UI, we create a profile
    and optionally switch symbols from 'default' to this new profile.
    """
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    try:
        body = await request.body()
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    profile_name = data.get("name")  # Optional: user-specified name
    settings = data.get("settings", {})  # Profile settings to apply
    strategy = data.get("strategy")  # Required: strategy type
    switch_from_default = data.get("switch_from_default", True)

    if not strategy:
        raise HTTPException(status_code=400, detail="strategy is required")

    # Generate profile name if not provided
    if not profile_name:
        import time
        profile_name = f"auto-{strategy.lower()}-{int(time.time())}"

    # Check if profile already exists
    profile_path = PROFILES_DIR / f"{profile_name}.json"
    is_update = profile_path.exists()

    # Validate settings against strategy schema if strategy config exists
    strategy_path = STRATEGIES_DIR / f"{strategy.lower()}.json"
    if strategy_path.exists():
        strategy_config = _load_json(strategy_path)
        # Get allowed keys from strategy config
        allowed_keys = set(strategy_config.get("properties", {}).keys())
        if allowed_keys:
            # Validate that settings only contain valid keys
            invalid_keys = set(settings.keys()) - allowed_keys
            if invalid_keys:
                logger.warning("Profile %s has invalid keys for strategy %s: %s",
                               profile_name, strategy, list(invalid_keys))

    # Build profile config
    profile = {
        "_description": f"{'Updated' if is_update else 'Auto-created'} profile for {strategy}",
        "_version": "1.0.0",
        "_inherits": "default",
        "_strategy": strategy,
        **settings  # Apply user settings
    }

    try:
        _save_json(profile_path, profile)
        logger.info("Profile %s %s", profile_name, "updated" if is_update else "created")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save profile: {e}")

    # Switch symbols from default to new profile
    switched_symbols = []
    previously_using_default = False

    if switch_from_default:
        active_path = CONFIG_DIR / "active.json"
        active = _load_json(active_path)
        instances = _get_strategy_instances(active)

        symbol_profiles = active.get("symbol_profiles", {})
        symbols_to_switch = []
        instance_ids_to_switch = []

        # Find symbols using 'default'
        for symbol, prof in symbol_profiles.items():
            if prof == "default":
                symbols_to_switch.append(symbol)

        for instance in instances:
            if instance.get("profile", "default") == "default":
                instance_ids_to_switch.append(instance["id"])
                symbols_to_switch.append(instance["symbol"])

        # Also check active symbols without explicit profile (implicitly default)
        symbols_config = active.get("symbols", {})
        all_active_symbols = []
        for exchange_symbols in symbols_config.values():
            all_active_symbols.extend(exchange_symbols)

        for symbol in all_active_symbols:
            if symbol not in symbol_profiles:  # No explicit profile = default
                symbols_to_switch.append(symbol)
                previously_using_default = True

        if symbols_to_switch:
            switched_symbols = list(set(symbols_to_switch))
            for symbol in switched_symbols:
                symbol_profiles[symbol] = profile_name
            active["symbol_profiles"] = symbol_profiles

            if instance_ids_to_switch:
                for instance in instances:
                    if instance["id"] in instance_ids_to_switch:
                        instance["profile"] = profile_name
                active["strategy_instances"] = instances
                active = _sync_active_legacy_fields(active)

            try:
                _save_json(active_path, active)
                logger.info("Switched %d symbols to profile %s", len(switched_symbols), profile_name)
            except OSError as e:
                # Profile was created, but symbol switch failed
                logger.warning("Failed to switch symbols: %s", e)

    return {
        "status": "ok",
        "profile": profile_name,
        "switchedSymbols": switched_symbols,
        "previouslyUsingDefault": previously_using_default or len(switched_symbols) > 0,
        "isUpdate": is_update
    }


@router.get("/profiles/{name}/usage")
async def get_profile_usage(
    name: str,
    _user: dict = Depends(get_current_user)
) -> dict:
    """Get list of symbols using a specific profile."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    profile_path = PROFILES_DIR / f"{name}.json"
    if not profile_path.exists():
        raise HTTPException(status_code=404, detail=f"Profile not found: {name}")

    active = _load_json(CONFIG_DIR / "active.json")
    symbol_profiles = active.get("symbol_profiles", {})
    instances = _get_strategy_instances(active)

    # Find all symbols using this profile
    symbols = [s for s, p in symbol_profiles.items() if p == name]
    used_instances = [
        {
            "id": item["id"],
            "symbol": item["symbol"],
            "strategy": item["strategy"],
            "enabled": item.get("enabled", True),
        }
        for item in instances
        if item.get("profile", "default") == name
    ]
    symbols.extend(item["symbol"] for item in used_instances)

    # Also check implicit default
    if name == "default":
        symbols_config = active.get("symbols", {})
        all_active_symbols = []
        for exchange_symbols in symbols_config.values():
            all_active_symbols.extend(exchange_symbols)

        for symbol in all_active_symbols:
            if symbol not in symbol_profiles:
                symbols.append(symbol)
        symbols.extend(
            item["symbol"] for item in instances
            if item.get("profile", "default") == "default"
        )

    return {
        "profile": name,
        "symbols": sorted(set(symbols)),
        "instances": used_instances,
        "isUsed": len(symbols) > 0 or len(used_instances) > 0,
        "usageCount": len(set(symbols)) + len(used_instances)
    }


# ============================================================================
# Symbol Profile Mapping
# ============================================================================

@router.get("/symbol-profiles")
async def get_symbol_profiles(_user: dict = Depends(get_current_user)) -> dict:
    """Get symbol to profile mapping."""
    if not _use_new_config_system():
        return {"symbol_profiles": {}, "symbols": []}

    active = _load_json(CONFIG_DIR / "active.json")
    instances = _get_strategy_instances(active)
    symbols_config = active.get("symbols", {})
    all_symbols = [item["symbol"] for item in instances]
    for exchange_symbols in symbols_config.values():
        all_symbols.extend(exchange_symbols)

    instance_profiles = {
        item["id"]: item.get("profile", "default")
        for item in instances
    }

    return {
        "symbol_profiles": active.get("symbol_profiles", {}),
        "instance_profiles": instance_profiles,
        "strategy_instances": instances,
        "symbols": sorted(set(all_symbols)),
        "disabled_symbols": active.get("disabled_symbols", []),
    }


@router.put("/symbol-profiles/{symbol}")
async def set_symbol_profile(symbol: str, request: Request, _user: dict = Depends(get_current_user)) -> dict:
    """Set profile for a specific symbol."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    try:
        body = await request.body()
        data = json.loads(body)
        profile = data.get("profile", "default")
        instance_id = data.get("instance_id")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Verify profile exists
    if profile != "default":
        profile_path = PROFILES_DIR / f"{profile}.json"
        if not profile_path.exists():
            raise HTTPException(status_code=400, detail=f"Profile not found: {profile}")

    active_path = CONFIG_DIR / "active.json"
    active = _load_json(active_path)
    instances = _get_strategy_instances(active)

    if instance_id:
        target_id = str(instance_id).lower()
        updated = False
        for instance in instances:
            if instance["id"] == target_id:
                instance["profile"] = profile
                updated = True
                break
        if not updated:
            raise HTTPException(status_code=404, detail=f"Strategy instance not found: {instance_id}")
        active["strategy_instances"] = instances
        active = _sync_active_legacy_fields(active)
    else:
        symbol_key = _normalize_symbol_key(symbol)
        for instance in instances:
            if _normalize_symbol_key(instance["symbol"]) == symbol_key:
                instance["profile"] = profile
        if instances:
            active["strategy_instances"] = instances
            active = _sync_active_legacy_fields(active)

    symbol_profiles = active.get("symbol_profiles", {})
    symbol_profiles[_normalize_symbol_key(symbol)] = profile
    active["symbol_profiles"] = symbol_profiles

    try:
        _save_json(active_path, active)
        logger.info("Symbol %s profile set to %s", symbol, profile)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "symbol": symbol.upper(), "profile": profile}
