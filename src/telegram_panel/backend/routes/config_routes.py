import json

from fastapi import APIRouter, Depends, HTTPException, Request

from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/config", tags=["config"])
reader = DataReader()

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
}

# Settings that require process restart
RESTART_REQUIRED_KEYS = {
    "EXCHANGE_SYMBOLS", "CHART_RANGES", "ENABLE_PARALLEL_MODE", "GRID_SETTINGS",
}

# Validation rules: (key, type, min, max)
VALIDATION_RULES = {
    "POSITION_SIZE_PERCENT": {"type": (int, float), "min": 0.1, "max": 100},
    "MIN_CONFIDENCE_THRESHOLD": {"type": (int, float), "min": 0.0, "max": 1.0},
    "MIN_RISK_REWARD_RATIO": {"type": (int, float), "min": 0.1, "max": 100},
    "MIN_TRADE_AMOUNT_USDT": {"type": (int, float), "min": 1, "max": 100000},
    "TAKE_PROFIT_PERCENT": {"type": (int, float), "min": 0.1, "max": 100},
    "STOP_LOSS_PERCENT": {"type": (int, float), "min": 0.1, "max": 100},
    "STRATEGY_STYLE": {"type": str, "values": ["SCALP", "INTRADAY", "SWING", "GRID", "HYBRID"]},
}


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


@router.get("")
async def get_config(_user: dict = Depends(get_current_user)) -> dict:
    return reader.read_config()


@router.get("/meta")
async def get_config_meta(_user: dict = Depends(get_current_user)) -> dict:
    """Return metadata about config fields (hot-reloadable vs restart-required)."""
    return {
        "hot_reloadable": sorted(HOT_RELOADABLE_KEYS),
        "restart_required": sorted(RESTART_REQUIRED_KEYS),
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
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    current_config = reader.read_config()
    changes = classify_changes(current_config, new_config)

    try:
        reader.write_config(new_config)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write config: {e}")

    return {
        "status": "ok",
        "changes": changes,
        "needs_restart": len(changes["restart_required"]) > 0,
    }
