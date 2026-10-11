"""Атомарный GRID-реестр; один writer на точный scope экземпляра."""

import fcntl
import json
import os
import tempfile
from pathlib import Path

from src.exchanges.errors import ExchangeStateUnavailableError
from src.utils.logger import error


class GridStateStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = open(str(self.path) + ".lock", "a", encoding="utf-8")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            error("❌ GRID: durable scope уже занят другим writer")
            raise ExchangeStateUnavailableError("GRID: экземпляр уже запущен") from exc

    def load(self):
        try:
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("Неизвестный формат реестра GRID")
            for field in ("identity", "signature", "state", "commands", "owned_positions"):
                if not isinstance(data.get(field), dict):
                    raise ValueError(f"Некорректное поле реестра GRID: {field}")
            ids = set()
            for cid, command in data["commands"].items():
                if not isinstance(command, dict) or command.get("client_order_id") != cid:
                    raise ValueError("GRID: команда не связана с durable client ID")
                oid = command.get("order_id")
                if oid is not None:
                    if not isinstance(oid, str) or not oid or oid in ids:
                        raise ValueError("GRID: некорректный/повторный order ID в реестре")
                    ids.add(oid)
            return data
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            error(f"❌ GRID: повреждён durable-реестр: {exc}")
            self.close()
            raise ExchangeStateUnavailableError("GRID: восстановление реестра невозможно") from exc

    def save(self, data):
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", delete=False) as handle:
                name = handle.name
                json.dump(data, handle, ensure_ascii=False, allow_nan=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError) as exc:
            error(f"❌ GRID: durable-запись не подтверждена: {exc}")
            raise ExchangeStateUnavailableError("GRID: запись реестра невозможна") from exc
        finally:
            if name is not None and os.path.exists(name):
                os.unlink(name)

    def close(self):
        self._lock.close()
