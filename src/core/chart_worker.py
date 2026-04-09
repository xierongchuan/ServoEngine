import time
import json
import os
import multiprocessing
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

from src.config import SYMBOLS, DATA_DIR, ENABLE_PARALLEL_PROCESSING, DEFAULT_PLOTTER_RANGE, CHART_SETTINGS
from src.utils.logger import info, error, warning
from src.core import plotter

ACTIVE_TRADES_FILE = os.path.join(DATA_DIR, "active_trades.json")

def load_active_trades():
    """Safely loads active trades from JSON"""
    try:
        if os.path.exists(ACTIVE_TRADES_FILE):
             # Simple read retry logic could be added here if needed,
             # but standard read is usually atomic enough for this size.
            with open(ACTIVE_TRADES_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        warning(f"⚠️ Failed to read active_trades.json: {e}")
    return {}

def run_chart_worker():
    """
    Main loop for the dedicated Chart Worker process.
    Continuously updates charts for all symbols every few seconds.
    """
    info(f"🎨 [ChartWorker] Process started (PID: {os.getpid()})")

    # Configuration
    UPDATE_INTERVAL = CHART_SETTINGS.get("update_interval", 10)

    # 3. Parallel Execution Setup
    if ENABLE_PARALLEL_PROCESSING:
        max_workers = min(multiprocessing.cpu_count(), len(SYMBOLS))
        executor = ProcessPoolExecutor(max_workers=max_workers)
        info(f"🚀 [ChartWorker] Parallel execution enabled (workers: {max_workers})")

    try:
        # Ждём первый цикл, чтобы collector успел записать данные
        time.sleep(UPDATE_INTERVAL)

        while True:
            start_time = time.time()

            try:
                # 1. Load current active trades to pass to plotter
                active_trades = load_active_trades()

                # 2. Prepare tasks
                symbols_to_plot = SYMBOLS

                if ENABLE_PARALLEL_PROCESSING:
                    futures = {}
                    for symbol in symbols_to_plot:
                        # Get active trade for this symbol if exists
                        current_position = active_trades.get(symbol)
                        # Submit task
                        future = executor.submit(plotter.plot_symbol, symbol, DEFAULT_PLOTTER_RANGE, current_position)
                        futures[future] = symbol

                    # Wait for results
                    for future in as_completed(futures):
                        symbol = futures[future]
                        try:
                            future.result()
                        except Exception as e:
                            error(f"❌ [ChartWorker] Error plotting {symbol}: {e}")
                else:
                    # Sequential Fallback
                    for symbol in symbols_to_plot:
                        try:
                            current_position = active_trades.get(symbol)
                            plotter.plot_symbol(symbol, DEFAULT_PLOTTER_RANGE, current_position)
                        except Exception as e:
                            error(f"❌ [ChartWorker] Error plotting {symbol}: {e}")

                elapsed = time.time() - start_time
                info(f"🎨 [ChartWorker] Charts updated in {elapsed:.2f}s")

            except Exception as e:
                error(f"❌ [ChartWorker] Critical Loop Error: {e}")
                error(traceback.format_exc())

            # 4. Sleep ensuring accurate interval
            # 4. Sleep ensuring accurate interval
            execution_time = time.time() - start_time
            _min_sleep = CHART_SETTINGS.get("min_sleep", 0.5)
            sleep_time = max(_min_sleep, UPDATE_INTERVAL - execution_time)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        info("🛑 [ChartWorker] Shutdown requested (KeyboardInterrupt)")
    finally:
        if ENABLE_PARALLEL_PROCESSING:
            executor.shutdown()

if __name__ == "__main__":
    try:
        run_chart_worker()
    except KeyboardInterrupt:
        pass
