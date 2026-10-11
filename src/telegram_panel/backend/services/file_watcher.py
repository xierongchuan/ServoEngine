import asyncio
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from ..config import DATA_DIR, CHARTS_DIR

logger = logging.getLogger("panel.file_watcher")
DEBOUNCE_INTERVAL = 0.1


class _Handler(FileSystemEventHandler):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self._pending = {}
        self._lock = threading.Lock()
        self._timer = None
        self._closed = False

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._dispatch(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._dispatch(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._dispatch(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._dispatch(event.src_path)
            self._dispatch(event.dest_path)

    def _dispatch(self, path: str) -> None:
        event_type = self._classify(path)
        if not event_type:
            return
        key = (event_type, Path(path).stem if event_type == "log_symbol" else event_type)
        with self._lock:
            if self._closed:
                return
            self._pending[key] = path
            if len(self._pending) > 512:
                self._pending.pop(next(iter(self._pending)))
            if self._timer is None:
                # Фиксированное окно: непрерывная запись не откладывает flush навсегда.
                self._timer = threading.Timer(DEBOUNCE_INTERVAL, self.flush)
                self._timer.daemon = True
                self._timer.start()

    def flush(self):
        with self._lock:
            pending, self._pending = self._pending, {}
            self._timer = None
            if self._closed:
                return
        for (event_type, _), path in pending.items():
            try:
                self._callback(event_type, path)
            except Exception:
                logger.warning("Не удалось передать уведомление об изменении файла")

    def close(self):
        with self._lock:
            self._closed = True
            if self._timer:
                self._timer.cancel()
            self._pending.clear()

    @staticmethod
    def _classify(path: str) -> str | None:
        p = Path(path)
        name = p.name.lower()
        if name in ("events.sqlite3", "events.sqlite3-wal") or p.suffix.lower() == ".jsonl":
            return "log_events"
        if name in ("active_trades.json", "trade_history.json"):
            return "trade_update"
        if name == "runtime_status.json":
            return "runtime_status"
        if p.suffix.lower() == ".json" and p.parent.name == "prices":
            return "data_update"
        if p.suffix.lower() == ".png":
            return "chart_update"
        if p.suffix.lower() == ".log":
            return "log_symbol" if p.parent.name == "logs" else "log_line"
        if name in ("bot_config.json", "active.json", "trading.json"):
            return "config_changed"
        if name == "decision_journal.json":
            return "journal_update"
        return None


class FileWatcher:
    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._observer = Observer()
        self._loop = loop
        self._ws_manager = None
        self._handler = _Handler(self._on_change)

    def set_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    def _on_change(self, event_type: str, path: str) -> None:
        if not self._ws_manager or not self._loop or self._loop.is_closed():
            return
        p = Path(path)
        if event_type == "log_events":
            data = {"changed": True}
        elif event_type == "log_symbol":
            data = {"source": p.stem}
        else:
            data = {"path": p.name}
        future = asyncio.run_coroutine_threadsafe(self._ws_manager.broadcast(event_type, data), self._loop)
        future.add_done_callback(self._observe_result)

    @staticmethod
    def _observe_result(future):
        if not future.cancelled() and future.exception():
            logger.warning("Не удалось отправить уведомление WebSocket")

    def start(self) -> None:
        for directory, recursive in ((DATA_DIR, True), (CHARTS_DIR, False)):
            if Path(directory).is_dir():
                self._observer.schedule(self._handler, str(directory), recursive=recursive)
                logger.info("Наблюдение за каталогом: %s", directory)
            else:
                logger.warning("Каталог наблюдения отсутствует: %s", directory)
        self._observer.start()

    def stop(self) -> None:
        self._handler.close()
        self._observer.stop()
        self._observer.join(timeout=5)
