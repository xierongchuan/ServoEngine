# SCALP Mode AI Integration Design

## Executive Summary

After analyzing the full codebase (scalp.py, hybrid_veto.py, swing_veto.py, signal_generator.py, regime.py, process_worker.py, predict.py, builder.py, config), here is the recommended design for SCALP mode AI integration.

**Recommendation: Option E (Layered Hybrid) -- two separate AI roles at different frequencies, with a pure deterministic fast-path as the default.**

---

## 1. AI Role in SCALP Mode

### Analysis of Each Option

#### Option A: No AI -- Pure Deterministic Engine
- **Pros:** Zero latency, zero cost, perfectly predictable, fully testable.
- **Cons:** Loses the one thing AI is good at: pattern recognition across multiple noisy indicators. The current deterministic signal_generator.py already does weighted scoring, but it uses linear thresholds. Crypto 1m charts are noisy -- a deterministic engine tuned for one regime will fail in another. The regime detector helps, but is itself rule-based.
- **Verdict:** Good baseline, but loses value. Suitable as the fallback, not the whole system.

#### Option B: AI for Position Management Only
- **Pros:** Entry is deterministic (fast), AI only activates when already in position. Can run on a slower cadence (every 3-5 cycles = 15-25s).
- **Cons:** Scalp positions last 1-15 minutes. If the loop is 5s and AI takes 10-20s, you get at most 3-6 AI management checks per position. Exit decisions in scalping need to be instant (SL/TP on exchange handles this). AI adds little value for exit management when SL/TP are already placed.
- **Verdict:** AI for exits is low-value in scalping. SL/TP on the exchange is faster and more reliable.

#### Option C: AI Veto on Borderline Signals (like HYBRID_VETO)
- **Pros:** Proven pattern (already works for HYBRID). AI catches what the scoring system misses.
- **Cons:** 10-20s latency per veto call. In a 5s loop, a borderline signal detected in cycle N would get its AI response at cycle N+2 or N+3. By then the price has moved 2-3 candles. The signal may be stale.
- **Verdict:** Possible if we accept staleness. Need an async/non-blocking approach.

#### Option D: AI for Regime Detection Only
- **Pros:** Runs infrequently (every 5-15 minutes). Sets parameters for the deterministic engine. Zero impact on per-cycle latency. Uses AI where it adds most value: understanding market context.
- **Cons:** Regime detection is already done deterministically by regime.py. AI may not add much over the existing TRENDING/RANGING/VOLATILE/TRANSITIONAL classification. However, AI can detect subtler patterns: "this looks like accumulation before a breakout", "this is a distribution pattern", "whale manipulation on the book".
- **Verdict:** High value-add, low cost, no latency impact. Strongly recommended as one layer.

#### Option E: Layered Hybrid (RECOMMENDED)

**Three layers running at different frequencies:**

| Layer | Role | Frequency | Latency Impact | Cost |
|-------|------|-----------|----------------|------|
| L1: Deterministic Engine | Signal generation + immediate execution | Every cycle (3-5s) | None | Zero |
| L2: AI Regime Advisor | Market regime + parameter tuning | Every 5-10 minutes | None (async) | Very low |
| L3: AI Scalp Veto | Risk veto on borderline signals | On-demand (async, non-blocking) | None (fire-and-forget) | Low |

**Why this is best:**
1. L1 handles 90%+ of decisions with zero latency.
2. L2 provides strategic context that makes L1 smarter (sets regime-adaptive parameters).
3. L3 catches dangerous entries on borderline signals, but is NEVER blocking. If the AI response arrives after the signal expires, we just skip it.

**Key architectural insight:** The 5s loop NEVER waits for AI. AI runs in a background thread and writes its results to a shared state dict. The next cycle reads the latest AI state.

---

## 2. Prompt Optimization

### Language: English

**Switch from Russian.** Reasons:
- All modern LLMs are trained predominantly on English data. English prompts produce more reliable structured output.
- HYBRID_VETO and SWING_VETO already use English successfully.
- Reduced token count (Russian uses ~1.5x more tokens than English for equivalent content due to Cyrillic tokenization).
- The existing codebase already mixes languages (comments in Russian, code in English). Prompts in English align with the API/JSON output format.

### Token Budget

| Component | Current (SCALP) | Target (L2 Regime) | Target (L3 Veto) |
|-----------|-----------------|---------------------|-------------------|
| System prompt | ~2000 tokens | ~300 tokens | ~250 tokens |
| Context data | ~500 tokens | ~150 tokens | ~100 tokens |
| Output | ~100 tokens | ~50 tokens | ~30 tokens |
| **Total** | **~2600 tokens** | **~500 tokens** | **~380 tokens** |

### Data Inclusion Strategy

**L2 Regime Advisor (runs every 5-10 min):**
- Include: EMA spread, BB width, ATR ratio, last 20 candles summary (compressed), volume profile, S/R levels, last regime classification.
- Exclude: Individual candle OHLCV, full indicator history, position data, SL/TP calculations.

**L3 Scalp Veto (on-demand):**
- Include: Signal direction, score, quality, regime, RSI, volume ratio, momentum, auto-detected risk flags.
- Exclude: Candle history, full indicator values, SL/TP tables, strategy explanations. The scoring system already evaluated all indicators -- AI just needs the summary.

### Model Selection

**gemini-2.5-flash** (current) is a good choice for cost/speed:
- Median latency: 3-8 seconds for short prompts
- Cost: ~$0.15/M input tokens, ~$0.60/M output tokens (OpenRouter)
- Good at structured JSON output

**Alternative for L3 Veto (if latency matters more):**
- gemini-2.0-flash-lite -- even faster, cheaper, good enough for binary APPROVE/REJECT
- Typical latency: 1-3 seconds

**Recommendation:** Use gemini-2.5-flash for L2 Regime, gemini-2.0-flash-lite for L3 Veto.

---

## 3. SCALP_VETO Prompt Design (L3)

### System Role (injected once)
```
You are a scalp trade risk filter. You receive a pre-scored signal and must APPROVE or REJECT it. You CANNOT reverse the signal direction. Be fast and decisive.
```

### Context Template (per-call, ~100 tokens)
```
Signal: {signal} | Score: {score}/{max_score} | Quality: {quality:.2f}
Regime: {regime} | RSI: {rsi:.0f} | Volume: {volume_ratio:.1f}x | Momentum: {last_5_direction}
Trend: {global_trend}/{local_trend} | SEB: {seb_status}
Risk flags: {flags_str}
```

### Response Format (~30 tokens)
```json
{"action":"buy|sell|hold","confidence":0.7,"reason":"12 words max"}
```

### Full Prompt (assembled)
```
SCALP VETO. Approve or reject this signal.

Signal: BUY | Score: 7/10 | Quality: 0.60
Regime: TRENDING | RSI: 38 | Volume: 1.2x | Momentum: UP
Trend: UP/BULLISH | SEB: INSIDE
Risk flags: none

Rules:
- APPROVE (action=buy) if no critical risk. Default bias: approve.
- REJECT (action=hold) if: RSI extreme (>78 buy, <22 sell), dead volume (<0.3x), strong counter-trend, 2+ risk flags.
- Cannot reverse signal. Cannot generate new signals.

Reply ONLY with JSON: {"action":"...","confidence":0.0-1.0,"reason":"..."}
```

**Total tokens: ~120 input, ~25 output = ~145 tokens per call.**

### Hard REJECT Rules (built into prompt)
- RSI > 78 for BUY or RSI < 22 for SELL
- Volume < 0.3x
- Counter-trend: BUY when global=DOWN+local=BEARISH (or vice versa)
- 2+ risk flags present

### Hard APPROVE Rules (built into prompt)
- Score >= 8/10 + all indicators aligned
- Quality >= 0.7 + volume confirmed + trend matches

---

## 4. SCALP_REGIME Prompt Design (L2)

### System Role
```
You are a market regime classifier for a crypto scalping system. Analyze the technical summary and classify the current market state. Your classification sets parameters for the scalp engine.
```

### Context Template (~200 tokens)
```
Symbol: {symbol} | Price: {price:.2f} | Timeframe: 1m

Indicators:
- EMA9/21 spread: {ema_spread:.2f}%
- RSI: {rsi:.0f} | MACD hist: {macd_hist:+.6f}
- BB width: {bb_width:.2f} (percentile: {bb_pctl:.0f}%)
- ATR ratio: {atr_ratio:.2f} | Volume avg: {vol_ratio:.2f}x
- Support: {support:.2f} | Resistance: {resistance:.2f}

Last 20 candles: {up_count} up, {down_count} down, avg body: {avg_body:.4f}
Previous regime: {prev_regime} (for {regime_duration} cycles)
```

### Response Format (~80 tokens)
```json
{
  "regime": "TRENDING|RANGING|VOLATILE|CHOPPY",
  "confidence": 0.8,
  "bias": "long|short|neutral",
  "scalp_mode": "momentum|reversal|range|off",
  "params": {
    "min_score": 5,
    "size_factor": 1.0,
    "sl_mult": 1.5,
    "tp_mult": 2.0
  },
  "note": "Clean uptrend, favor momentum longs"
}
```

### Regime -> Scalp Engine Parameter Mapping

| Regime | scalp_mode | min_score | size_factor | SL mult | TP mult |
|--------|-----------|-----------|-------------|---------|---------|
| TRENDING | momentum | 4 | 1.2 | 1.5 | 2.5 |
| RANGING | range | 5 | 1.0 | 1.0 | 1.5 |
| VOLATILE | momentum | 6 | 0.7 | 2.0 | 3.0 |
| CHOPPY | off | 8 | 0.5 | 1.5 | 2.0 |

**"off" mode:** When AI classifies CHOPPY, min_score is raised to 8 (effectively blocking most signals). This is the AI's "don't trade" recommendation without blocking the loop.

### Full Prompt (~280 tokens)
```
Classify market regime for {symbol} scalping.

EMA9/21: {spread}% | RSI: {rsi} | MACD: {macd_hist:+.6f}
BB width: {bb_width} (p{bb_pctl}) | ATR: {atr_ratio}x | Vol: {vol_ratio}x
S/R: {support}-{resistance} | Last 20: {up}/{down} candles
Previous: {prev_regime} for {duration} cycles

Regimes:
- TRENDING: clear direction, momentum scalps work
- RANGING: sideways, mean reversion scalps work
- VOLATILE: wide swings, reduce size, widen stops
- CHOPPY: no edge, stop trading

Reply JSON only:
{"regime":"...","confidence":0.0-1.0,"bias":"long|short|neutral","scalp_mode":"momentum|reversal|range|off","params":{"min_score":5,"size_factor":1.0,"sl_mult":1.5,"tp_mult":2.0},"note":"10 words max"}
```

---

## 5. Cost/Latency Analysis

### Model: gemini-2.5-flash via OpenRouter

**Pricing (as of early 2026):**
- Input: ~$0.15 per 1M tokens
- Output: ~$0.60 per 1M tokens

### L2 Regime Advisor (every 5 min, per symbol)

| Metric | Value |
|--------|-------|
| Calls per hour per symbol | 12 |
| Input tokens per call | ~300 |
| Output tokens per call | ~80 |
| Input tokens/hour/symbol | 3,600 |
| Output tokens/hour/symbol | 960 |
| Cost per hour per symbol | $0.0011 |
| **Cost per hour (5 symbols)** | **$0.0054** |
| **Cost per day (5 symbols)** | **$0.13** |

### L3 Scalp Veto (on-demand, estimated 5-10 calls/hour/symbol)

| Metric | Value |
|--------|-------|
| Calls per hour per symbol | ~8 (estimated) |
| Input tokens per call | ~150 |
| Output tokens per call | ~30 |
| Input tokens/hour/symbol | 1,200 |
| Output tokens/hour/symbol | 240 |
| Cost per hour per symbol | $0.0003 |
| **Cost per hour (5 symbols)** | **$0.0016** |
| **Cost per day (5 symbols)** | **$0.04** |

### Total Estimated Cost

| Scenario | Per Hour | Per Day | Per Month |
|----------|----------|---------|-----------|
| L2 only (regime) | $0.005 | $0.13 | $3.90 |
| L2 + L3 (regime + veto) | $0.007 | $0.17 | $5.10 |
| Current HYBRID mode (for comparison) | ~$0.05 | ~$1.20 | ~$36 |

**The SCALP AI integration costs ~7x less than current HYBRID mode** because prompts are ultra-short and calls are infrequent.

### Latency Budget

| Layer | Acceptable Latency | Expected Latency | Blocking? |
|-------|-------------------|-------------------|-----------|
| L1 Deterministic | 0ms (in-process) | <1ms | No |
| L2 Regime | 30s max | 3-8s (gemini-2.5-flash) | No (async background) |
| L3 Veto | 10s max | 2-5s (gemini-2.0-flash-lite) | No (async fire-and-forget) |

**Critical design rule:** The 5s scalp loop NEVER blocks on AI. AI responses are written to a shared state dict by background threads. If a response arrives late, the next cycle picks it up. If a signal expires before the veto arrives, the veto is discarded.

---

## 6. Architecture: Async AI Integration

### State Flow

```
Background Threads                     Main Loop (5s cycle)
==================                     ====================

[L2 Regime Thread]                     cycle N:
  every 5 min:                           1. read shared_state["regime"] (from L2)
  call AI -> parse ->                    2. run signal_generator with regime params
  shared_state["regime"] = result        3. if signal and borderline:
                                              fire L3 veto (non-blocking)
[L3 Veto Thread Pool]                   4. if signal and high quality:
  on-demand:                                  execute immediately
  call AI -> parse ->                    5. check shared_state["veto_result"]
  shared_state["veto_result"] = result        if fresh and matches current signal:
                                                apply veto decision
                                              else: expired, ignore
                                         6. execute/hold
                                         7. sleep
```

### Shared State Dict Structure
```python
scalp_ai_state = {
    "regime": {
        "regime": "TRENDING",
        "confidence": 0.85,
        "bias": "long",
        "scalp_mode": "momentum",
        "params": {"min_score": 4, "size_factor": 1.2, ...},
        "timestamp": 1707900000,
        "symbol": "BTCUSDT"
    },
    "pending_veto": {
        "signal": "BUY",
        "score": 6,
        "request_time": 1707900120,
        "cycle_id": 42
    },
    "veto_result": {
        "action": "buy",
        "confidence": 0.75,
        "reason": "Trend aligned, clean setup",
        "response_time": 1707900125,
        "for_cycle_id": 42
    }
}
```

### Veto Staleness Rule

A veto result is **stale** if:
- `response_time - request_time > 10s` (AI took too long)
- `current_cycle_id - for_cycle_id > 2` (more than 2 cycles passed)
- Signal direction changed since the veto was requested

Stale veto results are discarded. The signal defaults to the deterministic engine's decision (execute if high quality, hold if borderline with no veto).

---

## 7. ScalpVetoStrategy Implementation Sketch

```python
class ScalpVetoStrategy(BaseStrategy):
    """Ultra-minimal veto prompt for SCALP mode."""

    def get_role(self) -> str:
        return "Scalp trade risk filter. APPROVE or REJECT only."

    def get_objective(self) -> str:
        return "Catch dangerous entries the scoring system missed. Be fast."

    def get_time_horizon(self) -> str:
        return "1-15 minutes."

    def get_strategy_section(self, ctx: dict) -> str:
        signal_data = ctx.get("signal_data", {})
        signal = signal_data.get("signal", "HOLD")
        score = signal_data.get("score", 0)
        max_score = signal_data.get("max_score", 10)
        quality = signal_data.get("quality", 0.0)
        regime = signal_data.get("regime", "UNKNOWN")

        rsi = ctx.get("rsi", 50)
        volume_ratio = ctx.get("volume_ratio", 1.0)
        global_trend = ctx.get("global_trend", "N/A")
        local_trend = ctx.get("local_trend", "N/A")
        last_5_direction = ctx.get("last_5_direction", "MIXED")
        seb_status = ctx.get("seb_status", "INSIDE")

        # Auto-detect risk flags (same pattern as hybrid_veto.py)
        risk_flags = []
        if signal == "BUY" and rsi > 75:
            risk_flags.append(f"RSI overbought ({rsi:.0f})")
        elif signal == "SELL" and rsi < 25:
            risk_flags.append(f"RSI oversold ({rsi:.0f})")
        if volume_ratio < 0.3:
            risk_flags.append(f"Dead volume ({volume_ratio:.2f}x)")
        if signal == "BUY" and global_trend == "DOWN" and local_trend == "BEARISH":
            risk_flags.append("Counter-trend")
        elif signal == "SELL" and global_trend == "UP" and local_trend == "BULLISH":
            risk_flags.append("Counter-trend")

        details = signal_data.get("details", {})
        if details.get("conflicting"):
            risk_flags.append("Conflicting signals")

        flags_str = ", ".join(risk_flags) if risk_flags else "none"

        opposite = "SELL" if signal == "BUY" else "BUY"

        return f"""SCALP VETO. Approve or reject.

Signal: {signal} | Score: {score}/{max_score} | Q: {quality:.2f}
Regime: {regime} | RSI: {rsi:.0f} | Vol: {volume_ratio:.1f}x | Mom: {last_5_direction}
Trend: {global_trend}/{local_trend} | SEB: {seb_status}
Flags: {flags_str}

APPROVE (action="{signal.lower()}"): no critical risk, default bias.
REJECT (action="hold"): RSI extreme, dead volume, counter-trend, 2+ flags.
Cannot use action="{opposite.lower()}". Cannot generate signals."""

    def get_position_management(self, ctx: dict) -> str:
        return ""

    def get_special_situations(self, ctx: dict) -> str:
        return ""

    def get_risk_table(self, ctx: dict) -> str:
        return ""
```

### Token Count Estimate for ScalpVetoStrategy

With the minimal prompt above plus the response_format block trimmed:
- Role + objective + time_horizon: ~25 tokens
- Strategy section: ~90 tokens
- Response format (trimmed for scalp): ~30 tokens
- **Total input: ~145 tokens**
- **Expected output: ~25 tokens**

---

## 8. ScalpRegimeStrategy Implementation Sketch

```python
class ScalpRegimeStrategy(BaseStrategy):
    """Regime classification prompt for SCALP mode. Runs every 5-10 min."""

    def get_role(self) -> str:
        return "Market regime classifier for crypto scalping."

    def get_objective(self) -> str:
        return "Classify regime. Set scalp engine parameters."

    def get_time_horizon(self) -> str:
        return "Next 5-15 minutes (regime window)."

    def get_strategy_section(self, ctx: dict) -> str:
        symbol = ctx.get("symbol", "?")
        current_price = ctx.get("current_price", 0)
        ema9 = ctx.get("ema9", 0)
        ema21 = ctx.get("ema21", 0)
        rsi = ctx.get("rsi", 50)
        atr_ratio = ctx.get("atr_ratio", 1.0)
        volume_ratio = ctx.get("volume_ratio", 1.0)
        macd_hist = ctx.get("macd_hist", 0)
        support = ctx.get("support", 0)
        resistance = ctx.get("resistance", 0)
        seb_status = ctx.get("seb_status", "INSIDE")
        last_5_direction = ctx.get("last_5_direction", "MIXED")

        ema_spread = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0

        # Previous regime from shared state
        prev_regime = ctx.get("prev_regime", "UNKNOWN")

        return f"""{symbol} | {current_price:.2f}

EMA spread: {ema_spread:.2f}% | RSI: {rsi:.0f} | MACD: {macd_hist:+.6f}
ATR: {atr_ratio:.2f}x | Vol: {volume_ratio:.2f}x | SEB: {seb_status}
S/R: {support:.2f}-{resistance:.2f} | Mom: {last_5_direction}
Previous: {prev_regime}

Classify:
- TRENDING: directional momentum, use momentum scalps
- RANGING: sideways, use range/reversal scalps
- VOLATILE: wide swings, reduce size, widen stops
- CHOPPY: no edge, set min_score=8 to block most trades

JSON: {{"regime":"...","confidence":0.0-1.0,"bias":"long|short|neutral","scalp_mode":"momentum|reversal|range|off","params":{{"min_score":5,"size_factor":1.0,"sl_mult":1.5,"tp_mult":2.0}},"note":"max 10 words"}}"""

    def get_position_management(self, ctx: dict) -> str:
        return ""

    def get_special_situations(self, ctx: dict) -> str:
        return ""

    def get_risk_table(self, ctx: dict) -> str:
        return ""
```

---

## 9. Comparison: Current vs Proposed

| Aspect | Current SCALP | Proposed SCALP |
|--------|--------------|----------------|
| AI role | Full decision maker (every cycle) | L2: regime advisor (every 5min) + L3: veto (on-demand) |
| Loop blocking | Yes (5-30s per AI call) | Never (async background) |
| Language | Russian | English |
| Prompt tokens | ~2600 | L2: ~300, L3: ~145 |
| AI calls/hour | ~720 (every 5s) | L2: 12 + L3: ~40 = ~52 |
| Cost/day (5 symbols) | $52+ (impractical) | $0.17 |
| Latency impact | 5-30s per cycle | 0ms (async) |
| Deterministic? | No (AI decides everything) | Yes (L1 is deterministic, AI advises) |
| Testable? | Hard (AI is non-deterministic) | Easy (L1 is fully deterministic, AI is optional overlay) |

---

## 10. Implementation Priority

### Phase 1: Deterministic Scalp Engine (no AI)
1. Adapt `SignalGenerator` for 1m timeframe (adjust weights/thresholds for scalp)
2. Add scalp-specific scoring rules (momentum breakout, liquidity grab, range scalp, mean reversion from current scalp.py)
3. Port the HYBRID workflow in `process_worker.py` to work for SCALP (direct execution path)
4. Use existing `regime.py` deterministic detector with scalp-tuned parameters

### Phase 2: L2 AI Regime Advisor
5. Create `ScalpRegimeStrategy` class
6. Add background thread in `process_worker.py` for periodic regime AI calls
7. Shared state dict between regime thread and main loop
8. A/B test: deterministic regime vs AI regime classification

### Phase 3: L3 AI Scalp Veto
9. Create `ScalpVetoStrategy` class
10. Add async veto thread pool in `process_worker.py`
11. Implement staleness rules for veto results
12. A/B test: with and without veto layer

### Phase 4: Tuning
13. Collect performance data per regime classification
14. Tune signal_generator weights for each scalp_mode
15. Evaluate whether L3 veto adds value (reject rate vs win rate improvement)
