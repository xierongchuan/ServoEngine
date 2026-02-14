"""
Тесты для модуля отслеживания производительности (performance.py).
"""

import json
import os
import tempfile
import shutil
from datetime import datetime, timedelta

import pytest

from src.core.performance import PerformanceTracker, get_performance_tracker


@pytest.fixture
def temp_data_dir():
    """Создает временную директорию для тестовых данных."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_trades():
    """Создает пример истории сделок."""
    base_time = datetime.now()

    return [
        # Profitable trades with high score
        {
            "symbol": "BTC-USDT",
            "side": "LONG",
            "entry_price": 97000.0,
            "open_time": (base_time - timedelta(hours=10)).isoformat(),
            "close_time": (base_time - timedelta(hours=9)).isoformat(),
            "last_pnl": 45.2,
            "entry_regime": "TRENDING",
            "entry_score": 8,
            "entry_quality": 0.75,
            "entry_rsi": 42,
            "entry_atr": 350.0,
            "entry_volume_ratio": 1.3
        },
        {
            "symbol": "BTC-USDT",
            "side": "LONG",
            "entry_price": 97500.0,
            "open_time": (base_time - timedelta(hours=8)).isoformat(),
            "close_time": (base_time - timedelta(hours=7)).isoformat(),
            "last_pnl": 32.1,
            "entry_regime": "TRENDING",
            "entry_score": 7,
            "entry_quality": 0.68,
            "entry_rsi": 38,
            "entry_atr": 340.0,
            "entry_volume_ratio": 1.5
        },
        # Loss with low score
        {
            "symbol": "BTC-USDT",
            "side": "SHORT",
            "entry_price": 98000.0,
            "open_time": (base_time - timedelta(hours=6)).isoformat(),
            "close_time": (base_time - timedelta(hours=5)).isoformat(),
            "last_pnl": -28.5,
            "entry_regime": "RANGING",
            "entry_score": 4,
            "entry_quality": 0.45,
            "entry_rsi": 68,
            "entry_atr": 320.0,
            "entry_volume_ratio": 0.6
        },
        # Another loss with low score and low volume
        {
            "symbol": "BTC-USDT",
            "side": "LONG",
            "entry_price": 97200.0,
            "open_time": (base_time - timedelta(hours=4)).isoformat(),
            "close_time": (base_time - timedelta(hours=3)).isoformat(),
            "last_pnl": -15.3,
            "entry_regime": "RANGING",
            "entry_score": 5,
            "entry_quality": 0.50,
            "entry_rsi": 45,
            "entry_atr": 330.0,
            "entry_volume_ratio": 0.7
        },
        # Profitable trade
        {
            "symbol": "ETH-USDT",
            "side": "LONG",
            "entry_price": 3600.0,
            "open_time": (base_time - timedelta(hours=2)).isoformat(),
            "close_time": (base_time - timedelta(hours=1)).isoformat(),
            "last_pnl": 18.7,
            "entry_regime": "TRENDING",
            "entry_score": 6,
            "entry_quality": 0.62,
            "entry_rsi": 40,
            "entry_atr": 50.0,
            "entry_volume_ratio": 1.1
        }
    ]


@pytest.fixture
def tracker_with_data(temp_data_dir, sample_trades, monkeypatch):
    """Создает PerformanceTracker с тестовыми данными."""
    # Patch DATA_DIR
    monkeypatch.setattr('src.core.performance.DATA_DIR', temp_data_dir)

    # Create trade_history.json
    history_file = os.path.join(temp_data_dir, "trade_history.json")
    with open(history_file, 'w') as f:
        json.dump(sample_trades, f, indent=2)

    tracker = PerformanceTracker()
    return tracker


class TestPerformanceTracker:
    """Тесты для класса PerformanceTracker."""

    def test_initialization_empty(self, temp_data_dir, monkeypatch):
        """Тест инициализации с пустой историей."""
        monkeypatch.setattr('src.core.performance.DATA_DIR', temp_data_dir)
        tracker = PerformanceTracker()
        assert tracker.history == []
        assert len(tracker.history) == 0

    def test_initialization_with_data(self, tracker_with_data, sample_trades):
        """Тест инициализации с данными."""
        assert len(tracker_with_data.history) == len(sample_trades)

    def test_load_invalid_json(self, temp_data_dir, monkeypatch):
        """Тест обработки невалидного JSON."""
        monkeypatch.setattr('src.core.performance.DATA_DIR', temp_data_dir)

        # Create invalid JSON file
        history_file = os.path.join(temp_data_dir, "trade_history.json")
        with open(history_file, 'w') as f:
            f.write("{invalid json")

        tracker = PerformanceTracker()
        assert tracker.history == []

    def test_filter_trades_all(self, tracker_with_data):
        """Тест фильтрации всех сделок."""
        trades = tracker_with_data._filter_trades()
        assert len(trades) == 5

    def test_filter_trades_by_symbol(self, tracker_with_data):
        """Тест фильтрации по символу."""
        btc_trades = tracker_with_data._filter_trades(symbol="BTC-USDT")
        assert len(btc_trades) == 4

        eth_trades = tracker_with_data._filter_trades(symbol="ETH-USDT")
        assert len(eth_trades) == 1

    def test_filter_trades_last_n(self, tracker_with_data):
        """Тест ограничения количества сделок."""
        trades = tracker_with_data._filter_trades(last_n=3)
        assert len(trades) == 3

    def test_is_win(self, tracker_with_data):
        """Тест определения прибыльной сделки."""
        win_trade = {"last_pnl": 45.2}
        loss_trade = {"last_pnl": -28.5}
        zero_trade = {"last_pnl": 0}

        assert tracker_with_data._is_win(win_trade) is True
        assert tracker_with_data._is_win(loss_trade) is False
        assert tracker_with_data._is_win(zero_trade) is False

    def test_calculate_win_rate(self, tracker_with_data, sample_trades):
        """Тест расчета винрейта."""
        trades = tracker_with_data._filter_trades()
        win_rate = tracker_with_data._calculate_win_rate(trades)

        # 3 wins out of 5 trades = 60%
        assert win_rate == 0.6

    def test_calculate_avg_pnl(self, tracker_with_data):
        """Тест расчета среднего PnL."""
        trades = tracker_with_data._filter_trades()
        avg_pnl = tracker_with_data._calculate_avg_pnl(trades)

        # (45.2 + 32.1 - 28.5 - 15.3 + 18.7) / 5 = 10.44
        assert abs(avg_pnl - 10.44) < 0.01

    def test_calculate_avg_hold_time(self, tracker_with_data):
        """Тест расчета среднего времени удержания."""
        trades = tracker_with_data._filter_trades()
        avg_hold = tracker_with_data._calculate_avg_hold_time(trades)

        # All trades held for 1 hour
        assert abs(avg_hold - 1.0) < 0.1

    def test_calculate_streak_wins(self, tracker_with_data):
        """Тест расчета стрика побед."""
        # Last trade is a win, so streak should be 1
        streak = tracker_with_data._calculate_streak(tracker_with_data.history)
        assert streak == 1

    def test_calculate_streak_losses(self):
        """Тест расчета стрика проигрышей."""
        trades = [
            {"last_pnl": 10.0},
            {"last_pnl": -5.0},
            {"last_pnl": -8.0},
            {"last_pnl": -3.0}
        ]

        class DummyTracker:
            def _is_win(self, trade):
                return trade.get("last_pnl", 0) > 0

        tracker = PerformanceTracker()
        streak = tracker._calculate_streak(trades)
        assert streak == -3

    def test_group_by_regime(self, tracker_with_data):
        """Тест группировки по режиму."""
        grouped = tracker_with_data._group_by_regime(tracker_with_data.history)

        assert "TRENDING" in grouped
        assert "RANGING" in grouped
        assert len(grouped["TRENDING"]) == 3
        assert len(grouped["RANGING"]) == 2

    def test_group_by_score_range(self, tracker_with_data):
        """Тест группировки по диапазону скора."""
        grouped = tracker_with_data._group_by_score_range(tracker_with_data.history)

        assert len(grouped["4-5"]) == 2
        assert len(grouped["6-7"]) == 2
        assert len(grouped["8+"]) == 1

    def test_get_stats(self, tracker_with_data):
        """Тест получения общей статистики."""
        stats = tracker_with_data.get_stats()

        assert stats["total_trades"] == 5
        assert stats["win_rate"] == 0.6
        assert abs(stats["avg_pnl"] - 10.44) < 0.01
        assert abs(stats["avg_hold_time_hours"] - 1.0) < 0.1
        assert stats["streak"] == 1

        assert "TRENDING" in stats["by_regime"]
        assert "RANGING" in stats["by_regime"]

        assert "4-5" in stats["by_score_range"]
        assert "6-7" in stats["by_score_range"]
        assert "8+" in stats["by_score_range"]

    def test_get_stats_by_symbol(self, tracker_with_data):
        """Тест статистики по символу."""
        btc_stats = tracker_with_data.get_stats(symbol="BTC-USDT")
        assert btc_stats["total_trades"] == 4

        eth_stats = tracker_with_data.get_stats(symbol="ETH-USDT")
        assert eth_stats["total_trades"] == 1

    def test_get_stats_last_n(self, tracker_with_data):
        """Тест статистики для последних N сделок."""
        stats = tracker_with_data.get_stats(last_n=3)
        assert stats["total_trades"] == 3

    def test_get_stats_empty(self):
        """Тест статистики при пустой истории."""
        tracker = PerformanceTracker()
        stats = tracker.get_stats()

        assert stats["total_trades"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["avg_pnl"] == 0.0
        assert stats["by_regime"] == {}
        assert stats["by_score_range"] == {}

    def test_get_recent_performance(self, tracker_with_data):
        """Тест упрощенной статистики."""
        perf = tracker_with_data.get_recent_performance(last_n=5)

        assert perf["total_trades"] == 5
        assert perf["win_rate"] == 0.6
        assert abs(perf["avg_pnl"] - 10.44) < 0.01
        assert perf["streak"] == 1

    def test_should_adjust_thresholds_insufficient_data(self, tracker_with_data, monkeypatch):
        """Тест предложений без достаточных данных."""
        # Set min_trades_for_analysis to 100
        tracker_with_data.config["min_trades_for_analysis"] = 100

        suggestions = tracker_with_data.should_adjust_thresholds()
        # Should have no suggestions due to insufficient data
        assert len(suggestions) == 0

    def test_singleton_pattern(self):
        """Тест паттерна singleton."""
        tracker1 = get_performance_tracker()
        tracker2 = get_performance_tracker()

        assert tracker1 is tracker2


class TestPerformanceTrackerEdgeCases:
    """Тесты крайних случаев."""

    def test_malformed_trade_data(self, temp_data_dir, monkeypatch):
        """Тест обработки некорректных данных сделок."""
        monkeypatch.setattr('src.core.performance.DATA_DIR', temp_data_dir)

        malformed_trades = [
            {"symbol": "BTC-USDT"},  # Missing fields
            {"last_pnl": "not_a_number"},  # Invalid PnL
            {"entry_score": "invalid"},  # Invalid score
            None,  # Null trade
        ]

        history_file = os.path.join(temp_data_dir, "trade_history.json")
        with open(history_file, 'w') as f:
            json.dump(malformed_trades, f)

        tracker = PerformanceTracker()
        stats = tracker.get_stats()

        # Should handle gracefully without crashing
        assert stats["total_trades"] >= 0

    def test_missing_timestamps(self, temp_data_dir, monkeypatch):
        """Тест обработки отсутствующих временных меток."""
        monkeypatch.setattr('src.core.performance.DATA_DIR', temp_data_dir)

        trades = [
            {"symbol": "BTC-USDT", "last_pnl": 10.0},  # No timestamps
        ]

        history_file = os.path.join(temp_data_dir, "trade_history.json")
        with open(history_file, 'w') as f:
            json.dump(trades, f)

        tracker = PerformanceTracker()
        stats = tracker.get_stats()

        # avg_hold_time should be 0 when no valid timestamps
        assert stats["avg_hold_time_hours"] == 0.0

    def test_dict_history_fallback(self, temp_data_dir, monkeypatch):
        """Тест обработки истории в виде dict вместо list."""
        monkeypatch.setattr('src.core.performance.DATA_DIR', temp_data_dir)

        # Write dict instead of list
        history_file = os.path.join(temp_data_dir, "trade_history.json")
        with open(history_file, 'w') as f:
            json.dump({"invalid": "format"}, f)

        tracker = PerformanceTracker()
        assert tracker.history == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
