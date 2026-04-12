"""
MACDX Signal Generator - Deterministic MACD Crossover Strategy.

Works without AI. Opens trades based on 3-5 indicator confirmations.

REQUIRED SIGNAL (must be present):
  - MACD crossover: MACD line crosses signal line

CONFIRMATION INDICATORS (at least 3 required):
  1. RSI Zone (+2) - RSI not overbought for long, not oversold for short
  2. EMA Alignment (+2) - EMA9 > EMA21 for long, EMA9 < EMA21 for short
  3. Not Sideways (+1) - BB width > threshold or ADX > 20 (market has direction)
  4. No Exhaustion (+1) - No RSI divergence against signal direction
  5. Volume (+1) - Volume ratio >= threshold

Max score: 8 (MACD + all confirmations)
Min score for signal: 4 (MACD + at least 2-3 confirmations)
"""

from src.config import BOT_CONFIG
from src.utils.logger import info


class MACDXSignalGenerator:
    """Deterministic signal generator based on MACD crossover with confirmations."""

    def __init__(self):
        self.settings = BOT_CONFIG.get("MACDX_SETTINGS", {})
        self.rules = self.settings.get("signal_rules", {})

    def generate_signal(self, analysis: dict, regime: dict = None) -> dict:
        """
        Generate deterministic signal based on MACD crossover and confirmations.

        Args:
            analysis: dict with indicators from analyzer
            regime: dict from MarketRegimeDetector (optional)

        Returns:
            dict with signal, score, quality, and details
        """
        # Debug: log received analysis data
        from src.utils.logger import debug
        debug(f"[MACDX] Analysis keys: {list(analysis.keys())}")
        debug(f"[MACDX] macd_line={analysis.get('macd_line')}, macd_signal={analysis.get('macd_signal')}, macd_hist={analysis.get('macd_hist')}, macd_hist_prev={analysis.get('macd_hist_prev')}")

        # === EXTRACT DATA ===
        analysis.get("current_price") or 0
        rsi = analysis.get("rsi") or 50
        volume_ratio = analysis.get("volume_ratio") or 1.0
        ema9 = analysis.get("ema9") or 0
        ema21 = analysis.get("ema21") or 0

        macd_line = analysis.get("macd_line") or 0
        macd_signal_line = analysis.get("macd_signal") or 0
        macd_hist = analysis.get("macd_hist") or 0
        macd_hist_prev = analysis.get("macd_hist_prev") or 0

        bb_upper = analysis.get("bb_upper") or 0
        bb_lower = analysis.get("bb_lower") or 0
        bb_middle = analysis.get("bb_middle") or 0
        analysis.get("atr") or 0
        atr_ratio = analysis.get("atr_ratio") or 1.0

        # ADX for trend strength (if available)
        adx = analysis.get("adx") or 25  # Default to moderate trend

        # === CONFIG THRESHOLDS ===
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

        # Динамический расчет max_score на основе включенных индикаторов
        # По умолчанию: MACD + RSI + EMA + NotSideways + NoExhaustion + Volume = 9
        max_score_base = macd_cross_weight + rsi_weight + ema_weight + not_sideways_weight + no_exhaustion_weight + volume_weight

        # Учитываем отключенные фильтры
        enable_volume_filter = self.rules.get("enable_volume_filter", True)
        if not enable_volume_filter:
            max_score_base -= volume_weight  # Volume filter отключен

        max_score = max_score_base

        # Use regime-adaptive min score if available
        if regime and regime.get("recommended_min_score"):
            min_score = regime["recommended_min_score"]

        # === HARD FILTERS ===
        if atr_ratio < min_atr_ratio:
            info(f"📊 [MACDX] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return self._hold_result(max_score, [f"Low volatility (ATR {atr_ratio:.2f})"],
                                     {"atr_ratio": atr_ratio, "filter": "volatility", "confirmations": 0, "potential_score": 0}, regime)

        # Volume filter - can be disabled via config
        enable_volume_filter = self.rules.get("enable_volume_filter", True)
        if enable_volume_filter and volume_ratio < min_volume:
            info(f"📊 [MACDX] HOLD | Low volume ({volume_ratio:.2f}x)")
            return self._hold_result(max_score, [f"Low volume ({volume_ratio:.2f}x)"],
                                     {"volume_ratio": volume_ratio, "filter": "volume", "confirmations": 0, "potential_score": 0}, regime)

        # === CONSECUTIVE CANDLE FILTER (Momentum Protection) ===
        # Get config for this filter
        consecutive_red_filter = self.rules.get("consecutive_red_filter", True)
        min_consecutive_for_block = self.rules.get("min_consecutive_for_block", 3)
        enable_counter_trend_filter = self.rules.get("enable_counter_trend_filter", True)
        counter_trend_ema_threshold = self.rules.get("counter_trend_ema_threshold", 1.0)

        last_5_direction = analysis.get("last_5_direction") or "MIXED"

        # Detect potential crossover for filter use
        # Только смена знака гистограммы — рост/падение НЕ является пересечением
        potential_long = macd_hist > 0 and macd_hist_prev <= 0
        potential_short = macd_hist < 0 and macd_hist_prev >= 0

        # Filter 1: Block LONG when BOTH conditions met:
        # 1) Consecutive red candles (price momentum down)
        # 2) MACD histogram was bearish (just crossed from negative — weak reversal)
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
                    info(f"📊 [MACDX] HOLD | {block_reason}")
                    return self._hold_result(max_score, [block_reason],
                                             {"last_5_direction": last_5_direction, "filter": "consecutive_red_momentum", "confirmations": 1, "potential_score": 2}, regime)

        # Filter 2: Counter-trend protection (EMA vs MACD)
        # Работает постоянно — не только в момент пересечения
        if enable_counter_trend_filter and ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100

            # Блокируем LONG если EMA направлена вниз и MACD показывает бычий сигнал
            if ema9 < ema21 and ema_diff_pct > counter_trend_ema_threshold and potential_long:
                info(f"📊 [MACDX] HOLD | Counter-trend: EMA down {ema_diff_pct:.1f}%")
                return self._hold_result(max_score, [f"Counter-trend: EMA below by {ema_diff_pct:.1f}%"],
                                         {"ema_diff_pct": ema_diff_pct, "filter": "counter_trend", "confirmations": 2, "potential_score": 4}, regime)

            # Блокируем SHORT если EMA направлена вверх и MACD показывает медвежий сигнал
            if ema9 > ema21 and ema_diff_pct > counter_trend_ema_threshold and potential_short:
                info(f"📊 [MACDX] HOLD | Counter-trend: EMA up {ema_diff_pct:.1f}%")
                return self._hold_result(max_score, [f"Counter-trend: EMA above by {ema_diff_pct:.1f}%"],
                                         {"ema_diff_pct": ema_diff_pct, "filter": "counter_trend", "confirmations": 2, "potential_score": 4}, regime)

        # === DETECT MACD CROSSOVER (PRIMARY SIGNAL) ===
        # Пересечение определяется по изменению знака гистограммы:
        # - Бычье пересечение: гистограмма переходит из отрицательной в положительную
        # - Медвежье пересечение: гистограмма переходит из положительной в отрицательную
        #
        # ВАЖНО: Проверяем что пересечение было именно в предыдущей свече (не раньше):
        # 1) Гистограмма 2 свечи назад была <= 0 (для бычьего) или >= 0 (для медвежьего)
        # 2) Предыдущая гистограмма была в нужную сторону от нуля
        # 3) Текущая гистограмма продолжает движение в ту же сторону

        macd_cross_long = False
        macd_cross_short = False

        # Пересечение MACD происходит между предыдущей (n-1) и текущей (n) свечой
        # Для бычьего пересечения: в предыдущей свече (n-1) гистограмма <= 0, в текущей (n) > 0
        # Для медвежьего: в предыдущей свече >= 0, в текущей < 0

        # Подтверждение: гистограмма в текущей свече сохраняет направление (для бычьего - положительная)

        # Бычье пересечение: предыдущая <= 0, текущая > 0
        if macd_hist_prev <= 0 and macd_hist > 0:
            macd_cross_long = True
            debug(f"[MACDX] Бычье пересечение: hist_prev={macd_hist_prev:.6f} <= 0, hist={macd_hist:.6f} > 0 (подтверждено)")

        # Медвежье пересечение: предыдущая >= 0, текущая < 0
        if macd_hist_prev >= 0 and macd_hist < 0:
            macd_cross_short = True
            debug(f"[MACDX] Медвежье пересечение: hist_prev={macd_hist_prev:.6f} >= 0, hist={macd_hist:.6f} < 0 (подтверждено)")

        # No EMA fallback! Strategy requires MACD crossover (12,26,9) ONLY
        # Если нет MACD пересечения - это HOLD

        # No MACD signal - HOLD (strategy requires MACD crossover ONLY)
        if not macd_cross_long and not macd_cross_short:
            info(f"📊 [MACDX] HOLD | No MACD crossover (hist: {macd_hist:.6f}, hist_prev: {macd_hist_prev:.6f}, line: {macd_line:.6f}, signal: {macd_signal_line:.6f})")

            # Вычисляем переменные которые нужны для indicators_status
            is_sideways = False
            bb_width = 0
            if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
                bb_width = (bb_upper - bb_lower) / bb_middle * 100
                if bb_width < bb_width_threshold or adx < adx_threshold:
                    is_sideways = True

            close_prices = analysis.get("close_prices", [])
            rsi_values = analysis.get("rsi_values", [])
            bearish_div, bullish_div = self._detect_rsi_divergence(close_prices, rsi_values)

            # Собираем ПОЛНУЮ информацию о ВСЕХ 6 индикаторах
            indicators_status = []

            # 1. MACD Crossover (основной сигнал, +2)
            macd_status = {
                "name": "MACD Crossover",
                "weight": macd_cross_weight,
                "ok": False,
                "value": f"hist={macd_hist:.6f}, prev={macd_hist_prev:.6f}",
                "detail": "Нет пересечения"
            }
            indicators_status.append(macd_status)

            # 2. RSI Zone (+2)
            rsi_status = {
                "name": "RSI Zone",
                "weight": rsi_weight,
                "ok": False,
                "value": f"{rsi:.1f}",
                "detail": ""
            }
            if 0 < rsi <= 100:
                if rsi < 35:
                    rsi_status["detail"] = "Перепродан (<35)"
                elif rsi > 65:
                    rsi_status["detail"] = "Перекуплен (>65)"
                else:
                    rsi_status["ok"] = True
                    rsi_status["detail"] = "В зоне"
            else:
                rsi_status["detail"] = "Нет данных"
            indicators_status.append(rsi_status)

            # 3. EMA Alignment (+2)
            ema_status = {
                "name": "EMA Alignment",
                "weight": ema_weight,
                "ok": False,
                "value": f"9:{ema9:.2f} 21:{ema21:.2f}",
                "detail": ""
            }
            if ema9 > 0 and ema21 > 0:
                if ema9 > ema21:
                    ema_status["ok"] = True
                    ema_status["detail"] = "Бычий (9>21)"
                else:
                    ema_status["detail"] = "Медвежий (9<21)"
            else:
                ema_status["detail"] = "Нет данных"
            indicators_status.append(ema_status)

            # 4. Not Sideways (+1)
            sideways_status = {
                "name": "Not Sideways",
                "weight": not_sideways_weight,
                "ok": False,
                "value": f"BB:{bb_width:.1f}% ADX:{adx:.0f}",
                "detail": ""
            }
            if not is_sideways:
                sideways_status["ok"] = True
                sideways_status["detail"] = "Тренд есть"
            else:
                sideways_status["detail"] = "Боковик"
            indicators_status.append(sideways_status)

            # 5. No Exhaustion (+1)
            exhaustion_status = {
                "name": "No Exhaustion",
                "weight": no_exhaustion_weight,
                "ok": False,
                "value": "",
                "detail": ""
            }
            if not bearish_div and not bullish_div:
                exhaustion_status["ok"] = True
                exhaustion_status["detail"] = "Нет дивергенции"
            else:
                exhaustion_status["detail"] = "Есть дивергенция"
            indicators_status.append(exhaustion_status)

            # 6. Volume (+1)
            volume_status = {
                "name": "Volume",
                "weight": volume_weight,
                "ok": False,
                "value": f"{volume_ratio:.1f}x",
                "detail": ""
            }
            if volume_ratio >= 0.8:
                volume_status["ok"] = True
                volume_status["detail"] = "Достаточный"
            else:
                volume_status["detail"] = "Слабый"
            indicators_status.append(volume_status)

            # Рассчитываем реальные метрики
            ok_count = sum(1 for s in indicators_status if s["ok"])
            total_count = len(indicators_status)
            potential_score = sum(s["weight"] for s in indicators_status if s["ok"])
            max_possible_score = sum(s["weight"] for s in indicators_status)

            # Формируем строки для reason
            indicators_ok = [f"{s['name']}: {s['detail']}" for s in indicators_status if s["ok"]]
            indicators_fail = [f"{s['name']}: {s['detail']}" for s in indicators_status if not s["ok"]]

            return self._hold_result(max_score, ["No MACD crossover"],
                                     {"macd_hist": macd_hist, "filter": "no_macd_cross",
                                      "potential_score": potential_score, "confirmations": ok_count,
                                      "max_confirmations": total_count,
                                      "indicators_ok": indicators_ok, "indicators_fail": indicators_fail,
                                      "indicators_status": indicators_status,
                                      "indicators_ok_count": ok_count,
                                      "indicators_total_count": total_count,
                                      "max_possible_score": max_possible_score}, regime)

        # === SCORE CONFIRMATIONS ===
        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []
        long_confirmations = 0
        short_confirmations = 0

        # 1. MACD Crossover (Required, already confirmed)
        if macd_cross_long:
            long_score += macd_cross_weight
            long_reasons.append(f"MACD↑ cross +{macd_cross_weight}\n")
            long_confirmations += 1
        if macd_cross_short:
            short_score += macd_cross_weight
            short_reasons.append(f"MACD↓ cross +{macd_cross_weight}\n")
            short_confirmations += 1

        # 2. RSI Zone (+2)
        rsi_long_ok = rsi_long_min <= rsi <= rsi_long_max
        rsi_short_ok = rsi_short_min <= rsi <= rsi_short_max

        # Дополнительная защита: экстремальные зоны блокируют сигналы
        rsi_extreme_oversold = rsi < 30
        rsi_extreme_overbought = rsi > 70

        long_blocked = False
        short_blocked = False

        if macd_cross_long:
            # Блокируем LONG при экстремальной перекупленности (независимо от зоны)
            if rsi_extreme_overbought:
                long_reasons.append(f"RSI {rsi:.0f} EXTREME OVERBOUGHT - блокируем LONG\n")
                long_score = 0  # Блокируем сигнал
                long_blocked = True
            elif rsi_long_ok:
                long_score += rsi_weight
                long_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}\n")
                long_confirmations += 1
            else:
                long_reasons.append(f"RSI {rsi:.0f} outside zone (need {rsi_long_min}-{rsi_long_max})\n")

        if macd_cross_short:
            # Блокируем SELL при экстремальной перепроданности (независимо от зоны)
            if rsi_extreme_oversold:
                short_reasons.append(f"RSI {rsi:.0f} EXTREME OVERSOLD - блокируем SELL\n")
                short_score = 0  # Блокируем сигнал
                short_confirmations = 0  # Сбрасываем подтверждения
                short_blocked = True
            elif rsi_short_ok:
                short_score += rsi_weight
                short_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}\n")
                short_confirmations += 1
            else:
                short_reasons.append(f"RSI {rsi:.0f} outside zone (need {rsi_short_min}-{rsi_short_max})\n")

        # 3. EMA Alignment (+2) — пропускаем если сигнал заблокирован
        ema_long = False
        ema_short = False
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            if ema9 > ema21:
                ema_long = True
                if macd_cross_long and not long_blocked:
                    long_score += ema_weight
                    long_reasons.append(f"EMA↑ ({ema_diff_pct:.1f}%) +{ema_weight}\n")
                    long_confirmations += 1
            elif ema9 < ema21:
                ema_short = True
                if macd_cross_short and not short_blocked:
                    short_score += ema_weight
                    short_reasons.append(f"EMA↓ ({ema_diff_pct:.1f}%) +{ema_weight}\n")
                    short_confirmations += 1

        # Penalty for counter-trend MACD signals
        if macd_cross_long and ema_short:
            long_reasons.append("EMA counter-trend (caution)\n")
        if macd_cross_short and ema_long:
            short_reasons.append("EMA counter-trend (caution)\n")

        # 4. Not Sideways (+1) — пропускаем если сигнал заблокирован
        is_sideways = False
        bb_width = 0
        if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
            bb_width = (bb_upper - bb_lower) / bb_middle * 100
            # Sideways = narrow BB width AND low ADX
            if bb_width < bb_width_threshold or adx < adx_threshold:
                is_sideways = True

        if not is_sideways:
            if macd_cross_long and not long_blocked:
                long_score += not_sideways_weight
                long_reasons.append(f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}\n")
                long_confirmations += 1
            if macd_cross_short and not short_blocked:
                short_score += not_sideways_weight
                short_reasons.append(f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}\n")
                short_confirmations += 1
        else:
            if macd_cross_long:
                long_reasons.append(f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})\n")
            if macd_cross_short:
                short_reasons.append(f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})\n")

        # 5. No Exhaustion (+1) — пропускаем если сигнал заблокирован
        close_prices = analysis.get("close_prices", [])
        rsi_values = analysis.get("rsi_values", [])
        bearish_div, bullish_div = self._detect_rsi_divergence(close_prices, rsi_values)

        # For long: no bearish divergence
        if macd_cross_long and not long_blocked:
            if not bearish_div:
                long_score += no_exhaustion_weight
                long_reasons.append(f"No bearish divergence +{no_exhaustion_weight}\n")
                long_confirmations += 1
            else:
                long_reasons.append("Bearish RSI divergence (exhaustion warning)\n")

        # For short: no bullish divergence
        if macd_cross_short and not short_blocked:
            if not bullish_div:
                short_score += no_exhaustion_weight
                short_reasons.append(f"No bullish divergence +{no_exhaustion_weight}\n")
                short_confirmations += 1
            else:
                short_reasons.append("Bullish RSI divergence (exhaustion warning)\n")

        # 6. Volume (+1) — пропускаем если сигнал заблокирован
        volume_confirm_threshold = self.rules.get("volume_confirm_threshold", 0.8)
        volume_ok = volume_ratio >= volume_confirm_threshold
        if volume_ok:
            if macd_cross_long and not long_blocked:
                long_score += volume_weight
                long_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}\n")
                long_confirmations += 1
            if macd_cross_short and not short_blocked:
                short_score += volume_weight
                short_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}\n")
                short_confirmations += 1

        # === DETERMINE FINAL SIGNAL ===
        signal = "HOLD"
        score = 0
        reasons = []
        confirmations = 0

        # Require minimum confirmations (at least 3 including MACD)
        min_confirmations = self.rules.get("min_confirmations", 3)

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
            # Conflict - both have signals
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [f"CONFLICT L:{long_score}({long_confirmations}conf) S:{short_score}({short_confirmations}conf)"]
        else:
            signal = "HOLD"
            score = max(long_score, short_score)
            conf_str = f"L:{long_confirmations}/{min_confirmations}" if macd_cross_long else f"S:{short_confirmations}/{min_confirmations}"
            reasons = [f"Insufficient confirmations ({conf_str}) or score L:{long_score} S:{short_score} (need {min_score})"]

        # === QUALITY & CONFIDENCE ===
        if signal != "HOLD" and max_score > min_score:
            raw_quality = (score - min_score) / (max_score - min_score)
            quality = max(0.1, min(1.0, 0.1 + raw_quality * 0.9))
        else:
            quality = 0.0

        if signal != "HOLD":
            if quality >= 0.7:
                confidence = 0.85
            elif quality >= 0.4:
                confidence = 0.70
            else:
                confidence = 0.60
        else:
            confidence = 0.0

        # === BUILD RESULT ===
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

        # Log
        regime_label = regime.get("regime", "?") if regime else "?"
        if signal != "HOLD":
            info(f"📊 [MACDX] {signal} | {score}/{max_score} Q:{quality:.2f} [{regime_label}] | {confirmations} confirmations | {' '.join(reasons[:3])}")
        else:
            info(f"📊 [MACDX] HOLD | L:{long_score}({long_confirmations}) S:{short_score}({short_confirmations}) (need {min_score}, {min_confirmations}conf) [{regime_label}]")

        return result

    def should_close_position(self, analysis: dict, position: dict) -> dict:
        """Deterministic exit signal check."""
        if not position:
            return {"should_close": False, "reason": "No position", "urgency": "low"}

        # Support both dict and Position dataclass
        if hasattr(position, 'entry_price'):  # Position dataclass
            pos_type = "BUY" if position.is_long else "SELL"
            entry_price = float(position.entry_price)
        else:  # dict format
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

        # 1. MACD reversal against position
        if pos_type == "BUY" and macd_hist < 0:
            if pnl_pct >= 0.5:  # Take profit if in profit
                return {"should_close": True, "reason": f"MACD↓ + profit {pnl_pct:.1f}%", "urgency": "medium"}
            elif pnl_pct < -1.0:  # Cut loss if losing
                return {"should_close": True, "reason": f"MACD↓ + loss {pnl_pct:.1f}%", "urgency": "high"}

        if pos_type == "SELL" and macd_hist > 0:
            if pnl_pct >= 0.5:
                return {"should_close": True, "reason": f"MACD↑ + profit {pnl_pct:.1f}%", "urgency": "medium"}
            elif pnl_pct < -1.0:
                return {"should_close": True, "reason": f"MACD↑ + loss {pnl_pct:.1f}%", "urgency": "high"}

        # 2. RSI extreme
        if pos_type == "BUY" and rsi > 80:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} > 80 (overbought)", "urgency": "high"}
        if pos_type == "SELL" and rsi < 20:
            return {"should_close": True, "reason": f"RSI {rsi:.0f} < 20 (oversold)", "urgency": "high"}

        # 3. Take profit at good levels
        if pnl_pct >= 3.0:
            if (pos_type == "BUY" and rsi > 70) or (pos_type == "SELL" and rsi < 30):
                return {"should_close": True, "reason": f"Take profit +{pnl_pct:.1f}% RSI extreme", "urgency": "medium"}

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}

    def _hold_result(self, max_score: int, reasons: list, details: dict, regime: dict = None) -> dict:
        """Helper for generating HOLD result."""
        # Используем potential_score из details если есть
        actual_score = details.get('potential_score', 0)
        # Для HOLD показываем реальное количество подтверждений (даже если MACD не кросс)
        # Это позволяет видеть сколько индикаторов уже подтверждают направление
        actual_confirmations = details.get('confirmations', 0)
        return {
            "signal": "HOLD",
            "score": actual_score,
            "max_score": max_score,
            "quality": 0.0,
            "confidence": 0.0,
            "reasons": reasons,
            "confirmations": actual_confirmations,
            "filters_passed": False,
            "details": details,
            "regime": regime.get("regime", "UNKNOWN") if regime else "NO_REGIME",
        }

    @staticmethod
    def _detect_rsi_divergence(prices: list, rsi_values: list) -> tuple:
        """
        Detect RSI divergence over the given window.

        Returns:
            (bearish_divergence, bullish_divergence) booleans

        Улучшения:
        - Ограничивает окно поиска последними 30 свечами
        - Требует минимальное расстояние между экстремумами (3 свечи)
        - Игнорирует экстремумы слишком близко к концу окна
        """
        if not prices or not rsi_values:
            return False, False

        n = min(len(prices), len(rsi_values))
        if n < 10:
            return False, False

        # Ограничиваем окно последними 30 свечами для актуальности
        max_window = 30
        prices = prices[-max_window:]
        rsi_values = rsi_values[-max_window:]
        n = len(prices)

        min_extrema_distance = 3  # Минимум 3 свечи между экстремумами

        # Find local maxima (for bearish divergence detection)
        maxima = []
        for i in range(1, n - 1):
            if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
                # Проверяем минимальное расстояние от предыдущего экстремума
                if not maxima or (i - maxima[-1]) >= min_extrema_distance:
                    maxima.append(i)

        # Find local minima (for bullish divergence detection)
        minima = []
        for i in range(1, n - 1):
            if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
                # Проверяем минимальное расстояние от предыдущего экстремума
                if not minima or (i - minima[-1]) >= min_extrema_distance:
                    minima.append(i)

        bearish_div = False
        bullish_div = False

        # Bearish divergence: price higher high + RSI lower high
        if len(maxima) >= 2:
            prev_max = maxima[-2]
            last_max = maxima[-1]
            if prices[last_max] > prices[prev_max] and rsi_values[last_max] < rsi_values[prev_max]:
                bearish_div = True

        # Bullish divergence: price lower low + RSI higher low
        if len(minima) >= 2:
            prev_min = minima[-2]
            last_min = minima[-1]
            if prices[last_min] < prices[prev_min] and rsi_values[last_min] > rsi_values[prev_min]:
                bullish_div = True

        return bearish_div, bullish_div


# Singleton
_generator = None


def get_macdx_signal_generator() -> MACDXSignalGenerator:
    global _generator
    if _generator is None:
        _generator = MACDXSignalGenerator()
    return _generator


def generate_macdx_signal(analysis: dict, regime: dict = None) -> dict:
    return get_macdx_signal_generator().generate_signal(analysis, regime)


def should_close_macdx(analysis: dict, position: dict) -> dict:
    return get_macdx_signal_generator().should_close_position(analysis, position)
