"""
Single-symbol worker process.
Runs the complete trading pipeline for ONE symbol in isolation.
"""

import time
import os
import traceback

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
        from src.utils.logger import setup_symbol_logger, info, error, warning, StageTimer
        setup_symbol_logger(symbol)

        info(f"🚀 [PROCESS START] Запущен бесконечный процесс для {symbol} (PID: {os.getpid()})")

        # Check if SCALP mode — use dedicated engine
        from src.config import STRATEGY_STYLE
        if STRATEGY_STYLE == "SCALP":
            info(f"⚡ [{symbol}] SCALP mode — launching ScalpEngine")
            from src.core.scalp_engine import ScalpEngine
            engine = ScalpEngine(symbol, ws_cache=ws_cache, ws_ready=ws_ready)
            engine.run()  # Blocks forever
            return

        # Импортируем модули один раз
        from src.core import collector, analyzer, predict, executor, monitor, plotter
        from src.core.trade_tracker import TradeTracker
        from src.core.decision_journal import DecisionJournal
        from src.config import ERROR_HANDLING
        from src.config import should_reload_config, reload_bot_config

        tracker = TradeTracker()
        journal = DecisionJournal()

        # === STARTUP SYNC: Clean stale trades ===
        try:
            from src.exchanges.exchange_factory import get_exchange_client
            client = get_exchange_client()
            real_positions_dict = client.get_positions()  # Returns {symbol: [positions]}
            stale_count = tracker.force_sync_all(real_positions_dict)
            if stale_count > 0:
                info(f"🧹 [{symbol}] Startup sync: cleaned {stale_count} stale trades")
        except Exception as e:
            warning(f"⚠️ [{symbol}] Startup sync failed: {e}")

        # === STARTUP: Fetch real commission rates from exchange ===
        try:
            commission = client.get_commission_rate(symbol)
            if commission:
                from src.config import update_fee_rates
                update_fee_rates(commission["maker"], commission["taker"])
                info(f"💰 [{symbol}] Commission rates from exchange: maker={commission['maker']}%, taker={commission['taker']}%")
            else:
                info(f"ℹ️ [{symbol}] Using default commission rates from config")
        except Exception as e:
            warning(f"⚠️ [{symbol}] Commission rate fetch failed: {e}")

        cycle_count = 0
        config_check_interval = 30  # Check config every 30 seconds
        last_config_check = time.time()

        while True:
            try:
                start_time = time.time()

                # 0. Periodic config hot-reload check
                current_time = time.time()
                if current_time - last_config_check >= config_check_interval:
                    if should_reload_config():
                        info(f"🔄 [{symbol}] Config file changed, reloading...")
                        reload_bot_config()
                        # Re-import updated module-level variables
                        from src.config import STRATEGY_STYLE as _new_style
                        from src.config import ERROR_HANDLING as _new_err
                        STRATEGY_STYLE = _new_style
                        ERROR_HANDLING = _new_err
                        info(f"✅ [{symbol}] Config reloaded (strategy={STRATEGY_STYLE})")
                    last_config_check = current_time

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
                    signal_quality = signal_data.get("quality", 0.0)
                    signal_confidence = signal_data.get("confidence", 0.0)
                    close_signal = analysis_result.get("close_signal", {})
                    regime_data = analysis_result.get("regime", {})

                    # Check if AI filter is enabled
                    from src.config import BOT_CONFIG
                    hybrid_settings = BOT_CONFIG.get("HYBRID_SETTINGS", {})
                    ai_filter_cfg = hybrid_settings.get("ai_filter", {})
                    ai_filter_enabled = ai_filter_cfg.get("enabled", False)
                    auto_approve_quality = ai_filter_cfg.get("auto_approve_quality", 0.7)
                    invoke_on_borderline = ai_filter_cfg.get("invoke_on_borderline", True)

                    regime_label = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

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
                    elif signal == "HOLD":
                        # No signal from deterministic system - skip AI call
                        info(f"🔧 [{symbol}] HYBRID: No signal (score: {signal_data.get('score', 0)}) [{regime_label}] - skipping AI")
                        prediction = {
                            "symbol": symbol,
                            "action": "hold",
                            "confidence": 0.0,
                            "reason": f"[HYBRID] No signal (score: {signal_data.get('score', 0)}) [{regime_label}]",
                            "current_price": analysis_result.get("current_price", 0)
                        }
                    else:
                        # Signal exists — decide whether to use AI or execute directly
                        details = signal_data.get("details", {})
                        support = details.get("support", 0)
                        resistance = details.get("resistance", 0)
                        current_price = analysis_result.get("current_price", 0)

                        # Dynamic SL/TP calculation
                        risk_validation_failed = False
                        try:
                            from src.core.risk_manager import calculate_dynamic_sl_tp
                            sl_tp = calculate_dynamic_sl_tp(
                                signal=signal,
                                current_price=current_price,
                                atr=analysis_result.get("atr", 0),
                                support=support,
                                resistance=resistance,
                                regime=regime_data if regime_data else {},
                                quality=signal_quality
                            )
                            sl = sl_tp["stop_loss"]
                            tp = sl_tp["take_profit"]
                            info(f"🎯 [{symbol}] Dynamic SL/TP: SL={sl:.2f} TP={tp:.2f} R/R={sl_tp['risk_reward']:.2f}")

                            # Validate risk parameters before proceeding
                            try:
                                from src.core.risk_manager import validate_risk_parameters
                                if not validate_risk_parameters(sl_tp):
                                    warning(f"⚠️ [{symbol}] Risk validation failed (R/R={sl_tp.get('risk_reward', 0):.2f}), skipping trade")
                                    prediction = {
                                        "symbol": symbol,
                                        "action": "hold",
                                        "confidence": 0.0,
                                        "reason": f"[HYBRID] Risk validation failed (R/R={sl_tp.get('risk_reward', 0):.2f})",
                                        "current_price": current_price
                                    }
                                    risk_validation_failed = True
                            except Exception as e:
                                warning(f"⚠️ [{symbol}] Risk validation error: {e}")
                        except Exception as e:
                            warning(f"⚠️ [{symbol}] Dynamic SL/TP failed: {e}, using ATR-based fallback")
                            atr = analysis_result.get("atr", 0)
                            if signal == "BUY":
                                sl = analysis_result.get("long_sl", current_price - atr * 1.5)
                                tp = analysis_result.get("long_tp", current_price + atr * 3.0)
                            else:
                                sl = analysis_result.get("short_sl", current_price + atr * 1.5)
                                tp = analysis_result.get("short_tp", current_price - atr * 3.0)

                        if risk_validation_failed:
                            pass  # prediction already set to HOLD above, skip to post-signal logic
                        else:
                            # Dynamic position sizing
                            size_pct = None
                            try:
                                from src.core.risk_manager import calculate_position_size
                                from src.core.performance import get_performance_tracker
                                from src.config import POSITION_SIZE_PERCENT
                                perf = get_performance_tracker().get_recent_performance(symbol)
                                size_pct = calculate_position_size(
                                    base_pct=POSITION_SIZE_PERCENT,
                                    quality=signal_quality,
                                    regime=regime_data if regime_data else {},
                                    recent_performance=perf
                                )
                                info(f"📐 [{symbol}] Dynamic sizing: {size_pct:.1f}% (base={POSITION_SIZE_PERCENT}%, Q={signal_quality:.2f})")
                            except Exception as e:
                                warning(f"⚠️ [{symbol}] Dynamic sizing failed: {e}, using default")

                            # Determine if AI should be invoked
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
                                # Execute deterministic signal directly
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
                                # AI veto invocation with focused HYBRID_VETO prompt
                                with StageTimer("AI Veto", symbol, "🧠"):
                                    info(f"🔧 [{symbol}] HYBRID: AI veto invoked ({ai_reason})")
                                    # Rebuild prompt with HYBRID_VETO strategy for focused risk assessment
                                    try:
                                        from src.prompts.builder import PromptBuilder
                                        veto_ctx = analysis_result.get("prompt_ctx", {})
                                        if veto_ctx:
                                            veto_prompt = PromptBuilder.build("HYBRID_VETO", veto_ctx)
                                            analysis_result["prompt"] = veto_prompt.strip()
                                    except Exception as e:
                                        warning(f"⚠️ [{symbol}] HYBRID_VETO prompt failed: {e}, using default")
                                    prediction = predict.process_analysis(analysis_result)

                                    # Override SL/TP with dynamic values if AI didn't provide better ones
                                    if not prediction.get("stop_loss"):
                                        prediction["stop_loss"] = sl
                                    if not prediction.get("take_profit"):
                                        prediction["take_profit"] = tp
                                    if size_pct:
                                        prediction["size_pct"] = size_pct

                                    # HYBRID constraint: AI cannot generate opposite signal
                                    if signal in ("BUY", "SELL"):
                                        ai_action = prediction.get("action", "hold").upper()
                                        if ai_action not in (signal, "HOLD", "CLOSE", "CLOSE_PARTIAL"):
                                            info(f"🔧 [{symbol}] HYBRID: AI tried {ai_action} but signal was {signal} - forcing HOLD")
                                            prediction["action"] = "hold"
                                            prediction["reason"] = f"[HYBRID] AI rejected {signal} signal"
                elif STRATEGY_STYLE == "INTRADAY":
                    # INTRADAY pipeline: pre-filter → signal scoring → regime → risk → AI (always)
                    signal_data = analysis_result.get("signal_data", {})
                    signal = signal_data.get("signal", "HOLD")
                    signal_quality = signal_data.get("quality", 0.0)
                    close_signal = analysis_result.get("close_signal", {})
                    regime_data = analysis_result.get("regime", {})
                    htf_data = analysis_result.get("htf_data", {})
                    current_price = analysis_result.get("current_price", 0)

                    regime_label = regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN"

                    # Compute dynamic SL/TP and sizing when we have a directional signal
                    sl = None
                    tp = None
                    size_pct = None

                    if signal in ("BUY", "SELL"):
                        details = signal_data.get("details", {})
                        support = details.get("support", 0)
                        resistance = details.get("resistance", 0)

                        # Dynamic SL/TP
                        try:
                            from src.core.risk_manager import calculate_dynamic_sl_tp
                            sl_tp = calculate_dynamic_sl_tp(
                                signal=signal,
                                current_price=current_price,
                                atr=analysis_result.get("atr", 0),
                                support=support,
                                resistance=resistance,
                                regime=regime_data if regime_data else {},
                                quality=signal_quality
                            )
                            sl = sl_tp["stop_loss"]
                            tp = sl_tp["take_profit"]
                            info(f"🎯 [{symbol}] Dynamic SL/TP: SL={sl:.2f} TP={tp:.2f} R/R={sl_tp['risk_reward']:.2f}")

                            try:
                                from src.core.risk_manager import validate_risk_parameters
                                if not validate_risk_parameters(sl_tp):
                                    warning(f"⚠️ [{symbol}] Risk validation failed (R/R={sl_tp.get('risk_reward', 0):.2f})")
                                    # Pass info to AI — it will see this in context
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
                            from src.core.risk_manager import calculate_position_size
                            from src.core.performance import get_performance_tracker
                            from src.config import POSITION_SIZE_PERCENT
                            perf = get_performance_tracker().get_recent_performance(symbol)
                            size_pct = calculate_position_size(
                                base_pct=POSITION_SIZE_PERCENT,
                                quality=signal_quality,
                                regime=regime_data if regime_data else {},
                                recent_performance=perf
                            )
                            info(f"📐 [{symbol}] Dynamic sizing: {size_pct:.1f}% (base={POSITION_SIZE_PERCENT}%, Q={signal_quality:.2f})")
                        except Exception as e:
                            warning(f"⚠️ [{symbol}] Dynamic sizing failed: {e}, using default")

                    # Log deterministic close signal for AI context
                    if real_position and close_signal and close_signal.get("should_close"):
                        close_reason = close_signal.get("reason", "Deterministic exit")
                        close_urgency = close_signal.get("urgency", "medium")
                        info(f"🚨 [{symbol}] INTRADAY close signal: {close_reason} (urgency: {close_urgency})")
                        # Pass close signal info to AI as recommendation
                        analysis_result["deterministic_close"] = {
                            "should_close": True,
                            "reason": close_reason,
                            "urgency": close_urgency,
                        }

                    # Log signal status
                    score = signal_data.get("score", 0)
                    max_score = signal_data.get("max_score", 13)
                    info(f"🔧 [{symbol}] INTRADAY: signal={signal} score={score}/{max_score} Q={signal_quality:.2f} [{regime_label}]")

                    # === ALWAYS invoke AI — it makes the final decision ===
                    with StageTimer("AI Прогноз", symbol, "🧠"):
                        info(f"🧠 [{symbol}] INTRADAY: AI invoked (signal={signal}, regime={regime_label})")
                        prediction = predict.process_analysis(analysis_result)

                        # Use dynamic SL/TP if AI didn't provide its own
                        if sl is not None and not prediction.get("stop_loss"):
                            prediction["stop_loss"] = sl
                        if tp is not None and not prediction.get("take_profit"):
                            prediction["take_profit"] = tp
                        if size_pct is not None:
                            prediction["size_pct"] = size_pct

                else:
                    # Fallback for SWING/GRID/etc - standard AI prediction
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

                # Проверка отключённых символов (не открываем новые позиции)
                from src.config import DISABLED_SYMBOLS
                if not real_position and prediction.get("action") in ("buy", "sell"):
                    # Нормализуем символ (убираем дефисы для сравнения)
                    normalized_symbol = symbol.replace("-", "").upper()
                    normalized_disabled = [s.replace("-", "").upper() for s in DISABLED_SYMBOLS]
                    if normalized_symbol in normalized_disabled:
                        info(f"⏹️ [{symbol}] Symbol is disabled. Changing action from {prediction['action']} to HOLD.")
                        prediction["action"] = "hold"
                        prediction["reason"] = f"Symbol {symbol} is disabled for trading"

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

                # 6b. Запись контекста входа для performance tracking (HYBRID)
                if action in ("buy", "sell") and not real_position and STRATEGY_STYLE in ("HYBRID", "INTRADAY"):
                    try:
                        entry_ctx = {
                            "entry_regime": regime_data.get("regime", "UNKNOWN") if regime_data else "UNKNOWN",
                            "entry_score": signal_data.get("score", 0) if signal_data else 0,
                            "entry_quality": signal_data.get("quality", 0.0) if signal_data else 0.0,
                            "entry_rsi": analysis_result.get("rsi", 0),
                            "entry_atr": analysis_result.get("atr", 0),
                            "entry_volume_ratio": analysis_result.get("volume_ratio", 0),
                        }
                        tracker.set_entry_context(symbol, entry_ctx)
                        info(f"📝 [{symbol}] Entry context saved: regime={entry_ctx['entry_regime']}, score={entry_ctx['entry_score']}, Q={entry_ctx['entry_quality']:.2f}")
                    except Exception as e:
                        warning(f"⚠️ [{symbol}] Failed to save entry context: {e}")

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

                # Periodic calibration check
                cycle_count += 1
                if cycle_count % 50 == 0:
                    try:
                        from src.core.performance import get_performance_tracker
                        perf_tracker = get_performance_tracker()
                        suggestions = perf_tracker.should_adjust_thresholds()
                        if suggestions:
                            perf_tracker.save_calibration_suggestions(suggestions)
                            info(f"📊 [{symbol}] Calibration check: {len(suggestions)} suggestions saved (cycle {cycle_count})")
                    except Exception as e:
                        warning(f"⚠️ [{symbol}] Calibration check failed: {e}")

                # Periodic funding rate check (every 10 cycles)
                if cycle_count % 10 == 0:
                    try:
                        funding = client.get_funding_rate(symbol)
                        if funding:
                            info(f"💸 [{symbol}] Funding rate: {funding['funding_rate_pct']:+.4f}% | Next: {funding['next_funding_time']}")
                    except Exception:
                        pass  # Non-critical

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
