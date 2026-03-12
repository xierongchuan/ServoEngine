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
from src.utils.logger import info, warning


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
        # === EXTRACT DATA ===
        current_price = analysis.get("current_price", 0)
        rsi = analysis.get("rsi", 50)
        volume_ratio = analysis.get("volume_ratio", 1.0)
        ema9 = analysis.get("ema9", 0)
        ema21 = analysis.get("ema21", 0)

        macd_line = analysis.get("macd_line", 0)
        macd_signal_line = analysis.get("macd_signal", 0)
        macd_hist = analysis.get("macd_hist", 0)
        macd_hist_prev = analysis.get("macd_hist_prev", 0)

        bb_upper = analysis.get("bb_upper", 0)
        bb_lower = analysis.get("bb_lower", 0)
        bb_middle = analysis.get("bb_middle", 0)
        atr = analysis.get("atr", 0)
        atr_ratio = analysis.get("atr_ratio", 1.0)

        # ADX for trend strength (if available)
        adx = analysis.get("adx", 25)  # Default to moderate trend

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

        max_score = macd_cross_weight + rsi_weight + ema_weight + not_sideways_weight + no_exhaustion_weight + volume_weight

        # Use regime-adaptive min score if available
        if regime and regime.get("recommended_min_score"):
            min_score = regime["recommended_min_score"]

        # === HARD FILTERS ===
        if atr_ratio < min_atr_ratio:
            info(f"📊 [MACDX] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return self._hold_result(max_score, [f"Low volatility (ATR {atr_ratio:.2f})"],
                                     {"atr_ratio": atr_ratio, "filter": "volatility"}, regime)

        if volume_ratio < min_volume:
            info(f"📊 [MACDX] HOLD | Low volume ({volume_ratio:.2f}x)")
            return self._hold_result(max_score, [f"Low volume ({volume_ratio:.2f}x)"],
                                     {"volume_ratio": volume_ratio, "filter": "volume"}, regime)

        # === DETECT MACD CROSSOVER (PRIMARY SIGNAL) ===
        macd_cross_long = False
        macd_cross_short = False

        # Bullish crossover: MACD crosses above signal line
        # Current: MACD > Signal, Previous histogram was negative or smaller
        if macd_line > macd_signal_line and macd_hist > 0:
            # Confirm it's a fresh crossover or building momentum
            if macd_hist_prev <= 0 or macd_hist > macd_hist_prev:
                macd_cross_long = True

        # Bearish crossover: MACD crosses below signal line
        if macd_line < macd_signal_line and macd_hist < 0:
            if macd_hist_prev >= 0 or macd_hist < macd_hist_prev:
                macd_cross_short = True

        # No MACD signal - HOLD
        if not macd_cross_long and not macd_cross_short:
            info(f"📊 [MACDX] HOLD | No MACD crossover (hist: {macd_hist:.4f})")
            return self._hold_result(max_score, ["No MACD crossover signal"],
                                     {"macd_hist": macd_hist, "filter": "no_macd_cross"}, regime)

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
            long_reasons.append(f"MACD↑ cross +{macd_cross_weight}")
            long_confirmations += 1
        if macd_cross_short:
            short_score += macd_cross_weight
            short_reasons.append(f"MACD↓ cross +{macd_cross_weight}")
            short_confirmations += 1

        # 2. RSI Zone (+2)
        rsi_long_ok = rsi_long_min <= rsi <= rsi_long_max
        rsi_short_ok = rsi_short_min <= rsi <= rsi_short_max

        if macd_cross_long and rsi_long_ok:
            long_score += rsi_weight
            long_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}")
            long_confirmations += 1
        elif macd_cross_long and not rsi_long_ok:
            long_reasons.append(f"RSI {rsi:.0f} outside zone (need {rsi_long_min}-{rsi_long_max})")

        if macd_cross_short and rsi_short_ok:
            short_score += rsi_weight
            short_reasons.append(f"RSI {rsi:.0f} in zone +{rsi_weight}")
            short_confirmations += 1
        elif macd_cross_short and not rsi_short_ok:
            short_reasons.append(f"RSI {rsi:.0f} outside zone (need {rsi_short_min}-{rsi_short_max})")

        # 3. EMA Alignment (+2)
        ema_long = False
        ema_short = False
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            if ema9 > ema21:
                ema_long = True
                if macd_cross_long:
                    long_score += ema_weight
                    long_reasons.append(f"EMA↑ ({ema_diff_pct:.1f}%) +{ema_weight}")
                    long_confirmations += 1
            elif ema9 < ema21:
                ema_short = True
                if macd_cross_short:
                    short_score += ema_weight
                    short_reasons.append(f"EMA↓ ({ema_diff_pct:.1f}%) +{ema_weight}")
                    short_confirmations += 1

        # Penalty for counter-trend MACD signals
        if macd_cross_long and ema_short:
            long_reasons.append("EMA counter-trend (caution)")
        if macd_cross_short and ema_long:
            short_reasons.append("EMA counter-trend (caution)")

        # 4. Not Sideways (+1) - BB width or ADX check
        is_sideways = False
        bb_width = 0
        if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
            bb_width = (bb_upper - bb_lower) / bb_middle * 100
            # Sideways = narrow BB width AND low ADX
            if bb_width < bb_width_threshold and adx < adx_threshold:
                is_sideways = True

        if not is_sideways:
            if macd_cross_long:
                long_score += not_sideways_weight
                long_reasons.append(f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}")
                long_confirmations += 1
            if macd_cross_short:
                short_score += not_sideways_weight
                short_reasons.append(f"Not sideways (BB:{bb_width:.1f}% ADX:{adx:.0f}) +{not_sideways_weight}")
                short_confirmations += 1
        else:
            if macd_cross_long:
                long_reasons.append(f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})")
            if macd_cross_short:
                short_reasons.append(f"Sideways market (BB:{bb_width:.1f}% ADX:{adx:.0f})")

        # 5. No Exhaustion (+1) - Check for RSI divergence
        close_prices = analysis.get("close_prices", [])
        rsi_values = analysis.get("rsi_values", [])
        bearish_div, bullish_div = self._detect_rsi_divergence(close_prices, rsi_values)

        # For long: no bearish divergence
        if macd_cross_long:
            if not bearish_div:
                long_score += no_exhaustion_weight
                long_reasons.append(f"No bearish divergence +{no_exhaustion_weight}")
                long_confirmations += 1
            else:
                long_score -= 1  # Penalty for divergence
                long_reasons.append("Bearish RSI divergence (exhaustion warning) -1")

        # For short: no bullish divergence
        if macd_cross_short:
            if not bullish_div:
                short_score += no_exhaustion_weight
                short_reasons.append(f"No bullish divergence +{no_exhaustion_weight}")
                short_confirmations += 1
            else:
                short_score -= 1  # Penalty for divergence
                short_reasons.append("Bullish RSI divergence (exhaustion warning) -1")

        # 6. Volume (+1)
        volume_ok = volume_ratio >= 0.8
        if volume_ok:
            if macd_cross_long:
                long_score += volume_weight
                long_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}")
                long_confirmations += 1
            if macd_cross_short:
                short_score += volume_weight
                short_reasons.append(f"Volume {volume_ratio:.1f}x +{volume_weight}")
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
        return {
            "signal": "HOLD",
            "score": 0,
            "max_score": max_score,
            "quality": 0.0,
            "confidence": 0.0,
            "reasons": reasons,
            "confirmations": 0,
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
        """
        if not prices or not rsi_values:
            return False, False

        n = min(len(prices), len(rsi_values))
        if n < 10:
            return False, False

        prices = prices[-n:]
        rsi_values = rsi_values[-n:]

        # Find local maxima (for bearish divergence detection)
        maxima = []
        for i in range(1, n - 1):
            if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
                maxima.append(i)

        # Find local minima (for bullish divergence detection)
        minima = []
        for i in range(1, n - 1):
            if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
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
