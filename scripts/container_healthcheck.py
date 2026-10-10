"""Проверки контейнеров без сторонних зависимостей и обращения к бирже."""

import json
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen


def check_bot():
    data_dir = Path(os.getenv("SERVO_DATA_DIR", "data"))
    status_path = Path(os.getenv("SERVO_RUNTIME_STATUS_PATH", str(data_dir / "runtime_status.json")))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    os.kill(int(status["supervisor_pid"]), 0)
    age = time.time() - float(status["updated_at_ts"])
    # Остановка торговли из панели штатна; падение runtime — нет.
    if not 0 <= age < 90 or status["state"] == "crashed":
        raise ValueError("Supervisor не обновляет статус или торговый runtime упал")


def check_panel():
    port = int(os.getenv("PANEL_PORT", "8080"))
    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
        if json.load(response) != {"status": "ok"}:
            raise ValueError("Панель вернула некорректный статус")


def main():
    try:
        {"bot": check_bot, "panel": check_panel}[sys.argv[1]]()
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"❌ Проверка контейнера не пройдена: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
