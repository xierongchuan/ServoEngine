"""News on/off через runtime config с настоящим instance source envelope."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.analyzer import analyze_symbol
from src.exchanges.market_data_storage import AtomicJsonStore
from src.utils.helpers import get_filename
from market_data_fixtures import FixedClock, publish_instance, runtime_config


class TestPromptGeneration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = FixedClock()
        fixed_time = patch("src.exchanges.market_data.time.time", self.clock)
        fixed_time.start()
        self.addCleanup(fixed_time.stop)
        self.symbol = "BTC/USD"
        self.config = runtime_config(self.temp.name, history=30, instance="prompt")
        self.key, self.snapshot = publish_instance(self.config, self.symbol, self.clock)
        filename = get_filename(self.symbol) + ".json"
        AtomicJsonStore(Path(self.temp.name) / "news" / filename).write([
            {"timestamp": "10:00", "title": "Good News", "description": "Bitcoin is up"},
        ])
        AtomicJsonStore(Path(self.temp.name) / "prices" / filename).write([{"closePrice": 999999}])
        import src.prompts.builder as builder
        builder._block_cache.clear()

    def _analyze(self, news):
        result = analyze_symbol(self.symbol, config={**self.config, "ENABLE_NEWS": news})
        self.assertEqual(result["market_data"]["stream"], self.key.to_dict())
        self.assertEqual(result["market_data"]["instance_id"], "prompt")
        self.assertEqual(result["current_price"], self.snapshot["candles"][-2]["closePrice"])
        self.assertNotIn("999999", result["prompt"])
        return result["prompt"]

    def test_prompt_news_disabled(self):
        prompt = self._analyze(False)
        self.assertNotIn("НОВОСТНОЙ ФОН", prompt)
        self.assertIn("РОЛЬ И ЗАДАЧА", prompt)
        self.assertTrue("СТРАТЕГИЯ" in prompt or "РЕЖИМ" in prompt or "STRATEGY" in prompt)
        self.assertIn("ФОРМАТ ОТВЕТА", prompt)

    def test_prompt_news_enabled(self):
        prompt = self._analyze(True)
        self.assertIn("НОВОСТНОЙ ФОН", prompt)
        self.assertIn("Good News", prompt)
        self.assertIn("РОЛЬ И ЗАДАЧА", prompt)
        self.assertTrue("СТРАТЕГИЯ" in prompt or "РЕЖИМ" in prompt or "STRATEGY" in prompt)


if __name__ == "__main__":
    unittest.main()
