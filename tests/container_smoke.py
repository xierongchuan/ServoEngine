"""Запускается внутри готового образа через stdin, без mount исходников."""

import importlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


def verify_image(kind):
    assert not Path("/app/.env").exists(), "В образ попал файл секретов"
    assert not Path("/app/src/telegram_panel/frontend/node_modules").exists()
    assert not list(Path("/app/src").rglob("__pycache__")), "В образ попали кеши хоста"
    os.environ["SERVO_RUNTIME_START_ON_BOOT"] = "0"
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    if kind == "bot":
        importlib.import_module("src.main")
        command = [sys.executable, "run.py"]
    else:
        importlib.import_module("src.telegram_panel.bot")
        command = [sys.executable, "-m", "src.telegram_panel.run_panel"]
        assert Path("/app/frontend/dist/index.html").is_file()

    process = subprocess.Popen(command)
    try:
        for _ in range(100):
            assert process.poll() is None, "Процесс завершился до проверки"
            try:
                if kind == "bot":
                    status = json.loads(Path("/app/data/runtime_status.json").read_text())
                    assert status["supervisor_pid"] == process.pid
                    assert status["state"] == "stopped"
                else:
                    with urlopen("http://127.0.0.1:8080/api/health", timeout=1) as response:
                        assert json.load(response) == {"status": "ok"}
                    with urlopen("http://127.0.0.1:8080/", timeout=1) as response:
                        assert "text/html" in response.headers["Content-Type"]
                        assert b"<html" in response.read()
                break
            except (OSError, AssertionError, ValueError):
                time.sleep(0.2)
        else:
            raise AssertionError("Контейнер не стал готов за 20 секунд")
        subprocess.run([sys.executable, "scripts/container_healthcheck.py", kind], check=True, timeout=5)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise AssertionError("Процесс не завершился по SIGTERM")
    # Uvicorn после штатного shutdown повторно отправляет себе исходный сигнал.
    expected_codes = {0, -signal.SIGTERM} if kind == "panel" else {0}
    assert process.returncode in expected_codes, f"Нештатный код завершения: {process.returncode}"
    print(f"✅ {kind}: автономный запуск, healthcheck и SIGTERM проверены")


verify_image(sys.argv[1])
