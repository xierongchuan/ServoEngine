"""Production-safety SCALP: реальные execution settings, durable UNKNOWN и доказанный own close."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.execution_coordinator import attach_execution_safety
from src.exchanges.errors import ExchangeStateUnavailableError, UnknownOrderStateError
from src.trading_settings import validate_trading_settings
from test_bingx_safety_regressions import FakeExchange, new_client


def _candles(count=60):
    result = []
    price = 50_000.0
    for index in range(count):
        price += 15 if index % 4 else -5
        result.append({"openPrice": price - 5, "highPrice": price + 20, "lowPrice": price - 20,
                       "closePrice": price, "volume": 1_000 + index * 5,
                       "timestamp": 1_700_000_000_000 + index * 60_000,
                       "snapshotTimeUTC": 1_700_000_000_000 + index * 60_000})
    return result


def test_backtest_adapter_supports_scalp():
    from src.backtest.signals import SignalGenerator
    from src.config_loader import resolve_symbol_config
    generator = SignalGenerator("SCALP", resolve_symbol_config("BTCUSDT", "SCALP", "scalp_no_ai"))
    result = generator.generate_signal(_candles(), 40)
    assert result["action"] in {"HOLD", "BUY", "SELL"}
    assert "Unsupported strategy" not in result.get("reason", "")


def test_no_ai_runtime_does_not_require_ai_key(monkeypatch):
    import src.main as main_module
    fake = FakeExchange()
    fake.install(monkeypatch)
    monkeypatch.setenv("BINGX_API_KEY", "scalp-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "scalp-fake-secret")
    settings = validate_trading_settings({"exchange": "bingx", "execution_mode": "demo"})
    instance = SimpleNamespace(strategy="SCALP")
    resolved = {"execution": settings, "SCALP_SETTINGS": {"ai_integration": {"regime_enabled": False, "veto_enabled": False}}}
    raw = new_client()
    with patch.object(main_module, "AI_API_KEY", ""), \
         patch("src.trading_settings.get_trading_settings", return_value=settings), \
         patch("src.exchanges.exchange_factory._create_client", return_value=raw) as create, \
         patch("src.config_loader.get_strategy_instances", return_value=[instance]), \
         patch("src.config_loader.resolve_strategy_instance_config", return_value=resolved):
        assert main_module.check_prerequisites() is True
    assert create.call_count == 1 and create.call_args.kwargs["settings"] == settings
    assert any(path.endswith("/dual") for _, path, _ in fake.calls)
    assert not fake.mutations


@pytest.fixture
def execution(monkeypatch, tmp_path):
    from src import config
    from src.core import executor
    monkeypatch.setenv("BINGX_API_KEY", "scalp-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "scalp-fake-secret")
    monkeypatch.setattr(config, "LEVERAGE", 10)
    monkeypatch.setattr(config, "MIN_TRADE_AMOUNT_USDT", 1)
    monkeypatch.setattr(config, "POSITION_SIZE_PERCENT", 10)
    monkeypatch.setattr(config, "TRADING_FEE_TAKER", .05)
    monkeypatch.setattr(config, "POSITION_LIMITS", {"balance_safety_margin": .95, "max_positions": 5})
    fake = FakeExchange()
    fake.install(monkeypatch)
    def make(policy):
        settings = validate_trading_settings({"exchange": "bingx", "execution_mode": "demo", "protection": {"failure_policy": policy}})
        client = attach_execution_safety(new_client(), journal_path=tmp_path / "execution.sqlite",
                settings=settings, owner_id="scalp-owner", account_id="scalp-account")
        monkeypatch.setattr(executor, "get_exchange_client", lambda: client)
        return client
    return fake, make


@pytest.mark.parametrize("policy", ["pause", "close"])
def test_unprotected_entry_is_closed_fail_closed(execution, monkeypatch, policy):
    from src.core.executor import create_order
    fake, make = execution
    client = make(policy)
    original = fake.add_order
    def externally_cancelled(params):
        order = original(params)
        if params["type"] == "STOP_MARKET":
            # Биржа дала ACK, но защита отменена до обязательного адресного readback.
            order["status"] = "CANCELLED"
        return order
    monkeypatch.setattr(fake, "add_order", externally_cancelled)
    with pytest.raises(UnknownOrderStateError):
        create_order("BTCUSDT", "BUY", 50000, ai_sl=49500, ai_tp=51500, command_id="unsafe-entry")
    assert client.journal.get(client.account_id, "unsafe-entry")["state"] == "FILLED"
    pending = client.journal.intents(client.account_id, pending_only=True)
    assert any(r["operation"] == "protection" and r["state"] == "UNKNOWN" for r in pending)
    assert client.is_symbol_blocked("BTCUSDT")
    owned = client.journal.owned(client.account_id)[0]
    assert owned["position_id"] == "p1" and owned["owner"] == "scalp-owner"
    if policy == "close":
        closes = [r for r in client.journal.intents(client.account_id) if r["operation"] == "close"]
        assert len(closes) == 1 and closes[0]["state"] == "FILLED"
        assert closes[0]["request"]["position_id"] == "p1"
        assert closes[0]["result"]["close_confirmed"] is True
        assert fake.amount == Decimal(owned["quantity"]) == 0
        params = fake.mutations[-1][2]
        assert params["side"] == "SELL" and params["positionSide"] == "LONG"
        assert Decimal(params["quantity"]) == Decimal(fake.orders["1"]["origQty"])
    else:
        assert len(fake.mutations) == 2
        assert fake.amount == Decimal(owned["quantity"]) > 0
        assert not any(r["operation"] == "close" for r in client.journal.intents(client.account_id))


def test_unprotected_entry_manual_change_never_excess_closes(execution, monkeypatch):
    from src.core.executor import create_order
    fake, make = execution
    client = make("close")
    def failed_protection(*args, **kwargs):
        fake.amount += Decimal(".001")
        return False
    monkeypatch.setattr(client.raw_client, "set_sl_tp", failed_protection)
    with pytest.raises((ExchangeStateUnavailableError, UnknownOrderStateError)):
        create_order("BTCUSDT", "BUY", 50000, ai_sl=49500, ai_tp=51500, command_id="manual-race")
    assert len(fake.mutations) == 1
    owned = client.journal.owned(client.account_id)[0]
    assert fake.amount == Decimal(owned["quantity"]) + Decimal(".001")
    assert client.is_symbol_blocked("BTCUSDT")
    assert not any(r["operation"] == "close" for r in client.journal.intents(client.account_id))
