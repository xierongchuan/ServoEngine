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
  profiles/           # Per-instance profiles (overrides)
    _templates/       # Profile templates (optional)
    default.json      # Default profile (works with any strategy)
    btc_aggressive.json
    eth_conservative.json
  active.json         # Runtime strategy instances + legacy fallback
```

## Configuration Resolution Order

`config/active.json` selects the enabled runtime instances. For each enabled `StrategyInstance`, configuration is resolved in the following order (later overrides earlier):
```
instance = active.strategy_instances[]

effective_config = deep_merge(
    base.json,           # 1. Infrastructure defaults
    trading.json,        # 2. Trading defaults
    strategies/{instance.strategy}.json,  # 3. Strategy-specific settings
    profiles/{instance.profile}.json      # 4. Instance-specific overrides
)
```

The legacy `strategy` + `symbols` format is converted to strategy instances internally and remains supported as a fallback.

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
Moderately changed, controls trading behavior:
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
- **Style preset** (timeframe, loop_interval, leverage, atr_mult, etc.)
- **Signal rules** (weights, thresholds, RSI zones)
- **AI filter settings**
- **Interaction rules**
- **Exit rules**
- **Risk limits** (for SCALP)

#### Available Strategies
| Strategy | Description | Timeframe | AI | Key Parameters |
|----------|-------------|-----------|-----|----------------|
| SCALP | Dual-loop scalp with trailing stops | 1m | Optional | min_score_for_signal, tier1_required, weights |
| AISCALP | Multi-timeframe AI decisions | 1m | Yes | sessions, multi_timeframe, ai_filter |
| HYBRID | Deterministic + AI confirmation | 5m | Yes | ai_filter, min_confidence_to_approve |
| MACDX | MACD crossover, no AI | 15m | No | macd_cross_weight, rsi_zone_weight |
| SWING | Multi-day holding | 1h | Optional | min_hold_hours, cooldown_after_close |
| GRID | Grid trading | 1m | Optional | grid_levels, grid_spacing_pct |

### 4. Profile Configurations (`config/profiles/*.json`)
Per-instance override system with strategy binding:

#### Profile Fields
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `_description` | string | No | Human-readable description |
| `_version` | string | No | Schema version (e.g. "1.0.0") |
| `_inherits` | string | No | Parent profile name to inherit from |
| `_strategy` | string | **Yes** for non-default | Strategy this profile belongs to (SCALP, MACDX, etc.) |

#### Profile Structure Example
```json
{
  "_description": "Aggressive profile for BTC",
  "_version": "1.0.0",
  "_inherits": "default",
  "_strategy": "SCALP",

  "preset": {
    "leverage": 15,
    "atr_sl_mult": 1.0,
    "atr_tp_mult": 2.5
  },

  "position": {
    "size_percent": 15
  },

  "signal_rules": {
    "min_score_for_signal": 3
  }
}
```

#### Special Profiles
- **`default`** - Universal profile with `_strategy: null`. Works with any strategy. Use when you don't need instance-specific overrides.
- **`macdx_5m` / `macdx_15m` / `macdx_30m` / `macdx_1h` / `macdx_1d`** - MACDX profiles with timeframe-specific history, polling, volatility, cooldown and ATR risk settings.
- **`_templates/`** - Future: reusable profile templates for common configurations (aggressive, conservative, balanced)

### 5. Active Configuration (`config/active.json`)
Runtime selection.

Preferred format for multi-strategy runtime:
```json
{
  "strategy_instances": [
    {
      "id": "btc_macdx",
      "symbol": "BTCUSDT",
      "strategy": "MACDX",
      "profile": "default",
      "enabled": true
    },
    {
      "id": "btc_hybrid",
      "symbol": "BTCUSDT",
      "strategy": "HYBRID",
      "profile": "default",
      "enabled": true
    },
    {
      "id": "eth_aiscalp",
      "symbol": "ETHUSDT",
      "strategy": "AISCALP",
      "profile": "default",
      "enabled": true
    }
  ],
  "disabled_symbols": []
}
```

Runtime rule: if multiple strategy instances trade the same symbol, only the instance that opened the current position owns it. Other instances for that symbol wait until the owner closes the position. Ownership is stored in `data/position_owners.json` and synchronized with real exchange positions.

`id` must be stable and unique. It is used in logs, Telegram Panel, position ownership, and runtime process names.

Legacy fallback format without `strategy_instances` is still read for migration.
New saves convert it to `strategy_instances`; `symbol_profiles` is not written anymore
because one symbol can have several strategy-specific profiles:
```json
{
  "strategy": "MACDX",
  "symbols": {
    "bingx": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
  },
  "symbol_profiles": {
    "BTCUSDT": "default",
    "ETHUSDT": "eth_conservative",
    "SOLUSDT": "default"
  },
  "disabled_symbols": []
}
```

## Profile-Strategy Binding

### Why Profiles Are Bound to Strategies

Different strategies use **incompatible parameter systems**:

| Strategy | Parameter System | Example |
|----------|------------------|--------|
| SCALP | Weighted scores (0-10+) | `min_score_for_signal: 4` = score threshold |
| HYBRID | AI confidence (0-1) | `min_score_for_signal: 4` = 40% confidence! |
| MACDX | Confirmation count | `min_score_for_signal: 4` = 4 confirmations |

Setting `min_score_for_signal: 3` means different things for different strategies!

### How It Works

1. Each profile has a `_strategy` field specifying which strategy it belongs to
2. When loading instance config, the system validates profile-strategy compatibility
3. If incompatible, an error is raised at startup:
   ```
   ❌ Profile 'btc_aggressive' belongs to strategy 'SCALP',
   but symbol is using strategy 'MACDX'.
   Use a profile compatible with MACDX or change strategy.
   ```

### Creating New Profiles

Always specify `_strategy` for strategy-specific profiles:

```json
{
  "_description": "Conservative profile for high-volatility pairs",
  "_version": "1.0.0",
  "_inherits": "default",
  "_strategy": "SCALP",

  "preset": {
    "leverage": 5,
    "atr_sl_mult": 2.0
  },
  "signal_rules": {
    "min_score_for_signal": 6
  }
}
```

## Profile Inheritance System

Profiles use a layered inheritance model:
1. `config/base.json` - Infrastructure defaults
2. `config/trading.json` - Trading defaults
3. `config/strategies/{strategy}.json` - Strategy-specific defaults
4. `config/profiles/{profile}.json` - Instance-specific overrides

### Inheritance Example
```json
// config/profiles/eth_conservative.json
{
  "_inherits": "default",
  "_strategy": "MACDX",
  "preset": {
    "leverage": 5
  }
}
```
This profile inherits from `default.json` and adds MACDX-specific overrides.

## Validation

The config loader validates:
- Required fields are present
- Types match schema
- Numeric ranges are valid
- No conflicting parameters at the same level
- Profile references exist
- **Profile matches the instance strategy**

## Hot-Reload

The system supports hot-reload for:
- `config/active.json` - Strategy and profile changes
- `config/trading.json` - Trading parameter adjustments

Changing `strategy_instances`, assigning profiles, enabling/disabling instances, and editing `disabled_symbols` is done through `config/active.json` and is checked by workers every 30 seconds.

Changes to `config/base.json` and strategy files require restart.
# Выбор биржи и рынка

Биржа и рынок выбираются через `.env`: `EXCHANGE=bingx|mexc` и `MARKET_TYPE=perpetual|spot`. Старый список `symbols.bingx` остаётся допустимым. Для нескольких продуктов используется вложенная схема `symbols.<exchange>.<market_type>`. Одновременное смешивание рынков в одном runtime не поддерживается; после смены нужен restart.

Комиссии в `base.json` поддерживают ту же вложенность `exchange.fees.<exchange>.<market_type>`, а старый плоский BingX-формат сохраняется.
