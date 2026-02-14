# SCALP Strategy Blueprint

> Compiled from brainstorm by 4 agents: Quant Analyst, Prompt Engineer, Code Architect, Strategy Researcher.

---

## 1. Executive Summary

Redesign SCALP mode from a generic AI-dependent loop into a dedicated **dual-loop scalping engine** with deterministic signal generation, trailing stops, session management, and optional async AI overlay.

**Key architectural change:** Replace the single `process_worker.py` loop with `ScalpEngine` — a dual-loop system where a 1-2s fast loop handles position management and signal detection, while a 30-60s slow loop handles full analysis, regime detection, and optional AI veto. AI **never blocks** the fast loop.

**Critical fee insight:** BingX actual fees are **0.02% maker / 0.05% taker** (not 0.05%/0.05%). Using limit orders for entries reduces round-trip cost from 0.10% to 0.07% (or 0.04% with maker/maker). This is the single biggest edge for automated scalping.

---

## 2. Quantitative Model

### 2.1 Indicator Stack (tuned for 1m)

| Indicator | Period | Role | Weight | Notes |
|-----------|--------|------|--------|-------|
| EMA fast/medium | 5/13 | Trend direction | 2 | Replaces 9/21 for faster reactivity |
| EMA slow | 21 | Macro filter | — | Friction penalty if price against EMA21 |
| RSI | 7 | Momentum/extremes | 2 | Zones: long 25-40, short 60-75, exit 75/25 |
| Order Book Imbalance | top 10 levels | Microstructure | 2 | Bid/Ask ratio > 1.5 = buy signal |
| VWAP | session (00:00 UTC reset) | Mean-reversion anchor | 1 | Rolling cumulative from 1m candles |
| Volume ratio | 10-bar avg | Confirmation | 1 | Current / avg, > 1.3 = volume surge |
| MACD | 6,13,5 | Momentum confirm | 1 | Halved params, histogram direction only |
| Bollinger Bands | 20,2.0 | Extremes/squeeze | 1 | Touch/breach for mean reversion |
| ATR | 10 (primary), 5 (spike) | Volatility | — | SL/TP sizing + spike detection |

**Removed from SCALP:** S/R levels (too noisy on 1m, replaced by VWAP), SEB, news.

### 2.2 Signal Scoring System

```
SCALP SIGNAL SCORING
====================
Tier 1: DIRECTION (at least 1 required)
  EMA(5,13) alignment:       +2
  3-candle momentum:          +1

Tier 2: CONFIRMATION (at least 1 required)
  RSI(7) zone:                +2
  VWAP position:              +1

Tier 3: SUPPORT (optional)
  Volume surge (>1.3x):      +1
  Order book imbalance:       +1
  MACD histogram:             +1
  BB touch/breach:            +1

Max base score: 10
Interaction bonuses: up to +3
Total possible: 13
```

**Interaction bonuses:**
- Momentum Burst (+2): EMA aligned + Volume > 1.5x + 3 consecutive candles
- VWAP Bounce (+1): Price at VWAP + RSI in zone + EMA confirms
- Order Book Confluence (+1): OB imbalance + EMA + Volume

**Penalties:**
- Counter-momentum (-2): EMA says BUY but RSI > 70 (or inverse)
- ATR Spike (-1): ATR(5) > 2.0 × ATR(10) — entering during spike

### 2.3 Regime-Adaptive Weights

| Regime | EMA wt | RSI wt | BB wt | Vol wt | Min Score | Size Factor |
|--------|--------|--------|-------|--------|-----------|-------------|
| TRENDING | 3 | 1 | 0 | 1 | 3 | 1.2 |
| RANGING | 1 | 3 | 2 | 1 | 6 | 0.6 |
| VOLATILE | 2 | 2 | 1 | 2 | 5 | 0.5 |
| TRANSITIONAL | 2 | 2 | 1 | 1 | 7 | 0.4 |

### 2.4 Three Entry Patterns

**Pattern 1: Momentum Breakout**
- EMA(5) > EMA(13) > EMA(21) (stacked)
- Price breaks 5-candle high/low
- Volume > 1.3x average
- RSI 45-65 (room to run)
- Quality: High | Best in: TRENDING

**Pattern 2: Mean Reversion**
- Price at BB lower/upper band
- RSI < 30 (LONG) or > 70 (SHORT)
- Regime = RANGING (critical filter!)
- Volume NOT spiking (< 1.5x)
- OB imbalance confirms (> 0.2)
- Quality: Medium | Best in: RANGING

**Pattern 3: Pullback Entry**
- EMA(5) > EMA(13) > EMA(21) (trend intact)
- Price pulls back to EMA(13) ± 0.3 × ATR
- RSI bounced from 40-55 zone
- MACD histogram still positive
- Declining volume on pullback
- Quality: Highest | Best in: TRENDING

---

## 3. SL/TP & Risk Management

### 3.1 Fee Impact (corrected)

| Execution | RT Cost | Min Profitable Move | At 15x Leverage |
|-----------|---------|---------------------|-----------------|
| Taker/Taker | 0.10% | 0.10% | 1.5% ROE |
| Taker/Maker | 0.07% | 0.07% | 1.05% ROE |
| Maker/Maker | 0.04% | 0.04% | 0.6% ROE |

**Recommendation:** Use limit orders for entries (maker 0.02%) whenever possible.

### 3.2 SL/TP Strategy: Asymmetric with Trailing Stop

```
Initial SL: ATR(10) × 1.0
Initial TP: ATR(10) × 3.0
Trailing activation: at 1.5 × ATR profit
Trailing distance: 0.5 × ATR behind price
Breakeven move: at 0.3% profit → SL to entry + 0.05% fee buffer
```

**Expected outcomes (BTC at ATR ~$45):**
- 30% trades → SL hit → -$45 - fees
- 40% trades → trailing stop at ~2x ATR → +$90 - fees
- 20% trades → TP at 3x ATR → +$135 - fees
- 10% trades → runner via trailing → +$200+ - fees
- **EV per trade: ~+9% ROE on margin**

### 3.3 Risk Limits

| Parameter | Value | Action |
|-----------|-------|--------|
| Base position size | 5% of balance | (vs 10% INTRADAY) |
| Max consecutive losses | 5 | Pause 30 minutes |
| Daily loss limit | 3% | Stop trading for the day |
| Hourly loss limit | 1% | Pause 15 minutes |
| Max hold time | 15 minutes | Force close |
| Breakeven timeout | 8 minutes | SL → entry if in profit |
| Max trades/hour | 6 | Prevent overtrading |
| Max trades/day | 50 | |
| Min cooldown between trades | 2 minutes | |
| Max concurrent positions | 1 per symbol | |

### 3.4 Deterministic Exit Rules

Close immediately if ANY is true:
1. **SL/TP hit** (exchange order)
2. **Time exit:** > 15 minutes open
3. **Momentum reversal:** EMA(5) crosses against + RSI confirms
4. **Volume capitulation:** At loss + volume > 2x average
5. **RSI extreme:** > 80 when LONG, < 20 when SHORT (take profit)
6. **Trailing stop hit**
7. **Breakeven tightened:** After 8min in profit, SL → entry + fees
8. **Session limit hit:** Close ALL, stop trading

---

## 4. AI Integration: Layered Hybrid

### Architecture

| Layer | Role | Frequency | Blocking? | Cost/day |
|-------|------|-----------|-----------|----------|
| L1: Deterministic Engine | Signal + execution | Every 1-2s | No | $0 |
| L2: AI Regime Advisor | Market classification + params | Every 5-10 min | No (async) | $0.13 |
| L3: AI Scalp Veto | Risk veto on borderline signals | On-demand | No (async) | $0.04 |
| **Total** | | | | **$0.17/day** |

**vs current HYBRID mode: $1.20/day (7x more expensive)**

### L2: Regime Advisor Prompt (~300 tokens)

Runs every 5-10 minutes in background thread. Classifies regime and sets engine parameters.

```
Classify market regime for {symbol} scalping.
EMA9/21: {spread}% | RSI: {rsi} | MACD: {macd_hist:+.6f}
BB width: {bb_width} (p{bb_pctl}) | ATR: {atr_ratio}x | Vol: {vol_ratio}x
S/R: {support}-{resistance} | Last 20: {up}/{down} candles
Previous: {prev_regime} for {duration} cycles
```

Returns: `{"regime","confidence","bias","scalp_mode","params":{"min_score","size_factor","sl_mult","tp_mult"},"note"}`

### L3: Scalp Veto Prompt (~145 tokens)

Fire-and-forget for borderline signals. Result applied only if still fresh.

```
SCALP VETO. Approve or reject.
Signal: BUY | Score: 7/10 | Q: 0.60
Regime: TRENDING | RSI: 38 | Vol: 1.2x | Mom: UP
Trend: UP/BULLISH | SEB: INSIDE
Flags: none
```

Returns: `{"action":"buy|sell|hold","confidence":0.7,"reason":"12 words max"}`

**Staleness rules:** Veto expired if > 10s old OR 2+ cycles passed OR signal changed.

### Models
- L2 Regime: gemini-2.5-flash (good structured output)
- L3 Veto: gemini-2.0-flash-lite (faster, cheaper, binary decision)

---

## 5. Code Architecture

### 5.1 New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `src/core/scalp_engine.py` | ScalpEngine (dual-loop), TrailingStopManager, ScalpSession | ~400 |
| `src/core/scalp_signal.py` | ScalpSignalGenerator (entry/exit scoring) | ~300 |
| `src/core/lightweight_analyzer.py` | Incremental indicators from WS cache | ~250 |
| `src/prompts/strategies/scalp_veto.py` | ScalpVetoStrategy (L3, ~145 tokens) | ~80 |
| `src/prompts/strategies/scalp_regime.py` | ScalpRegimeStrategy (L2, ~300 tokens) | ~80 |

### 5.2 Modified Files

| File | Change |
|------|--------|
| `src/core/process_worker.py` | Add 5-line SCALP branch at top |
| `bot_config.json` | Add SCALP_SETTINGS section |
| `src/config.py` | Load SCALP_SETTINGS |
| `src/prompts/strategies/__init__.py` | Register SCALP_VETO, SCALP_REGIME |

### 5.3 Dual-Loop Architecture

```
run_symbol_pipeline(symbol)
    │
    ├── if STRATEGY_STYLE == "SCALP":
    │       ScalpEngine(symbol, ws_cache).run()  ─── blocks forever
    │           │
    │           ├── Fast Loop (main thread, 1-2s)
    │           │     1. Get candles from WS cache (<1ms)
    │           │     2. Compute indicators incrementally (<5ms)
    │           │     3. If position: trailing stop, breakeven, time exit, exit signals
    │           │     4. If no position: ScalpSignalGenerator.generate()
    │           │        - High quality → execute immediately
    │           │        - Borderline → queue for AI veto
    │           │
    │           └── Slow Loop (daemon thread, 30-60s)
    │                 1. Sync position from exchange
    │                 2. Full analyzer.analyze()
    │                 3. Regime detection
    │                 4. Process AI veto for pending signal
    │                 5. Session stats update
    │
    └── else: existing while True loop (unchanged)
```

### 5.4 Key Design Decisions

1. **Threading (not asyncio/multiprocessing):** Fast+slow loops share state within same worker process. GIL not a bottleneck (fast loop is <5ms compute, slow loop is I/O-bound).
2. **Separate ScalpSignalGenerator:** Different indicators and periods from HYBRID's SignalGenerator. Avoids config namespace collision.
3. **WebSocket cache mandatory:** REST API call = 200-800ms. WS cache = <1ms. For 1-2s fast loop, REST is too slow.
4. **SL update throttling:** Only update when change > 0.05%, max 1 per 5 seconds. BingX `set_sl_tp()` is expensive (2-4 API calls).

---

## 6. Configuration

### SCALP_SETTINGS (new section in bot_config.json)

```json
{
  "SCALP_SETTINGS": {
    "enabled": true,
    "signal_rules": {
      "ema_periods": [5, 13],
      "ema_macro": 21,
      "rsi_period": 7,
      "macd_params": [6, 13, 5],
      "atr_period": 10,
      "atr_fast_period": 5,
      "ema_weight": 2,
      "momentum_weight": 1,
      "rsi_weight": 2,
      "vwap_weight": 1,
      "volume_weight": 1,
      "ob_imbalance_weight": 1,
      "macd_weight": 1,
      "bb_weight": 1,
      "rsi_long_zone": [25, 40],
      "rsi_short_zone": [60, 75],
      "ob_imbalance_threshold": 0.3,
      "spread_max_bps": 5.0,
      "min_score_for_signal": 4,
      "auto_execute_quality": 0.6,
      "tier1_required": true,
      "conflict_friction_threshold": 2
    },
    "sl_tp": {
      "sl_atr_mult": 1.0,
      "tp_atr_mult": 3.0,
      "trailing_activation_mult": 1.5,
      "trailing_distance_mult": 0.5,
      "trailing_mode": "atr"
    },
    "breakeven": {
      "enabled": true,
      "trigger_pct": 0.3,
      "fee_buffer_pct": 0.05
    },
    "time_exit": {
      "max_hold_minutes": 15,
      "breakeven_timeout_minutes": 8,
      "loss_timeout_minutes": 5
    },
    "risk_limits": {
      "base_position_pct": 5.0,
      "max_consecutive_losses": 5,
      "consecutive_loss_cooldown_minutes": 30,
      "daily_loss_limit_pct": 3.0,
      "hourly_loss_limit_pct": 1.0,
      "max_trades_per_hour": 6,
      "max_trades_per_day": 50,
      "min_cooldown_seconds": 120
    },
    "loops": {
      "fast_interval": 1.5,
      "slow_interval": 45
    },
    "regime_overrides": {
      "TRENDING": { "ema_weight": 3, "rsi_weight": 1, "bb_weight": 0, "min_score": 3 },
      "RANGING": { "ema_weight": 1, "rsi_weight": 3, "bb_weight": 2, "min_score": 6 },
      "VOLATILE": { "ema_weight": 2, "volume_weight": 2, "min_score": 5 },
      "TRANSITIONAL": { "min_score": 7 }
    },
    "interaction_rules": {
      "momentum_burst_bonus": 2,
      "vwap_bounce_bonus": 1,
      "ob_confluence_bonus": 1,
      "counter_momentum_penalty": -2,
      "spike_penalty": -1
    },
    "ai_integration": {
      "regime_enabled": true,
      "regime_interval_seconds": 300,
      "veto_enabled": true,
      "veto_model": "google/gemini-2.0-flash-lite",
      "borderline_quality_threshold": 0.3
    },
    "session_awareness": {
      "enabled": false,
      "peak_hours_utc": [14, 19],
      "normal_hours_utc": [8, 14],
      "reduced_size_factor": 0.5,
      "weekend_size_factor": 0.5
    }
  }
}
```

### STYLE_PRESETS.SCALP update

```json
{
  "SCALP": {
    "timeframe": "1m",
    "chart_period": "6h",
    "plotter_period": "30m",
    "loop_interval": 1.5,
    "position_check_interval": 1,
    "atr_sl_mult": 1.0,
    "atr_tp_mult": 3.0,
    "leverage": 15,
    "description": "Dual-loop scalp engine with deterministic signals and trailing stops."
  }
}
```

---

## 7. Implementation Priority

### Phase 1: Core Engine (MVP)
1. `scalp_engine.py` — ScalpEngine with dual-loop, TrailingStopManager, ScalpSession
2. `scalp_signal.py` — ScalpSignalGenerator with entry scoring (3 patterns) + exit rules
3. `lightweight_analyzer.py` — Incremental EMA/RSI/ATR/BB/Volume from WS cache
4. `process_worker.py` — Add SCALP branch (5 lines)
5. `bot_config.json` + `config.py` — SCALP_SETTINGS

### Phase 2: Indicators
6. VWAP calculation (rolling cumulative, 00:00 UTC reset)
7. Order book imbalance scoring (from existing `get_order_book()`)
8. MACD(6,13,5) in lightweight analyzer

### Phase 3: AI Overlay
9. `scalp_veto.py` — ScalpVetoStrategy (~145 tokens)
10. `scalp_regime.py` — ScalpRegimeStrategy (~300 tokens)
11. Async AI thread integration in ScalpEngine
12. `__init__.py` — Register SCALP_VETO, SCALP_REGIME

### Phase 4: Advanced Features
13. Limit order entries (maker fee optimization)
14. Partial close capability
15. Session-aware trading (time-of-day)
16. Choppiness filter
17. CVD approximation

### Phase 5: Tuning & Monitoring
18. Performance tracking per regime/pattern
19. Calibration suggestions
20. A/B testing: with vs without AI overlay

---

## 8. Expected Performance

| Metric | Conservative | Target |
|--------|-------------|--------|
| Win rate | 52-55% | 55-60% |
| Average R/R | 1.2:1 | 1.5:1 |
| Trades per day | 15-25 | 25-35 |
| Daily net PnL | 0.5-1.5% | 1.5-3.0% |
| Max drawdown | 5-10% | < 8% |
| AI cost/month | $3-5 | $5 |
| Loop latency | < 10ms | < 5ms |

---

## 9. Comparison: Current vs Proposed

| Aspect | Current SCALP | Proposed SCALP |
|--------|--------------|----------------|
| Architecture | Single loop, same as all strategies | Dual-loop engine |
| Signal source | AI only (every cycle) | Deterministic L1 + optional AI L2/L3 |
| Loop interval | 3-5s (blocked by AI) | 1.5s fast / 45s slow |
| Trailing stops | None | ATR-based, 3 modes |
| Time exits | None | 15-min max hold |
| Session limits | None | Daily loss, hourly, consecutive |
| Fee optimization | Market orders only | Limit order entries planned |
| Indicators | EMA 9/21, RSI 14, standard MACD | EMA 5/13/21, RSI 7, MACD 6/13/5, VWAP, OB |
| AI cost/day | $50+ (impractical) | $0.17 |
| Testability | Hard (AI non-deterministic) | Easy (L1 fully deterministic) |

---

## 10. Open Questions

1. **Limit orders for entries:** BingX API supports limit orders, but current `create_order()` uses market orders. Limit orders save ~30% on fees but risk non-fill. Phase 4 feature.
2. **Symbol filtering:** Should SCALP be limited to BTC/ETH only (best liquidity) or allow all 14 pairs? Recommend starting with top 5 by volume.
3. **Leverage:** 15x is aggressive. Consider offering 10x as a conservative preset.
4. **WebSocket dependency:** If WS connection drops, fast loop degrades to REST (5s interval). Acceptable or should we block?
5. **BingX `set_sl_tp()` implementation:** Currently cancels ALL orders then re-creates. Need to verify this doesn't interfere with other strategies' orders on the same symbol.

---

*Generated: 2026-02-14*
*Sources: quant_analysis.md, prompt_design.md, code_architecture.md, research_findings.md*
