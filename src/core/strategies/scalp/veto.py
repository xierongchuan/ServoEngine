"""ScalpVetoProcessor — AI veto processing, regime detection, rejection tracking."""

import json
import re
import time
from typing import Dict, Optional

from src.config import AI_VETO_OVERRIDE, AI_REGIME_OVERRIDE, AI_MODEL
from src.utils.logger import info, warning


class ScalpVetoProcessor:
    """Processes AI veto requests, regime updates, and tracks rejections."""

    def __init__(self, symbol: str, config: Dict, signal_generator, performance_tracker=None):
        self.symbol = symbol
        self.config = config
        self.signal_generator = signal_generator
        self.performance = performance_tracker

        ai_cfg = config.get("ai_integration", {})
        self._regime_ai_enabled = ai_cfg.get("regime_enabled", True)
        self._regime_interval_sec = ai_cfg.get("regime_interval_seconds", 300)
        self._regime_model = AI_REGIME_OVERRIDE.get("model", None) or AI_MODEL
        self._regime_temperature = AI_REGIME_OVERRIDE.get("temperature", 0.2)
        self._regime_max_tokens = AI_REGIME_OVERRIDE.get("max_tokens", 150)
        self._veto_enabled = ai_cfg.get("veto_enabled", True)
        self._veto_model = AI_VETO_OVERRIDE.get("model", None) or AI_MODEL
        self._veto_temperature = AI_VETO_OVERRIDE.get("temperature", 0.1)
        self._veto_max_tokens = AI_VETO_OVERRIDE.get("max_tokens", 100)
        self._veto_staleness_sec = ai_cfg.get("veto_staleness_seconds", 10)
        self._veto_max_cycles = ai_cfg.get("veto_max_stale_cycles", 2)
        self._borderline_quality = ai_cfg.get("borderline_quality_threshold", 0.3)

        # AI regime advisor state
        self._last_ai_regime_time: float = 0.0
        self._ai_regime_duration: int = 0
        self._ai_regime_label: str = "UNKNOWN"

        # Rejection tracking
        self._rejection_counts: Dict[str, int] = {}
        self._rejection_window_cycles: int = 60
        self._last_rejection_log_cycle: int = 0

        # Veto skip tracking
        self._veto_skip_counter: int = 0
        self._veto_skip_reasons: Dict[str, int] = {}
        self._last_veto_skip_log_cycle: int = 0
        self._veto_skip_log_interval: int = 40

    def process_veto(self, pending: Dict, fast_cycle: int, analyzer, regime_lock,
                     current_regime, ob_imbalance, position, execute_entry_fn) -> None:
        """Process pending AI veto request (slow loop).

        Staleness checks:
        1. Time-based: discard if age > veto_staleness_sec
        2. Cycle-based: discard if fast_cycle advanced > veto_max_cycles
        3. Signal-changed: discard if current signal direction differs from queued
        """
        # --- Staleness check 1: time-based ---
        age = time.time() - pending["time"]
        if age > self._veto_staleness_sec:
            info(f"[SCALP] {self.symbol}: Veto stale (time: {age:.1f}s > {self._veto_staleness_sec}s)")
            return

        # --- Staleness check 2: cycle-based ---
        cycles_elapsed = fast_cycle - pending.get("cycle", fast_cycle)
        if cycles_elapsed > self._veto_max_cycles:
            info(f"[SCALP] {self.symbol}: Veto stale (cycles: {cycles_elapsed} > {self._veto_max_cycles})")
            return

        # Don't veto if we now have a position
        if position:
            return

        signal = pending["signal"]
        indicators = pending["indicators"]

        # --- Staleness check 3: signal direction changed ---
        try:
            current_snap = analyzer.get_snapshot() if analyzer else None
            if current_snap:
                with regime_lock:
                    cr = current_regime
                current_signal = self.signal_generator.generate(
                    current_snap, regime=cr, ob_imbalance=ob_imbalance,
                )
                if current_signal["signal"] != signal["signal"]:
                    info(f"[SCALP] {self.symbol}: Veto stale (signal changed: "
                         f"{signal['signal']} \u2192 {current_signal['signal']})")
                    return
        except Exception:
            pass

        try:
            from src.prompts.strategies.scalp_veto import ScalpVetoStrategy
            from src.core.predict import get_prediction, parse_response

            veto_ctx = {
                "symbol": self.symbol,
                "signal": signal["signal"],
                "score": signal["score"],
                "max_score": signal["max_score"],
                "quality": signal["quality"],
                "regime": signal.get("regime", "UNKNOWN"),
                "rsi": indicators.get("rsi", 50),
                "volume_ratio": indicators.get("volume_ratio", 1.0),
                "momentum_dir": indicators.get("momentum_dir", "MIXED"),
                "pattern": signal.get("pattern", "generic"),
            }

            prompt = ScalpVetoStrategy().get_strategy_section(veto_ctx)
            raw_response = get_prediction(
                prompt,
                model=self._veto_model,
                max_tokens=self._veto_max_tokens,
                temperature=self._veto_temperature,
            )
            ai_result = parse_response(raw_response)

            if ai_result and ai_result.get("action"):
                ai_action = ai_result.get("action", "hold").upper()
                if ai_action == signal["signal"]:
                    info(f"[SCALP-L3] {self.symbol}: AI APPROVED {signal['signal']}")
                    execute_entry_fn(signal, indicators, ai_veto_used=True)
                else:
                    info(f"[SCALP-L3] {self.symbol}: AI REJECTED {signal['signal']} "
                         f"(AI said {ai_action}: {ai_result.get('reason', '?')})")
            else:
                warning(f"[SCALP-L3] {self.symbol}: Veto parse failed, discarding signal")

        except Exception as e:
            warning(f"[SCALP-L3] {self.symbol}: Veto error: {e}")

    def update_regime_deterministic(self, analyzer) -> Optional[Dict]:
        """Run deterministic regime detection from current indicators."""
        try:
            if not analyzer or not analyzer._bootstrapped:
                return None

            snapshot = analyzer.get_snapshot()

            from src.core.regime import detect_regime
            regime_input = {
                "ema9": snapshot["ema_fast"],
                "ema21": snapshot["ema_med"],
                "bb_upper": snapshot["bb_upper"],
                "bb_lower": snapshot["bb_lower"],
                "close_prices": list(analyzer._recent_closes),
                "atr_ratio": snapshot["atr_ratio"],
            }
            regime = detect_regime(regime_input)
            return regime

        except Exception as e:
            warning(f"[SCALP] {self.symbol}: Regime detection error: {e}")
            return None

    def update_regime_ai(self, analyzer, regime_lock, current_regime) -> None:
        """Run AI regime advisor (L2) if interval has elapsed."""
        now = time.time()
        if now - self._last_ai_regime_time < self._regime_interval_sec:
            return

        if not analyzer or not analyzer._bootstrapped:
            return

        try:
            from src.prompts.strategies.scalp_regime import ScalpRegimeStrategy
            from src.core.predict import get_prediction

            snapshot = analyzer.get_snapshot()

            ema_spread = 0.0
            if snapshot["ema_med"] > 0:
                ema_spread = (snapshot["ema_fast"] - snapshot["ema_med"]) / snapshot["ema_med"] * 100

            closes = list(analyzer._recent_closes)
            up_candles = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
            down_candles = max(0, len(closes) - 1 - up_candles)

            regime_ctx = {
                "symbol": self.symbol,
                "ema_spread": ema_spread,
                "rsi": snapshot.get("rsi", 50),
                "macd_hist": snapshot.get("macd_hist", 0.0),
                "bb_width": snapshot.get("bb_width", 0.0),
                "bb_percentile": 50,
                "atr_ratio": snapshot.get("atr_ratio", 1.0),
                "volume_ratio": snapshot.get("volume_ratio", 1.0),
                "support": snapshot.get("vwap_lower", 0),
                "resistance": snapshot.get("vwap_upper", 0),
                "up_candles": up_candles,
                "down_candles": down_candles,
                "prev_regime": self._ai_regime_label,
                "duration": self._ai_regime_duration,
            }

            prompt = ScalpRegimeStrategy().get_strategy_section(regime_ctx)
            raw_response = get_prediction(
                prompt,
                model=self._regime_model,
                max_tokens=self._regime_max_tokens,
                temperature=self._regime_temperature,
            )

            ai_result = self._parse_regime_response(raw_response)

            if ai_result:
                new_regime = ai_result.get("regime", "UNKNOWN")
                confidence = ai_result.get("confidence", 0.0)

                if new_regime == self._ai_regime_label:
                    self._ai_regime_duration += 1
                else:
                    self._ai_regime_duration = 1
                self._ai_regime_label = new_regime

                ai_log = (f"[SCALP-L2] {self.symbol}: AI regime={new_regime} conf={confidence:.2f} "
                          f"bias={ai_result.get('bias', '?')} mode={ai_result.get('scalp_mode', '?')} "
                          f"note={ai_result.get('note', '')}")

                if confidence >= 0.6:
                    ai_params = ai_result.get("params", {})
                    with regime_lock:
                        if current_regime:
                            current_regime["regime"] = new_regime
                            current_regime["ai_confidence"] = confidence
                            current_regime["ai_bias"] = ai_result.get("bias", "neutral")
                            current_regime["ai_scalp_mode"] = ai_result.get("scalp_mode", "")
                            if ai_params:
                                for key in ("min_score", "size_factor", "sl_mult", "tp_mult"):
                                    if key in ai_params:
                                        current_regime[f"recommended_{key}"] = ai_params[key]

                    info(f"{ai_log} [APPLIED]")
                else:
                    info(f"{ai_log} [LOW_CONF-IGNORED]")

            self._last_ai_regime_time = now

        except Exception as e:
            warning(f"[SCALP] {self.symbol}: AI regime advisor error: {e}")
            self._last_ai_regime_time = now

    def _parse_regime_response(self, raw_response) -> Optional[Dict]:
        """Parse L2 regime advisor JSON response."""
        try:
            if isinstance(raw_response, dict):
                return raw_response

            cleaned = re.sub(r'```json\s*', '', raw_response)
            cleaned = re.sub(r'```', '', cleaned)

            start = cleaned.find('{')
            if start == -1:
                return None

            brace_count = 0
            end = -1
            for i in range(start, len(cleaned)):
                if cleaned[i] == '{':
                    brace_count += 1
                elif cleaned[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break

            if end == -1:
                return None

            data = json.loads(cleaned[start:end])

            if "regime" not in data:
                return None

            data["regime"] = data["regime"].upper()
            if data["regime"] not in ("TRENDING", "RANGING", "VOLATILE", "TRANSITIONAL"):
                data["regime"] = "UNKNOWN"

            data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.5))))

            return data

        except Exception as e:
            warning(f"[SCALP-L2] {self.symbol}: Regime parse error: {e}")
            return None

    def track_rejection(self, reason: str, fast_cycle: int) -> None:
        """Track signal rejection reason for periodic summary."""
        self._rejection_counts[reason] = self._rejection_counts.get(reason, 0) + 1

        if fast_cycle - self._last_rejection_log_cycle >= self._rejection_window_cycles:
            self._log_rejection_summary()
            self._last_rejection_log_cycle = fast_cycle

    def _log_rejection_summary(self) -> None:
        """Log summary of signal rejections."""
        if not self._rejection_counts:
            info(f"[SCALP] {self.symbol}: Last {self._rejection_window_cycles} cycles: all HOLD (no signals)")
            return

        total = sum(self._rejection_counts.values())
        hold_count = max(0, self._rejection_window_cycles - total)
        parts = [f"HOLD:{hold_count}"]

        for reason, count in sorted(self._rejection_counts.items(), key=lambda x: -x[1]):
            parts.append(f"{reason}:{count}")

        info(f"[SCALP] {self.symbol}: Last {self._rejection_window_cycles} cycles: {', '.join(parts)}")
        self._rejection_counts = {}

    def track_veto_skip(self, reason: str, fast_cycle: int) -> None:
        """Track why veto wasn't used for periodic summary."""
        self._veto_skip_counter += 1
        self._veto_skip_reasons[reason] = self._veto_skip_reasons.get(reason, 0) + 1

        if fast_cycle - self._last_veto_skip_log_cycle >= self._veto_skip_log_interval:
            self._log_veto_skip_summary()
            self._last_veto_skip_log_cycle = fast_cycle

    def _log_veto_skip_summary(self) -> None:
        """Log summary of veto skips."""
        if not self._veto_skip_reasons:
            return

        reasons_str = ", ".join(f"{k}:{v}" for k, v in self._veto_skip_reasons.items())
        info(f"[SCALP-L3] {self.symbol}: Veto skip summary ({self._veto_skip_counter} cycles): {reasons_str}")
        self._veto_skip_counter = 0
        self._veto_skip_reasons = {}

    @property
    def veto_enabled(self) -> bool:
        return self._veto_enabled

    @property
    def borderline_quality(self) -> float:
        return self._borderline_quality
