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
def run_symbol_pipeline(symbol: str):
    """
    Запускает бесконечный торговый цикл для ОДНОГО символа.
    Этот код выполняется в отдельном процессе.
    """
    try:
        # 1. Настройка логгера (один раз на старте процесса)
        from src.utils.logger import setup_symbol_logger, info, error
        setup_symbol_logger(symbol)

        info(f"🚀 [PROCESS START] Запущен бесконечный процесс для {symbol} (PID: {os.getpid()})")

        # Импортируем модули один раз
        from src.core import collector, analyzer, predict, executor, monitor, plotter
        from src.core.trade_tracker import TradeTracker
        from src.core.decision_journal import DecisionJournal
        from src.config import STRATEGY_STYLE, ERROR_HANDLING

        tracker = TradeTracker()
        journal = DecisionJournal()

        while True:
            try:
                start_time = time.time()
                info(f"▶️ [{symbol}] Начало торгового цикла")

                # 2. Сбор данных
                info(f"📊 [{symbol}] Сбор данных...")
                collector.process_symbol(symbol)

                # 3. Анализ (с контекстом предыдущих решений)
                decision_context = journal.get_context(symbol, STRATEGY_STYLE)
                info(f"🔍 [{symbol}] Анализ индикаторов...")
                analysis_result = analyzer.analyze_symbol_with_position(symbol, decision_context=decision_context)

                # Sync Trade Tracker (History & Manual Close Detection)
                real_position = analysis_result.get("position")
                active_trade = tracker.sync_position(symbol, real_position)

                # 4. Прогноз (AI)
                info(f"🧠 [{symbol}] AI Прогноз...")
                prediction = predict.process_analysis(analysis_result)

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
                    journal.clear_trade_plan(symbol)

                journal.trim_entries(symbol, STRATEGY_STYLE)

                # 6. Исполнение
                info(f"💰 [{symbol}] Исполнение сигналов...")
                executor.execute_prediction(prediction)

                # 6. Мониторинг
                info(f"👀 [{symbol}] Мониторинг позиции...")
                monitor.monitor_symbol(symbol)

                # 7. Графики (moved to separate process)
                # plotter.plot_symbol(symbol, current_position=active_trade)

                elapsed = time.time() - start_time

                # Dynamic Sleep based on Strategy & Position Status
                from src.config import STYLE_PRESETS
                preset = STYLE_PRESETS.get(STRATEGY_STYLE, STYLE_PRESETS["INTRADAY"])
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
