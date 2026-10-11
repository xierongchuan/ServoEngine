"""Smoke настоящего read_only spawn: только одноразовый контейнер без mounts/сети/ключей."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    assert not Path("/app/.env").exists(), "В образ попал файл секретов"
    assert not any(value for key, value in os.environ.items()
                   if key.endswith(("API_KEY", "SECRET_KEY", "BOT_TOKEN"))), "Для проверки ключи запрещены"
    root = Path("/app")
    mounts = [Path(line.split()[4]) for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    assert not any(mount == root or mount.is_relative_to(root) for mount in mounts), "Mounts проекта/данных запрещены"
    data = root / "data"
    assert not list(data.rglob("*.sqlite3")), "Проверка требует чистые контейнерные данные"
    config = root / "config"
    trading_path = config / "trading.json"
    trading = json.loads(trading_path.read_text())
    trading["execution"].update(exchange="bingx", market_type="perpetual", execution_mode="read_only")
    trading["features"]["enable_news"] = False
    trading_path.write_text(json.dumps(trading))
    active = {"strategy": "MACDX", "strategy_instances": [
        {"id": "smoke_btc", "symbol": "BTCUSDT", "strategy": "MACDX", "profile": "macdx_5m", "enabled": True}],
        "symbols": {"bingx": ["BTCUSDT"]}, "disabled_symbols": []}
    (config / "active.json").write_text(json.dumps(active))
    os.environ["SERVO_DATA_DIR"] = str(data)
    os.environ["SERVO_RUNTIME_START_ON_BOOT"] = "1"
    from src.utils.logger import configure_logging, info
    configure_logging(data)
    info("Проверка запуска read_only после создания логовой БД")
    assert (data / "logs" / "events.sqlite3").exists()

    process = subprocess.Popen([sys.executable, "-m", "src.core.runtime_supervisor"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    status_path = data / "runtime_status.json"
    try:
        stable = 0
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            assert process.poll() is None, "Supervisor завершился до проверки"
            try:
                status = json.loads(status_path.read_text())
                assert status["state"] != "crashed", status.get("last_error")
                workers = json.loads((data / "symbol_runtime_status.json").read_text())
                healthy = (status["state"] == "running" and status["runtime_pid"] > 0
                           and status["execution"]["execution_mode"] == "read_only"
                           and workers["runtime_pid"] == status["runtime_pid"]
                           and len(workers["workers"]) == 1
                           and workers["workers"][0]["id"] == "smoke_btc"
                           and workers["workers"][0]["alive"])
                stable = stable + 1 if healthy else 0
                if stable >= 8:
                    break
            except (OSError, ValueError, KeyError):
                stable = 0
            time.sleep(0.5)
        else:
            raise AssertionError("Runtime/worker не стал готов за 40 секунд")
        assert (data / "exchange_rate_limits.sqlite3").exists()
        assert not list(data.glob("execution*.sqlite3")), "Read_only создал execution ledger"
    finally:
        process.terminate()
        try:
            process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise AssertionError("Supervisor не завершился по SIGTERM")
    assert process.returncode == 0, f"Нештатное завершение: {process.returncode}"
    status = json.loads(status_path.read_text())
    assert status["state"] == "stopped" and status["runtime_pid"] is None
    print("✅ Настоящий read_only runtime и worker запущены после создания events.sqlite3; SIGTERM проверен")
    print("ℹ️ Сеть отключена: получение биржевых свечей не проверяется этим smoke-тестом")


if __name__ == "__main__":
    main()
