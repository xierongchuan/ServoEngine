import json
import os
import time
from unittest.mock import patch

import pytest

from scripts.container_healthcheck import check_bot, check_panel, main


@pytest.fixture
def status_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SERVO_RUNTIME_STATUS_PATH", raising=False)
    return tmp_path / "runtime_status.json"


def write_status(path, state="running", age=0, pid=None):
    path.write_text(json.dumps({
        "supervisor_pid": os.getpid() if pid is None else pid,
        "updated_at_ts": time.time() - age,
        "state": state,
    }), encoding="utf-8")


@pytest.mark.parametrize("state", ["running", "stopped", "starting", "stopping"])
def test_bot_accepts_live_supervisor(status_path, state):
    write_status(status_path, state=state)
    check_bot()


@pytest.mark.parametrize("state,age", [("crashed", 0), ("running", 100), ("running", -100)])
def test_bot_rejects_crashed_or_stale_status(status_path, state, age):
    write_status(status_path, state=state, age=age)
    with pytest.raises(ValueError):
        check_bot()


def test_bot_rejects_dead_supervisor(status_path):
    write_status(status_path)
    with (
        patch("scripts.container_healthcheck.os.kill", side_effect=ProcessLookupError),
        pytest.raises(ProcessLookupError),
    ):
        check_bot()


def test_bot_uses_custom_status_path(tmp_path, monkeypatch):
    path = tmp_path / "custom.json"
    write_status(path)
    monkeypatch.setenv("SERVO_RUNTIME_STATUS_PATH", str(path))
    check_bot()


def test_main_returns_failure_without_status(status_path):
    with patch("sys.argv", ["container_healthcheck.py", "bot"]):
        assert main() == 1


def test_panel_uses_configured_port(monkeypatch):
    monkeypatch.setenv("PANEL_PORT", "8090")
    with patch("scripts.container_healthcheck.urlopen") as request:
        with patch("scripts.container_healthcheck.json.load", return_value={"status": "ok"}):
            check_panel()
        request.assert_called_once_with("http://127.0.0.1:8090/api/health", timeout=3)


def test_panel_rejects_invalid_status():
    with (
        patch("scripts.container_healthcheck.urlopen"),
        patch("scripts.container_healthcheck.json.load", return_value={"status": "error"}),
        pytest.raises(ValueError),
    ):
        check_panel()
