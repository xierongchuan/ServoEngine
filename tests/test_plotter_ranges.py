"""Диапазоны нативного live графика с реальными source files и atomic PNG."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from src.core.plotter import plot_symbol
from src.exchanges.market_data import StreamKey
from src.exchanges.market_data_storage import publish_market_snapshot
from market_data_fixtures import FixedClock, make_snapshot


class TestPlotterRanges(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = FixedClock()
        fixed_time = patch("src.exchanges.market_data.time.time", self.clock)
        fixed_time.start()
        self.addCleanup(fixed_time.stop)
        self.symbol = "BTCUSDT"
        self.key = StreamKey("bingx", "perpetual", self.symbol, "1m")
        self.snapshot = make_snapshot(self.key, self.clock, 1440, close=102, source="ws")
        publish_market_snapshot(self.temp.name, self.snapshot)
        self.config = {"PLOTTER_RANGES": {"1H": {"hours": 1}, "4H": {"hours": 4}}}

    def _plot_range(self, time_range, expected):
        mock_fig = MagicMock()
        axes = [MagicMock() for _ in range(3)]
        # PNG проходит настоящий atomic_publish, а не глобальные open/exists mocks.
        mock_fig.savefig.side_effect = lambda stream, **kwargs: stream.write(b"\x89PNG\r\n\x1a\nfixture")
        with patch("src.core.plotter.plt") as mock_plt, patch("src.core.plotter.resample_data") as resample:
            mock_plt.subplots.return_value = (mock_fig, axes)
            filename = plot_symbol(self.symbol, time_range, stream_key=self.key, config=self.config,
                                   data_dir=self.temp.name, charts_dir=str(Path(self.temp.name) / "charts"))
            self.assertTrue(axes[0].bar.called)
            dates = axes[0].bar.call_args.args[0]
            self.assertEqual(len(dates), expected)
            self.assertTrue(all((right - left).total_seconds() == 60 for left, right in zip(dates, dates[1:])))
            resample.assert_not_called()
            self.assertEqual(mock_plt.close.call_args_list, [call("all"), call(mock_fig)])
        self.assertEqual(Path(filename).name, "bingx_perpetual_BTCUSDT_1m.png")
        self.assertTrue(Path(filename).read_bytes().startswith(b"\x89PNG"))
        self.assertFalse(list(Path(filename).parent.glob("*.tmp")))

    def test_plot_symbol_1h_range(self):
        # Обе границы включены: ровно 60 минут и 61 нативная свеча.
        self._plot_range("1h", 61)

    def test_plot_symbol_4h_range(self):
        self._plot_range("4h", 241)


if __name__ == "__main__":
    unittest.main()
