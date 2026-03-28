from src.prompts.strategies.base import BaseStrategy


class AiScalpStrategy(BaseStrategy):

    def get_role(self) -> str:
        return "Ты — AI Scalping Trader (внутридневной трейдер на 1m TF с мультитаймфреймовым анализом)."

    def get_objective(self) -> str:
        return "Ловить дневные движения в направлении HTF тренда. Держать позицию 4-12 часов. Шире стопы чем в скальпинге."

    def get_time_horizon(self) -> str:
        return "Horizon: 4-12 часов. Закрыть к концу торговой сессии."

    def get_strategy_section(self, ctx: dict) -> str:
        current_price = ctx.get("current_price", 0)
        atr = ctx.get("atr", 0)
        rsi = ctx.get("rsi", 50)

        global_trend = ctx.get("global_trend", "N/A")
        local_trend = ctx.get("local_trend", "N/A")
        last_5_direction = ctx.get("last_5_direction", "MIXED")

        support = ctx.get("support", 0)
        resistance = ctx.get("resistance", 0)
        support_dist_pct = ctx.get("support_dist_pct", 0)
        resistance_dist_pct = ctx.get("resistance_dist_pct", 0)

        seb_upper = ctx.get("seb_upper", 0)
        seb_lower = ctx.get("seb_lower", 0)
        seb_status = ctx.get("seb_status", "INSIDE")
        trend_quality_desc = ctx.get("trend_quality_desc", "Low")

        long_sl = ctx.get("long_sl", 0)
        long_tp = ctx.get("long_tp", 0)
        short_sl = ctx.get("short_sl", 0)
        short_tp = ctx.get("short_tp", 0)

        if current_price > 0:
            long_potential_pct = (long_tp - current_price) / current_price * 100
            short_potential_pct = (current_price - short_tp) / current_price * 100
            long_risk_pct = (current_price - long_sl) / current_price * 100
            short_risk_pct = (short_sl - current_price) / current_price * 100
        else:
            long_potential_pct = short_potential_pct = long_risk_pct = short_risk_pct = 0

        warnings = []
        if "MIXED" in last_5_direction and trend_quality_desc == "Low":
            warnings.append("CHOPPY: No direction — consider HOLD.")

        warnings_block = ""
        if warnings:
            warnings_block = "\n**CURRENT RISKS:**\n" + "\n".join(f"- {w}" for w in warnings)

        # === HTF CONTEXT BLOCK ===
        htf_block = ""
        htf_data = ctx.get("htf_data")
        if htf_data:
            htf_trend = htf_data.get("htf_trend", "NEUTRAL")
            daily_bias = htf_data.get("daily_bias", "NEUTRAL")
            htf_rsi = htf_data.get("htf_rsi", 50)
            daily_change = htf_data.get("daily_change_pct", 0)
            htf_block = f"""
---

### HIGHER TIMEFRAME (1H)
| HTF Trend | Daily Bias | HTF RSI | Change |
|-----------|------------|---------|--------|
| {htf_trend} | {daily_bias} | {htf_rsi:.1f} | {daily_change:+.2f}% |

**RULE: Trade ONLY in HTF direction. Counter-HTF entries are high risk.**
"""

        # === SESSION CONTEXT BLOCK ===
        session_block = ""
        session_data = ctx.get("session_data")
        if session_data:
            hour = session_data.get("current_hour_utc", 0)
            sessions = "+".join(session_data.get("active_sessions", [])) or "NONE"
            quality = session_data.get("session_quality", "MEDIUM")
            session_block = f"""
---

### TRADING SESSION
| Time UTC | Active Sessions | Quality |
|----------|----------------|---------|
| {hour}:00 | {sessions} | {quality} |
"""

        # === SIGNAL SCORE BLOCK ===
        signal_block = ""
        signal_data = ctx.get("signal_data")
        if signal_data and signal_data.get("signal") != "HOLD":
            sig = signal_data.get("signal", "HOLD")
            score = signal_data.get("score", 0)
            max_score = signal_data.get("max_score", 13)
            quality = signal_data.get("quality", 0)
            regime = signal_data.get("regime", "UNKNOWN")
            reasons = signal_data.get("reasons", [])
            reasons_str = ", ".join(reasons[:5])
            signal_block = f"""
---

### SYSTEM SIGNAL (advisory — YOU decide)
| Signal | Score | Quality | Regime |
|--------|-------|---------|--------|
| {sig} | {score}/{max_score} | {quality:.2f} | {regime} |

**Reasons:** {reasons_str}

**This is a recommendation.** You make the final decision. You may:
- **Confirm** the signal if your analysis agrees
- **Reject** and HOLD if you see risks the system missed
- **Adjust** SL/TP based on your analysis
"""
        elif signal_data:
            reasons = signal_data.get("reasons", ["No signal"])
            reasons_str = reasons[0] if reasons else ""
            signal_block = f"""
---

### SYSTEM SIGNAL (advisory — YOU decide)
**No deterministic signal.** {reasons_str}

**This does NOT mean you must HOLD.** Analyze the market yourself:
- If you see a clear setup with HTF alignment — you can enter
- If the market is unclear — HOLD is correct
- Trust your own analysis over the scoring system
"""

        # === DETERMINISTIC CLOSE BLOCK ===
        close_block = ""
        det_close = ctx.get("deterministic_close")
        if det_close and det_close.get("should_close"):
            close_reason = det_close.get("reason", "Exit signal")
            close_urgency = det_close.get("urgency", "medium")
            urgency_note = "**URGENT — strongly consider closing.**" if close_urgency == "high" else "Consider closing, but evaluate the full picture."
            close_block = f"""
---

### EXIT SIGNAL (advisory — YOU decide)
**System recommends CLOSE:** {close_reason}
Urgency: {close_urgency}

{urgency_note}
You may override if your analysis shows the position is still valid.
"""

        # === RISK WARNING BLOCK ===
        risk_block = ""
        risk_warning = ctx.get("risk_warning")
        if risk_warning:
            risk_block = f"""
---

### RISK WARNING
**{risk_warning}**
Factor this into your decision — poor R/R means higher risk.
"""

        return f"""## 3. STRATEGY: AI SCALPING (1m TF + 1H HTF)
*Multi-timeframe AI scalping. Trade in HTF direction, confirm on 1m.*

**YOU are the final decision-maker.** The scoring system provides signals as recommendations — confirm, reject, or override based on your own analysis.
{htf_block}{session_block}{signal_block}{close_block}{risk_block}
---

### AI SCALP MINDSET

**Key principles:**
- Trade ONLY in HTF (1H) trend direction
- Use 1m for precise entry timing and confirmation
- Wide SL (see table below) — let price breathe through noise
- Wide TP — capture the full intraday move
- Hold for hours, not minutes
- Fewer trades, bigger moves
{warnings_block}

---

### ENTRY SETUPS

**1. TREND CONTINUATION**

Conditions for LONG:
- HTF Trend = BULLISH (required)
- Local Trend = BULLISH (EMA9 > EMA21)
- RSI 30-55 (not overbought, current: {rsi:.1f})
- Last 5 Direction: UP or STRONG UP (current: {last_5_direction})

Conditions for SHORT:
- HTF Trend = BEARISH (required)
- Local Trend = BEARISH
- RSI 45-70
- Last 5 Direction: DOWN or STRONG DOWN

---

**2. PULLBACK ENTRY**

Conditions for LONG:
- HTF Trend = BULLISH (not broken)
- Price pulled back to support ({support:.2f}) or EMA
- RSI dipped to 35-50 (current: {rsi:.1f})

Conditions for SHORT:
- HTF Trend = BEARISH
- Price bounced to resistance ({resistance:.2f})
- RSI rose to 50-65

**Entry:** On reversal from level. Do NOT catch falling knives.

---

**3. BREAKOUT**

Conditions:
- Price breaks resistance ({resistance:.2f}) or support ({support:.2f})
- Confirmation: close beyond level

**Entry:** After candle close beyond level, not during the break.
**SL:** Behind the broken level.

---

### POSITION MANAGEMENT (ATR-BASED)

**For LONG:**
| Parameter | Level | % from price |
|-----------|-------|-------------|
| Entry | ~{current_price:.2f} | — |
| Stop Loss | {long_sl:.2f} | -{long_risk_pct:.2f}% |
| Take Profit | {long_tp:.2f} | +{long_potential_pct:.2f}% |

**For SHORT:**
| Parameter | Level | % from price |
|-----------|-------|-------------|
| Entry | ~{current_price:.2f} | — |
| Stop Loss | {short_sl:.2f} | +{short_risk_pct:.2f}% |
| Take Profit | {short_tp:.2f} | -{short_potential_pct:.2f}% |

**Rules:**
1. Use the SL/TP levels from the table above (ATR-based, regime-adjusted)
2. Do NOT move SL against your position
3. Move SL to breakeven after price moves halfway to TP

---

### MARKET ADAPTATION

**Current state:**
- Trend: Global={global_trend}, Local={local_trend}
- Momentum: {last_5_direction}
- Trend quality: {trend_quality_desc}
- SEB: {seb_status}

| State | Action |
|-------|--------|
| Trending | Trend Continuation |
| Trending + Pullback | Pullback Entry |
| Breakout | Breakout Trade |
| Ranging (INSIDE) | HOLD or Range play |

---

### WHEN NOT TO ENTER (HOLD)

**Hard filters:**
1. MIXED direction + Low quality + RSI 45-55 (full chaos)
2. Counter-HTF trade (signal against 1H trend)

**Soft filters:**
- Price in middle of range (far from levels)
- Conflicting trends (Global UP, Local BEARISH)
- Off-session hours (lower liquidity, use caution)

**IMPORTANT:** Do not skip a trade due to imperfection.
If there is HTF direction + LTF confirmation — consider entry."""

    def get_position_management(self, ctx: dict) -> str:
        return """### POSITION MANAGEMENT (AI SCALP MODE)

**RULE:** Let profits run, but don't be greedy.

1. **Small profit (+0.5% - +1.5%):**
   - Move SL to breakeven
   - HOLD — this is not TP yet

2. **Good profit (+1.5% - +3%):**
   - Consider partial close (50%)
   - Trailing stop on remainder

3. **Small loss (up to -1%):**
   - HOLD if structure is intact
   - CLOSE if key level is broken

4. **Near SL:**
   - Do NOT move SL further
   - Accept the loss, move on

5. **Position stalled:**
   - No movement > 2 hours = consider exit"""

    def get_special_situations(self, ctx: dict) -> str:
        return """### SPECIAL SITUATIONS (AI SCALP)

**1. STRONG TREND DAY:**
- Price moves in one direction all day
- Do NOT counter-trend — only trade with the trend
- Can add to position on pullbacks

**2. FAKEOUT:**
- Breakout + return = enter AGAINST the breakout
- SL behind the fakeout extreme

**3. REVERSAL:**
- After strong morning move, price reverses
- Wait for confirmation (break of key level in opposite direction)
- Caution — false reversals are common

**4. HTF TREND REVERSAL:**
- If 1H trend changes direction while in position
- Tighten SL or close if in loss
- Do not add to position against new HTF direction"""
