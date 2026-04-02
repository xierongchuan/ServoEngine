"""AISCALP pipeline — pre-filter → scoring → regime → AI (always)."""

from typing import Any, Dict, Optional

from src.config import BOT_CONFIG, POSITION_SIZE_PERCENT, STYLE_PRESETS
from src.core import analyzer, predict, executor
from src.core.decision_journal import DecisionJournal
from src.core.trade_tracker import TradeTracker
from src.core.risk_manager import calculate_dynamic_sl_tp, validate_risk_parameters, calculate_position_size
from src.core.performance import get_performance_tracker
from src.exchanges.exchange_factory import get_exchange_client
from src.utils.logger import info, warning, StageTimer
from .base import StrategyPipeline


class AiscalpPipeline(StrategyPipeline):
    """Пайплайн AISCALP: сбор данных → HTF → pre-filter → сигнал → режим → AI."""

    def __init__(self, config: Dict):
        super().__init__(config)
        self._tracker = TradeTracker()
        self._journal = DecisionJournal()
        self._client = get_exchange_client()
        self._aiscalp_settings = config.get("AISCALP_SETTINGS", {})

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
        close_signal = analysis_result.get("close_signal", {})
        regime_data = analysis_result.get("regime", {})
        htf_data = analysis_result.get("htf_data", {})
        current_price = analysis_result.get("current_price", 0)
        regime_label = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

        # Compute dynamic SL/TP and sizing when we have a directional signal
        sl, tp, size_pct = None, None, None

        if signal in ("BUY", "SELL"):
            details = signal_data.get("details", {})
            support = details.get("support", 0)
            resistance = details.get("resistance", 0)

            # Dynamic SL/TP
            try:
                sl_tp = calculate_dynamic_sl_tp(
                    signal=signal, current_price=current_price,
                    atr=analysis_result.get("atr", 0), support=support, resistance=resistance,
                    regime=regime_data if regime_data else {}, quality=signal_quality
                )
                sl = sl_tp["stop_loss"]
                tp = sl_tp["take_profit"]
                info(f"🎯 [{symbol}] Dynamic SL/TP: SL={sl:.2f} TP={tp:.2f} R/R={sl_tp['risk_reward']:.2f}")

                try:
                    if not validate_risk_parameters(sl_tp, regime=regime_data if regime_data else None):
                        warning(f"⚠️ [{symbol}] Risk validation failed (R/R={sl_tp.get('risk_reward', 0):.2f})")
                        analysis_result["risk_warning"] = f"R/R={sl_tp.get('risk_reward', 0):.2f} below minimum"
                except Exception as e:
                    warning(f"⚠️ [{symbol}] Risk validation error: {e}")
            except Exception as e:
                warning(f"⚠️ [{symbol}] Dynamic SL/TP failed: {e}, using ATR-based fallback")
                atr = analysis_result.get("atr", 0)
                if signal == "BUY":
                    sl = current_price - atr * 2.0
                    tp = current_price + atr * 3.0
                else:
                    sl = current_price + atr * 2.0
                    tp = current_price - atr * 3.0

            # Dynamic position sizing
            try:
                perf = get_performance_tracker().get_recent_performance(symbol)
                size_pct = calculate_position_size(
                    base_pct=POSITION_SIZE_PERCENT, quality=signal_quality,
                    regime=regime_data if regime_data else {}, recent_performance=perf
                )
                info(f"📐 [{symbol}] Dynamic sizing: {size_pct:.1f}% (base={POSITION_SIZE_PERCENT}%, Q={signal_quality:.2f})")
            except Exception as e:
                warning(f"⚠️ [{symbol}] Dynamic sizing failed: {e}, using default")

        # Log deterministic close signal for AI context
        if real_position and close_signal and close_signal.get("should_close"):
            close_reason = close_signal.get("reason", "Deterministic exit")
            close_urgency = close_signal.get("urgency", "medium")
            info(f"🚨 [{symbol}] AISCALP close signal: {close_reason} (urgency: {close_urgency})")
            analysis_result["deterministic_close"] = {
                "should_close": True,
                "reason": close_reason,
                "urgency": close_urgency,
            }

        # Log signal status
        score = signal_data.get("score", 0)
        max_score = signal_data.get("max_score", 13)
        info(f"🔧 [{symbol}] AISCALP: signal={signal} score={score}/{max_score} Q={signal_quality:.2f} [{regime_label}]")

        # === ALWAYS invoke AI — it makes the final decision ===
        with StageTimer("AI Прогноз", symbol, "🧠"):
            info(f"🧠 [{symbol}] AISCALP: AI invoked (signal={signal}, regime={regime_label})")
            prediction = predict.process_analysis(analysis_result)

            # Use dynamic SL/TP if AI didn't provide its own
            if sl is not None and not prediction.get("stop_loss"):
                prediction["stop_loss"] = sl
            if tp is not None and not prediction.get("take_profit"):
                prediction["take_profit"] = tp
            if size_pct is not None:
                prediction["size_pct"] = size_pct

        return prediction
