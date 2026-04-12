"""MACDX — детерминированный генератор сигналов на основе MACD-кроссовера."""

from typing import Any, Dict, Optional

from src.utils.logger import debug, info, warning

from .base import BaseSignalGenerator
from .utils import detect_rsi_divergence

# Таймфрейм → минуты (для логов и расчёта абсолютного времени)
TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1D": 1440,
}


def tf_to_minutes(timeframe: str) -> int:
    """Конвертирует строку таймфрейма в минуты."""
    return TIMEFRAME_MINUTES.get(timeframe, 60)


class MacdxSignalGenerator(BaseSignalGenerator):
    """Deterministic signal generator based on MACD crossover with confirmations."""

    def __init__(self, settings: Dict):
        super().__init__(settings)
        self.rules = self.settings.get("signal_rules", {})
        self.exit_rules = self.settings.get("exit_rules", {})
        self.preset = self.settings.get("preset", {})

    def generate(self, analysis: Dict, regime: Optional[Dict] = None) -> Dict:
        # Minimal logging for backtest
        current_price = analysis.get("current_price") or 0
        rsi = analysis.get("rsi") or 50
        volume_ratio = analysis.get("volume_ratio") or 1.0
        ema9 = analysis.get("ema9") or 0
        ema21 = analysis.get("ema21") or 0
        macd_line = analysis.get("macd_line") or 0
        macd_signal = analysis.get("macd_signal") or 0
        macd_hist = analysis.get("macd_hist") or 0
        macd_hist_prev = analysis.get("macd_hist_prev") or 0
        macd_hist_2prev = analysis.get("macd_hist_2prev") or 0
        bb_upper = analysis.get("bb_upper") or 0
        bb_lower = analysis.get("bb_lower") or 0
        bb_middle = analysis.get("bb_middle") or 0
        atr = analysis.get("atr") or 0
        atr_ratio = analysis.get("atr_ratio") or 1.0
        adx = analysis.get("adx") or 25

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

        max_score_base = (
            macd_cross_weight
            + rsi_weight
            + ema_weight
            + not_sideways_weight
            + no_exhaustion_weight
            + volume_weight
        )
        enable_volume_filter = self.rules.get("enable_volume_filter", True)
        if not enable_volume_filter:
            max_score_base -= volume_weight
        max_score = max_score_base

        if regime and regime.get("recommended_min_score"):
            min_score = regime["recommended_min_score"]

        if atr_ratio < min_atr_ratio:
            debug(f"[MACDX] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return self._hold_result(
                max_score,
                [f"Low volatility (ATR {atr_ratio:.2f})"],
                {
                    "atr_ratio": atr_ratio,
                    "filter": "volatility",
                    "confirmations": 0,
                    "potential_score": 0,
                },
                regime,
            )

        enable_volume_filter = self.rules.get("enable_volume_filter", True)
        ignore_volume_on_strong = self.rules.get("ignore_volume_on_strong_trend", True)
        
        # Проверяем strengthening trend ДО проверки volume
        macd_hist = analysis.get("macd_hist", 0)
        macd_hist_prev = analysis.get("macd_hist_prev", 0)
        macd_hist_2prev = analysis.get("macd_hist_2prev", 0)
        has_strong_trend = (macd_hist > 0 and macd_hist_prev > 0) or (macd_hist < 0 and macd_hist_prev < 0)
        has_strengthening_3 = (macd_hist > 0 and macd_hist > macd_hist_prev and macd_hist_prev > macd_hist_2prev) or (macd_hist < 0 and macd_hist < macd_hist_prev and macd_hist_prev < macd_hist_2prev)
        has_strengthening_2 = (macd_hist > 0 and macd_hist > macd_hist_prev) or (macd_hist < 0 and macd_hist < macd_hist_prev)
        
        # Если есть strengthening trend 2+ свечи - пропускаем проверку volume
        skip_volume_check = enable_volume_filter and ignore_volume_on_strong and (has_strengthening_3 or has_strengthening_2)
        
        print(f"[MACDX] DEBUG volume: volume_ratio={volume_ratio:.2f}, min_volume={min_volume}, enable_vol={enable_volume_filter}, ignore={ignore_volume_on_strong}, strong_trend={has_strong_trend}, str_3={has_strengthening_3}, str_2={has_strengthening_2}, skip={skip_volume_check}")
        
        if enable_volume_filter and volume_ratio < min_volume and not skip_volume_check:
            debug(f"[MACDX] HOLD | Low volume ({volume_ratio:.2f}x)")
            return self._hold_result(
                max_score,
                [f"Low volume ({volume_ratio:.2f}x)"],
                {
                    "volume_ratio": volume_ratio,
                    "filter": "volume",
                    "confirmations": 0,
                    "potential_score": 0,
                },
                regime,
            )

        consecutive_red_filter = self.rules.get("consecutive_red_filter", True)
        min_consecutive_for_block = self.rules.get("min_consecutive_for_block", 3)
        enable_counter_trend_filter = self.rules.get(
            "enable_counter_trend_filter", True
        )
        counter_trend_ema_threshold = self.rules.get("counter_trend_ema_threshold", 1.0)
        last_5_direction = analysis.get("last_5_direction") or "MIXED"

        require_2_candle_confirmation = self.rules.get(
            "require_2_candle_confirmation", False
        )

        if require_2_candle_confirmation:
            potential_long = (
                macd_hist > 0 and macd_hist_prev > 0 and macd_hist_2prev <= 0
            )
            potential_short = (
                macd_hist < 0 and macd_hist_prev < 0 and macd_hist_2prev >= 0
            )
        else:
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
                    debug(f"[MACDX] HOLD | {block_reason}")
                    return self._hold_result(
                        max_score,
                        [block_reason],
                        {
                            "last_5_direction": last_5_direction,
                            "filter": "consecutive_red_momentum",
                            "confirmations": 1,
                            "potential_score": 2,
                        },
                        regime,
                    )

        if enable_counter_trend_filter and ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100
            if (
                ema9 < ema21
                and ema_diff_pct > counter_trend_ema_threshold
                and potential_long
            ):
                debug(f"[MACDX] HOLD | Counter-trend: EMA down {ema_diff_pct:.1f}%")
                return self._hold_result(
                    max_score,
                    [f"Counter-trend: EMA below by {ema_diff_pct:.1f}%"],
                    {
                        "ema_diff_pct": ema_diff_pct,
                        "filter": "counter_trend",
                        "confirmations": 2,
                        "potential_score": 4,
                    },
                    regime,
                )
            if (
                ema9 > ema21
                and ema_diff_pct > counter_trend_ema_threshold
                and potential_short
            ):
                debug(f"[MACDX] HOLD | Counter-trend: EMA up {ema_diff_pct:.1f}%")
                return self._hold_result(
                    max_score,
                    [f"Counter-trend: EMA above by {ema_diff_pct:.1f}%"],
                    {
                        "ema_diff_pct": ema_diff_pct,
                        "filter": "counter_trend",
                        "confirmations": 2,
                        "potential_score": 4,
                    },
                    regime,
                )

        macd_cross_long = False
        macd_cross_short = False

        if require_2_candle_confirmation:
            # 2-candle confirmation: crossover happened 1 candle ago, confirmed now
            if macd_hist_prev > 0 and macd_hist_2prev <= 0 and macd_hist > 0:
                macd_cross_long = True
                debug(
                    f"[MACDX] Bullish MACD 2-candle confirmed: hist_2prev={macd_hist_2prev:.6f} <= 0, hist_prev={macd_hist_prev:.6f} > 0, hist={macd_hist:.6f} > 0"
                )
            if macd_hist_prev < 0 and macd_hist_2prev >= 0 and macd_hist < 0:
                macd_cross_short = True
                debug(
                    f"[MACDX] Bearish MACD 2-candle confirmed: hist_2prev={macd_hist_2prev:.6f} >= 0, hist_prev={macd_hist_prev:.6f} < 0, hist={macd_hist:.6f} < 0"
                )
        else:
            # Immediate crossover
            if macd_hist_prev <= 0 and macd_hist > 0:
                macd_cross_long = True
                debug(
                    f"[MACDX] Bullish MACD crossover detected: hist_prev={macd_hist_prev:.6f} <= 0, hist={macd_hist:.6f} > 0"
                )
            if macd_hist_prev >= 0 and macd_hist < 0:
                macd_cross_short = True
                debug(
                    f"[MACDX] Bearish MACD crossover detected: hist_prev={macd_hist_prev:.6f} >= 0, hist={macd_hist:.6f} < 0"
                )

        debug(
            f"[MACDX] Crossover check: long={macd_cross_long}, short={macd_cross_short}"
        )

        if not macd_cross_long and not macd_cross_short:
            debug(
                f"[MACDX] HOLD | No MACD crossover (hist: {macd_hist:.6f}, hist_prev: {macd_hist_prev:.6f})"
            )
            is_sideways = False
            bb_width = 0
            if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
                bb_width = (bb_upper - bb_lower) / bb_middle * 100
                if bb_width < bb_width_threshold or adx < adx_threshold:
                    is_sideways = True

            close_prices = analysis.get("close_prices", [])
            rsi_values = analysis.get("rsi_values", [])
            bearish_div, bullish_div = detect_rsi_divergence(close_prices, rsi_values)

            indicators_status = [
                {
                    "name": "MACD Crossover",
                    "weight": macd_cross_weight,
                    "ok": False,
                    "value": f"hist={macd_hist:.6f}, prev={macd_hist_prev:.6f}",
                    "detail": "\u041d\u0435\u0442 \u043f\u0435\u0440\u0435\u0441\u0435\u0447\u0435\u043d\u0438\u044f",
                },
                {
                    "name": "RSI Zone",
                    "weight": rsi_weight,
                    "ok": (35 <= rsi <= 65),
                    "value": f"{rsi:.1f}",
                    "detail": "\u0412 \u0437\u043e\u043d\u0435"
                    if 35 <= rsi <= 65
                    else "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445",
                },
                {
                    "name": "EMA Alignment",
                    "weight": ema_weight,
                    "ok": (ema9 > ema21 if ema9 > 0 and ema21 > 0 else False),
                    "value": f"9:{ema9:.2f} 21:{ema21:.2f}",
                    "detail": "\u0411\u044b\u0447\u0438\u0439 (9>21)"
                    if ema9 > ema21
                    else "\u041c\u0435\u0434\u0432\u0435\u0436\u0438\u0439 (9<21)",
                },
                {
                    "name": "Not Sideways",
                    "weight": not_sideways_weight,
                    "ok": not is_sideways,
                    "value": f"BB:{bb_width:.1f}% ADX:{adx:.0f}",
                    "detail": "\u0422\u0440\u0435\u043d\u0434 \u0435\u0441\u0442\u044c"
                    if not is_sideways
                    else "\u0411\u043e\u043a\u043e\u0432\u0438\u043a",
                },
                {
                    "name": "No Exhaustion",
                    "weight": no_exhaustion_weight,
                    "ok": (not bearish_div and not bullish_div),
                    "value": "",
                    "detail": "\u041d\u0435\u0442 \u0434\u0438\u0432\u0435\u0440\u0433\u0435\u043d\u0446\u0438\u0438"
                    if not bearish_div and not bullish_div
                    else "\u0415\u0441\u0442\u044c \u0434\u0438\u0432\u0435\u0440\u0433\u0435\u043d\u0446\u0438\u044f",
                },
                {
                    "name": "Volume",
                    "weight": volume_weight,
                    "ok": (volume_ratio >= 0.8),
                    "value": f"{volume_ratio:.1f}x",
                    "detail": "\u0414\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u044b\u0439"
                    if volume_ratio >= 0.8
                    else "\u0421\u043b\u0430\u0431\u044b\u0439",
                },
            ]

            ok_count = sum(1 for s in indicators_status if s["ok"])
            potential_score = sum(s["weight"] for s in indicators_status if s["ok"])
            max_possible_score = sum(s["weight"] for s in indicators_status)
            indicators_ok = [
                f"{s['name']}: {s['detail']}" for s in indicators_status if s["ok"]
            ]
            indicators_fail = [
                f"{s['name']}: {s['detail']}" for s in indicators_status if not s["ok"]
            ]

            return self._hold_result(
                max_score,
                ["No MACD crossover"],
                {
                    "macd_hist": macd_hist,
                    "filter": "no_macd_cross",
                    "potential_score": potential_score,
                    "confirmations": ok_count,
                    "max_confirmations": len(indicators_status),
                    "indicators_ok": indicators_ok,
                    "indicators_fail": indicators_fail,
                    "indicators_status": indicators_status,
                    "indicators_ok_count": ok_count,
                    "indicators_total_count": len(indicators_status),
                    "max_possible_score": max_possible_score,
                },
                regime,
            )

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

        # Strengthening trend: 2+ candles in same direction (even without crossover)
        enable_strengthening = self.rules.get("enable_strengthening_trend_entry", False)
        print(f"[MACDX] DEBUG: enable_str={enable_strengthening}, weight={self.rules.get('strengthening_trend_weight', 2)}, hist={analysis.get('macd_hist', 0):.4f}, hist_prev={analysis.get('macd_hist_prev', 0):.4f}")
        if enable_strengthening:
            strengthening_weight = self.rules.get("strengthening_trend_weight", 2)
            min_candles = self.rules.get("strengthening_trend_min_candles", 2)
            macd_hist = analysis.get("macd_hist", 0)
            macd_hist_prev = analysis.get("macd_hist_prev", 0)
            macd_hist_2prev = analysis.get("macd_hist_2prev", 0)

            # Check for strengthening trend (min_candles candles in same direction)
            if min_candles >= 4:
                strengthening_long = macd_hist > 0 and macd_hist_prev > 0 and macd_hist_2prev > 0
                strengthening_short = macd_hist < 0 and macd_hist_prev < 0 and macd_hist_2prev < 0
            elif min_candles >= 2:
                strengthening_long = macd_hist > 0 and macd_hist_prev > 0
                strengthening_short = macd_hist < 0 and macd_hist_prev < 0
            else:
                strengthening_long = False
                strengthening_short = False

            if strengthening_long:
                long_score += strengthening_weight
                long_reasons.append(f"Strengthening trend +{strengthening_weight}\n")
                long_confirmations += 1
                debug(f"[MACDX] Strengthening trend LONG: hist={macd_hist:.4f}")
            if strengthening_short:
                short_score += strengthening_weight
                short_reasons.append(f"Strengthening trend +{strengthening_weight}\n")
                short_confirmations += 1
                debug(f"[MACDX] Strengthening trend SHORT: hist={macd_hist:.4f}")

        rsi_long_ok = rsi_long_min <= rsi <= rsi_long_max
        rsi_short_ok = rsi_short_min <= rsi <= rsi_short_max
        rsi_extreme_oversold = rsi < 30
        rsi_extreme_overbought = rsi > 70
        long_blocked = False
        short_blocked = False

        if macd_cross_long:
            if rsi_extreme_overbought:
                long_reasons.append(
                    f"RSI {rsi:.0f} EXTREME OVERBOUGHT - блокируем LONG\n"
                )
                long_score = 0
                long_blocked = True
                debug(f"[MACDX] RSI blocks LONG: extreme overbought ({rsi:.0f})")
            elif rsi_long_ok:
                long_score += rsi_weight
                long_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}\n")
                long_confirmations += 1
                debug(f"[MACDX] RSI confirms LONG: {rsi:.0f} in zone, +{rsi_weight}")
            else:
                long_reasons.append(
                    f"RSI {rsi:.0f} outside zone (need {rsi_long_min}-{rsi_long_max})\n"
                )
                debug(f"[MACDX] RSI rejects LONG: {rsi:.0f} outside zone")

        if macd_cross_short:
            if rsi_extreme_oversold:
                short_reasons.append(
                    f"RSI {rsi:.0f} EXTREME OVERSOLD - блокируем SELL\n"
                )
                short_score = 0
                short_confirmations = 0
                short_blocked = True
                debug(f"[MACDX] RSI blocks SHORT: extreme oversold ({rsi:.0f})")
            elif rsi_short_ok:
                short_score += rsi_weight
                short_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}\n")
                short_confirmations += 1
                debug(f"[MACDX] RSI confirms SHORT: {rsi:.0f} in zone, +{rsi_weight}")
            else:
                short_reasons.append(
                    f"RSI {rsi:.0f} outside zone (need {rsi_short_min}-{rsi_short_max})\n"
                )
                debug(f"[MACDX] RSI rejects SHORT: {rsi:.0f} outside zone")

        ema_long = False
        ema_short = False
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            debug(
                f"[MACDX] EMA: 9={ema9:.2f}, 21={ema21:.2f}, diff={ema_diff_pct:.1f}%"
            )
            if ema9 > ema21:
                ema_long = True
                if macd_cross_long and not long_blocked:
                    long_score += ema_weight
                    long_reasons.append(f"EMA↑ ({ema_diff_pct:.1f}%) +{ema_weight}\n")
                    long_confirmations += 1
                    debug(
                        f"[MACDX] EMA confirms LONG: bullish alignment, +{ema_weight}"
                    )
            elif ema9 < ema21:
                ema_short = True
                if macd_cross_short and not short_blocked:
                    short_score += ema_weight
                    short_reasons.append(f"EMA↓ ({ema_diff_pct:.1f}%) +{ema_weight}\n")
                    short_confirmations += 1
                    debug(
                        f"[MACDX] EMA confirms SHORT: bearish alignment, +{ema_weight}"
                    )
            else:
                debug(f"[MACDX] EMA neutral: flat")

        if macd_cross_long and ema_short:
            long_reasons.append("EMA counter-trend (caution)\n")
        if macd_cross_short and ema_long:
            short_reasons.append("EMA counter-trend (caution)\n")

        is_sideways = False
        bb_width = 0
        sideways_block_signals = self.rules.get("sideways_block_signals", False)

        if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
            bb_width = (bb_upper - bb_lower) / bb_middle * 100
            if bb_width < bb_width_threshold or adx < adx_threshold:
                is_sideways = True

        debug(
            f"[MACDX] Sideways check: BB={bb_width:.1f}%, ADX={adx:.0f}, is_sideways={is_sideways}, block_signals={sideways_block_signals}"
        )

        # Полностью блокировать сигналы при боковике, если включено
        if is_sideways and sideways_block_signals:
            debug(f"[MACDX] BLOCKED: Sideways market detected - no trading")
            return self._hold_result(max_score, ["Sideways market - trading blocked"])

        if not is_sideways:
            if macd_cross_long and not long_blocked:
                long_score += not_sideways_weight
                long_reasons.append(
                    f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}\n"
                )
                long_confirmations += 1
                debug(f"[MACDX] Not sideways confirms LONG: +{not_sideways_weight}")
            if macd_cross_short and not short_blocked:
                short_score += not_sideways_weight
                short_reasons.append(
                    f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}\n"
                )
                short_confirmations += 1
                debug(f"[MACDX] Not sideways confirms SHORT: +{not_sideways_weight}")
        else:
            debug(f"[MACDX] Sideways market detected")
            if macd_cross_long:
                long_reasons.append(
                    f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})\n"
                )
            if macd_cross_short:
                short_reasons.append(
                    f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})\n"
                )

        close_prices = analysis.get("close_prices", [])
        rsi_values = analysis.get("rsi_values", [])
        bearish_div, bullish_div = detect_rsi_divergence(close_prices, rsi_values)

        debug(f"[MACDX] Divergence: bearish={bearish_div}, bullish={bullish_div}")

        if macd_cross_long and not long_blocked:
            if not bearish_div:
                long_score += no_exhaustion_weight
                long_reasons.append(f"No bearish divergence +{no_exhaustion_weight}\n")
                long_confirmations += 1
                debug(f"[MACDX] No exhaustion confirms LONG: +{no_exhaustion_weight}")
            else:
                long_reasons.append("Bearish RSI divergence (exhaustion warning)\n")
                debug(f"[MACDX] Exhaustion rejects LONG: bearish divergence")

        if macd_cross_short and not short_blocked:
            if not bullish_div:
                short_score += no_exhaustion_weight
                short_reasons.append(f"No bullish divergence +{no_exhaustion_weight}\n")
                short_confirmations += 1
                debug(f"[MACDX] No exhaustion confirms SHORT: +{no_exhaustion_weight}")
            else:
                short_reasons.append("Bullish RSI divergence (exhaustion warning)\n")
                debug(f"[MACDX] Exhaustion rejects SHORT: bullish divergence")

        volume_confirm_threshold = self.rules.get("volume_confirm_threshold", 0.8)
        volume_ok = volume_ratio >= volume_confirm_threshold
        debug(
            f"[MACDX] Volume: {volume_ratio:.1f}x (threshold {volume_confirm_threshold:.1f}), ok={volume_ok}"
        )
        if volume_ok:
            if macd_cross_long and not long_blocked:
                long_score += volume_weight
                long_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}\n")
                long_confirmations += 1
                debug(f"[MACDX] Volume confirms LONG: +{volume_weight}")
            if macd_cross_short and not short_blocked:
                short_score += volume_weight
                short_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}\n")
                short_confirmations += 1
                debug(f"[MACDX] Volume confirms SHORT: +{volume_weight}")
        else:
            debug(f"[MACDX] Volume insufficient for confirmation")

        min_confirmations = self.rules.get("min_confirmations", 3)

        # Log scoring details
        try:
            debug(
                f"[MACDX] Scoring: LONG {long_score}/{max_score} ({long_confirmations} conf), SHORT {short_score}/{max_score} ({short_confirmations} conf), min_score={min_score}, min_conf={min_confirmations}"
            )
        except Exception as e:
            debug(f"[MACDX] Scoring log error: {e}")

        signal = "HOLD"
        score = 0
        reasons = []
        confirmations = 0

        # DEBUG
        print(f"[MACDX] DEBUG: long_score={long_score}, short_score={short_score}, min_score={min_score}")

        # Упрощённая логика: только score (веса определяют силу сигнала)
        if long_score >= min_score and long_score > short_score:
            signal = "BUY"
            score = long_score
            reasons = long_reasons
            confirmations = long_confirmations
        elif short_score >= min_score and short_score > long_score:
            signal = "SELL"
            score = short_score
            reasons = short_reasons
            confirmations = short_confirmations
        elif long_score >= min_score and short_score >= min_score:
            # Конфликт сигналов
            signal = "HOLD"
            score = max(long_score, short_score)
            reasons = [
                f"CONFLICT L:{long_score}({long_confirmations}conf) S:{short_score}({short_confirmations}conf)"
            ]
        else:
            # Trend-follow вход: MACD тренд усиливается 2-3 свечи подряд, без проверки volume
            macd_hist = analysis.get("macd_hist", 0)
            macd_hist_prev = analysis.get("macd_hist_prev", 0)
            macd_hist_2prev = analysis.get("macd_hist_2prev", 0)
            enable_strengthening = self.rules.get("enable_strengthening_trend_entry", True)
            strengthening_min_candles = self.rules.get("strengthening_trend_min_candles", 3)
            
            if enable_strengthening and strengthening_min_candles >= 3:
                trend_strong_long = macd_hist > 0 and macd_hist > macd_hist_prev and macd_hist_prev > macd_hist_2prev
                trend_strong_short = macd_hist < 0 and macd_hist < macd_hist_prev and macd_hist_prev < macd_hist_2prev
            elif enable_strengthening and strengthening_min_candles >= 2:
                trend_strong_long = macd_hist > 0 and macd_hist > macd_hist_prev
                trend_strong_short = macd_hist < 0 and macd_hist < macd_hist_prev
            else:
                trend_strong_long = False
                trend_strong_short = False
            
            if trend_strong_long:
                signal = "BUY"
                score = long_score
                reasons = ["MACD trend 2-3 candles +"]
                confirmations = 1
                print(f"[MACDX] DEBUG trend_follow: signal=BUY hist={macd_hist:.4f}")
            elif trend_strong_short:
                signal = "SELL"
                score = short_score
                reasons = ["MACD trend 2-3 candles +"]
                confirmations = 1
                print(f"[MACDX] DEBUG trend_follow: signal=SELL hist={macd_hist:.4f}")
            else:
                signal = "HOLD"
                score = max(long_score, short_score)
                conf_str = (
                    f"L:{long_confirmations}/{min_confirmations}"
                    if macd_cross_long
                    else f"S:{short_confirmations}/{min_confirmations}"
                )
                reasons = [
                    f"Insufficient confirmations ({conf_str}) or score L:{long_score} S:{short_score} (need {min_score})"
                ]

        info(
            f"[MACDX] Final signal: {signal}, score={score}, confirmations={confirmations}, reasons={''.join(reasons).strip()}"
        )

        if signal != "HOLD" and max_score > min_score:
            raw_quality = (score - min_score) / (max_score - min_score)
            quality = max(0.1, min(1.0, 0.1 + raw_quality * 0.9))
        elif signal != "HOLD":
            quality = 0.5
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
                debug(
                    f"[MACDX] {signal} | {score}/{max_score} Q:{quality:.2f} [{regime_label}] | {confirmations} confirmations"
                )
            else:
                debug(
                    f"[MACDX] HOLD | L:{long_score}({long_confirmations}) S:{short_score}({short_confirmations}) (need {min_score}, {min_confirmations}conf) [{regime_label}]"
                )
        except Exception as e:
            debug(f"[MACDX] Final log error: {e}, signal={signal}, score={score}")

        return result

    # =========================================================================
    # SHOULD CLOSE — приоритизированная система выходов
    # =========================================================================

    def should_close(self, analysis: Dict, position: Any, **kwargs) -> Dict:
        """
        Приоритизация выходов (основной цикл — по закрытым свечам):
        1. Экстренные выходы (critical) — немедленное закрытие
        2. Trailing profit (high) — защита накопленной прибыли
        3. Ослабление MACD импульса (medium) — оптимальный выход на пике
        4. Импульсные свечи + прибыль (medium) — фиксация на волатильности
        5. ATR trailing stop (high) — trailing от пиковой цены
        6. Max hold time (low) — ограничение времени в позиции
        7. Текущие правила (RSI экстремумы, MACD разворот + PnL)

        Пампы/дампы между свечами обрабатываются быстрым циклом
        (position_guard_check) в process_worker.
        """
        if not position:
            return {"should_close": False, "reason": "No position", "urgency": "low"}

        if hasattr(position, "entry_price"):
            pos_type = "BUY" if position.is_long else "SELL"
            entry_price = float(position.entry_price)
        else:
            pos_type = position.get("type", "").upper()
            entry_price = float(position.get("entry", position.get("avgPrice", 0)))

        current_price = analysis.get("current_price", 0)
        rsi = analysis.get("rsi", 50)
        macd_hist = analysis.get("macd_hist", 0)
        macd_hist_prev = analysis.get("macd_hist_prev", 0)
        atr = analysis.get("atr", 0)
        adx = analysis.get("adx", 25)

        if entry_price <= 0 or current_price <= 0:
            return {"should_close": False, "reason": "Invalid prices", "urgency": "low"}

        leverage = self.preset.get("leverage", 6)
        if pos_type == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price * 100 * leverage
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100 * leverage

        # Получаем exit_context и regime из kwargs
        exit_context = kwargs.get("exit_context", {})
        regime = analysis.get("regime", {})

        # Обновляем exit_context
        if exit_context is not None:
            self._update_exit_context(
                exit_context,
                current_price,
                entry_price,
                pos_type,
                pnl_pct,
                macd_hist,
                atr,
            )

        # Получаем адаптивные пороги по режиму
        regime_params = self._get_regime_params(regime)

        # === 1. ЭКСТРЕННЫЕ ВЫХОДЫ ===
        emergency = self._check_emergency_exit(
            analysis, pos_type, pnl_pct, atr, macd_hist, macd_hist_prev, exit_context
        )
        if emergency.get("should_close"):
            return emergency

        # === 2. TRAILING PROFIT ===
        trailing = self._check_trailing_profit(pnl_pct, exit_context, regime_params)
        if trailing.get("should_close"):
            return trailing

        # === 3. ОСЛАБЛЕНИЕ MACD ИМПУЛЬСА ===
        momentum = self._check_macd_weakening(
            analysis, pos_type, pnl_pct, exit_context, regime_params
        )
        if momentum.get("should_close"):
            return momentum

        # === 4. ИМПУЛЬСНЫЕ СВЕЧИ ===
        impulse = self._check_impulse_exit(analysis, pnl_pct, regime_params)
        if impulse.get("should_close"):
            return impulse

        # === 5. ATR TRAILING STOP ===
        atr_trailing = self._check_atr_trailing_stop(
            current_price, entry_price, pos_type, atr, exit_context
        )
        if atr_trailing.get("should_close"):
            return atr_trailing

        # === 6. MAX HOLD TIME ===
        max_hold = self._check_max_hold_time(exit_context, pnl_pct)
        if max_hold.get("should_close"):
            return max_hold

        # === 7. СТАНДАРТНЫЕ ВЫХОДЫ (существующая логика) ===
        return self._check_standard_exits(pos_type, pnl_pct, rsi, macd_hist)

    def _update_exit_context(
        self,
        ctx: Dict,
        current_price: float,
        entry_price: float,
        pos_type: str,
        pnl_pct: float,
        macd_hist: float,
        atr: float,
    ):
        """Обновляет exit_context при каждом основном цикле."""
        # peak_price
        peak_price = ctx.get("peak_price", entry_price)
        if pos_type == "BUY":
            peak_price = max(peak_price, current_price)
        else:
            peak_price = min(peak_price, current_price)
        ctx["peak_price"] = peak_price

        # peak_pnl
        peak_pnl = ctx.get("peak_pnl", 0)
        ctx["peak_pnl"] = max(peak_pnl, pnl_pct)

        # candles_in_trade
        ctx["candles_in_trade"] = ctx.get("candles_in_trade", 0) + 1

        # last_atr (для быстрого цикла)
        if atr > 0:
            ctx["last_atr"] = atr

        # MACD peak histogram tracking
        abs_hist = abs(macd_hist)
        peak_hist = ctx.get("macd_peak_hist", 0)
        if abs_hist > abs(peak_hist):
            ctx["macd_peak_hist"] = macd_hist
            ctx["macd_peak_candle"] = ctx.get("candles_in_trade", 1)

        # Weakening count (consecutive candles where hist weakens)
        if abs(peak_hist) > 0 and abs_hist < abs(peak_hist):
            ctx["weakening_count"] = ctx.get("weakening_count", 0) + 1
        else:
            ctx["weakening_count"] = 0

    def _get_regime_params(self, regime: Dict) -> Dict:
        """Возвращает адаптивные пороги на основе рыночного режима."""
        regime_adaptation = self.exit_rules.get("regime_adaptation", {})
        if not regime_adaptation.get("enabled", True):
            return {
                "weakening_threshold": 0.50,
                "trailing_drawdown": 0.40,
                "impulse_min_profit": 1.5,
            }

        regime_name = regime.get("regime", "TRANSITIONAL") if regime else "TRANSITIONAL"
        defaults = {
            "weakening_threshold": 0.50,
            "trailing_drawdown": 0.40,
            "impulse_min_profit": 1.5,
        }
        return regime_adaptation.get(regime_name, defaults)

    def _check_emergency_exit(
        self,
        analysis: Dict,
        pos_type: str,
        pnl_pct: float,
        atr: float,
        macd_hist: float,
        macd_hist_prev: float,
        exit_context: Dict,
    ) -> Dict:
        """Экстренные выходы — максимальный приоритет."""
        emergency_cfg = self.exit_rules.get("emergency", {})
        if not emergency_cfg:
            return {"should_close": False}
        if not emergency_cfg.get("enabled", False):
            return {"should_close": False}

        # Защита: не закрывать если позиция соответствует текущему MACD тренду
        if emergency_cfg.get("protect_matching_trend", True):
            position_matches_trend = (pos_type == "BUY" and macd_hist > 0) or (
                pos_type == "SELL" and macd_hist < 0
            )
            if position_matches_trend:
                # Только проверяем max_loss — остальные emergency пропускаем
                max_loss_pct = emergency_cfg.get("max_loss_pct", 3.0)
                if pnl_pct <= -max_loss_pct:
                    return {
                        "should_close": True,
                        "reason": f"Max loss guard: {pnl_pct:.1f}% (matching trend)",
                        "urgency": "critical",
                    }
                return {"should_close": False}

        # Резкий разворот гистограммы (смена знака + значительная величина)
        if atr > 0:
            macd_reversal_threshold = atr * 0.001
            if (
                pos_type == "BUY"
                and macd_hist_prev > 0
                and macd_hist < -macd_reversal_threshold
            ):
                return {
                    "should_close": True,
                    "reason": "MACD instant reversal (bearish)",
                    "urgency": "critical",
                }
            if (
                pos_type == "SELL"
                and macd_hist_prev < 0
                and macd_hist > macd_reversal_threshold
            ):
                return {
                    "should_close": True,
                    "reason": "MACD instant reversal (bullish)",
                    "urgency": "critical",
                }

        # Убыток превышает max_loss_atr * ATR
        if atr > 0:
            max_loss_atr = emergency_cfg.get("max_loss_atr", 2.0)
            leverage = self.preset.get("leverage", 6)
            current_price = analysis.get("current_price", 0)
            entry_price_raw = exit_context.get("entry_price", current_price)
            if pos_type == "BUY":
                loss_in_atr = (
                    (entry_price_raw - current_price) / atr
                    if current_price < entry_price_raw
                    else 0
                )
            else:
                loss_in_atr = (
                    (current_price - entry_price_raw) / atr
                    if current_price > entry_price_raw
                    else 0
                )
            if loss_in_atr > max_loss_atr:
                return {
                    "should_close": True,
                    "reason": f"Max loss exceeded ({loss_in_atr:.1f}x ATR)",
                    "urgency": "critical",
                }

        # Максимальный убыток в %
        max_loss_pct = emergency_cfg.get("max_loss_pct", 3.0)
        if pnl_pct <= -max_loss_pct:
            return {
                "should_close": True,
                "reason": f"Max loss guard: {pnl_pct:.1f}%",
                "urgency": "critical",
            }

        return {"should_close": False}

    def _check_trailing_profit(
        self, pnl_pct: float, exit_context: Dict, regime_params: Dict
    ) -> Dict:
        """Trailing profit — защита накопленной прибыли."""
        tp_cfg = self.exit_rules.get("trailing_profit", {})
        if not tp_cfg.get("enabled", True):
            return {"should_close": False}
        if not exit_context:
            return {"should_close": False}

        peak_pnl = exit_context.get("peak_pnl", 0)
        activation_pnl = tp_cfg.get("activation_pnl", 3.0)
        hysteresis_buffer = tp_cfg.get("hysteresis_buffer", 0.1)

        if peak_pnl < activation_pnl:
            return {"should_close": False}

        drawdown_from_peak = (peak_pnl - pnl_pct) / peak_pnl if peak_pnl > 0 else 0

        # Адаптивный порог: чем больше прибыль, тем строже защита
        drawdown_levels = tp_cfg.get("drawdown_levels", {})
        high_cfg = drawdown_levels.get(
            "high_profit", {"threshold": 8.0, "max_drawdown": 0.40}
        )
        med_cfg = drawdown_levels.get(
            "medium_profit", {"threshold": 5.0, "max_drawdown": 0.50}
        )
        low_cfg = drawdown_levels.get(
            "low_profit", {"threshold": 3.0, "max_drawdown": 0.60}
        )

        if peak_pnl >= high_cfg["threshold"]:
            max_drawdown = high_cfg["max_drawdown"]
        elif peak_pnl >= med_cfg["threshold"]:
            max_drawdown = med_cfg["max_drawdown"]
        else:
            max_drawdown = low_cfg["max_drawdown"]

        # Адаптация по режиму
        regime_trailing = regime_params.get("trailing_drawdown")
        if regime_trailing is not None:
            max_drawdown = regime_trailing

        # Hysteresis: буфер для предотвращения закрытия на временных колебаниях
        effective_threshold = max_drawdown + hysteresis_buffer
        last_drawdown = exit_context.get("trailing_drawdown_max", 0)

        # Обновить максимальный drawdown
        if drawdown_from_peak > last_drawdown:
            exit_context["trailing_drawdown_max"] = drawdown_from_peak

        if drawdown_from_peak >= effective_threshold:
            return {
                "should_close": True,
                "reason": f"Trailing profit: peak +{peak_pnl:.1f}%, now +{pnl_pct:.1f}% (drawdown {drawdown_from_peak:.0%})",
                "urgency": "medium",
            }

        return {"should_close": False}

    def _check_macd_weakening(
        self,
        analysis: Dict,
        pos_type: str,
        pnl_pct: float,
        exit_context: Dict,
        regime_params: Dict,
    ) -> Dict:
        """Выход на ослаблении импульса MACD."""
        weak_cfg = self.exit_rules.get("macd_weakening", {})
        if not weak_cfg.get("enabled", True):
            return {"should_close": False}
        if not exit_context:
            return {"should_close": False}

        candles_in_trade = exit_context.get("candles_in_trade", 0)
        min_candles = weak_cfg.get("min_candles_in_trade", 2)
        if candles_in_trade < min_candles:
            return {"should_close": False}

        # ADX override: не закрывать по ослаблению при сильном тренде + прибыли
        adx = analysis.get("adx", 25)
        adx_override = weak_cfg.get("adx_override", 30)
        if adx > adx_override and pnl_pct > 0:
            return {"should_close": False}

        # Weakening ratio
        macd_hist = analysis.get("macd_hist", 0)
        peak_hist = exit_context.get("macd_peak_hist", 0)
        peak_candle = exit_context.get("macd_peak_candle", 0)
        candles_after_peak = candles_in_trade - peak_candle

        if abs(peak_hist) == 0:
            return {"should_close": False}

        # Рассчитываем ослабление
        if peak_hist > 0:
            weakening_ratio = max(0, macd_hist / peak_hist) if macd_hist > 0 else 0
        else:
            weakening_ratio = max(0, macd_hist / peak_hist) if macd_hist < 0 else 0

        threshold = regime_params.get(
            "weakening_threshold", weak_cfg.get("threshold", 0.30)
        )
        min_candles_after_peak = weak_cfg.get("min_candles_after_peak", 5)
        min_profit = weak_cfg.get("min_profit_pct", 2.0)
        confirmation_candles = weak_cfg.get("confirmation_candles", 2)
        trend_strength_min = weak_cfg.get("trend_strength_min", 0.7)
        weakening_count = exit_context.get("weakening_count", 0)

        # Проверить силу тренда
        trend_strength = abs(peak_hist) / (abs(peak_hist) + abs(macd_hist) + 0.001)
        if trend_strength < trend_strength_min:
            return {"should_close": False}

        # Увеличить счетчик ослабления
        if weakening_ratio <= threshold:
            exit_context["weakening_count"] = weakening_count + 1
        else:
            exit_context["weakening_count"] = 0  # Сброс при восстановлении

        if (
            weakening_ratio <= threshold
            and candles_after_peak >= min_candles_after_peak
            and pnl_pct >= min_profit
            and weakening_count >= confirmation_candles
        ):
            timeframe = self.preset.get("timeframe", "1h")
            tf_min = tf_to_minutes(timeframe)
            return {
                "should_close": True,
                "reason": f"MACD momentum weakened to {weakening_ratio:.0%} (peak exit, {candles_after_peak} candles/{candles_after_peak * tf_min}m after peak)",
                "urgency": "medium",
            }

        # Пересечение сигнальной линии (запасной сигнал)
        macd_line = analysis.get("macd_line", 0)
        macd_signal = analysis.get("macd_signal", 0)
        macd_hist_prev = analysis.get("macd_hist_prev", 0)
        if (
            pos_type == "BUY"
            and macd_hist < 0
            and macd_hist_prev >= 0
            and pnl_pct >= min_profit
        ):
            return {
                "should_close": True,
                "reason": "MACD bearish signal crossover",
                "urgency": "medium",
            }
        if (
            pos_type == "SELL"
            and macd_hist > 0
            and macd_hist_prev <= 0
            and pnl_pct >= min_profit
        ):
            return {
                "should_close": True,
                "reason": "MACD bullish signal crossover",
                "urgency": "medium",
            }

        return {"should_close": False}

    def _check_impulse_exit(
        self, analysis: Dict, pnl_pct: float, regime_params: Dict
    ) -> Dict:
        """Фиксация прибыли на импульсных свечах."""
        impulse_cfg = self.exit_rules.get("impulse_candle", {})
        if not impulse_cfg.get("enabled", True):
            return {"should_close": False}

        atr = analysis.get("atr", 0)
        if atr <= 0:
            return {"should_close": False}

        # Определяем размер тела последней свечи
        close_prices = analysis.get("close_prices", [])
        open_prices = analysis.get("open_prices", [])
        if not close_prices or not open_prices:
            return {"should_close": False}

        candle_close = close_prices[-1] if close_prices else 0
        candle_open = open_prices[-1] if open_prices else 0
        candle_body = abs(candle_close - candle_open)

        body_mult = impulse_cfg.get("body_atr_multiplier", 2.0)
        is_impulse = candle_body > atr * body_mult

        if not is_impulse:
            return {"should_close": False}

        min_profit = regime_params.get(
            "impulse_min_profit", impulse_cfg.get("min_profit_pct", 1.5)
        )
        if pnl_pct >= min_profit:
            return {
                "should_close": True,
                "reason": f"Impulse candle +{pnl_pct:.1f}% (body={candle_body / atr:.1f}x ATR)",
                "urgency": "high",
            }

        # Объёмное подтверждение
        volume_ratio = analysis.get("volume_ratio", 1.0)
        vol_confirm = impulse_cfg.get("volume_confirm_ratio", 2.5)
        vol_min_profit = impulse_cfg.get("volume_min_profit_pct", 1.0)
        if volume_ratio > vol_confirm and pnl_pct >= vol_min_profit:
            return {
                "should_close": True,
                "reason": f"Volume impulse +{pnl_pct:.1f}% (vol={volume_ratio:.1f}x)",
                "urgency": "high",
            }

        return {"should_close": False}

    def _check_atr_trailing_stop(
        self,
        current_price: float,
        entry_price: float,
        pos_type: str,
        atr: float,
        exit_context: Dict,
    ) -> Dict:
        """ATR-адаптивный trailing stop от пиковой цены."""
        ts_cfg = self.exit_rules.get("trailing_stop", {})
        if not ts_cfg.get("enabled", True):
            return {"should_close": False}
        if atr <= 0 or not exit_context:
            return {"should_close": False}

        activation_atr = ts_cfg.get("activation_atr", 1.0)
        atr_mult = ts_cfg.get("atr_multiplier", 1.5)
        stop_distance = atr * atr_mult
        activation_threshold = atr * activation_atr
        peak_price = exit_context.get("peak_price", entry_price)

        if pos_type == "BUY":
            if current_price > entry_price + activation_threshold:
                trailing_stop = peak_price - stop_distance
                if current_price <= trailing_stop:
                    return {
                        "should_close": True,
                        "reason": f"ATR trailing stop hit ({trailing_stop:.2f}, peak={peak_price:.2f})",
                        "urgency": "high",
                    }
        else:
            if current_price < entry_price - activation_threshold:
                trailing_stop = peak_price + stop_distance
                if current_price >= trailing_stop:
                    return {
                        "should_close": True,
                        "reason": f"ATR trailing stop hit ({trailing_stop:.2f}, peak={peak_price:.2f})",
                        "urgency": "high",
                    }

        return {"should_close": False}

    def _check_max_hold_time(self, exit_context: Dict, pnl_pct: float) -> Dict:
        """Ограничение максимального времени в позиции."""
        emergency_cfg = self.exit_rules.get("emergency", {})
        if not emergency_cfg.get("enabled", False):
            return {"should_close": False}
        max_hold = emergency_cfg.get("max_hold_candles", 80)
        if not exit_context or max_hold <= 0:
            return {"should_close": False}

        candles = exit_context.get("candles_in_trade", 0)
        if candles >= max_hold:
            timeframe = self.preset.get("timeframe", "1h")
            tf_min = tf_to_minutes(timeframe)
            return {
                "should_close": True,
                "reason": f"Max hold time reached: {candles} candles ({candles * tf_min}m), PnL={pnl_pct:.1f}%",
                "urgency": "low",
            }
        return {"should_close": False}

    def _check_standard_exits(
        self, pos_type: str, pnl_pct: float, rsi: float, macd_hist: float
    ) -> Dict:
        """Стандартные правила выхода (существующая логика, без учёта leverage в pnl)."""
        std_cfg = self.exit_rules.get("standard_exits", {})
        if not std_cfg.get("enabled", False):
            return {"should_close": False}

        # Пересчитываем pnl без leverage для совместимости со старыми порогами
        leverage = self.preset.get("leverage", 6)
        pnl_raw = pnl_pct / leverage if leverage > 0 else pnl_pct

        if pos_type == "BUY" and macd_hist < 0:
            if pnl_raw >= 0.5:
                return {
                    "should_close": True,
                    "reason": f"MACD\u2193 + profit {pnl_raw:.1f}%",
                    "urgency": "medium",
                }
            elif pnl_raw < -1.0:
                return {
                    "should_close": True,
                    "reason": f"MACD\u2193 + loss {pnl_raw:.1f}%",
                    "urgency": "high",
                }

        if pos_type == "SELL" and macd_hist > 0:
            if pnl_raw >= 0.5:
                return {
                    "should_close": True,
                    "reason": f"MACD\u2191 + profit {pnl_raw:.1f}%",
                    "urgency": "medium",
                }
            elif pnl_raw < -1.0:
                return {
                    "should_close": True,
                    "reason": f"MACD\u2191 + loss {pnl_raw:.1f}%",
                    "urgency": "high",
                }

        if pos_type == "BUY" and rsi > 80:
            return {
                "should_close": True,
                "reason": f"RSI {rsi:.0f} > 80 (overbought)",
                "urgency": "high",
            }
        if pos_type == "SELL" and rsi < 20:
            return {
                "should_close": True,
                "reason": f"RSI {rsi:.0f} < 20 (oversold)",
                "urgency": "high",
            }

        if pnl_raw >= 3.0:
            if (pos_type == "BUY" and rsi > 70) or (pos_type == "SELL" and rsi < 30):
                return {
                    "should_close": True,
                    "reason": f"Take profit +{pnl_raw:.1f}% RSI extreme",
                    "urgency": "medium",
                }

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}


# =========================================================================
# POSITION GUARD — быстрый цикл защиты позиций (WebSocket)
# =========================================================================


def position_guard_check(
    symbol: str,
    position: Dict,
    exit_context: Dict,
    ws_cache,
    exit_rules: Dict,
    preset: Dict,
) -> Dict:
    """
    Быстрая проверка позиции через WebSocket кэш.

    Работает каждые position_check_interval секунд при открытой позиции.
    Не делает REST-запросов — только чтение из WebSocket кэша.
    Проверяет: pump/dump reversal, trailing stop, profit lock, max loss.

    Args:
        symbol: Торговый символ
        position: Текущая позиция (dict с entry_price, side/type)
        exit_context: Контекст выхода (peak_price, peak_pnl, last_atr)
        ws_cache: WebSocket кэш (dict proxy от Manager)
        exit_rules: Правила выхода из конфига
        preset: Пресет стратегии (leverage, timeframe)

    Returns:
        dict: {should_close, reason, urgency}
    """
    import time as _time

    try:
        if ws_cache is None:
            return {"should_close": False, "reason": "No WS cache"}

        # Нормализация символа для кэша
        if "-" in symbol:
            cache_key = symbol
        elif symbol.endswith("USDT"):
            cache_key = symbol[:-4] + "-USDT"
        else:
            cache_key = symbol

        cached = list(ws_cache.get(cache_key, []))
        if not cached:
            return {"should_close": False, "reason": "Empty cache"}

        last_candle = cached[-1]

        # Проверка свежести данных
        pump_cfg = exit_rules.get("pump_guard", {})
        if not pump_cfg.get("enabled", True):
            return {"should_close": False, "reason": "Pump guard disabled"}

        max_staleness = pump_cfg.get("max_staleness_sec", 60)
        candle_ts = last_candle.get("timestamp", 0)
        if candle_ts > 0:
            candle_age = _time.time() - candle_ts / 1000
            if candle_age > max_staleness:
                debug(f"[GUARD] {symbol}: stale data ({candle_age:.0f}s)")
                return {
                    "should_close": False,
                    "reason": f"Stale data ({candle_age:.0f}s)",
                }

        # Текущая цена из кэша
        current_price = float(
            last_candle.get("closePrice", last_candle.get("close", 0))
        )
        if current_price <= 0:
            return {"should_close": False, "reason": "Invalid price from cache"}

        # Параметры позиции
        if hasattr(position, "entry_price"):
            entry_price = float(position.entry_price)
            pos_type = "BUY" if position.is_long else "SELL"
        else:
            entry_price = float(
                position.get(
                    "entry", position.get("avgPrice", position.get("entry_price", 0))
                )
            )
            pos_type = position.get("type", position.get("side", "BUY")).upper()

        if entry_price <= 0:
            return {"should_close": False, "reason": "Invalid entry price"}

        leverage = preset.get("leverage", 6)

        # Обновляем peak_price
        peak_price = exit_context.get("peak_price", entry_price)
        if pos_type == "BUY":
            peak_price = max(peak_price, current_price)
            drawdown_pct = (peak_price - current_price) / peak_price * 100
            pnl_pct = (current_price - entry_price) / entry_price * 100 * leverage
        else:
            peak_price = min(peak_price, current_price)
            drawdown_pct = (current_price - peak_price) / peak_price * 100
            pnl_pct = (entry_price - current_price) / entry_price * 100 * leverage
        exit_context["peak_price"] = peak_price

        # Обновляем peak_pnl
        peak_pnl = exit_context.get("peak_pnl", 0)
        peak_pnl = max(peak_pnl, pnl_pct)
        exit_context["peak_pnl"] = peak_pnl

        # 1. ЭКСТРЕННЫЙ СТОП: резкий разворот от пика
        reversal_pct = pump_cfg.get("reversal_pct", 1.5)
        if drawdown_pct >= reversal_pct:
            return {
                "should_close": True,
                "reason": f"Pump reversal: -{drawdown_pct:.2f}% from peak",
                "urgency": "critical",
            }

        # 2. TRAILING STOP (быстрый): ATR trailing от peak_price
        ts_cfg = exit_rules.get("trailing_stop", {})
        if ts_cfg.get("enabled", True):
            atr = exit_context.get("last_atr", 0)
            if atr > 0:
                atr_mult = ts_cfg.get("atr_multiplier", 1.5)
                stop_distance = atr * atr_mult
                if pos_type == "BUY" and current_price <= peak_price - stop_distance:
                    return {
                        "should_close": True,
                        "reason": f"Fast ATR trailing stop (peak={peak_price:.2f})",
                        "urgency": "high",
                    }
                if pos_type == "SELL" and current_price >= peak_price + stop_distance:
                    return {
                        "should_close": True,
                        "reason": f"Fast ATR trailing stop (peak={peak_price:.2f})",
                        "urgency": "high",
                    }

        # 3. ФИКСАЦИЯ ПРИБЫЛИ на пампе в нашу сторону
        profit_lock_pct = pump_cfg.get("profit_lock_pct", 5.0)
        if pnl_pct >= profit_lock_pct:
            return {
                "should_close": True,
                "reason": f"Pump profit lock: +{pnl_pct:.1f}%",
                "urgency": "high",
            }

        # 4. МАКСИМАЛЬНЫЙ УБЫТОК (быстрая проверка)
        emergency_cfg = exit_rules.get("emergency", {})
        max_loss_pct = emergency_cfg.get("max_loss_pct", 3.0)
        if pnl_pct <= -max_loss_pct:
            return {
                "should_close": True,
                "reason": f"Max loss guard: {pnl_pct:.1f}%",
                "urgency": "critical",
            }

        return {"should_close": False}

    except Exception as e:
        warning(f"[GUARD] {symbol}: error {e}")
        fallback = (
            pump_cfg.get("fallback_on_error", "skip") if "pump_cfg" in dir() else "skip"
        )
        return {"should_close": False, "reason": f"Guard error: {e}"}
