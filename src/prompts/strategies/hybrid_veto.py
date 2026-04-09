"""
HybridVetoStrategy — English-only risk-veto prompt for HYBRID mode.

Focused AI role: assess risks the scoring system might miss.
- Concise English prompt (reduced token usage)
- Only invoked for borderline/transitional/conflicting signals
- Cannot generate new signals, only APPROVE or REJECT
"""

from src.prompts.strategies.base import BaseStrategy


class HybridVetoStrategy(BaseStrategy):

    def get_role(self) -> str:
        return "You are a risk-assessment AI filter for a crypto futures trading system. You do NOT generate signals — you only APPROVE or REJECT signals from the deterministic scoring system."

    def get_objective(self) -> str:
        return "Evaluate whether the deterministic signal has acceptable risk. Reject only when you detect clear danger the scoring system cannot see."

    def get_time_horizon(self) -> str:
        return "Short-term: 4-12 hours."

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
        volume_ratio = ctx.get("volume_ratio", 1.0)
        global_trend = ctx.get("global_trend", "N/A")
        local_trend = ctx.get("local_trend", "N/A")
        last_5_direction = ctx.get("last_5_direction", "MIXED")
        atr_ratio = ctx.get("atr_ratio", 1.0)
        support = ctx.get("support", 0)
        resistance = ctx.get("resistance", 0)
        seb_status = ctx.get("seb_status", "INSIDE")
        macd_hist = ctx.get("macd_hist", 0)

        long_score = details.get("long_score", 0)
        short_score = details.get("short_score", 0)
        conflicting = details.get("conflicting", False)
        tier1 = details.get("long_tier1", False) if signal == "BUY" else details.get("short_tier1", False)
        tier2 = details.get("long_tier2", False) if signal == "BUY" else details.get("short_tier2", False)

        reasons_str = " | ".join(reasons[:5]) if reasons else "none"

        # Determine risk flags
        risk_flags = []
        if signal == "BUY" and rsi > 70:
            risk_flags.append(f"RSI overbought ({rsi:.0f})")
        elif signal == "SELL" and rsi < 30:
            risk_flags.append(f"RSI oversold ({rsi:.0f})")
        if volume_ratio < 0.5:
            risk_flags.append(f"Low volume ({volume_ratio:.2f}x)")
        if signal == "BUY" and global_trend == "DOWN" and local_trend == "BEARISH":
            risk_flags.append("Counter-trend (DOWN+BEARISH)")
        elif signal == "SELL" and global_trend == "UP" and local_trend == "BULLISH":
            risk_flags.append("Counter-trend (UP+BULLISH)")
        if conflicting:
            risk_flags.append(f"Conflicting signals (L:{long_score} vs S:{short_score})")
        if regime == "VOLATILE":
            risk_flags.append("Volatile regime")
        if regime == "TRANSITIONAL":
            risk_flags.append("Transitional regime (uncertain)")

        flags_str = "\n".join(f"  - {f}" for f in risk_flags) if risk_flags else "  - None detected"

        return f"""## HYBRID VETO MODE

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
| RSI | {rsi:.1f} |
| Volume | {volume_ratio:.2f}x |
| ATR ratio | {atr_ratio:.2f} |
| MACD hist | {macd_hist:.6f} |
| Trend | {global_trend} / {local_trend} |
| Momentum | {last_5_direction} |
| SEB | {seb_status} |
| Support | {support:.2f} |
| Resistance | {resistance:.2f} |

### Risk Flags (auto-detected)
{flags_str}

### Your Decision

**APPROVE** (action = "{signal.lower()}") if:
- No critical risk flags above
- Market structure supports the direction
- Risk/reward is acceptable

**REJECT** (action = "hold") if:
- RSI extreme against signal direction (>75 for BUY, <25 for SELL)
- Strong counter-trend movement
- Dead volume (<0.3x) with no momentum
- Price trapped in mid-range with no clear direction
- Multiple risk flags present simultaneously

### Rules
- You CANNOT reverse the signal (no {("SELL" if signal == "BUY" else "BUY")} allowed)
- You CANNOT generate new signals
- Default bias: APPROVE unless clear danger exists
- Provide your stop_loss and take_profit recommendations"""

    def get_position_management(self, ctx: dict) -> str:
        close_signal = ctx.get("close_signal") or {}
        should_close = close_signal.get("should_close", False)
        close_reason = close_signal.get("reason", "")
        urgency = close_signal.get("urgency", "low")

        if should_close:
            return f"""### Position Exit Signal

The scoring system recommends **CLOSING** the position.

| Field | Value |
|-------|-------|
| Action | CLOSE |
| Reason | {close_reason} |
| Urgency | {urgency.upper()} |

Confirm (action = "close") or reject (action = "hold")."""

        return """### Position Management

If a position is open, the system monitors exit conditions:
- RSI extremes, trend reversals, profit targets
- You can confirm or reject close recommendations."""

    def get_special_situations(self, ctx: dict) -> str:
        return """### Quick Reference

**Always REJECT:** RSI >78 for BUY | RSI <22 for SELL | Volume <0.3x | Strong counter-trend + loss
**Always APPROVE:** Score 8+, all indicators aligned, volume confirmed, trend matches signal
**Use judgment:** Transitional regime, borderline RSI (65-75), mixed momentum"""

    def get_risk_table(self, ctx: dict) -> str:
        return ""
