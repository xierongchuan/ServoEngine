import json
import os
import logging
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, CHARTS_DIR, CONFIG_PATH
from src.utils.log_context import SecretRedactor
from src.utils.log_store import EventReader

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

    def read_active_trades(self) -> dict | list:
        """Read active trades. Returns dict (expected format) or list (legacy/corrupted format)."""
        data = self._read_json(self.data_dir / "active_trades.json", default={})
        if not isinstance(data, (dict, list)):
            logger.warning("Unexpected active_trades format: %s, returning empty dict", type(data).__name__)
            return {}
        return data

    def read_trade_history(self) -> list:
        return self._read_json(self.data_dir / "trade_history.json", default=[])

    def read_config(self) -> dict:
        return self._read_json(self.config_path, default={})

    def write_config(self, data: dict) -> None:
        """Write config to file. For bind-mounted files, we can't use atomic rename,
        so we truncate and rewrite in place."""
        import tempfile
        # First write to a temp file to ensure we have valid JSON
        fd, tmp_path = tempfile.mkstemp(dir=str(self.config_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # For bind-mounted files, we need to write in place instead of rename
        # Read the temp file content and write directly to config
        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.unlink(tmp_path)
        except Exception:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def read_journal(self) -> dict:
        return self._read_json(self.data_dir / "decision_journal.json", default={})

    def read_log_tail(self, path: Path, lines: int = 100) -> list[str]:
        """Ограниченный хвост: не выдаёт частичные строки и очищает старую историю."""
        if not 1 <= lines <= 1000:
            raise ValueError("Недопустимое количество строк")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.data_dir.resolve()) or path.is_symlink():
            raise ValueError("Недопустимый источник журнала")
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as f:
                size = f.seek(0, 2)
                start = max(0, size - 1024 * 1024)
                f.seek(start)
                data = f.read(size - start)
            if start:
                boundary = data.find(b"\n")
                data = data[boundary + 1:] if boundary >= 0 else b""
            end = data.rfind(b"\n")
            if end < 0:
                return []
            redactor = SecretRedactor()
            return [redactor.text(line) for line in data[:end].decode("utf-8", errors="replace").splitlines()[-lines:]]
        except FileNotFoundError:
            return []

    def read_log_events(self, filters=None, cursor=None, limit=100) -> dict:
        result = EventReader(self.data_dir).events(filters, cursor, limit)
        # Новые значения секретов также скрываем в ранее записанных событиях.
        return SecretRedactor().clean(result)

    def read_log_sources(self) -> dict:
        return SecretRedactor().clean(EventReader(self.data_dir).sources())

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
