"""Поведенческие тесты дедупликации истории сделок."""

import json

import pytest


@pytest.fixture
def tracker_with_history(tmp_path, monkeypatch):
    import src.core.trade_tracker as tracker_module

    history_path = tmp_path / "trade_history.json"
    active_path = tmp_path / "active_trades.json"
    history_path.write_text(json.dumps([
        {"dealId": "known-string", "symbol": "BTCUSDT"},
        {"dealId": 123456, "symbol": "ETHUSDT"},
    ]), encoding="utf-8")
    active_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tracker_module, "HISTORY_FILE", str(history_path))
    monkeypatch.setattr(tracker_module, "ACTIVE_TRADES_FILE", str(active_path))
    return tracker_module.TradeTracker()


def test_existing_deal_id_is_duplicate(tracker_with_history):
    assert tracker_with_history._is_duplicate_in_history("known-string") is True


def test_numeric_exchange_id_matches_string_runtime_id(tracker_with_history):
    assert tracker_with_history._is_duplicate_in_history("123456") is True


def test_unknown_or_empty_id_is_not_duplicate(tracker_with_history):
    assert tracker_with_history._is_duplicate_in_history("unknown") is False
    assert tracker_with_history._is_duplicate_in_history("") is False
    assert tracker_with_history._is_duplicate_in_history(None) is False
