"""
Scalp Signal Generator — deterministic scoring for SCALP mode.

Separate from HYBRID's SignalGenerator:
- Different indicators (EMA 5/13/21, RSI 7, MACD 6/13/5)
- Order book imbalance as Tier 2 indicator
- VWAP as mean-reversion anchor
- Three entry patterns: Momentum Breakout, Mean Reversion, Pullback
- Regime-adaptive weights from SCALP_SETTINGS
- Exit signal detection for position management

Tiered scoring (max 10 base + 3 interaction = 13):
  Tier 1 (Direction):  EMA alignment(+2), 3-candle momentum(+1)
  Tier 2 (Confirm):    RSI zone(+2), VWAP position(+1)
  Tier 3 (Support):    Volume(+1), OB imbalance(+1), MACD(+1), BB(+1)
"""

from typing import Dict, Optional
from src.config import SCALP_SETTINGS
from src.utils.logger import info


class ScalpSignalGenerator:
    """Deterministic signal generator tuned for 1m scalping."""

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or SCALP_SETTINGS
        self.rules = cfg.get("signal_rules", {})
        self.interactions = cfg.get("interaction_rules", {})
        self.regime_overrides = cfg.get("regime_overrides", {})

    def generate(self, indicators: Dict, regime: Optional[Dict] = None,
                 ob_imbalance: float = 0.0) -> Dict:
        """
        Generate a scalp signal from lightweight analyzer indicators.

        Args:
            indicators: Dict from LightweightAnalyzer.get_snapshot()
            regime: Optional regime dict (regime, recommended_min_score, etc.)
            ob_imbalance: Order book imbalance score [-1.0, 1.0]

        Returns:
            Dict with signal, score, quality, confidence, pattern, reasons, details
        """
        # Extract indicators
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

        # Load weights (apply regime overrides if available)
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

        # RSI zones
        rsi_long_zone = self.rules.get("rsi_long_zone", [25, 40])
        rsi_short_zone = self.rules.get("rsi_short_zone", [60, 75])
        ob_threshold = self.rules.get("ob_imbalance_threshold", 0.3)
        chop_threshold = self.rules.get("choppiness_threshold", 61.8)

        # === CHOPPINESS FILTER ===
        choppiness = indicators.get("choppiness", 50.0)
        is_choppy = choppiness > chop_threshold
        if is_choppy:
            # In choppy market, only allow RANGING regime or hold
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

        # === SCORING ===
        long_score = 0
        short_score = 0
        long_reasons = []
        short_reasons = []
        long_tier1 = False
        short_tier1 = False

        # --- TIER 1: Direction ---

        # 1a. EMA alignment (fast > med > macro = stacked bullish)
        ema_long = ema_fast > ema_med > 0
        ema_short = ema_fast < ema_med and ema_med > 0
        if ema_long:
            long_score += ema_w
            long_reasons.append(f"EMA↑ +{ema_w}")
            long_tier1 = True
        if ema_short:
            short_score += ema_w
            short_reasons.append(f"EMA↓ +{ema_w}")
            short_tier1 = True

        # 1b. 3-candle momentum
        if momentum_dir == "UP":
            long_score += momentum_w
            long_reasons.append(f"Mom↑ +{momentum_w}")
            long_tier1 = True
        elif momentum_dir == "DOWN":
            short_score += momentum_w
            short_reasons.append(f"Mom↓ +{momentum_w}")
            short_tier1 = True

        # --- TIER 2: Confirmation ---

        # 2a. RSI zone
        rsi_long = rsi_long_zone[0] <= rsi <= rsi_long_zone[1]
        rsi_short = rsi_short_zone[0] <= rsi <= rsi_short_zone[1]
        if rsi_long:
            long_score += rsi_w
            long_reasons.append(f"RSI {rsi:.0f} +{rsi_w}")
        if rsi_short:
            short_score += rsi_w
            short_reasons.append(f"RSI {rsi:.0f} +{rsi_w}")

        # 2b. VWAP position
        if vwap > 0 and current_price > 0:
            vwap_dist_pct = (current_price - vwap) / vwap * 100
            if current_price > vwap and abs(vwap_dist_pct) < 0.5:
                # Price above VWAP and close to it = bullish bounce zone
                long_score += vwap_w
                long_reasons.append(f"VWAP↑ +{vwap_w}")
            elif current_price < vwap and abs(vwap_dist_pct) < 0.5:
                short_score += vwap_w
                short_reasons.append(f"VWAP↓ +{vwap_w}")

        # --- TIER 3: Support ---

        # 3a. Volume surge
        if volume_ratio >= 1.3:
            # Add to winning side only
            if long_score > short_score:
                long_score += volume_w
                long_reasons.append(f"Vol {volume_ratio:.1f}x +{volume_w}")
            elif short_score > long_score:
                short_score += volume_w
                short_reasons.append(f"Vol {volume_ratio:.1f}x +{volume_w}")

        # 3b. Order book imbalance
        if abs(ob_imbalance) >= ob_threshold:
            if ob_imbalance > 0:  # More bids than asks
                long_score += ob_w
                long_reasons.append(f"OB↑ {ob_imbalance:.2f} +{ob_w}")
            else:
                short_score += ob_w
                short_reasons.append(f"OB↓ {ob_imbalance:.2f} +{ob_w}")

        # 3c. MACD histogram + crossover boost
        macd_crossover = indicators.get("macd_crossover", "NONE")
        if macd_hist > 0:
            long_score += macd_w
            long_reasons.append(f"MACD↑ +{macd_w}")
            if macd_crossover == "BULLISH":
                long_reasons.append("MACDx↑")
        elif macd_hist < 0:
            short_score += macd_w
            short_reasons.append(f"MACD↓ +{macd_w}")
            if macd_crossover == "BEARISH":
                short_reasons.append("MACDx↓")

        # 3d. Bollinger Bands
        if bb_lower > 0 and current_price <= bb_lower * 1.005:
            long_score += bb_w
            long_reasons.append(f"BB↓ +{bb_w}")
        elif bb_upper > 0 and current_price >= bb_upper * 0.995:
            short_score += bb_w
            short_reasons.append(f"BB↑ +{bb_w}")

        # 3e. CVD (Cumulative Volume Delta)
        cvd_trend = indicators.get("cvd_trend", "FLAT")
        if cvd_trend == "RISING":
            long_score += cvd_w
            long_reasons.append(f"CVD↑ +{cvd_w}")
        elif cvd_trend == "FALLING":
            short_score += cvd_w
            short_reasons.append(f"CVD↓ +{cvd_w}")

        # === INTERACTION BONUSES ===
        long_int = 0
        short_int = 0

        # Momentum Burst: EMA aligned + Volume > 1.5x + 3 consecutive candles
        burst_bonus = self.interactions.get("momentum_burst_bonus", 2)
        if ema_long and volume_ratio >= 1.5 and momentum_dir == "UP":
            long_int += burst_bonus
            long_reasons.append(f"MomBurst +{burst_bonus}")
        if ema_short and volume_ratio >= 1.5 and momentum_dir == "DOWN":
            short_int += burst_bonus
            short_reasons.append(f"MomBurst +{burst_bonus}")

        # VWAP Bounce: Price at VWAP + RSI in zone + EMA confirms
        vwap_bonus = self.interactions.get("vwap_bounce_bonus", 1)
        if vwap > 0 and current_price > 0:
            near_vwap = abs(current_price - vwap) / vwap < 0.002  # Within 0.2%
            if near_vwap and rsi_long and ema_long:
                long_int += vwap_bonus
                long_reasons.append(f"VWAPBounce +{vwap_bonus}")
            if near_vwap and rsi_short and ema_short:
                short_int += vwap_bonus
                short_reasons.append(f"VWAPBounce +{vwap_bonus}")

        # OB Confluence: OB imbalance + EMA + Volume
        ob_conf_bonus = self.interactions.get("ob_confluence_bonus", 1)
        if ob_imbalance > ob_threshold and ema_long and volume_ratio >= 1.0:
            long_int += ob_conf_bonus
            long_reasons.append(f"OBConfl +{ob_conf_bonus}")
        if ob_imbalance < -ob_threshold and ema_short and volume_ratio >= 1.0:
            short_int += ob_conf_bonus
            short_reasons.append(f"OBConfl +{ob_conf_bonus}")

        # === PENALTIES ===

        # Counter-momentum: EMA says BUY but RSI > 70 (overbought)
        counter_pen = self.interactions.get("counter_momentum_penalty", -2)
        if ema_long and rsi > 70:
            long_int += counter_pen
            long_reasons.append(f"CounterMom {counter_pen}")
        if ema_short and rsi < 30:
            short_int += counter_pen
            short_reasons.append(f"CounterMom {counter_pen}")

        # CVD divergence penalty: price direction vs CVD direction
        cvd_div_pen = self.interactions.get("cvd_divergence_penalty", -1)
        if momentum_dir == "UP" and cvd_trend == "FALLING":
            long_int += cvd_div_pen
            long_reasons.append(f"CVDdiv {cvd_div_pen}")
        elif momentum_dir == "DOWN" and cvd_trend == "RISING":
            short_int += cvd_div_pen
            short_reasons.append(f"CVDdiv {cvd_div_pen}")

        # ATR spike penalty
        spike_pen = self.interactions.get("spike_penalty", -1)
        if atr_ratio > 2.0:
            if long_score > short_score:
                long_int += spike_pen
                long_reasons.append(f"ATRSpike {spike_pen}")
            elif short_score > long_score:
                short_int += spike_pen
                short_reasons.append(f"ATRSpike {spike_pen}")

        # Apply interactions
        long_score += long_int
        short_score += short_int

        # === CONFLICT FRICTION ===
        conflicting = False
        if long_score > short_score and short_score >= conflict_friction:
            long_score -= 1
            long_reasons.append("Friction -1")
            conflicting = True
        elif short_score > long_score and long_score >= conflict_friction:
            short_score -= 1
            short_reasons.append("Friction -1")
            conflicting = True

        # === DETERMINE SIGNAL ===
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

        # Quality & Confidence
        if signal != "HOLD" and max_score > min_score:
            quality = max(0.0, min(1.0, (score - min_score) / (max_score - min_score)))
        else:
            quality = 0.0

        if signal != "HOLD":
            confidence = 0.85 if quality >= 0.7 else (0.70 if quality >= 0.4 else 0.55)
        else:
            confidence = 0.0

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

    def check_exit(self, indicators: Dict, position: Dict) -> Dict:
        """
        Check if an open position should be closed based on deterministic rules.

        Args:
            indicators: Current indicator snapshot
            position: Position dict with type, entry, pnl, etc.

        Returns:
            Dict with should_close, reason, urgency
        """
        if not position:
            return {"should_close": False, "reason": "No position", "urgency": "low"}

        pos_type = position.get("type", "").upper()
        entry_price = float(position.get("entry", position.get("avgPrice", 0)))
        current_price = indicators.get("current_price", 0)
        rsi = indicators.get("rsi", 50)
        ema_fast = indicators.get("ema_fast", 0)
        ema_med = indicators.get("ema_med", 0)
        macd_hist = indicators.get("macd_hist", 0)
        volume_ratio = indicators.get("volume_ratio", 1.0)

        if entry_price <= 0 or current_price <= 0:
            return {"should_close": False, "reason": "Invalid prices", "urgency": "low"}

        # PnL calculation
        if pos_type == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        # 1. RSI extreme exit
        if pos_type == "BUY" and rsi > 80:
            return {"should_close": True, "reason": f"RSI {rsi:.0f}>80", "urgency": "high"}
        if pos_type == "SELL" and rsi < 20:
            return {"should_close": True, "reason": f"RSI {rsi:.0f}<20", "urgency": "high"}

        # 2. Momentum reversal: EMA cross against + RSI confirms
        if pos_type == "BUY" and ema_fast < ema_med and rsi > 55:
            return {"should_close": True, "reason": "EMA↓ reversal", "urgency": "medium"}
        if pos_type == "SELL" and ema_fast > ema_med and rsi < 45:
            return {"should_close": True, "reason": "EMA↑ reversal", "urgency": "medium"}

        # 3. Volume capitulation: at loss + volume spike
        if pnl_pct < -0.5 and volume_ratio > 2.0:
            return {"should_close": True, "reason": f"VolCapitulation {volume_ratio:.1f}x", "urgency": "high"}

        # 4. MACD reversal against + at loss
        if pos_type == "BUY" and macd_hist < 0 and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"MACD↓ loss {pnl_pct:.1f}%", "urgency": "medium"}
        if pos_type == "SELL" and macd_hist > 0 and pnl_pct < -0.5:
            return {"should_close": True, "reason": f"MACD↑ loss {pnl_pct:.1f}%", "urgency": "medium"}

        return {"should_close": False, "reason": "No exit signal", "urgency": "low"}

    def _get_weights(self, regime: Optional[Dict]) -> Dict:
        """Get indicator weights, applying regime overrides if available."""
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
        """Classify which entry pattern matches best."""
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
            # Pullback: EMA stacked + price near EMA med + RSI bounced
            if ema_fast > ema_med > ema_macro and atr > 0:
                dist_to_ema = abs(current_price - ema_med) / atr if atr > 0 else 99
                if dist_to_ema < 0.5 and 40 <= rsi <= 55:
                    return "pullback"

            # Momentum Breakout: EMA stacked + volume surge + RSI mid
            if ema_fast > ema_med and volume_ratio >= 1.3 and 45 <= rsi <= 65:
                return "momentum"

            # Mean Reversion: at BB lower + RSI oversold + RANGING
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


def _get_order_book_levels(order_book) -> tuple:
    """Возвращает bids/asks из dict или DTO OrderBook."""
    if not order_book:
        return [], []
    if isinstance(order_book, dict):
        return order_book.get("bids", []), order_book.get("asks", [])
    return getattr(order_book, "bids", []) or [], getattr(order_book, "asks", []) or []


def calculate_ob_imbalance(order_book: Dict, levels: int = 10) -> float:
    """
    Calculate order book imbalance from exchange order book data.

    Args:
        order_book: Dict or OrderBook with bids/asks levels.
        levels: Number of levels to consider

    Returns:
        float in [-1.0, 1.0]: positive = more bids (bullish), negative = more asks (bearish)
    """
    bids, asks = _get_order_book_levels(order_book)

    if not bids or not asks:
        return 0.0

    bid_vol = sum(float(b[1]) for b in bids[:levels])
    ask_vol = sum(float(a[1]) for a in asks[:levels])
    total = bid_vol + ask_vol

    if total == 0:
        return 0.0

    return (bid_vol - ask_vol) / total


def calculate_ob_spread_bps(order_book: Dict) -> float:
    """
    Calculate bid-ask spread in basis points from order book data.

    Args:
        order_book: Dict or OrderBook with bids/asks levels.

    Returns:
        float: spread in basis points (1 bp = 0.01%)
    """
    bids, asks = _get_order_book_levels(order_book)

    if not bids or not asks:
        return 0.0

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2

    if mid <= 0:
        return 0.0

    return (best_ask - best_bid) / mid * 10000
