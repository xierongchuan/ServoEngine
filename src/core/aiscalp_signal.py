"""
Deterministic Signal Generator for AISCALP mode.
Generates trading signals using HTF trend alignment, session awareness, and tiered scoring.
AI confirms/rejects these signals — it cannot generate its own direction.

TIERED SCORING SYSTEM:
  Tier 1 (Direction, at least 1 required):
    - HTF trend alignment: +3
    - EMA alignment (5m): +2
  Tier 2 (Confirmation, at least 1 required):
    - RSI zone: +2
    - S/R proximity: +2
  Tier 3 (Support, optional):
    - MACD: +1
    - Momentum: +1
    - Bollinger Bands: +1
    - Volume: +1
  Interaction bonuses/penalties: ±1..3
  Session adjustment: +1 (overlap) / -1 (off-session)

Max base: 13, Min for signal: regime-adaptive (default 5)
"""

from src.config import BOT_CONFIG
from src.utils.logger import info, warning


class AiScalpSignalGenerator:
    """AISCALP signal generator with HTF trend and session awareness."""

    def __init__(self):
        self.settings = BOT_CONFIG.get("AISCALP_SETTINGS", {})
        self.scoring = self.settings.get("signal_scoring", {})
        self.weights = self.scoring.get("weights", {})
        self.interactions = self.settings.get("interaction_rules", {})

    def pre_filter(self, analysis: dict, htf_data: dict, session_data: dict) -> tuple:
        """
        Pre-filter to skip cycles where no good signal is possible.

        Returns:
            (should_proceed: bool, reason: str)
        """
        pf_cfg = self.settings.get("pre_filter", {})

        volume_ratio = analysis.get("volume_ratio", 1.0)
        rsi = analysis.get("rsi", 50)
        htf_trend = htf_data.get("htf_trend", "NEUTRAL") if htf_data else "NEUTRAL"
        daily_bias = htf_data.get("daily_bias", "NEUTRAL") if htf_data else "NEUTRAL"
        session_quality = session_data.get("session_quality", "MEDIUM") if session_data else "MEDIUM"

        # 1. Dead market (volume too low)
        dead_vol = pf_cfg.get("skip_dead_market_volume", 0.2)
        if volume_ratio < dead_vol:
            return False, f"Dead market (volume {volume_ratio:.2f} < {dead_vol})"

        # 2. RSI neutral + no HTF trend (full neutral — nothing to trade)
        rsi_neutral = pf_cfg.get("skip_rsi_neutral_zone", [46, 54])
        if pf_cfg.get("skip_no_htf_trend", True):
            if htf_trend == "NEUTRAL" and rsi_neutral[0] <= rsi <= rsi_neutral[1]:
                return False, f"RSI neutral ({rsi:.0f}) + no HTF trend"

        # 3. No HTF trend AND no daily bias
        if pf_cfg.get("skip_no_htf_trend", True):
            if htf_trend == "NEUTRAL" and daily_bias == "NEUTRAL":
                return False, "No HTF trend and no daily bias"

        return True, "Passed"

    def generate_signal(self, analysis: dict, htf_data: dict, session_data: dict, regime: dict = None) -> dict:
        """
        Generate deterministic AISCALP signal with HTF and session context.

        Args:
            analysis: dict with indicators from analyzer (5m timeframe)
            htf_data: dict with HTF analysis (1h trend, EMA, RSI)
            session_data: dict from get_session_info()
            regime: dict from MarketRegimeDetector (optional)

        Returns:
            dict with signal, score, quality, confidence, reasons, details
        """
        # === EXTRACT DATA ===
        rsi = analysis.get("rsi", 50)
        volume_ratio = analysis.get("volume_ratio", 1.0)
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

        # HTF data
        htf_trend = htf_data.get("htf_trend", "NEUTRAL") if htf_data else "NEUTRAL"

        # MACD crossover data from analyzer
        macd_crossover = analysis.get("macd_crossover", "NONE")
        macd_crossover_confirmed = analysis.get("macd_crossover_confirmed", False)

        # Session data
        session_quality = session_data.get("session_quality", "MEDIUM") if session_data else "MEDIUM"
        is_overlap = session_data.get("is_overlap", False) if session_data else False
        quality_score_adj = session_data.get("quality_score_adj", 0) if session_data else 0

        # === WEIGHTS FROM CONFIG ===
        w_htf = self.weights.get("htf_trend", 3)
        w_ema = self.weights.get("ema_cross", 2)
        w_rsi = self.weights.get("rsi_zone", 2)
        w_sr = self.weights.get("sr_proximity", 2)
        w_macd = self.weights.get("macd", 1)
        w_mom = self.weights.get("momentum", 1)
        w_bb = self.weights.get("bb", 1)
        w_vol = self.weights.get("volume", 1)

        max_score = w_htf + w_ema + w_rsi + w_sr + w_macd + w_mom + w_bb + w_vol

        # === THRESHOLDS ===
        min_volume = self.scoring.get("min_volume_ratio", 0.3)
        min_atr = self.scoring.get("min_atr_ratio", 0.3)
        rsi_long_zone = self.scoring.get("rsi_long_zone", [25, 55])
        rsi_short_zone = self.scoring.get("rsi_short_zone", [45, 75])
        sr_proximity_pct = self.scoring.get("sr_proximity_pct", 2.5)
        tier1_required = self.scoring.get("tier1_required", True)
        conflict_friction = self.scoring.get("conflict_friction_threshold", 4)

        # Min score: regime-adaptive or default
        if regime and regime.get("recommended_min_score"):
            min_score = regime["recommended_min_score"]
        else:
            min_score = self.scoring.get("min_score_for_signal", 5)

        # === HARD FILTERS ===
        if atr_ratio < min_atr:
            info(f"📊 [AISCALP] HOLD | Low volatility (ATR: {atr_ratio:.2f})")
            return self._hold_result(max_score, [f"Low volatility (ATR {atr_ratio:.2f})"],
                                     {"atr_ratio": atr_ratio, "filter": "volatility"}, regime)

        if volume_ratio < min_volume:
            info(f"📊 [AISCALP] HOLD | Low volume ({volume_ratio:.2f}x)")
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

        # --- TIER 1: Direction (at least 1 required) ---

        # 1a. HTF Trend Alignment (+3) — heaviest weight
        htf_long = False
        htf_short = False
        if htf_trend == "BULLISH":
            long_score += w_htf
            long_reasons.append(f"HTF↑ +{w_htf}")
            long_tier1 = True
            htf_long = True
        elif htf_trend == "BEARISH":
            short_score += w_htf
            short_reasons.append(f"HTF↓ +{w_htf}")
            short_tier1 = True
            htf_short = True

        # 1b. EMA Alignment on 5m (+2)
        ema_long = False
        ema_short = False
        if ema9 > 0 and ema21 > 0:
            ema_diff_pct = abs(ema9 - ema21) / ema21 * 100 if ema21 > 0 else 0
            if ema9 > ema21:
                long_score += w_ema
                long_reasons.append(f"EMA↑ ({ema_diff_pct:.1f}%) +{w_ema}")
                long_tier1 = True
                ema_long = True
            elif ema9 < ema21:
                short_score += w_ema
                short_reasons.append(f"EMA↓ ({ema_diff_pct:.1f}%) +{w_ema}")
                short_tier1 = True
                ema_short = True

        # --- TIER 2: Confirmation (at least 1 required) ---

        # 2a. RSI Zone (+2) — wider than HYBRID for pullback entries
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

        # 2b. S/R Proximity (+2)
        sr_long = False
        sr_short = False
        if current_price > 0 and support > 0 and resistance > 0:
            support_dist_pct = abs((current_price - support) / current_price * 100)
            resistance_dist_pct = abs((resistance - current_price) / current_price * 100)
            sr_spread_pct = (resistance - support) / current_price * 100

            if sr_spread_pct >= 1.0:
                if support_dist_pct <= sr_proximity_pct:
                    long_score += w_sr
                    long_reasons.append(f"S/R↓ ({support_dist_pct:.1f}%) +{w_sr}")
                    long_tier2 = True
                    sr_long = True
                if resistance_dist_pct <= sr_proximity_pct:
                    short_score += w_sr
                    short_reasons.append(f"S/R↑ ({resistance_dist_pct:.1f}%) +{w_sr}")
                    short_tier2 = True
                    sr_short = True

        # --- TIER 3: Support (optional) ---

        # 3a. MACD (+1) - using advanced crossover detection
        macd_long = False
        macd_short = False
        # Priority: use advanced crossover detection if available
        if macd_crossover != "NONE":
            # Use analyzer's crossover detection for more accurate signals
            if macd_crossover == "BULLISH":
                long_score += w_macd
                long_reasons.append(f"MACD↑ {macd_crossover_confirmed and '✓' or '○'} +{w_macd}")
                macd_long = True
            elif macd_crossover == "BEARISH":
                short_score += w_macd
                short_reasons.append(f"MACD↓ {macd_crossover_confirmed and '✓' or '○'} +{w_macd}")
                macd_short = True
        else:
            # Fallback to simple histogram check
            if macd_line > macd_signal_val and macd_hist > 0:
                long_score += w_macd
                long_reasons.append(f"MACD↑ +{w_macd}")
                macd_long = True
            elif macd_line < macd_signal_val and macd_hist < 0:
                short_score += w_macd
                short_reasons.append(f"MACD↓ +{w_macd}")
                macd_short = True

        # 3b. Momentum (+1)
        momentum_long = False
        momentum_short = False
        if last_5_direction in ("UP", "STRONG UP"):
            long_score += w_mom
            long_reasons.append(f"Mom↑ +{w_mom}")
            momentum_long = True
        elif last_5_direction in ("DOWN", "STRONG DOWN"):
            short_score += w_mom
            short_reasons.append(f"Mom↓ +{w_mom}")
            momentum_short = True

        # 3c. Bollinger Bands (+1)
        bb_long = False
        bb_short = False
        if bb_lower > 0 and current_price <= bb_lower * 1.005:
            long_score += w_bb
            long_reasons.append(f"BB↓ +{w_bb}")
            bb_long = True
        elif bb_upper > 0 and current_price >= bb_upper * 0.995:
            short_score += w_bb
            short_reasons.append(f"BB↑ +{w_bb}")
            bb_short = True

        # 3d. Volume (+1)
        volume_confirmed = volume_ratio >= 0.8
        if volume_confirmed:
            if ema_long or htf_long or momentum_long:
                long_score += w_vol
                long_reasons.append(f"Vol {volume_ratio:.1f}x +{w_vol}")
            if ema_short or htf_short or momentum_short:
                short_score += w_vol
                short_reasons.append(f"Vol {volume_ratio:.1f}x +{w_vol}")

        # === INTERACTION BONUSES/PENALTIES ===
        long_interactions = 0
        short_interactions = 0
        long_int_reasons = []
        short_int_reasons = []

        # HTF + LTF confluence bonus (+2)
        htf_ltf_bonus = self.interactions.get("htf_ltf_confluence_bonus", 2)
        if htf_long and ema_long:
            long_interactions += htf_ltf_bonus
            long_int_reasons.append(f"HTF+LTF confluence +{htf_ltf_bonus}")
        if htf_short and ema_short:
            short_interactions += htf_ltf_bonus
            short_int_reasons.append(f"HTF+LTF confluence +{htf_ltf_bonus}")

        # EMA + MACD confluence bonus (+1)
        ema_macd_bonus = self.interactions.get("ema_macd_confluence_bonus", 1)
        if ema_long and macd_long:
            long_interactions += ema_macd_bonus
            long_int_reasons.append(f"EMA+MACD confluence +{ema_macd_bonus}")
        if ema_short and macd_short:
            short_interactions += ema_macd_bonus
            short_int_reasons.append(f"EMA+MACD confluence +{ema_macd_bonus}")

        # Reversal confluence: RSI + S/R + BB (+2)
        reversal_bonus = self.interactions.get("reversal_confluence_bonus", 2)
        if rsi_long and sr_long and bb_long:
            long_interactions += reversal_bonus
            long_int_reasons.append(f"Reversal confluence +{reversal_bonus}")
        if rsi_short and sr_short and bb_short:
            short_interactions += reversal_bonus
            short_int_reasons.append(f"Reversal confluence +{reversal_bonus}")

        # Momentum burst: Volume spike + directional candles + EMA (+1)
        burst_bonus = self.interactions.get("momentum_burst_bonus", 1)
        if volume_ratio >= 1.5 and momentum_long and ema_long:
            long_interactions += burst_bonus
            long_int_reasons.append(f"Momentum burst +{burst_bonus}")
        if volume_ratio >= 1.5 and momentum_short and ema_short:
            short_interactions += burst_bonus
            short_int_reasons.append(f"Momentum burst +{burst_bonus}")

        # Counter-HTF trend penalty (-3) — heavy penalty for trading against daily trend
        counter_penalty = self.interactions.get("counter_htf_trend_penalty", -3)
        if htf_trend == "BEARISH" and long_tier1 and not htf_long:
            long_interactions += counter_penalty
            long_int_reasons.append(f"Counter-HTF {counter_penalty}")
        if htf_trend == "BULLISH" and short_tier1 and not htf_short:
            short_interactions += counter_penalty
            short_int_reasons.append(f"Counter-HTF {counter_penalty}")

        # RSI divergence penalty (-2)
        div_penalty = self.interactions.get("rsi_divergence_penalty", -2)
        close_prices = analysis.get("close_prices", [])
        rsi_values = analysis.get("rsi_values", [])
        if len(close_prices) >= 20 and len(rsi_values) >= 20:
            bearish_div, bullish_div = self._detect_rsi_divergence(close_prices[-20:], rsi_values[-20:])
            if bearish_div:
                long_interactions += div_penalty
                long_int_reasons.append(f"RSI bearish div {div_penalty}")
            if bullish_div:
                short_interactions += div_penalty
                short_int_reasons.append(f"RSI bullish div {div_penalty}")

        # Apply interactions
        long_score += long_interactions
        short_score += short_interactions
        long_reasons.extend(long_int_reasons)
        short_reasons.extend(short_int_reasons)

        # === SESSION ADJUSTMENT (post-scoring) ===
        if quality_score_adj != 0:
            long_score += quality_score_adj
            short_score += quality_score_adj
            adj_label = f"Session {quality_score_adj:+d}"
            long_reasons.append(adj_label)
            short_reasons.append(adj_label)

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

        # === QUALITY SCORE ===
        if signal != "HOLD" and max_score > min_score:
            raw_quality = (score - min_score) / (max_score - min_score)
            quality = max(0.1, min(1.0, 0.1 + raw_quality * 0.9))
        else:
            quality = 0.0

        # === CONFIDENCE MAPPING ===
        if signal != "HOLD":
            if quality >= 0.7:
                confidence = 0.85
            elif quality >= 0.4:
                confidence = 0.70
            else:
                confidence = 0.55
        else:
            confidence = 0.0

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

        # Log
        regime_label = regime.get("regime", "?") if regime else "?"
        htf_label = htf_trend[:1] if htf_trend != "NEUTRAL" else "N"
        sess_label = session_quality[:1]
        if signal != "HOLD":
            info(f"📊 [AISCALP] {signal} | {score}/{max_score} Q:{quality:.2f} [{regime_label}] HTF:{htf_label} Sess:{sess_label} | {' '.join(reasons[:3])}")
        else:
            info(f"📊 [AISCALP] HOLD | L:{long_score} S:{short_score} (need {min_score}) [{regime_label}] HTF:{htf_label}")

        return result

    def should_close_position(self, analysis: dict, position: dict, htf_data: dict = None) -> dict:
        """Deterministic close check, including HTF trend reversal rule."""
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

        macd_exit_pnl = self.scoring.get("macd_exit_pnl_threshold", -1.5)

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

        # 3. MACD reversal against position with loss
        if pos_type == "BUY" and macd_hist < 0 and pnl_pct < macd_exit_pnl:
            return {"should_close": True, "reason": f"MACD↓ + loss {pnl_pct:.1f}%", "urgency": "high"}
        if pos_type == "SELL" and macd_hist > 0 and pnl_pct < macd_exit_pnl:
            return {"should_close": True, "reason": f"MACD↑ + loss {pnl_pct:.1f}%", "urgency": "high"}

        # 4. HTF trend reversal against position (AISCALP-specific)
        if htf_data:
            htf_trend = htf_data.get("htf_trend", "NEUTRAL")
            if pos_type == "BUY" and htf_trend == "BEARISH" and pnl_pct < 0:
                return {"should_close": True, "reason": f"HTF↓ reversal + loss {pnl_pct:.1f}%", "urgency": "high"}
            if pos_type == "SELL" and htf_trend == "BULLISH" and pnl_pct < 0:
                return {"should_close": True, "reason": f"HTF↑ reversal + loss {pnl_pct:.1f}%", "urgency": "high"}

        # 5. Trend reversal + loss (LTF)
        global_trend = analysis.get("global_trend", "N/A")
        local_trend = analysis.get("local_trend", "N/A")

        if pos_type == "BUY" and global_trend == "DOWN" and local_trend == "BEARISH" and pnl_pct < 0:
            return {"should_close": True, "reason": f"Trend↓ + loss {pnl_pct:.1f}%", "urgency": "high"}
        if pos_type == "SELL" and global_trend == "UP" and local_trend == "BULLISH" and pnl_pct < 0:
            return {"should_close": True, "reason": f"Trend↑ + loss {pnl_pct:.1f}%", "urgency": "high"}

        # 6. Breakeven trailing: lock profit if PnL >= 3%
        if pnl_pct >= 3.0:
            if pos_type == "BUY" and (rsi > 65 or macd_hist < 0):
                return {"should_close": True, "reason": f"Trail +{pnl_pct:.1f}% momentum fading", "urgency": "medium"}
            if pos_type == "SELL" and (rsi < 35 or macd_hist > 0):
                return {"should_close": True, "reason": f"Trail +{pnl_pct:.1f}% momentum fading", "urgency": "medium"}

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}

    def _hold_result(self, max_score, reasons, details, regime=None):
        """Helper for HOLD result."""
        return {
            "signal": "HOLD",
            "score": 0,
            "max_score": max_score,
            "quality": 0.0,
            "confidence": 0.0,
            "reasons": reasons,
            "filters_passed": False,
            "details": details,
            "regime": regime.get("regime", "UNKNOWN") if regime else "NO_REGIME",
        }

    @staticmethod
    def _detect_rsi_divergence(prices: list, rsi_values: list) -> tuple:
        """
        Detect RSI divergence over the given window.
        Returns: (bearish_divergence, bullish_divergence) booleans
        """
        n = len(prices)
        if n < 5 or len(rsi_values) < n:
            return False, False

        maxima = []
        for i in range(1, n - 1):
            if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
                maxima.append(i)

        minima = []
        for i in range(1, n - 1):
            if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
                minima.append(i)

        bearish_div = False
        bullish_div = False

        if len(maxima) >= 2:
            prev_max = maxima[-2]
            last_max = maxima[-1]
            if prices[last_max] > prices[prev_max] and rsi_values[last_max] < rsi_values[prev_max]:
                bearish_div = True

        if len(minima) >= 2:
            prev_min = minima[-2]
            last_min = minima[-1]
            if prices[last_min] < prices[prev_min] and rsi_values[last_min] > rsi_values[prev_min]:
                bullish_div = True

        return bearish_div, bullish_div


# Singleton
_generator = None


def get_aiscalp_signal_generator() -> AiScalpSignalGenerator:
    global _generator
    if _generator is None:
        _generator = AiScalpSignalGenerator()
    return _generator


def aiscalp_pre_filter(analysis: dict, htf_data: dict, session_data: dict) -> tuple:
    return get_aiscalp_signal_generator().pre_filter(analysis, htf_data, session_data)


def generate_aiscalp_signal(analysis: dict, htf_data: dict, session_data: dict, regime: dict = None) -> dict:
    return get_aiscalp_signal_generator().generate_signal(analysis, htf_data, session_data, regime)


def aiscalp_should_close(analysis: dict, position: dict, htf_data: dict = None) -> dict:
    return get_aiscalp_signal_generator().should_close_position(analysis, position, htf_data)
