"""Offline WS lifecycle: fake sockets, поколения, ACK, backoff и range recovery."""

import gzip
import json
import threading
import unittest
from unittest.mock import Mock, patch

from src.exchanges.bingx_ws_data_provider import BingXWebSocketDataProvider
from src.exchanges.mexc_futures_ws_data_provider import MEXCFuturesWebSocketDataProvider
from src.exchanges.mexc_spot_ws_data_provider import MEXCSpotWebSocketDataProvider
from src.exchanges.market_data import StreamKey, StreamStore, interval_start
from src.exchanges.ws.runtime import ManagedMarketDataProvider, RESTPollingProvider
from src.exchanges import ws_provider_factory as factory
from test_market_data_layer import Clock, T, raw


class FakeSocket:
    def __init__(self, *args, **callbacks):
        self.sent = []
        self.callbacks = callbacks
        self.closed = threading.Event()
    def send(self, message, **kwargs):
        self.sent.append(json.loads(message) if message.startswith("{") else message)
    def close(self):
        self.closed.set()
    def run_forever(self):
        self.callbacks["on_open"](self)
        self.closed.wait(3)
        self.callbacks["on_close"](self, 1000, "offline")


class WSRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(T / 1000 + 135)
        self.settings = {"history_limit": 3, "min_history": 2, "max_age_seconds": 30,
                         "reconcile_interval_seconds": 10, "heartbeat_interval_seconds": 5,
                         "heartbeat_timeout_seconds": 15, "subscription_timeout_seconds": 10}
        self.key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
        self.rows = [raw(T), raw(T + 60_000), raw(T + 120_000)]
        self.rest = Mock(return_value=self.rows)
        self.store = StreamStore(settings=self.settings, clock=self.clock)
        self.provider = BingXWebSocketDataProvider(store=self.store, rest_fetcher=self.rest,
                                                   publisher=Mock(), clock=self.clock, monotonic=self.clock)
        self.provider.sync_subscriptions([self.key])
        self.socket = FakeSocket()
        self.provider._ws = self.socket

    def tearDown(self):
        self.provider.stop()

    def connect(self):
        self.provider._on_open(self.socket)
        self.provider.reconcile_once(self.key)

    def update(self, ts, close=11):
        row = raw(ts, close)
        self.provider._on_message(self.socket, json.dumps({"dataType": "BTC-USDT@kline_1min", "data": row}))

    def test_callback_never_fetches_rest(self):
        self.connect(); count = self.rest.call_count
        self.update(T + 120_000, 11.5)
        self.assertEqual(self.rest.call_count, count)
        self.assertEqual(self.store.snapshot(self.key)["candles"][-1]["closePrice"], 11.5)
        self.assertTrue(self.store.snapshot(self.key)["subscribed"])

    def test_reconnect_requests_range_backfill_not_single_candle(self):
        self.connect()
        self.clock.now = T / 1000 + 315
        self.provider._connection_lost()
        self.provider._on_open(self.socket)
        self.update(T + 300_000)
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        self.rest.return_value = [raw(T + i * 60_000) for i in range(6)]
        self.provider.reconcile_once(self.key)
        params = self.rest.call_args.kwargs
        self.assertLessEqual(params["start_ms"], T + 180_000)
        value = self.store.snapshot(self.key)
        self.assertFalse(value["gaps"])
        self.assertTrue(value["ready"])
        self.assertEqual(len(value["candles"]), 3)

    def test_failed_backfill_recovers_later(self):
        self.rest.side_effect = RuntimeError("offline REST")
        self.provider.reconcile_once(self.key)
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        self.assertEqual(self.store.snapshot(self.key)["status"], "error")
        self.rest.side_effect = None
        self.provider.reconcile_once(self.key)
        self.assertTrue(self.store.snapshot(self.key)["ready"])

    def test_rest_inflight_unsubscribe_cannot_resurrect_stream(self):
        def fetch(*args, **kwargs):
            self.provider.sync_subscriptions([])
            return self.rows
        self.rest.side_effect = fetch
        self.provider.reconcile_once(self.key)
        self.assertNotIn(self.key.id, self.store.cache)

    def test_multi_tf_subscription_sync_idempotent_and_unsubscribe(self):
        self.connect()
        five = StreamKey("bingx", "perpetual", "BTCUSDT", "5m")
        self.provider.sync_subscriptions([self.key, five])
        subscriptions = [msg for msg in self.socket.sent if isinstance(msg, dict) and msg.get("reqType") == "sub"]
        self.assertEqual(len(subscriptions), 2)
        self.assertEqual({msg["dataType"] for msg in subscriptions}, {"BTC-USDT@kline_1min", "BTC-USDT@kline_5min"})
        self.provider.sync_subscriptions([self.key, five]); self.assertEqual(len(self.socket.sent), 2)
        self.provider.sync_subscriptions([five])
        self.assertEqual(self.socket.sent[-1]["reqType"], "unsub")
        self.assertNotIn(self.key.id, self.store.cache)

    def test_incremental_subscribe_derives_single_tf_but_rejects_ambiguous_default(self):
        self.connect()
        self.provider.subscribe(["ETHUSDT"])
        self.assertIn(StreamKey("bingx", "perpetual", "ETHUSDT", "1m"), self.provider.streams)
        five = StreamKey("bingx", "perpetual", "BTCUSDT", "5m")
        self.provider.sync_subscriptions([*self.provider.streams, five])
        with self.assertRaises(ValueError): self.provider.subscribe(["SOLUSDT"])

    def test_gzip_multi_candle_packet_all_rows_reduced(self):
        self.connect()
        packet = gzip.compress(json.dumps({"dataType": "BTC-USDT@kline_1min", "data": [raw(T), raw(T + 120_000, 11.6)]}).encode())
        self.provider._on_message(self.socket, packet)
        self.assertEqual(self.store.snapshot(self.key)["candles"][-1]["closePrice"], 11.6)
        self.assertEqual(len(self.store.snapshot(self.key)["candles"]), 3)

    def test_malformed_candle_invalidates_ready_no_local_time_fabrication(self):
        self.connect()
        self.provider._on_message(self.socket, json.dumps({"dataType": "BTC-USDT@kline_1min", "data": {"c": 11}}))
        snapshot = self.store.snapshot(self.key)
        self.assertFalse(snapshot["ready"])
        self.assertEqual(len(snapshot["candles"]), 3)

    def test_subscription_ack_and_ack_timeout(self):
        self.provider._on_open(self.socket)
        request_id = self.socket.sent[0]["id"]
        self.provider._on_message(self.socket, json.dumps({"id": request_id, "code": 0}))
        self.assertIn(self.key, self.provider._acked)
        self.clock.now += 11
        self.provider._last_transport_message = self.clock()
        self.provider.watchdog_once()
        self.assertFalse(self.socket.closed.is_set())
        self.provider._on_open(self.socket)
        self.clock.now += 11
        self.provider._last_transport_message = self.clock()
        self.provider.watchdog_once()
        self.assertTrue(self.socket.closed.is_set())

    def test_pong_does_not_refresh_candle_freshness(self):
        self.connect(); self.update(T + 120_000)
        self.clock.now += 31
        self.provider._on_message(self.socket, '{"pong":1}')
        self.provider.watchdog_once()
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        self.assertFalse(self.store.ready[self.key.id])

    def test_heartbeat_timeout_and_old_generation_is_ignored(self):
        self.connect(); self.update(T + 120_000)
        self.clock.now += 6
        self.provider.watchdog_once()
        self.assertIsNotNone(self.provider._pending_ping)
        old = self.socket; new = FakeSocket(); self.provider._ws = new
        self.provider._on_open(new)
        self.provider._on_message(old, '{"pong":1}')
        self.clock.now += 6; self.provider.watchdog_once()
        self.assertIsNotNone(self.provider._pending_ping)
        self.clock.now += 16
        self.provider._last_transport_message = self.clock()
        self.provider._ack_deadlines.clear()
        self.provider.watchdog_once()
        self.assertTrue(new.closed.is_set())

    def test_server_ping_is_not_ack_for_pending_client_heartbeat(self):
        self.connect()
        self.provider._pending_ping = self.clock()
        self.provider._on_message(self.socket, "Ping")
        self.assertIsNotNone(self.provider._pending_ping)
        self.assertEqual(self.socket.sent[-1], "Pong")
        self.provider._on_pong(FakeSocket(), b"1")
        self.assertIsNotNone(self.provider._pending_ping)
        self.provider._on_pong(self.socket, b"1")
        self.assertIsNone(self.provider._pending_ping)

    def test_unsubscribe_publishes_stopped_and_readd_has_higher_revision(self):
        self.connect()
        live = self.store.snapshot(self.key)
        self.provider.sync_subscriptions([])
        self.provider._flush_publish()
        stopped = self.provider.publisher.call_args.args[0]
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(stopped["ready"])
        self.provider.sync_subscriptions([self.key])
        self.assertGreater(self.store.snapshot(self.key)["version"], stopped["version"])
        self.assertGreater(stopped["version"], live["version"])

    def test_stop_invalidates_and_cannot_restore_ready(self):
        self.connect()
        self.provider.stop()
        self.provider._connection_lost()
        self.assertEqual(self.store.snapshot(self.key)["status"], "stopped")
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        self.provider.reconcile_once(self.key)
        self.assertEqual(self.store.snapshot(self.key)["status"], "stopped")

    def test_period_rollover_queues_previous_finalization_without_rest_callback(self):
        self.connect()
        self.provider._pending.clear()
        self.clock.now = T / 1000 + 195
        count = self.rest.call_count
        self.update(T + 180_000)
        self.assertEqual(self.rest.call_count, count)
        self.assertEqual(self.provider._pending[self.key][0], T + 120_000)

    def test_provider_does_not_silently_shorten_requested_history(self):
        self.connect()
        self.assertEqual(self.provider.get_klines("BTCUSDT", 4, "1m"), [])
        self.assertEqual(len(self.provider.get_klines("BTCUSDT", 2, "1m", closed_only=True)), 2)

    def test_owner_pid_blocks_lifecycle_in_worker(self):
        with patch("src.exchanges.ws.runtime.os.getpid", return_value=-1):
            with self.assertRaises(RuntimeError): self.provider.sync_subscriptions([])
            with self.assertRaises(RuntimeError): self.provider.stop()

    def test_restart_requeues_stopped_stream_and_rejects_surviving_threads(self):
        self.connect()
        self.provider.stop()
        self.provider._threads = [Mock(is_alive=Mock(return_value=True))]
        with self.assertRaises(RuntimeError): self.provider.start(streams=[self.key])
        self.provider._threads = []
        with patch("src.exchanges.ws.runtime.threading.Thread") as thread:
            thread.return_value.is_alive.return_value = False
            self.provider.start(streams=[self.key])
            self.assertEqual(self.store.snapshot(self.key)["status"], "recovering")
            self.assertIn(self.key, self.provider._pending)
            self.provider.reconcile_once(self.key)
            self.assertTrue(self.store.snapshot(self.key)["ready"])
            self.provider.stop()

    def test_normal_close_backoff_always_applies(self):
        provider = self.provider
        calls, delays = [], []
        class Event:
            stopped = False
            def is_set(self): return self.stopped
            def wait(self, delay):
                delays.append(delay)
                if len(delays) == 3: self.stopped = True
                return self.stopped
        provider._stop_event = Event()
        def socket_factory(*args, **kwargs):
            calls.append(1)
            return Mock(run_forever=lambda: None)
        provider.ws_factory = socket_factory
        provider._network_loop()
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(delays), 3)
        self.assertGreater(delays[1], delays[0])
        provider._stop_event = threading.Event()


class MEXCWSRuntimeTests(unittest.TestCase):
    def test_rest_ws_merge_and_multi_tf_ambiguity(self):
        clock = Clock(T / 1000 + 135)
        store = StreamStore(settings={"min_history": 1}, clock=clock)
        key = StreamKey("mexc", "perpetual", "BTC_USDT", "1m")
        provider = MEXCFuturesWebSocketDataProvider(store=store, clock=clock, monotonic=clock,
                    rest_fetcher=Mock(return_value=[raw(T), raw(T + 60_000), raw(T + 120_000)]), publisher=Mock())
        provider.sync_subscriptions([key]); ws = FakeSocket(); provider._ws = ws
        provider._on_open(ws); provider.reconcile_once(key)
        packet = {"channel": "push.kline", "data": {"symbol": "BTC_USDT", "interval": "Min1", "t": (T + 120_000) // 1000,
                   "o": "10", "h": "12", "l": "9", "c": "11.5", "q": "3"}}
        provider._on_message(ws, json.dumps(packet))
        value = store.snapshot(key)
        self.assertEqual(len(value["candles"]), 3)
        self.assertEqual(value["candles"][-1]["volume"], 3)
        self.assertEqual(value["candles"][-1]["volume_unit"], "contracts")
        five = StreamKey("mexc", "perpetual", "BTCUSDT", "5m")
        with self.assertRaises(ValueError): provider.sync_subscriptions([key, five])
        self.assertEqual(provider.streams, [key])
        packet["data"]["interval"] = "Min5"
        before = store.snapshot(key)["candles"]
        provider._on_message(ws, json.dumps(packet))
        self.assertEqual(store.snapshot(key)["candles"], before)
        self.assertFalse(ws.sent[0]["gzip"])
        self.assertEqual(ws.sent[0]["param"]["symbol"], "BTC_USDT")
        provider.stop()

    def test_rest_polling_provider_never_creates_socket(self):
        clock = Clock(); socket_factory = Mock(side_effect=AssertionError("Явный RESTPollingProvider не должен создавать WS"))
        provider = RESTPollingProvider("mexc", "spot", settings={"min_history": 1}, clock=clock, monotonic=clock,
                    rest_fetcher=Mock(return_value=[raw(T), raw(T + 60_000), raw(T + 120_000)]), ws_factory=socket_factory, publisher=Mock())
        key = StreamKey("mexc", "spot", "BTCUSDT", "1m")
        provider.sync_subscriptions([key]); provider.reconcile_once(key)
        self.assertTrue(provider.store.snapshot(key)["ready"])
        self.assertEqual(provider.store.snapshot(key)["transport"], "rest")
        socket_factory.assert_not_called(); provider.stop()


class FactoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.cache, self.ready = {}, {}
        self.publisher = Mock()
        self.selection = patch.object(factory, "trading_selection", return_value=("bingx", "perpetual", {}))
        self.selection.start(); self.addCleanup(self.selection.stop)
        def start_offline(provider, symbols=None, interval="5m", *, streams=None):
            provider.sync_subscriptions(streams or [])
        self.start_patch = patch.object(ManagedMarketDataProvider, "start", start_offline)
        self.start_patch.start(); self.addCleanup(self.start_patch.stop)
        self.addCleanup(factory.stop_ws_provider)

    def start(self, keys, **kwargs):
        return factory.start_ws_provider(streams=keys, cache=self.cache, ready=self.ready,
                                         publisher=self.publisher, **kwargs)

    def test_multiple_markets_and_mexc_tf_have_independent_owners(self):
        keys = [StreamKey("bingx", "perpetual", "BTCUSDT", "1m"),
                StreamKey("bingx", "perpetual", "BTCUSDT", "5m"),
                StreamKey("mexc", "perpetual", "BTCUSDT", "1m"),
                StreamKey("mexc", "perpetual", "BTCUSDT", "5m"),
                StreamKey("mexc", "spot", "BTCUSDT", "1m")]
        cache, ready = self.start(keys)
        self.assertIs(cache, self.cache); self.assertIs(ready, self.ready)
        self.assertEqual(set(cache), {key.id for key in keys})
        one = factory.get_ws_provider("mexc", "perpetual", interval="1m")
        five = factory.get_ws_provider("mexc", "perpetual", interval="5m")
        self.assertIsNot(one, five)
        self.assertIsNone(factory.get_ws_provider("mexc", "perpetual"))
        self.assertEqual(len(factory.get_ws_provider("bingx", "perpetual").streams), 2)
        self.assertIsInstance(factory.get_ws_provider("mexc", "spot"), MEXCSpotWebSocketDataProvider)
        self.assertEqual(factory.get_ws_provider("mexc", "spot").WS_URL, "wss://wbs-api.mexc.com/ws")
        factory.sync_subscriptions([keys[3]])
        self.assertEqual(set(cache), {keys[3].id})
        self.assertIs(factory.get_ws_provider("mexc", "perpetual", interval="5m"), five)
        self.assertTrue(one._stop_event.is_set())
        self.assertTrue(factory.request_reconcile(keys[3]))
        self.assertFalse(factory.request_reconcile(keys[2]))

    def test_invalid_desired_set_does_not_remove_valid_streams(self):
        key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
        self.start([key])
        provider = factory.get_ws_provider("bingx", "perpetual")
        with self.assertRaises(ValueError):
            factory.sync_subscriptions([StreamKey("mexc", "perpetual", "BTCUSDT", "3m")])
        self.assertIs(factory.get_ws_provider("bingx", "perpetual"), provider)
        self.assertEqual(provider.streams, [key])
        self.assertEqual(set(self.cache), {key.id})

    def test_hot_settings_preserve_proxies_and_update_shared_public_limiter(self):
        key = StreamKey("bingx", "perpetual", "BTCUSDT", "1m")
        first_limiter, second_limiter = Mock(), Mock()
        self.start([key], rate_limiters={"bingx": first_limiter})
        provider = factory.get_ws_provider("bingx", "perpetual")
        self.assertIs(provider.rest_fetcher.rate_limiters["bingx"], first_limiter)
        factory.configure_market_data(settings={"max_age_seconds": 9, "history_limit": 50},
                                      rate_limiters={"bingx": second_limiter})
        self.assertIs(factory.get_shared_cache()[0], self.cache)
        self.assertEqual(provider.settings["max_age_seconds"], 9)
        self.assertEqual(provider.store.snapshot(key)["max_age_seconds"], 9)
        self.assertIs(provider.rest_fetcher.rate_limiters["bingx"], second_limiter)
        self.assertEqual(provider.streams, [key])

    def test_worker_cannot_change_lifecycle(self):
        self.start([StreamKey("bingx", "perpetual", "BTCUSDT", "1m")])
        with patch.object(factory.os, "getpid", return_value=-1):
            with self.assertRaises(RuntimeError): factory.sync_subscriptions([])
            with self.assertRaises(RuntimeError): factory.configure_market_data(settings={"max_age_seconds": 1})
            with self.assertRaises(RuntimeError): factory.stop_ws_provider()

    def test_empty_sync_then_readd_keeps_manager_and_monotonic_stream_revision(self):
        key = StreamKey("mexc", "perpetual", "BTCUSDT", "1m")
        self.start([key])
        previous = self.cache[key.id]["version"]
        factory.sync_subscriptions([])
        self.assertEqual(self.cache, {})
        factory.sync_subscriptions([key])
        self.assertGreater(self.cache[key.id]["version"], previous)
        self.assertIs(factory.get_shared_cache()[0], self.cache)


if __name__ == "__main__":
    unittest.main()
