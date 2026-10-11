"""Динамические SL/TP через реальный core/coordinator и fake BingX, без MagicMock execution."""

from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from src.core.execution_coordinator import attach_execution_safety
from src.core.position_ownership import PositionOwnershipStore
from src.exchanges.config.bingx_config import BingXConfig
from src.exchanges.impl.bingx_client import BingXClient
from test_bingx_safety_regressions import FakeExchange, Response


class PricedExchange(FakeExchange):
    price = Decimal("92000")

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        if path.endswith("/ticker") or path.endswith("/premiumIndex"):
            self.calls.append((method, path, dict(kwargs.get("params", {}))))
            return Response({"lastPrice": str(self.price), "bestBidPrice": str(self.price - 1),
                             "bestAskPrice": str(self.price + 1), "markPrice": str(self.price), "lastFundingRate": "0"})
        return super().request(method, url, **kwargs)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from src import config
    from src.core import executor
    monkeypatch.setenv("BINGX_API_KEY", "dynamic-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "dynamic-fake-secret")
    fake = PricedExchange()
    fake.install(monkeypatch)
    settings = {"exchange": "bingx", "execution_mode": "demo"}
    raw = BingXClient(BingXConfig(settings=settings))
    client = attach_execution_safety(raw, settings=settings, journal_path=tmp_path / "execution.sqlite",
                                     owner_id="dynamic-owner", account_id="dynamic-account")
    owners = PositionOwnershipStore(str(tmp_path / "owners.json"))
    monkeypatch.setattr("src.core.position_ownership.get_position_ownership_store", lambda: owners)
    monkeypatch.setattr(executor, "get_exchange_client", lambda: client)
    monkeypatch.setattr(config, "LEVERAGE", 5)
    monkeypatch.setattr(config, "MIN_TRADE_AMOUNT_USDT", 1)
    monkeypatch.setattr(config, "POSITION_SIZE_PERCENT", 10)
    monkeypatch.setattr(config, "TRADING_FEE_TAKER", .05)
    monkeypatch.setattr(config, "POSITION_LIMITS", {"balance_safety_margin": .95, "max_positions": 5})
    monkeypatch.setattr(executor, "MIN_CONFIDENCE_THRESHOLD", .6)
    monkeypatch.setattr(executor, "AGGRESSIVE_MODE", False)
    return fake, client, owners


def test_dynamic_sltp(runtime):
    from src.core.executor import main
    from src.core.predict import parse_response
    fake, client, owners = runtime
    parsed = parse_response('```json\n{"action":"buy","confidence":0.9,"stop_loss":90000.5,"take_profit":95000,"reason":"Тест"}\n```')
    assert parsed["stop_loss"] == 90000.5 and parsed["take_profit"] == 95000
    prediction = {"symbol": "BTCUSDT", "current_price": 92000, "action": "buy", "confidence": .9,
                  "stop_loss": 90000, "take_profit": 95000, "reason": "Тест", "command_id": "entry"}
    assert main([prediction]) == [True]
    assert fake.orders["1"]["type"] == "MARKET"
    assert fake.orders["2"]["type"] == "STOP_MARKET" and Decimal(fake.orders["2"]["stopPrice"]) == 90000
    assert fake.orders["3"]["type"] == "TAKE_PROFIT_MARKET" and Decimal(fake.orders["3"]["stopPrice"]) == 95000
    assert fake.orders["2"]["positionSide"] == fake.orders["3"]["positionSide"] == "LONG"
    assert client.verify_protection("BTCUSDT") is True
    assert client.journal.get(client.account_id, "entry")["state"] == "FILLED"
    assert owners.get_owner("BTCUSDT").owner_id == client.owner_id
    assert client.journal.owned(client.account_id)[0]["position_id"] == "p1"

    hold = {"symbol": "BTCUSDT", "current_price": 92000, "action": "hold", "confidence": .5,
            "stop_loss": 91500, "take_profit": 96000, "reason": "Перенос SL", "command_id": "trail"}
    assert main([hold]) == [True]
    active = [o for o in fake.orders.values() if o["status"] == "NEW"]
    assert len(active) == 2
    assert {o["type"]: Decimal(o["stopPrice"]) for o in active} == {"STOP_MARKET": Decimal(91500), "TAKE_PROFIT_MARKET": Decimal(96000)}
    assert all(o["positionSide"] == "LONG" and o["side"] == "SELL" for o in active)
    assert client.journal.get(client.account_id, "trail")["result"]["protection_confirmed"] is True
    assert client.verify_protection("BTCUSDT") is True


def test_create_order_passes_runtime_leverage(runtime):
    from src.core.executor import create_order
    fake, client, _ = runtime
    fake.price = Decimal(100)
    oid = create_order("BTCUSDT", "BUY", 100, ai_sl=95, ai_tp=110, confidence=.9, command_id="runtime-leverage")
    assert oid == "1"
    posts = [params for method, path, params in fake.calls if method == "post" and path.endswith("/leverage")]
    assert len(posts) == 1 and int(posts[0]["leverage"]) == 5 and posts[0]["side"] == "LONG"
    assert fake.leverage == 5
    assert client.journal.get(client.account_id, "runtime-leverage")["request"]["leverage"] == 5
    assert client.verify_protection("BTCUSDT") is True
