import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Preload config to avoid it consuming mocks during tests
try:
    import src.config
except ImportError:
    pass

class TestPromptGeneration(unittest.TestCase):
    def setUp(self):
        # Mock data
        self.mock_prices = [{"closePrice": "50000"} for _ in range(30)]
        self.mock_news = [{"timestamp": "10:00", "title": "Good News", "description": "Bitcoin is up"}]
        self.symbol = "BTC/USD"
        
        # Mock file operations
        self.file_patcher = patch('builtins.open', new_callable=unittest.mock.mock_open)
        self.mock_file = self.file_patcher.start()
        
        # Mock json.load
        self.json_patcher = patch('json.load')
        self.mock_json = self.json_patcher.start()
        
        # Configure side_effect to return prices then news, and then default to empty dict to prevent StopIteration
        # We use an iterator that yields our values and then repeats the last one or empty dict
        def side_effect(fp, *args, **kwargs):
            # Simple state machine or check file path if possible
            # But fp is a mock object from open(), so we can't easily check path
            # So we just rely on order: prices -> news
            if not hasattr(self, '_json_call_count'):
                self._json_call_count = 0
            self._json_call_count += 1
            
            if self._json_call_count == 1:
                return self.mock_prices
            elif self._json_call_count == 2:
                return self.mock_news
            else:
                return {}
                
        self.mock_json.side_effect = side_effect

    def tearDown(self):
        self.file_patcher.stop()
        self.json_patcher.stop()

    def test_prompt_news_disabled(self):
        """Test prompt generation when ENABLE_NEWS is False"""
        self._json_call_count = 0 # Reset counter
        # Patch ENABLE_NEWS in src.core.analyzer
        with patch('src.core.analyzer.ENABLE_NEWS', False):
            from src.core.analyzer import analyze_symbol
            
            result = analyze_symbol(self.symbol)
            prompt = result['prompt']
            
            # Assertions
            self.assertNotIn("### НОВОСТНОЙ ФОН", prompt)
            self.assertNotIn("СЦЕНАРИЙ А", prompt)
            self.assertNotIn("СЦЕНАРИЙ Б", prompt)
            self.assertIn("ЕСЛИ НЕТ ПОЗИЦИИ (ВХОД - ЧИСТАЯ ТЕХНИКА)", prompt)
            print("\n[SUCCESS] News Disabled Prompt Verified")

    def test_prompt_news_enabled(self):
        """Test prompt generation when ENABLE_NEWS is True"""
        self._json_call_count = 0 # Reset counter
        # Patch ENABLE_NEWS in src.core.analyzer
        with patch('src.core.analyzer.ENABLE_NEWS', True):
            from src.core.analyzer import analyze_symbol
            
            result = analyze_symbol(self.symbol)
            prompt = result['prompt']
            
            # Assertions
            self.assertIn("### НОВОСТНОЙ ФОН", prompt)
            self.assertIn("Good News", prompt)
            self.assertNotIn("СЦЕНАРИЙ А", prompt)
            self.assertNotIn("СЦЕНАРИЙ Б", prompt)
            self.assertIn("ЕСЛИ НЕТ ПОЗИЦИИ (ВХОД):", prompt)
            self.assertIn("Позитивные новости", prompt)
            print("\n[SUCCESS] News Enabled Prompt Verified")

if __name__ == '__main__':
    unittest.main()
