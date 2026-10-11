"""Offline-тесты collector/PNG с настоящими package imports, без initializer stubs."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.exchanges.market_data import StreamKey, StreamStore, normalize_candle
from src.exchanges.market_data_storage import (
    AtomicJsonStore, instance_prices_path, publish_market_snapshot, stream_snapshot_path,
)
from src.exchanges import ws_provider_factory as factory
from test_market_data_layer import Clock, T, raw


from src.core.data import collector
from src.core import plotter, chart_worker


class CollectorMarketDataTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
        self.config = {
            "EXCHANGE": "bingx", "MARKET_TYPE": "perpetual", "STRATEGY_STYLE": "TEST",
            "STYLE_PRESETS": {"TEST": {"timeframe": "1m", "history_candles": 2}},
            "CHART_RANGES": {}, "ENABLE_NEWS": False,
            "MARKET_DATA": {"history_limit": 3, "min_history": 1},
        }
        self.store = StreamStore(settings=self.config["MARKET_DATA"], clock=self.clock)
        self.time_patch = patch("src.exchanges.market_data.time.time", self.clock)
        self.time_patch.start(); self.addCleanup(self.time_patch.stop)
        self.store_patch = patch.object(collector, "StreamStore", side_effect=lambda **kwargs: StreamStore(clock=self.clock, **kwargs))
        self.store_patch.start(); self.addCleanup(self.store_patch.stop)
        factory.set_shared_cache(self.store.cache, self.store.ready)
        self.addCleanup(factory.set_shared_cache, None, None)
        self.client = Mock()
        self.client.get_kline_data.return_value = [raw(T), raw(T + 60_000), raw(T + 120_000)]
        self.client_patch = patch.object(collector, "get_exchange_client", return_value=self.client)
        self.client_patch.start(); self.addCleanup(self.client_patch.stop)

    def test_public_rest_normalizes_sorts_dedups_and_keeps_closed_strategy(self):
        self.client.get_kline_data.return_value = [raw(T + 120_000), raw(T), raw(T + 60_000), raw(T)]
        rows = collector.fetch_prices("BTC-USDT", self.config)
        self.assertEqual([row["timestamp"] for row in rows], [T, T + 60_000])
        self.assertTrue(all(row["is_closed"] for row in rows))
        self.client.get_kline_data.assert_called_once_with("BTC-USDT", interval="1m", limit=3)

    def test_live_cache_and_closed_strategy_are_distinct_without_rest(self):
        self.store.merge(self.key, self.client.get_kline_data.return_value, status="rest")
        live = collector.fetch_price_snapshot("BTC-USDT", self.config, limit=3)
        strategy = collector.fetch_prices("BTC-USDT", self.config)
        self.assertEqual(len(live["candles"]), 3)
        self.assertFalse(live["candles"][-1]["is_closed"])
        self.assertEqual(len(strategy), 2)
        self.client.get_kline_data.assert_not_called()

    def test_wrong_tf_cache_is_not_used_for_htf(self):
        self.store.merge(self.key, self.client.get_kline_data.return_value, status="rest")
        self.client.get_kline_data.return_value = [raw(T - 7_200_000), raw(T - 3_600_000), raw(T)]
        config = {**self.config, "AISCALP_SETTINGS": {"multi_timeframe": {"enabled": True, "htf_timeframe": "1h", "htf_candles": 2}}}
        rows = collector.fetch_htf_prices("BTCUSDT", config)
        self.assertEqual([row["timestamp"] for row in rows], [T - 7_200_000, T - 3_600_000])
        self.client.get_kline_data.assert_called_once_with("BTCUSDT", interval="1h", limit=3)

    def test_stale_cache_causes_public_rest_fallback(self):
        self.store.merge(self.key, self.client.get_kline_data.return_value, status="rest")
        self.clock.now += 31
        collector.fetch_prices("BTCUSDT", self.config)
        self.client.get_kline_data.assert_called_once()

    def test_invalid_gapped_or_short_history_is_not_strategy_ready(self):
        for rows in ([raw(T), raw(T + 120_000)], [raw(T + 120_000)],
                     [{**raw(T), "volume": -1}, raw(T + 60_000), raw(T + 120_000)]):
            with self.subTest(rows=rows):
                self.client.get_kline_data.return_value = rows
                with self.assertRaises(ValueError): collector.fetch_prices("BTCUSDT", self.config)

    def test_instance_publication_and_live_source_do_not_collide(self):
        with tempfile.TemporaryDirectory() as td:
            for instance, exchange in (("first", "bingx"), ("second", "mexc")):
                config = {**self.config, "DATA_DIR": td, "STRATEGY_INSTANCE_ID": instance, "EXCHANGE": exchange}
                self.assertTrue(collector.process_symbol("BTC-USDT", config))
                path = instance_prices_path(td, instance, "BTC-USDT")
                rows = AtomicJsonStore(path).read()
                envelope = AtomicJsonStore(str(path) + ".meta.json").read()
                self.assertEqual(len(rows), 2)
                self.assertEqual(envelope["stream"]["exchange"], exchange)
                self.assertEqual(envelope["candles"], rows)
                self.assertTrue(envelope["ready"])
                self.assertEqual(envelope["min_history"], 2)
                key = StreamKey(exchange, "perpetual", "BTCUSDT", "1m")
                self.assertEqual(len(AtomicJsonStore(stream_snapshot_path(td, key)).read()["candles"]), 3)
            self.assertFalse((Path(td) / "prices" / "BTCUSDT.json").exists())

    def test_shared_limiter_is_forwarded_to_unsigned_rest(self):
        limiter = Mock()
        with patch.object(collector, "get_exchange_client", return_value=collector.PublicMarketDataClient()), \
             patch.object(collector, "PublicRESTFetcher") as adapter:
            adapter.return_value.return_value = self.client.get_kline_data.return_value
            collector.fetch_prices("BTCUSDT", {**self.config, "PUBLIC_RATE_LIMITERS": {"bingx": limiter}})
            self.assertIs(adapter.call_args.kwargs["rate_limiters"]["bingx"], limiter)


class NativeChartTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.time_patch = patch("src.exchanges.market_data.time.time", self.clock)
        self.time_patch.start(); self.addCleanup(self.time_patch.stop)
        self.key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
        self.store = StreamStore(settings={"min_history": 1}, clock=self.clock)
        self.snapshot = self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        self.config = {"CHART_SETTINGS": {"dpi": 35, "chart_height": 6, "sma_periods": []},
                       "PLOTTER_RANGES": {"TEST": {"minutes": 10}},
                       "STRATEGY_STYLE": "OTHER", "STYLE_PRESETS": {"OTHER": {"timeframe": "1h"}}}

    def render(self, td, **kwargs):
        return plotter.plot_symbol("BTC-USDT", "TEST", config=self.config, data_dir=td,
                                   charts_dir=str(Path(td) / "charts"), **kwargs)

    def test_real_png_uses_native_candles_not_global_tf_and_atomic_source_name(self):
        captured = []
        original = plotter.plt.subplots
        def capture(*args, **kwargs):
            result = original(*args, **kwargs); captured.append(result); return result
        with tempfile.TemporaryDirectory() as td, patch.object(plotter, "resample_data", side_effect=AssertionError("Неявный resample запрещён")), \
             patch.object(plotter.plt, "subplots", side_effect=capture):
            path = self.render(td, stream_key=self.key, snapshot=self.snapshot)
            self.assertEqual(Path(path).name, "bingx_perpetual_BTCUSDT_1m.png")
            self.assertEqual(Path(path).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            fig, axes = captured[0]
            self.assertEqual(len(axes[0].patches), 3)
            self.assertIn("bingx/perpetual 1m", axes[0].get_title())
            self.assertIn("LIVE", axes[0].get_title())
            self.assertEqual(len(plotter.plt.get_fignums()), 0)
            self.assertEqual(list(Path(path).parent.glob("*.tmp")), [])

    def test_source_mismatch_and_ambiguous_catalog_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as td:
            publish_market_snapshot(td, self.snapshot)
            other = StreamKey("mexc", "spot", "BTCUSDT", "5m")
            publish_market_snapshot(td, self.store.merge(other, [raw(T)], status="rest"))
            self.assertIsNone(self.render(td))
            with self.assertRaises(ValueError): self.render(td, stream_key=other, snapshot=self.snapshot)
            self.assertFalse((Path(td) / "charts").exists())

    def test_stale_chart_is_labelled_and_failed_png_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as td:
            self.clock.now += 31
            path = self.render(td, stream_key=self.key, snapshot=self.snapshot)
            original = Path(path).read_bytes()
            from matplotlib.figure import Figure
            with patch.object(Figure, "savefig", side_effect=RuntimeError("Ошибка PNG")):
                with self.assertRaises(RuntimeError): self.render(td, stream_key=self.key, snapshot=self.snapshot)
            self.assertEqual(Path(path).read_bytes(), original)
            self.assertEqual(plotter.plt.get_fignums(), [])

    def test_utc_offsets_legacy_order_and_dedup(self):
        candles = [normalize_candle(raw(ts), self.key, now=self.clock()) for ts in (T + 120_000, T, T + 60_000, T)]
        for row in candles:
            row["snapshotTimeUTC"] = datetime.fromtimestamp(row["timestamp"] / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        candles[1]["snapshotTimeUTC"] = "2026-10-11T15:00:00+03:00"
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None): return datetime.fromtimestamp(T / 1000 + 135, timezone.utc)
        captured = []
        original = plotter.plt.subplots
        def capture(*args, **kwargs):
            result = original(*args, **kwargs); captured.append(result); return result
        with tempfile.TemporaryDirectory() as td, patch.object(plotter, "datetime", FrozenDateTime), \
             patch.object(plotter.plt, "subplots", side_effect=capture):
            AtomicJsonStore(Path(td) / "prices" / "BTC-USDT.json").write(candles)
            self.render(td)
            _, axes = captured[0]
            self.assertEqual(len(axes[0].patches), 3)
            dates = axes[2].lines[0].get_xdata()
            self.assertEqual(list(dates), [datetime(2026, 10, 11, 12, i) for i in range(3)])
            self.assertIn("UNKNOWN", axes[0].get_title())

    def test_catalog_adds_tf_dynamically_and_isolates_position_source(self):
        with tempfile.TemporaryDirectory() as td:
            publish_market_snapshot(td, self.snapshot)
            trade = {"exchange": "bingx", "market_type": "perpetual", "status": "OPEN"}
            self.assertEqual(len(chart_worker.chart_tasks(td)), 1)
            other = StreamKey("mexc", "perpetual", "BTCUSDT", "5m")
            publish_market_snapshot(td, self.store.merge(other, [raw(T)], status="rest"))
            tasks = chart_worker.chart_tasks(td, {"BTCUSDT": trade})
            self.assertEqual(len(tasks), 2)
            positions = {key.exchange: position for key, _, position in tasks}
            self.assertEqual(positions["bingx"], trade)
            self.assertIsNone(positions["mexc"])

    def test_explicit_resample_requires_complete_period_without_gap(self):
        rows = [raw(T + i * 60_000) for i in range(5)]
        dates = [datetime.fromtimestamp(row["timestamp"] / 1000, timezone.utc) for row in rows]
        columns = ([row["open"] for row in rows], [row["high"] for row in rows], [row["low"] for row in rows],
                   [row["close"] for row in rows], [row["volume"] for row in rows])
        result = plotter.resample_data(dates, *columns, "5m", now=T / 1000 + 600)
        self.assertEqual(len(result[0]), 1)
        self.assertEqual(result[-1], [10])
        with self.assertRaises(ValueError):
            plotter.resample_data(dates[:-1], *(values[:-1] for values in columns), "5m", now=T / 1000 + 600)
        with self.assertRaises(ValueError):
            plotter.resample_data(dates[::2], *(values[::2] for values in columns), "5m", now=T / 1000 + 600)


if __name__ == "__main__":
    unittest.main()
