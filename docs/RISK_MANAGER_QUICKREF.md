# Risk Manager Quick Reference

## Functions

### calculate_dynamic_sl_tp(signal, current_price, atr, support, resistance, regime, quality)

**Purpose:** Calculate dynamic SL/TP based on ATR with S/R validation

**Returns:**
```python
{
    "stop_loss": float,      # SL price
    "take_profit": float,    # TP price
    "risk_reward": float,    # R/R ratio
    "risk_pct": float,       # Risk %
    "reward_pct": float      # Reward %
}
```

**Example:**
```python
sl_tp = calculate_dynamic_sl_tp(
    signal="BUY",
    current_price=50000.0,
    atr=500.0,
    support=49200.0,
    resistance=51500.0,
    regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
    quality=0.75
)
# → {"stop_loss": 49350.0, "take_profit": 51950.0, "risk_reward": 2.47, ...}
```

---

### calculate_position_size(base_pct, quality, regime, recent_performance)

**Purpose:** Calculate adaptive position size with quality/regime/performance adjustments

**Returns:** `float` - Position size percentage (bounded by config min/max)

**Example:**
```python
size = calculate_position_size(
    base_pct=10.0,
    quality=0.8,
    regime={"position_size_factor": 1.2},
    recent_performance={"win_rate": 0.65, "total_trades": 12}
)
# → 13.5 (increased due to high quality + hot streak)
```

---

### validate_risk_parameters(sl_tp_result, min_rr_ratio)

**Purpose:** Validate SL/TP before execution

**Returns:** `bool` - True if valid, False if rejected

**Example:**
```python
if not validate_risk_parameters(sl_tp_result, min_rr_ratio=1.2):
    logger.warning("Trade rejected: R/R validation failed")
    return
```

---

## Typical Workflow

```python
from src.core.risk_manager import (
    calculate_dynamic_sl_tp,
    calculate_position_size,
    validate_risk_parameters
)

# 1. Calculate SL/TP
sl_tp = calculate_dynamic_sl_tp(
    signal=signal,
    current_price=ctx["current_price"],
    atr=ctx["atr"],
    support=ctx["support"],
    resistance=ctx["resistance"],
    regime=regime,
    quality=signal_quality
)

# 2. Validate
if not validate_risk_parameters(sl_tp):
    return  # Reject trade

# 3. Calculate position size
position_size_pct = calculate_position_size(
    base_pct=BOT_CONFIG["POSITION_SIZE_PERCENT"],
    quality=signal_quality,
    regime=regime,
    recent_performance={"win_rate": win_rate, "total_trades": trades_count}
)

# 4. Execute
place_order_with_sl_tp(
    ...,
    stop_loss=sl_tp["stop_loss"],
    take_profit=sl_tp["take_profit"],
    position_size=position_size_pct
)
```

---

## Regime Parameters Reference

| Regime | SL Mult | TP Mult | Size | Use Case |
|--------|---------|---------|------|----------|
| TRENDING | 1.5 | 3.5 | 1.2x | Strong directional move |
| RANGING | 1.0 | 1.5 | 0.8x | Sideways, mean reversion |
| VOLATILE | 2.5 | 2.5 | 0.6x | High uncertainty, wide swings |
| TRANSITIONAL | 2.0 | 2.5 | 0.5x | Choppy, low confidence |

---

## Quality Impact

| Quality | SL Adj | TP Adj | Size Factor | Description |
|---------|--------|--------|-------------|-------------|
| 1.0 | 0.8x | 1.3x | 1.2x | Perfect signal |
| 0.75 | 0.85x | 1.225x | 1.025x | Strong signal |
| 0.5 | 0.9x | 1.15x | 0.85x | Medium signal |
| 0.25 | 0.95x | 1.075x | 0.675x | Weak signal |
| 0.0 | 1.0x | 1.0x | 0.5x | Minimal confidence |

**Formula:**
- `quality_sl_adj = 1.0 - (quality * 0.2)` → Tighter SL with higher quality
- `quality_tp_adj = 1.0 + (quality * 0.3)` → Wider TP with higher quality
- `quality_factor = 0.5 + (quality * 0.7)` → Larger size with higher quality

---

## Performance Streaks

| Win Rate | Trades | Multiplier | Effect |
|----------|--------|------------|--------|
| < 30% | ≥ 5 | 0.5x | Cold streak - reduce size |
| 30-60% | ≥ 5 | 1.0x | Neutral - normal size |
| > 60% | ≥ 5 | 1.1x | Hot streak - increase size |
| Any | < 5 | 1.0x | Insufficient data - ignore |

---

## Configuration (bot_config.json)

```json
{
  "DYNAMIC_SIZING": {
    "enabled": true,
    "min_size_pct": 3.0,
    "max_size_pct": 20.0
  },
  "MIN_RISK_REWARD_RATIO": 1.2,
  "REGIME_SETTINGS": {
    "regime_params": {
      "TRENDING": {
        "sl_multiplier": 1.5,
        "tp_multiplier": 3.5,
        "position_size_factor": 1.2
      },
      ...
    }
  }
}
```

---

## Common Use Cases

### HYBRID Mode (5m)
```python
# After signal_generator produces signal with score
signal_quality = signal_score / 11.0  # Max score = 11

sl_tp = calculate_dynamic_sl_tp(
    signal=signal_action,
    current_price=price,
    atr=atr,
    support=support,
    resistance=resistance,
    regime=detected_regime,
    quality=signal_quality
)
```

### SWING Mode (1h)
```python
# Wide stops, long-term position
regime = {
    "sl_multiplier": 3.0,
    "tp_multiplier": 6.0,
    "position_size_factor": 1.0
}

sl_tp = calculate_dynamic_sl_tp(
    signal="BUY",
    current_price=price,
    atr=atr_1h,
    support=weekly_support,
    resistance=weekly_resistance,
    regime=regime,
    quality=0.8  # High confidence for SWING
)
```

### SCALP Mode (1m)
```python
# Tight stops, fast execution
regime = {
    "sl_multiplier": 1.5,
    "tp_multiplier": 2.0,
    "position_size_factor": 1.0
}

sl_tp = calculate_dynamic_sl_tp(
    signal="BUY",
    current_price=price,
    atr=atr_1m,
    support=recent_support,
    resistance=recent_resistance,
    regime=regime,
    quality=0.6
)
```

---

## Validation Rules

| Check | Threshold | Rejection Reason |
|-------|-----------|------------------|
| R/R Ratio | < 1.2 | Poor risk/reward |
| Risk % | > 10% | Excessive risk |
| SL Value | == 0 | Invalid stop loss |
| TP Value | == 0 | Invalid take profit |

---

## File Locations

- **Module:** `/home/temur/Projects/OpenProducerBot/src/core/risk_manager.py`
- **Tests:** `/home/temur/Projects/OpenProducerBot/tests/test_risk_manager.py`
- **Examples:** `/home/temur/Projects/OpenProducerBot/examples/risk_manager_usage.py`
- **Validation:** `/home/temur/Projects/OpenProducerBot/validate_risk_manager.py`
- **Docs:** `/home/temur/Projects/OpenProducerBot/docs/RISK_MANAGER.md`

---

## Testing

```bash
# Run pytest suite
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c \
  "pip install -q requests pandas matplotlib pytest && \
   python -m pytest tests/test_risk_manager.py -v"

# Run standalone validation
python3 validate_risk_manager.py

# Run usage examples
python3 examples/risk_manager_usage.py
```

---

## Integration Points

### 1. After analyzer.py
Extract `current_price`, `atr`, `support`, `resistance` from analyzer context

### 2. After signal_generator.py (HYBRID)
Use `signal_score / max_score` as quality input

### 3. Before executor.py
Pass `sl_tp["stop_loss"]` and `sl_tp["take_profit"]` to order placement

### 4. With trade_tracker.py
Load recent trades to calculate `win_rate` for performance adjustment

---

## Performance Characteristics

- **CPU:** ~0.1ms per calculation (negligible overhead)
- **Memory:** Stateless functions, zero memory footprint
- **Latency:** Synchronous, no API calls
- **Thread Safety:** Pure functions, thread-safe

---

## Error Handling

```python
try:
    sl_tp = calculate_dynamic_sl_tp(...)
except ValueError as e:
    logger.error(f"Invalid signal direction: {e}")
    return

if not validate_risk_parameters(sl_tp):
    logger.warning("Risk validation failed")
    return
```

---

## Debugging

Enable verbose logging to see calculation details:

```python
# Logs automatically print intermediate steps
# Example output:
# 🎯 Расчет SL/TP | Signal: BUY, Price: 50000.0000, ATR: 500.0000
#    Режим: SL_mult=2.0, TP_mult=3.0 | Quality: 0.75 (SL_adj=0.85, TP_adj=1.23)
#    SL скорректирован по поддержке: 49250.0000 → 49350.0000 (support=49500.0000)
# ✅ Результат: SL=49350.0000 (-1.30%), TP=51838.0000 (+3.68%), R/R=2.84
```

---

## License

Same as OpenProducerBot main project.
