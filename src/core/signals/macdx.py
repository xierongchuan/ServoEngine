"""MACDX — детерминированный генератор сигналов на основе MACD-кроссовера."""

from typing import Any, Dict, Optional

from src.utils.logger import info, debug

from .base import BaseSignalGenerator
from .utils import detect_rsi_divergence


class MacdxSignalGenerator(BaseSignalGenerator):
    """Deterministic signal generator based on MACD crossover with confirmations."""

    def __init__(self, settings: Dict):
        super().__init__(settings)
        self.rules = self.settings.get("signal_rules", {})

    def generate(self, analysis: Dict, regime: Optional[Dict] = None) -> Dict:
        info(f"[MACDX] Analysis keys: {list(analysis.keys())}")
        info(f"[MACDX] macd_line={analysis.get('macd_line')}, macd_signal={analysis.get('macd_signal')}, macd_hist={analysis.get('macd_hist')}, macd_hist_prev={analysis.get('macd_hist_prev')}")

        current_price = analysis.get("current_price") or 0
        rsi = analysis.get("rsi") or 50
        volume_ratio = analysis.get("volume_ratio") or 1.0
        ema9 = analysis.get("ema9") or 0
        ema21 = analysis.get("ema21") or 0
        macd_line = analysis.get("macd_line") or 0
        macd_signal = analysis.get("macd_signal") or 0
        macd_hist = analysis.get("macd_hist") or 0
        macd_hist_prev = analysis.get("macd_hist_prev") or 0
        bb_upper = analysis.get("bb_upper") or 0
        bb_lower = analysis.get("bb_lower") or 0
        bb_middle = analysis.get("bb_middle") or 0
        atr = analysis.get("atr") or 0
        atr_ratio = analysis.get("atr_ratio") or 1.0
        adx = analysis.get("adx") or 25

        # Log key indicators
        info(f"[MACDX] Indicators: RSI={rsi:.1f}, EMA9={ema9:.2f}, EMA21={ema21:.2f}, MACD_hist={macd_hist:.6f}/{macd_hist_prev:.6f}, Volume={volume_ratio:.1f}x, ATR={atr_ratio:.2f}, ADX={adx:.0f}")

        macd_cross_weight = self.rules.get("macd_cross_weight", 2)
        rsi_weight = self.rules.get("rsi_zone_weight", 2)
        ema_weight = self.rules.get("ema_alignment_weight", 2)
        not_sideways_weight = self.rules.get("not_sideways_weight", 1)
        no_exhaustion_weight = self.rules.get("no_exhaustion_weight", 1)
        volume_weight = self.rules.get("volume_weight", 1)

        min_score = self.rules.get("min_score_for_signal", 4)
        min_volume = self.rules.get("min_volume_ratio", 0.5)
        min_atr_ratio = self.rules.get("min_atr_ratio", 0.3)
        rsi_long_max = self.rules.get("rsi_long_max", 65)
        rsi_long_min = self.rules.get("rsi_long_min", 25)
        rsi_short_max = self.rules.get("rsi_short_max", 75)
        rsi_short_min = self.rules.get("rsi_short_min", 35)
        bb_width_threshold = self.rules.get("bb_width_threshold", 0.5)
        adx_threshold = self.rules.get("adx_threshold", 20)

        max_score_base = macd_cross_weight + rsi_weight + ema_weight + not_sideways_weight + no_exhaustion_weight + volume_weight
        enable_volume_filter = self.rules.get("enable_volume_filter", True)
        if not enable_volume_filter:
            max_score_base -= volume_weight
        max_score = max_score_base

        if regime and regime.get("recommended_min_score"):
            min_score = regime["recommended_min_score"]

        if atr_ratio < min_atr_ratio:
            info(f"[MACDX] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return self._hold_result(max_score, [f"Low volatility (ATR {atr_ratio:.2f})"],
                                     {"atr_ratio": atr_ratio, "filter": "volatility", "confirmations": 0, "potential_score": 0}, regime)

        enable_volume_filter = self.rules.get("enable_volume_filter", True)
        if enable_volume_filter and volume_ratio < min_volume:
            info(f"[MACDX] HOLD | Low volume ({volume_ratio:.2f}x)")
            return self._hold_result(max_score, [f"Low volume ({volume_ratio:.2f}x)"],
                                     {"volume_ratio": volume_ratio, "filter": "volume", "confirmations": 0, "potential_score": 0}, regime)

        consecutive_red_filter = self.rules.get("consecutive_red_filter", True)
        min_consecutive_for_block = self.rules.get("min_consecutive_for_block", 3)
        enable_counter_trend_filter = self.rules.get("enable_counter_trend_filter", True)
        counter_trend_ema_threshold = self.rules.get("counter_trend_ema_threshold", 1.0)
        last_5_direction = analysis.get("last_5_direction") or "MIXED"

        potential_long = macd_hist > 0 and macd_hist_prev <= 0
        potential_short = macd_hist < 0 and macd_hist_prev >= 0

        if consecutive_red_filter and potential_long:
            has_consecutive_reds = last_5_direction in ["STRONG_DOWN", "DOWN"]
            if has_consecutive_reds:
                should_block = False
                block_reason = ""
                if last_5_direction == "STRONG_DOWN":
                    should_block = True
                    block_reason = "STRONG_DOWN (4+ reds) + bearish MACD momentum"
                elif last_5_direction == "DOWN" and min_consecutive_for_block == 2:
                    should_block = True
                    block_reason = "DOWN (3 reds) + bearish MACD momentum"
                elif last_5_direction == "DOWN" and macd_hist_prev < 0:
                    should_block = True
                    block_reason = "DOWN (3 reds) + negative MACD histogram"

                if should_block:
                    info(f"[MACDX] HOLD | {block_reason}")
                    return self._hold_result(max_score, [block_reason],
                                             {"last_5_direction": last_5_direction, "filter": "consecutive_red_momentum", "confirmations": 1, "potential_score": 2}, regime)

        if enable_counter_trend_filter and ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100
            if ema9 < ema21 and ema_diff_pct > counter_trend_ema_threshold and potential_long:
                info(f"[MACDX] HOLD | Counter-trend: EMA down {ema_diff_pct:.1f}%")
                return self._hold_result(max_score, [f"Counter-trend: EMA below by {ema_diff_pct:.1f}%"],
                                         {"ema_diff_pct": ema_diff_pct, "filter": "counter_trend", "confirmations": 2, "potential_score": 4}, regime)
            if ema9 > ema21 and ema_diff_pct > counter_trend_ema_threshold and potential_short:
                info(f"[MACDX] HOLD | Counter-trend: EMA up {ema_diff_pct:.1f}%")
                return self._hold_result(max_score, [f"Counter-trend: EMA above by {ema_diff_pct:.1f}%"],
                                         {"ema_diff_pct": ema_diff_pct, "filter": "counter_trend", "confirmations": 2, "potential_score": 4}, regime)

        macd_cross_long = False
        macd_cross_short = False

        if macd_hist_prev <= 0 and macd_hist > 0:
            macd_cross_long = True
            info(f"[MACDX] Bullish MACD crossover detected: hist_prev={macd_hist_prev:.6f} <= 0, hist={macd_hist:.6f} > 0")
        if macd_hist_prev >= 0 and macd_hist < 0:
            macd_cross_short = True
            info(f"[MACDX] Bearish MACD crossover detected: hist_prev={macd_hist_prev:.6f} >= 0, hist={macd_hist:.6f} < 0")

        info(f"[MACDX] Crossover check: long={macd_cross_long}, short={macd_cross_short}")

        if not macd_cross_long and not macd_cross_short:
            info(f"[MACDX] HOLD | No MACD crossover (hist: {macd_hist:.6f}, hist_prev: {macd_hist_prev:.6f})")
            is_sideways = False
            bb_width = 0
            if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
                bb_width = (bb_upper - bb_lower) / bb_middle * 100
                if bb_width < bb_width_threshold and adx < adx_threshold:
                    is_sideways = True

            close_prices = analysis.get("close_prices", [])
            rsi_values = analysis.get("rsi_values", [])
            bearish_div, bullish_div = detect_rsi_divergence(close_prices, rsi_values)

            indicators_status = [
                {"name": "MACD Crossover", "weight": macd_cross_weight, "ok": False, "value": f"hist={macd_hist:.6f}, prev={macd_hist_prev:.6f}", "detail": "\u041d\u0435\u0442 \u043f\u0435\u0440\u0435\u0441\u0435\u0447\u0435\u043d\u0438\u044f"},
                {"name": "RSI Zone", "weight": rsi_weight, "ok": (35 <= rsi <= 65), "value": f"{rsi:.1f}", "detail": "\u0412 \u0437\u043e\u043d\u0435" if 35 <= rsi <= 65 else "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445"},
                {"name": "EMA Alignment", "weight": ema_weight, "ok": (ema9 > ema21 if ema9 > 0 and ema21 > 0 else False), "value": f"9:{ema9:.2f} 21:{ema21:.2f}", "detail": "\u0411\u044b\u0447\u0438\u0439 (9>21)" if ema9 > ema21 else "\u041c\u0435\u0434\u0432\u0435\u0436\u0438\u0439 (9<21)"},
                {"name": "Not Sideways", "weight": not_sideways_weight, "ok": not is_sideways, "value": f"BB:{bb_width:.1f}% ADX:{adx:.0f}", "detail": "\u0422\u0440\u0435\u043d\u0434 \u0435\u0441\u0442\u044c" if not is_sideways else "\u0411\u043e\u043a\u043e\u0432\u0438\u043a"},
                {"name": "No Exhaustion", "weight": no_exhaustion_weight, "ok": (not bearish_div and not bullish_div), "value": "", "detail": "\u041d\u0435\u0442 \u0434\u0438\u0432\u0435\u0440\u0433\u0435\u043d\u0446\u0438\u0438" if not bearish_div and not bullish_div else "\u0415\u0441\u0442\u044c \u0434\u0438\u0432\u0435\u0440\u0433\u0435\u043d\u0446\u0438\u044f"},
                {"name": "Volume", "weight": volume_weight, "ok": (volume_ratio >= 0.8), "value": f"{volume_ratio:.1f}x", "detail": "\u0414\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u044b\u0439" if volume_ratio >= 0.8 else "\u0421\u043b\u0430\u0431\u044b\u0439"},
            ]

            ok_count = sum(1 for s in indicators_status if s["ok"])
            potential_score = sum(s["weight"] for s in indicators_status if s["ok"])
            max_possible_score = sum(s["weight"] for s in indicators_status)
            indicators_ok = [f"{s['name']}: {s['detail']}" for s in indicators_status if s["ok"]]
            indicators_fail = [f"{s['name']}: {s['detail']}" for s in indicators_status if not s["ok"]]

            return self._hold_result(max_score, ["No MACD crossover"],
                                     {"macd_hist": macd_hist, "filter": "no_macd_cross",
                                      "potential_score": potential_score, "confirmations": ok_count,
                                      "max_confirmations": len(indicators_status),
                                      "indicators_ok": indicators_ok, "indicators_fail": indicators_fail,
                                      "indicators_status": indicators_status,
                                      "indicators_ok_count": ok_count,
                                      "indicators_total_count": len(indicators_status),
                                      "max_possible_score": max_possible_score}, regime)

        # === SCORE CONFIRMATIONS ===
        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []
        long_confirmations = 0
        short_confirmations = 0

        if macd_cross_long:
            long_score += macd_cross_weight
            long_reasons.append(f"MACD\u2191 cross +{macd_cross_weight}\n")
            long_confirmations += 1
        if macd_cross_short:
            short_score += macd_cross_weight
            short_reasons.append(f"MACD\u2193 cross +{macd_cross_weight}\n")
            short_confirmations += 1

        rsi_long_ok = rsi_long_min <= rsi <= rsi_long_max
        rsi_short_ok = rsi_short_min <= rsi <= rsi_short_max
        rsi_extreme_oversold = rsi < 30
        rsi_extreme_overbought = rsi > 70
        long_blocked = False
        short_blocked = False

        if macd_cross_long:
            if rsi_extreme_overbought:
                long_reasons.append(f"RSI {rsi:.0f} EXTREME OVERBOUGHT - блокируем LONG\n")
                long_score = 0
                long_blocked = True
                info(f"[MACDX] RSI blocks LONG: extreme overbought ({rsi:.0f})")
            elif rsi_long_ok:
                long_score += rsi_weight
                long_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}\n")
                long_confirmations += 1
                info(f"[MACDX] RSI confirms LONG: {rsi:.0f} in zone, +{rsi_weight}")
            else:
                long_reasons.append(f"RSI {rsi:.0f} outside zone (need {rsi_long_min}-{rsi_long_max})\n")
                info(f"[MACDX] RSI rejects LONG: {rsi:.0f} outside zone")

        if macd_cross_short:
            if rsi_extreme_oversold:
                short_reasons.append(f"RSI {rsi:.0f} EXTREME OVERSOLD - блокируем SELL\n")
                short_score = 0
                short_confirmations = 0
                short_blocked = True
                info(f"[MACDX] RSI blocks SHORT: extreme oversold ({rsi:.0f})")
            elif rsi_short_ok:
                short_score += rsi_weight
                short_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}\n")
                short_confirmations += 1
                info(f"[MACDX] RSI confirms SHORT: {rsi:.0f} in zone, +{rsi_weight}")
            else:
                short_reasons.append(f"RSI {rsi:.0f} outside zone (need {rsi_short_min}-{rsi_short_max})\n")
                info(f"[MACDX] RSI rejects SHORT: {rsi:.0f} outside zone")

        ema_long = False
        ema_short = False
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            info(f"[MACDX] EMA: 9={ema9:.2f}, 21={ema21:.2f}, diff={ema_diff_pct:.1f}%")
            if ema9 > ema21:
                ema_long = True
                if macd_cross_long and not long_blocked:
                    long_score += ema_weight
                    long_reasons.append(f"EMA↑ ({ema_diff_pct:.1f}%) +{ema_weight}\n")
                    long_confirmations += 1
                    info(f"[MACDX] EMA confirms LONG: bullish alignment, +{ema_weight}")
            elif ema9 < ema21:
                ema_short = True
                if macd_cross_short and not short_blocked:
                    short_score += ema_weight
                    short_reasons.append(f"EMA↓ ({ema_diff_pct:.1f}%) +{ema_weight}\n")
                    short_confirmations += 1
                    info(f"[MACDX] EMA confirms SHORT: bearish alignment, +{ema_weight}")
            else:
                info(f"[MACDX] EMA neutral: flat")

        if macd_cross_long and ema_short:
            long_reasons.append("EMA counter-trend (caution)\n")
        if macd_cross_short and ema_long:
            short_reasons.append("EMA counter-trend (caution)\n")

        is_sideways = False
        bb_width = 0
        if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
            bb_width = (bb_upper - bb_lower) / bb_middle * 100
            if bb_width < bb_width_threshold and adx < adx_threshold:
                is_sideways = True

        info(f"[MACDX] Sideways check: BB={bb_width:.1f}%, ADX={adx:.0f}, is_sideways={is_sideways}")

        if not is_sideways:
            if macd_cross_long and not long_blocked:
                long_score += not_sideways_weight
                long_reasons.append(f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}\n")
                long_confirmations += 1
                info(f"[MACDX] Not sideways confirms LONG: +{not_sideways_weight}")
            if macd_cross_short and not short_blocked:
                short_score += not_sideways_weight
                short_reasons.append(f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}\n")
                short_confirmations += 1
                info(f"[MACDX] Not sideways confirms SHORT: +{not_sideways_weight}")
        else:
            info(f"[MACDX] Sideways market detected")
            if macd_cross_long:
                long_reasons.append(f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})\n")
            if macd_cross_short:
                short_reasons.append(f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})\n")

        close_prices = analysis.get("close_prices", [])
        rsi_values = analysis.get("rsi_values", [])
        bearish_div, bullish_div = detect_rsi_divergence(close_prices, rsi_values)

        info(f"[MACDX] Divergence: bearish={bearish_div}, bullish={bullish_div}")

        if macd_cross_long and not long_blocked:
            if not bearish_div:
                long_score += no_exhaustion_weight
                long_reasons.append(f"No bearish divergence +{no_exhaustion_weight}\n")
                long_confirmations += 1
                info(f"[MACDX] No exhaustion confirms LONG: +{no_exhaustion_weight}")
            else:
                long_reasons.append("Bearish RSI divergence (exhaustion warning)\n")
                info(f"[MACDX] Exhaustion rejects LONG: bearish divergence")

        if macd_cross_short and not short_blocked:
            if not bullish_div:
                short_score += no_exhaustion_weight
                short_reasons.append(f"No bullish divergence +{no_exhaustion_weight}\n")
                short_confirmations += 1
                info(f"[MACDX] No exhaustion confirms SHORT: +{no_exhaustion_weight}")
            else:
                short_reasons.append("Bullish RSI divergence (exhaustion warning)\n")
                info(f"[MACDX] Exhaustion rejects SHORT: bullish divergence")

        volume_confirm_threshold = self.rules.get("volume_confirm_threshold", 0.8)
        volume_ok = volume_ratio >= volume_confirm_threshold
        info(f"[MACDX] Volume: {volume_ratio:.1f}x (threshold {volume_confirm_threshold:.1f}), ok={volume_ok}")
        if volume_ok:
            if macd_cross_long and not long_blocked:
                long_score += volume_weight
                long_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}\n")
                long_confirmations += 1
                info(f"[MACDX] Volume confirms LONG: +{volume_weight}")
            if macd_cross_short and not short_blocked:
                short_score += volume_weight
                short_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}\n")
                short_confirmations += 1
                info(f"[MACDX] Volume confirms SHORT: +{volume_weight}")
        else:
            info(f"[MACDX] Volume insufficient for confirmation")

        min_confirmations = self.rules.get("min_confirmations", 3)

        # Log scoring details
        try:
            info(f"[MACDX] Scoring: LONG {long_score}/{max_score} ({long_confirmations} conf), SHORT {short_score}/{max_score} ({short_confirmations} conf), min_score={min_score}, min_conf={min_confirmations}")
        except Exception as e:
            info(f"[MACDX] Scoring log error: {e}")

        signal = "HOLD"
        score = 0
        reasons = []
        confirmations = 0

        if long_score >= min_score and long_score > short_score and long_confirmations >= min_confirmations:
            signal = "BUY"
            score = long_score
            reasons = long_reasons
            confirmations = long_confirmations
        elif short_score >= min_score and short_score > long_score and short_confirmations >= min_confirmations:
            signal = "SELL"
            score = short_score
            reasons = short_reasons
            confirmations = short_confirmations
        elif long_score >= min_score and short_score >= min_score:
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"CONFLICT L:{long_score}({long_confirmations}conf) S:{short_score}({short_confirmations}conf)"]
        else:
            signal = "HOLD"
            score = max(long_score, short_score)
            conf_str = f"L:{long_confirmations}/{min_confirmations}" if macd_cross_long else f"S:{short_confirmations}/{min_confirmations}"
            reasons = [f"Insufficient confirmations ({conf_str}) or score L:{long_score} S:{short_score} (need {min_score})"]

        info(f"[MACDX] Final signal: {signal}, score={score}, confirmations={confirmations}, reasons={''.join(reasons).strip()}")

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
            "long_confirmations": long_confirmations,
            "short_confirmations": short_confirmations,
            "min_confirmations": min_confirmations,
            "min_score_required": min_score,
            "macd_hist": macd_hist,
            "macd_hist_prev": macd_hist_prev,
            "macd_cross_long": macd_cross_long,
            "macd_cross_short": macd_cross_short,
            "rsi": rsi,
            "bb_width": bb_width,
            "adx": adx,
            "is_sideways": is_sideways,
            "bearish_divergence": bearish_div,
            "bullish_divergence": bullish_div,
            "volume_ratio": volume_ratio,
        }

        result = {
            "signal": signal,
            "score": score,
            "max_score": max_score,
            "quality": quality,
            "confidence": confidence,
            "reasons": reasons,
            "confirmations": confirmations,
            "filters_passed": score >= min_score,
            "details": details,
            "regime": regime.get("regime", "UNKNOWN") if regime else "NO_REGIME",
        }

        regime_label = regime.get("regime", "?") if regime else "?"
        try:
            if signal != "HOLD":
                info(f"[MACDX] {signal} | {score}/{max_score} Q:{quality:.2f} [{regime_label}] | {confirmations} confirmations")
            else:
                info(f"[MACDX] HOLD | L:{long_score}({long_confirmations}) S:{short_score}({short_confirmations}) (need {min_score}, {min_confirmations}conf) [{regime_label}]")
        except Exception as e:
            info(f"[MACDX] Final log error: {e}, signal={signal}, score={score}")

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

        if pos_type == "BUY" and macd_hist < 0:
            if pnl_pct >= 0.5:
                return {"should_close": True, "reason": f"MACD\u2193 + profit {pnl_pct:.1f}%", "urgency": "medium"}
            elif pnl_pct < -1.0:
                return {"should_close": True, "reason": f"MACD\u2193 + loss {pnl_pct:.1f}%", "urgency": "high"}

        if pos_type == "SELL" and macd_hist > 0:
            if pnl_pct >= 0.5:
                return {"should_close": True, "reason": f"MACD\u2191 + profit {pnl_pct:.1f}%", "urgency": "medium"}
            elif pnl_pct < -1.0:
                return {"should_close": True, "reason": f"MACD\u2191 + loss {pnl_pct:.1f}%", "urgency": "high"}

        if pos_type == "BUY" and rsi > 80:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} > 80 (overbought)", "urgency": "high"}
        if pos_type == "SELL" and rsi < 20:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} < 20 (oversold)", "urgency": "high"}

        if pnl_pct >= 3.0:
            if (pos_type == "BUY" and rsi > 70) or (pos_type == "SELL" and rsi < 30):
                return {"should_close": True, "reason": f"Take profit +{pnl_pct:.1f}% RSI extreme", "urgency": "medium"}

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}
