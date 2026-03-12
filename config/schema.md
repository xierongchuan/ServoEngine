# Configuration Schema Documentation

## Overview

The configuration system is organized into the following categories:

### Directory Structure
```
config/
  base.json           # Global defaults and rarely-changed settings
  trading.json        # Core trading parameters (changed occasionally)
  strategies/         # Strategy-specific settings
    scalp.json
    aiscalp.json
    swing.json
    grid.json
    hybrid.json
    macdx.json
  profiles/           # Per-symbol profiles (overrides)
    default.json      # Default profile (base for all)
    btc_aggressive.json
    eth_conservative.json
  active.json         # Runtime selection (strategy + profile mapping)
```

## Configuration Categories

### 1. Base Configuration (`config/base.json`)
Rarely changed, infrastructure-level settings:
- Exchange configuration (fees, API settings)
- Chart rendering settings
- Data ranges (CHART_RANGES, PLOTTER_RANGES)
- Technical analysis indicator parameters
- AI provider settings
- Error handling
- Position limits
- News settings

### 2. Trading Configuration (`config/trading.json`)
Moderately changed, trading behavior:
- POSITION_SIZE_PERCENT (default)
- MIN_TRADE_AMOUNT_USDT
- MIN_CONFIDENCE_THRESHOLD
- MIN_RISK_REWARD_RATIO
- Feature flags (ENABLE_NEWS, AGGRESSIVE_MODE, etc.)
- SMART_SAMPLING
- DYNAMIC_SIZING
- PERFORMANCE_TRACKING
- REGIME_SETTINGS

### 3. Strategy Configurations (`config/strategies/*.json`)
Frequently adjusted per-strategy:
Each file contains the complete configuration for one strategy:
- Style preset (timeframe, loop_interval, leverage, atr_mult, etc.)
- Signal rules (weights, thresholds, RSI zones)
- AI filter settings
- Interaction rules
- Exit rules
- Risk limits (for SCALP)

### 4. Profile Configurations (`config/profiles/*.json`)
Per-symbol override system:
- Inherits from strategy defaults
- Can override any parameter
- Supports symbol-specific tuning
- Minimal files (only overrides, not full copy)

### 5. Active Configuration (`config/active.json`)
Runtime selection:
```json
{
  "strategy": "MACDX",
  "symbols": {
    "BTCUSDT": "default",
    "ETHUSDT": "eth_conservative",
    "SOLUSDT": "btc_aggressive"
  },
  "disabled_symbols": []
}
```

## Profile Inheritance System

Profiles use a layered inheritance model:
1. `config/base.json` - Infrastructure defaults
2. `config/trading.json` - Trading defaults
3. `config/strategies/{strategy}.json` - Strategy-specific defaults
4. `config/profiles/{profile}.json` - Symbol-specific overrides

When resolving a configuration for a symbol:
```
effective_config = deep_merge(
    base.json,
    trading.json,
    strategies/{active_strategy}.json,
    profiles/{symbol_profile}.json
)
```

## Validation

The config loader validates:
- Required fields are present
- Types match schema
- Numeric ranges are valid
- No conflicting parameters at the same level
- Profile references exist

## Hot-Reload

The system supports hot-reload for:
- `config/active.json` - Strategy and profile changes
- `config/trading.json` - Trading parameter adjustments
- Profile files - Per-symbol tuning

Changes to `config/base.json` and strategy files require restart.
