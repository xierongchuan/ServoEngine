"""Безопасная очистка только временного проекта: без сети и рабочих данных."""

from io import StringIO
import json
from pathlib import Path
import sqlite3
import time
from unittest.mock import Mock

import pytest

from scripts import cleanup
from src.core.execution_journal import ExecutionJournal
from src.exchanges.rate_limit import ExchangeRateLimiter
from src.utils.log_store import EventStore


def put(root, relative, content="данные"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    put(root, "Dockerfile", "FROM python")
    (root / "src").mkdir()
    for relative, content in {
        ".env": "PRIVATE_KEY=do-not-print",
        "config/base.json": '{"ai": {}}',
        "config/trading.json": '{"execution": {"execution_mode": "read_only"}}',
        "config/.trading.lock": "",
        "data/.gitignore": "*\n!.gitignore\n",
        "data/active_trades.json": "{}",
        "data/position_owners.json": "{}",
        "data/trade_history.json": '[{"last_pnl": 1}]',
        "data/decision_journal.json": "[]",
        "data/instances/btc/prices/BTCUSDT.json": "[]",
        "data/instances/btc/prices/BTCUSDT.json.lock": "",
        "data/instances/btc/decision_journal.json": "[]",
        "data/instances/btc/custom.json": '{"retain": true}',
        "data/market_data/bingx/perpetual/BTCUSDT/30m.json": "{}",
        "data/market_data/bingx/perpetual/BTCUSDT/.30m.json.abcd.tmp": "partial",
        "data/prices/BTCUSDT.json": "[]",
        "data/news/news.json": "{}",
        "data/runtime_command.json": '{"action": "start"}',
        "data/runtime_status.json": '{"state": "stopped"}',
        "data/steps.log": "старые события",
        "data/steps.log.lock": "",
        "data/unrecognized.json": "{}",
        ".cache/vst-probes/native-market/probe.sqlite": "UNKNOWN-probe",
        ".cache/test-wheels/pytest.whl": "wheel",
        "src/__pycache__/cache.pyc": "bytecode",
        ".pytest_cache/v/cache/lastfailed": "{}",
        "src/telegram_panel/frontend/dist/index.html": "build",
        "src/telegram_panel/frontend/node_modules/package/file.js": "dependency",
        "charts/chart.png": "png",
    }.items():
        put(root, relative, content)
    return root


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def run(root, monkeypatch, *arguments, inventory=None):
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps([] if inventory is None else inventory)))
    return cleanup.main(["--root", str(root), "--host-root", str(root), "--inventory-stdin", *arguments])


def test_no_arguments_is_preview_without_writes_or_secret_output(project, monkeypatch, capsys):
    before = snapshot(project)
    assert run(project, monkeypatch) == 0
    assert snapshot(project) == before
    output = capsys.readouterr().out
    assert "Предварительный просмотр" in output
    assert "do-not-print" not in output


def test_reset_preview_does_not_import_guard_or_read_sqlite(project, monkeypatch):
    guard = Mock(side_effect=AssertionError("В preview guard не вызывается"))
    monkeypatch.setattr(cleanup, "assert_reset_safe", guard)
    put(project, "data/orders.sqlite3", "invalid database")
    before = snapshot(project)
    assert run(project, monkeypatch, "--mode", "reset") == 0
    assert snapshot(project) == before
    guard.assert_not_called()


def test_cache_removes_only_rebuildable_files(project, monkeypatch):
    before = snapshot(project)
    plan = cleanup.build_plan(project, "cache")
    assert plan
    assert run(project, monkeypatch, "--apply") == 0
    for path in plan:
        assert not path.exists()
    for relative, content in before.items():
        if project / relative not in plan:
            assert (project / relative).read_bytes() == content
    assert not (project / ".cache/cleanup-backups").exists()


def test_reset_archives_exact_bytes_preserves_durable_state_and_is_idempotent(project, monkeypatch):
    journal = ExecutionJournal(project / "data/execution.sqlite3")
    limiter = ExchangeRateLimiter("cleanup-test", capacity=5, period=60, data_dir=project / "data")
    limiter.cooldown(60)
    store = EventStore(project / "data")
    store.write({"event_id": "test", "timestamp": "2026-10-11T00:00:00Z", "level": "INFO",
                 "category": "runtime", "source": "test", "message": "Тестовый журнал"})
    plan = cleanup.build_plan(project, "reset")
    before = snapshot(project)
    assert run(project, monkeypatch, "--mode", "reset", "--apply") == 0
    archives = list((project / ".cache/cleanup-backups").iterdir())
    assert len(archives) == 1
    archive = archives[0]
    assert archive.stat().st_mode & 0o777 == 0o700
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["complete"] and set(manifest["moved"]) == set(manifest["files"])
    for relative in manifest["files"]:
        if relative in before:
            assert (archive / relative).read_bytes() == before[relative]
        else:
            # mode=ro может создать SQLite sidecars; архив включает их после guard.
            assert relative in {"data/logs/events.sqlite3-wal", "data/logs/events.sqlite3-shm"}
            assert (archive / relative).exists()
        assert not (project / relative).exists()
    for relative, content in before.items():
        if project / relative not in plan:
            assert (project / relative).read_bytes() == content
    assert (project / "data/active_trades.json").read_text() == "{}"
    assert (project / "data/position_owners.json").read_text() == "{}"
    assert journal.path.exists()
    assert run(project, monkeypatch, "--mode", "reset", "--apply") == 0
    assert list((project / ".cache/cleanup-backups").iterdir()) == archives
    with sqlite3.connect(archive / "data/logs/events.sqlite3") as database:
        assert database.execute("SELECT event_id FROM log_events").fetchone() == ("test",)


@pytest.mark.parametrize("busy", ["running", "paused", "restarting"])
@pytest.mark.parametrize("mount", ["project", "data", "ancestor"])
def test_active_container_with_any_project_mount_blocks_all_modes(project, monkeypatch, busy, mount):
    source = {"project": project, "data": project / "data", "ancestor": project.parent}[mount]
    inventory = [{"State": {"Status": busy}, "Mounts": [{"Source": str(source)}]}]
    before = snapshot(project)
    assert run(project, monkeypatch, "--apply", inventory=inventory) == 1
    assert snapshot(project) == before


@pytest.mark.parametrize("inventory", [{}, [None], [{}], [{"State": {"Running": True}}]])
def test_unverifiable_container_inventory_fails_closed(project, monkeypatch, inventory):
    before = snapshot(project)
    assert run(project, monkeypatch, "--apply", inventory=inventory) == 1
    assert snapshot(project) == before


def test_unrelated_container_does_not_block_and_fresh_status_does(project, monkeypatch):
    inventory = [{"State": {"Running": True}, "Mounts": [{"Source": "/other-project/data"}]}]
    cleanup.assert_no_writers(project, inventory, project)
    put(project, "data/runtime_status.json", json.dumps({"state": "running", "updated_at_ts": time.time()}))
    before = snapshot(project)
    assert run(project, monkeypatch, "--apply", inventory=inventory) == 1
    assert snapshot(project) == before


@pytest.mark.parametrize("filename,content", [
    ("active_trades.json", '{"BTCUSDT": {"volume": 1}}'),
    ("position_owners.json", '{"BTCUSDT": "owner"}'),
    ("unknown_orders.json", '{"order": "UNKNOWN"}'),
    ("pending_orders.json", "invalid JSON"),
    ("unknown.sqlite3", "invalid SQLite"),
])
def test_reset_cannot_hide_open_or_unknown_state(project, monkeypatch, filename, content):
    put(project, "data/" + filename, content)
    before = snapshot(project)
    assert run(project, monkeypatch, "--mode", "reset", "--apply") == 1
    assert all((project / name).read_bytes() == data for name, data in before.items())
    assert not (project / ".cache/cleanup-backups").exists()


def test_pending_execution_in_committed_wal_blocks_reset(project, monkeypatch):
    path = project / "data/journal.db"
    journal = ExecutionJournal(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_autocheckpoint=0")
    try:
        journal.prepare("test:bingx:demo", "owner", "BTC-USDT", "entry", {})
        before = (project / "data/trade_history.json").read_bytes()
        assert run(project, monkeypatch, "--mode", "reset", "--apply") == 1
        assert (project / "data/trade_history.json").read_bytes() == before
        assert connection.execute("SELECT state FROM intents").fetchone()[0] == "PREPARED"
    finally:
        connection.close()


def test_execution_schema_under_logs_name_is_never_archived(project, monkeypatch):
    path = project / "data/logs/events.sqlite3"
    path.parent.mkdir(parents=True)
    ExecutionJournal(path)
    before = path.read_bytes()
    assert run(project, monkeypatch, "--mode", "reset", "--apply") == 1
    assert path.read_bytes() == before
    assert (project / "data/trade_history.json").exists()


@pytest.mark.parametrize("mode", ["cache", "reset"])
def test_database_and_guard_inside_cache_area_are_protected(project, mode):
    paths = [put(project, "data/prices/orders.SQLITE3", "db"),
             put(project, "data/prices/orders.SQLITE3-wal", "wal"),
             put(project, "data/prices/orders.SQLITE3.lock", "lock"),
             put(project, "data/prices/pending_orders.json", "UNKNOWN")]
    plan = cleanup.build_plan(project, mode)
    assert all(path not in plan for path in paths)


@pytest.mark.parametrize("relative", ["data/prices", "charts", ".pytest_cache", ".cache"])
def test_symlink_to_outside_is_refused_without_deleting_target(project, tmp_path, monkeypatch, relative):
    outside = tmp_path / "outside"
    put(outside, "keep.json", "не удалять")
    path = project / relative
    if path.exists():
        import shutil
        shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside, target_is_directory=True)
    assert run(project, monkeypatch, "--apply") == 1
    assert (outside / "keep.json").read_text() == "не удалять"


def test_cleanup_requires_wrapper_inventory_to_apply(project):
    before = snapshot(project)
    assert cleanup.main(["--root", str(project), "--apply"]) == 1
    assert snapshot(project) == before


@pytest.mark.parametrize("content", ["{", "[]", '{"state": "running"}',
    '{"state": "running", "updated_at_ts": "invalid"}', '{"state": "unknown"}'])
def test_invalid_runtime_status_never_authorizes_cleanup(project, monkeypatch, content):
    put(project, "data/runtime_status.json", content)
    before = snapshot(project)
    assert run(project, monkeypatch, "--apply") == 1
    assert snapshot(project) == before


def test_preview_tolerates_completed_atomic_replace(project, monkeypatch):
    temporary = project / "data/market_data/bingx/perpetual/BTCUSDT/.30m.json.abcd.tmp"
    original = Path.stat
    def disappearing(path, *args, **kwargs):
        if path == temporary:
            raise FileNotFoundError(2, "Atomic replace завершён", str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", disappearing)
    assert run(project, monkeypatch) == 0


def test_archive_failure_leaves_manifest_and_never_reports_complete(project, monkeypatch):
    plan = cleanup.build_plan(project, "reset")
    original = Path.rename
    calls = 0
    def fail_second(path, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Тестовый отказ rename")
        return original(path, destination)
    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(OSError):
        cleanup.apply_plan(project, "reset", plan)
    archive = next((project / ".cache/cleanup-backups").iterdir())
    manifest = json.loads((archive / "manifest.json").read_text())
    assert not manifest["complete"] and len(manifest["moved"]) == 1
    for relative in manifest["files"]:
        assert (archive / relative).exists() or (project / relative).exists()


@pytest.mark.parametrize("apply,inspect_failure", [(False, False), (True, False), (True, True)])
def test_shell_wrapper_uses_podman_and_minimal_inventory_only(tmp_path, apply, inspect_failure):
    import os
    import shutil
    import subprocess
    bash = shutil.which("bash")
    assert bash, "Для проверки shell wrapper нужен bash внутри тестового контейнера"
    binary = put(tmp_path, "bin/podman", '''#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "$1" in
  image) exit 0 ;;
  ps) printf 'first\\nsecond\\n' ;;
  inspect)
    if [ "$FAKE_INSPECT_FAILURE" = 1 ]; then exit 42; fi
    printf '%s\\n' '{"State":{"Running":true},"Mounts":[]}' '{"State":{"Running":false},"Mounts":[]}' ;;
  run) cat > "$FAKE_INVENTORY" ;;
  *) exit 43 ;;
esac
''')
    binary.chmod(0o700)
    log = tmp_path / "calls.log"
    inventory = tmp_path / "inventory.json"
    environment = dict(os.environ, PATH=str(binary.parent) + os.pathsep + os.environ["PATH"],
                       FAKE_LOG=str(log), FAKE_INVENTORY=str(inventory),
                       FAKE_INSPECT_FAILURE="1" if inspect_failure else "0")
    script = Path(cleanup.__file__).with_name("clean_project.sh")
    arguments = [bash, str(script), "--mode", "reset"] + (["--apply"] if apply else [])
    result = subprocess.run(arguments, env=environment, capture_output=True, text=True, timeout=10)
    calls = log.read_text()
    if inspect_failure:
        assert result.returncode != 0 and "run --rm" not in calls
        return
    assert result.returncode == 0
    assert "run --rm -i --network none --read-only" in calls
    assert "--env-file" not in calls and "Config.Env" not in calls
    assert (":rw,z" if apply else ":ro,z") in calls
    value = json.loads(inventory.read_text())
    if apply:
        assert "inspect --format" in calls
        assert value == [{"State": {"Running": True}, "Mounts": []},
                         {"State": {"Running": False}, "Mounts": []}]
    else:
        assert "inspect" not in calls and value == []
