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

                # 7. Графики
                info(f"📈 [{symbol}] Генерация графиков...")
                plotter.plot_symbol(symbol, current_position=active_trade)

                elapsed = time.time() - start_time
                info(f"✅ [{symbol}] Цикл завершён за {elapsed:.2f} сек. Ожидание 20 сек...")

            except Exception as e:
                error(f"❌ [{symbol}] Ошибка внутри торгового цикла: {str(e)}")
                error(traceback.format_exc())

            # Пауза между циклами
            time.sleep(20)

    except Exception as e:
        # In case import fails or other init error
        print(f"CRITICAL WORKER INIT ERROR {symbol}: {e}")
        traceback.print_exc()
