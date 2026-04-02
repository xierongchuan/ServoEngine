"""HYBRID pipeline — детерминированный сигнал + опциональный AI veto."""

from typing import Any, Dict, Optional

from src.config import BOT_CONFIG, POSITION_SIZE_PERCENT, DISABLED_SYMBOLS, STYLE_PRESETS
from src.core import analyzer, predict, executor
from src.core.decision_journal import DecisionJournal
from src.core.trade_tracker import TradeTracker
from src.core.risk_manager import calculate_dynamic_sl_tp, validate_risk_parameters, calculate_position_size
from src.core.performance import get_performance_tracker
from src.exchanges.exchange_factory import get_exchange_client
from src.utils.logger import info, warning, StageTimer
from .base import StrategyPipeline


class HybridPipeline(StrategyPipeline):
    """Пайплайн HYBRID: сбор данных → индикаторы → детерминированный сигнал → AI veto → исполнение."""

    def __init__(self, config: Dict):
        super().__init__(config)
        self._tracker = TradeTracker()
        self._journal = DecisionJournal()
        self._client = get_exchange_client()
        self._hybrid_settings = config.get("HYBRID_SETTINGS", {})
        self._ai_filter_cfg = self._hybrid_settings.get("ai_filter", {})

    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        from src.config import STRATEGY_STYLE

        # Fetch positions
        all_positions = self._client.get_positions()
        normalized_symbol = symbol.replace("-", "")
        symbol_positions = all_positions.get(normalized_symbol, [])
        real_position = symbol_positions[0] if symbol_positions else None

        # Decision context
        decision_context = self._journal.get_context(symbol, STRATEGY_STYLE)

        # Analysis
        with StageTimer("Анализ индикаторов", symbol, "🔍"):
            analysis_result = analyzer.analyze_symbol(
                symbol, position=real_position, decision_context=decision_context
            )

        self._tracker.sync_position(symbol, real_position, exchange_client=self._client)

        # Extract signal data
        signal_data = analysis_result.get("signal_data", {})
        signal = signal_data.get("signal", "HOLD")
        signal_quality = signal_data.get("quality", 0.0)
        signal_confidence = signal_data.get("confidence", 0.0)
        close_signal = analysis_result.get("close_signal", {})
        regime_data = analysis_result.get("regime", {})
        regime_label = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"
        current_price = analysis_result.get("current_price", 0)

        # === PRIORITY 1: Check for deterministic CLOSE signal ===
        if real_position and close_signal.get("should_close"):
            close_reason = close_signal.get("reason", "Deterministic exit")
            close_urgency = close_signal.get("urgency", "medium")
            info(f"🚨 [{symbol}] HYBRID CLOSE: {close_reason} (urgency: {close_urgency})")
            prediction = {
                "symbol": symbol,
                "action": "close",
                "confidence": 0.9 if close_urgency == "high" else 0.75,
                "reason": f"[HYBRID] {close_reason}",
                "current_price": current_price,
            }
        elif signal == "HOLD":
            info(f"🔧 [{symbol}] HYBRID: No signal (score: {signal_data.get('score', 0)}) [{regime_label}] - skipping AI")
            prediction = {
                "symbol": symbol,
                "action": "hold",
                "confidence": 0.0,
                "reason": f"[HYBRID] No signal (score: {signal_data.get('score', 0)}) [{regime_label}]",
                "current_price": current_price,
            }
        else:
            # Signal exists — decide whether to use AI or execute directly
            details = signal_data.get("details", {})
            support = details.get("support", 0)
            resistance = details.get("resistance", 0)

            # Dynamic SL/TP
            sl, tp, size_pct = self._calculate_sl_tp_and_size(
                symbol, signal, current_price, analysis_result, support, resistance, regime_data, signal_quality
            )

            ai_filter_enabled = self._ai_filter_cfg.get("enabled", False)
            auto_approve_quality = self._ai_filter_cfg.get("auto_approve_quality", 0.7)
            invoke_on_borderline = self._ai_filter_cfg.get("invoke_on_borderline", True)

            should_use_ai = False
            ai_reason = ""

            if ai_filter_enabled and signal in ("BUY", "SELL"):
                if signal_quality >= auto_approve_quality:
                    info(f"🔧 [{symbol}] HYBRID: High quality ({signal_quality:.2f}) - auto-approve, skip AI")
                elif invoke_on_borderline and signal_quality < 0.3:
                    should_use_ai = True
                    ai_reason = f"Borderline quality ({signal_quality:.2f})"
                elif regime_label == "TRANSITIONAL":
                    should_use_ai = True
                    ai_reason = "Transitional regime"
                elif details.get("conflicting", False):
                    should_use_ai = True
                    ai_reason = "Conflicting signals"

            if real_position and ai_filter_enabled:
                should_use_ai = True
                ai_reason = "Position management"

            if not should_use_ai:
                info(f"🔧 [{symbol}] HYBRID: {signal} Q:{signal_quality:.2f} [{regime_label}] - direct execution")
                prediction = {
                    "symbol": symbol,
                    "action": signal.lower(),
                    "confidence": signal_confidence,
                    "reason": f"[HYBRID] {signal} (score: {signal_data.get('score', 0)}/{signal_data.get('max_score', 10)}) [{regime_label}]",
                    "current_price": current_price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "size_pct": size_pct,
                }
            else:
                with StageTimer("AI Veto", symbol, "🧠"):
                    info(f"🔧 [{symbol}] HYBRID: AI veto invoked ({ai_reason})")
                    try:
                        from src.prompts.builder import PromptBuilder
                        veto_ctx = analysis_result.get("prompt_ctx", {})
                        if veto_ctx:
                            veto_prompt = PromptBuilder.build("HYBRID_VETO", veto_ctx)
                            analysis_result["prompt"] = veto_prompt.strip()
                    except Exception as e:
                        warning(f"⚠️ [{symbol}] HYBRID_VETO prompt failed: {e}")
                    prediction = predict.process_analysis(analysis_result)

                    if sl and not prediction.get("stop_loss"):
                        prediction["stop_loss"] = sl
                    if tp and not prediction.get("take_profit"):
                        prediction["take_profit"] = tp
                    if size_pct:
                        prediction["size_pct"] = size_pct

                    if signal in ("BUY", "SELL"):
                        ai_action = prediction.get("action", "hold").upper()
                        if ai_action not in (signal, "HOLD", "CLOSE", "CLOSE_PARTIAL"):
                            info(f"🔧 [{symbol}] HYBRID: AI tried {ai_action} but signal was {signal} - forcing HOLD")
                            prediction["action"] = "hold"
                            prediction["reason"] = f"[HYBRID] AI rejected {signal} signal"

        return prediction

    def _calculate_sl_tp_and_size(self, symbol, signal, current_price, analysis, support, resistance, regime, quality):
        sl, tp, size_pct = None, None, None
        try:
            sl_tp = calculate_dynamic_sl_tp(
                signal=signal, current_price=current_price,
                atr=analysis.get("atr", 0), support=support, resistance=resistance,
                regime=regime if regime else {}, quality=quality
            )
            sl = sl_tp["stop_loss"]
            tp = sl_tp["take_profit"]
            info(f"🎯 [{symbol}] Dynamic SL/TP: SL={sl:.2f} TP={tp:.2f} R/R={sl_tp['risk_reward']:.2f}")

            if not validate_risk_parameters(sl_tp, regime=regime if regime else None):
                warning(f"⚠️ [{symbol}] Risk validation failed (R/R={sl_tp.get('risk_reward', 0):.2f})")
                return None, None, None
        except Exception as e:
            warning(f"⚠️ [{symbol}] Dynamic SL/TP failed: {e}, using ATR fallback")
            atr = analysis.get("atr", 0)
            if signal == "BUY":
                sl = current_price - atr * 1.5
                tp = current_price + atr * 3.0
            else:
                sl = current_price + atr * 1.5
                tp = current_price - atr * 3.0

        try:
            perf = get_performance_tracker().get_recent_performance(symbol)
            size_pct = calculate_position_size(
                base_pct=POSITION_SIZE_PERCENT, quality=quality,
                regime=regime if regime else {}, recent_performance=perf
            )
            info(f"📐 [{symbol}] Dynamic sizing: {size_pct:.1f}% (base={POSITION_SIZE_PERCENT}%, Q={quality:.2f})")
        except Exception as e:
            warning(f"⚠️ [{symbol}] Dynamic sizing failed: {e}")

        return sl, tp, size_pct
