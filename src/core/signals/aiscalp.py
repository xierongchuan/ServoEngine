"""AISCALP — генератор сигналов с HTF-трендом и awareness сессий."""

from typing import Any, Dict, Optional

from src.utils.logger import info

from .base import BaseSignalGenerator
from .utils import detect_rsi_divergence


class AiscalpSignalGenerator(BaseSignalGenerator):
    """AISCALP signal generator with HTF trend and session awareness."""

    def __init__(self, settings: Dict):
        super().__init__(settings)
        self.scoring = self.settings.get("signal_scoring", {})
        self.weights = self.scoring.get("weights", {})
        self.interactions = self.settings.get("interaction_rules", {})

    def pre_filter(self, analysis: Dict, htf_data: Dict, session_data: Dict) -> tuple:
        pf_cfg = self.settings.get("pre_filter", {})
        rsi = analysis.get("rsi", 50)
        htf_trend = htf_data.get("htf_trend", "NEUTRAL") if htf_data else "NEUTRAL"
        daily_bias = htf_data.get("daily_bias", "NEUTRAL") if htf_data else "NEUTRAL"

        rsi_neutral = pf_cfg.get("skip_rsi_neutral_zone", [46, 54])
        if pf_cfg.get("skip_no_htf_trend", True):
            if htf_trend == "NEUTRAL" and rsi_neutral[0] <= rsi <= rsi_neutral[1]:
                return False, f"RSI neutral ({rsi:.0f}) + no HTF trend"

        if pf_cfg.get("skip_no_htf_trend", True):
            if htf_trend == "NEUTRAL" and daily_bias == "NEUTRAL":
                return False, "No HTF trend and no daily bias"

        return True, "Passed"

    def generate(self, analysis: Dict, htf_data: Optional[Dict] = None,
                 session_data: Optional[Dict] = None, regime: Optional[Dict] = None) -> Dict:
        rsi = analysis.get("rsi", 50)
        current_price = analysis.get("current_price", 0)
        support = analysis.get("support", 0)
        resistance = analysis.get("resistance", 0)
        ema9 = analysis.get("ema9", 0)
        ema21 = analysis.get("ema21", 0)
        atr_ratio = analysis.get("atr_ratio", 1.0)
        macd_line = analysis.get("macd_line", 0)
        macd_signal_val = analysis.get("macd_signal", 0)
        macd_hist = analysis.get("macd_hist", 0)
        bb_upper = analysis.get("bb_upper", 0)
        bb_lower = analysis.get("bb_lower", 0)
        last_5_direction = analysis.get("last_5_direction", "MIXED")

        htf_trend = htf_data.get("htf_trend", "NEUTRAL") if htf_data else "NEUTRAL"
        macd_crossover = analysis.get("macd_crossover", "NONE")
        macd_crossover_confirmed = analysis.get("macd_crossover_confirmed", False)
        session_quality = session_data.get("session_quality", "MEDIUM") if session_data else "MEDIUM"
        session_data.get("is_overlap", False) if session_data else False
        quality_score_adj = session_data.get("quality_score_adj", 0) if session_data else 0

        w_htf = self.weights.get("htf_trend", 3)
        w_ema = self.weights.get("ema_cross", 2)
        w_rsi = self.weights.get("rsi_zone", 2)
        w_sr = self.weights.get("sr_proximity", 2)
        w_macd = self.weights.get("macd", 1)
        w_mom = self.weights.get("momentum", 1)
        w_bb = self.weights.get("bb", 1)

        max_score = w_htf + w_ema + w_rsi + w_sr + w_macd + w_mom + w_bb

        min_atr = self.scoring.get("min_atr_ratio", 0.3)
        rsi_long_zone = self.scoring.get("rsi_long_zone", [25, 55])
        rsi_short_zone = self.scoring.get("rsi_short_zone", [45, 75])
        sr_proximity_pct = self.scoring.get("sr_proximity_pct", 2.5)
        tier1_required = self.scoring.get("tier1_required", True)
        conflict_friction = self.scoring.get("conflict_friction_threshold", 4)

        if regime and regime.get("recommended_min_score"):
            min_score = regime["recommended_min_score"]
        else:
            min_score = self.scoring.get("min_score_for_signal", 5)

        if atr_ratio < min_atr:
            info(f"[AISCALP] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return self._hold_result(max_score, [f"Low volatility (ATR {atr_ratio:.2f})"],
                                     {"atr_ratio": atr_ratio, "filter": "volatility"}, regime)

        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []
        long_tier1 = False
        short_tier1 = False
        long_tier2 = False
        short_tier2 = False

        # --- TIER 1 ---
        htf_long = False
        htf_short = False
        if htf_trend == "BULLISH":
            long_score += w_htf
            long_reasons.append(f"HTF\u2191 +{w_htf}")
            long_tier1 = True
            htf_long = True
        elif htf_trend == "BEARISH":
            short_score += w_htf
            short_reasons.append(f"HTF\u2193 +{w_htf}")
            short_tier1 = True
            htf_short = True

        ema_long = False
        ema_short = False
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            if ema9 > ema21:
                long_score += w_ema
                long_reasons.append(f"EMA\u2191 ({ema_diff_pct:.1f}%) +{w_ema}")
                long_tier1 = True
                ema_long = True
            elif ema9 < ema21:
                short_score += w_ema
                short_reasons.append(f"EMA\u2193 ({ema_diff_pct:.1f}%) +{w_ema}")
                short_tier1 = True
                ema_short = True

        # --- TIER 2 ---
        rsi_long = False
        rsi_short = False
        if rsi_long_zone[0] <= rsi <= rsi_long_zone[1]:
            long_score += w_rsi
            long_reasons.append(f"RSI {rsi:.0f} +{w_rsi}")
            long_tier2 = True
            rsi_long = True
        if rsi_short_zone[0] <= rsi <= rsi_short_zone[1]:
            short_score += w_rsi
            short_reasons.append(f"RSI {rsi:.0f} +{w_rsi}")
            short_tier2 = True
            rsi_short = True

        sr_long = False
        sr_short = False
        if current_price > 0 and support > 0 and resistance > 0:
            support_dist_pct = abs((current_price - support) / current_price * 100)
            resistance_dist_pct = abs((resistance - current_price) / current_price * 100)
            sr_spread_pct = (resistance - support) / current_price * 100

            if sr_spread_pct >= 1.0:
                if support_dist_pct <= sr_proximity_pct:
                    long_score += w_sr
                    long_reasons.append(f"S/R\u2193 ({support_dist_pct:.1f}%) +{w_sr}")
                    long_tier2 = True
                    sr_long = True
                if resistance_dist_pct <= sr_proximity_pct:
                    short_score += w_sr
                    short_reasons.append(f"S/R\u2191 ({resistance_dist_pct:.1f}%) +{w_sr}")
                    short_tier2 = True
                    sr_short = True

        # --- TIER 3 ---
        macd_long = False
        macd_short = False
        if macd_crossover != "NONE":
            if macd_crossover == "BULLISH":
                long_score += w_macd
                long_reasons.append(f"MACD\u2191 {'\u2713' if macd_crossover_confirmed else '\u25cb'} +{w_macd}")
                macd_long = True
            elif macd_crossover == "BEARISH":
                short_score += w_macd
                short_reasons.append(f"MACD\u2193 {'\u2713' if macd_crossover_confirmed else '\u25cb'} +{w_macd}")
                macd_short = True
        else:
            if macd_line > macd_signal_val and macd_hist > 0:
                long_score += w_macd
                long_reasons.append(f"MACD\u2191 +{w_macd}")
                macd_long = True
            elif macd_line < macd_signal_val and macd_hist < 0:
                short_score += w_macd
                short_reasons.append(f"MACD\u2193 +{w_macd}")
                macd_short = True

        momentum_long = False
        momentum_short = False
        if last_5_direction in ("UP", "STRONG UP"):
            long_score += w_mom
            long_reasons.append(f"Mom\u2191 +{w_mom}")
            momentum_long = True
        elif last_5_direction in ("DOWN", "STRONG DOWN"):
            short_score += w_mom
            short_reasons.append(f"Mom\u2193 +{w_mom}")
            momentum_short = True

        bb_long = False
        bb_short = False
        if bb_lower > 0 and current_price <= bb_lower * 1.005:
            long_score += w_bb
            long_reasons.append(f"BB\u2193 +{w_bb}")
            bb_long = True
        elif bb_upper > 0 and current_price >= bb_upper * 0.995:
            short_score += w_bb
            short_reasons.append(f"BB\u2191 +{w_bb}")
            bb_short = True

        # === INTERACTIONS ===
        long_interactions = 0
        short_interactions = 0
        long_int_reasons = []
        short_int_reasons = []

        htf_ltf_bonus = self.interactions.get("htf_ltf_confluence_bonus", 2)
        if htf_long and ema_long:
            long_interactions += htf_ltf_bonus
            long_int_reasons.append(f"HTF+LTF confluence +{htf_ltf_bonus}")
        if htf_short and ema_short:
            short_interactions += htf_ltf_bonus
            short_int_reasons.append(f"HTF+LTF confluence +{htf_ltf_bonus}")

        ema_macd_bonus = self.interactions.get("ema_macd_confluence_bonus", 1)
        if ema_long and macd_long:
            long_interactions += ema_macd_bonus
            long_int_reasons.append(f"EMA+MACD confluence +{ema_macd_bonus}")
        if ema_short and macd_short:
            short_interactions += ema_macd_bonus
            short_int_reasons.append(f"EMA+MACD confluence +{ema_macd_bonus}")

        reversal_bonus = self.interactions.get("reversal_confluence_bonus", 2)
        if rsi_long and sr_long and bb_long:
            long_interactions += reversal_bonus
            long_int_reasons.append(f"Reversal confluence +{reversal_bonus}")
        if rsi_short and sr_short and bb_short:
            short_interactions += reversal_bonus
            short_int_reasons.append(f"Reversal confluence +{reversal_bonus}")

        burst_bonus = self.interactions.get("momentum_burst_bonus", 1)
        if momentum_long and ema_long:
            long_interactions += burst_bonus
            long_int_reasons.append(f"Momentum burst +{burst_bonus}")
        if momentum_short and ema_short:
            short_interactions += burst_bonus
            short_int_reasons.append(f"Momentum burst +{burst_bonus}")

        counter_penalty = self.interactions.get("counter_htf_trend_penalty", -3)
        if htf_trend == "BEARISH" and long_tier1 and not htf_long:
            long_interactions += counter_penalty
            long_int_reasons.append(f"Counter-HTF {counter_penalty}")
        if htf_trend == "BULLISH" and short_tier1 and not htf_short:
            short_interactions += counter_penalty
            short_int_reasons.append(f"Counter-HTF {counter_penalty}")

        div_penalty = self.interactions.get("rsi_divergence_penalty", -2)
        close_prices = analysis.get("close_prices", [])
        rsi_values = analysis.get("rsi_values", [])
        if len(close_prices) >= 20 and len(rsi_values) >= 20:
            bearish_div, bullish_div = detect_rsi_divergence(close_prices[-20:], rsi_values[-20:])
            if bearish_div:
                long_interactions += div_penalty
                long_int_reasons.append(f"RSI bearish div {div_penalty}")
            if bullish_div:
                short_interactions += div_penalty
                short_int_reasons.append(f"RSI bullish div {div_penalty}")

        long_score += long_interactions
        short_score += short_interactions
        long_reasons.extend(long_int_reasons)
        short_reasons.extend(short_int_reasons)

        if quality_score_adj != 0:
            long_score += quality_score_adj
            short_score += quality_score_adj
            adj_label = f"Session {quality_score_adj:+d}"
            long_reasons.append(adj_label)
            short_reasons.append(adj_label)

        conflicting = False
        if long_score > short_score and short_score >= conflict_friction:
            long_score -= 1
            long_reasons.append(f"Conflict friction -1 (short={short_score})")
            conflicting = True
        elif short_score > long_score and long_score >= conflict_friction:
            short_score -= 1
            short_reasons.append(f"Conflict friction -1 (long={long_score})")
            conflicting = True

        signal = "HOLD"
        score = 0
        reasons = []

        if long_score >= min_score and long_score > short_score:
            if tier1_required and not long_tier1:
                signal = "HOLD"
                score = long_score
                reasons = [f"No Tier1 direction (L:{long_score} needs HTF/EMA)"]
            else:
                signal = "BUY"
                score = long_score
                reasons = long_reasons
        elif short_score >= min_score and short_score > long_score:
            if tier1_required and not short_tier1:
                signal = "HOLD"
                score = short_score
                reasons = [f"No Tier1 direction (S:{short_score} needs HTF/EMA)"]
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
            "atr_ratio": atr_ratio,
            "macd_hist": macd_hist,
            "support": support,
            "resistance": resistance,
            "long_tier1": long_tier1,
            "short_tier1": short_tier1,
            "long_tier2": long_tier2,
            "short_tier2": short_tier2,
            "conflicting": conflicting,
            "htf_trend": htf_trend,
            "session_quality": session_quality,
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
        htf_label = htf_trend[:1] if htf_trend != "NEUTRAL" else "N"
        sess_label = session_quality[:1]
        if signal != "HOLD":
            info(f"[AISCALP] {signal} | {score}/{max_score} Q:{quality:.2f} [{regime_label}] HTF:{htf_label} Sess:{sess_label} | {' '.join(reasons[:3])}")
        else:
            info(f"[AISCALP] HOLD | L:{long_score} S:{short_score} (need {min_score}) [{regime_label}] HTF:{htf_label}")

        return result

    def should_close(self, analysis: Dict, position: Any, htf_data: Optional[Dict] = None, **kwargs) -> Dict:
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

        macd_exit_pnl = self.scoring.get("macd_exit_pnl_threshold", -1.5)

        if pos_type == "BUY" and rsi > 80:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} > 80", "urgency": "high"}
        if pos_type == "SELL" and rsi < 20:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} < 20", "urgency": "high"}

        if pnl_pct >= 2.0:
            if pos_type == "BUY" and rsi > 70:
                return {"should_close": True, "reason": f"+{pnl_pct:.1f}% RSI {rsi:.0f}", "urgency": "medium"}
            if pos_type == "SELL" and rsi < 30:
                return {"should_close": True, "reason": f"+{pnl_pct:.1f}% RSI {rsi:.0f}", "urgency": "medium"}

        if pos_type == "BUY" and macd_hist < 0 and pnl_pct < macd_exit_pnl:
            return {"should_close": True, "reason": f"MACD\u2193 + loss {pnl_pct:.1f}%", "urgency": "high"}
        if pos_type == "SELL" and macd_hist > 0 and pnl_pct < macd_exit_pnl:
            return {"should_close": True, "reason": f"MACD\u2191 + loss {pnl_pct:.1f}%", "urgency": "high"}

        if htf_data:
            htf_trend = htf_data.get("htf_trend", "NEUTRAL")
            if pos_type == "BUY" and htf_trend == "BEARISH" and pnl_pct < -0.5:
                return {"should_close": True, "reason": f"HTF\u2193 reversal + loss {pnl_pct:.1f}%", "urgency": "high"}
            if pos_type == "SELL" and htf_trend == "BULLISH" and pnl_pct < -0.5:
                return {"should_close": True, "reason": f"HTF\u2191 reversal + loss {pnl_pct:.1f}%", "urgency": "high"}

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
