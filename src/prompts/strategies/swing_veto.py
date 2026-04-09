"""
SwingVetoStrategy — English-only risk-veto prompt for SWING mode.

Focused AI role: assess whether a SWING signal is safe for multi-day holding.
- Concise English prompt (reduced token usage)
- Only invoked for borderline/transitional/conflicting signals
- Cannot generate new signals, only APPROVE or REJECT
- Evaluates daily trend strength, entry timing, R/R, macro risks

Key differences from HybridVetoStrategy:
- Higher bar for approval (multi-day commitment)
- Checks for daily structure, not just hourly
- Evaluates pullback quality (entry timing)
- Assesses weekend/event risk
- Minimum R/R: 2:1 (vs 1.2:1 for HYBRID)
"""

from src.prompts.strategies.base import BaseStrategy


class SwingVetoStrategy(BaseStrategy):

    def get_role(self) -> str:
        return (
            "You are a risk-assessment AI filter for a SWING trading system (multi-day positions). "
            "You do NOT generate signals — you only APPROVE or REJECT signals from the deterministic "
            "scoring system. Your focus: is this signal safe to hold for 2-14 DAYS?"
        )

    def get_objective(self) -> str:
        return (
            "Evaluate whether the deterministic SWING signal has acceptable risk for multi-day holding. "
            "Reject when you detect: weak daily trend, poor entry timing (chasing), bad R/R (<2:1), "
            "or macro risks the scoring system cannot see."
        )

    def get_time_horizon(self) -> str:
        return "SWING: 2-14 days. Minimum hold: 24 hours."

    def get_strategy_section(self, ctx: dict) -> str:
        signal_data = ctx.get("signal_data") or {}
        signal = signal_data.get("signal", "HOLD")
        score = signal_data.get("score", 0)
        max_score = signal_data.get("max_score", 10)
        quality = signal_data.get("quality", 0.0)
        signal_data.get("confidence", 0.0)
        reasons = signal_data.get("reasons", [])
        details = signal_data.get("details", {})
        regime = signal_data.get("regime", "UNKNOWN")

        current_price = ctx.get("current_price", 0)
        rsi = ctx.get("rsi", 50)
        atr = ctx.get("atr", 0)
        volume_ratio = ctx.get("volume_ratio", 1.0)
        global_trend = ctx.get("global_trend", "N/A")
        local_trend = ctx.get("local_trend", "N/A")
        last_5_direction = ctx.get("last_5_direction", "MIXED")
        atr_ratio = ctx.get("atr_ratio", 1.0)
        support = ctx.get("support", 0)
        resistance = ctx.get("resistance", 0)
        seb_status = ctx.get("seb_status", "INSIDE")
        seb_r_sq = ctx.get("seb_r_sq", 0)
        trend_quality_desc = ctx.get("trend_quality_desc", "Low")
        macd_hist = ctx.get("macd_hist", 0)

        long_score = details.get("long_score", 0)
        short_score = details.get("short_score", 0)
        conflicting = details.get("conflicting", False)
        tier1 = details.get("long_tier1", False) if signal == "BUY" else details.get("short_tier1", False)
        tier2 = details.get("long_tier2", False) if signal == "BUY" else details.get("short_tier2", False)

        reasons_str = " | ".join(reasons[:5]) if reasons else "none"

        # === SWING-SPECIFIC RISK FLAGS ===
        risk_flags = []

        # 1. Trend quality too low for multi-day hold
        if seb_r_sq < 0.5:
            risk_flags.append(f"Weak trend quality (R²={seb_r_sq:.2f} < 0.5)")

        # 2. Chasing price (RSI extreme in signal direction)
        if signal == "BUY" and rsi > 70:
            risk_flags.append(f"Chasing: RSI overbought ({rsi:.0f}) for LONG entry")
        elif signal == "SELL" and rsi < 30:
            risk_flags.append(f"Chasing: RSI oversold ({rsi:.0f}) for SHORT entry")

        # 3. Counter-trend
        if signal == "BUY" and global_trend == "DOWN":
            risk_flags.append("Counter-trend: BUY against DOWN macro trend")
        elif signal == "SELL" and global_trend == "UP":
            risk_flags.append("Counter-trend: SELL against UP macro trend")

        # 4. Low volume (unreliable for multi-day commitment)
        if volume_ratio < 0.5:
            risk_flags.append(f"Low volume ({volume_ratio:.2f}x) — unreliable for SWING entry")

        # 5. Volatile/transitional regime
        if regime == "VOLATILE":
            risk_flags.append("Volatile regime — wide swings increase SL risk")
        if regime == "TRANSITIONAL":
            risk_flags.append("Transitional regime — trend may be reversing")

        # 6. Conflicting signals
        if conflicting:
            risk_flags.append(f"Conflicting signals (L:{long_score} vs S:{short_score})")

        # 7. Price in mid-range (poor entry for SWING)
        if support > 0 and resistance > 0 and current_price > 0:
            sr_range = resistance - support
            if sr_range > 0:
                position_in_range = (current_price - support) / sr_range
                if 0.35 < position_in_range < 0.65:
                    risk_flags.append(f"Mid-range entry ({position_in_range:.0%}) — wait for pullback to S/R")

        # 8. ATR spike (high volatility period)
        if atr_ratio > 2.0:
            risk_flags.append(f"ATR spike ({atr_ratio:.2f}x) — elevated stop distance")

        # 9. No tier1 direction
        if not tier1:
            risk_flags.append("No Tier1 direction confirmation (EMA/MACD)")

        flags_str = "\n".join(f"  - {f}" for f in risk_flags) if risk_flags else "  - None detected"
        flag_count = len(risk_flags)

        # R/R calculation
        if signal == "BUY":
            sl = ctx.get("long_sl", current_price - atr * 3.0)
            tp = ctx.get("long_tp", current_price + atr * 6.0)
        else:
            sl = ctx.get("short_sl", current_price + atr * 3.0)
            tp = ctx.get("short_tp", current_price - atr * 6.0)

        risk = abs(current_price - sl) if sl else 0
        reward = abs(tp - current_price) if tp else 0
        rr_ratio = reward / risk if risk > 0 else 0

        return f"""## SWING VETO MODE

### Signal from Scoring System

| Field | Value |
|-------|-------|
| Signal | **{signal}** |
| Score | {score}/{max_score} (quality: {quality:.2f}) |
| Regime | {regime} |
| Tier1 (Direction) | {"Yes" if tier1 else "NO"} |
| Tier2 (Confirmation) | {"Yes" if tier2 else "NO"} |
| Reasons | {reasons_str} |

### Market Snapshot

| Indicator | Value |
|-----------|-------|
| Price | {current_price:.2f} |
| RSI (1H) | {rsi:.1f} |
| Volume | {volume_ratio:.2f}x |
| ATR (1H) | {atr:.2f} (ratio: {atr_ratio:.2f}) |
| MACD hist | {macd_hist:.6f} |
| Trend | {global_trend} / {local_trend} |
| Momentum | {last_5_direction} |
| SEB | {seb_status} |
| Trend Quality | {trend_quality_desc} (R²={seb_r_sq:.2f}) |
| Support | {support:.2f} |
| Resistance | {resistance:.2f} |

### Risk/Reward Assessment

| Field | Value |
|-------|-------|
| SL | {sl:.2f} |
| TP | {tp:.2f} |
| Risk (price) | {risk:.2f} |
| Reward (price) | {reward:.2f} |
| R/R Ratio | **{rr_ratio:.2f}:1** |
| Minimum R/R | 2.0:1 |

### Risk Flags ({flag_count} detected)
{flags_str}

### Your Decision

**APPROVE** (action = "{signal.lower()}") if:
- Daily trend supports the direction (Global Trend aligned)
- Trend quality is adequate (R² >= 0.5)
- Entry is NOT chasing (pullback entry, not breakout chase)
- R/R >= 2:1
- No more than 1 critical risk flag

**REJECT** (action = "hold") if:
- R/R < 2:1 (insufficient reward for multi-day risk)
- Counter-trend: signal opposes macro trend
- Weak trend quality (R² < 0.3) — no clear trend to follow
- Chasing: RSI >75 for BUY or <25 for SELL
- Multiple risk flags (3+) present simultaneously
- VOLATILE regime with conflicting signals
- Mid-range entry with no clear direction

### Rules
- You CANNOT reverse the signal (no {("SELL" if signal == "BUY" else "BUY")} allowed)
- You CANNOT generate new signals
- Default bias: slightly conservative (multi-day commitment requires higher conviction)
- Provide your stop_loss and take_profit recommendations
- For SWING: wider stops are EXPECTED (3x ATR). Do not reject for wide SL alone."""

    def get_position_management(self, ctx: dict) -> str:
        close_signal = ctx.get("close_signal") or {}
        should_close = close_signal.get("should_close", False)
        close_reason = close_signal.get("reason", "")
        urgency = close_signal.get("urgency", "low")

        if should_close:
            return f"""### Position Exit Signal (SWING)

The scoring system recommends **CLOSING** the position.

| Field | Value |
|-------|-------|
| Action | CLOSE |
| Reason | {close_reason} |
| Urgency | {urgency.upper()} |

**SWING CHECK before confirming:**
- Is the DAILY structure broken? (not just hourly!)
- Is the reversal confirmed by volume?
- Could this be a normal pullback within the trend?

If daily structure is INTACT, prefer HOLD even if hourly indicators signal exit.

Confirm (action = "close") or reject (action = "hold")."""

        return """### Position Management (SWING)

If a position is open, the system monitors exit conditions.
For SWING positions, only these warrant closing:
- Daily structure break (HH/HL pattern violated)
- Trend reversal confirmed on higher timeframe
- TP reached or SL hit

Do NOT close for:
- Hourly RSI overbought/oversold (normal for SWING)
- Small drawdown (-1% to -3%)
- Single candle against position (noise)
- Time elapsed < 14 days"""

    def get_special_situations(self, ctx: dict) -> str:
        return """### Quick Reference (SWING)

**Always REJECT entry:** R/R <2:1 | Counter-trend | R²<0.3 | RSI >75/BUY or <25/SELL | Volume <0.3x | 3+ risk flags
**Always APPROVE entry:** Trend aligned + R/R >=2.5:1 + pullback entry + volume confirmed + R²>0.5
**Use judgment:** Transitional regime, borderline R/R (2.0-2.5), mixed momentum, mid-range price"""

    def get_risk_table(self, ctx: dict) -> str:
        return ""
