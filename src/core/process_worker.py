"""
Single-symbol worker process.
Runs the complete trading pipeline for ONE symbol in isolation.
"""

import time
import os
import sys
import traceback
from datetime import datetime

# Implements the pipeline for a single process
def run_symbol_pipeline(symbol: str, ws_cache=None, ws_ready=None):
    """
    Запускает бесконечный торговый цикл для ОДНОГО символа.
    Этот код выполняется в отдельном процессе.

    Args:
        symbol: Trading pair symbol (e.g., "BTCUSDT")
        ws_cache: Shared WebSocket cache dict (multiprocessing.Manager proxy)
        ws_ready: Shared ready flags dict (multiprocessing.Manager proxy)
    """
    try:
        # 0. Setup shared WebSocket cache (if available)
        if ws_cache is not None and ws_ready is not None:
            try:
                from src.exchanges.ws_data_provider import set_shared_cache
                set_shared_cache(ws_cache, ws_ready)
            except Exception as e:
                pass  # WS not critical, will fallback to REST

        # 1. Настройка логгера (один раз на старте процесса)
        from src.utils.logger import setup_symbol_logger, info, error, StageTimer
        setup_symbol_logger(symbol)

        info(f"🚀 [PROCESS START] Запущен бесконечный процесс для {symbol} (PID: {os.getpid()})")

        # Импортируем модули один раз
        from src.core import collector, analyzer, predict, executor, monitor, plotter
        from src.core.trade_tracker import TradeTracker
        from src.core.decision_journal import DecisionJournal
        from src.config import STRATEGY_STYLE, ERROR_HANDLING

        tracker = TradeTracker()
        journal = DecisionJournal()

        # === STARTUP SYNC: Clean stale trades ===
        try:
            from src.exchanges.exchange_client import get_exchange_client
            client = get_exchange_client()
            all_positions = client.get_all_positions()  # Returns list of positions
            # Convert to dict: {symbol: position}
            real_positions_dict = {}
            for pos in all_positions:
                sym = pos.get("symbol")
                if sym:
                    real_positions_dict[sym] = pos
            stale_count = tracker.force_sync_all(real_positions_dict)
            if stale_count > 0:
                info(f"🧹 [{symbol}] Startup sync: cleaned {stale_count} stale trades")
        except Exception as e:
            from src.utils.logger import warning
            warning(f"⚠️ [{symbol}] Startup sync failed: {e}")

        while True:
            try:
                start_time = time.time()
                info(f"▶️ [{symbol}] Начало торгового цикла")

                # 2. Сбор данных
                with StageTimer("Сбор данных", symbol, "📊"):
                    collector.process_symbol(symbol)

                # 3. Анализ (с контекстом предыдущих решений)
                decision_context = journal.get_context(symbol, STRATEGY_STYLE)
                with StageTimer("Анализ индикаторов", symbol, "🔍"):
                    analysis_result = analyzer.analyze_symbol_with_position(symbol, decision_context=decision_context)

                # Sync Trade Tracker (History & Manual Close Detection)
                real_position = analysis_result.get("position")
                active_trade = tracker.sync_position(symbol, real_position)

                # 4. Проверка min_hold_hours (SWING режим)
                from src.config import STYLE_PRESETS
                preset = STYLE_PRESETS.get(STRATEGY_STYLE, {})
                min_hold_hours = preset.get("min_hold_hours", 0)

                if real_position and min_hold_hours > 0:
                    position_age = journal.get_position_age_hours(symbol)
                    if position_age is not None and position_age < min_hold_hours:
                        info(f"⏳ [{symbol}] Position age: {position_age:.1f}h < min_hold: {min_hold_hours}h. Forcing HOLD")
                        analysis_result["force_hold"] = True

                # 5. Прогноз (AI) - HYBRID mode optimization
                if STRATEGY_STYLE == "HYBRID":
                    signal_data = analysis_result.get("signal_data", {})
                    signal = signal_data.get("signal", "HOLD")
                    close_signal = analysis_result.get("close_signal", {})

                    # Check if AI filter is enabled
                    from src.config import BOT_CONFIG
                    hybrid_settings = BOT_CONFIG.get("HYBRID_SETTINGS", {})
                    ai_filter_enabled = hybrid_settings.get("ai_filter", {}).get("enabled", True)

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
                            "current_price": analysis_result.get("current_price", 0)
                        }
                    elif signal == "HOLD" and not real_position:
                        # No signal from deterministic system - skip AI call
                        info(f"🔧 [{symbol}] HYBRID: No signal (score: {signal_data.get('score', 0)}) - skipping AI")
                        prediction = {
                            "symbol": symbol,
                            "action": "hold",
                            "confidence": 0.0,
                            "reason": f"[HYBRID] No deterministic signal (score: {signal_data.get('score', 0)})",
                            "current_price": analysis_result.get("current_price", 0)
                        }
                    elif not ai_filter_enabled:
                        # AI filter disabled - use deterministic signal directly
                        info(f"🔧 [{symbol}] HYBRID: AI filter OFF - executing {signal} directly")

                        details = signal_data.get("details", {})
                        support = details.get("support")
                        resistance = details.get("resistance")

                        # SL/TP depends on direction
                        if signal == "BUY":
                            sl = support   # SL below support
                            tp = resistance  # TP at resistance
                        else:  # SELL
                            sl = resistance  # SL above resistance
                            tp = support     # TP at support

                        prediction = {
                            "symbol": symbol,
                            "action": signal.lower(),
                            "confidence": 0.75,
                            "reason": f"[HYBRID] {signal} (score: {signal_data.get('score', 0)}/{signal_data.get('max_score', 11)})",
                            "current_price": analysis_result.get("current_price", 0),
                            "stop_loss": sl,
                            "take_profit": tp
                        }
                    else:
                        # Signal exists and AI filter enabled - ask AI to confirm/reject
                        with StageTimer("AI Filter", symbol, "🧠"):
                            info(f"🔧 [{symbol}] HYBRID: Signal {signal} - asking AI to confirm")
                            prediction = predict.process_analysis(analysis_result)

                            # HYBRID constraint: AI cannot generate opposite signal
                            if signal in ("BUY", "SELL"):
                                ai_action = prediction.get("action", "hold").upper()
                                if ai_action not in (signal, "HOLD", "CLOSE", "CLOSE_PARTIAL"):
                                    info(f"🔧 [{symbol}] HYBRID: AI tried {ai_action} but signal was {signal} - forcing HOLD")
                                    prediction["action"] = "hold"
                                    prediction["reason"] = f"[HYBRID] AI rejected {signal} signal"
                else:
                    # Non-HYBRID mode - standard AI prediction
                    with StageTimer("AI Прогноз", symbol, "🧠"):
                        prediction = predict.process_analysis(analysis_result)

                # Проверка cooldown (если нет позиции и хотим открыть)
                cooldown_hours = preset.get("cooldown_after_close_hours", 0)
                if not real_position and cooldown_hours > 0:
                    in_cooldown, hours_left = journal.is_in_cooldown(symbol, cooldown_hours)
                    if in_cooldown and prediction.get("action") in ("buy", "sell"):
                        info(f"❄️ [{symbol}] Cooldown active: {hours_left:.1f}h remaining. Skipping entry signal.")
                        prediction["action"] = "hold"
                        prediction["reason"] = f"Cooldown period: {hours_left:.1f}h left"

                # 5. Запись решения в журнал
                current_price = analysis_result.get("current_price", 0)
                current_pnl = None
                if real_position:
                    entry_price = float(real_position.get("entry", real_position.get("avgPrice", 0)))
                    if entry_price > 0:
                        current_pnl = ((current_price - entry_price) / entry_price) * 100

                journal.record(symbol, prediction, current_price, current_pnl)

                # Trade plan: фиксируем при открытии, очищаем при закрытии
                action = prediction.get("action", "hold")
                if action in ("buy", "sell") and not real_position:
                    journal.set_trade_plan(symbol, prediction, current_price)
                elif not real_position and journal.data.get(symbol, {}).get("trade_plan"):
                    # Позиция закрылась - записываем для cooldown и очищаем план
                    journal.record_close(symbol)
                    journal.clear_trade_plan(symbol)

                journal.trim_entries(symbol, STRATEGY_STYLE)

                # 6. Исполнение
                with StageTimer("Исполнение сигналов", symbol, "💰"):
                    executor.execute_prediction(prediction)

                # 7. Мониторинг
                with StageTimer("Мониторинг позиции", symbol, "👀"):
                    monitor.monitor_symbol(symbol)

                # 7. Графики (moved to separate process)
                # plotter.plot_symbol(symbol, current_position=active_trade)

                elapsed = time.time() - start_time

                # Dynamic Sleep based on Strategy & Position Status
                # preset already loaded above for min_hold check
                base_interval = preset.get("loop_interval", 60)

                if real_position:
                    # If in position: Use configured active interval
                    pos_interval = preset.get("position_check_interval", 5)
                    sleep_time = pos_interval
                    info(f"✅ [{symbol}] Цикл завершён ({elapsed:.2f}s). 👀 Позиция активна -> Sleep {sleep_time}s")
                else:
                    # If searching: Relax based on strategy
                    sleep_time = base_interval
                    info(f"✅ [{symbol}] Цикл завершён ({elapsed:.2f}s). 💤 Поиск ({STRATEGY_STYLE}) -> Sleep {sleep_time}s")

                # Jitter: добавляем случайный разброс ±20% чтобы процессы не синхронизировались
                import random
                jitter = random.uniform(-0.2, 0.2) * sleep_time
                sleep_time = max(5, sleep_time + jitter)  # Минимум 5 секунд

            except KeyboardInterrupt:
                info(f"🛑 [{symbol}] Остановка по запросу (KeyboardInterrupt)")
                return
            except Exception as e:
                error(f"❌ [{symbol}] Ошибка внутри торгового цикла: {str(e)}")
                error(traceback.format_exc())
                sleep_time = ERROR_HANDLING.get("cycle_error_fallback_sleep", 5)

            # Пауза между циклами
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"🛑 [{symbol}] Process terminated.")
    except Exception as e:
        # In case import fails or other init error
        print(f"CRITICAL WORKER INIT ERROR {symbol}: {e}")
        traceback.print_exc()
