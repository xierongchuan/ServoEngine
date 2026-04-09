"""SCALP — детерминированный генератор сигналов для скальпинга."""

from typing import Any, Dict, Optional

from src.utils.logger import info

from .base import BaseSignalGenerator


class ScalpSignalGenerator(BaseSignalGenerator):
    """Deterministic signal generator tuned for 1m scalping."""

    def __init__(self, settings: Dict):
        super().__init__(settings)
        self.rules = self.settings.get("signal_rules", {})
        self.interactions = self.settings.get("interaction_rules", {})
        self.regime_overrides = self.settings.get("regime_overrides", {})

    def generate(self, indicators: Dict, regime: Optional[Dict] = None,
                 ob_imbalance: float = 0.0, **kwargs) -> Dict:
        ema_fast = indicators.get("ema_fast", 0)
        ema_med = indicators.get("ema_med", 0)
        indicators.get("ema_macro", 0)
        rsi = indicators.get("rsi", 50)
        volume_ratio = indicators.get("volume_ratio", 1.0)
        current_price = indicators.get("current_price", 0)
        vwap = indicators.get("vwap", 0)
        macd_hist = indicators.get("macd_hist", 0)
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        momentum_dir = indicators.get("momentum_dir", "MIXED")
        atr_ratio = indicators.get("atr_ratio", 1.0)

        weights = self._get_weights(regime)
        ema_w = weights["ema_weight"]
        rsi_w = weights["rsi_weight"]
        volume_w = weights["volume_weight"]
        momentum_w = weights.get("momentum_weight", 1)
        vwap_w = weights.get("vwap_weight", 1)
        ob_w = weights.get("ob_imbalance_weight", 1)
        macd_w = weights.get("macd_weight", 1)
        bb_w = weights.get("bb_weight", 1)
        cvd_w = weights.get("cvd_weight", 1)

        max_score = ema_w + momentum_w + rsi_w + vwap_w + volume_w + ob_w + macd_w + bb_w + cvd_w
        min_score = weights.get("min_score", self.rules.get("min_score_for_signal", 4))
        tier1_required = self.rules.get("tier1_required", True)
        conflict_friction = self.rules.get("conflict_friction_threshold", 2)

        rsi_long_zone = self.rules.get("rsi_long_zone", [25, 40])
        rsi_short_zone = self.rules.get("rsi_short_zone", [60, 75])
        ob_threshold = self.rules.get("ob_imbalance_threshold", 0.3)
        chop_threshold = self.rules.get("choppiness_threshold", 61.8)

        choppiness = indicators.get("choppiness", 50.0)
        is_choppy = choppiness > chop_threshold
        if is_choppy:
            regime_label_chk = regime.get("regime", "") if regime else ""
            if regime_label_chk != "RANGING":
                return {
                    "signal": "HOLD",
                    "score": 0,
                    "max_score": max_score,
                    "quality": 0.0,
                    "confidence": 0.0,
                    "pattern": "none",
                    "reasons": [f"Choppy ({choppiness:.1f} > {chop_threshold})"],
                    "details": {
                        "long_score": 0, "short_score": 0,
                        "min_score_required": min_score,
                        "conflicting": False,
                    },
                    "regime": regime.get("regime", "?") if regime else "?",
                }

        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []
        long_tier1 = False
        short_tier1 = False

        # --- TIER 1 ---
        ema_long = ema_fast > ema_med > 0
        ema_short = ema_fast < ema_med and ema_med > 0
        if ema_long:
            long_score += ema_w
            long_reasons.append(f"EMA\u2191 +{ema_w}")
            long_tier1 = True
        if ema_short:
            short_score += ema_w
            short_reasons.append(f"EMA\u2193 +{ema_w}")
            short_tier1 = True

        if momentum_dir == "UP":
            long_score += momentum_w
            long_reasons.append(f"Mom\u2191 +{momentum_w}")
            long_tier1 = True
        elif momentum_dir == "DOWN":
            short_score += momentum_w
            short_reasons.append(f"Mom\u2193 +{momentum_w}")
            short_tier1 = True

        # --- TIER 2 ---
        rsi_long = rsi_long_zone[0] <= rsi <= rsi_long_zone[1]
        rsi_short = rsi_short_zone[0] <= rsi <= rsi_short_zone[1]
        if rsi_long:
            long_score += rsi_w
            long_reasons.append(f"RSI {rsi:.0f} +{rsi_w}")
        if rsi_short:
            short_score += rsi_w
            short_reasons.append(f"RSI {rsi:.0f} +{rsi_w}")

        if vwap > 0 and current_price > 0:
            vwap_dist_pct = (current_price - vwap) / vwap * 100
            if current_price > vwap and abs(vwap_dist_pct) < 0.5:
                long_score += vwap_w
                long_reasons.append(f"VWAP\u2191 +{vwap_w}")
            elif current_price < vwap and abs(vwap_dist_pct) < 0.5:
                short_score += vwap_w
                short_reasons.append(f"VWAP\u2193 +{vwap_w}")

        # --- TIER 3 ---
        if volume_ratio >= 1.3:
            if long_score > short_score:
                long_score += volume_w
                long_reasons.append(f"Vol {volume_ratio:.1f}x +{volume_w}")
            elif short_score > long_score:
                short_score += volume_w
                short_reasons.append(f"Vol {volume_ratio:.1f}x +{volume_w}")

        if abs(ob_imbalance) >= ob_threshold:
            if ob_imbalance > 0:
                long_score += ob_w
                long_reasons.append(f"OB\u2191 {ob_imbalance:.2f} +{ob_w}")
            else:
                short_score += ob_w
                short_reasons.append(f"OB\u2193 {ob_imbalance:.2f} +{ob_w}")

        macd_crossover = indicators.get("macd_crossover", "NONE")
        if macd_hist > 0:
            long_score += macd_w
            long_reasons.append(f"MACD\u2191 +{macd_w}")
            if macd_crossover == "BULLISH":
                long_reasons.append("MACDx\u2191")
        elif macd_hist < 0:
            short_score += macd_w
            short_reasons.append(f"MACD\u2193 +{macd_w}")
            if macd_crossover == "BEARISH":
                short_reasons.append("MACDx\u2193")

        if bb_lower > 0 and current_price <= bb_lower * 1.005:
            long_score += bb_w
            long_reasons.append(f"BB\u2193 +{bb_w}")
        elif bb_upper > 0 and current_price >= bb_upper * 0.995:
            short_score += bb_w
            short_reasons.append(f"BB\u2191 +{bb_w}")

        cvd_trend = indicators.get("cvd_trend", "FLAT")
        if cvd_trend == "RISING":
            long_score += cvd_w
            long_reasons.append(f"CVD\u2191 +{cvd_w}")
        elif cvd_trend == "FALLING":
            short_score += cvd_w
            short_reasons.append(f"CVD\u2193 +{cvd_w}")

        # === INTERACTION BONUSES ===
        long_int = 0
        short_int = 0

        burst_bonus = self.interactions.get("momentum_burst_bonus", 2)
        if ema_long and volume_ratio >= 1.5 and momentum_dir == "UP":
            long_int += burst_bonus
            long_reasons.append(f"MomBurst +{burst_bonus}")
        if ema_short and volume_ratio >= 1.5 and momentum_dir == "DOWN":
            short_int += burst_bonus
            short_reasons.append(f"MomBurst +{burst_bonus}")

        vwap_bonus = self.interactions.get("vwap_bounce_bonus", 1)
        if vwap > 0 and current_price > 0:
            near_vwap = abs(current_price - vwap) / vwap < 0.002
            if near_vwap and rsi_long and ema_long:
                long_int += vwap_bonus
                long_reasons.append(f"VWAPBounce +{vwap_bonus}")
            if near_vwap and rsi_short and ema_short:
                short_int += vwap_bonus
                short_reasons.append(f"VWAPBounce +{vwap_bonus}")

        ob_conf_bonus = self.interactions.get("ob_confluence_bonus", 1)
        if ob_imbalance > ob_threshold and ema_long and volume_ratio >= 1.0:
            long_int += ob_conf_bonus
            long_reasons.append(f"OBConfl +{ob_conf_bonus}")
        if ob_imbalance < -ob_threshold and ema_short and volume_ratio >= 1.0:
            short_int += ob_conf_bonus
            short_reasons.append(f"OBConfl +{ob_conf_bonus}")

        counter_pen = self.interactions.get("counter_momentum_penalty", -2)
        if ema_long and rsi > 70:
            long_int += counter_pen
            long_reasons.append(f"CounterMom {counter_pen}")
        if ema_short and rsi < 30:
            short_int += counter_pen
            short_reasons.append(f"CounterMom {counter_pen}")

        cvd_div_pen = self.interactions.get("cvd_divergence_penalty", -1)
        if momentum_dir == "UP" and cvd_trend == "FALLING":
            long_int += cvd_div_pen
            long_reasons.append(f"CVDdiv {cvd_div_pen}")
        elif momentum_dir == "DOWN" and cvd_trend == "RISING":
            short_int += cvd_div_pen
            short_reasons.append(f"CVDdiv {cvd_div_pen}")

        spike_pen = self.interactions.get("spike_penalty", -1)
        if atr_ratio > 2.0:
            if long_score > short_score:
                long_int += spike_pen
                long_reasons.append(f"ATRSpike {spike_pen}")
            elif short_score > long_score:
                short_int += spike_pen
                short_reasons.append(f"ATRSpike {spike_pen}")

        long_score += long_int
        short_score += short_int

        conflicting = False
        if long_score > short_score and short_score >= conflict_friction:
            long_score -= 1
            long_reasons.append("Friction -1")
            conflicting = True
        elif short_score > long_score and long_score >= conflict_friction:
            short_score -= 1
            short_reasons.append("Friction -1")
            conflicting = True

        signal = "HOLD"
        score = 0
        reasons = []
        pattern = "none"

        if long_score >= min_score and long_score > short_score:
            if tier1_required and not long_tier1:
                signal = "HOLD"
                score = long_score
                reasons = [f"No T1 (L:{long_score})"]
            else:
                signal = "BUY"
                score = long_score
                reasons = long_reasons
                pattern = self._detect_pattern(indicators, "BUY", regime)
        elif short_score >= min_score and short_score > long_score:
            if tier1_required and not short_tier1:
                signal = "HOLD"
                score = short_score
                reasons = [f"No T1 (S:{short_score})"]
            else:
                signal = "SELL"
                score = short_score
                reasons = short_reasons
                pattern = self._detect_pattern(indicators, "SELL", regime)
        elif long_score >= min_score and short_score >= min_score:
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"CONFLICT L:{long_score} S:{short_score}"]
        else:
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"Low L:{long_score} S:{short_score} (need {min_score})"]

        if signal != "HOLD" and max_score > min_score:
            quality = max(0.0, min(1.0, (score - min_score) / (max_score - min_score)))
        else:
            quality = 0.0

        confidence = self._map_quality(quality, signal != "HOLD")

        regime_label = regime.get("regime", "?") if regime else "?"

        result = {
            "signal": signal,
            "score": score,
            "max_score": max_score,
            "quality": quality,
            "confidence": confidence,
            "pattern": pattern,
            "reasons": reasons,
            "details": {
                "long_score": long_score,
                "short_score": short_score,
                "min_score_required": min_score,
                "conflicting": conflicting,
            },
            "regime": regime_label,
        }

        if signal != "HOLD":
            info(f"[SCALP-SIG] {signal} | {score}/{max_score} Q:{quality:.2f} "
                 f"[{regime_label}] pat:{pattern} | {' '.join(reasons[:4])}")

        return result

    def should_close(self, indicators: Dict, position: Any, **kwargs) -> Dict:
        if not position:
            return {"should_close": False, "reason": "No position", "urgency": "low"}

        pos_type = position.get("type", "").upper() if isinstance(position, dict) else ("BUY" if position.is_long else "SELL")
        entry_price = float(position.get("entry", position.get("avgPrice", 0))) if isinstance(position, dict) else float(position.entry_price)
        current_price = indicators.get("current_price", 0)
        rsi = indicators.get("rsi", 50)
        ema_fast = indicators.get("ema_fast", 0)
        ema_med = indicators.get("ema_med", 0)
        macd_hist = indicators.get("macd_hist", 0)
        volume_ratio = indicators.get("volume_ratio", 1.0)

        if entry_price <= 0 or current_price <= 0:
            return {"should_close": False, "reason": "Invalid prices", "urgency": "low"}

        if pos_type == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        if pos_type == "BUY" and rsi > 80:
            return {"should_close": True, "reason": f"RSI {rsi:.0f}>80", "urgency": "high"}
        if pos_type == "SELL" and rsi < 20:
            return {"should_close": True, "reason": f"RSI {rsi:.0f}<20", "urgency": "high"}

        if pos_type == "BUY" and ema_fast < ema_med and rsi > 55:
            return {"should_close": True, "reason": "EMA\u2193 reversal", "urgency": "medium"}
        if pos_type == "SELL" and ema_fast > ema_med and rsi < 45:
            return {"should_close": True, "reason": "EMA\u2191 reversal", "urgency": "medium"}

        if pnl_pct < -0.5 and volume_ratio > 2.0:
            return {"should_close": True, "reason": f"VolCapitulation {volume_ratio:.1f}x", "urgency": "high"}

        if pos_type == "BUY" and macd_hist < 0 and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"MACD\u2193 loss {pnl_pct:.1f}%", "urgency": "medium"}
        if pos_type == "SELL" and macd_hist > 0 and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"MACD\u2191 loss {pnl_pct:.1f}%", "urgency": "medium"}

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}

    def _get_weights(self, regime: Optional[Dict]) -> Dict:
        base = {
            "ema_weight": self.rules.get("ema_weight", 2),
            "rsi_weight": self.rules.get("rsi_weight", 2),
            "volume_weight": self.rules.get("volume_weight", 1),
            "momentum_weight": self.rules.get("momentum_weight", 1),
            "vwap_weight": self.rules.get("vwap_weight", 1),
            "ob_imbalance_weight": self.rules.get("ob_imbalance_weight", 1),
            "macd_weight": self.rules.get("macd_weight", 1),
            "bb_weight": self.rules.get("bb_weight", 1),
            "cvd_weight": self.rules.get("cvd_weight", 1),
            "min_score": self.rules.get("min_score_for_signal", 4),
        }

        if regime:
            regime_label = regime.get("regime", "")
            overrides = self.regime_overrides.get(regime_label, {})
            for key, val in overrides.items():
                base[key] = val

        return base

    def _detect_pattern(self, indicators: Dict, signal: str, regime: Optional[Dict]) -> str:
        ema_fast = indicators.get("ema_fast", 0)
        ema_med = indicators.get("ema_med", 0)
        ema_macro = indicators.get("ema_macro", 0)
        rsi = indicators.get("rsi", 50)
        volume_ratio = indicators.get("volume_ratio", 1.0)
        current_price = indicators.get("current_price", 0)
        bb_lower = indicators.get("bb_lower", 0)
        bb_upper = indicators.get("bb_upper", 0)
        atr = indicators.get("atr", 0)

        regime_label = regime.get("regime", "") if regime else ""

        if signal == "BUY":
            if ema_fast > ema_med > ema_macro and atr > 0:
                dist_to_ema = abs(current_price - ema_med) / atr if atr > 0 else 99
                if dist_to_ema < 0.5 and 40 <= rsi <= 55:
                    return "pullback"

            if ema_fast > ema_med and volume_ratio >= 1.3 and 45 <= rsi <= 65:
                return "momentum"

            if regime_label == "RANGING" and bb_lower > 0 and current_price <= bb_lower * 1.005 and rsi < 30:
                return "mean_reversion"

        elif signal == "SELL":
            if ema_fast < ema_med < ema_macro and atr > 0:
                dist_to_ema = abs(current_price - ema_med) / atr if atr > 0 else 99
                if dist_to_ema < 0.5 and 45 <= rsi <= 60:
                    return "pullback"

            if ema_fast < ema_med and volume_ratio >= 1.3 and 35 <= rsi <= 55:
                return "momentum"

            if regime_label == "RANGING" and bb_upper > 0 and current_price >= bb_upper * 0.995 and rsi > 70:
                return "mean_reversion"

        return "generic"
