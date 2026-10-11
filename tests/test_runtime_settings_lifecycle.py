"""Lifecycle и гонки execution: фиктивные процессы, реальный flock, без биржи и сети."""

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import queue
import shutil
import threading

from types import SimpleNamespace

import pytest

from src import config_loader, trading_settings
from src.core import runtime_supervisor
from src.runtime import apply_legacy_runtime_config, prepare_runtime_config
from src.symbol_runtime_control import read_json, write_symbol_command
from src.telegram_panel.backend.routes import config_routes, runtime as runtime_routes


class FakeProcess:
    created = []
    start_hook = None
    stubborn = False

    def __init__(self, target, args=(), name=None):
        self.target = target
        self.args = args
        self.name = name
        self.pid = 987654
        self.exitcode = None
        self.alive = False
        self.daemon = None
        type(self).created.append(self)

    def start(self):
        if type(self).start_hook:
            type(self).start_hook(self)
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        if not type(self).stubborn:
            self.alive = False
            self.exitcode = 0

    def kill(self):
        self.terminate()


class JsonRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    shutil.copytree(Path(config_loader._CONFIG_DIR), config_dir)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config_loader, "_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(config_loader, "_resolved_configs", {})
    monkeypatch.setattr(trading_settings, "_runtime_settings", None)
    monkeypatch.setattr(config_routes, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(runtime_routes, "STATUS_PATH", data_dir / "runtime_status.json")
    monkeypatch.setattr(runtime_routes, "COMMAND_PATH", data_dir / "runtime_command.json")
    monkeypatch.setattr(runtime_supervisor, "Process", FakeProcess)
    monkeypatch.setattr(runtime_supervisor, "os", SimpleNamespace(environ=os.environ, getenv=os.getenv, getpid=os.getpid, kill=lambda *args: None))
    monkeypatch.setattr(FakeProcess, "created", [])
    monkeypatch.setattr(FakeProcess, "start_hook", None)
    monkeypatch.setattr(FakeProcess, "stubborn", False)
    from src import config
    original_globals = config.__dict__.copy()
    supervisor = runtime_supervisor.RuntimeSupervisor(
        command_path=runtime_routes.COMMAND_PATH, status_path=runtime_routes.STATUS_PATH,
        settings_path=config_dir / "trading.json", data_dir=data_dir, start_on_boot=False,
    )
    supervisor._write_status()
    yield supervisor, config_dir / "trading.json", data_dir
    config.__dict__.update(original_globals)


def replace_execution(path, **changes):
    data = json.loads(path.read_text())
    data["execution"].update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_refresh_validates_before_replacing_and_reset_is_explicit(lifecycle):
    _, path, _ = lifecycle
    original = trading_settings.get_trading_settings()
    replace_execution(path, execution_mode="demo")
    assert trading_settings.get_trading_settings() == original
    assert trading_settings.refresh_trading_settings(path)["execution_mode"] == "demo"
    path.write_text('{"execution":{"margin_mode":"cross"}}', encoding="utf-8")
    with pytest.raises(ValueError):
        trading_settings.refresh_trading_settings(path)
    assert trading_settings.get_trading_settings()["execution_mode"] == "demo"
    path.write_text('{"execution":{}}', encoding="utf-8")
    trading_settings.reset_trading_settings()
    assert trading_settings.get_trading_settings()["execution_mode"] == "read_only"


def test_stop_start_refreshes_parent_globals_and_passes_snapshot(lifecycle):
    supervisor, path, _ = lifecycle
    supervisor.start_runtime()
    first = FakeProcess.created[0]
    assert first.args[0]["execution_mode"] == "read_only"
    assert supervisor.state == "running"
    supervisor.stop_runtime()
    assert supervisor.state == "stopped"
    replace_execution(path, exchange="mexc", execution_mode="live", position_mode="one_way", grid_mode="inventory")
    supervisor.start_runtime()
    second = FakeProcess.created[1]
    assert second.args[0]["exchange"] == "mexc"
    assert second.args[0]["execution_mode"] == "live"
    from src import config
    assert (config.EXCHANGE, config.MARKET_TYPE, config.MODE) == ("mexc", "perpetual", "real")
    assert config.TRADING_SETTINGS == trading_settings.get_trading_settings() == second.args[0]
    status = json.loads(supervisor.status_path.read_text())
    assert status["execution"] == second.args[0]
    assert status["runtime_pid"] == second.pid


def test_spawn_uses_one_validated_read_even_if_file_changes_after_guard(lifecycle, monkeypatch):
    supervisor, path, _ = lifecycle
    def edit_after_read(data_dir):
        replace_execution(path, exchange="mexc", execution_mode="live")
        return None
    monkeypatch.setattr(runtime_supervisor, "managed_state_block_reason", edit_after_read)
    supervisor.start_runtime()
    assert FakeProcess.created[0].args[0]["exchange"] == "bingx"
    assert FakeProcess.created[0].args[0]["execution_mode"] == "read_only"
    assert trading_settings.get_trading_settings() == FakeProcess.created[0].args[0]


def test_start_on_alive_runtime_does_not_refresh_snapshot(lifecycle):
    supervisor, path, _ = lifecycle
    supervisor.start_runtime()
    original = trading_settings.get_trading_settings()
    replace_execution(path, execution_mode="live")
    supervisor.start_runtime()
    assert trading_settings.get_trading_settings() == original
    assert len(FakeProcess.created) == 1


def test_custom_status_path_does_not_redirect_managed_state_guard(lifecycle, monkeypatch):
    _, path, data = lifecycle
    monkeypatch.setattr(runtime_supervisor, "DATA_DIR", data)
    (data / "position_owners.json").write_text('{"BTCUSDT":{"owner_id":"btc_grid"}}', encoding="utf-8")
    supervisor = runtime_supervisor.RuntimeSupervisor(
        command_path=data / "control" / "runtime_command.json",
        status_path=data / "control" / "runtime_status.json",
        settings_path=path, start_on_boot=False,
    )
    supervisor.start_runtime()
    assert not FakeProcess.created
    assert "position_owners.json" in supervisor.last_error


def test_invalid_start_does_not_spawn_and_reports_crashed(lifecycle):
    supervisor, path, _ = lifecycle
    path.write_text('{"execution":{"margin_mode":"cross"}}', encoding="utf-8")
    supervisor.start_runtime()
    assert not FakeProcess.created
    status = json.loads(supervisor.status_path.read_text())
    assert status["state"] == "crashed"
    assert status["runtime_pid"] is None
    assert "margin_mode" in status["last_error"]


def test_start_requires_successful_starting_status(lifecycle, monkeypatch):
    supervisor, _, _ = lifecycle
    def fail(*args):
        raise OSError("Тестовый отказ status")
    monkeypatch.setattr(runtime_supervisor, "_atomic_write_json", fail)
    supervisor.start_runtime()
    assert not FakeProcess.created
    assert supervisor.state == "crashed"


def test_failed_spawn_does_not_report_running(lifecycle, monkeypatch):
    supervisor, _, _ = lifecycle
    def fail(process):
        raise OSError("Тестовый отказ spawn")
    monkeypatch.setattr(FakeProcess, "start_hook", fail)
    supervisor.start_runtime()
    status = json.loads(supervisor.status_path.read_text())
    assert status["state"] == "crashed"
    assert status["runtime_pid"] is None


def test_partial_spawn_failure_keeps_alive_process_handle(lifecycle, monkeypatch):
    supervisor, _, _ = lifecycle
    def fail_after_spawn(process):
        process.alive = True
        raise OSError("Тестовый отказ после spawn")
    monkeypatch.setattr(FakeProcess, "start_hook", fail_after_spawn)
    supervisor.start_runtime()
    assert supervisor.runtime_process is FakeProcess.created[0]
    status = json.loads(supervisor.status_path.read_text())
    assert status["state"] == "stopping"
    assert status["runtime_pid"] == supervisor.runtime_process.pid
    assert status["started_at"] is not None
    assert status["stopped_at"] is None
    supervisor.start_runtime()
    assert len(FakeProcess.created) == 1


def test_shutdown_requested_before_spawn_does_not_start_runtime(lifecycle):
    supervisor, _, _ = lifecycle
    supervisor.request_shutdown()
    supervisor.start_runtime()
    assert not FakeProcess.created
    status = json.loads(supervisor.status_path.read_text())
    assert status["state"] == "stopped"
    assert status["control_enabled"] is False


def test_failed_stop_keeps_process_and_blocks_restart(lifecycle, monkeypatch):
    supervisor, _, _ = lifecycle
    supervisor.start_runtime()
    process = supervisor.runtime_process
    monkeypatch.setattr(FakeProcess, "stubborn", True)
    supervisor.stop_runtime()
    assert supervisor.state == "stopping"
    assert supervisor.runtime_process is process
    supervisor._refresh_state()
    assert supervisor.state == "stopping"
    supervisor.restart_runtime()
    assert len(FakeProcess.created) == 1
    status = json.loads(supervisor.status_path.read_text())
    assert status["state"] == "stopping"
    assert status["runtime_pid"] == process.pid


@pytest.mark.parametrize("filename,content", [
    ("position_owners.json", '{"BTCUSDT":{"owner_id":"btc_grid"}}'),
    ("pending_orders.json", '[{"state":"unknown"}]'),
    ("execution_journal.json", '{broken'),
    ("order_journal.sqlite3", "неизвестная схема"),
])
def test_manual_account_switch_is_blocked_by_managed_state(lifecycle, filename, content):
    supervisor, path, data = lifecycle
    supervisor.start_runtime()
    supervisor.stop_runtime()
    (data / filename).write_text(content, encoding="utf-8")
    replace_execution(path, execution_mode="live")
    supervisor.start_runtime()
    assert len(FakeProcess.created) == 1
    assert supervisor.state == "crashed"
    assert supervisor.execution_settings["execution_mode"] == "read_only"
    assert trading_settings.get_trading_settings()["execution_mode"] == "read_only"


def test_api_blocks_unknown_sqlite_journal_without_schema_assumptions(lifecycle):
    _, _, data = lifecycle
    (data / "execution_journal.sqlite3").write_bytes(b"SQLite format 3\x00")
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(JsonRequest({"execution_mode": "live"}), {}))
    assert exc.value.status_code == 409
    assert "SQLite" in exc.value.detail


def test_same_account_can_restart_for_reconciliation(lifecycle):
    supervisor, _, data = lifecycle
    supervisor.start_runtime()
    supervisor.stop_runtime()
    (data / "position_owners.json").write_text('{"BTCUSDT":{"owner_id":"btc_grid"}}', encoding="utf-8")
    supervisor.start_runtime()
    assert len(FakeProcess.created) == 2
    assert supervisor.state == "running"


@pytest.mark.parametrize("source", [
    {"EXCHANGE": "mexc"}, {"MODE": "real"}, {"execution": {"execution_mode": "live"}},
    {"STRATEGY_INSTANCE": {"id": "btc", "TRADING_SETTINGS": {"exchange": "mexc"}}},
])
def test_instance_config_cannot_change_execution(lifecycle, source):
    module = SimpleNamespace(BOT_CONFIG={"original": True})
    with pytest.raises(ValueError):
        apply_legacy_runtime_config(module, source)
    assert module.BOT_CONFIG == {"original": True}


def test_symbol_restart_uses_running_snapshot_not_staged_file(lifecycle):
    supervisor, path, data = lifecycle
    supervisor.start_runtime()
    replace_execution(path, protection={"trigger_price": "last", "failure_policy": "pause", "verification_timeout_seconds": 20, "recovery_attempts": 4})
    command = write_symbol_command("restart", instance_id="btc_macdx", command_path=data / "symbol_runtime_command.json", settings_path=path)
    assert command["execution"] == supervisor.execution_settings
    assert command["execution"]["protection"]["verification_timeout_seconds"] == 10
    assert command["runtime_pid"] == supervisor.runtime_process.pid


def test_symbol_start_requires_running_status(lifecycle):
    _, path, data = lifecycle
    with pytest.raises(ValueError):
        write_symbol_command("start", instance_id="btc_macdx", command_path=data / "symbol_runtime_command.json", settings_path=path)
    assert not (data / "symbol_runtime_command.json").exists()
    assert write_symbol_command("stop", instance_id="btc_macdx", command_path=data / "symbol_runtime_command.json", settings_path=path)["action"] == "stop"


def _spawn_chart_snapshot_probe(settings, config_dir, chart_config, stop_event, result):
    config_loader._CONFIG_DIR = str(config_dir)
    from src.core import chart_worker
    from src.runtime import run_runtime_chart_worker
    def probe(config, event):
        from src import config as globals_config
        result.put((trading_settings.get_trading_settings(), globals_config.EXCHANGE,
                    config, event.is_set()))
    chart_worker.run_chart_worker = probe
    run_runtime_chart_worker(settings, chart_config, stop_event)


def test_chart_spawn_uses_approved_snapshot_and_explicit_paths(lifecycle):
    _, path, data = lifecycle
    approved = trading_settings.refresh_trading_settings(path)
    replace_execution(path, exchange="mexc", execution_mode="live")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    stop_event = context.Event()
    stop_event.set()
    chart_config = {"DATA_DIR": str(data), "CHARTS_DIR": str(data / "charts"),
                    "CHART_SETTINGS": {"update_interval": 17}}
    child = context.Process(target=_spawn_chart_snapshot_probe,
                            args=(approved, path.parent, chart_config, stop_event, result))
    child.start()
    try:
        snapshot, exchange, config, stopped = result.get(timeout=20)
        child.join(timeout=20)
        assert child.exitcode == 0
        assert snapshot == config["execution"] == config["TRADING_SETTINGS"] == approved
        assert exchange == config["EXCHANGE"] == "bingx"
        assert config["DATA_DIR"] == str(data)
        assert config["CHARTS_DIR"] == str(data / "charts")
        assert config["CHART_SETTINGS"] == chart_config["CHART_SETTINGS"]
        assert stopped is True
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=5)
        result.close()


def _spawn_worker_market_reader_probe(instance, config, cache, ready, result):
    from src.core import pipeline
    from src.core.process_worker import run_strategy_instance_pipeline
    from src.exchanges import ws_provider_factory as ws
    from src.exchanges.market_data import StreamKey
    key = StreamKey("bingx", "perpetual", instance["symbol"], "5m")
    class ProbePipeline:
        def __init__(self, **kwargs):
            pass
        def run_symbol_pipeline(self, *args):
            rejected = []
            for action in (lambda: ws.start_ws_provider(streams=[]),
                           lambda: ws.configure_market_data(settings={}),
                           lambda: ws.sync_subscriptions([]),
                           lambda: ws.request_reconcile(key),
                           lambda: ws.stop_ws_provider()):
                try:
                    action()
                except RuntimeError:
                    rejected.append(True)
                else:
                    rejected.append(False)
            snapshot = ws.get_market_snapshot(key, allow_stale=True)
            result.put((trading_settings.get_trading_settings(), snapshot["stream"],
                        rejected, ws.get_ws_provider() is None, os.getpid()))
    pipeline.PipelineOrchestrator = ProbePipeline
    # Даже уже загруженный чужой снимок child заменяется явным worker config.
    trading_settings.install_trading_settings_snapshot({"exchange": "mexc", "execution_mode": "read_only"})
    run_strategy_instance_pipeline(instance, config, cache, ready)


def test_spawn_worker_reads_owner_proxies_but_cannot_control_lifecycle(lifecycle, monkeypatch):
    from src.exchanges import ws_provider_factory as ws
    from src.exchanges.market_data import StreamKey
    from src.exchanges.ws.runtime import ManagedMarketDataProvider
    from src.runtime import StrategyInstance
    _, path, data = lifecycle
    approved = trading_settings.refresh_trading_settings(path)
    instance = StrategyInstance.from_dict({"id": "btc_macdx", "symbol": "BTC-USDT", "strategy": "MACDX"})
    config = config_loader.resolve_strategy_instance_config(instance)
    config.update(execution=approved, TRADING_SETTINGS=approved, DATA_DIR=str(data),
                  EXCHANGE="bingx", MARKET_TYPE="perpetual", EXECUTION_MODE="read_only")
    key = StreamKey("bingx", "perpetual", instance.symbol, "5m")
    # Настоящий owner/Store/Manager, но без фоновых потоков, HTTP и WS.
    monkeypatch.setattr(ManagedMarketDataProvider, "start", lambda self, *, streams: self.sync_subscriptions(streams))
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    with context.Manager() as manager:
        cache, ready = manager.dict(), manager.dict()
        child = None
        try:
            shared = ws.start_ws_provider(streams=[key], settings={"data_dir": str(data)},
                                          cache=cache, ready=ready)
            assert shared == (cache, ready)
            child = context.Process(target=_spawn_worker_market_reader_probe,
                                    args=(instance.to_dict(), config, cache, ready, result))
            child.start()
            snapshot, stream, rejected, no_provider, child_pid = result.get(timeout=20)
            child.join(timeout=20)
            assert child.exitcode == 0
            assert child_pid != os.getpid()
            assert snapshot == approved
            assert stream == key.to_dict()
            assert rejected == [True] * 5 and no_provider
            # Попытки reader не удалили подписку/owner в parent.
            assert ws.get_ws_provider().streams == [key]
            assert key.id in cache
        finally:
            if child is not None and child.is_alive():
                child.kill()
                child.join(timeout=5)
            ws.stop_ws_provider()
            result.close()


def test_symbol_reader_rejects_other_snapshot_or_other_runtime(lifecycle):
    _, _, data = lifecycle
    path = data / "symbol_runtime_command.json"
    for payload in ({"action": "restart", "execution": {"execution_mode": "live"}},
                    {"action": "start", "runtime_pid": os.getpid() + 1}):
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert read_json(path) is None


def _child_snapshot_echo(settings, config_dir, result):
    config_loader._CONFIG_DIR = str(config_dir)
    prepare_runtime_config(settings)
    from src import config
    result.put((trading_settings.get_trading_settings(), config.EXCHANGE, config.MODE))


@pytest.mark.parametrize("method", ["fork", "spawn"])
def test_child_uses_approved_snapshot_not_new_file(lifecycle, method):
    _, path, _ = lifecycle
    approved = trading_settings.refresh_trading_settings(path)
    replace_execution(path, exchange="mexc", execution_mode="live")
    context = multiprocessing.get_context(method)
    result = context.Queue()
    child = context.Process(target=_child_snapshot_echo, args=(approved, path.parent, result))
    child.start()
    try:
        snapshot, exchange, mode = result.get(timeout=20)
        child.join(timeout=20)
        assert child.exitcode == 0
        assert snapshot == approved
        assert (exchange, mode) == ("bingx", "read_only")
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=5)
        result.close()


def _put_execution_in_child(config_dir, data_dir, result):
    config_routes.CONFIG_DIR = config_dir
    config_routes.DATA_DIR = data_dir
    runtime_routes.STATUS_PATH = data_dir / "runtime_status.json"
    runtime_routes.COMMAND_PATH = data_dir / "runtime_command.json"
    result.put("attempted")
    try:
        response = asyncio.run(config_routes.update_execution_config(JsonRequest({"exchange": "mexc", "execution_mode": "live"}), {}))
        result.put(response["execution"])
    except config_routes.HTTPException as exc:
        result.put(exc.status_code)


def test_put_waits_for_start_and_cannot_switch_spawned_account(lifecycle, monkeypatch):
    supervisor, path, data = lifecycle
    entered = threading.Event()
    release = threading.Event()
    def hold_start(process):
        entered.set()
        assert release.wait(timeout=20)
    monkeypatch.setattr(FakeProcess, "start_hook", hold_start)
    starter = threading.Thread(target=supervisor.start_runtime)
    starter.start()
    assert entered.wait(timeout=20)
    assert json.loads(supervisor.status_path.read_text())["state"] == "starting"
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    child = context.Process(target=_put_execution_in_child, args=(path.parent, data, result))
    child.start()
    try:
        assert result.get(timeout=20) == "attempted"
        with pytest.raises(queue.Empty):
            result.get(timeout=0.2)
        release.set()
        starter.join(timeout=20)
        assert not starter.is_alive()
        assert result.get(timeout=20) == 409
        child.join(timeout=20)
        assert child.exitcode == 0
        assert FakeProcess.created[0].args[0]["exchange"] == "bingx"
        assert json.loads(path.read_text())["execution"]["exchange"] == "bingx"
    finally:
        release.set()
        starter.join(timeout=20)
        if child.is_alive():
            child.kill()
            child.join(timeout=5)
        result.close()


def _start_in_child(config_dir, data_dir, result):
    config_loader._CONFIG_DIR = str(config_dir)
    runtime_supervisor.Process = FakeProcess
    FakeProcess.start_hook = None
    FakeProcess.created = []
    supervisor = runtime_supervisor.RuntimeSupervisor(command_path=data_dir / "runtime_command.json",
        status_path=data_dir / "runtime_status.json", settings_path=config_dir / "trading.json", data_dir=data_dir, start_on_boot=False)
    result.put("attempted")
    supervisor.start_runtime()
    result.put(FakeProcess.created[0].args[0] if FakeProcess.created else supervisor.last_error)


def test_start_waits_for_put_and_loads_new_account(lifecycle, monkeypatch):
    _, path, data = lifecycle
    entered = threading.Event()
    release = threading.Event()
    original_save = config_routes._save_json
    def hold_save(*args):
        entered.set()
        assert release.wait(timeout=20)
        original_save(*args)
    monkeypatch.setattr(config_routes, "_save_json", hold_save)
    responses = []
    def save():
        responses.append(asyncio.run(config_routes.update_execution_config(JsonRequest({"exchange": "mexc", "execution_mode": "live"}), {})))
    writer = threading.Thread(target=save)
    writer.start()
    assert entered.wait(timeout=20)
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    child = context.Process(target=_start_in_child, args=(path.parent, data, result))
    child.start()
    try:
        assert result.get(timeout=20) == "attempted"
        with pytest.raises(queue.Empty):
            result.get(timeout=0.2)
        release.set()
        writer.join(timeout=20)
        selected = result.get(timeout=20)
        child.join(timeout=20)
        assert child.exitcode == 0
        assert selected["exchange"] == "mexc"
        assert selected["execution_mode"] == "live"
        assert responses[0]["execution"] == selected
    finally:
        release.set()
        writer.join(timeout=20)
        if child.is_alive():
            child.kill()
            child.join(timeout=5)
        result.close()


@pytest.mark.parametrize("budget,consent,message", [(0, False, "бюджет"), (100.25, False, "риск")])
def test_live_spot_start_refused_before_spawn(lifecycle, budget, consent, message):
    supervisor, path, _ = lifecycle
    replace_execution(path, exchange="mexc", market_type="spot", execution_mode="live",
        position_mode="one_way", grid_mode="inventory", protection={"trigger_price": "last"},
        spot={"quote_budget_usdt": budget, "allow_local_protection": consent})
    supervisor.start_runtime()
    assert FakeProcess.created == []
    assert supervisor.state == "crashed"
    assert message in supervisor.last_error
    status = json.loads(supervisor.status_path.read_text())
    assert status["runtime_pid"] is None
    assert status["state"] == "crashed"


@pytest.mark.parametrize("mode,budget,consent", [("read_only", 0, False), ("live", 100.25, True)])
def test_spot_start_passes_total_budget_snapshot_not_worker_multiplier(lifecycle, mode, budget, consent):
    supervisor, path, _ = lifecycle
    replace_execution(path, account_id="second-account", exchange="mexc", market_type="spot", execution_mode=mode,
        position_mode="one_way", grid_mode="inventory", protection={"trigger_price": "last"},
        spot={"quote_budget_usdt": budget, "allow_local_protection": consent})
    supervisor.start_runtime()
    assert supervisor.state == "running"
    selected = FakeProcess.created[0].args[0]
    assert selected["spot"]["quote_budget_usdt"] == budget
    from src import config
    assert config.ACCOUNT_ID == "second-account"
    assert config.EXECUTION_ACCOUNT_ID == f"second-account:mexc:{mode}"


def test_empty_initialized_journal_allows_start_and_account_edit(lifecycle):
    from src.core.execution_journal import ExecutionJournal
    supervisor, _, data = lifecycle
    ExecutionJournal(data / "execution.sqlite3")
    result = asyncio.run(config_routes.update_execution_config(JsonRequest({"account_id": "second"}), {}))
    assert result["execution"]["account_id"] == "second"
    supervisor.start_runtime()
    assert supervisor.state == "running"


def test_pending_old_account_journal_blocks_new_account_and_manual_switch(lifecycle):
    from src.core.execution_journal import ExecutionJournal
    supervisor, path, data = lifecycle
    journal = ExecutionJournal(data / "execution.sqlite3")
    journal.prepare("old:mexc:live", "owner", "BTC-USDT", "entry", {})
    original = path.read_bytes()
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(JsonRequest({"account_id": "second"}), {}))
    assert exc.value.status_code == 409
    assert path.read_bytes() == original
    replace_execution(path, account_id="second")
    supervisor.start_runtime()
    assert supervisor.state == "crashed"
    assert not FakeProcess.created


def test_spot_budget_manual_change_blocked_with_old_ownership(lifecycle):
    supervisor, path, data = lifecycle
    supervisor.start_runtime()
    supervisor.stop_runtime()
    (data / "position_owners.json").write_text('{"BTCUSDT":{"owner":"existing"}}')
    replace_execution(path, spot={"quote_budget_usdt": 100.25, "allow_local_protection": True})
    supervisor.start_runtime()
    assert supervisor.state == "crashed"
    assert len(FakeProcess.created) == 1
    assert trading_settings.get_trading_settings() == supervisor.execution_settings


@pytest.mark.parametrize("override", [{"ACCOUNT_ID": "foreign"}, {"EXECUTION_ACCOUNT_ID": "foreign:bingx:live"}])
def test_instance_cannot_override_account_namespace(lifecycle, override):
    _, path, _ = lifecycle
    prepare_runtime_config(trading_settings.read_trading_settings(path))
    from src.runtime import validate_runtime_config_source
    with pytest.raises(ValueError, match="Instance"):
        validate_runtime_config_source({"STRATEGY_INSTANCE": override})
