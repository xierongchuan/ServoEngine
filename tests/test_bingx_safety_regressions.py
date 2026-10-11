"""Офлайн-регрессии BingX: сеть заменяется полностью, ключи исключительно фиктивные."""

import copy
import hashlib
import hmac
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from src.exchanges.config.bingx_config import BingXConfig
from src.exchanges.dto.models import OrderStatus, OrderType, PositionSide
from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError
from src.exchanges.impl.bingx_client import BingXClient


class Response:
    def __init__(self, data=None, *, status=200, code=0):
        self.status_code = status
        self.headers = {}
        self.payload = {"code": code, "data": data, "msg": "тест"}

    def json(self):
        return copy.deepcopy(self.payload)


class FakeExchange:
    def __init__(self, hedge=True):
        self.hedge = hedge
        self.margin = "ISOLATED"
        self.amount = Decimal(0)
        self.direction = "LONG"
        self.orders = {}
        self.calls = []
        self.lost_response = False
        self.late_fill = False
        self.replace_fails = False
        self.leverage = 10
        self.leverage_error = 0
        self.position_reads = 0
        self.manual_delta = Decimal(0)

    def install(self, monkeypatch):
        # HTTP полностью fake; production SQLite limiter проверяется отдельными тестами.
        from types import SimpleNamespace
        monkeypatch.setattr("src.exchanges.impl.bingx_client.get_exchange_rate_limiters",
                            lambda *a, **k: {"private": SimpleNamespace(acquire=lambda: None)})
        for method in ("get", "post", "delete"):
            monkeypatch.setattr(f"src.exchanges.impl.bingx_client.requests.{method}",
                                lambda url, _method=method, **kwargs: self.request(_method, url, **kwargs))
        monkeypatch.setattr("src.exchanges.impl.bingx_client.time.sleep", lambda _: None)

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        encoded = kwargs.get("data") or urlsplit(url).query
        params = kwargs.get("params") or {k: v[0] for k, v in parse_qs(encoded).items()}
        params = {k: v for k, v in params.items() if k not in {"signature", "timestamp", "recvWindow"}}
        self.calls.append((method, path, dict(params)))
        if path.endswith("/contracts"):
            return Response([{"symbol": "BTC-USDT", "pricePrecision": 1, "quantityPrecision": 3,
                              "tradeMinQuantity": "0.001", "tradeMinUSDT": "5", "status": 1,
                              "apiStateOpen": True, "apiStateClose": True, "size": "999"}])
        if path.endswith("/ticker"):
            return Response({"lastPrice": "50000", "bestBidPrice": "49999", "bestAskPrice": "50001"})
        if path.endswith("/premiumIndex"):
            return Response({"markPrice": "50000", "lastFundingRate": "0.001"})
        if path.endswith("/dual"):
            return Response({"dualSidePosition": str(self.hedge).lower()})
        if path.endswith("/marginType"):
            return Response({"marginType": self.margin})
        if path.endswith("/leverage"):
            if method == "post":
                if self.leverage_error:
                    return Response({}, code=self.leverage_error)
                self.leverage = int(params["leverage"])
            return Response({"longLeverage": self.leverage, "shortLeverage": self.leverage,
                             "maxLongLeverage": 125, "maxShortLeverage": 100})
        if path.endswith("/balance"):
            return Response({"balance": {"asset": "USDT", "balance": "10000", "availableMargin": "10000", "unrealizedProfit": "100"}})
        if path.endswith("/positions"):
            self.position_reads += 1
            amount = self.amount
            if amount and not self.hedge and self.direction == "SHORT":
                amount = -amount
            return Response([{"symbol": "BTC-USDT", "positionId": "p1", "positionAmt": str(amount),
                              "availableAmt": str(abs(amount)), "positionSide": self.direction if self.hedge else "BOTH",
                              "isolated": True, "avgPrice": "50000", "unrealizedProfit": "0", "leverage": self.leverage}] if amount else [])
        if path.endswith("/openOrders") or path.endswith("/allOrders"):
            return Response({"orders": [o for o in self.orders.values() if o["status"] == "NEW"]})
        if path.endswith("/cancelReplace"):
            self.orders[params["cancelOrderId"]]["status"] = "CANCELLED"
            if self.replace_fails:
                return Response({"cancelResult": "true", "replaceResult": "false", "newOrderResponse": {}})
            new = self.add_order(params)
            return Response({"cancelResult": "true", "replaceResult": "true", "newOrderResponse": new})
        if path.endswith("/order"):
            if method == "get":
                order = self.orders.get(params.get("orderId"))
                if order is None:
                    order = next((o for o in self.orders.values() if o.get("clientOrderId") == params.get("clientOrderID")), None)
                return Response({"order": order}) if order else Response({}, code=80014)
            if method == "delete":
                self.orders[params["orderId"]]["status"] = "CANCELLED"
                return Response({})
            order = self.add_order(params)
            if params["type"] == "MARKET" and not self.late_fill:
                direction = "LONG" if params["side"] == "BUY" else "SHORT"
                qty = Decimal(params["quantity"])
                if self.amount and direction != self.direction:
                    self.amount -= qty
                else:
                    self.amount += qty
                    self.direction = direction
                self.amount += self.manual_delta
            if self.lost_response:
                raise requests.Timeout("не логировать подписанный URL")
            return Response({"order": order})
        raise AssertionError(f"Непредусмотренный запрос: {method} {path}")

    def add_order(self, params):
        oid = str(len(self.orders) + 1)
        filled = params["type"] == "MARKET" and not self.late_fill
        order = dict(params, orderId=oid, origQty=params["quantity"], executedQty=params["quantity"] if filled else "0",
                     status="FILLED" if filled else "NEW", avgPrice="50000", price=params.get("price", "0"))
        self.orders[oid] = order
        return order

    @property
    def mutations(self):
        return [c for c in self.calls if c[0] != "get"]


def new_client(mode="demo", hedge=True):
    return BingXClient(BingXConfig(settings={"exchange": "bingx", "execution_mode": mode,
                                          "position_mode": "hedge" if hedge else "one_way",
                                          "grid_mode": "hedged" if hedge else "inventory"}))


@pytest.fixture
def exchange(monkeypatch):
    monkeypatch.setenv("BINGX_API_KEY", "regression-fake-key")
    monkeypatch.setenv("BINGX_SECRET_KEY", "regression-fake-secret")
    fake = FakeExchange()
    fake.install(monkeypatch)
    return fake


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_signing_exact_wire_and_query(method, exchange, monkeypatch):
    client = new_client()
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.time", lambda: 1234)
    wire, headers = client._signed_payload(method, {"memo": '{"text":"a b&+"}', "apiKey": "remove", "timestamp": 1})
    unsigned, signature = wire.rsplit("&signature=", 1)
    expected = unsigned if method == "post" else 'memo={"text":"a b&+"}&recvWindow=5000&timestamp=1234000'
    assert signature == hmac.new(b"regression-fake-secret", expected.encode(), hashlib.sha256).hexdigest()
    assert "apiKey=" not in wire and headers == {"X-BX-APIKEY": "regression-fake-key"}
    assert ("a+b" in unsigned) if method == "post" else ("a%20b" in unsigned)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_mutation_http_never_retried(status, exchange, monkeypatch):
    client = new_client()
    calls = []
    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.post", lambda *a, **k: calls.append(1) or Response({}, status=status))
    with pytest.raises(UnknownOrderStateError):
        client._make_request("post", "/openApi/swap/v2/trade/order", {"symbol": "BTC-USDT"})
    assert len(calls) == 1 and client._generation == 1


@pytest.mark.parametrize("code", [100500, 109500, 109429])
def test_busy_api_mutation_unknown_not_rejected_or_retried(code, exchange, monkeypatch):
    client = new_client()
    calls = []
    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.post",
                        lambda *a, **k: calls.append(1) or Response({}, code=code))
    with pytest.raises(UnknownOrderStateError) as caught:
        client._make_request("post", "/openApi/swap/v2/trade/leverage", {"symbol": "BTC-USDT", "side": "LONG", "leverage": 5})
    assert len(calls) == 1 and caught.value.request_params["leverage"] == 5


@pytest.mark.parametrize("attribute,value", [("api_key", "another-account"), ("base_url", "https://untrusted.example")])
def test_private_identity_cannot_change_under_existing_cache(attribute, value, exchange):
    client = new_client()
    setattr(client, attribute, value)
    with pytest.raises(ExchangeStateUnavailableError):
        client.get_balance()
    assert not exchange.calls


def test_get_retries_refresh_timestamp(exchange, monkeypatch):
    client = new_client()
    times = iter([1, 2, 3])
    monkeypatch.setattr("src.exchanges.impl.bingx_client.time.time", lambda: next(times))
    calls = []
    monkeypatch.setattr("src.exchanges.impl.bingx_client.requests.get", lambda url, **k: calls.append(url) or Response({}, status=503 if len(calls) < 3 else 200))
    assert client._make_request("get", "/read")["code"] == 0
    assert [parse_qs(urlsplit(url).query)["timestamp"][0] for url in calls] == ["1000", "2000", "3000"]


@pytest.mark.parametrize("mode,margin,hedge", [("read_only", "ISOLATED", True), ("demo", "CROSSED", True), ("demo", "ISOLATED", False)])
def test_mode_checks_do_not_mutate(mode, margin, hedge, exchange):
    exchange.margin, exchange.hedge = margin, hedge
    with pytest.raises(UnsupportedCapabilityError):
        new_client(mode).place_order("BTCUSDT", "buy", "0.01", client_order_id="mode1")
    assert exchange.mutations == []


def test_decimal_rules_quantity_in_coin_and_rounding(exchange):
    client = new_client()
    rules = client.get_instrument_rules("BTCUSDT")
    assert rules.contract_size == Decimal(1) and rules.quantity_step == Decimal("0.001")
    oid = client.place_order("BTCUSDT", "buy", "0.010999", order_type="limit", price="50000.09", client_order_id="limit1")
    assert exchange.orders[oid]["quantity"] == "0.010"
    assert exchange.orders[oid]["price"] == "50000.0"


@pytest.mark.parametrize("quantity", ["nan", "Infinity", "-1", "0", True, "0.0001"])
def test_bad_quantity_no_mutation(quantity, exchange):
    with pytest.raises(ExchangeStateUnavailableError):
        new_client().place_order("BTCUSDT", "BUY", quantity)
    assert exchange.mutations == []


def test_lost_entry_reconciles_once_and_replay_does_not_claim_volume_twice(exchange):
    exchange.lost_response = True
    client = new_client()
    assert client.place_order("BTCUSDT", "buy", "0.01", client_order_id="entry1") == "1"
    assert client.place_order("BTCUSDT", "buy", "0.01", client_order_id="entry1") == "1"
    assert len(exchange.mutations) == 1
    assert client._owned_positions["p1"][1] == Decimal("0.01")
    assert any("clientOrderID" in c[2] for c in exchange.calls)
    assert client.base_url == "https://open-api-vst.bingx.com"


def test_same_id_different_command_rejected(exchange):
    client = new_client()
    client.place_order("BTCUSDT", "buy", ".01", client_order_id="entry1")
    with pytest.raises(UnsupportedCapabilityError):
        client.place_order("BTCUSDT", "buy", ".02", client_order_id="entry1")
    assert len(exchange.mutations) == 1


def test_late_fill_unknown_has_context_and_no_replay(exchange):
    exchange.late_fill = True
    client = new_client()
    for _ in range(2):
        with pytest.raises(UnknownOrderStateError) as caught:
            client.place_order("BTCUSDT", "BUY", ".01", client_order_id="late1")
        assert caught.value.client_order_id == "late1"
        assert caught.value.request_params["quantity"] == "0.01"
    assert len(exchange.mutations) == 1 and not client._owned_positions


def test_reconciliation_after_restart_reads_only(exchange):
    exchange.lost_response = True
    client = new_client()
    client.place_order("BTCUSDT", "BUY", ".01", client_order_id="restart1")
    params = client._intents["restart1"]["params"]
    restored = new_client()
    order = restored.reconcile_submission(params)
    assert order.order_id == "1" and len(exchange.mutations) == 1


def test_manual_position_and_manual_cancel_are_forbidden(exchange):
    exchange.amount = Decimal("0.01")
    client = new_client()
    with pytest.raises(UnsupportedCapabilityError):
        client.place_order("BTCUSDT", "buy", ".01")
    with pytest.raises(UnsupportedCapabilityError):
        client.cancel_order("BTCUSDT", "manual1")
    assert not exchange.mutations


def test_position_race_never_claims_manual_delta(exchange):
    exchange.manual_delta = Decimal("0.001")
    client = new_client()
    with pytest.raises(UnknownOrderStateError):
        client.place_order("BTCUSDT", "buy", ".01", client_order_id="race1")
    assert not client._owned_positions


@pytest.mark.parametrize("hedge", [True, False])
def test_close_modes_and_stable_replay(hedge, exchange):
    exchange.hedge = hedge
    client = new_client(hedge=hedge)
    client.place_order("BTCUSDT", "buy", ".02", client_order_id="entry1")
    assert client.close_position("BTCUSDT", "p1", .5, client_order_id="close1")
    assert client.close_position("BTCUSDT", "p1", .5, client_order_id="close1")
    assert exchange.amount == Decimal(".01") and len(exchange.mutations) == 2
    close = exchange.orders["2"]
    assert close["positionSide"] == ("LONG" if hedge else "BOTH")
    assert ("reduceOnly" not in close) if hedge else (close["reduceOnly"] == "true")


@pytest.mark.parametrize("pct", [0, -1, 2, "nan", float("inf"), True])
def test_invalid_percentage_no_mutation(pct, exchange):
    with pytest.raises(ExchangeStateUnavailableError):
        new_client().close_position("BTCUSDT", "p1", pct)
    assert not exchange.mutations


def prepare_position(exchange):
    client = new_client()
    client.place_order("BTCUSDT", "buy", ".02", client_order_id="entry1")
    return client


def test_protection_verification_and_replacement_preserves_tp(exchange):
    client = prepare_position(exchange)
    assert client.set_sl_tp("BTCUSDT", "long", sl="49000.01", tp="51000.09", position_id="p1")
    old_tp = exchange.orders["3"].copy()
    assert client.last_protection_confirmed
    assert exchange.orders["2"]["stopPrice"] == "49000.1"
    assert exchange.orders["3"]["stopPrice"] == "51000.0"
    client.set_sl_tp("BTCUSDT", "LONG", sl="49500", position_id="p1")
    assert exchange.orders["2"]["status"] == "CANCELLED" and exchange.orders["3"] == old_tp
    assert exchange.mutations[-1][1].endswith("/cancelReplace")


def test_all_protection_validated_before_mutation(exchange):
    client = prepare_position(exchange)
    with pytest.raises(ExchangeStateUnavailableError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=49000, tp=49000, position_id="p1")
    assert len(exchange.mutations) == 1


def test_manual_protection_not_cancelled(exchange):
    client = prepare_position(exchange)
    exchange.add_order({"symbol": "BTC-USDT", "side": "SELL", "positionSide": "LONG",
                        "type": "STOP_MARKET", "quantity": ".02", "stopPrice": "49000", "workingType": "MARK_PRICE"})
    with pytest.raises(UnsupportedCapabilityError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=49500, position_id="p1")
    assert len(exchange.mutations) == 1 and exchange.orders["2"]["status"] == "NEW"


def test_cancel_replace_partial_outcome_unknown_and_blocks_new_protection(exchange):
    client = prepare_position(exchange)
    client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    exchange.replace_fails = True
    with pytest.raises(UnknownOrderStateError) as caught:
        client.set_sl_tp("BTCUSDT", "LONG", sl=49500, position_id="p1")
    assert caught.value.cancel_result is True and caught.value.replace_result is False
    assert caught.value.old_order_id == "2" and not client.last_protection_confirmed
    with pytest.raises(ExchangeStateUnavailableError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=49500, position_id="p1")
    assert len(exchange.mutations) == 3


def test_lost_conditional_does_not_adopt_or_retry(exchange):
    client = prepare_position(exchange)
    exchange.lost_response = True
    with pytest.raises(UnknownOrderStateError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    assert "2" not in client._owned_orders and len(exchange.mutations) == 2
    with pytest.raises(ExchangeStateUnavailableError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    assert len(exchange.mutations) == 2


def test_restore_exact_protection_from_journal(exchange):
    client = prepare_position(exchange)
    client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    params = exchange.mutations[-1][2]
    restored = new_client()
    restored.register_owned_position("BTCUSDT", "p1", ".02", position_side="LONG")
    restored.register_owned_protection("BTCUSDT", "2", request_params=params, position_id="p1")
    restored.cancel_order("BTCUSDT", "2")
    assert exchange.orders["2"]["status"] == "CANCELLED"


def test_leverage_80001_is_error_not_success(exchange):
    exchange.leverage_error = 80001
    with pytest.raises(ExchangeAPIError) as caught:
        new_client().set_leverage("BTCUSDT", 5, "LONG")
    assert caught.value.code == 80001
    assert len(exchange.mutations) == 1


def test_order_status_aliases_types_and_raw(exchange):
    client = new_client()
    for status, expected in [("PARTIALLYFILLED", OrderStatus.PARTIALLY_FILLED), ("CANCELLED", OrderStatus.CANCELED), ("FAILED", OrderStatus.REJECTED)]:
        raw = {"symbol": "BTC-USDT", "orderID": "huge-123", "type": "STOP_MARKET", "status": status,
               "side": "SELL", "positionSide": "BOTH", "origQty": ".02", "executedQty": ".001", "stopPrice": "49000"}
        order = client._parse_orders([raw])[0]
        assert order.status == expected and order.order_type == OrderType.STOP_MARKET and order.raw_data == raw


def test_generation_discards_racing_snapshot(exchange, monkeypatch):
    client = new_client()
    exchange.amount = Decimal(".02")
    original = client._make_request
    calls = []
    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1].endswith("/positions"):
            calls.append(1)
            if len(calls) == 1:
                client.invalidate_cache()
                exchange.amount = Decimal(".03")
        return result
    monkeypatch.setattr(client, "_make_request", racing)
    positions = client.get_positions()
    assert positions["BTCUSDT"][0].size == .03 and len(calls) == 2
    positions["BTCUSDT"].clear()
    assert client._positions_cache["BTCUSDT"]


def test_no_shared_account_cache(exchange):
    first = new_client()
    exchange.amount = Decimal(".02")
    first.get_positions()
    exchange.amount = Decimal(0)
    second = new_client()
    assert second.get_positions() == {} and first._positions_cache != second._positions_cache


def test_restore_reversed_position_requires_original_direction(exchange):
    exchange.amount = Decimal(".02")
    exchange.direction = "SHORT"
    client = new_client()
    with pytest.raises(ExchangeStateUnavailableError):
        client.register_owned_position("BTCUSDT", "p1", ".02", position_side="LONG")
    with pytest.raises(UnsupportedCapabilityError):
        client.register_owned_position("BTCUSDT", "p1", ".02")
    assert not client._owned_positions


def test_separate_isolated_close_and_protection_use_position_id(exchange):
    exchange.margin = "SEPARATE_ISOLATED"
    client = prepare_position(exchange)
    client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    assert exchange.orders["2"]["positionId"] == "p1"
    client.close_position("BTCUSDT", "p1", .5, client_order_id="close1")
    assert exchange.orders["3"]["positionId"] == "p1"


def test_cancel_all_leaves_foreign_orders_untouched(exchange):
    client = prepare_position(exchange)
    client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    foreign = exchange.add_order({"symbol": "BTC-USDT", "side": "BUY", "positionSide": "LONG", "type": "LIMIT", "quantity": ".01", "price": "48000"})
    client.cancel_all_orders("BTCUSDT")
    assert foreign["status"] == "NEW" and exchange.orders["2"]["status"] == "CANCELLED"
    assert not any(c[1].endswith("/allOpenOrders") for c in exchange.calls)


@pytest.mark.parametrize("bad", [None, {"data": {}}, {"code": True, "data": {}}, {"code": 0, "data": None}])
def test_corrupt_mutation_reconciles_without_retry(bad, exchange, monkeypatch):
    client = new_client()
    original = exchange.request
    def corrupt(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if method == "post" and urlsplit(url).path.endswith("/order"):
            response.payload = bad
        return response
    monkeypatch.setattr(exchange, "request", corrupt)
    assert client.place_order("BTCUSDT", "BUY", ".01", client_order_id="corrupt1") == "1"
    assert len(exchange.mutations) == 1


def test_ack_journal_failure_is_unknown_not_success(exchange):
    client = new_client()
    def observer(event):
        if event["status"] == "ACKNOWLEDGED":
            raise OSError("журнал не записал ID")
    client._submission_observer = observer
    with pytest.raises(UnknownOrderStateError) as caught:
        client.place_order("BTCUSDT", "BUY", ".01", client_order_id="ackfail1")
    assert caught.value.order_id == "1" and len(exchange.mutations) == 1


def test_prepared_journal_failure_prevents_mutation(exchange):
    client = new_client()
    def failing(event):
        raise OSError("журнал недоступен")
    client._submission_observer = failing
    with pytest.raises(ExchangeStateUnavailableError):
        client.place_order("BTCUSDT", "BUY", ".01", client_order_id="journal1")
    assert not exchange.mutations


def test_durable_observer_sees_all_protective_ids(exchange):
    client = new_client()
    events = []
    client._submission_observer = events.append
    client.place_order("BTCUSDT", "BUY", ".02", sl=49000, tp=51000, client_order_id="journal1")
    assert {e["order_id"] for e in events if e["status"] == "ACKNOWLEDGED"} == {"1", "2", "3"}
    assert len([e for e in events if e["status"] == "PREPARED"]) == 3
    assert not any("signature" in e["request_params"] for e in events)


def test_invalid_protection_is_rejected_before_entry(exchange):
    with pytest.raises(ExchangeStateUnavailableError):
        new_client().place_order("BTCUSDT", "BUY", ".01", sl=51000)
    assert not exchange.mutations


def test_protection_readback_mismatch_is_unknown(exchange, monkeypatch):
    client = prepare_position(exchange)
    original = exchange.add_order
    def changed(params):
        order = original(params)
        if params["type"] == "STOP_MARKET":
            order["workingType"] = "CONTRACT_PRICE"
        return order
    monkeypatch.setattr(exchange, "add_order", changed)
    with pytest.raises(UnknownOrderStateError):
        client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    assert not client.last_protection_confirmed


def test_close_does_not_cancel_protection_of_another_position(exchange):
    client = prepare_position(exchange)
    client.set_sl_tp("BTCUSDT", "LONG", sl=49000, position_id="p1")
    other = exchange.add_order({"symbol": "BTC-USDT", "side": "SELL", "positionSide": "LONG",
                                "type": "TAKE_PROFIT_MARKET", "quantity": ".01", "stopPrice": "51000", "workingType": "MARK_PRICE"})
    client._owned_orders[other["orderId"]] = "BTC-USDT"
    client._owned_order_positions[other["orderId"]] = "p-other"
    client.close_position("BTCUSDT", "p1", client_order_id="close1")
    assert other["status"] == "NEW" and exchange.orders["2"]["status"] == "CANCELLED"


def test_manual_reversal_not_closed_as_owned(exchange):
    client = prepare_position(exchange)
    exchange.direction = "SHORT"
    with pytest.raises(ExchangeStateUnavailableError):
        client.close_position("BTCUSDT", "p1")
    assert len(exchange.mutations) == 1


def test_legacy_adapter_denies_raw_mutation_and_propagates(exchange):
    from src.exchanges.bingx_client import BingXClient as Legacy
    client = Legacy(trading_settings={"execution_mode": "demo"})
    with pytest.raises(UnsupportedCapabilityError):
        client.make_request("post", "/openApi/swap/v2/trade/order", {})
    assert client.place_order("BTCUSDT", "buy", 50000, ".01", client_order_id="legacy1") == "1"
    assert client.get_positions()["BTCUSDT"][0]["dealId"] == "p1"
    assert client.get_balance()["equity"] == 10100
    assert len(exchange.mutations) == 1
