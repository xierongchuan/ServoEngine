"""Регрессии API/live без app lifespan, Telegram и реальных API."""

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.utils import logger as logs
from src.utils.log_context import CATEGORIES
from src.telegram_panel.backend.routes import logs as routes
from src.telegram_panel.backend.services.auth import get_current_user
from src.telegram_panel.backend.services.data_reader import DataReader
from src.telegram_panel.backend.services.file_watcher import _Handler
from src.telegram_panel.backend.ws import ConnectionManager


class LogsAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.previous_dir = logs.LOG_DIR
        self.previous_reader = routes.reader
        logs.clear_log_context()
        logs.configure_logging(self.path)
        routes.reader = DataReader(data_dir=self.path)
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_current_user] = lambda: {"user": {"id": 123}}
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        routes.reader = self.previous_reader
        logs.clear_log_context()
        logs.configure_logging(self.previous_dir)
        self.directory.cleanup()

    def test_events_metadata_legacy_and_normalization(self):
        logs.set_log_context(symbol="BTC-USDT", instance_id="btc-macdx", exchange="bingx")
        logs.info("Создан ордер", category="order", order_id="order", request_id="request")
        query = {"level": "INFO", "category": "order", "symbol": "BTC-USDT", "instance": "btc-macdx", "order": "order", "request": "request", "time_from": "2020-01-01T00:00:00Z"}
        response = self.client.get("/api/logs/events", params=query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(len(response.json()["events"]), 1)
        self.assertEqual(response.json()["events"][0]["category"], "order")
        sources = self.client.get("/api/logs/sources").json()
        self.assertEqual(sources["symbols"], ["BTCUSDT"])
        self.assertEqual(sources["instances"], ["btc-macdx"])
        self.assertEqual(sources["exchanges"], ["bingx"])
        self.assertTrue(sources["legacy_sources"])
        legacy = self.client.get("/api/logs/BTC-USDT").json()
        self.assertEqual(legacy["source"], "BTCUSDT")
        self.assertEqual(len(legacy["lines"]), 1)
        self.assertEqual(len(self.client.get("/api/logs/events").json()["events"]), 1)

    def test_all_categories_and_standard_rest_info_are_visible(self):
        logs.set_log_context(exchange="mexc", symbol="BTC-USDT", instance="btc-reader", execution_mode="read_only")
        for category in CATEGORIES:
            logs.info(f"Проверка категории {category}", category=category)
        # Синтетический LogRecord проверяет capture; сетевых вызовов здесь нет.
        logging.getLogger("src.exchanges.impl.mexc_transport").info("REST GET: синтетический ответ 200")
        response = self.client.get("/api/logs/events", params={"limit": 200})
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        self.assertEqual(len(events), len(CATEGORIES) + 1)
        self.assertEqual({event["category"] for event in events}, set(CATEGORIES))
        self.assertEqual(len({event["event_id"] for event in events}), len(events))
        standard = events[-1]
        self.assertEqual(standard["level"], "INFO")
        self.assertEqual(standard["category"], "exchange")
        self.assertEqual(standard["symbol"], "BTCUSDT")
        self.assertEqual(standard["instance_id"], "btc-reader")
        self.assertEqual(standard["execution_mode"], "read_only")
        for category in CATEGORIES:
            filtered = self.client.get("/api/logs/events", params={"category": category}).json()["events"]
            self.assertEqual(len(filtered), 2 if category == "exchange" else 1)
            self.assertTrue(all(event["category"] == category for event in filtered))
        sources = self.client.get("/api/logs/sources").json()
        self.assertEqual(set(sources["categories"]), set(CATEGORIES))
        self.assertEqual(sources["exchanges"], ["mexc"])
        self.assertEqual(sources["instances"], ["btc-reader"])

    def test_limits_bad_filters_cursors_times_and_traversal(self):
        logs.info("событие")
        for query in ({"limit": 201}, {"limit": 0}, {"category": "unknown"}, {"level": "TRACE"}, {"search": "x" * 257}, {"time_from": "yesterday"}, {"time_from": "2025-01-01T00:00:00"}, {"time_from": "2025-01-01T00:00:00Z", "time_to": "2020-01-01T00:00:00Z"}):
            self.assertEqual(self.client.get("/api/logs/events", params=query).status_code, 422)
        self.assertEqual(self.client.get("/api/logs/events?cursor=bad").status_code, 409)
        self.assertEqual(self.client.get("/api/logs/BTC%5CUSDT").status_code, 400)
        with self.assertRaises(ValueError):
            routes.reader.read_log_tail(self.path.parent / "outside.log")

    def test_legacy_partial_line_errors_and_secrets(self):
        path = self.path / "steps.log"
        path.write_text("готовая строка\napiKey=old-secret&token=old-token\nнезавершённая", encoding="utf-8")
        response = self.client.get("/api/logs/system")
        self.assertEqual(response.status_code, 200)
        content = response.json()["lines"]
        self.assertEqual(len(content), 2)
        self.assertNotIn("old-secret", str(content))
        self.assertNotIn("old-token", str(content))
        self.assertNotIn("незавершённая", str(content))
        with patch.object(routes.reader, "read_log_tail", side_effect=OSError("synthetic sensitive details")):
            response = self.client.get("/api/logs/system")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("sensitive", response.text)
        outside = self.path.parent / f"outside-{os.getpid()}.log"
        outside.write_text("outside\n")
        try:
            path.unlink()
            path.symlink_to(outside)
            self.assertEqual(self.client.get("/api/logs/system").status_code, 422)
        finally:
            outside.unlink()

    def test_sql_reads_run_outside_request_event_loop(self):
        # Endpoint должен быть sync: FastAPI выполнит его в worker threadpool.
        self.assertFalse(asyncio.iscoroutinefunction(routes.get_events))
        threads = []
        original = routes.reader.read_log_events
        def read(*args):
            threads.append(threading.current_thread().name)
            return original(*args)
        with patch.object(routes.reader, "read_log_events", side_effect=read):
            self.assertEqual(self.client.get("/api/logs/events").status_code, 200)
        self.assertTrue(all("AnyIO worker" in name for name in threads))

    def test_history_snapshot_live_and_filter_mismatch(self):
        for i in range(5):
            logs.info(f"event-{i}")
        page = self.client.get("/api/logs/events?limit=2").json()
        logs.info("event-5")
        older = self.client.get("/api/logs/events", params={"cursor": page["next_cursor"], "limit": 2}).json()
        self.assertEqual([e["message"] for e in older["events"]], ["event-1", "event-2"])
        live = self.client.get("/api/logs/events", params={"cursor": page["live_cursor"]}).json()
        self.assertEqual([e["message"] for e in live["events"]], ["event-5"])
        mismatch = self.client.get("/api/logs/events", params={"cursor": page["next_cursor"], "level": "ERROR"})
        self.assertEqual(mismatch.status_code, 409)


class WatcherTests(unittest.TestCase):
    def test_trailing_flush_classification_and_shutdown(self):
        seen = []
        handler = _Handler(lambda event, path: seen.append((event, path)))
        try:
            with patch("src.telegram_panel.backend.services.file_watcher.threading.Timer"):
                handler._dispatch("/data/steps.log")
                handler._dispatch("/data/steps.log")
                handler._dispatch("/data/logs/events.sqlite3-wal")
                handler._dispatch("/data/logs/events/pid-1.jsonl")
                handler.flush()
            self.assertEqual([event for event, _ in seen], ["log_line", "log_events"])
            self.assertIsNone(handler._classify("/data/logs/events.sqlite3-shm"))
            self.assertEqual(handler._classify("/data/logs/BTCUSDT.log"), "log_symbol")
            handler.close()
            handler._dispatch("/data/steps.log")
            handler.flush()
            self.assertEqual(len(seen), 2)
        finally:
            handler.close()


class FakeSocket:
    def __init__(self, delay=0, callback=None):
        self.messages = []
        self.delay = delay
        self.callback = callback
        self.closed = False
    async def accept(self):
        pass
    async def send_text(self, message):
        if self.callback:
            self.callback()
        await asyncio.sleep(self.delay)
        self.messages.append(json.loads(message))
    async def close(self, code=1000):
        self.closed = True


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_disconnect_and_slow_client_timeout(self):
        manager = ConnectionManager(send_timeout=0.02)
        slow, fast = FakeSocket(delay=0.2), FakeSocket()
        changing = FakeSocket(callback=lambda: manager.disconnect(changing))
        for socket in (slow, fast, changing):
            await manager.connect(socket)
        await manager.broadcast("log_events", {"changed": True})
        await asyncio.sleep(0.06)
        self.assertEqual(fast.messages[0]["type"], "log_events")
        self.assertNotIn(slow, manager._connections)
        self.assertNotIn(changing, manager._connections)
        self.assertTrue(slow.closed)
        manager.disconnect(fast)
        await asyncio.sleep(0)

    async def test_bounded_queue_serialized_order_and_redaction(self):
        manager = ConnectionManager(queue_size=2, send_timeout=0.05)
        overflow = FakeSocket(delay=0.1)
        await manager.connect(overflow)
        for i in range(3):
            await manager.broadcast("update", {"i": i})
        self.assertNotIn(overflow, manager._connections)
        await asyncio.sleep(0.01)
        self.assertTrue(overflow.closed)
        fast = FakeSocket()
        await manager.connect(fast)
        await manager.broadcast("first", {"api_key": "fake-sensitive"})
        await manager.broadcast("second", {})
        await asyncio.sleep(0.02)
        self.assertEqual([item["type"] for item in fast.messages], ["first", "second"])
        self.assertEqual(fast.messages[0]["data"]["api_key"], "[REDACTED]")
        manager.disconnect(fast)
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
