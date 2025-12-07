import unittest
import json
from src.core.predict import parse_response

class TestPredictFix(unittest.TestCase):
    def test_parse_response_valid(self):
        response = '{"action": "buy", "confidence": 0.8, "percentage": 0.5, "reason": "test"}'
        data = parse_response(response)
        self.assertEqual(data["action"], "buy")
        self.assertEqual(data["confidence"], 0.8)
        self.assertEqual(data["percentage"], 0.5)

    def test_parse_response_none_percentage(self):
        # Simulate response where percentage is null/None
        response = '{"action": "sell", "confidence": 0.9, "percentage": null, "reason": "test"}'
        data = parse_response(response)
        self.assertEqual(data["action"], "sell")
        self.assertEqual(data["percentage"], 1.0) # Should default to 1.0

    def test_parse_response_missing_percentage(self):
        # Simulate response where percentage is missing
        response = '{"action": "hold", "confidence": 0.5, "reason": "test"}'
        data = parse_response(response)
        self.assertEqual(data["percentage"], 1.0) # Should default to 1.0

    def test_parse_response_markdown_json(self):
        response = '```json\n{"action": "buy", "confidence": 0.7}\n```'
        data = parse_response(response)
        self.assertEqual(data["action"], "buy")
        self.assertEqual(data["confidence"], 0.7)

    def test_parse_response_invalid_json(self):
        response = 'invalid json'
        data = parse_response(response)
        self.assertEqual(data["action"], "hold")
        self.assertEqual(data["confidence"], 0.0)
        self.assertEqual(data["reason"], "Ошибка парсинга ответа DeepSeek")

if __name__ == '__main__':
    unittest.main()
