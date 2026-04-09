"""HYBRID — детерминированный генератор сигналов с тирной структурой."""

from typing import Any, Dict, Optional

from src.utils.logger import info

from .base import BaseSignalGenerator
from .utils import detect_rsi_divergence


class HybridSignalGenerator(BaseSignalGenerator):
    """Детерминированный генератор сигналов с тирной структурой и взаимодействием индикаторов."""

    def __init__(self, settings: Dict):
        super().__init__(settings)
        self.rules = self.settings.get("signal_rules", {})
        self.interactions = self.settings.get("interaction_rules", {})

    def generate(self, analysis: Dict, regime: Optional[Dict] = None) -> Dict:
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
        atr_ratio = analysis.get("atr_ratio", 1.0)
        macd_line = analysis.get("macd_line", 0)
        macd_signal = analysis.get("macd_signal", 0)
        macd_hist = analysis.get("macd_hist", 0)
        bb_upper = analysis.get("bb_upper", 0)
        bb_lower = analysis.get("bb_lower", 0)

        # === WEIGHTS FROM CONFIG ===
        weights = self.rules.get("weights", {})
        ema_weight = weights.get("ema_cross", 2)
        rsi_weight = weights.get("rsi_zone", 2)
        volume_weight = weights.get("volume", 1)
        sr_weight = weights.get("sr", 2)
        momentum_weight = weights.get("momentum", 1)
        macd_weight = weights.get("macd", 1)
        bb_weight = weights.get("bb", 1)

        max_score = ema_weight + rsi_weight + sr_weight + momentum_weight + macd_weight + bb_weight + volume_weight

        # === THRESHOLDS ===
        min_volume = self.rules.get("min_volume_ratio", 0.5)
        rsi_long_max = self.rules.get("rsi_long_max", 43)
        rsi_long_min = self.rules.get("rsi_long_min", 20)
        rsi_short_max = self.rules.get("rsi_short_max", 80)
        rsi_short_min = self.rules.get("rsi_short_min", 57)
        sr_proximity_pct = self.rules.get("sr_proximity_pct", 2.0)
        min_atr_ratio = self.rules.get("min_atr_ratio", 0.5)
        tier1_required = self.rules.get("tier1_required", True)
        conflict_friction = self.rules.get("conflict_friction_threshold", 3)

        if regime and regime.get("recommended_min_score"):
            min_score = regime.get("recommended_min_score")
        else:
            min_score = self.rules.get("min_score_for_signal", 5)

        # === VOLATILITY FILTER ===
        if atr_ratio < min_atr_ratio:
            info(f"[SIGNAL] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return self._hold_result(max_score, [f"Low volatility (ATR {atr_ratio:.2f})"],
                                     {"atr_ratio": atr_ratio, "filter": "volatility"}, regime)

        # === VOLUME HARD FILTER ===
        if volume_ratio < min_volume:
            info(f"[SIGNAL] HOLD | Low volume ({volume_ratio:.2f}x)")
            return self._hold_result(max_score, [f"Low volume ({volume_ratio:.2f}x)"],
                                     {"volume_ratio": volume_ratio, "filter": "volume"}, regime)

        # === TIERED SCORING ===
        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []
        long_tier1 = False
        short_tier1 = False
        long_tier2 = False
        short_tier2 = False

        # --- TIER 1: Direction ---
        ema_long = False
        ema_short = False
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            if ema9 > ema21:
                long_score += ema_weight
                long_reasons.append(f"EMA\u2191 ({ema_diff_pct:.1f}%) +{ema_weight}")
                long_tier1 = True
                ema_long = True
            elif ema9 < ema21:
                short_score += ema_weight
                short_reasons.append(f"EMA\u2193 ({ema_diff_pct:.1f}%) +{ema_weight}")
                short_tier1 = True
                ema_short = True

        macd_long = False
        macd_short = False
        if macd_line > macd_signal and macd_hist > 0:
            long_score += macd_weight
            long_reasons.append(f"MACD\u2191 +{macd_weight}")
            long_tier1 = True
            macd_long = True
        elif macd_line < macd_signal and macd_hist < 0:
            short_score += macd_weight
            short_reasons.append(f"MACD\u2193 +{macd_weight}")
            short_tier1 = True
            macd_short = True

        # --- TIER 2: Confirmation ---
        rsi_long = False
        rsi_short = False
        if rsi_long_min <= rsi <= rsi_long_max:
            long_score += rsi_weight
            long_reasons.append(f"RSI {rsi:.0f} +{rsi_weight}")
            long_tier2 = True
            rsi_long = True
        if rsi_short_min <= rsi <= rsi_short_max:
            short_score += rsi_weight
            short_reasons.append(f"RSI {rsi:.0f} +{rsi_weight}")
            short_tier2 = True
            rsi_short = True

        sr_long = False
        sr_short = False
        if current_price > 0 and support > 0 and resistance > 0:
            support_dist_pct = abs((current_price - support) / current_price * 100)
            resistance_dist_pct = abs((resistance - current_price) / current_price * 100)
            sr_spread_pct = (resistance - support) / current_price * 100 if current_price > 0 else 0

            if sr_spread_pct >= 1.0:
                if support_dist_pct <= sr_proximity_pct:
                    long_score += sr_weight
                    long_reasons.append(f"S/R\u2193 ({support_dist_pct:.1f}%) +{sr_weight}")
                    long_tier2 = True
                    sr_long = True
                if resistance_dist_pct <= sr_proximity_pct:
                    short_score += sr_weight
                    short_reasons.append(f"S/R\u2191 ({resistance_dist_pct:.1f}%) +{sr_weight}")
                    short_tier2 = True
                    sr_short = True

        # --- TIER 3: Support ---
        momentum_long = False
        momentum_short = False
        last_5_direction = analysis.get("last_5_direction", "MIXED")
        if last_5_direction in ["UP", "STRONG UP"]:
            long_score += momentum_weight
            long_reasons.append(f"Mom\u2191 +{momentum_weight}")
            momentum_long = True
        elif last_5_direction in ["DOWN", "STRONG DOWN"]:
            short_score += momentum_weight
            short_reasons.append(f"Mom\u2193 +{momentum_weight}")
            momentum_short = True

        bb_long = False
        bb_short = False
        if bb_lower > 0 and current_price <= bb_lower * 1.005:
            long_score += bb_weight
            long_reasons.append(f"BB\u2193 +{bb_weight}")
            bb_long = True
        elif bb_upper > 0 and current_price >= bb_upper * 0.995:
            short_score += bb_weight
            short_reasons.append(f"BB\u2191 +{bb_weight}")
            bb_short = True

        volume_confirmed = volume_ratio >= 0.8
        if volume_confirmed:
            if momentum_long or ema_long:
                long_score += volume_weight
                long_reasons.append(f"Vol {volume_ratio:.1f}x +{volume_weight}")
            if momentum_short or ema_short:
                short_score += volume_weight
                short_reasons.append(f"Vol {volume_ratio:.1f}x +{volume_weight}")

        if self.rules.get("trend_alignment_required", False):
            if global_trend == "UP" and local_trend == "BULLISH":
                long_score += 1
                long_reasons.append("Trend\u2191 +1")
            elif global_trend == "DOWN" and local_trend == "BEARISH":
                short_score += 1
                short_reasons.append("Trend\u2193 +1")

        # === INTERACTION BONUSES/PENALTIES ===
        long_interactions = 0
        short_interactions = 0
        long_int_reasons = []
        short_int_reasons = []

        confluence_bonus = self.interactions.get("ema_macd_confluence_bonus", 1)
        if ema_long and macd_long:
            long_interactions += confluence_bonus
            long_int_reasons.append(f"EMA+MACD confluence +{confluence_bonus}")
        if ema_short and macd_short:
            short_interactions += confluence_bonus
            short_int_reasons.append(f"EMA+MACD confluence +{confluence_bonus}")

        reversal_bonus = self.interactions.get("reversal_confluence_bonus", 2)
        if rsi_long and sr_long and bb_long:
            long_interactions += reversal_bonus
            long_int_reasons.append(f"Reversal confluence +{reversal_bonus}")
        if rsi_short and sr_short and bb_short:
            short_interactions += reversal_bonus
            short_int_reasons.append(f"Reversal confluence +{reversal_bonus}")

        burst_bonus = self.interactions.get("momentum_burst_bonus", 1)
        if volume_ratio >= 1.5 and momentum_long and ema_long:
            long_interactions += burst_bonus
            long_int_reasons.append(f"Momentum burst +{burst_bonus}")
        if volume_ratio >= 1.5 and momentum_short and ema_short:
            short_interactions += burst_bonus
            short_int_reasons.append(f"Momentum burst +{burst_bonus}")

        div_penalty = self.interactions.get("rsi_divergence_penalty", -2)
        close_prices = analysis.get("close_prices", [])
        rsi_values = analysis.get("rsi_values", [])
        if len(close_prices) >= 20 and len(rsi_values) >= 20:
            bearish_div, bullish_div = detect_rsi_divergence(close_prices[-20:], rsi_values[-20:])
            if bearish_div:
                long_interactions += div_penalty
                long_int_reasons.append(f"RSI bearish divergence {div_penalty}")
            if bullish_div:
                short_interactions += div_penalty
                short_int_reasons.append(f"RSI bullish divergence {div_penalty}")

        long_score += long_interactions
        short_score += short_interactions
        long_reasons.extend(long_int_reasons)
        short_reasons.extend(short_int_reasons)

        # === CONFLICT FRICTION ===
        conflicting = False
        if long_score > short_score and short_score >= conflict_friction:
            long_score -= 1
            long_reasons.append(f"Conflict friction -1 (short={short_score})")
            conflicting = True
        elif short_score > long_score and long_score >= conflict_friction:
            short_score -= 1
            short_reasons.append(f"Conflict friction -1 (long={long_score})")
            conflicting = True

        # === DETERMINE SIGNAL ===
        signal = "HOLD"
        score = 0
        reasons = []

        if long_score >= min_score and long_score > short_score:
            if tier1_required and not long_tier1:
                signal = "HOLD"
                score = long_score
                reasons = [f"No Tier1 direction (L:{long_score} needs EMA/MACD)"]
            else:
                signal = "BUY"
                score = long_score
                reasons = long_reasons
        elif short_score >= min_score and short_score > long_score:
            if tier1_required and not short_tier1:
                signal = "HOLD"
                score = short_score
                reasons = [f"No Tier1 direction (S:{short_score} needs EMA/MACD)"]
            else:
                signal = "SELL"
                score = short_score
                reasons = short_reasons
        elif long_score >= min_score and short_score >= min_score:
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"CONFLICT L:{long_score} S:{short_score}"]
        else:
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"Low score L:{long_score} S:{short_score} (need {min_score})"]

        # === QUALITY & CONFIDENCE ===
        if signal != "HOLD" and max_score > min_score:
            raw_quality = (score - min_score) / (max_score - min_score)
            quality = max(0.1, min(1.0, 0.1 + raw_quality * 0.9))
        else:
            quality = 0.0

        confidence = self._map_quality(quality, signal != "HOLD")

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
            "resistance": resistance,
            "long_tier1": long_tier1,
            "short_tier1": short_tier1,
            "long_tier2": long_tier2,
            "short_tier2": short_tier2,
            "conflicting": conflicting,
            "interactions": {
                "long": long_int_reasons,
                "short": short_int_reasons,
            },
        }

        result = {
            "signal": signal,
            "score": score,
            "max_score": max_score,
            "quality": quality,
            "confidence": confidence,
            "reasons": reasons,
            "filters_passed": score >= min_score,
            "details": details,
            "regime": regime.get("regime", "UNKNOWN") if regime else "NO_REGIME",
        }

        regime_label = regime.get("regime", "?") if regime else "?"
        if signal != "HOLD":
            info(f"[SIGNAL] {signal} | {score}/{max_score} Q:{quality:.2f} [{regime_label}] | {' '.join(reasons[:3])}")
        else:
            info(f"[SIGNAL] HOLD | L:{long_score} S:{short_score} (need {min_score}) [{regime_label}]")

        return result

    def should_close(self, analysis: Dict, position: Any, **kwargs) -> Dict:
        if not position:
            return {"should_close": False, "reason": "No position", "urgency": "low"}

        if hasattr(position, 'entry_price'):
            pos_type = "BUY" if position.is_long else "SELL"
            entry_price = float(position.entry_price)
        else:
            pos_type = position.get("type", "").upper()
            entry_price = float(position.get("entry", position.get("avgPrice", 0)))

        current_price = analysis.get("current_price", 0)
        rsi = analysis.get("rsi", 50)
        macd_hist = analysis.get("macd_hist", 0)

        if entry_price <= 0 or current_price <= 0:
            return {"should_close": False, "reason": "Invalid prices", "urgency": "low"}

        if pos_type == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        if pos_type == "BUY" and rsi > 80:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} > 80", "urgency": "high"}
        if pos_type == "SELL" and rsi < 20:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} < 20", "urgency": "high"}

        if pnl_pct >= 2.0:
            if pos_type == "BUY" and rsi > 70:
                return {"should_close": True, "reason": f"+{pnl_pct:.1f}% RSI {rsi:.0f}", "urgency": "medium"}
            if pos_type == "SELL" and rsi < 30:
                return {"should_close": True, "reason": f"+{pnl_pct:.1f}% RSI {rsi:.0f}", "urgency": "medium"}

        macd_exit_pnl = self.rules.get("macd_exit_pnl_threshold", -1.5)
        if pos_type == "BUY" and macd_hist < 0 and pnl_pct < macd_exit_pnl:
            return {"should_close": True, "reason": f"MACD\u2193 + loss {pnl_pct:.1f}%", "urgency": "high"}
        if pos_type == "SELL" and macd_hist > 0 and pnl_pct < macd_exit_pnl:
            return {"should_close": True, "reason": f"MACD\u2191 + loss {pnl_pct:.1f}%", "urgency": "high"}

        global_trend = analysis.get("global_trend", "N/A")
        local_trend = analysis.get("local_trend", "N/A")
        if pos_type == "BUY" and global_trend == "DOWN" and local_trend == "BEARISH" and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"Trend\u2193 + loss {pnl_pct:.1f}%", "urgency": "high"}
        if pos_type == "SELL" and global_trend == "UP" and local_trend == "BULLISH" and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"Trend\u2191 + loss {pnl_pct:.1f}%", "urgency": "high"}

        if pnl_pct >= 3.0:
            if pos_type == "BUY" and (rsi > 65 or macd_hist < 0):
                return {"should_close": True, "reason": f"Trail +{pnl_pct:.1f}% momentum fading", "urgency": "medium"}
            if pos_type == "SELL" and (rsi < 35 or macd_hist > 0):
                return {"should_close": True, "reason": f"Trail +{pnl_pct:.1f}% momentum fading", "urgency": "medium"}

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}
