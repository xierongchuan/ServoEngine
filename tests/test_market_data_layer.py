"""Offline-регрессии идентичности, OHLCV, свежести, REST ranges и atomic публикации."""

import json
import multiprocessing
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.exchanges.market_data import (
    StreamKey, StreamStore, candle_gaps, closed_candles, evaluate_snapshot,
    interval_start, merge_candles, next_start, normalize_candle, timestamp_ms,
)
from src.exchanges.market_data_storage import (
    AtomicJsonStore, atomic_publish, instance_prices_path, publish_market_snapshot,
    publish_strategy_view, read_market_snapshot, read_strategy_snapshot, stream_snapshot_path,
)
from src.exchanges.public_market_rest import PublicRESTFetcher
from src.exchanges import ws_provider_factory as factory

T = int(datetime(2026, 10, 11, 12, tzinfo=timezone.utc).timestamp() * 1000)


def raw(ts, close=11, volume=2):
    return {"timestamp": ts, "open": 10, "high": max(12, close), "low": min(9, close), "close": close, "volume": volume}


class Clock:
    def __init__(self, now=T / 1000 + 135):
        self.now = now
    def __call__(self):
        return self.now


def _read_proxy(cache, ready, key, output):
    factory.set_shared_cache(cache, ready)
    value = factory.get_market_snapshot(key, allow_stale=True)
    output.put((value["stream"], value["version"], value["candles"][-1]["timestamp"]))


class MarketDataLayerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.key = StreamKey("bingx", "perpetual", "BTC-USDT", "1m")
        self.settings = {"history_limit": 10, "min_history": 2, "max_age_seconds": 30}
        self.store = StreamStore(settings=self.settings, clock=self.clock)
        self.time_patch = patch("src.exchanges.market_data.time.time", self.clock)
        self.time_patch.start()
        factory.set_shared_cache(self.store.cache, self.store.ready)

    def tearDown(self):
        factory.set_shared_cache(None, None)
        self.time_patch.stop()

    def test_stream_identity_and_interval_aliases(self):
        self.assertEqual(self.key.id, "bingx:perpetual:BTCUSDT:1m")
        self.assertEqual(self.key, StreamKey("BINGX", "PERPETUAL", "btc/usdt", "1min"))
        self.assertNotEqual(self.key, StreamKey("mexc", "spot", "BTC_USDT", "1m"))
        self.assertEqual(StreamKey.from_value(self.key.id), self.key)
        for symbol in ("../BTC", "BTC USDT", ""):
            with self.assertRaises(ValueError): StreamKey("bingx", "perpetual", symbol, "1m")

    def test_numeric_timestamp_seconds_iso_and_ohlcv(self):
        for value in (T, T // 1000, str(T), datetime.fromtimestamp(T / 1000, timezone.utc).isoformat()):
            self.assertEqual(timestamp_ms(value), T)
        candle = normalize_candle(raw(T), self.key, now=self.clock())
        self.assertEqual(candle["timestamp"], T)
        self.assertTrue(candle["is_closed"])
        bad_rows = [{**raw(T), "high": 1}, {**raw(T), "low": 15}, {**raw(T), "volume": -1},
                    {**raw(T), "close": "NaN"}, {**raw(T), "open": True}, {**raw(T), "timestamp": T + 1},
                    {**raw(T), "timestamp": None}, {**raw(T), "symbol": "ETHUSDT"},
                    {**raw(T), "interval": "5m"}, {**raw(T), "closed": "false"},
                    {**raw(T), "t": T + 60_000}, {**raw(T), "closed": True, "x": False}]
        missing = raw(T); del missing["high"]; bad_rows.append(missing)
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises((TypeError, ValueError)):
                normalize_candle(row, self.key, now=self.clock())

    def test_rest_ws_timestamp_merge_no_duplicate(self):
        rest = raw(T + 120_000)
        del rest["timestamp"]
        rest["snapshotTimeUTC"] = datetime.fromtimestamp((T + 120_000) / 1000, timezone.utc).isoformat()
        self.store.merge(self.key, [raw(T), raw(T + 60_000), rest], status="rest")
        value = self.store.merge(self.key, [raw(T + 120_000, 11.5)], source="ws")
        self.assertEqual(len(value["candles"]), 3)
        self.assertEqual(value["candles"][-1]["closePrice"], 11.5)
        self.assertFalse(value["candles"][-1]["is_closed"])

    def test_order_dedup_and_rest_final_cannot_regress(self):
        value = self.store.merge(self.key, [raw(T + 120_000), raw(T), raw(T + 60_000), raw(T)], status="rest")
        self.assertEqual([row["timestamp"] for row in value["candles"]], [T, T + 60_000, T + 120_000])
        previous = value["candles"][0]
        value = self.store.merge(self.key, [raw(T, 9.5)], source="ws")
        self.assertEqual(value["candles"][0], previous)
        self.assertTrue(value["candles"][0]["rest_confirmed"])

    def test_late_historical_message_does_not_refresh_tail(self):
        self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        self.clock.now += 31
        value = self.store.merge(self.key, [raw(T, 10.5)], source="ws")
        self.assertTrue(value["stale"])
        self.assertFalse(value["ready"])

    def test_same_period_late_and_replayed_event_cannot_refresh_freshness(self):
        event = T + 130_000
        self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        self.store.merge(self.key, [{**raw(T + 120_000, 11.7), "event_time_ms": event}], source="ws")
        self.clock.now += 31
        for message_time in (event - 1000, event):
            snapshot = self.store.merge(self.key, [{**raw(T + 120_000, 10.5), "event_time_ms": message_time}], source="ws")
            self.assertEqual(snapshot["candles"][-1]["closePrice"], 11.7)
            self.assertTrue(snapshot["stale"])

    def test_rest_inflight_response_does_not_overwrite_new_ws_open(self):
        self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        self.store.merge(self.key, [raw(T + 120_000, 11.8)], source="ws", received_at=self.clock() + 1)
        value = self.store.merge(self.key, [raw(T + 120_000, 10.5)], source="rest", received_at=self.clock())
        self.assertEqual(value["candles"][-1]["closePrice"], 11.8)

    def test_gap_detection_and_repair(self):
        self.clock.now = T / 1000 + 195
        value = self.store.merge(self.key, [raw(T), raw(T + 180_000)], status="rest")
        self.assertEqual(value["gaps"], [{"start_ms": T + 60_000, "end_ms": T + 180_000}])
        self.assertFalse(value["ready"])
        repaired = self.store.merge(self.key, [raw(T + 60_000), raw(T + 120_000)], status="rest")
        self.assertEqual(repaired["gaps"], [])
        self.assertTrue(repaired["ready"])

    def test_cache_tf_exchange_limit_and_closed_view(self):
        self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        lookup = {"exchange": "bingx", "market_type": "perpetual"}
        self.assertEqual(len(factory.get_klines_from_shared_cache("BTCUSDT", 2, "1m", **lookup)), 2)
        self.assertEqual(factory.get_klines_from_shared_cache("BTCUSDT", 2, "1h", **lookup), [])
        self.assertEqual(factory.get_klines_from_shared_cache("BTCUSDT", 4, "1m", **lookup), [])
        rows = factory.get_klines_from_shared_cache("BTCUSDT", 2, "1m", closed_only=True, **lookup)
        self.assertEqual([row["timestamp"] for row in rows], [T, T + 60_000])
        five = StreamKey("bingx", "perpetual", "BTCUSDT", "5m")
        self.store.merge(five, [raw(T - 300_000), raw(T)], status="rest")
        self.assertEqual(factory.get_klines_from_shared_cache("BTCUSDT", 2, **lookup), [])
        self.assertEqual(factory.get_klines_from_shared_cache("BTCUSDT", 2, "1m", exchange="mexc", market_type="perpetual"), [])

    def test_closed_view_cannot_hide_missing_final_confirmation_in_middle(self):
        self.clock.now = T / 1000 + 255
        rows = [{**raw(T + i * 60_000), "x": i in {0, 2, 3}} for i in range(5)]
        self.store.merge(self.key, rows, source="ws", status="live")
        self.assertTrue(self.store.snapshot(self.key)["ready"])
        self.assertEqual(factory.get_klines_from_shared_cache("BTCUSDT", 3, "1m", exchange="bingx",
                         market_type="perpetual", closed_only=True), [])
        self.assertEqual(len(factory.get_klines_from_shared_cache("BTCUSDT", 2, "1m", exchange="bingx",
                             market_type="perpetual", closed_only=True)), 2)

    def test_reader_freshness_not_candle_age_or_ready_flag(self):
        five = StreamKey("bingx", "perpetual", "BTCUSDT", "5m")
        self.store.merge(five, [raw(T - 300_000), raw(T)], status="rest")
        self.assertTrue(self.store.snapshot(five)["ready"])
        self.clock.now += 31
        self.assertFalse(self.store.snapshot(five)["ready"])
        fresh = evaluate_snapshot(self.store.cache[five.id], now=T / 1000 + 135)
        self.assertTrue(fresh["ready"])

    def test_error_and_recovery_state_fail_closed(self):
        self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        for status in ("error", "recovering", "stopped"):
            self.assertFalse(self.store.mark(self.key, status=status)["ready"])
        self.assertTrue(self.store.merge(self.key, [raw(T + 120_000)], status="rest")["ready"])

    def test_month_and_week_boundaries(self):
        january = timestamp_ms("2026-01-01T00:00:00Z")
        february = timestamp_ms("2026-02-01T00:00:00Z")
        march = timestamp_ms("2026-03-01T00:00:00Z")
        self.assertEqual(next_start(january, "1M"), february)
        self.assertEqual(next_start(february, "1M"), march)
        monday = timestamp_ms("2026-10-05T00:00:00Z")
        self.assertEqual(interval_start(T, "1w"), monday)

    def test_slow_rest_cannot_finalize_period_after_request_or_fabricate_freshness(self):
        requested = self.clock()
        self.clock.now += 55
        value = self.store.merge(self.key, [raw(T + 120_000)], received_at=requested, status="rest")
        self.assertFalse(value["candles"][-1]["is_closed"])
        self.assertEqual(value["last_valid_update"], requested)
        self.assertTrue(value["stale"])

    def test_hot_settings_recalculate_readiness_and_history(self):
        self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        self.clock.now += 20
        self.store.reconfigure({"max_age_seconds": 10, "history_limit": 2})
        self.assertEqual(len(self.store.snapshot(self.key)["candles"]), 2)
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        with self.assertRaises(ValueError): self.store.reconfigure({"history_limit": 0.5})

    def test_batch_validation_is_atomic(self):
        before = self.store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
        with self.assertRaises(ValueError):
            self.store.merge(self.key, [raw(T, 10.5), {**raw(T + 120_000), "volume": float("inf")}])
        self.assertEqual(self.store.cache[self.key.id], before)

    def test_multiprocessing_snapshot_proxy(self):
        context = multiprocessing.get_context("spawn")
        with context.Manager() as manager:
            store = StreamStore(manager.dict(), manager.dict(), self.settings, clock=self.clock)
            snapshot = store.merge(self.key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
            output = context.Queue()
            child = context.Process(target=_read_proxy, args=(store.cache, store.ready, self.key, output))
            child.start(); child.join(timeout=10)
            if child.is_alive(): child.terminate(); child.join(); self.fail("Worker чтения завис")
            self.assertEqual(child.exitcode, 0)
            stream, version, tail = output.get(timeout=2)
            self.assertEqual(stream, self.key.to_dict()); self.assertEqual(version, snapshot["version"])
            self.assertEqual(tail, T + 120_000)
            output.close()


class AtomicMarketPublicationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.time_patch = patch("src.exchanges.market_data.time.time", self.clock)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)

    def test_failed_writer_preserves_old_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "snapshot.json"
            AtomicJsonStore(target).write({"value": 1})
            def fail(stream):
                stream.write(b"broken"); raise RuntimeError("Ошибка публикации")
            with self.assertRaises(RuntimeError): atomic_publish(target, fail)
            self.assertEqual(json.loads(target.read_text()), {"value": 1})
            self.assertEqual(list(Path(td).glob("*.tmp")), [])

    def test_atomic_readers_and_locked_append(self):
        with tempfile.TemporaryDirectory() as td:
            store = AtomicJsonStore(Path(td) / "rows.json")
            store.write([])
            errors = []
            def append_batch():
                try:
                    for value in range(25):
                        store.append(value); self.assertIsInstance(store.read(), list)
                except Exception as exc: errors.append(exc)
            threads = [threading.Thread(target=append_batch) for _ in range(4)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=10)
            self.assertFalse(errors); self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(len(store.read()), 100)

    def test_source_and_instance_isolation_live_vs_closed(self):
        with tempfile.TemporaryDirectory() as td:
            now = T / 1000 + 135
            one = StreamKey("bingx", "perpetual", "BTC-USDT", "1m")
            five = StreamKey("mexc", "perpetual", "BTC_USDT", "5m")
            store = StreamStore(settings={"min_history": 1}, clock=lambda: now)
            live = store.merge(one, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
            other = store.merge(five, [raw(T - 300_000), raw(T)], status="rest")
            publish_market_snapshot(td, live); publish_market_snapshot(td, other)
            publish_strategy_view(td, live, instance_id="scalp", symbol="BTC-USDT", closed_only=True, legacy=False)
            publish_strategy_view(td, other, instance_id="swing", symbol="BTC-USDT", closed_only=False, legacy=False)
            self.assertNotEqual(stream_snapshot_path(td, one), stream_snapshot_path(td, five))
            self.assertEqual(len(AtomicJsonStore(stream_snapshot_path(td, one)).read()["candles"]), 3)
            self.assertEqual(len(AtomicJsonStore(instance_prices_path(td, "scalp", "BTC-USDT")).read()), 2)
            self.assertEqual(len(AtomicJsonStore(instance_prices_path(td, "swing", "BTC-USDT")).read()), 2)
            with self.assertRaises(ValueError): instance_prices_path(td, "../escape", "BTCUSDT")

    def test_strategy_reader_is_one_envelope_with_source_freshness_and_limit_checks(self):
        with tempfile.TemporaryDirectory() as td:
            key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
            store = StreamStore(settings={"min_history": 1}, clock=self.clock)
            snapshot = store.merge(key, [raw(T), raw(T + 60_000), raw(T + 120_000)], status="rest")
            publish_strategy_view(td, snapshot, instance_id="first", closed_only=True, legacy=False)
            value = read_strategy_snapshot(td, "first", "BTCUSDT", stream_key=key, limit=2)
            self.assertEqual(len(value["candles"]), 2)
            self.assertTrue(all(row["is_closed"] for row in value["candles"]))
            self.assertIsNone(read_strategy_snapshot(td, "first", "BTCUSDT", limit=3))
            with self.assertRaises(ValueError):
                read_strategy_snapshot(td, "first", "BTCUSDT", stream_key=StreamKey("mexc", "spot", "BTCUSDT", "1m"))
            AtomicJsonStore(instance_prices_path(td, "first", "BTCUSDT")).write([])
            self.assertEqual(len(read_strategy_snapshot(td, "first", "BTCUSDT", limit=2)["candles"]), 2)
            self.clock.now += 31
            self.assertIsNone(read_strategy_snapshot(td, "first", "BTCUSDT", limit=2))
            self.assertTrue(read_strategy_snapshot(td, "first", "BTCUSDT", allow_stale=True)["stale"])

    def test_late_file_writer_cannot_restore_ready_after_error(self):
        with tempfile.TemporaryDirectory() as td:
            key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
            store = StreamStore(settings={"min_history": 1}, clock=self.clock)
            live = store.merge(key, [raw(T + 120_000)], status="rest")
            failed = store.mark(key, status="error", error="Ошибка подписки")
            publish_market_snapshot(td, failed)
            publish_market_snapshot(td, live)
            saved = AtomicJsonStore(stream_snapshot_path(td, key)).read()
            self.assertEqual(saved["version"], failed["version"])
            self.assertFalse(saved["ready"])

    def test_empty_recovering_snapshot_can_be_published(self):
        with tempfile.TemporaryDirectory() as td:
            key = StreamKey("mexc", "spot", "BTCUSDT", "1m")
            store = StreamStore(); store.ensure(key)
            publish_market_snapshot(td, store.snapshot(key)); publish_market_snapshot(td, store.mark(key))
            self.assertFalse(read_market_snapshot(td, key, allow_stale=True)["ready"])


class PublicMarketRESTTests(unittest.TestCase):
    def response(self, payload, status=200, headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.json.return_value = payload
        if status >= 400: response.raise_for_status.side_effect = requests.HTTPError("HTTP", response=response)
        return response

    def test_public_mexc_spot_params_and_limiter_no_auth(self):
        limiter = Mock(); request = Mock(return_value=self.response([[T, "10", "12", "9", "11", "2"]]))
        fetch = PublicRESTFetcher(request=request, rate_limiters={"mexc": limiter})
        key = StreamKey("mexc", "spot", "BTC/USDT", "1h")
        rows = fetch(key, start_ms=T, end_ms=T + 3_600_000, limit=1)
        self.assertEqual(rows[0]["timestamp"], T)
        args, kwargs = request.call_args
        self.assertEqual(args[0], "https://api.mexc.com/api/v3/klines")
        self.assertEqual(kwargs["params"]["interval"], "60m")
        self.assertNotIn("headers", kwargs); self.assertNotIn("signature", kwargs["params"])
        limiter.acquire.assert_called_once_with(weight=1)

    def test_429_shared_cooldown_then_retry(self):
        limiter = Mock(); sleeper = Mock()
        request = Mock(side_effect=[self.response({}, 429, {"Retry-After": "2"}), self.response({"code": 0, "data": [raw(T)]})])
        rows = PublicRESTFetcher(request=request, sleep=sleeper, rate_limiters={"bingx": limiter})(
            StreamKey("bingx", "perpetual", "BTCUSDT", "1m"), start_ms=T, end_ms=T + 60_000, limit=1)
        self.assertEqual(len(rows), 1); limiter.cooldown.assert_called_once_with(2)
        self.assertEqual(limiter.acquire.call_count, 2)

    def test_small_limit_does_not_skip_middle_of_explicit_range(self):
        calls = []
        def request(url, params, timeout):
            calls.append(params)
            self.assertEqual(params["limit"], 2)
            start = params["startTime"]
            return self.response([[start, "10", "12", "9", "11", "2"],
                                  [start + 60_000, "10", "12", "9", "11", "2"]])
        rows = PublicRESTFetcher(request=request, rate_limiters={"mexc": Mock()})(
            StreamKey("mexc", "spot", "BTCUSDT", "1m"), start_ms=T, end_ms=T + 6 * 60_000, limit=2)
        self.assertEqual(len(calls), 3)
        self.assertEqual([row["timestamp"] for row in rows], [T + 240_000, T + 300_000])

    def test_forward_range_pagination_and_reverse_response(self):
        limiter = Mock(); calls = []
        def request(url, params, timeout):
            calls.append(params)
            start = params["startTime"]
            return self.response([[start + 60_000, "10", "12", "9", "11", "2"], [start, "10", "12", "9", "11", "2"]])
        rows = PublicRESTFetcher(request=request, rate_limiters={"mexc": limiter})(
            StreamKey("mexc", "spot", "BTCUSDT", "1m"), start_ms=T, end_ms=T + 600 * 60_000, limit=600)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["startTime"], T + 500 * 60_000)
        self.assertEqual([row["timestamp"] for row in rows], sorted(row["timestamp"] for row in rows))

    def test_futures_parallel_arrays_must_match(self):
        request = Mock(return_value=self.response({"success": True, "data": {"time": [T // 1000], "open": [10], "high": [12], "low": [9], "close": [], "vol": [2]}}))
        with self.assertRaises(ValueError):
            PublicRESTFetcher(request=request, rate_limiters={"mexc": Mock()})(
                StreamKey("mexc", "perpetual", "BTCUSDT", "1m"), start_ms=T, end_ms=T + 60_000, limit=1)

    def test_spot_protobuf_capability_is_exact_not_rest_disguised_as_ws(self):
        capability = factory.provider_capabilities("mexc", "spot", "1m")
        self.assertTrue(capability["ws_supported"])
        self.assertEqual(capability["transport"], "ws")
        self.assertTrue(capability["rest_supported"])
        self.assertFalse(factory.provider_capabilities("mexc", "spot", "3m")["ws_supported"])
        self.assertFalse(PublicRESTFetcher.supports(StreamKey("bingx", "spot", "BTCUSDT", "1m")))


if __name__ == "__main__":
    unittest.main()
