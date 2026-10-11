"""Регрессии логирования; только временные каталоги и синтетические секреты."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
import json
import logging
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.utils import logger as logs
from src.utils.log_context import SecretRedactor, get_log_context
from src.utils.log_store import EventReader, EventStore, InvalidLogCursor, LogQueryTimeout


def _worker(data_dir, number):
    logs.configure_logging(data_dir, max_bytes=1024)
    logs.clear_log_context()
    logs.set_log_context(symbol="BTC-USDT", instance_id=f"worker-{number}", exchange="bingx")
    for index in range(25):
        logs.log_trade(f"worker={number}; event={index}")


class StructuredLoggingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.previous_dir = logs.LOG_DIR
        self.env = patch.dict(os.environ, {"AUDIT_API_KEY": "fake-key-2026-long", "AUDIT_BOT_TOKEN": "123456:fake-bot-secret"})
        self.env.start()
        logs.clear_log_context()
        logs.configure_logging(self.path, level="DEBUG", max_bytes=1024)

    def tearDown(self):
        logs.clear_log_context()
        self.env.stop()
        logs.configure_logging(self.previous_dir)
        self.directory.cleanup()

    def read(self, **filters):
        return EventReader(self.path).events(filters, limit=200)["events"]

    def test_context_standard_capture_and_idempotent_setup(self):
        for _ in range(3):
            logs.configure_logging(self.path, level="DEBUG")
        logs.set_log_context(symbol="BTC-USDT", exchange="bingx", instance="btc-scalp", execution_mode="read_only")
        logs.setup_symbol_logger("BTC-USDT")
        logging.getLogger("src.exchanges.impl.bingx_client").info("Синтетический ответ API")
        logs.debug("Отладка", category="strategy", order_id="order-1", request_id="request-1")
        events = self.read()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["category"], "exchange")
        self.assertEqual(events[1]["level"], "DEBUG")
        self.assertEqual(events[1]["category"], "strategy")
        self.assertEqual(events[1]["order_id"], "order-1")
        self.assertEqual(events[0]["instance_id"], "btc-scalp")
        self.assertEqual(events[0]["execution_mode"], "read_only")
        self.assertEqual(events[0]["symbol"], "BTCUSDT")
        self.assertEqual(events[0]["pid"], os.getpid())
        self.assertTrue(events[0]["timestamp"].endswith("Z"))
        self.assertIsNotNone(datetime.fromisoformat(events[0]["timestamp"].replace("Z", "+00:00")).tzinfo)
        self.assertEqual([e["event_id"] for e in events], [e["event_id"] for e in self.read()])
        self.assertEqual(len([h for h in logging.getLogger().handlers if getattr(h, "_servo_capture", False)]), 1)
        self.assertEqual(len((self.path / "logs/BTCUSDT.log").read_text().splitlines()), 2)

    def test_redaction_formatted_exception_urls_env_and_console(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(handler)
        try:
            logs.protect_logging_handlers()
            try:
                raise RuntimeError("https://example.invalid/private?apiKey=fake-key-2026-long&signature=unknown-signature")
            except RuntimeError:
                logs.error('Bearer synthetic-bearer-value; token="unknown-token"; raw=fake-key-2026-long\nforged', exc_info=True)
            logging.getLogger("httpx").setLevel(logging.INFO)
            logging.getLogger("httpx").info("HTTP GET https://example.invalid/bot123456:fake-bot-secret/getUpdates")
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
        content = json.dumps(self.read()) + stream.getvalue()
        for file in self.path.rglob("*.jsonl"):
            content += file.read_text()
        content += (self.path / "steps.log").read_text()
        for secret in ("fake-key-2026-long", "unknown-signature", "synthetic-bearer-value", "unknown-token", "123456:fake-bot-secret"):
            self.assertNotIn(secret, content)
        self.assertIn("[REDACTED]", content)
        self.assertEqual(len((self.path / "steps.log").read_text().splitlines()), 1)
        redactor = SecretRedactor()
        once = redactor.text('apiKey=another-value&token="another-token"')
        self.assertEqual(redactor.text(once), once)

    def test_trade_threads_no_duplicate_handlers_or_lines(self):
        logs.set_log_context(symbol="ETHUSDT", instance_id="eth-grid")
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda i: logs.log_trade(f"trade-{i}", level="WARNING"), range(80)))
        events = self.read()
        self.assertEqual(len(events), 80)
        self.assertTrue(all(e["category"] == "trade" and e["level"] == "WARNING" for e in events))
        self.assertTrue(all(e["symbol"] == "ETHUSDT" for e in events))
        self.assertEqual(len((self.path / "trades.log").read_text().splitlines()), 80)

    def test_multiprocess_rotation_and_existing_history_untouched(self):
        old = self.path / "steps.log"
        old.write_text("старая история\n")
        context = multiprocessing.get_context("fork")
        processes = [context.Process(target=_worker, args=(str(self.path), i)) for i in range(4)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(process.exitcode, 0)
        events = self.read()
        self.assertEqual(len(events), 100)
        self.assertEqual(len({e["event_id"] for e in events}), 100)
        self.assertEqual(len({e["run_id"] for e in events}), 4)
        self.assertEqual(len({e["pid"] for e in events}), 4)
        files = list(self.path.glob("logs/events/*.jsonl"))
        self.assertGreater(len(files), 4)
        copies = [json.loads(line) for file in files for line in file.read_text().splitlines()]
        self.assertEqual({e["event_id"] for e in copies}, {e["event_id"] for e in events})
        self.assertEqual(len(copies), 100)
        self.assertEqual(old.read_text(), "старая история\n")
        self.assertEqual(len((self.path / "trades.log").read_text().splitlines()), 100)

    def test_async_context_isolation_and_timer_failure(self):
        logs.set_log_context(symbol="BTCUSDT")
        async def task(request):
            with logs.log_context(request_id=request):
                await asyncio.sleep(0)
                logs.info("Запрос", category="panel")
        async def run():
            await asyncio.gather(task("a"), task("b"))
        asyncio.run(run())
        self.assertNotIn("request_id", get_log_context())
        with self.assertRaises(RuntimeError):
            with logs.StageTimer("Ошибка этапа"):
                raise RuntimeError("synthetic")
        events = self.read()
        self.assertEqual({e["request_id"] for e in events[:2]}, {"a", "b"})
        self.assertEqual(events[-1]["level"], "ERROR")
        self.assertGreaterEqual(events[-1]["duration_ms"], 0)

    def test_filters_snapshot_and_live_cursor_no_mirrors(self):
        logs.set_log_context(exchange="bingx", symbol="BTC-USDT", instance_id="btc-scalp")
        for i in range(8):
            logs.info(f"order {i} 100%", category="order", order_id="id", request_id="req")
        reader = EventReader(self.path)
        filters = {"category": "order,trade", "symbol": "BTC-USDT", "exchange": "bingx", "instance": "btc-scalp", "search": "100%", "order": "id", "request": "req", "time_from": "2000-01-01T00:00:00.000000Z"}
        page = reader.events(filters, limit=3)
        self.assertEqual([e["message"] for e in page["events"]], [f"order {i} 100%" for i in (5, 6, 7)])
        logs.info("order 8 100%", category="order", order_id="id", request_id="req")
        history = reader.events(filters, page["next_cursor"], limit=3)
        oldest = reader.events(filters, history["next_cursor"], limit=3)
        ids = [e["event_id"] for p in (page, history, oldest) for e in p["events"]]
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8)
        live = reader.events(filters, page["live_cursor"], limit=3)
        self.assertEqual([e["message"] for e in live["events"]], ["order 8 100%"])
        self.assertEqual(reader.events(filters, live["live_cursor"])["events"], [])
        with self.assertRaises(InvalidLogCursor):
            reader.events({"category": "runtime"}, page["next_cursor"])
        with self.assertRaises(InvalidLogCursor):
            reader.events(filters, "not-a-cursor")
        self.assertEqual(reader.events({"search": "' OR 1=1 --"})["events"], [])
        self.assertEqual(len(reader.events()["events"]), 9)
        self.assertEqual([e["message"] for e in reader.events(filters, history["live_cursor"])["events"]], ["order 8 100%"])

    def test_live_catchup_pages_no_gaps_and_byte_bound(self):
        logs.info("начало")
        reader = EventReader(self.path)
        cursor = reader.events()["live_cursor"]
        for index in range(7):
            logs.info("large " + str(index) + "x" * 16000)
        seen = []
        while True:
            page = reader.events(cursor=cursor, limit=2)
            seen.extend(e["event_id"] for e in page["events"])
            cursor = page["live_cursor"]
            if not page["has_more"]:
                break
        self.assertEqual(len(set(seen)), 7)
        self.assertEqual(len(seen), 7)

    def test_duplicate_event_id_and_query_deadline(self):
        logs.info("событие")
        event = self.read()[0]
        EventStore(self.path).write(event)
        self.assertEqual(len(self.read()), 1)
        path = self.path / "logs/events.sqlite3"
        with sqlite3.connect(path) as connection:
            columns = "event_id,timestamp,level,category,source,message,payload"
            connection.executemany(f"INSERT INTO log_events ({columns}) VALUES (?,?,?,?,?,?,?)", [(f"extra-{i}", event["timestamp"], "INFO", "runtime", "system", "message", json.dumps({**event, "event_id": f"extra-{i}"})) for i in range(2000)])
        with self.assertRaises(LogQueryTimeout):
            EventReader(self.path, query_seconds=-1).events({"search": "not-found"})

    def test_unicode_search_and_live_byte_budget(self):
        logs.info("ОШИБКА Биржи", category="exchange")
        self.assertEqual(len(self.read(search="ошибка биржи")), 1)
        initial = EventReader(self.path).events()["live_cursor"]
        for index in range(80):
            try:
                raise RuntimeError("y" * 16000)
            except RuntimeError:
                logs.error(str(index) + "x" * 16000, exc_info=True)
        page = EventReader(self.path).events(cursor=initial, limit=200)
        self.assertTrue(page["has_more"])
        self.assertLess(sum(len(json.dumps(event).encode()) for event in page["events"]), 1100 * 1024)
        self.assertLess(len(page["events"]), 80)

    def test_standard_root_logging_capture(self):
        logging.info("Общее событие")
        events = self.read()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["logger"], "root")
        self.assertEqual(events[0]["category"], "runtime")

    def test_log_read_requests_do_not_self_trigger_live(self):
        logging.getLogger("panel.app").info("→ %s %s", "GET", "/api/logs/events")
        self.assertEqual(self.read(), [])


if __name__ == "__main__":
    unittest.main()
