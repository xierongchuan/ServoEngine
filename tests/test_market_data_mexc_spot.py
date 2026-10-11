"""Offline MEXC Spot: официальный protobuf layout, ACK, heartbeat, TF и backfill."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.exchanges.market_data import StreamKey, StreamStore, interval_start, next_start, normalize_candle
from src.exchanges.mexc_spot_ws_data_provider import MEXCSpotWebSocketDataProvider
from src.exchanges.public_market_rest import PublicRESTFetcher
from src.exchanges.ws.mexc_spot_proto import channel_for, decode_spot_kline, MEXC_SPOT_WS_INTERVALS, MAX_MESSAGE_BYTES
from src.exchanges.ws.runtime import ManagedMarketDataProvider, RESTPollingProvider
from src.exchanges import ws_provider_factory as factory
from test_market_data_layer import Clock, T, raw
from test_market_data_ws_runtime import FakeSocket


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, value):
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return varint((number << 3) | 2) + varint(len(value)) + value
    return varint(number << 3) + varint(value)


def packet(key, start, *, event=None, close="11", volume="2", symbol=None, body_interval=None, window_end=None):
    # Независимый test encoder использует именно номера официальных .proto, не decoder constants.
    body = b"".join(field(n, v) for n, v in (
        (1, body_interval or MEXC_SPOT_WS_INTERVALS[key.interval]), (2, start // 1000),
        (3, "10"), (4, close), (5, "12"), (6, "9"), (7, volume), (8, "999999"),
        (9, (window_end or next_start(start, key.interval)) // 1000)))
    return (field(1, channel_for(key)) + field(3, symbol or key.symbol)
            + field(5, event or start + 1000) + field(308, body))


class SpotProtobufTests(unittest.TestCase):
    def test_official_documentation_example_fields_and_units(self):
        # https://mexcdevelop.github.io/apidocs/spot_v3_en/#k-line-streams
        body = b"".join(field(n, v) for n, v in (
            (1, "Min15"), (2, 1736410500), (3, "92925"), (4, "93158.47"),
            (5, "93158.47"), (6, "92800"), (7, "36.83803224"), (8, "3424811.05"), (9, 1736411400)))
        wire = (field(1, "spot@public.kline.v3.api.pb@BTCUSDT@Min15") + field(308, body)
                + field(3, "BTCUSDT") + field(4, "2fb942154ef44a4ab2ef98c8afb6a4a7") + field(5, 1736410707571))
        key, row = decode_spot_kline(wire)
        candle = normalize_candle(row, key, source="ws", now=1736410708)
        self.assertEqual(key, StreamKey("mexc", "spot", "BTCUSDT", "15m"))
        self.assertEqual(candle["timestamp"], 1736410500000)
        self.assertEqual(candle["event_time_ms"], 1736410707571)
        self.assertEqual(candle["volume"], 36.83803224)
        self.assertEqual(candle["closePrice"], 93158.47)
        self.assertEqual(candle["volume_unit"], "base")
        self.assertFalse(candle["is_closed"])

    def test_captured_public_wire_frame_uses_official_field_308_layout(self):
        # Публичный wss://wbs-api.mexc.com/ws: ACK + бинарная свеча, без credentials.
        wire = bytes.fromhex(
            "0a2873706f74407075626c69632e6b6c696e652e76332e6170692e70624042544355534454404d696e31"
            "1a074254435553445422203266623934323135346566343461346162326566393863386166623661346137"
            "28d7afbbc09234a213500a044d696e3110a488abd6061a0838333039362e3332220838333039362e3333"
            "2a0838333039362e3333320838333039362e33323a0a302e3133343534333433420831313137392e353848e088abd606")
        key, row = decode_spot_kline(wire)
        candle = normalize_candle(row, key, source="ws", now=1791673428)
        self.assertEqual(key, StreamKey("mexc", "spot", "BTCUSDT", "1m"))
        self.assertEqual(candle["timestamp"], 1791673380000)
        self.assertEqual(candle["openPrice"], 83096.32)
        self.assertEqual(candle["closePrice"], 83096.33)
        self.assertEqual(candle["volume"], 0.13454343)
        self.assertFalse(candle["is_closed"])

    def test_all_documented_intervals_roundtrip_and_optional_symbol(self):
        for interval in MEXC_SPOT_WS_INTERVALS:
            with self.subTest(interval=interval):
                key = StreamKey("mexc", "spot", "BTCUSDT", interval)
                start = interval_start(T, interval)
                encoded = packet(key, start, event=T + 1000)
                self.assertEqual(decode_spot_kline(encoded)[0], key)
                no_symbol = field(1, channel_for(key)) + field(6, T + 1000) + encoded[encoded.index(varint((308 << 3) | 2)):]
                self.assertEqual(decode_spot_kline(no_symbol)[1]["event_time_ms"], T + 1000)

    def test_unknown_fields_are_bounded_and_non_kline_body_is_not_interpreted(self):
        key = StreamKey("mexc", "spot", "BTCUSDT", "1m")
        encoded = packet(key, T) + field(100, "unknown") + varint((101 << 3) | 1) + b"\0" * 8
        self.assertEqual(decode_spot_kline(encoded)[0], key)
        deals = field(1, "spot@public.aggre.deals.v3.api.pb@100ms@BTCUSDT") + field(301, b"\xff")
        self.assertIsNone(decode_spot_kline(deals))

    def test_malformed_wire_missing_clock_and_oneof_fail_closed(self):
        key = StreamKey("mexc", "spot", "BTCUSDT", "1m")
        encoded = packet(key, T)
        malformed = [b"", b"\x00", b"\x80", b"\x0a\xff\xff", b"\x0b", b"\x08" + b"\xff" * 10,
                     b"x" * (MAX_MESSAGE_BYTES + 1), encoded[:-1], encoded + field(1, channel_for(key)),
                     encoded + field(301, b""), field(1, channel_for(key)),
                     field(1, channel_for(key)) + field(308, b"x" * 4097)]
        for value in malformed:
            with self.subTest(wire=value[:20]), self.assertRaises(ValueError):
                decode_spot_kline(value)
        # Нельзя подставлять время callback вместо отсутствующего createTime/sendTime.
        body = encoded[encoded.index(varint((308 << 3) | 2)):]
        with self.assertRaises(ValueError): decode_spot_kline(field(1, channel_for(key)) + body)

    def test_source_interval_and_window_mismatch_are_rejected(self):
        key = StreamKey("mexc", "spot", "BTCUSDT", "1m")
        for kwargs in ({"symbol": "ETHUSDT"}, {"body_interval": "Min5"}, {"window_end": T + 300_000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                decode_spot_kline(packet(key, T, **kwargs))
        with self.assertRaises(ValueError): channel_for(StreamKey("mexc", "spot", "BTCUSDT", "3m"))


class SpotRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.key = StreamKey("mexc", "spot", "BTCUSDT", "1m")
        self.settings = {"history_limit": 3, "min_history": 2, "max_age_seconds": 30,
                         "heartbeat_interval_seconds": 5, "heartbeat_timeout_seconds": 15,
                         "subscription_timeout_seconds": 10}
        self.store = StreamStore(settings=self.settings, clock=self.clock)
        self.rest = Mock(return_value=[raw(T), raw(T + 60_000), raw(T + 120_000)])
        self.provider = MEXCSpotWebSocketDataProvider(store=self.store, rest_fetcher=self.rest,
                         clock=self.clock, monotonic=self.clock, publisher=Mock())
        self.provider.sync_subscriptions([self.key])
        self.ws = FakeSocket(); self.provider._ws = self.ws; self.provider._on_open(self.ws)
        self.provider.reconcile_once(self.key)
        self.addCleanup(self.provider.stop)

    def ack(self, request=None, **changes):
        request = self.ws.sent[0] if request is None else request
        self.provider._on_message(self.ws, json.dumps({"id": request["id"], "code": 0,
            "msg": request["params"][0], **changes}))

    def update(self, **kwargs):
        self.provider._on_message(self.ws, packet(self.key, T + 120_000,
                                  event=int(self.clock() * 1000), **kwargs))

    def test_subscription_ack_is_exact_unsigned_and_not_candle_freshness(self):
        request = self.ws.sent[0]
        self.assertEqual(request["method"], "SUBSCRIPTION")
        self.assertEqual(request["params"], ["spot@public.kline.v3.api.pb@BTCUSDT@Min1"])
        self.assertIsInstance(request["id"], int)
        before = self.store.snapshot(self.key)["last_valid_update"]
        self.ack(msg="spot@public.kline.v3.api.pb@ETHUSDT@Min1")
        self.assertFalse(self.store.snapshot(self.key)["subscribed"])
        for code in (False, 0.0, "0", None):
            with self.subTest(code=code):
                self.ack(code=code)
                self.assertFalse(self.store.snapshot(self.key)["subscribed"])
        self.ack()
        self.assertTrue(self.store.snapshot(self.key)["subscribed"])
        self.assertEqual(self.store.snapshot(self.key)["last_valid_update"], before)

    def test_binary_ws_merges_with_rest_without_callback_network_or_quote_volume(self):
        before_calls = self.rest.call_count
        self.update(close="11.5", volume="3")
        value = self.store.snapshot(self.key)
        self.assertEqual(self.rest.call_count, before_calls)
        self.assertTrue(value["ready"])
        self.assertEqual(value["transport"], "ws")
        self.assertEqual(len(value["candles"]), 3)
        self.assertEqual(value["candles"][-1]["source"], "ws")
        self.assertEqual(value["candles"][-1]["volume"], 3)
        self.assertFalse(value["candles"][-1]["is_closed"])
        self.assertEqual(value["publisher_id"], self.store.publisher_id)

    def test_spot_ws_publication_drives_native_chart_and_closed_instance_view(self):
        from src.core import plotter
        from src.exchanges.market_data_storage import publish_market_snapshot, publish_strategy_view, read_strategy_snapshot
        self.update(close="11.5")
        snapshot = self.store.snapshot(self.key)
        with tempfile.TemporaryDirectory() as directory, patch("src.exchanges.market_data.time.time", self.clock):
            self.provider.publisher = lambda value: publish_market_snapshot(directory, value)
            self.provider._flush_publish()
            publish_strategy_view(directory, snapshot, instance_id="spot", symbol="BTCUSDT", closed_only=True, legacy=False)
            closed = read_strategy_snapshot(directory, "spot", "BTCUSDT", stream_key=self.key, limit=2)
            self.assertEqual(len(closed["candles"]), 2)
            self.assertEqual(closed["publisher_id"], snapshot["publisher_id"])
            config = {"CHART_SETTINGS": {"dpi": 35, "chart_height": 6, "sma_periods": []},
                      "PLOTTER_RANGES": {"TEST": {"minutes": 10}}}
            with patch.object(plotter, "resample_data", side_effect=AssertionError("TF графика нельзя подменять")):
                path = plotter.plot_symbol("BTCUSDT", "TEST", config=config, data_dir=directory,
                                          charts_dir=str(Path(directory) / "charts"), stream_key=self.key)
            self.assertEqual(Path(path).name, "mexc_spot_BTCUSDT_1m.png")
            self.assertEqual(Path(path).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertFalse((Path(directory) / "prices" / "BTCUSDT.json").exists())

    def test_multiple_tf_exact_unsubscribe_and_late_ack_do_not_resurrect(self):
        five = StreamKey("mexc", "spot", "BTCUSDT", "5m")
        self.provider.sync_subscriptions([self.key, five])
        five_request = self.ws.sent[-1]
        old_request = self.ws.sent[0]
        self.provider.sync_subscriptions([five])
        self.assertEqual(self.ws.sent[-1]["method"], "UNSUBSCRIPTION")
        self.assertEqual(self.ws.sent[-1]["params"], [channel_for(self.key)])
        self.assertEqual(self.provider.streams, [five])
        self.ack(old_request); self.update()
        self.assertNotIn(self.key.id, self.store.cache)
        self.ack(five_request)
        self.assertTrue(self.store.snapshot(five)["subscribed"])
        self.provider.sync_subscriptions([five, self.key])
        self.ack(old_request)
        self.assertFalse(self.store.snapshot(self.key)["subscribed"])
        self.assertGreater(self.ws.sent[-1]["id"], old_request["id"])

    def test_invalid_ohlcv_or_wire_revokes_ready(self):
        self.update(close="nan")
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        self.assertIn(self.key, self.provider._pending)
        self.provider.reconcile_once(self.key)
        self.provider._on_message(self.ws, b"\x80")
        self.assertFalse(self.store.snapshot(self.key)["ready"])

    def test_replayed_event_does_not_refresh_and_late_closed_does_not_regress(self):
        self.update()
        value = self.store.snapshot(self.key)
        replay = packet(self.key, T + 120_000, event=int(self.clock() * 1000), close="11.1")
        self.clock.now += 31
        self.provider._on_message(self.ws, replay)
        self.assertEqual(self.store.snapshot(self.key)["last_valid_update"], value["last_valid_update"])
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        closed = self.store.snapshot(self.key)["candles"][0]
        self.provider._on_message(self.ws, packet(self.key, T, event=int(self.clock() * 1000), close="11.9"))
        self.assertEqual(self.store.snapshot(self.key)["candles"][0], closed)

    def test_heartbeat_requires_matching_pong_not_server_ping_or_data(self):
        self.ack()
        before = self.store.snapshot(self.key)["last_valid_update"]
        self.clock.now += 5; self.provider.watchdog_once()
        ping = self.ws.sent[-1]
        self.assertEqual(ping, {"method": "PING"})
        self.provider._on_pong(self.ws, b"")
        self.assertIsNotNone(self.provider._pending_ping)
        self.provider._on_message(self.ws, json.dumps({"method": "PING", "id": 999}))
        self.assertEqual(self.ws.sent[-1]["method"], "PONG")
        self.assertIsNotNone(self.provider._pending_ping)
        self.provider._on_message(self.ws, json.dumps({"code": 0, "msg": "PONG", "id": 1}))
        self.assertIsNotNone(self.provider._pending_ping)
        for code in (False, 0.0, "0", None):
            with self.subTest(code=code):
                self.provider._on_message(self.ws, json.dumps({"code": code, "msg": "PONG", "id": 0}))
                self.assertIsNotNone(self.provider._pending_ping)
        self.provider._on_message(self.ws, json.dumps({"code": 0, "msg": "PONG", "id": 0}))
        self.assertIsNone(self.provider._pending_ping)
        self.assertEqual(self.store.snapshot(self.key)["last_valid_update"], before)
        self.clock.now += 5; self.provider.watchdog_once()
        self.clock.now += 16
        self.update()
        self.provider.watchdog_once()
        self.assertTrue(self.ws.closed.is_set())
        self.assertEqual(self.store.snapshot(self.key)["status"], "recovering")

    def test_reconnect_old_socket_and_ack_are_ignored_and_range_backfill_repairs_gaps(self):
        self.update()
        old_ws, old_request = self.ws, self.ws.sent[0]
        self.provider._on_close(old_ws, 1000, "Штатный reconnect")
        self.ws = FakeSocket(); self.provider._ws = self.ws; self.provider._on_open(self.ws)
        self.provider._on_message(old_ws, packet(self.key, T + 120_000, event=int(self.clock() * 1000)))
        self.ack(old_request)
        self.assertFalse(self.store.snapshot(self.key)["subscribed"])
        self.clock.now += 180
        start = interval_start(int(self.clock() * 1000), "1m")
        self.provider._on_message(self.ws, packet(self.key, start, event=int(self.clock() * 1000)))
        self.assertTrue(self.store.snapshot(self.key)["gaps"])
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        self.rest.return_value = [raw(start - 120_000), raw(start - 60_000), raw(start)]
        self.provider.reconcile_once(self.key)
        self.assertTrue(self.store.snapshot(self.key)["ready"])
        self.assertEqual(self.store.snapshot(self.key)["gaps"], [])
        self.assertLess(self.rest.call_args.kwargs["start_ms"], start)
        self.assertEqual(self.rest.call_args.kwargs["end_ms"], int(self.clock() * 1000) + 1)

    def test_rejected_ack_and_subscription_timeout_are_not_success(self):
        self.ack(code=400, msg="Некорректная подписка")
        self.assertFalse(self.store.snapshot(self.key)["ready"])
        self.clock.now += 11; self.provider.watchdog_once()
        self.assertTrue(self.ws.closed.is_set())

    def test_direct_provider_rejects_31_channels_before_mutation(self):
        keys = [StreamKey("mexc", "spot", f"ASSET{i}USDT", "1m") for i in range(31)]
        with self.assertRaises(ValueError): self.provider.sync_subscriptions(keys)
        self.assertEqual(self.provider.streams, [self.key])


class SpotFactoryTests(unittest.TestCase):
    def setUp(self):
        self.cache, self.ready = {}, {}
        self.addCleanup(factory.stop_ws_provider)
        self.selection = patch.object(factory, "trading_selection", return_value=("mexc", "spot", {}))
        self.selection.start(); self.addCleanup(self.selection.stop)
        self.start = patch.object(ManagedMarketDataProvider, "start",
            lambda provider, *args, streams=None, **kwargs: provider.sync_subscriptions(streams or []))
        self.start.start(); self.addCleanup(self.start.stop)

    def test_spot_shards_are_stable_multi_tf_aware_and_reconcile_routes_exact_key(self):
        keys = {StreamKey("mexc", "spot", f"ASSET{i}USDT", "1m") for i in range(61)}
        cache, ready = factory.start_ws_provider(streams=keys, cache=self.cache, ready=self.ready, publisher=Mock())
        providers = list(factory._providers.values())
        self.assertEqual(sorted(len(p.streams) for p in providers), [1, 30, 30])
        self.assertTrue(all(isinstance(p, MEXCSpotWebSocketDataProvider) for p in providers))
        ownership = {key: p for p in providers for key in p.streams}
        five = StreamKey("mexc", "spot", "ASSET0USDT", "5m")
        factory.sync_subscriptions(keys | {five})
        self.assertIs(cache, factory.get_shared_cache()[0]); self.assertIs(ready, factory.get_shared_cache()[1])
        for key, owner in ownership.items(): self.assertIn(key, owner.streams)
        self.assertTrue(factory.request_reconcile(five))
        self.assertIsNone(factory.get_ws_provider("mexc", "spot"))
        factory.sync_subscriptions([five])
        self.assertEqual(set(cache), {five.id})
        self.assertIsInstance(factory.get_ws_provider("mexc", "spot", interval="Min5"), MEXCSpotWebSocketDataProvider)
        with self.assertRaises(ValueError): factory.sync_subscriptions([StreamKey("mexc", "spot", "BTCUSDT", "3m")])
        self.assertEqual(set(cache), {five.id})

    def test_bingx_rest_only_intervals_do_not_reject_or_silently_change_tf(self):
        keys = [StreamKey("bingx", "perpetual", "BTCUSDT", tf) for tf in ("1m", "2h", "6h", "8h", "1d", "1w", "1M")]
        factory.start_ws_provider(streams=keys, cache=self.cache, ready=self.ready, publisher=Mock())
        for key in keys[1:]:
            capability = factory.provider_capabilities(key.exchange, key.market_type, key.interval)
            self.assertFalse(capability["ws_supported"]); self.assertTrue(capability["rest_supported"])
            self.assertIsInstance(factory.get_ws_provider("bingx", "perpetual", interval=key.interval), RESTPollingProvider)
            self.assertEqual(self.cache[key.id]["stream"], key.to_dict())


class SpotPublicRESTTests(unittest.TestCase):
    @staticmethod
    def response(rows):
        response = Mock(status_code=200, headers={}); response.json.return_value = rows
        return response

    def test_default_public_budget_is_durable_ip_shared_without_env_or_account_keys(self):
        from src.exchanges.rate_limit import get_exchange_rate_limiters
        with tempfile.TemporaryDirectory() as directory:
            fetch = PublicRESTFetcher(settings={"data_dir": directory})
            first = fetch._limiter("mexc")
            second = PublicRESTFetcher(settings={"data_dir": directory})._limiter("mexc")
            first.acquire(); second.acquire()
            self.assertEqual(first.limiters[0]._key, second.limiters[0]._key)
            self.assertEqual(first.limiters[0]._cooldown_key, second.limiters[0]._cooldown_key)
            self.assertEqual(first.limiters[0].db_path, str(Path(directory) / "exchange_rate_limits.sqlite3"))
            direct = get_exchange_rate_limiters("mexc", data_dir=directory)
            self.assertEqual(direct["public"]._key, first.limiters[0]._key)
            self.assertEqual(first.limiters[0].bucket, "public")

    def test_no_local_fallback_if_durable_limiter_unavailable(self):
        with patch("src.exchanges.rate_limit.get_exchange_rate_limiters", side_effect=OSError("Нет shared volume")), \
             patch("requests.get") as network:
            with self.assertRaises(OSError): PublicRESTFetcher()._limiter("mexc")
            network.assert_not_called()

    def test_current_futures_rest_host_is_api_mexc_com(self):
        request = Mock(return_value=self.response({"success": True, "data": {
            "time": [T // 1000], "open": [10], "high": [12], "low": [9], "close": [11], "vol": [2]}}))
        PublicRESTFetcher(request=request, rate_limiters={"mexc": Mock()})(
            StreamKey("mexc", "perpetual", "BTCUSDT", "1m"), start_ms=T, end_ms=T + 60_000, limit=1)
        self.assertEqual(request.call_args.args[0], "https://api.mexc.com/api/v1/contract/kline/BTC_USDT")

    def test_418_preserves_shared_cooldown_and_does_not_retry_ban(self):
        response = Mock(status_code=418, headers={"Retry-After": "120"})
        response.raise_for_status.side_effect = requests.HTTPError("Ban", response=response)
        limiter, request = Mock(), Mock(return_value=response)
        with self.assertRaises(requests.HTTPError):
            PublicRESTFetcher(request=request, rate_limiters={"mexc": limiter})(
                StreamKey("mexc", "spot", "BTCUSDT", "1m"), start_ms=T, end_ms=T + 60_000, limit=1)
        request.assert_called_once(); limiter.cooldown.assert_called_once_with(120)

    def test_spot_hour8_exact_4h_backfill_and_incomplete_historical_bucket_is_not_closed(self):
        start = interval_start(T, "8h")
        clock = Clock(start / 1000 + 9 * 3600)
        rows = [[start, "10", "12", "9", "11", "2"],
                [start + 14_400_000, "11", "13", "10", "12", "3"],
                [start + 28_800_000, "12", "14", "11", "13", "4"]]
        request = Mock(return_value=self.response(rows))
        fetch = PublicRESTFetcher(request=request, rate_limiters={"mexc": Mock()})
        key = StreamKey("mexc", "spot", "BTCUSDT", "8h")
        with patch("src.exchanges.public_market_rest.time.time", clock):
            value = fetch(key, start_ms=start, end_ms=int(clock() * 1000) + 1, limit=2)
            self.assertEqual([r["timestamp"] for r in value], [start, start + 28_800_000])
            self.assertEqual((value[0]["open"], value[0]["close"], value[0]["high"], value[0]["low"], value[0]["volume"]),
                             (10, 12, 13, 9, 5))
            self.assertTrue(value[0]["is_closed"]); self.assertFalse(value[1]["is_closed"])
            self.assertEqual(request.call_args.kwargs["params"]["interval"], "4h")
            request.return_value = self.response([rows[0], rows[2]])
            incomplete = fetch(key, start_ms=start, end_ms=int(clock() * 1000) + 1, limit=2)
            self.assertEqual([r["timestamp"] for r in incomplete], [start + 28_800_000])
            self.assertFalse(incomplete[0]["is_closed"])


if __name__ == "__main__":
    unittest.main()
