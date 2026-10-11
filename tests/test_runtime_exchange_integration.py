"""Сквозная привязка factory/worker без запросов к бирже и торговых операций."""

from pathlib import Path

import pytest

from src.exchanges import exchange_factory as factory
from src.core.execution_coordinator import ExecutionCoordinator
from src.exchanges.impl.spot_trading_adapter import SpotTradingAdapter
from src.trading_settings import (
    execution_account_label, get_trading_settings, install_trading_settings_snapshot,
    validate_trading_settings,
)


@pytest.fixture(autouse=True)
def isolated_context(tmp_path, monkeypatch):
    import src.config as config
    previous_settings = get_trading_settings()
    previous_globals = dict(vars(config))
    monkeypatch.setenv("SERVO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BINGX_API_KEY", "fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "fake-secret")
    monkeypatch.setenv("MEXC_API_KEY", "fake-key")
    monkeypatch.setenv("MEXC_SECRET_KEY", "fake-secret")
    factory.reset_client()
    yield
    factory.reset_client()
    install_trading_settings_snapshot(previous_settings)
    vars(config).update(previous_globals)


def test_factory_owners_share_journal_not_raw_clients(tmp_path):
    selected = validate_trading_settings({"execution_mode": "demo"})
    a = factory.get_trading_client(settings=selected, owner_id="a", runtime_config={"DATA_DIR": str(tmp_path)})
    b = factory.get_trading_client(settings=selected, owner_id="b", runtime_config={"DATA_DIR": str(tmp_path)})
    assert isinstance(a, ExecutionCoordinator)
    assert isinstance(b, ExecutionCoordinator)
    assert a.raw_client is not b.raw_client
    assert a.owner_id == "a" and b.owner_id == "b"
    assert a.account_id == b.account_id == execution_account_label(selected)
    assert a.journal.path == b.journal.path == tmp_path / "execution.sqlite3"
    assert a.raw_client.base_url == "https://open-api-vst.bingx.com"
    assert factory.get_trading_client(settings=selected, owner_id="a", runtime_config={"DATA_DIR": str(tmp_path)}) is a


def test_public_reader_does_not_create_execution_journal(tmp_path):
    selected = validate_trading_settings({"execution_mode": "demo"})
    factory.configure_client_context(data_dir=tmp_path)
    public = factory.get_market_data_client(settings=selected)
    assert not isinstance(public, ExecutionCoordinator)
    assert not (tmp_path / "execution.sqlite3").exists()
    with pytest.raises(PermissionError):
        public.place_order("BTC-USDT", "BUY", 1)


def test_worker_snapshot_and_owner_bound_before_pipeline(tmp_path, monkeypatch):
    import src.config as config
    import src.core.pipeline as pipeline
    from src.core.process_worker import run_strategy_instance_pipeline
    from src.config_loader import resolve_strategy_instance_config
    from src.runtime import StrategyInstance
    selected = validate_trading_settings({"execution_mode": "demo"})
    instance = StrategyInstance.from_dict({"id": "test_macdx", "symbol": "BTC-USDT", "strategy": "MACDX", "profile": "default", "enabled": True})
    runtime = resolve_strategy_instance_config(instance)
    runtime.update(execution=selected, TRADING_SETTINGS=selected, DATA_DIR=str(tmp_path),
                   EXCHANGE="bingx", MARKET_TYPE="perpetual", EXECUTION_MODE="demo")
    observed = []
    class ProbePipeline:
        def __init__(self, runtime_config=None, **kwargs):
            runtime_config = runtime_config or kwargs["config"]
            client = factory.get_exchange_client(settings=selected)
            assert client.owner_id == instance.id
            assert config.MODE == "demo" and config.EXECUTION_MODE == "demo"
            assert client.journal.path == Path(tmp_path) / "execution.sqlite3"
            observed.append(runtime_config["STRATEGY_INSTANCE_ID"])
        def run_symbol_pipeline(self, *args):
            return None
    monkeypatch.setattr(pipeline, "PipelineOrchestrator", ProbePipeline)
    run_strategy_instance_pipeline(instance.to_dict(), runtime)
    assert observed == [instance.id]


def test_internal_market_options_are_not_execution_fields():
    from src.core.data.reader import trading_settings_for_config
    config = {"execution": validate_trading_settings({}), "MARKET_DATA": {"heartbeat_timeout_seconds": 40}}
    selected = trading_settings_for_config(config)
    assert "heartbeat_timeout_seconds" not in selected["market_data"]
    assert validate_trading_settings(selected) == selected


def test_spot_factory_creates_owned_adapter_and_closes_resources(tmp_path, monkeypatch):
    started, stopped = [], []
    monkeypatch.setattr(SpotTradingAdapter, "start_supervisor", lambda self: started.append(self.owner_id))
    original = SpotTradingAdapter.shutdown
    def shutdown(self):
        stopped.append(self.owner_id)
        return original(self)
    monkeypatch.setattr(SpotTradingAdapter, "shutdown", shutdown)
    selected = validate_trading_settings({"exchange": "mexc", "market_type": "spot", "execution_mode": "live",
        "spot": {"quote_budget_usdt": 100, "allow_local_protection": True}})
    client = factory.get_trading_client(settings=selected, owner_id="spot_owner", runtime_config={"DATA_DIR": str(tmp_path)})
    assert isinstance(client, SpotTradingAdapter)
    assert client.owner_id == "spot_owner"
    assert client.protection_mode == "local_only"
    assert client.account_id == execution_account_label(selected)
    assert started == ["spot_owner"]
    assert client.journal.path == tmp_path / "spot_ledger.sqlite3"
    factory.reset_client()
    assert stopped == ["spot_owner"]


class RuntimeProcess:
    def __init__(self, harness, target, args=(), name=None):
        self.harness = harness
        self.target, self.args, self.name = target, args, name
        self.pid = None
        self.exitcode = None
        self.alive = False
        self.daemon = None
        harness.processes.append(self)

    def start(self):
        if self.name == self.harness.fail_process and self.harness.fail_before_spawn:
            raise OSError("Тестовый отказ до spawn")
        self.pid = 10000 + len(self.harness.processes)
        self.alive = True
        self.harness.events.append(("spawn", self.name))
        if self.name == self.harness.fail_process:
            raise OSError("Тестовый частичный отказ spawn")

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.harness.events.append(("join", self.name))
        self.alive = False
        self.exitcode = 0

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False


@pytest.fixture
def main_runtime(tmp_path, monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    import multiprocessing
    import threading
    import src.config as config
    import src.config_loader as loader
    import src.main as main
    import src.symbol_runtime_control as control
    from src.exchanges import rate_limit, ws_provider_factory as ws
    from src.runtime import StrategyInstance

    selected = validate_trading_settings({"execution_mode": "read_only"})
    install_trading_settings_snapshot(selected)
    instance = StrategyInstance.from_dict({"id": "btc_macdx", "symbol": "BTC-USDT", "strategy": "MACDX"})
    harness = SimpleNamespace(
        main=main, instances=[instance], configs={}, processes=[], events=[],
        configured=[], subscriptions=[], statuses=[], contexts=[], cache={}, ready={},
        fail_process=None, fail_before_spawn=False, command=None, tick=0, now=0.0, on_sleep=None, max_ticks=3,
        command_reader=control.SymbolCommandReader, command_read_json=control.read_json,
        command_write_json=control.atomic_write_json,
    )
    harness.configs[instance.id] = {
        "SYMBOLS": [instance.symbol], "STRATEGY_STYLE": "MACDX",
        "STYLE_PRESETS": {"MACDX": {"timeframe": "5m", "history_candles": 300}},
    }

    def context(method):
        harness.contexts.append(method)
        return SimpleNamespace(Process=lambda **kwargs: RuntimeProcess(harness, **kwargs), Event=threading.Event)

    def start_owner(**kwargs):
        harness.start_options = deepcopy(kwargs["settings"])
        harness.start_streams = list(kwargs["streams"])
        harness.events.append(("owner_start", None))
        return harness.cache, harness.ready

    def configure_owner(**kwargs):
        harness.configured.append(deepcopy(kwargs["settings"]))
        harness.events.append(("configure", None))

    def sync_owner(streams):
        harness.subscriptions.append(list(streams))
        harness.events.append(("sync", None))

    def sleep(_seconds):
        harness.tick += 1
        harness.now += 31
        if harness.on_sleep:
            harness.on_sleep(harness.tick)
        if harness.tick >= harness.max_ticks:
            raise KeyboardInterrupt

    monkeypatch.setattr(multiprocessing, "get_context", context)
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: harness.now, time=lambda: harness.now, sleep=sleep))
    monkeypatch.setattr(main, "info", lambda message: None)
    monkeypatch.setattr(main, "warning", lambda message: None)
    monkeypatch.setattr(main, "error", lambda message: harness.events.append(("error", message)))
    monkeypatch.setattr(config, "CHART_SETTINGS", {"enabled": False})
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path / "charts"))
    monkeypatch.setattr(loader, "clear_config_cache", lambda: None)
    monkeypatch.setattr(loader, "get_strategy_instances", lambda: harness.instances)
    monkeypatch.setattr(loader, "resolve_strategy_instance_config", lambda instance: deepcopy(harness.configs[instance.id]))
    monkeypatch.setattr(rate_limit, "get_exchange_rate_limiters", lambda *args, **kwargs: {})
    monkeypatch.setattr(ws, "start_ws_provider", start_owner)
    monkeypatch.setattr(ws, "configure_market_data", configure_owner)
    monkeypatch.setattr(ws, "sync_subscriptions", sync_owner)
    monkeypatch.setattr(ws, "stop_ws_provider", lambda: harness.events.append(("owner_stop", None)))
    monkeypatch.setattr(control, "read_json", lambda path: harness.command)
    monkeypatch.setattr(control, "SymbolCommandReader", lambda path: SimpleNamespace(read=lambda: control.read_json(path)))
    monkeypatch.setattr(control, "atomic_write_json", lambda path, value: harness.statuses.append(deepcopy(value)))
    monkeypatch.setattr("os.kill", lambda *args: harness.events.append(("signal", None)))
    return harness


@pytest.mark.parametrize("failure", ["exception", "missing_cache", "missing_ready"])
def test_main_owner_failure_is_closed_before_any_spawn(main_runtime, monkeypatch, failure):
    from src.exchanges import ws_provider_factory as ws
    runtime = main_runtime
    def fail(**kwargs):
        raise OSError("Тестовый отказ owner")
    if failure == "exception":
        monkeypatch.setattr(ws, "start_ws_provider", fail)
    else:
        setattr(runtime, "cache" if failure == "missing_cache" else "ready", None)
    with pytest.raises(RuntimeError, match="владелец market-data недоступен"):
        runtime.main.run_multiprocess_pipeline()
    assert not runtime.processes
    assert not runtime.configured and not runtime.subscriptions
    assert runtime.events[-1] == ("owner_stop", None)
    assert runtime.statuses[-1]["workers"] == []


@pytest.mark.parametrize("failed_worker", ["btc_macdx", "eth_macdx", "Worker-Charts"])
def test_main_partial_spawn_failure_stops_readers_before_owner(main_runtime, monkeypatch, failed_worker):
    from src import config
    from src.runtime import StrategyInstance
    runtime = main_runtime
    second = StrategyInstance.from_dict({"id": "eth_macdx", "symbol": "ETH-USDT", "strategy": "MACDX"})
    runtime.instances.append(second)
    runtime.configs[second.id] = {**runtime.configs["btc_macdx"], "SYMBOLS": [second.symbol]}
    monkeypatch.setattr(config, "CHART_SETTINGS", {"enabled": True})
    runtime.fail_process = failed_worker if failed_worker == "Worker-Charts" else f"Worker-{failed_worker}"
    with pytest.raises(OSError, match="частичный отказ spawn"):
        runtime.main.run_multiprocess_pipeline()
    assert all(not process.is_alive() for process in runtime.processes)
    stop_index = runtime.events.index(("owner_stop", None))
    assert all(runtime.events.index(("join", process.name)) < stop_index for process in runtime.processes)
    assert runtime.statuses[-1]["workers"] == []


def test_main_unchanged_polling_does_not_reconfigure_or_resubscribe(main_runtime):
    runtime = main_runtime
    runtime.main.run_multiprocess_pipeline()
    assert runtime.contexts == ["spawn"]
    assert runtime.events[0] == ("owner_start", None)
    assert not runtime.configured and not runtime.subscriptions
    assert runtime.start_options["history_limit"] >= 301
    worker = runtime.processes[0]
    assert worker.args[1]["execution"]["execution_mode"] == "read_only"
    assert worker.args[2] is runtime.cache and worker.args[3] is runtime.ready


def test_main_history_change_reconfigures_once_without_subscription_change(main_runtime):
    runtime = main_runtime
    def change(tick):
        if tick == 1:
            runtime.configs["btc_macdx"]["STYLE_PRESETS"]["MACDX"]["history_candles"] = runtime.start_options["history_limit"] + 1000
    runtime.on_sleep = change
    runtime.main.run_multiprocess_pipeline()
    assert len(runtime.configured) == 1
    assert runtime.configured[0]["history_limit"] == runtime.start_options["history_limit"] + 1001
    assert not runtime.subscriptions


def test_main_timeframe_and_htf_change_updates_subscriptions_only_once(main_runtime):
    runtime = main_runtime
    def change(tick):
        if tick == 1:
            runtime.configs["btc_macdx"].update(
                STRATEGY_STYLE="AISCALP",
                STYLE_PRESETS={"AISCALP": {"timeframe": "15m", "history_candles": 300}},
                AISCALP_SETTINGS={"multi_timeframe": {"enabled": True, "htf_timeframe": "1h"}},
            )
    runtime.on_sleep = change
    runtime.main.run_multiprocess_pipeline()
    assert not runtime.configured
    assert len(runtime.subscriptions) == 1
    assert {key.interval for key in runtime.subscriptions[0]} == {"15m", "1h"}


@pytest.mark.parametrize("stop_kind", ["command", "crash"])
def test_main_removes_unused_subscriptions_without_waiting_for_poll(main_runtime, stop_kind):
    runtime = main_runtime
    def stop(tick):
        if tick == 1:
            # Не наступил интервал hot-reload: unsubscribe всё равно должен быть немедленным.
            runtime.now = 1
            if stop_kind == "command":
                runtime.command = {"id": "stop-1", "action": "stop", "instance_id": "btc_macdx"}
            else:
                runtime.processes[0].alive = False
    runtime.on_sleep = stop
    runtime.main.run_multiprocess_pipeline()
    assert runtime.subscriptions == [[]]
    assert runtime.statuses[1]["workers"] == []


def test_main_hot_reconfigure_failure_stops_workers_and_owner(main_runtime, monkeypatch):
    from src.exchanges import ws_provider_factory as ws
    runtime = main_runtime
    def change(tick):
        runtime.configs["btc_macdx"]["STYLE_PRESETS"]["MACDX"]["history_candles"] = 10000
    def fail(**kwargs):
        raise ValueError("Тестовый отказ reconfigure")
    runtime.on_sleep = change
    monkeypatch.setattr(ws, "configure_market_data", fail)
    with pytest.raises(ValueError, match="отказ reconfigure"):
        runtime.main.run_multiprocess_pipeline()
    assert not runtime.processes[0].is_alive()
    assert runtime.events[-1] == ("owner_stop", None)


def test_main_empty_runtime_can_start_symbol_and_add_history(main_runtime):
    runtime = main_runtime
    instance = runtime.instances.pop()
    def start(tick):
        if tick == 1:
            runtime.instances.append(instance)
            runtime.configs[instance.id]["STYLE_PRESETS"]["MACDX"]["history_candles"] = 10000
            runtime.command = {"id": "start-1", "action": "start", "instance_id": instance.id}
    runtime.on_sleep = start
    runtime.main.run_multiprocess_pipeline()
    assert runtime.start_streams == []
    assert len(runtime.configured) == 1 and runtime.configured[0]["history_limit"] == 10001
    assert len(runtime.subscriptions) == 1 and runtime.subscriptions[0][0].symbol == "BTCUSDT"
    assert len(runtime.processes) == 1
    assert runtime.events.index(("sync", None)) < runtime.events.index(("spawn", "Worker-btc_macdx"))


def test_main_spot_keeps_protection_one_minute_subscription(main_runtime):
    runtime = main_runtime
    selected = validate_trading_settings({"exchange": "mexc", "market_type": "spot", "execution_mode": "read_only"})
    install_trading_settings_snapshot(selected)
    runtime.main.run_multiprocess_pipeline()
    assert {key.interval for key in runtime.start_streams} == {"1m", "5m"}
    assert all((key.exchange, key.market_type) == ("mexc", "spot") for key in runtime.start_streams)


def test_main_chart_spawn_receives_snapshot_config_and_shutdown_event(main_runtime, monkeypatch):
    from src import config
    from src.runtime import run_runtime_chart_worker
    runtime = main_runtime
    monkeypatch.setattr(config, "CHART_SETTINGS", {"enabled": True, "update_interval": 17})
    runtime.main.run_multiprocess_pipeline()
    chart = runtime.processes[-1]
    assert chart.target is run_runtime_chart_worker
    assert chart.daemon is False
    selected, chart_config, stop_event = chart.args
    assert selected == get_trading_settings()
    assert chart_config["DATA_DIR"] == config.DATA_DIR
    assert chart_config["CHARTS_DIR"] == config.CHARTS_DIR
    assert chart_config["CHART_SETTINGS"]["update_interval"] == 17
    assert stop_event.is_set()
    assert runtime.events.index(("join", "Worker-Charts")) < runtime.events.index(("owner_stop", None))


@pytest.mark.parametrize("process_name", ["Worker-btc_macdx", "Worker-Charts"])
def test_main_spawn_failure_before_pid_does_not_join_unstarted_process(main_runtime, monkeypatch, process_name):
    from src import config
    runtime = main_runtime
    monkeypatch.setattr(config, "CHART_SETTINGS", {"enabled": True})
    runtime.fail_process = process_name
    runtime.fail_before_spawn = True
    with pytest.raises(OSError, match="отказ до spawn"):
        runtime.main.run_multiprocess_pipeline()
    failed = runtime.processes[-1]
    assert failed.pid is None
    assert ("join", failed.name) not in runtime.events
    assert runtime.events[-1] == ("owner_stop", None)
    assert runtime.statuses[-1]["workers"] == []


def test_main_stopping_one_consumer_keeps_shared_subscription(main_runtime):
    from copy import deepcopy
    from src.runtime import StrategyInstance
    runtime = main_runtime
    second = StrategyInstance.from_dict({"id": "btc_second", "symbol": "BTC-USDT", "strategy": "MACDX"})
    runtime.instances.append(second)
    runtime.configs[second.id] = deepcopy(runtime.configs["btc_macdx"])
    def stop(tick):
        if tick == 1:
            runtime.command = {"id": "stop-1", "action": "stop", "instance_id": "btc_macdx"}
    runtime.on_sleep = stop
    runtime.main.run_multiprocess_pipeline()
    assert len(runtime.start_streams) == 1
    assert not runtime.configured and not runtime.subscriptions
    assert [worker["id"] for worker in runtime.statuses[1]["workers"]] == [second.id]


def test_main_history_decrease_is_applied_without_resubscribing(main_runtime):
    runtime = main_runtime
    preset = runtime.configs["btc_macdx"]["STYLE_PRESETS"]["MACDX"]
    preset["history_candles"] = 10000
    def change(tick):
        if tick == 1:
            preset["history_candles"] = 300
    runtime.on_sleep = change
    runtime.main.run_multiprocess_pipeline()
    assert runtime.start_options["history_limit"] == 10001
    assert len(runtime.configured) == 1
    assert runtime.configured[0]["history_limit"] == get_trading_settings()["market_data"]["history_limit"]
    assert not runtime.subscriptions


def test_main_rejected_control_revision_does_not_replay_but_fresh_restart_runs(main_runtime, tmp_path, monkeypatch):
    import os
    import src.symbol_runtime_control as control

    runtime = main_runtime
    path = tmp_path / "symbol_runtime_command.json"
    monkeypatch.setattr(control, "COMMAND_PATH", path)
    monkeypatch.setattr(control, "SymbolCommandReader", runtime.command_reader)
    monkeypatch.setattr(control, "read_json", runtime.command_read_json)
    warnings = []
    monkeypatch.setattr(control, "warning", warnings.append)
    old = {"id": "old-runtime", "action": "restart", "instance_id": "btc_macdx",
           "runtime_pid": os.getpid() + 10000, "execution": get_trading_settings()}
    runtime.command_write_json(path, old)
    original = path.read_bytes()
    runtime.max_ticks = 5

    def replace(tick):
        if tick == 3:
            assert len(runtime.processes) == 1
            assert path.read_bytes() == original
            runtime.command_write_json(path, {**old, "id": "fresh-runtime", "runtime_pid": os.getpid()})

    runtime.on_sleep = replace
    runtime.main.run_multiprocess_pipeline()
    assert len(warnings) == 1 and "другому runtime" in warnings[0]
    assert len(runtime.processes) == 2
    assert runtime.events.count(("spawn", "Worker-btc_macdx")) == 2
    assert runtime.statuses[-1]["last_command_id"] == "fresh-runtime"
    assert all(not process.is_alive() for process in runtime.processes)
