"""
Configuration API routes with support for new structured config system.

Provides endpoints for:
- Legacy config (bot_config.json) read/write
- New config system (config/) with strategies and profiles
- Strategy management
- Profile management per symbol
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..services.auth import get_current_user
from ..services.data_reader import DataReader
from ..config import CONFIG_PATH

logger = logging.getLogger("panel.config")

router = APIRouter(prefix="/api/config", tags=["config"])
reader = DataReader()

# Paths for new config system
# Derive from CONFIG_PATH to work correctly in container environments
# where bot_config.json and config/ are both mounted to /app/
CONFIG_DIR = CONFIG_PATH.parent / "config"
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
    "symbols",
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
# Legacy Config Endpoints (bot_config.json)
# ============================================================================

@router.get("")
async def get_config(_user: dict = Depends(get_current_user)) -> dict:
    return reader.read_config()


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
    current_config = reader.read_config()
    changes = classify_changes(current_config, new_config)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "changes": changes,
    }


@router.put("")
async def update_config(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    try:
        body = await request.body()
        new_config = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(new_config, dict):
        raise HTTPException(status_code=400, detail="Config must be a JSON object")

    # Validate before saving
    errors = validate_config_values(new_config)
    if errors:
        logger.warning("Config validation failed: %s", errors)
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation failed", "validation_errors": errors},
        )

    current_config = reader.read_config()
    changes = classify_changes(current_config, new_config)

    try:
        reader.write_config(new_config)
        logger.info("Config saved successfully. Changes: %s", changes)
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
        return {
            "strategy": legacy.get("STRATEGY_STYLE", "HYBRID"),
            "symbols": legacy.get("EXCHANGE_SYMBOLS", {}),
            "symbol_profiles": {},
            "disabled_symbols": legacy.get("DISABLED_SYMBOLS", []),
        }

    return _load_json(CONFIG_DIR / "active.json")


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

    # Merge with current
    current.update(new_data)

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

    logger.info("list_strategies: returning %d strategies: %s", len(strategies), list(strategies.keys()))
    return {"strategies": strategies, "available": list(strategies.keys())}


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

@router.get("/profiles")
async def list_profiles(_user: dict = Depends(get_current_user)) -> dict:
    """List all available profiles."""
    profiles = {}

    if _use_new_config_system() and PROFILES_DIR.exists():
        for path in PROFILES_DIR.glob("*.json"):
            name = path.stem
            config = _load_json(path)
            profiles[name] = {
                "name": name,
                "description": config.get("_description", ""),
                "inherits": config.get("_inherits"),
                "preset": config.get("preset", {}),
                "position": config.get("position", {}),
                "signal_rules": config.get("signal_rules", {}),
            }

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
    """Update or create profile configuration."""
    if not _use_new_config_system():
        raise HTTPException(status_code=400, detail="New config system not available")

    try:
        body = await request.body()
        new_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Ensure profiles directory exists
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    path = PROFILES_DIR / f"{name}.json"
    current = _load_json(path) if path.exists() else {
        "_description": f"Profile: {name}",
        "_version": "1.0.0",
        "_inherits": "default",
    }

    # Merge
    for key, value in new_data.items():
        if key.startswith("_") and key not in ("_description", "_inherits"):
            continue
        if key in current and isinstance(current[key], dict) and isinstance(value, dict):
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
# Symbol Profile Mapping
# ============================================================================

@router.get("/symbol-profiles")
async def get_symbol_profiles(_user: dict = Depends(get_current_user)) -> dict:
    """Get symbol to profile mapping."""
    if not _use_new_config_system():
        return {"symbol_profiles": {}, "symbols": []}

    active = _load_json(CONFIG_DIR / "active.json")
    symbols_config = active.get("symbols", {})
    all_symbols = []
    for exchange_symbols in symbols_config.values():
        all_symbols.extend(exchange_symbols)

    return {
        "symbol_profiles": active.get("symbol_profiles", {}),
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
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Verify profile exists
    if profile != "default":
        profile_path = PROFILES_DIR / f"{profile}.json"
        if not profile_path.exists():
            raise HTTPException(status_code=400, detail=f"Profile not found: {profile}")

    active_path = CONFIG_DIR / "active.json"
    active = _load_json(active_path)

    symbol_profiles = active.get("symbol_profiles", {})
    symbol_profiles[symbol.upper()] = profile
    active["symbol_profiles"] = symbol_profiles

    try:
        _save_json(active_path, active)
        logger.info("Symbol %s profile set to %s", symbol, profile)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")

    return {"status": "ok", "symbol": symbol.upper(), "profile": profile}
