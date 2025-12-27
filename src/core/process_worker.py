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

        tracker = TradeTracker()

        while True:
            try:
                start_time = time.time()
                info(f"▶️ [{symbol}] Начало торгового цикла")

                # 2. Сбор данных
                info(f"📊 [{symbol}] Сбор данных...")
                collector.process_symbol(symbol)

                # 3. Анализ
                info(f"🔍 [{symbol}] Анализ индикаторов...")
                analysis_result = analyzer.analyze_symbol_with_position(symbol)

                # Sync Trade Tracker (History & Manual Close Detection)
                real_position = analysis_result.get("position")
                active_trade = tracker.sync_position(symbol, real_position)

                # 4. Прогноз (AI)
                info(f"🧠 [{symbol}] AI Прогноз...")
                prediction = predict.process_analysis(analysis_result)

                # 5. Исполнение
                info(f"💰 [{symbol}] Исполнение сигналов...")
                executor.execute_prediction(prediction)

                # 6. Мониторинг
                info(f"👀 [{symbol}] Мониторинг позиции...")
                monitor.monitor_symbol(symbol)

                # 7. Графики (moved to separate process)
                # plotter.plot_symbol(symbol, current_position=active_trade)

                elapsed = time.time() - start_time

                # Dynamic Sleep based on Strategy & Position Status
                from src.config import STRATEGY_STYLE, STYLE_PRESETS
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
                sleep_time = 5 # Error fallback

            # Пауза между циклами
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"🛑 [{symbol}] Process terminated.")
    except Exception as e:
        # In case import fails or other init error
        print(f"CRITICAL WORKER INIT ERROR {symbol}: {e}")
        traceback.print_exc()
