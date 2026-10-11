"""Настоящие package imports и integrated readonly cycles; все I/O биржи запрещены."""

import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.core import analyzer, predict
from src.ai import client as ai_client
from test_ai_runtime import completion
from src.core.data import collector
from src.core.data.reader import (
    MarketDataUnavailable, build_streams_for_configs, read_strategy_data, read_live_data,
    runtime_data_config, stream_key_for_config, validate_analysis_market_data,
)
from src.core.lightweight_analyzer import LightweightAnalyzer
from src.core.pipeline import PipelineOrchestrator
from src.core.signals.macdx import position_guard_check
from src.core.strategies.factory import create_pipeline
from src.exchanges.market_data import StreamKey, StreamStore
from src.exchanges.market_data_storage import (
    AtomicJsonStore, instance_prices_path, publish_market_snapshot, publish_strategy_view,
)
from src.exchanges import ws_provider_factory as factory
from test_market_data_layer import Clock, T, raw


class StrategyReaderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = self.temp.name
        self.config = {
            "DATA_DIR": self.directory, "EXCHANGE": "bingx", "MARKET_TYPE": "perpetual",
            "EXECUTION_MODE": "read_only", "STRATEGY_STYLE": "MACDX", "STRATEGY_INSTANCE_ID": "first",
            "SYMBOLS": ["BTCUSDT"], "ENABLE_NEWS": False, "SMART_SAMPLING": {"enabled": False},
            "STYLE_PRESETS": {style: {"timeframe": "1m", "history_candles": 50, "loop_interval": 5}
                              for style in ("MACDX", "HYBRID", "AISCALP", "SWING", "SCALP")},
            "MARKET_DATA": {"history_limit": 100, "min_history": 1, "max_age_seconds": 30},
            "AISCALP_SETTINGS": {"multi_timeframe": {"enabled": False}},
            "MACDX_SETTINGS": {"signal_rules": {"skip_trading_hours_filter": True}},
        }
        self.store = StreamStore(settings=self.config["MARKET_DATA"], clock=self.clock)
        self.key = stream_key_for_config("BTCUSDT", self.config)
        rows = [raw(T + 120_000 - (60 - index) * 60_000, 10 + index * 0.03) for index in range(61)]
        self.snapshot = self.store.merge(self.key, rows, status="rest")
        self.patches = [
            patch("src.exchanges.market_data.time.time", self.clock),
            patch("src.exchanges.exchange_factory.get_exchange_client", side_effect=AssertionError("Private client запрещён")),
            patch("src.core.pipeline.get_exchange_client", side_effect=AssertionError("Private client запрещён")),
            patch.object(analyzer, "get_exchange_client", side_effect=AssertionError("Private read запрещён")),
            patch("src.core.pipeline.executor.execute_prediction", side_effect=AssertionError("Исполнение запрещено")),
            patch("src.core.pipeline.monitor.monitor_symbol", side_effect=AssertionError("Account monitor запрещён")),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "DEEPSEEK_API_KEY": "", "LM_STUDIO_API_KEY": ""}),
            patch("requests.request", side_effect=AssertionError("Сеть запрещена")),
            patch("requests.get", side_effect=AssertionError("Сеть запрещена")),
            patch("requests.post", side_effect=AssertionError("Сеть запрещена")),
        ]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
        factory.set_shared_cache(self.store.cache, self.store.ready)
        self.addCleanup(factory.set_shared_cache, None, None)
        self.publish(self.config)

    def publish(self, config, snapshot=None):
        snapshot = self.snapshot if snapshot is None else snapshot
        publish_market_snapshot(self.directory, snapshot)
        publish_strategy_view(self.directory, snapshot, instance_id=config["STRATEGY_INSTANCE_ID"],
                              symbol="BTCUSDT", closed_only=True, legacy=False)

    def test_root_initializer_and_real_analyzer_use_instance_not_shared_prices(self):
        AtomicJsonStore(Path(self.directory) / "prices" / "BTCUSDT.json").write([{"closePrice": 999999}])
        result = analyzer.analyze_symbol("BTCUSDT", config=self.config)
        self.assertAlmostEqual(result["current_price"], self.snapshot["candles"][-2]["closePrice"])
        self.assertEqual(result["market_data"]["stream"], self.key.to_dict())
        self.assertEqual(result["market_data"]["instance_id"], "first")
        self.assertNotIn("999999", result["prompt"])

    def test_missing_stale_or_wrong_instance_never_falls_back_to_live_or_legacy(self):
        AtomicJsonStore(Path(self.directory) / "prices" / "BTCUSDT.json").write(self.snapshot["candles"])
        with self.assertRaises(MarketDataUnavailable):
            analyzer.analyze_symbol("BTCUSDT", config={**self.config, "STRATEGY_INSTANCE_ID": "missing"})
        with self.assertRaises(MarketDataUnavailable):
            analyzer.analyze_symbol("BTCUSDT", config={**self.config, "EXCHANGE": "mexc"})
        self.clock.now += 31
        with self.assertRaises(MarketDataUnavailable): analyzer.analyze_symbol("BTCUSDT", config=self.config)

    def test_exact_source_and_tf_for_two_instances_same_symbol(self):
        other = {**self.config, "EXCHANGE": "mexc", "MARKET_TYPE": "spot", "STRATEGY_INSTANCE_ID": "second",
                 "STYLE_PRESETS": {"MACDX": {"timeframe": "5m", "history_candles": 50}}}
        key = stream_key_for_config("BTCUSDT", other)
        snapshot = self.store.merge(key, [raw(T - (60 - i) * 300_000, 50 + i) for i in range(61)], status="rest")
        self.publish(other, snapshot)
        one = analyzer.analyze_symbol("BTCUSDT", config=self.config)
        two = analyzer.analyze_symbol("BTCUSDT", config=other)
        self.assertEqual(one["market_data"]["stream"], self.key.to_dict())
        self.assertEqual(two["market_data"]["stream"], key.to_dict())
        self.assertLess(one["current_price"], two["current_price"])
        self.assertIn("5m", two["prompt"])

    def test_htf_uses_exact_cache_source_without_primary_instance_file(self):
        key = StreamKey("bingx", "perpetual", "BTCUSDT", "1h")
        htf = self.store.merge(key, [raw(T - i * 3_600_000, 20) for i in range(50)], status="rest")
        config = {**self.config, "AISCALP_SETTINGS": {"multi_timeframe": {"enabled": True, "htf_timeframe": "1h", "htf_candles": 48}}}
        result = analyzer.analyze_htf("BTCUSDT", config=config)
        self.assertEqual(result["market_data"]["stream"], key.to_dict())
        self.assertEqual(read_strategy_data("BTCUSDT", self.config)["stream"], self.key.to_dict())
        self.store.mark(key, status="error", error="Ошибка HTF")
        self.assertIsNone(analyzer.analyze_htf("BTCUSDT", config=config))

    def test_stream_builder_respects_execution_config_aliases_and_multi_tf(self):
        config = {**self.config, "EXCHANGE": "mexc", "MARKET_TYPE": "spot", "STRATEGY_STYLE": "AISCALP",
                  "AISCALP_SETTINGS": {"multi_timeframe": {"enabled": True, "htf_timeframe": "1h"}}}
        keys = build_streams_for_configs([self.config, config])
        self.assertEqual(set(keys), {self.key, StreamKey("mexc", "spot", "BTCUSDT", "1m"),
                                    StreamKey("mexc", "spot", "BTCUSDT", "1h")})
        raw_config = {"execution": {"exchange": "mexc", "market_type": "spot", "execution_mode": "read_only"},
                      "STRATEGY_STYLE": "MACDX", "STYLE_PRESETS": self.config["STYLE_PRESETS"],
                      "STRATEGY_INSTANCE": {"id": "raw", "symbol": "BTC-USDT"}}
        self.assertEqual(build_streams_for_configs([raw_config]), [StreamKey("mexc", "spot", "BTCUSDT", "1m")])
        self.assertEqual(runtime_data_config(raw_config)["STRATEGY_INSTANCE_ID"], "raw")

    def test_full_readonly_cycles_collect_analyze_and_journal_without_private_or_orders(self):
        sentinel = Path(self.directory) / "prices" / "BTCUSDT.json"
        AtomicJsonStore(sentinel).write([{"sentinel": True}])
        for style in ("MACDX", "HYBRID", "AISCALP", "SWING", "SCALP"):
            with self.subTest(style=style):
                config = {**self.config, "STRATEGY_STYLE": style, "STRATEGY_INSTANCE_ID": style.lower()}
                orchestrator = PipelineOrchestrator(config, ws_cache=self.store.cache, ws_ready=self.store.ready)
                prediction = orchestrator.run_iteration("BTCUSDT", self.store.cache, self.store.ready)
                self.assertIsNotNone(prediction)
                self.assertFalse(prediction["execution_allowed"])
                self.assertEqual(prediction["execution_mode"], "read_only")
                self.assertIsNone(orchestrator._client)
                self.assertIsNone(orchestrator.pipeline._client)
                self.assertIsNone(orchestrator._tracker)
                path = Path(self.directory) / "instances" / style.lower() / "decision_journal.json"
                journal = AtomicJsonStore(path).read()
                self.assertEqual(len(journal["BTCUSDT"]["entries"]), 1)
                self.assertTrue(instance_prices_path(self.directory, style.lower(), "BTCUSDT").exists())
        self.assertEqual(AtomicJsonStore(sentinel).read(), [{"sentinel": True}])

    def test_stopped_readonly_loop_does_not_do_startup_private_reads_or_cleanup(self):
        event = threading.Event(); event.set()
        orchestrator = PipelineOrchestrator(self.config)
        orchestrator.run_symbol_pipeline("BTCUSDT", self.store.cache, self.store.ready, stop_event=event)
        self.assertTrue(instance_prices_path(self.directory, "first", "BTCUSDT").exists())
        self.assertIsNone(orchestrator._client)

    def test_live_client_creation_happens_only_after_shared_cache_binding(self):
        config = {**self.config, "EXECUTION_MODE": "live"}
        with patch("src.core.strategies.macdx.TradeTracker"):
            pipeline = create_pipeline("MACDX", config)
        client = Mock(); client.get_positions.return_value = {}
        def create_client(**kwargs):
            settings = kwargs["settings"]
            self.assertEqual((settings["exchange"], settings["market_type"], settings["execution_mode"]),
                             ("bingx", "perpetual", "live"))
            self.assertIs(factory.get_shared_cache()[0], self.store.cache)
            return client
        with patch("src.exchanges.exchange_factory.get_exchange_client", side_effect=create_client) as create:
            factory.set_shared_cache(None, None)
            pipeline._position_for_symbol("BTCUSDT", self.store.cache, self.store.ready)
            create.assert_called_once()
            self.assertIs(pipeline._client, client)

    def test_macdx_native_and_compatibility_commands_preserve_execution_freshness_gate(self):
        config = {**self.config, "EXECUTION_MODE": "live"}
        client = Mock(); client.get_positions.return_value = {}
        with patch("src.core.strategies.macdx.TradeTracker"), \
             patch("src.exchanges.exchange_factory.get_exchange_client", return_value=client):
            pipeline = create_pipeline("MACDX", config)
            prediction = pipeline.run_cycle("BTCUSDT", self.store.cache, self.store.ready)
            self.assertTrue(prediction["execution_allowed"])
            self.assertEqual(prediction["market_data"]["stream"], self.key.to_dict())
            def slow_signal(analysis, regime):
                self.clock.now += 31
                return {"signal": "HOLD"}
            with patch.object(pipeline._signal_generator, "generate", side_effect=slow_signal):
                command = pipeline.generate_command("BTCUSDT", self.store.cache, self.store.ready)
            self.assertIsNotNone(command)
            self.assertFalse(command.metadata["execution_allowed"])

    def test_predict_rejects_wrong_source_before_ai_and_stale_after_slow_ai(self):
        analysis = analyzer.analyze_symbol("BTCUSDT", config=self.config)
        with patch.object(predict, "get_prediction") as ai:
            value = predict.process_analysis(analysis, config={**self.config, "EXCHANGE": "mexc"})
            self.assertEqual(value["action"], "hold"); ai.assert_not_called()
        def slow_ai(method, url, **kwargs):
            self.assertEqual(method, "POST")
            self.assertFalse(kwargs["allow_redirects"])
            self.assertTrue(kwargs["stream"])
            self.assertEqual(kwargs["json"]["messages"], [{"role": "user", "content": analysis["prompt"]}])
            self.clock.now += 31
            return completion(json.dumps({"action": "buy", "confidence": 0.9, "reason": "offline", "stop_loss": 9, "take_profit": 15}))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-offline-key"}), \
             patch.object(predict, "should_call_ai", return_value=(True, "offline")), \
             patch.object(ai_client.requests, "request", side_effect=slow_ai) as transport:
            value = predict.process_analysis(analysis, config=self.config)
            transport.assert_called_once()
            self.assertEqual(value["action"], "hold")
            self.assertFalse(value["execution_allowed"])

    def test_bound_cache_failure_or_unsubscribe_never_uses_fresh_disk(self):
        analysis = analyzer.analyze_symbol("BTCUSDT", config=self.config)
        self.assertTrue(validate_analysis_market_data(analysis, self.config))
        self.store.mark(self.key, status="error", error="Ошибка транспорта")
        with self.assertRaises(MarketDataUnavailable):
            read_live_data("BTCUSDT", self.config)
        self.assertFalse(validate_analysis_market_data(analysis, self.config))
        factory.set_shared_cache({}, {})
        with self.assertRaises(MarketDataUnavailable):
            read_live_data("BTCUSDT", self.config)
        self.assertFalse(validate_analysis_market_data(analysis, self.config))
        broken = Mock(); broken.get.side_effect = BrokenPipeError("Proxy недоступен")
        factory.set_shared_cache(broken, {})
        with self.assertRaises(MarketDataUnavailable):
            read_live_data("BTCUSDT", self.config)
        self.assertFalse(validate_analysis_market_data(analysis, self.config))
        factory.set_shared_cache(None, None)
        self.assertEqual(read_live_data("BTCUSDT", self.config)["stream"], self.key.to_dict())

    def test_reconfiguration_requires_exact_tf_and_current_history_length(self):
        changed_tf = {**self.config, "STYLE_PRESETS": {"MACDX": {"timeframe": "5m", "history_candles": 50}}}
        with self.assertRaises(MarketDataUnavailable):
            read_strategy_data("BTCUSDT", changed_tf)
        changed_length = {**self.config, "STYLE_PRESETS": {"MACDX": {"timeframe": "1m", "history_candles": 70}}}
        with self.assertRaises(MarketDataUnavailable):
            read_strategy_data("BTCUSDT", changed_length)
        for limit in (0, -1, True, "10"):
            with self.subTest(limit=limit), self.assertRaises(MarketDataUnavailable):
                read_strategy_data("BTCUSDT", self.config, limit=limit)

    def test_analysis_entry_points_readonly_ignore_passed_account_position(self):
        with_position = analyzer.analyze_symbol("BTCUSDT", position={"entry_price": 10, "side": "BUY"}, config=self.config)
        self.assertFalse(with_position["has_position"])
        automatic = analyzer.analyze_symbol_with_position("BTCUSDT", config=self.config)
        self.assertFalse(automatic["has_position"])
        results = analyzer.main(config=self.config)
        self.assertEqual(len(results), 1)
        for parallel in (True, False):
            predictions = predict.main(results, config={**self.config, "ENABLE_PARALLEL_PROCESSING": parallel})
            self.assertEqual(predictions[0]["market_data"]["instance_id"], "first")
            self.assertEqual(predictions[0]["action"], "hold")

    def test_htf_failure_after_ai_blocks_prediction_even_when_primary_remains_fresh(self):
        key = StreamKey("bingx", "perpetual", "BTCUSDT", "1h")
        self.store.merge(key, [raw(T - i * 3_600_000, 20) for i in range(50)], status="rest")
        config = {**self.config, "STRATEGY_STYLE": "AISCALP",
                  "AISCALP_SETTINGS": {"multi_timeframe": {"enabled": True, "htf_timeframe": "1h", "htf_candles": 48}}}
        analysis = analyzer.analyze_symbol("BTCUSDT", config=config)
        self.assertTrue(validate_analysis_market_data(analysis, config))
        def slow_ai(method, url, **kwargs):
            self.assertEqual(method, "POST")
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["json"]["messages"], [{"role": "user", "content": analysis["prompt"]}])
            self.store.mark(key, status="error", error="Ошибка HTF во время AI")
            return completion(json.dumps({"action": "buy", "confidence": 0.9, "reason": "offline", "stop_loss": 9, "take_profit": 15}))
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-offline-key"}), \
             patch.object(predict, "should_call_ai", return_value=(True, "offline")), \
             patch.object(ai_client.requests, "request", side_effect=slow_ai) as transport:
            value = predict.process_analysis(analysis, config=config)
            transport.assert_called_once()
        self.assertEqual(value["action"], "hold")
        self.assertFalse(value["execution_allowed"])
        self.assertTrue(read_live_data("BTCUSDT", self.config)["ready"])

    def test_last_execution_gate_rejects_staleness_during_account_sync(self):
        config = {**self.config, "EXECUTION_MODE": "live"}
        prediction = {**analyzer.analyze_symbol("BTCUSDT", config=self.config), "action": "buy"}
        client = Mock()
        calls = []
        def positions():
            calls.append(True)
            if len(calls) == 2:
                self.clock.now += 31
            return {}
        client.get_positions.side_effect = positions
        ownership = Mock(); ownership.get_owner.return_value = None
        with patch("src.core.pipeline.TradeTracker"), patch("src.core.strategies.macdx.TradeTracker"), \
             patch("src.core.pipeline.get_position_ownership_store", return_value=ownership), \
             patch("src.core.pipeline.get_exchange_client", return_value=client) as create:
            orchestrator = PipelineOrchestrator(config, ws_cache=self.store.cache, ws_ready=self.store.ready)
            with patch.object(orchestrator, "run_cycle", return_value=prediction):
                value = orchestrator.run_iteration("BTCUSDT", self.store.cache, self.store.ready)
        self.assertFalse(value["execution_allowed"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(create.call_args.kwargs["settings"]["execution_mode"], "live")

    def test_optional_entry_context_failure_does_not_skip_monitor(self):
        config = {**self.config, "EXECUTION_MODE": "live", "STRATEGY_STYLE": "HYBRID"}
        prediction = {**analyzer.analyze_symbol("BTCUSDT", config=self.config), "action": "buy"}
        client = Mock(); client.get_positions.return_value = {}
        tracker = Mock(); tracker.set_entry_context.side_effect = OSError("Journal недоступен")
        ownership = Mock(); ownership.get_owner.return_value = None
        with patch("src.core.pipeline.TradeTracker", return_value=tracker), \
             patch("src.core.strategies.hybrid.TradeTracker"), \
             patch("src.core.pipeline.get_position_ownership_store", return_value=ownership), \
             patch("src.core.pipeline.get_exchange_client", return_value=client), \
             patch("src.core.pipeline.executor.execute_prediction", return_value=True) as execute, \
             patch("src.core.pipeline.monitor.monitor_symbol") as monitor:
            orchestrator = PipelineOrchestrator(config, ws_cache=self.store.cache, ws_ready=self.store.ready)
            with patch.object(orchestrator, "run_cycle", return_value=prediction):
                value = orchestrator.run_iteration("BTCUSDT", self.store.cache, self.store.ready)
            self.assertTrue(value["execution_allowed"])
            execute.assert_called_once()
            tracker.set_entry_context.assert_called_once()
            monitor.assert_called_once()

    def test_lightweight_instance_sync_keeps_indicators_closed_live_price_separate_and_rebuilds(self):
        engine = LightweightAnalyzer("BTCUSDT")
        first = engine.sync_market_data(self.config)
        self.assertTrue(first["market_data_ready"])
        self.assertEqual(first["current_price"], self.snapshot["candles"][-1]["closePrice"])
        count = first["candle_count"]
        self.assertEqual(engine.sync_market_data(self.config)["candle_count"], count)
        changed = copy.deepcopy(self.snapshot)
        changed["candles"][-2]["closePrice"] += 0.1
        changed["version"] += 100
        self.publish(self.config, changed)
        self.assertTrue(engine.sync_market_data(self.config)["market_data_ready"])
        self.assertEqual(engine.get_snapshot()["candle_count"], count)
        self.clock.now += 31
        stale = engine.get_snapshot()
        self.assertFalse(stale["market_data_ready"])
        self.assertEqual(stale["current_price"], 0)


class MACDXGuardIntegrationTests(unittest.TestCase):
    def test_hour_candle_is_fresh_by_live_update_not_open_time(self):
        clock = Clock(T / 1000 + 1800)
        config = {"EXCHANGE": "bingx", "MARKET_TYPE": "perpetual", "STRATEGY_STYLE": "MACDX",
                  "STYLE_PRESETS": {"MACDX": {"timeframe": "1h"}},
                  "MARKET_DATA": {"min_history": 1, "max_age_seconds": 30}}
        key = stream_key_for_config("BTCUSDT", config)
        store = StreamStore(settings=config["MARKET_DATA"], clock=clock)
        store.merge(key, [raw(T - 3_600_000), raw(T)], status="rest")
        position = {"entry_price": 10, "side": "BUY"}
        with patch("src.exchanges.market_data.time.time", clock):
            result = position_guard_check("BTCUSDT", position, {}, store.cache, {}, {"timeframe": "1h", "leverage": 6}, config=config)
            self.assertTrue(result["should_close"])
            clock.now += 31
            context = {"peak_price": 10}
            result = position_guard_check("BTCUSDT", position, context, store.cache, {}, {"timeframe": "1h"}, config=config)
            self.assertFalse(result["should_close"])
            self.assertEqual(context, {"peak_price": 10})
            result = position_guard_check("BTCUSDT", position, {}, store.cache, {}, {"timeframe": "1h"}, config={**config, "EXCHANGE": "mexc"})
            self.assertFalse(result["should_close"])

    def test_symbol_only_legacy_cache_is_not_a_valid_guard_source(self):
        result = position_guard_check("BTCUSDT", {"entry_price": 10}, {}, {"BTC-USDT": [raw(T)]}, {}, {"timeframe": "1m"})
        self.assertFalse(result["should_close"])


if __name__ == "__main__":
    unittest.main()
