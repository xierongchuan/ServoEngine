"""Регрессии для production-safety SCALP."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _candles(count=60):
    result = []
    price = 50_000.0
    for index in range(count):
        price += 15 if index % 4 else -5
        result.append({
            "openPrice": price - 5,
            "highPrice": price + 20,
            "lowPrice": price - 20,
            "closePrice": price,
            "volume": 1_000 + index * 5,
            "timestamp": 1_700_000_000_000 + index * 60_000,
            "snapshotTimeUTC": 1_700_000_000_000 + index * 60_000,
        })
    return result


def test_backtest_adapter_supports_scalp():
    from src.backtest.signals import SignalGenerator
    from src.config_loader import resolve_symbol_config

    generator = SignalGenerator("SCALP", resolve_symbol_config("BTCUSDT", "SCALP", "scalp_no_ai"))
    candles = _candles()
    result = generator.generate_signal(candles, 40)

    assert result["action"] in {"HOLD", "BUY", "SELL"}
    assert "Unsupported strategy" not in result.get("reason", "")


def test_no_ai_runtime_does_not_require_ai_key():
    import src.main as main_module

    client = MagicMock()
    client.capabilities.automated_strategy = True
    client.check_prerequisites.return_value = True
    instance = SimpleNamespace(strategy="SCALP")
    resolved = {"SCALP_SETTINGS": {"ai_integration": {
        "regime_enabled": False, "veto_enabled": False,
    }}}

    with patch.object(main_module, "AI_API_KEY", ""), \
         patch.object(main_module, "get_exchange_client", return_value=client), \
         patch("src.config_loader.get_strategy_instances", return_value=[instance]), \
         patch("src.config_loader.resolve_strategy_instance_config", return_value=resolved):
        assert main_module.check_prerequisites() is True


def test_unprotected_entry_is_closed_fail_closed():
    from src.core.executor import create_order

    client = MagicMock()
    client.capabilities.attached_protection = False
    client.get_balance.return_value = {"balance": 1_000}
    client.place_order.return_value = "entry-1"
    position = {
        "symbol": "BTCUSDT", "type": "buy", "size": 0.01,
        "entry": 50_000, "dealId": "position-1", "pnl": 0,
    }
    client.get_positions.return_value = {"BTCUSDT": [position]}
    client.set_sl_tp.return_value = False
    client.get_open_orders.return_value = []
    client.close_position.return_value = True

    with patch("src.core.executor.get_exchange_client", return_value=client):
        result = create_order("BTCUSDT", "BUY", 50_000, ai_sl=49_500, ai_tp=51_500)

    assert result is None
    client.close_position.assert_called_once_with("BTCUSDT", "position-1", percentage=1.0)
