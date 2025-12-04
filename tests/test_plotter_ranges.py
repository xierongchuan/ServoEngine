import sys
import os
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.plotter import plot_symbol, PLOTTER_RANGES

class TestPlotterRanges(unittest.TestCase):
    def setUp(self):
        self.symbol = "BTCUSDT"
        self.now = datetime.now()

        # Create mock data
        self.prices = []
        # Generate data for the last 24 hours (1440 minutes)
        for i in range(1440):
            ts = self.now - timedelta(minutes=i)
            self.prices.append({
                "snapshotTimeUTC": ts.isoformat(),
                "openPrice": 100.0,
                "highPrice": 105.0,
                "lowPrice": 95.0,
                "closePrice": 102.0,
                "volume": 1000.0
            })
        # Reverse to be chronological
        self.prices.reverse()

    @patch('src.core.plotter.json.load')
    @patch('src.core.plotter.open')
    @patch('src.core.plotter.plt')
    @patch('src.core.plotter.os.path.exists')
    def test_plot_symbol_1h_range(self, mock_exists, mock_plt, mock_open, mock_json_load):
        mock_json_load.return_value = self.prices
        mock_exists.return_value = True

        # Mock file open
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        # Mock plt.subplots to return fig and (ax1, ax2, ax3)
        mock_fig = MagicMock()
        mock_ax1 = MagicMock()
        mock_ax2 = MagicMock()
        mock_ax3 = MagicMock()
        mock_plt.subplots.return_value = (mock_fig, (mock_ax1, mock_ax2, mock_ax3))

        # Run plot_symbol with 1h range
        plot_symbol(self.symbol, "1h")

        # Verify that ax1.bar was called (candlesticks)
        self.assertTrue(mock_ax1.bar.called)

        # Get arguments passed to bar
        args, _ = mock_ax1.bar.call_args
        dates = args[0]

        # Check number of candles
        # 1h range should have roughly 60 candles (since we generated 1m data)
        # Allow some margin
        self.assertTrue(55 <= len(dates) <= 65, f"Expected ~60 candles, got {len(dates)}")

        print(f"✅ 1h range test passed: {len(dates)} candles plotted")

    @patch('src.core.plotter.json.load')
    @patch('src.core.plotter.open')
    @patch('src.core.plotter.plt')
    @patch('src.core.plotter.os.path.exists')
    def test_plot_symbol_4h_range(self, mock_exists, mock_plt, mock_open, mock_json_load):
        mock_json_load.return_value = self.prices
        mock_exists.return_value = True

        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        # Mock plt.subplots to return fig and (ax1, ax2, ax3)
        mock_fig = MagicMock()
        mock_ax1 = MagicMock()
        mock_ax2 = MagicMock()
        mock_ax3 = MagicMock()
        mock_plt.subplots.return_value = (mock_fig, (mock_ax1, mock_ax2, mock_ax3))

        plot_symbol(self.symbol, "4h")

        # Verify that ax1.bar was called (candlesticks)
        self.assertTrue(mock_ax1.bar.called)

        args, _ = mock_ax1.bar.call_args
        dates = args[0]

        # 4h range = 240 minutes
        self.assertTrue(230 <= len(dates) <= 250, f"Expected ~240 candles, got {len(dates)}")

        print(f"✅ 4h range test passed: {len(dates)} candles plotted")

if __name__ == '__main__':
    unittest.main()
