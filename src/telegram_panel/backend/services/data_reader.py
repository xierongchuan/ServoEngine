import json
import os
import logging
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, CHARTS_DIR, CONFIG_PATH

logger = logging.getLogger("panel.data_reader")


class DataReader:
    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        charts_dir: Path = CHARTS_DIR,
        config_path: Path = CONFIG_PATH,
    ) -> None:
        self.data_dir = data_dir
        self.charts_dir = charts_dir
        self.config_path = config_path

    def _read_json(self, path: Path, default: Any = None) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.debug("File not found: %s", path)
            return default
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in %s", path)
            return default

    def read_active_trades(self) -> dict:
        return self._read_json(self.data_dir / "active_trades.json", default={})

    def read_trade_history(self) -> list:
        return self._read_json(self.data_dir / "trade_history.json", default=[])

    def read_config(self) -> dict:
        return self._read_json(self.config_path, default={})

    def write_config(self, data: dict) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def read_journal(self) -> dict:
        return self._read_json(self.data_dir / "decision_journal.json", default={})

    def read_log_tail(self, path: Path, lines: int = 100) -> list[str]:
        try:
            with open(path, "rb") as f:
                # Seek from end to efficiently read last N lines
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return []

                block_size = 8192
                data = b""
                pos = size

                while pos > 0 and data.count(b"\n") <= lines:
                    read_size = min(block_size, pos)
                    pos -= read_size
                    f.seek(pos)
                    data = f.read(read_size) + data

                result = data.decode("utf-8", errors="replace").splitlines()
                return result[-lines:]
        except FileNotFoundError:
            logger.debug("Log file not found: %s", path)
            return []
        except Exception as e:
            logger.warning("Error reading log %s: %s", path, e)
            return []

    def list_charts(self) -> list[dict]:
        try:
            charts = []
            for f in self.charts_dir.iterdir():
                if f.suffix.lower() == ".png":
                    stat = f.stat()
                    charts.append({
                        "filename": f.name,
                        "modified": stat.st_mtime,
                        "size": stat.st_size,
                    })
            charts.sort(key=lambda c: c["modified"], reverse=True)
            return charts
        except FileNotFoundError:
            return []
