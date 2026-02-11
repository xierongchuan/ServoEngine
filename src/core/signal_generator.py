"""
Deterministic Signal Generator for HYBRID mode.
Generates trading signals based on technical indicators with scoring system.
AI only confirms/rejects these signals - it cannot generate its own.

SCORING SYSTEM v2:
- EMA alignment: +2
- RSI zone: +2
- S/R proximity: +2
- Momentum: +1
- MACD crossover: +2
- Bollinger Bands: +1
- Volume: +1
Max: 11, Min for signal: 4
"""

from src.config import BOT_CONFIG
from src.utils.logger import info, warning


class SignalGenerator:
    """Детерминированный генератор сигналов с фильтром волатильности."""

    def __init__(self):
        self.settings = BOT_CONFIG.get("HYBRID_SETTINGS", {})
        self.rules = self.settings.get("signal_rules", {})

    def generate_signal(self, analysis: dict) -> dict:
        """
        Генерирует детерминированный сигнал на основе анализа.

        Features:
        - ATR volatility filter
        - MACD crossover scoring
        - Bollinger Bands touch
        - Non-overlapping RSI zones
        """
        # === EXTRACT DATA ===
        global_trend = analysis.get("global_trend", "N/A")
        local_trend = analysis.get("local_trend", "N/A")
        rsi = analysis.get("rsi", 50)
        volume_ratio = analysis.get("volume_ratio", 1.0)
        current_price = analysis.get("current_price", 0)
        support = analysis.get("support", 0)
        resistance = analysis.get("resistance", 0)
        ema9 = analysis.get("ema9", 0)
        ema21 = analysis.get("ema21", 0)

        # New indicators
        atr_ratio = analysis.get("atr_ratio", 1.0)
        macd_line = analysis.get("macd_line", 0)
        macd_signal = analysis.get("macd_signal", 0)
        macd_hist = analysis.get("macd_hist", 0)
        bb_upper = analysis.get("bb_upper", 0)
        bb_lower = analysis.get("bb_lower", 0)

        # === WEIGHTS ===
        ema_weight = self.rules.get("ema_cross_weight", 2)
        rsi_weight = self.rules.get("rsi_zone_weight", 2)
        volume_weight = self.rules.get("volume_weight", 1)
        sr_weight = self.rules.get("sr_weight", 2)
        momentum_weight = self.rules.get("momentum_weight", 1)
        macd_weight = self.rules.get("macd_weight", 2)
        bb_weight = self.rules.get("bb_weight", 1)

        max_score = ema_weight + rsi_weight + sr_weight + momentum_weight + macd_weight + bb_weight + volume_weight

        # === THRESHOLDS ===
        min_volume = self.rules.get("min_volume_ratio", 0.5)
        rsi_long_max = self.rules.get("rsi_long_max", 55)
        rsi_long_min = self.rules.get("rsi_long_min", 20)
        rsi_short_max = self.rules.get("rsi_short_max", 80)
        rsi_short_min = self.rules.get("rsi_short_min", 45)
        sr_proximity_pct = self.rules.get("sr_proximity_pct", 4.0)
        min_score = self.rules.get("min_score_for_signal", 4)
        min_atr_ratio = self.rules.get("min_atr_ratio", 0.5)

        # === VOLATILITY FILTER ===
        if atr_ratio < min_atr_ratio:
            info(f"📊 [SIGNAL] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return {
                "signal": "HOLD",
                "score": 0,
                "max_score": max_score,
                "reasons": [f"Low volatility (ATR {atr_ratio:.2f})"],
                "filters_passed": False,
                "details": {"atr_ratio": atr_ratio, "filter": "volatility"}
            }

        # === SCORING ===
        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []

        # 1. EMA Alignment (+2)
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            if ema9 > ema21:
                long_score += ema_weight
                long_reasons.append(f"EMA↑ ({ema_diff_pct:.1f}%) +{ema_weight}")
            elif ema9 < ema21:
                short_score += ema_weight
                short_reasons.append(f"EMA↓ ({ema_diff_pct:.1f}%) +{ema_weight}")

        # 2. Trend Alignment (optional)
        if self.rules.get("trend_alignment_required", False):
            if global_trend == "UP" and local_trend == "BULLISH":
                long_score += 1
                long_reasons.append("Trend↑ +1")
            elif global_trend == "DOWN" and local_trend == "BEARISH":
                short_score += 1
                short_reasons.append("Trend↓ +1")

        # 3. RSI Zone (+2)
        if rsi_long_min <= rsi <= rsi_long_max:
            long_score += rsi_weight
            long_reasons.append(f"RSI {rsi:.0f} +{rsi_weight}")
        if rsi_short_min <= rsi <= rsi_short_max:
            short_score += rsi_weight
            short_reasons.append(f"RSI {rsi:.0f} +{rsi_weight}")

        # 4. MACD Crossover (+2)
        if macd_line > macd_signal and macd_hist > 0:
            long_score += macd_weight
            long_reasons.append(f"MACD↑ +{macd_weight}")
        elif macd_line < macd_signal and macd_hist < 0:
            short_score += macd_weight
            short_reasons.append(f"MACD↓ +{macd_weight}")

        # 5. Bollinger Bands (+1)
        if bb_lower > 0 and current_price <= bb_lower * 1.005:
            long_score += bb_weight
            long_reasons.append(f"BB↓ +{bb_weight}")
        elif bb_upper > 0 and current_price >= bb_upper * 0.995:
            short_score += bb_weight
            short_reasons.append(f"BB↑ +{bb_weight}")

        # 6. S/R Proximity (+2)
        if current_price > 0 and support > 0 and resistance > 0:
            support_dist_pct = abs((current_price - support) / current_price * 100)
            resistance_dist_pct = abs((resistance - current_price) / current_price * 100)

            if support_dist_pct <= sr_proximity_pct:
                long_score += sr_weight
                long_reasons.append(f"S/R↓ ({support_dist_pct:.1f}%) +{sr_weight}")
            if resistance_dist_pct <= sr_proximity_pct:
                short_score += sr_weight
                short_reasons.append(f"S/R↑ ({resistance_dist_pct:.1f}%) +{sr_weight}")

        # 7. Momentum (+1)
        last_5_direction = analysis.get("last_5_direction", "MIXED")
        if last_5_direction in ["UP", "STRONG UP"]:
            long_score += momentum_weight
            long_reasons.append(f"Mom↑ +{momentum_weight}")
        elif last_5_direction in ["DOWN", "STRONG DOWN"]:
            short_score += momentum_weight
            short_reasons.append(f"Mom↓ +{momentum_weight}")

        # 8. Volume (added to winner only)
        volume_confirmed = volume_ratio >= min_volume

        # === DETERMINE SIGNAL ===
        signal = "HOLD"
        score = 0
        reasons = []

        if long_score >= min_score and long_score > short_score:
            signal = "BUY"
            score = long_score
            reasons = long_reasons
            if volume_confirmed:
                score += volume_weight
                reasons.append(f"Vol {volume_ratio:.1f}x +{volume_weight}")
        elif short_score >= min_score and short_score > long_score:
            signal = "SELL"
            score = short_score
            reasons = short_reasons
            if volume_confirmed:
                score += volume_weight
                reasons.append(f"Vol {volume_ratio:.1f}x +{volume_weight}")
        elif long_score >= min_score and short_score >= min_score:
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"CONFLICT L:{long_score} S:{short_score}"]
        else:
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"Low score L:{long_score} S:{short_score}"]

        details = {
            "long_score": long_score,
            "short_score": short_score,
            "long_reasons": long_reasons,
            "short_reasons": short_reasons,
            "min_score_required": min_score,
            "volume_confirmed": volume_confirmed,
            "atr_ratio": atr_ratio,
            "macd_hist": macd_hist,
            "support": support,
            "resistance": resistance
        }

        result = {
            "signal": signal,
            "score": score,
            "max_score": max_score,
            "reasons": reasons,
            "filters_passed": score >= min_score,
            "details": details
        }

        # Log
        if signal != "HOLD":
            info(f"📊 [SIGNAL] {signal} | {score}/{max_score} | {' '.join(reasons[:3])}")
        else:
            info(f"📊 [SIGNAL] HOLD | L:{long_score} S:{short_score} (need {min_score})")

        return result

    def should_close_position(self, analysis: dict, position: dict) -> dict:
        """Детерминированная проверка на закрытие позиции."""
        if not position:
            return {"should_close": False, "reason": "No position", "urgency": "low"}

        pos_type = position.get("type", "").upper()
        entry_price = float(position.get("entry", position.get("avgPrice", 0)))
        current_price = analysis.get("current_price", 0)
        rsi = analysis.get("rsi", 50)
        macd_hist = analysis.get("macd_hist", 0)

        if entry_price <= 0 or current_price <= 0:
            return {"should_close": False, "reason": "Invalid prices", "urgency": "low"}

        # Calculate P/L
        if pos_type == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        # === EXIT RULES ===

        # 1. RSI extreme
        if pos_type == "BUY" and rsi > 80:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} > 80", "urgency": "high"}
        if pos_type == "SELL" and rsi < 20:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} < 20", "urgency": "high"}

        # 2. Profit + RSI reversal
        if pnl_pct >= 2.0:
            if pos_type == "BUY" and rsi > 70:
                return {"should_close": True, "reason": f"+{pnl_pct:.1f}% RSI {rsi:.0f}", "urgency": "medium"}
            if pos_type == "SELL" and rsi < 30:
                return {"should_close": True, "reason": f"+{pnl_pct:.1f}% RSI {rsi:.0f}", "urgency": "medium"}

        # 3. MACD reversal against position
        if pos_type == "BUY" and macd_hist < 0 and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"MACD↓ + loss {pnl_pct:.1f}%", "urgency": "high"}
        if pos_type == "SELL" and macd_hist > 0 and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"MACD↑ + loss {pnl_pct:.1f}%", "urgency": "high"}

        # 4. Trend reversal + loss
        global_trend = analysis.get("global_trend", "N/A")
        local_trend = analysis.get("local_trend", "N/A")

        if pos_type == "BUY" and global_trend == "DOWN" and local_trend == "BEARISH" and pnl_pct < 0:
            return {"should_close": True, "reason": f"Trend↓ + loss {pnl_pct:.1f}%", "urgency": "high"}
        if pos_type == "SELL" and global_trend == "UP" and local_trend == "BULLISH" and pnl_pct < 0:
            return {"should_close": True, "reason": f"Trend↑ + loss {pnl_pct:.1f}%", "urgency": "high"}

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}


# Singleton
_generator = None

def get_signal_generator() -> SignalGenerator:
    global _generator
    if _generator is None:
        _generator = SignalGenerator()
    return _generator

def generate_signal(analysis: dict) -> dict:
    return get_signal_generator().generate_signal(analysis)

def should_close(analysis: dict, position: dict) -> dict:
    return get_signal_generator().should_close_position(analysis, position)
