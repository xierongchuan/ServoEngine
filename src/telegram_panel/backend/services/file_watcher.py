import asyncio
import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from ..config import DATA_DIR, CHARTS_DIR

logger = logging.getLogger("panel.file_watcher")

# Minimum time between events on the same file (seconds)
DEBOUNCE_INTERVAL = 0.1


class _Handler(FileSystemEventHandler):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self._last_event: dict[str, float] = {}

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._dispatch(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._dispatch(event.src_path)

    def _dispatch(self, path: str) -> None:
        now = time.monotonic()
        last = self._last_event.get(path, 0)
        if now - last < DEBOUNCE_INTERVAL:
            return
        self._last_event[path] = now

        event_type = self._classify(path)
        if event_type:
            self._callback(event_type, path)

    @staticmethod
    def _classify(path: str) -> str | None:
        p = Path(path)
        name = p.name.lower()

        if name == "active_trades.json" or name == "trade_history.json":
            return "trade_update"
        if p.suffix.lower() == ".png":
            return "chart_update"
        if p.suffix.lower() == ".log":
            return "log_line"
        if name == "bot_config.json":
            return "config_changed"
        if name == "decision_journal.json":
            return "journal_update"
        return None


class FileWatcher:
    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._observer = Observer()
        self._loop = loop
        self._ws_manager = None

    def set_ws_manager(self, ws_manager) -> None:
        self._ws_manager = ws_manager

    def _on_change(self, event_type: str, path: str) -> None:
        if not self._ws_manager or not self._loop:
            return
        data = {"path": Path(path).name}
        asyncio.run_coroutine_threadsafe(
            self._ws_manager.broadcast(event_type, data),
            self._loop,
        )

    def start(self) -> None:
        handler = _Handler(self._on_change)
        data_dir = str(DATA_DIR)
        charts_dir = str(CHARTS_DIR)

        # Watch data/ directory
        try:
            self._observer.schedule(handler, data_dir, recursive=True)
            logger.info("Watching directory: %s", data_dir)
        except FileNotFoundError:
            logger.warning("Data directory not found: %s", data_dir)

        # Watch charts/ directory
        try:
            self._observer.schedule(handler, charts_dir, recursive=False)
            logger.info("Watching directory: %s", charts_dir)
        except FileNotFoundError:
            logger.warning("Charts directory not found: %s", charts_dir)

        self._observer.start()
        logger.info("File watcher started")

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)
        logger.info("File watcher stopped")
