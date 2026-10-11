"""Проверка настоящих торговых логов без executor, аккаунта и биржевых запросов."""

from datetime import datetime
import json
import logging
import os
import re

from src.utils import logger as logs
from src.utils.log_context import get_log_context
from src.utils.log_store import EventReader


def test_trade_logging(tmp_path, monkeypatch):
    previous_dir = logs.LOG_DIR
    previous_context = get_log_context()
    previous_level = logging.getLogger().level
    try:
        with monkeypatch.context() as environment:
            environment.setenv("LOG_TEST_API_KEY", "synthetic-trade-key-2026")
            logs.clear_log_context()
            logs.configure_logging(tmp_path)
            logs.set_log_context(exchange="bingx", symbol="BTC-USDT", instance="btc-test", request="request-test")
            logs.log_trade(
                "Открыта BUY: Qty=0.1; Reason: Test Strategy; Conf: 0.95; raw=synthetic-trade-key-2026",
                order_id="open-test",
            )
            logs.log_trade(
                "Позиция закрыта: Qty=0.1; Причина: Take Profit Hit; Conf: 0.9; "
                "https://example.invalid/order?apiKey=unknown-key&token=unknown-token",
                order_id="close-test",
            )

            events = EventReader(tmp_path).events()["events"]
            assert len(events) == 2
            assert len({event["event_id"] for event in events}) == 2
            assert [event["order_id"] for event in events] == ["open-test", "close-test"]
            for event in events:
                assert event["schema_version"] == 1
                assert event["level"] == "INFO"
                assert event["category"] == "trade"
                assert event["logger"] == "trades"
                assert event["exchange"] == "bingx"
                assert event["symbol"] == "BTCUSDT"
                assert event["instance_id"] == "btc-test"
                assert event["request_id"] == "request-test"
                assert event["pid"] == os.getpid()
                assert event["timestamp"].endswith("Z")
                assert datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
            assert "Qty=0.1" in events[0]["message"]
            assert "Reason: Test Strategy" in events[0]["message"]
            assert "Conf: 0.95" in events[0]["message"]
            assert "закрыта" in events[1]["message"]
            assert "Причина: Take Profit Hit" in events[1]["message"]
            assert "Conf: 0.9" in events[1]["message"]

            text = (tmp_path / "trades.log").read_text(encoding="utf-8")
            lines = text.splitlines()
            assert len(lines) == 2
            for line, event in zip(lines, events):
                assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ", line)
                assert line == event["timestamp"][:19].replace("T", " ") + " | " + event["message"]
            copies = [json.loads(line) for path in tmp_path.glob("logs/events/*.jsonl")
                      for line in path.read_text(encoding="utf-8").splitlines()]
            assert len(copies) == 2
            assert {event["event_id"] for event in copies} == {event["event_id"] for event in events}
            assert [event["event_id"] for event in EventReader(tmp_path).events()["events"]] == [event["event_id"] for event in events]
            content = json.dumps(events) + json.dumps(copies) + text
            for secret in ("synthetic-trade-key-2026", "unknown-key", "unknown-token"):
                assert secret not in content
            assert "[REDACTED]" in content
    finally:
        logs.clear_log_context()
        logs.set_log_context(**previous_context)
        logs.configure_logging(previous_dir, level=previous_level)
