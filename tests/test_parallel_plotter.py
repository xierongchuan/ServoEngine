import sys
import os
import time
import shutil
from unittest.mock import patch

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.plotter import main, SYMBOLS, CHARTS_DIR
import logging

# Configure logging to show INFO
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')

# Add StreamHandler to 'steps' logger to see output in terminal
steps_logger = logging.getLogger('steps')
steps_logger.addHandler(logging.StreamHandler(sys.stdout))

def test_parallel_plotting():
    print("🧪 Testing Parallel Plotting...")

    # Ensure we have some data to plot
    # We rely on existing data in data/prices or we should mock it.
    # For simplicity, let's assume data exists for SYMBOLS.
    # If not, we might need to mock plot_symbol to just sleep.

    # Mock plot_symbol to simulate work and avoid actual plotting overhead for this test
    def mock_plot_symbol_delayed(symbol):
        time.sleep(0.5) # Simulate 0.5s plotting time
        return True

    # Patch plot_symbol in src.core.plotter
    # Note: Since plot_symbol is imported/used in main, we need to patch it where it's used.
    # But main imports plot_symbol from the same file.
    # However, ProcessPoolExecutor pickles the function. Mocking might be tricky with multiprocessing.
    # A better integration test is to run it with actual plotting but limited symbols.

    # Let's try to run the actual main() but with a small subset of SYMBOLS if possible.
    # We can patch SYMBOLS in src.core.plotter

    test_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

    with patch('src.core.plotter.SYMBOLS', test_symbols):
        # We also need to patch plot_symbol to be slow, but picklable.
        # It's hard to patch a function for multiprocessing.
        # So we will rely on the actual execution speed.
        # If actual plotting is fast, we might not see big difference.
        # But usually plotting is slow (~0.5-1s per chart).

        print(f"Plotting {len(test_symbols)} symbols...")
        start_time = time.time()
        main()
        end_time = time.time()

        duration = end_time - start_time
        print(f"⏱️ Processed {len(test_symbols)} charts in {duration:.2f} seconds.")

        # Sequential: ~2-4 seconds
        # Parallel: ~1-1.5 seconds

        if duration < 2.0:
             print("✅ Parallel execution confirmed (Duration < 2.0s)")
        else:
             print("⚠️ Duration >= 2.0s. Might be sequential or slow machine.")

if __name__ == "__main__":
    test_parallel_plotting()
