"""MACDX pipeline — полностью детерминированный, без AI."""

from typing import Any, Dict, Optional

from src.config import POSITION_SIZE_PERCENT
from src.core import analyzer
from src.core.decision_journal import DecisionJournal
from src.core.trade_tracker import TradeTracker
from src.core.execution.risk import calculate_dynamic_sl_tp, validate_risk_parameters, calculate_position_size
from src.core.performance import get_performance_tracker
from src.core.regime import get_regime_detector
from src.exchanges.exchange_factory import get_exchange_client
from src.utils.logger import info, warning, error, StageTimer
from .base import StrategyPipeline


class MacdxPipeline(StrategyPipeline):
    """Пайплайн MACDX: сбор данных → индикаторы → MACD crossover сигнал → исполнение (без AI)."""

    def __init__(self, config: Dict):
        super().__init__(config)
        self._tracker = TradeTracker()
        self._journal = DecisionJournal()
        self._client = get_exchange_client()
        self._macdx_settings = config.get("MACDX_SETTINGS", {})

    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        try:
            from src.config import STRATEGY_STYLE
            from src.core.signals.macdx import MacdxSignalGenerator

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

            # Regime detection
            regime_data = get_regime_detector().detect(analysis_result)
            analysis_result["regime"] = regime_data
            regime_label = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"
            current_price = analysis_result.get("current_price", 0)

            # Generate MACDX signal
            gen = MacdxSignalGenerator(self._macdx_settings)
            signal_data = gen.generate(analysis_result, regime_data)
            analysis_result["signal_data"] = signal_data
            signal = signal_data.get("signal", "HOLD")
            signal_quality = signal_data.get("quality", 0.0)
            signal_confidence = signal_data.get("confidence", 0.0)
            confirmations = signal_data.get("confirmations", 0)

            # Check for close signal first
            close_signal = gen.should_close(analysis_result, real_position)
            analysis_result["close_signal"] = close_signal

            if real_position and close_signal.get("should_close"):
                close_reason = close_signal.get("reason", "Deterministic exit")
                close_urgency = close_signal.get("urgency", "medium")
                info(f"🚨 [{symbol}] MACDX CLOSE: {close_reason} (urgency: {close_urgency})")
                prediction = {
                    "symbol": symbol,
                    "action": "close",
                    "confidence": 0.9 if close_urgency == "high" else 0.75,
                    "score": signal_data.get("score", 0),
                    "confirmations": confirmations,
                    "reason": f"[MACDX] {close_reason}",
                    "current_price": current_price,
                }
                # Записываем закрытие в journal
                self._journal.record_close(symbol)
            elif signal == "HOLD":
                details = signal_data.get("details", {})
                indicators_status = details.get("indicators_status", [])
                ok_count = details.get("indicators_ok_count", 0)
                total_count = details.get("indicators_total_count", 6)
                potential_score = details.get("potential_score", 0)
                max_possible_score = details.get("max_possible_score", 9)

                if indicators_status:
                    ok_indicators = [s for s in indicators_status if s.get("ok")]
                    fail_indicators = [s for s in indicators_status if not s.get("ok")]
                    ok_str = ", ".join([f"{s['name']}" for s in ok_indicators]) if ok_indicators else "Нет"
                    macd_info = details.get('macd_hist', 0)
                    macd_prev_info = details.get('macd_hist_prev', 0)
                    fail_str = ", ".join([f"{s['name']}: {s.get('detail', '')}" for s in fail_indicators]) if fail_indicators else "Нет"
                    reason = (
                        f"[MACDX] Нет пересечения MACD (hist={macd_info:.6f}, prev={macd_prev_info:.6f}). "
                        f"Индикаторы: {ok_count}/{total_count} подтверждены. "
                        f"Score: {potential_score}/{max_possible_score}. "
                        f"Подтверждены: {ok_str}. "
                        f"Отклонены: {fail_str}. "
                        f"[{regime_label}]"
                    )
                else:
                    reason = f"[MACDX] Нет пересечения MACD [{regime_label}]"

                confidence = ok_count / total_count if total_count > 0 else 0.0
                info(f"🔧 [{symbol}] MACDX: No MACD cross (score: {potential_score}/{max_possible_score}, conf: {ok_count}/{total_count}) [{regime_label}]")
                prediction = {
                    "symbol": symbol,
                    "action": "hold",
                    "confidence": confidence,
                    "score": potential_score,
                    "max_score": max_possible_score,
                    "confirmations": ok_count,
                    "max_confirmations": total_count,
                    "reason": reason,
                    "current_price": current_price,
                    "indicators_status": indicators_status,
                }
            else:
                # Signal exists - execute directly (NO AI)
                details = signal_data.get("details", {})
                support = analysis_result.get("support", 0)
                resistance = analysis_result.get("resistance", 0)

                # Calculate SL/TP
                sl, tp, size_pct = self._calculate_sl_tp_and_size(
                    symbol, signal, current_price, analysis_result, support, resistance, regime_data, signal_quality
                )

                info(f"🔧 [{symbol}] MACDX: {signal} Q:{signal_quality:.2f} conf:{confirmations} [{regime_label}] - DIRECT EXECUTION (NO AI)")
                prediction = {
                    "symbol": symbol,
                    "action": signal.lower(),
                    "confidence": signal_confidence,
                    "score": signal_data.get("score", 0),
                    "confirmations": confirmations,
                    "reason": f"[MACDX] {signal} (score: {signal_data.get('score', 0)}/{signal_data.get('max_score', 9)}, conf: {confirmations}) [{regime_label}]",
                    "current_price": current_price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "size_pct": size_pct,
                }

            # Записываем решение в journal
            info(f"[{symbol}] Recording to journal: action={prediction.get('action')}, reason={prediction.get('reason', 'N/A')}, current_price={current_price}")
            self._journal.record(symbol, prediction, current_price)

            # Если сигнал на открытие, фиксируем trade plan
            action = prediction.get("action", "hold")
            if action in ("buy", "sell"):
                self._journal.set_trade_plan(symbol, prediction, current_price)

            # Обрезаем старые записи
            self._journal.trim_entries(symbol, STRATEGY_STYLE)

            return prediction
        except Exception as e:
            error(f"[{symbol}] MACDX pipeline error: {e}")
            import traceback
            traceback.print_exc()
            return None

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
            info(f"🎯 [{symbol}] MACDX SL/TP: SL={sl:.2f} TP={tp:.2f} R/R={sl_tp['risk_reward']:.2f}")

            macdx_rules = self._macdx_settings.get("signal_rules", {})
            if macdx_rules.get("enable_risk_validation", False):
                if not validate_risk_parameters(sl_tp, regime=regime if regime else None):
                    warning(f"⚠️ [{symbol}] MACDX: Risk validation failed (R/R={sl_tp.get('risk_reward', 0):.2f})")
                    return None, None, None
            else:
                info(f"✅ [{symbol}] MACDX: Risk validation disabled, proceeding (R/R={sl_tp.get('risk_reward', 0):.2f})")
        except Exception as e:
            warning(f"⚠️ [{symbol}] MACDX: SL/TP calculation failed: {e}, using ATR fallback")
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
            info(f"📐 [{symbol}] MACDX sizing: {size_pct:.1f}% (Q={quality:.2f})")
        except Exception as e:
            warning(f"⚠️ [{symbol}] MACDX: Dynamic sizing failed: {e}")

        return sl, tp, size_pct