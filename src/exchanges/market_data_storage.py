"""Атомарная публикация JSON/PNG и изолированные рыночные snapshots."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from src.exchanges.market_data import StreamKey, canonical_symbol, candle_gaps, closed_candles, evaluate_snapshot
from src.utils.helpers import get_filename


def atomic_publish(filepath, writer):
    """Temp и target на одной ФС; прежний файл сохраняется при любой ошибке writer."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AtomicJsonStore:
    def __init__(self, filepath: str):
        self.filepath = str(filepath)

    @contextmanager
    def _lock(self, exclusive=False):
        Path(self.filepath).parent.mkdir(parents=True, exist_ok=True)
        # Блокируем стабильный sidecar, не inode, заменяемый os.replace.
        with open(self.filepath + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self, default=None):
        try:
            with open(self.filepath, "r", encoding="utf-8") as stream:
                return json.load(stream)
        except FileNotFoundError:
            return default

    def read(self, default: Any = None) -> Any:
        # Читателю достаточно атомарного inode; read-only panel не создаёт lock-файлы.
        return self._read(default)

    def _write(self, data):
        content = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        atomic_publish(self.filepath, lambda stream: stream.write(content))

    def write(self, data: Any) -> None:
        with self._lock(exclusive=True):
            self._write(data)

    def append(self, item: Any, key: Optional[str] = None) -> None:
        with self._lock(exclusive=True):
            data = self._read(default={} if key else [])
            if key:
                data[key] = item
            elif isinstance(data, list):
                data.append(item)
            else:
                data = [item]
            self._write(data)


def stream_snapshot_path(data_dir, key):
    key = StreamKey.from_value(key)
    return Path(data_dir) / "market_data" / key.exchange / key.market_type / get_filename(key.symbol) / f"{key.interval}.json"


def instance_prices_path(data_dir, instance_id, symbol):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", str(instance_id)):
        raise ValueError("Некорректный strategy instance ID")
    filename = get_filename(symbol)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", filename):
        raise ValueError("Некорректное имя файла символа")
    return Path(data_dir) / "instances" / str(instance_id) / "prices" / f"{filename}.json"


def _older_publication(previous, snapshot):
    if not previous:
        return False
    if previous.get("publisher_id") and previous.get("publisher_id") == snapshot.get("publisher_id"):
        return previous.get("version", 0) > snapshot.get("version", 0)
    previous_order = (previous.get("last_valid_update") or 0, previous.get("published_at") or 0)
    current_order = (snapshot.get("last_valid_update") or 0, snapshot.get("published_at") or 0)
    return previous_order > current_order


def publish_market_snapshot(data_dir, snapshot):
    key = StreamKey.from_value(snapshot["stream"])
    target = stream_snapshot_path(data_dir, key)
    store = AtomicJsonStore(target)
    # REST collector не должен затирать более свежий snapshot другого publisher.
    with store._lock(exclusive=True):
        previous = store._read()
        if _older_publication(previous, snapshot):
            return target
        store._write(snapshot)
    return target


def publish_strategy_view(data_dir, snapshot, *, instance_id=None, symbol=None, closed_only=False, legacy=True):
    key = StreamKey.from_value(snapshot["stream"])
    symbol = key.symbol if symbol is None else symbol
    rows = closed_candles(snapshot["candles"], key.interval) if closed_only else snapshot["candles"]
    if instance_id:
        target = instance_prices_path(data_dir, instance_id, symbol)
        store = AtomicJsonStore(target)
        metadata = AtomicJsonStore(str(target) + ".meta.json")
        # Envelope — атомарный источник истины; список остаётся legacy-совместимым зеркалом.
        with store._lock(exclusive=True):
            if not _older_publication(metadata.read(), snapshot):
                store._write(rows)
                metadata.write(evaluate_snapshot({**snapshot, "candles": rows, "gaps": candle_gaps(rows, key.interval),
                                                  "instance_id": instance_id, "view": "closed" if closed_only else "live"}))
    if legacy:
        filename = get_filename(symbol)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", filename):
            raise ValueError("Некорректное имя файла символа")
        AtomicJsonStore(Path(data_dir) / "prices" / f"{filename}.json").write(rows)
    return rows


def read_strategy_snapshot(data_dir, instance_id, symbol, *, stream_key=None, limit=None,
                           allow_stale=False, max_age_seconds=None):
    """Стратегия читает один атомарный envelope, не пару list/metadata разных ревизий."""
    target = instance_prices_path(data_dir, instance_id, symbol)
    value = AtomicJsonStore(str(target) + ".meta.json").read()
    if value is None:
        return None
    key = StreamKey.from_value(value["stream"])
    if value.get("instance_id") != instance_id or key.symbol != canonical_symbol(symbol):
        raise ValueError("Instance/symbol strategy snapshot не совпадает с запросом")
    if stream_key is not None and StreamKey.from_value(stream_key) != key:
        raise ValueError("Источник/таймфрейм strategy snapshot не совпадает с запросом")
    if value.get("view") != "closed" or len(closed_candles(value["candles"], key.interval)) != len(value["candles"]):
        value = {**value, "status": "error", "error": "Strategy reader требует только подтверждённые закрытые свечи"}
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("Некорректная длина истории стратегии")
        value = {**value, "candles": value["candles"][-limit:], "min_history": limit}
    value = {**value, "gaps": candle_gaps(value["candles"], key.interval)}
    value = evaluate_snapshot(value, max_age_seconds=max_age_seconds)
    return value if allow_stale or value["ready"] else None


def read_market_snapshot(data_dir, key, *, allow_stale=False, max_age_seconds=None):
    value = AtomicJsonStore(stream_snapshot_path(data_dir, key)).read()
    if value is None:
        return None
    if StreamKey.from_value(value["stream"]) != StreamKey.from_value(key):
        raise ValueError("Источник snapshot не совпадает с путём")
    value = evaluate_snapshot(value, max_age_seconds=max_age_seconds)
    return value if allow_stale or value["ready"] else None


def list_market_snapshots(data_dir):
    result = []
    for path in (Path(data_dir) / "market_data").glob("*/*/*/*.json"):
        try:
            value = AtomicJsonStore(path).read()
            key = StreamKey.from_value(value["stream"])
            if path == stream_snapshot_path(data_dir, key):
                result.append(evaluate_snapshot(value))
        except (ValueError, KeyError, TypeError, OSError):
            continue
    return result
