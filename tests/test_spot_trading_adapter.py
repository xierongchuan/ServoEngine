"""Spot safety: fake credentials, без сети, без импорта ручного inventory."""

import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.exchanges.dto.models import (
    AssetBalance, CommissionRate, InstrumentRules, MarketType, Order, OrderSide,
    OrderStatus, OrderSubmission, OrderType, PositionSide, SpotOrderRequest, SubmissionStatus, Ticker,
)
from src.exchanges.errors import (
    ExchangeStateUnavailableError, UnknownOrderStateError, UnsupportedCapabilityError,
)
from src.exchanges.exchange_client import ExchangeClient
from src.exchanges.impl.spot_trading_adapter import SpotTradingAdapter


D = Decimal


class Clock:
    now = 1700000045.0

    def __call__(self):
        return self.now


class FakeSpot:
    EXCHANGE_NAME = "mexc"

    def __init__(self):
        self._config = SimpleNamespace(execution_mode="live", api_key="fake-key", secret_key="fake-secret")
        self.wallet = {"BTC": [D("50"), D("0")], "USDT": [D("10000"), D("0")]}
        self.orders = {}
        self.trades = {}
        self.requests = []
        self.cancelled = []
        self.price = D("100")
        self.ticker_calls = 0
        self.balance_calls = 0
        self.timeout = False
        self.accept_before_timeout = True
        self.auto_market_fill = True
        self.fee_asset = "USDT"
        self.fee_rate = D("0")
        self.hidden_fills = False

    def get_instrument_rules(self, symbol):
        return InstrumentRules("BTCUSDT", "BTCUSDT", "BTC", "USDT", True,
            D("0.01"), D("0.001"), D("0.001"), D("100"), D("0.01"),
            order_types=("MARKET", "LIMIT"))

    def get_asset_balances(self):
        self.balance_calls += 1
        return [AssetBalance(asset, free, locked) for asset, (free, locked) in self.wallet.items()]

    def get_ticker(self, symbol):
        self.ticker_calls += 1
        return Ticker("BTCUSDT", float(self.price), float(self.price), float(self.price), 0)

    def get_klines(self, symbol, interval, limit):
        return []

    def get_order_book(self, symbol, limit):
        return []

    def get_commission_rate(self, symbol):
        return CommissionRate(float(self.fee_rate * 100), float(self.fee_rate * 100))

    def check_prerequisites(self):
        return True

    def place_spot_order(self, request):
        self.requests.append(request)
        if self.timeout and not self.accept_before_timeout:
            raise UnknownOrderStateError("имитация потерянного ответа")
        qty = request.base_quantity or (request.quote_quantity / self.price)
        oid = str(len(self.orders) + 1)
        self.orders[request.client_order_id] = {
            "symbol": request.symbol, "clientOrderId": request.client_order_id,
            "orderId": oid, "side": request.side.value, "type": request.order_type.value,
            "origQty": str(qty), "executedQty": "0", "cumulativeQuoteQty": "0",
            "price": str(request.price or 0), "status": "NEW",
        }
        self.trades[oid] = []
        if request.order_type == OrderType.MARKET and self.auto_market_fill:
            self.fill(request.client_order_id, qty)
        if self.timeout:
            raise UnknownOrderStateError("имитация потерянного ответа после приёма")
        return OrderSubmission(SubmissionStatus.ACKNOWLEDGED, oid, request.client_order_id,
                               raw_data={"orderId": oid})

    def fill(self, cid, quantity):
        row = self.orders[cid]
        qty = D(str(quantity))
        quote = qty * (D(row["price"]) or self.price)
        fee = qty * self.fee_rate if self.fee_asset == "BTC" else quote * self.fee_rate
        buy = row["side"] == "BUY"
        self.wallet["BTC"][0] += qty if buy else -qty
        self.wallet["USDT"][0] += -quote if buy else quote
        self.wallet.setdefault(self.fee_asset, [D("100"), D("0")])[0] -= fee
        row["executedQty"] = str(D(row["executedQty"]) + qty)
        row["cumulativeQuoteQty"] = str(D(row["cumulativeQuoteQty"]) + quote)
        row["status"] = "FILLED" if D(row["executedQty"]) == D(row["origQty"]) else "PARTIALLY_FILLED"
        trades = self.trades[row["orderId"]]
        trades.append({"id": str(len(trades) + 1), "orderId": row["orderId"], "symbol": "BTCUSDT",
            "qty": str(qty), "price": str(quote / qty), "quoteQty": str(quote),
            "commission": str(fee), "commissionAsset": self.fee_asset})

    def _query_by_client_id(self, symbol, cid):
        row = self.orders.get(cid)
        if row is None:
            return None
        filled, quote = D(row["executedQty"]), D(row["cumulativeQuoteQty"])
        return Order(row["orderId"], row["symbol"], OrderSide(row["side"]), OrderType(row["type"]),
            OrderStatus(row["status"]), float(row["price"]), float(row["origQty"]), float(filled),
            float(quote / filled) if filled else 0, raw_data=dict(row))

    def get_order_trades(self, symbol, oid):
        return [] if self.hidden_fills else self.trades[oid]

    def restore_order_ownership(self, symbol, oid):
        assert any(row["orderId"] == oid for row in self.orders.values())

    def cancel_order(self, symbol, oid):
        self.cancelled.append(oid)
        row = next(row for row in self.orders.values() if row["orderId"] == oid)
        row["status"] = "CANCELED"
        return True


@pytest.fixture
def setup(tmp_path):
    spot, clock, adapters = FakeSpot(), Clock(), []

    def make(*, start=True, **kwargs):
        options = dict(ledger_path=tmp_path / "spot.sqlite", account_id="account-a",
            instance_id="instance-a", quote_budget="1000", execution_mode="live",
            local_protection=True, clock=clock, monitor_interval=60,
            snapshot_reader=lambda key: None)
        options.update(kwargs)
        adapter = SpotTradingAdapter(spot, **options)
        adapters.append(adapter)
        if start:
            adapter.start_supervisor()
            deadline = time.monotonic() + 3
            while not adapter.supervisor_running and time.monotonic() < deadline:
                time.sleep(0.005)
            assert adapter.supervisor_running
        return adapter

    yield spot, clock, make
    for adapter in adapters:
        adapter.shutdown()


def buy(adapter, **kwargs):
    return adapter.submit_order("BTC-USDT", OrderSide.BUY, "1", sl="90", tp="110", **kwargs)


def owned(adapter):
    return sum((D(str(p.size)) for p in adapter.get_positions().get("BTCUSDT", [])), D(0))


def test_contract_no_manual_wallet_adoption_and_explicit_quote_budget(setup):
    spot, _, make = setup
    adapter = make()
    assert isinstance(adapter, ExchangeClient)
    assert adapter.get_positions() == {}
    assert adapter.get_balance().total_balance == 1000
    assert adapter.get_balance().available_balance == 1000
    assert spot.wallet["BTC"][0] == 50
    assert adapter.capabilities.market_type == MarketType.SPOT
    assert adapter.capabilities.positions and adapter.capabilities.automated_strategy
    assert not any((adapter.capabilities.shorting, adapter.capabilities.leverage,
                    adapter.capabilities.funding, adapter.capabilities.native_protection,
                    adapter.capabilities.attached_protection))
    assert adapter.get_funding_rate("BTCUSDT") is None
    assert adapter.protection_confirmation_scope == "local_registration"


@pytest.mark.parametrize("kwargs", [dict(leverage=2), dict(position_side=PositionSide.SHORT),
                                  dict(order_type=OrderType.STOP_MARKET)])
def test_no_hidden_leverage_short_or_native_stops(setup, kwargs):
    spot, _, make = setup
    adapter = make()
    with pytest.raises(UnsupportedCapabilityError):
        buy(adapter, **kwargs)
    assert not spot.requests
    assert adapter.set_leverage("BTCUSDT", 1)


def test_entry_fails_closed_without_local_opt_in_supervisor_or_sl(setup):
    spot, _, make = setup
    adapter = make(start=False)
    with pytest.raises(UnsupportedCapabilityError, match="supervisor"):
        buy(adapter)
    adapter.start_supervisor()
    deadline = time.monotonic() + 3
    while not adapter.supervisor_running and time.monotonic() < deadline:
        time.sleep(0.005)
    with pytest.raises(UnsupportedCapabilityError, match="SL"):
        adapter.place_order("BTCUSDT", OrderSide.BUY, 1)
    other = make(start=False, instance_id="disabled", local_protection=False, quote_budget=0)
    with pytest.raises(UnsupportedCapabilityError):
        buy(other)
    assert not spot.requests


def test_sell_without_owned_lots_does_not_sell_manual_wallet(setup):
    spot, _, make = setup
    adapter = make()
    with pytest.raises(UnsupportedCapabilityError, match="SELL без"):
        adapter.submit_order("BTCUSDT", OrderSide.SELL, 50)
    with pytest.raises(UnsupportedCapabilityError):
        adapter.close_position("BTCUSDT", "manual-id")
    assert not spot.requests


def test_market_buy_quote_precision_and_confirmed_fills(setup):
    spot, _, make = setup
    adapter = make()
    submission = adapter.submit_order("BTC-USDT", OrderSide.BUY, "1.23456", sl="90.009", tp="110.019")
    request = spot.requests[0]
    assert request.base_quantity is None
    assert request.quote_quantity == D("123.40")
    assert request.price is None
    assert request.client_order_id == submission.client_order_id
    assert owned(adapter) == D("1.234")
    assert adapter.get_balance().total_balance == 876.6
    lot = adapter._rows("lots")[0]
    assert lot["sl"] == "90.00" and lot["tp"] == "110.01"
    assert adapter.last_protection_confirmed
    assert adapter.check_protection("BTCUSDT")


def test_market_sell_prediction_only_reduces_long_even_if_quantity_is_huge(setup):
    spot, _, make = setup
    adapter = make()
    buy(adapter)
    adapter.submit_order("BTCUSDT", OrderSide.SELL, 1000)
    assert spot.requests[-1].base_quantity == 1
    assert adapter.get_positions() == {}
    assert spot.wallet["BTC"][0] == 50


def test_limit_ack_is_not_position_or_protection_and_partial_fills_are_idempotent(setup):
    spot, _, make = setup
    adapter = make()
    submission = buy(adapter, order_type=OrderType.LIMIT, price="100.009", command_id="limit")
    assert owned(adapter) == 0
    assert not adapter.last_protection_confirmed
    assert not adapter.check_protection("BTCUSDT")
    assert adapter.get_balance().available_balance == 900
    spot.fill(submission.client_order_id, "0.4")
    adapter.get_order("BTCUSDT", submission.order_id)
    assert owned(adapter) == D("0.4")
    assert owned(adapter) == D("0.4")
    assert adapter.get_balance().available_balance == 900
    adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl="95", tp="115")
    spot.fill(submission.client_order_id, "0.6")
    assert owned(adapter) == 1
    assert adapter.get_balance().total_balance == 900
    assert D(adapter._rows("lots")[0]["sl"]) == D("95")
    assert adapter.get_open_orders("BTCUSDT") == []


def test_cancel_partial_limit_restores_reserved_quote_without_discarding_fills(setup):
    spot, _, make = setup
    adapter = make()
    submission = buy(adapter, order_type=OrderType.LIMIT, price="100")
    spot.fill(submission.client_order_id, "0.25")
    assert adapter.cancel_order("BTCUSDT", submission.order_id)
    assert owned(adapter) == D("0.25")
    assert adapter.get_balance().available_balance == 975
    assert spot.cancelled == [submission.order_id]


def test_partial_close_uses_lot_id_and_stable_command_after_quantity_changes(setup):
    spot, _, make = setup
    adapter = make()
    submission = buy(adapter)
    assert adapter.close_position("BTCUSDT", submission.client_order_id, .25, command_id="reduce-1")
    assert owned(adapter) == D("0.75")
    assert adapter.close_position("BTCUSDT", submission.client_order_id, .25, command_id="reduce-1")
    assert owned(adapter) == D("0.75")
    assert len(spot.requests) == 2


def test_stable_submission_and_collision_detection(setup):
    spot, _, make = setup
    adapter = make()
    first = buy(adapter, command_id="entry-1")
    again = buy(adapter, command_id="entry-1")
    assert first.order_id == again.order_id
    assert len(spot.requests) == 1
    with pytest.raises(UnsupportedCapabilityError, match="другой командой"):
        adapter.submit_order("BTCUSDT", OrderSide.BUY, 2, sl=90, command_id="entry-1")


def test_timeout_after_acceptance_reconciles_without_second_post(setup):
    spot, _, make = setup
    adapter = make()
    spot.timeout = True
    with pytest.raises(UnknownOrderStateError):
        buy(adapter, command_id="timeout")
    assert not adapter.last_protection_confirmed
    spot.timeout = False
    submission = buy(adapter, command_id="timeout")
    assert submission.order_id == "1"
    assert len(spot.requests) == 1
    assert owned(adapter) == 1


def test_timeout_not_found_does_not_retry_even_after_restart(setup):
    spot, _, make = setup
    adapter = make()
    spot.timeout, spot.accept_before_timeout = True, False
    with pytest.raises(UnknownOrderStateError):
        buy(adapter, command_id="unknown")
    adapter.stop_supervisor()
    restarted = make(start=False)
    with pytest.raises(UnknownOrderStateError):
        buy(restarted, command_id="unknown")
    with pytest.raises(UnknownOrderStateError):
        restarted.get_positions()
    assert len(spot.requests) == 1


def test_external_increase_never_increases_owned_lot_or_cash(setup):
    spot, _, make = setup
    adapter = make()
    buy(adapter)
    spot.wallet["BTC"][0] += 100
    spot.wallet["USDT"][0] += 1000
    assert owned(adapter) == 1
    assert adapter.get_balance().total_balance == 900
    adapter.submit_order("BTCUSDT", OrderSide.SELL, 1)
    assert spot.wallet["BTC"][0] == 150


def test_manual_decrease_even_with_manual_buffer_causes_durable_account_pause(setup):
    spot, _, make = setup
    adapter = make()
    buy(adapter)
    assert owned(adapter) == 1
    spot.wallet["BTC"][0] -= D("0.1")
    with pytest.raises(ExchangeStateUnavailableError, match="внешнее уменьшение"):
        adapter.get_positions()
    assert adapter.paused_reason
    adapter.stop_supervisor()
    spot.wallet["BTC"][0] += D("0.1")
    restarted = make(start=False)
    assert restarted.paused_reason
    with pytest.raises(ExchangeStateUnavailableError):
        restarted.submit_order("BTCUSDT", OrderSide.SELL, 1)
    assert len(spot.requests) == 1


def test_instance_isolation_and_sibling_fill_reconciliation(setup):
    spot, _, make = setup
    first = make()
    second = make(instance_id="second")
    a = buy(first, order_type=OrderType.LIMIT, price=100)
    b = buy(second, order_type=OrderType.LIMIT, price=100)
    assert a.client_order_id != b.client_order_id
    spot.fill(a.client_order_id, "0.3")
    spot.fill(b.client_order_id, "0.5")
    assert owned(first) == D("0.3")
    assert owned(second) == D("0.5")
    with pytest.raises(UnsupportedCapabilityError):
        second.close_position("BTCUSDT", a.client_order_id)
    with pytest.raises(UnsupportedCapabilityError):
        second.cancel_order("BTCUSDT", a.order_id)
    assert not first.paused_reason and not second.paused_reason


def test_base_commission_not_adopted_and_cost_uses_actual_fill(setup):
    spot, _, make = setup
    adapter = make()
    spot.fee_asset, spot.fee_rate = "BTC", D("0.01")
    buy(adapter)
    assert owned(adapter) == D("0.99")
    assert adapter.get_balance().total_balance == 900
    assert adapter.get_positions()["BTCUSDT"][0].entry_price == pytest.approx(100 / .99)


def test_quote_fee_reduces_allocated_cash(setup):
    spot, _, make = setup
    adapter = make()
    spot.fee_rate = D("0.01")
    buy(adapter)
    assert adapter.get_balance().total_balance == 899
    assert owned(adapter) == 1


def test_incomplete_trade_history_blocks_accounting_instead_of_assuming_fill(setup):
    spot, _, make = setup
    adapter = make()
    spot.hidden_fills = True
    with pytest.raises(ExchangeStateUnavailableError, match="fills"):
        buy(adapter)
    assert adapter._rows("lots") == []
    assert not adapter.last_protection_confirmed
    spot.hidden_fills = False
    assert owned(adapter) == 1


def test_restarts_keep_lots_and_local_tp_supervisor_closes_only_owned(setup):
    spot, clock, make = setup
    adapter = make()
    submission = buy(adapter)
    assert owned(adapter) == 1
    adapter.stop_supervisor()
    restarted = make(start=False)
    assert restarted.check_protection("BTCUSDT", position_id=submission.client_order_id)
    spot.price = D("111")
    clock.now += 3
    restarted.start_supervisor()
    deadline = time.monotonic() + 3
    while len(spot.requests) < 2 and time.monotonic() < deadline:
        time.sleep(.005)
    assert len(spot.requests) == 2
    assert spot.requests[-1].side == OrderSide.SELL
    assert restarted.get_positions() == {}
    assert spot.wallet["BTC"][0] == 50


def test_stop_cancels_pending_limit_before_market_exit(setup):
    spot, clock, make = setup
    adapter = make()
    submission = buy(adapter, order_type=OrderType.LIMIT, price=100)
    spot.fill(submission.client_order_id, "0.5")
    assert owned(adapter) == D("0.5")
    spot.price = D("89")
    clock.now += 3
    assert adapter.monitor_once()
    assert spot.cancelled == [submission.order_id]
    assert spot.requests[-1].base_quantity == D("0.5")
    assert adapter.get_positions() == {}
    assert spot.wallet["BTC"][0] == 50


def test_stop_latch_survives_price_rebound_while_exit_pending(setup):
    spot, clock, make = setup
    adapter = make()
    buy(adapter)
    spot.price, spot.auto_market_fill = D("89"), False
    clock.now += 3
    adapter.monitor_once()
    exit_cid = spot.requests[-1].client_order_id
    assert adapter._rows("lots")[0]["triggered"] == 1
    spot.price = D("100")
    clock.now += 3
    adapter.monitor_once()
    assert len(spot.requests) == 2
    spot.fill(exit_cid, 1)
    adapter.monitor_once()
    assert adapter.get_positions() == {}


def test_local_sltp_registration_not_exchange_order_and_full_coverage_required(setup):
    spot, _, make = setup
    adapter = make()
    buy(adapter)
    assert adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=95)
    assert adapter.last_protection_confirmed
    assert len(spot.requests) == 1
    assert adapter.get_open_orders() == []
    with pytest.raises(UnsupportedCapabilityError, match="весь"):
        adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=96, quantity=.5)
    assert not adapter.last_protection_confirmed


def test_read_only_missing_ledger_creates_no_files_and_never_mutates(setup, tmp_path):
    spot, _, make = setup
    path = tmp_path / "missing" / "readonly.sqlite"
    adapter = make(start=False, execution_mode="read_only", ledger_path=path)
    assert adapter.check_prerequisites()
    assert adapter.get_positions() == {}
    assert adapter.get_balance().total_balance == 0
    assert not path.parent.exists()
    for call in [lambda: buy(adapter), lambda: adapter.close_position("BTCUSDT", "any"),
                 lambda: adapter.cancel_all_orders("BTCUSDT"),
                 lambda: adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=90)]:
        with pytest.raises(UnsupportedCapabilityError, match="read_only"):
            call()
    assert not spot.requests and not spot.cancelled


def test_read_only_monitor_reports_trigger_but_preserves_ledger(setup):
    spot, clock, make = setup
    writer = make()
    buy(writer)
    assert owned(writer) == 1
    writer.stop_supervisor()
    viewer = make(start=False, execution_mode="read_only")
    before = viewer._db.iterdump()
    content = list(before)
    spot.price = D("89")
    clock.now += 3
    events = viewer.monitor_once()
    assert events[0]["read_only"]
    assert list(viewer._db.iterdump()) == content
    assert len(spot.requests) == 1
    assert not viewer.capabilities.automated_strategy


def test_authenticated_permission_probe_is_optional_read_only_gets_public_data(setup):
    spot, _, make = setup
    adapter = make(permission_checker=lambda: {"canTrade": True})
    assert adapter.check_prerequisites(authenticated=True)
    blocked = make(start=False, instance_id="blocked", quote_budget=0,
                   permission_checker=lambda: {"canTrade": False})
    with pytest.raises(ExchangeStateUnavailableError, match="trade"):
        blocked.check_prerequisites(authenticated=True)
    assert not spot.requests


def test_fresh_shared_live_1m_close_used_without_public_ticker(setup):
    spot, clock, make = setup

    def snapshot(key):
        return {"stream": key.to_dict(), "last_valid_update": clock(), "status": "ws", "stale": False,
                "candles": [{"timestamp": int(clock() // 60 * 60000), "closePrice": "101", "is_closed": False}]}

    adapter = make(snapshot_reader=snapshot)
    buy(adapter)
    assert spot.ticker_calls == 0
    assert spot.requests[0].quote_quantity == 101


@pytest.mark.parametrize("change", ["stale", "closed", "wrong_stream", "old_candle", "future"])
def test_invalid_live_tick_falls_back_to_rate_limited_public_ticker(setup, change):
    spot, clock, make = setup

    def snapshot(key):
        value = {"stream": key.to_dict(), "last_valid_update": clock(), "status": "ws", "stale": False,
                 "candles": [{"timestamp": int(clock() // 60 * 60000), "closePrice": "500", "is_closed": False}]}
        if change == "stale":
            value["last_valid_update"] -= 100
        if change == "closed":
            value["candles"][0]["is_closed"] = True
        if change == "wrong_stream":
            value["stream"]["market_type"] = "perpetual"
        if change == "old_candle":
            value["candles"][0]["timestamp"] -= 60000
        if change == "future":
            value["last_valid_update"] += 1
        return value

    adapter = make(snapshot_reader=snapshot)
    buy(adapter)
    assert owned(adapter) == 1
    assert owned(adapter) == 1
    assert spot.ticker_calls == 1


def test_ticker_failure_rate_limited_and_old_cached_price_not_used(setup, monkeypatch):
    spot, clock, make = setup
    adapter = make()
    assert adapter._live_price("BTCUSDT") == 100
    calls = []

    def fail(symbol):
        calls.append(symbol)
        raise ExchangeStateUnavailableError("ticker недоступен")

    monkeypatch.setattr(spot, "get_ticker", fail)
    clock.now += 20
    with pytest.raises(ExchangeStateUnavailableError):
        adapter._live_price("BTCUSDT")
    with pytest.raises(ExchangeStateUnavailableError, match="свежей цены"):
        adapter._live_price("BTCUSDT")
    assert len(calls) == 1


def test_only_owned_orders_returned_or_cancelled(setup):
    spot, _, make = setup
    adapter = make()
    submission = buy(adapter, order_type=OrderType.LIMIT, price=100)
    assert len(adapter.get_open_orders()) == 1
    with pytest.raises(UnsupportedCapabilityError):
        adapter.cancel_order("BTCUSDT", "manual-order")
    assert adapter.cancel_all_orders("BTCUSDT")
    assert spot.cancelled == [submission.order_id]
    assert len(adapter.get_recent_orders("BTCUSDT")) == 1


def test_unknown_order_identity_pauses_before_adopting_fills(setup):
    spot, _, make = setup
    adapter = make()
    submission = buy(adapter, order_type=OrderType.LIMIT, price=100)
    spot.orders[submission.client_order_id]["side"] = "SELL"
    with pytest.raises(ExchangeStateUnavailableError, match="identity"):
        adapter.get_positions()
    assert not adapter._rows("lots")
    assert adapter.paused_reason


def test_empty_budget_does_not_spend_manual_quote_wallet(setup):
    spot, _, make = setup
    adapter = make(quote_budget=0)
    assert adapter.get_balance().total_balance == 0
    with pytest.raises(UnsupportedCapabilityError, match="бюджета"):
        buy(adapter)
    assert not spot.requests


def test_sell_reserves_possible_base_fee_and_never_spends_manual_coins(setup):
    spot, _, make = setup
    adapter = make()
    spot.fee_asset, spot.fee_rate = "BTC", D("0.01")
    buy(adapter)
    manual = spot.wallet["BTC"][0] - D("0.99")
    adapter.submit_order("BTCUSDT", OrderSide.SELL, 1)
    assert spot.requests[-1].base_quantity == D("0.980")
    assert spot.wallet["BTC"][0] >= manual
    assert owned(adapter) == D("0.0002")


def test_pending_unfilled_limit_cancelled_at_local_sl(setup):
    spot, clock, make = setup
    adapter = make()
    submission = buy(adapter, order_type=OrderType.LIMIT, price=100)
    spot.price = D("89")
    clock.now += 3
    events = adapter.monitor_once()
    assert events[0]["action"] == "cancel_entry"
    assert spot.cancelled == [submission.order_id]
    assert adapter.get_positions() == {}
    assert len(spot.requests) == 1


def test_read_only_source_has_no_new_wal_shm_or_content_changes(setup, tmp_path):
    _, _, make = setup
    writer = make()
    buy(writer)
    writer.stop_supervisor()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    reader = make(start=False, execution_mode="read_only")
    assert owned(reader) == 1
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert after == before


def test_disabled_local_protection_cannot_mutate_via_monitor(setup):
    spot, clock, make = setup
    writer = make()
    buy(writer)
    writer.stop_supervisor()
    adapter = make(start=False, local_protection=False)
    spot.price = D("89")
    clock.now += 3
    with pytest.raises(UnsupportedCapabilityError, match="согласия"):
        adapter.monitor_once()
    assert len(spot.requests) == 1


def test_changed_order_quantity_is_not_adopted(setup):
    spot, _, make = setup
    adapter = make()
    submission = buy(adapter, order_type=OrderType.LIMIT, price=100)
    spot.orders[submission.client_order_id]["origQty"] = "2"
    with pytest.raises(ExchangeStateUnavailableError, match="intent"):
        adapter.get_positions()
    assert adapter.paused_reason


class NativeFakeTransport:
    """Настоящий MEXCSpotClient с fake REST, без HTTP и загрузки конфигурации."""

    def __init__(self, fake):
        self.fake = fake
        self.calls = []

    def request(self, method, endpoint, params=None, **kwargs):
        self.calls.append((method, endpoint, params, kwargs))
        fake = self.fake
        if endpoint == "/api/v3/exchangeInfo":
            return {"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
                "isSpotTradingAllowed": True, "status": "1", "baseAssetPrecision": 3,
                "quotePrecision": 2, "baseSizePrecision": "0.001", "quoteAmountPrecision": "0.01",
                "maxQuoteAmount": "0", "quoteAmountPrecisionMarket": "0.01",
                "maxQuoteAmountMarket": "0", "tradeSideType": 1, "orderTypes": ["MARKET", "LIMIT"],
                "filters": []}]}
        if endpoint == "/api/v3/account":
            return {"canTrade": True, "balances": [{"asset": b.asset, "free": str(b.free), "locked": str(b.locked)}
                                                    for b in fake.get_asset_balances()]}
        if endpoint == "/api/v3/ticker/24hr":
            return dict(symbol="BTCUSDT", lastPrice=str(fake.price), bidPrice=str(fake.price),
                askPrice=str(fake.price), volume="0", quoteVolume="0", priceChange="0",
                priceChangePercent="0", highPrice=str(fake.price), lowPrice=str(fake.price))
        if endpoint == "/api/v3/ticker/price":
            return {"symbol": "BTCUSDT", "price": str(fake.price)}
        if endpoint == "/api/v3/tradeFee":
            return {"makerCommission": "0", "takerCommission": "0"}
        if endpoint == "/api/v3/myTrades":
            return fake.trades[params["orderId"]]
        if endpoint == "/api/v3/order":
            if method == "POST":
                request = SpotOrderRequest(params["symbol"], OrderSide(params["side"]), OrderType(params["type"]),
                    base_quantity=D(params["quantity"]) if "quantity" in params else None,
                    quote_quantity=D(params["quoteOrderQty"]) if "quoteOrderQty" in params else None,
                    price=D(params["price"]) if "price" in params else None,
                    client_order_id=params["newClientOrderId"])
                submission = fake.place_spot_order(request)
                return dict(fake.orders[submission.client_order_id])
            if method == "GET":
                return dict(fake.orders[params["origClientOrderId"]])
            if method == "DELETE":
                fake.cancel_order(params["symbol"], params["orderId"])
                return dict(next(o for o in fake.orders.values() if o["orderId"] == params["orderId"]))
        raise AssertionError((method, endpoint, params))


def test_real_native_spot_contract_market_and_limit_with_fake_transport(tmp_path):
    from src.exchanges.impl.mexc_spot_client import MEXCSpotClient

    fake = FakeSpot()
    native = MEXCSpotClient(config=fake._config, transport=NativeFakeTransport(fake))
    adapter = SpotTradingAdapter(native, ledger_path=tmp_path / "native.sqlite",
        account_id="fake-account", instance_id="fake-instance", execution_mode="live",
        quote_budget=1000, local_protection=True, snapshot_reader=lambda key: None,
        monitor_interval=60)
    try:
        adapter.start_supervisor()
        deadline = time.monotonic() + 3
        while not adapter.supervisor_running and time.monotonic() < deadline:
            time.sleep(.005)
        assert adapter.check_prerequisites(authenticated=True)
        entry = buy(adapter, command_id="native-buy")
        assert owned(adapter) == 1
        assert adapter.close_position("BTCUSDT", entry.client_order_id, command_id="native-close")
        assert fake.wallet["BTC"][0] == 50
        pending = buy(adapter, order_type=OrderType.LIMIT, price=100)
        fake.fill(pending.client_order_id, "0.5")
        assert owned(adapter) == D("0.5")
        assert adapter.cancel_order("BTCUSDT", pending.order_id)
        assert owned(adapter) == D("0.5")
        assert all(endpoint not in {"/api/v3/order/test", "/api/v3/margin/order"}
                   for _, endpoint, _, _ in native._transport.calls)
    finally:
        adapter.shutdown()


def test_confirmed_trade_ids_persist_idempotently_and_rewrites_pause(setup):
    spot, _, make = setup
    adapter = make()
    submission = buy(adapter)
    for _ in range(3):
        adapter.get_order("BTCUSDT", submission.order_id)
    fills = adapter._db.execute("SELECT * FROM fills").fetchall()
    assert len(fills) == 1
    assert fills[0]["cid"] == submission.client_order_id
    assert fills[0]["oid"] == submission.order_id
    spot.trades[submission.order_id][0]["commissionAsset"] = "BTC"
    with pytest.raises(ExchangeStateUnavailableError, match="fill изменился"):
        adapter.get_order("BTCUSDT", submission.order_id)
    assert adapter.paused_reason


def test_concurrent_same_command_across_adapters_has_one_post(setup):
    from concurrent.futures import ThreadPoolExecutor

    spot, _, make = setup
    first, second = make(), make()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(buy, adapter, command_id="same-command") for adapter in (first, second)]
        results = [future.result(timeout=5) for future in futures]
    assert results[0].order_id == results[1].order_id
    assert len(spot.requests) == 1
    assert owned(first) == owned(second) == 1


def test_restart_does_not_refund_or_reallocate_quote_budget(setup):
    _, _, make = setup
    writer = make()
    buy(writer)
    writer.stop_supervisor()
    restarted = make(start=False, quote_budget=99999)
    assert restarted.get_balance().total_balance == 900
    other_account = make(start=False, account_id="another-account", quote_budget=0)
    assert other_account.get_positions() == {}
    assert other_account.get_balance().total_balance == 0


def test_factory_settings_spot_options_and_safe_defaults(setup):
    spot, _, make = setup
    selected = {"exchange": "mexc", "market_type": "spot", "execution_mode": "live"}
    adapter = make(start=False, settings=selected, quote_budget=None, local_protection=None)
    assert adapter.execution_safety_enabled is True
    assert not hasattr(adapter, "_transport")
    assert adapter.owner_id == "instance-a"
    assert adapter.account_id == adapter.execution_account_id == "account-a"
    assert adapter.settings["spot"] == {"quote_budget_usdt": 0, "allow_local_protection": False}
    assert adapter.settings["position_mode"] == "one_way"
    assert adapter.settings["protection"]["trigger_price"] == "last"
    assert not adapter.check_prerequisites()
    with pytest.raises(UnsupportedCapabilityError):
        buy(adapter, command_id="guarded")
    record = adapter.journal.get(adapter.account_id, "guarded")
    assert record["state"] == "REJECTED" and record["result"]["not_sent"]
    assert not spot.requests


def test_execution_settings_and_identity_are_validated_and_not_mutable(setup):
    _, _, make = setup
    adapter = make()
    snapshot = adapter.settings
    snapshot["execution_mode"] = "read_only"
    assert adapter.settings["execution_mode"] == "live"
    assert adapter.validate_execution_context(owner_id=adapter.owner_id, account_id=adapter.account_id,
                                              settings=adapter.settings, journal_path=adapter.journal.path) is adapter
    for kwargs in [{"owner_id": "another"}, {"account_id": "another"},
                   {"settings": {"exchange": "bingx"}}, {"journal_path": "different.sqlite"}]:
        with pytest.raises(ExchangeStateUnavailableError):
            adapter.validate_execution_context(**kwargs)
    for name in ("owner_id", "account_id", "execution_account_id", "execution_mode", "local_protection"):
        with pytest.raises(AttributeError):
            setattr(adapter, name, "changed")


@pytest.mark.parametrize("bad", [{"allow_local_protection": "true"}, {"unrecognized": 1},
                                 {"quote_budget_usdt": -1}])
def test_invalid_spot_options_are_rejected_without_mutation(setup, bad):
    spot, _, make = setup
    with pytest.raises((ValueError, ExchangeStateUnavailableError)):
        make(start=False, quote_budget=None, local_protection=None, spot_options=bad)
    assert not spot.requests


def test_factory_can_pass_validated_execution_and_separate_spot_options(setup):
    _, _, make = setup
    adapter = make(settings={"exchange": "mexc", "market_type": "spot", "execution_mode": "live"},
                   quote_budget=None, local_protection=None,
                   spot_options={"quote_budget_usdt": 250, "allow_local_protection": True})
    assert adapter.get_balance().available_balance == 250
    assert adapter.check_prerequisites(authenticated=False)


def test_authenticated_read_permission_failure_and_native_identity_drift(setup):
    spot, _, make = setup
    adapter = make(permission_checker=lambda: {"canTrade": True, "canRead": False})
    with pytest.raises(ExchangeStateUnavailableError):
        adapter.check_prerequisites(authenticated=True)
    spot._config.api_key = "another-fake-account"
    try:
        with pytest.raises(ExchangeStateUnavailableError, match="credentials"):
            adapter.get_balance()
        with pytest.raises(ExchangeStateUnavailableError, match="credentials"):
            buy(adapter)
        adapter.close()  # Cleanup не должен обращаться к изменённому аккаунту.
    finally:
        spot._config.api_key = "fake-key"
    assert not spot.requests


def test_live_budget_zero_even_with_local_opt_in_cannot_enter(setup):
    spot, _, make = setup
    adapter = make(quote_budget=0)
    assert not adapter.check_prerequisites()
    with pytest.raises(UnsupportedCapabilityError, match="бюджета"):
        buy(adapter, command_id="no-allocation")
    assert adapter.journal.get(adapter.account_id, "no-allocation")["state"] == "REJECTED"
    assert not spot.requests


def test_market_place_order_never_reports_ack_as_confirmed_fill(setup):
    spot, _, make = setup
    adapter = make()
    spot.auto_market_fill = False
    with pytest.raises(UnknownOrderStateError, match="ACK"):
        adapter.place_order("BTCUSDT", OrderSide.BUY, 1, sl=90, tp=110, command_id="pending-market")
    assert adapter.is_symbol_blocked("BTCUSDT")
    assert not adapter.last_protection_confirmed
    record = adapter.journal.get(adapter.account_id, "pending-market")
    assert record["state"] == "ACK"
    assert record["result"]["entry_complete"] is False
    spot.fill(record["client_id"], 1)
    assert adapter.recover("BTCUSDT") == {}
    assert not adapter.is_symbol_blocked("BTCUSDT")
    assert adapter.wait_for_entry("BTCUSDT", record["order_id"], timeout=0)
    assert len(spot.requests) == 1


def test_wait_for_entry_unfilled_timeout_cancels_exact_order_and_returns_false(setup):
    spot, _, make = setup
    adapter = make()
    entry = buy(adapter, order_type=OrderType.LIMIT, price=100, command_id="timeout-limit")
    assert adapter.is_symbol_blocked("BTCUSDT")
    assert adapter.wait_for_entry("BTCUSDT", entry.order_id, timeout=0) is False
    assert spot.cancelled == [entry.order_id]
    assert not adapter.is_symbol_blocked("BTCUSDT")
    assert adapter.journal.get(adapter.account_id, "timeout-limit")["state"] == "RESOLVED"
    assert adapter.get_positions() == {}


def test_wait_for_terminal_partial_keeps_lot_and_durable_pause_without_auto_clear(setup):
    spot, _, make = setup
    adapter = make()
    entry = buy(adapter, order_type=OrderType.LIMIT, price=100, command_id="partial-limit")
    spot.fill(entry.client_order_id, "0.4")
    with pytest.raises(UnknownOrderStateError, match="partial"):
        adapter.wait_for_entry("BTCUSDT", entry.order_id, timeout=0)
    assert owned(adapter) == D("0.4")
    assert adapter.is_symbol_blocked("BTCUSDT")
    assert adapter.reconcile_pending()["BTCUSDT"] == "ENTRY_PARTIAL_REQUIRES_ACKNOWLEDGEMENT"
    adapter.stop_supervisor()
    restarted = make(start=False)
    assert restarted.is_symbol_blocked("BTCUSDT")
    assert restarted.journal.get(restarted.account_id, "partial-limit")["state"] == "PARTIAL"
    assert restarted.owns_position("BTCUSDT", entry.client_order_id)


def test_scalp_snapshot_partial_close_lifecycle_and_closure_proofs(setup):
    spot, _, make = setup
    adapter = make()
    entry = buy(adapter, command_id="scalp-entry")
    snapshot = adapter.get_position_execution_snapshot("BTCUSDT", entry.client_order_id)
    assert snapshot["owner"] == adapter.owner_id and snapshot["side"] == "LONG"
    assert snapshot["snapshot"]["entry_order_id"] == entry.order_id
    protection = snapshot["protections"][0]
    assert protection["order_id"].startswith("local:")
    assert protection["snapshot"]["native"] is False
    assert protection["snapshot"]["stopLossPrice"] == "90"
    assert protection["snapshot"]["takeProfitPrice"] == "110"
    token = adapter.position_command_id("BTCUSDT", entry.client_order_id, "scalp:tp1")
    assert adapter.close_position("BTCUSDT", entry.client_order_id, .25, command_id=token)
    record = adapter.journal.get(adapter.account_id, token)
    assert record["state"] == "FILLED" and record["result"]["close_confirmed"] is True
    assert adapter.position_command_id("BTC-USDT", entry.client_order_id, "scalp:tp1") == token
    assert not adapter.closure_confirmed("BTCUSDT", entry.client_order_id)
    assert adapter.close_position("BTCUSDT", entry.client_order_id, .25, command_id=token)
    assert len(spot.requests) == 2
    with pytest.raises(ExchangeStateUnavailableError, match="percentage"):
        adapter.close_position("BTCUSDT", entry.client_order_id, .5, command_id=token)
    assert adapter.close_position("BTCUSDT", entry.client_order_id,
        command_id=adapter.position_command_id("BTCUSDT", entry.client_order_id, "monitor:full-close"))
    assert adapter.closure_confirmed("BTCUSDT", entry.client_order_id)
    assert not adapter.closure_confirmed("BTCUSDT", "manual-position")
    assert spot.wallet["BTC"][0] == 50


def test_full_close_with_fee_dust_is_not_falsely_confirmed(setup):
    spot, _, make = setup
    adapter = make()
    spot.fee_rate = D("0.01")
    entry = buy(adapter)
    assert adapter.close_position("BTCUSDT", entry.client_order_id, command_id="dust-close") is False
    record = adapter.journal.get(adapter.account_id, "dust-close")
    assert record["state"] == "FILLED" and record["result"]["close_confirmed"] is False
    assert owned(adapter) > 0
    assert not adapter.closure_confirmed("BTCUSDT", entry.client_order_id)


def test_protection_command_replay_never_rolls_back_newer_manual_levels(setup):
    spot, clock, make = setup
    adapter = make()
    entry = buy(adapter)
    assert adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=95, position_id=entry.client_order_id,
                             command_id="old-sl")
    assert adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=97, position_id=entry.client_order_id,
                             command_id="manual-update")
    with pytest.raises(ExchangeStateUnavailableError, match="старой защиты"):
        adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=95, position_id=entry.client_order_id, command_id="old-sl")
    snapshot = adapter.get_position_execution_snapshot("BTCUSDT", entry.client_order_id)
    assert D(snapshot["protections"][0]["snapshot"]["stopLossPrice"]) == 97
    record = adapter.journal.get(adapter.account_id, "manual-update")
    assert record["result"]["confirmation_scope"] == "local_registration"
    assert not record["result"]["native"]
    spot.price = D("96")
    clock.now += 3
    adapter.monitor_once()
    assert adapter.closure_confirmed("BTCUSDT", entry.client_order_id)
    assert spot.wallet["BTC"][0] == 50


def test_operator_pause_stays_after_recovery_and_explicit_protection_update(setup):
    _, _, make = setup
    adapter = make()
    entry = buy(adapter)
    adapter.pause_symbol("BTCUSDT", "operator-pause")
    adapter.recover()
    adapter.set_sl_tp("BTCUSDT", PositionSide.LONG, sl=95, position_id=entry.client_order_id)
    assert adapter.journal.blocked(adapter.account_id, "BTCUSDT") == "operator-pause"
    with pytest.raises(ExchangeStateUnavailableError):
        buy(adapter, command_id="paused-new-entry")
    # Выход только из доказанного лота разрешён, но не снимает pause.
    assert adapter.close_position("BTCUSDT", entry.client_order_id)
    assert adapter.is_symbol_blocked("BTCUSDT")
    assert not adapter.closure_confirmed("BTCUSDT", entry.client_order_id)


def test_pending_close_is_not_resubmitted_and_receipt_recovers(setup):
    spot, _, make = setup
    adapter = make()
    entry = buy(adapter)
    spot.auto_market_fill = False
    assert adapter.close_position("BTCUSDT", entry.client_order_id, command_id="pending-close") is False
    assert adapter.close_position("BTCUSDT", entry.client_order_id, command_id="pending-close") is False
    close = adapter.journal.get(adapter.account_id, "pending-close")
    assert close["state"] == "ACK" and not close["result"]["close_confirmed"]
    spot.fill(close["client_id"], 1)
    adapter.recover()
    assert adapter.close_position("BTCUSDT", entry.client_order_id, command_id="pending-close") is True
    assert adapter.closure_confirmed("BTCUSDT", entry.client_order_id)
    assert len(spot.requests) == 2


def test_cancel_timeout_reconciles_without_repeating_delete(setup, monkeypatch):
    spot, _, make = setup
    adapter = make()
    entry = buy(adapter, order_type=OrderType.LIMIT, price=100)
    calls = []

    def unknown(symbol, order_id):
        calls.append(order_id)
        raise UnknownOrderStateError("cancel result неизвестен")

    monkeypatch.setattr(spot, "cancel_order", unknown)
    with pytest.raises(UnknownOrderStateError):
        adapter.cancel_order("BTCUSDT", entry.order_id, command_id="cancel-timeout")
    with pytest.raises(UnknownOrderStateError, match="повтор DELETE"):
        adapter.cancel_order("BTCUSDT", entry.order_id, command_id="cancel-timeout")
    assert len(calls) == 1
    spot.orders[entry.client_order_id]["status"] = "CANCELED"
    assert adapter.recover() == {}
    assert adapter.journal.get(adapter.account_id, "cancel-timeout")["result"]["cancel_confirmed"] is True
    assert adapter.cancel_order("BTCUSDT", entry.order_id, command_id="cancel-timeout")
    assert len(calls) == 1


def test_close_and_factory_reset_release_resources_without_wallet_mutations(setup):
    spot, _, make = setup
    adapter = make()
    entry = buy(adapter)
    adapter.stop_supervisor()
    assert not adapter.supervisor_running
    with pytest.raises(UnsupportedCapabilityError, match="supervisor"):
        buy(adapter, command_id="after-stop")
    adapter.close()
    adapter.close()
    adapter.shutdown()
    with pytest.raises(ExchangeStateUnavailableError, match="закрыт"):
        adapter.submit_order("BTCUSDT", OrderSide.SELL, 1)
    with pytest.raises(ExchangeStateUnavailableError):
        adapter.start_supervisor()
    assert len(spot.requests) == 1
    restarted = make()
    assert restarted.owns_position("BTCUSDT", entry.client_order_id)
    assert owned(restarted) == 1


@pytest.fixture
def isolated_core_packages(monkeypatch):
    """Не выполняем eager core.__init__/config; целевые .py модули реальные."""
    import sys
    import src
    from pathlib import Path
    from types import ModuleType

    before = set(sys.modules)
    root = Path(__file__).resolve().parents[1] / "src" / "core"
    for name, relative in (("src.core", ""), ("src.core.signals", "signals"),
                           ("src.core.strategies", "strategies"), ("src.core.strategies.grid", "strategies/grid")):
        package = ModuleType(name)
        package.__path__ = [str(root / relative)]
        monkeypatch.setitem(sys.modules, name, package)
        if name == "src.core":
            monkeypatch.setattr(src, "core", package, raising=False)
    yield
    for name in set(sys.modules) - before:
        if name.startswith("src.core."):
            sys.modules.pop(name, None)


@pytest.fixture
def real_core_facade(setup, monkeypatch, tmp_path, isolated_core_packages):
    """Реальные helper/executor/attach и fake config без загрузки .env."""
    import sys
    import src
    from types import ModuleType

    _, _, make = setup
    adapter = make()
    config = ModuleType("src.config")
    values = dict(LEVERAGE=1, POSITION_SIZE_PERCENT=10, TRADING_FEE_TAKER=0,
        POSITION_LIMITS={"balance_safety_margin": .95, "max_positions": 5},
        MIN_TRADE_AMOUNT_USDT=1, STOP_LOSS_PERCENT=10, TAKE_PROFIT_PERCENT=10,
        DATA_DIR=str(tmp_path), MIN_CONFIDENCE_THRESHOLD=.6, AGGRESSIVE_MODE=False,
        AGGRESSIVE_SETTINGS={}, BOT_CONFIG={"STRATEGY_INSTANCE_ID": adapter.owner_id})
    config.__dict__.update(values)
    monkeypatch.setitem(sys.modules, "src.config", config)
    monkeypatch.setattr(src, "config", config, raising=False)
    from src.core import execution_helpers, executor
    for key, value in values.items():
        if hasattr(executor, key):
            monkeypatch.setattr(executor, key, value)

    # attach_execution_safety уже поддерживает trusted Spot facade; не подменяем
    # его или helpers/executor, только источник клиента и глобальный config.
    monkeypatch.setattr(executor, "get_exchange_client", lambda: adapter)
    return adapter, config, execution_helpers, executor


def test_real_protected_order_helper_passes_local_sltp_even_without_attached_capability(real_core_facade, setup):
    adapter, _, helper, _ = real_core_facade
    spot, _, _ = setup
    assert not adapter.capabilities.attached_protection
    oid = helper.create_protected_order(adapter, "BTCUSDT", "BUY", 100,
        sl=90, tp=110, command_id="real-helper", owner_id=adapter.owner_id)
    assert oid == "1"
    assert owned(adapter) == 1
    record = adapter.journal.get(adapter.account_id, "real-helper")
    assert record["state"] == "FILLED" and record["result"]["entry_complete"]
    assert adapter.verify_protection("BTCUSDT")
    assert adapter.last_protection_confirmed
    assert len(spot.requests) == 1


def test_real_executor_create_order_uses_facade_not_transport_coordinator(real_core_facade):
    adapter, _, _, executor = real_core_facade
    oid = executor.create_order("BTCUSDT", "BUY", 100, ai_sl=90, ai_tp=110,
                                command_id="executor-entry", owner_id=adapter.owner_id)
    assert oid == "1"
    rows = adapter.get_owned_positions("BTCUSDT")["BTCUSDT"]
    assert rows[0].side == PositionSide.LONG
    assert executor._wait_for_position(adapter, "BTCUSDT", PositionSide.LONG).position_id == rows[0].position_id


def test_real_helper_forces_spot_leverage_one_not_global_derivative_leverage(real_core_facade, setup):
    adapter, config, helper, _ = real_core_facade
    spot, _, _ = setup
    config.LEVERAGE = 5
    assert helper.create_protected_order(adapter, "BTCUSDT", "BUY", 100, sl=90, tp=110, command_id="spot-1x")
    assert spot.requests[0].quote_quantity == 100
    assert owned(adapter) == 1
    assert adapter.journal.get(adapter.account_id, "spot-1x")["request"]["leverage"] == "1"


def test_real_helper_never_returns_success_for_market_ack(real_core_facade, setup):
    adapter, _, helper, _ = real_core_facade
    spot, _, _ = setup
    spot.auto_market_fill = False
    with pytest.raises(UnknownOrderStateError):
        helper.create_protected_order(adapter, "BTCUSDT", "BUY", 100,
                                      sl=90, tp=110, command_id="helper-pending")
    assert adapter.is_symbol_blocked("BTCUSDT")
    assert not adapter.last_protection_confirmed
    assert len(spot.requests) == 1


def test_multiple_grid_buy_limits_reserve_exact_quote_and_owned_sell_ids(setup):
    spot, _, make = setup
    adapter = make()
    first = buy(adapter, order_type=OrderType.LIMIT, price=100, command_id="grid-a")
    second = buy(adapter, order_type=OrderType.LIMIT, price=99, command_id="grid-b")
    assert adapter.get_balance().available_balance == 801
    spot.fill(first.client_order_id, 1)
    spot.fill(second.client_order_id, 1)
    adapter.recover()
    assert owned(adapter) == 2
    sell = adapter.submit_order("BTCUSDT", OrderSide.SELL, 1, order_type=OrderType.LIMIT,
        price=105, position_id=first.client_order_id, command_id="grid-sell-a")
    with pytest.raises(ExchangeStateUnavailableError, match="резервирует"):
        adapter.submit_order("BTCUSDT", OrderSide.SELL, .5, order_type=OrderType.LIMIT,
                            price=106, position_id=first.client_order_id, command_id="grid-sell-again")
    assert adapter.get_sellable_quantity("BTCUSDT", first.client_order_id) == 0
    spot.fill(sell.client_order_id, 1)
    adapter.recover()
    assert not adapter.owns_position("BTCUSDT", first.client_order_id)
    assert adapter.owns_position("BTCUSDT", second.client_order_id)
    assert owned(adapter) == 1


def test_limit_budget_reservation_does_not_round_notional_down(setup):
    _, _, make = setup
    adapter = make(quote_budget="9.995")
    with pytest.raises(UnsupportedCapabilityError, match="бюджета"):
        adapter.submit_order("BTCUSDT", OrderSide.BUY, ".101", order_type=OrderType.LIMIT,
                            price=99, sl=90, command_id="exact-limit-notional")


def test_real_grid_executor_owned_buy_sell_and_emergency_close(setup, tmp_path, isolated_core_packages):
    from src.core.grid_executor import GridExecutor

    spot, _, make = setup
    adapter = make()
    settings = {"exchange": "mexc", "market_type": "spot", "execution_mode": "live"}
    runtime = {"execution": settings, "STRATEGY_INSTANCE_ID": adapter.owner_id,
               "EXECUTION_ACCOUNT_ID": adapter.account_id}
    grid = GridExecutor("BTCUSDT", {"order_size_usdt": 100}, runtime_config=runtime,
                        client=adapter, state_dir=tmp_path / "grid-state")
    try:
        assert not grid.paused_reason
        first = grid._place_grid_order("BUY", 100, 100)
        second = grid._place_grid_order("BUY", 99, 100)
        assert first and second
        for request in list(spot.requests):
            spot.fill(request.client_order_id, request.base_quantity)
        assert len(grid.reconcile()) == 2
        grid.update_inventory()
        assert len(grid.owned_positions) == 2
        oid = grid._place_grid_order("SELL", 105, 100)
        assert oid
        request = spot.requests[-1]
        spot.fill(request.client_order_id, request.base_quantity)
        assert grid.reconcile()
        grid.update_inventory()
        assert grid.state.short_inventory == 0
        assert grid.emergency_close()
        assert adapter.get_positions() == {}
        assert spot.wallet["BTC"][0] == 50
    finally:
        grid.close()


def test_shared_settings_budget_is_not_duplicated_for_each_instance(setup):
    spot, _, make = setup
    selected = {"exchange": "mexc", "market_type": "spot", "execution_mode": "live",
                "spot": {"quote_budget_usdt": 250, "allow_local_protection": True}}
    first = make(settings=selected, quote_budget=None, local_protection=None)
    second = make(instance_id="shared-second", settings=selected, quote_budget=None, local_protection=None)
    assert first.get_balance().available_balance == second.get_balance().available_balance == 250
    a, b = buy(first), buy(second)
    assert owned(first) == owned(second) == 1
    assert first.get_balance().available_balance == second.get_balance().available_balance == 50
    with pytest.raises(UnsupportedCapabilityError, match="бюджета"):
        buy(second, command_id="no-extra-budget")
    assert len(spot.requests) == 2
    spot.wallet["USDT"][0] += 5000
    assert first.get_balance().total_balance == 50
    assert first.close_position("BTCUSDT", a.client_order_id)
    assert first.get_balance().available_balance == second.get_balance().available_balance == 150
    assert second.owns_position("BTCUSDT", b.client_order_id)
    # Возвращённый quote доступен другой стратегии, но ручной base не adopted.
    buy(second)
    assert owned(first) == 0 and owned(second) == 2
    assert first.get_balance().available_balance == 50
    assert spot.wallet["BTC"][0] == 52


def test_shared_pending_quote_reservations_are_atomic_across_instances(setup):
    _, _, make = setup
    options = {"quote_budget_usdt": 250, "allow_local_protection": True}
    first = make(spot_options=options, quote_budget=None, local_protection=None)
    second = make(instance_id="reserved-second", spot_options=options, quote_budget=None, local_protection=None)
    a = buy(first, order_type=OrderType.LIMIT, price=100)
    buy(second, order_type=OrderType.LIMIT, price=100)
    assert first.get_balance().available_balance == second.get_balance().available_balance == 50
    with pytest.raises(UnsupportedCapabilityError, match="бюджета"):
        buy(second, order_type=OrderType.LIMIT, price=100, command_id="over-reserve")
    assert first.cancel_order("BTCUSDT", a.order_id)
    assert second.get_balance().available_balance == 150
    buy(second, order_type=OrderType.LIMIT, price=100, command_id="use-released-budget")
    assert first.get_balance().available_balance == second.get_balance().available_balance == 50
    assert not first.paused_reason and not second.paused_reason


def test_shared_budget_restart_keeps_allocation_and_requires_explicit_adjustment(setup):
    _, _, make = setup
    options = {"quote_budget_usdt": 250, "allow_local_protection": True}
    first = make(spot_options=options, quote_budget=None, local_protection=None)
    buy(first)
    first.stop_supervisor()
    restarted = make(spot_options=options, quote_budget=None, local_protection=None)
    assert restarted.get_balance().total_balance == 150
    assert owned(restarted) == 1
    with pytest.raises(ExchangeStateUnavailableError, match="общего бюджета"):
        make(start=False, spot_options={**options, "quote_budget_usdt": 500},
             quote_budget=None, local_protection=None)


def test_lot_open_time_is_first_confirmed_fill_not_limit_ack(setup):
    spot, clock, make = setup
    adapter = make()
    entry = buy(adapter, order_type=OrderType.LIMIT, price=100)
    prepared_at = clock.now
    assert adapter._rows("lots") == []
    clock.now += 30
    spot.fill(entry.client_order_id, 1)
    adapter.get_order("BTCUSDT", entry.order_id)
    snapshot = adapter.get_position_execution_snapshot("BTCUSDT", entry.client_order_id)
    assert snapshot["snapshot"]["opened_at"] > prepared_at
    assert snapshot["snapshot"]["opened_at"] == clock.now
    assert snapshot["snapshot"]["opened_at_source"] == "first_fill_observed"
    assert adapter.get_positions()["BTCUSDT"][0].created_at.timestamp() == clock.now


def test_actual_attach_recognizes_facade_without_transport_and_checks_owner(real_core_facade):
    from src.core.execution_coordinator import attach_execution_safety

    adapter, _, _, _ = real_core_facade
    assert attach_execution_safety(adapter, owner_id=adapter.owner_id, settings=adapter.settings,
                                   account_id=adapter.account_id, journal_path=adapter.journal.path) is adapter
    assert not hasattr(adapter, "_transport")
    with pytest.raises(ExchangeStateUnavailableError, match="instance"):
        attach_execution_safety(adapter, owner_id="foreign-owner")


def test_adapter_provenance_is_committed_before_native_submit(setup, monkeypatch):
    import sqlite3

    spot, _, make = setup
    adapter = make()
    original = spot.place_spot_order
    observed = []

    def inspect_intent(request):
        with sqlite3.connect(adapter.journal.path) as db:
            row = db.execute("SELECT status,sl,tp,oid FROM commands WHERE cid=?", (request.client_order_id,)).fetchone()
            assert row == ("INTENT", "90", "110", None)
            meta = db.execute("SELECT state,command_id FROM command_meta WHERE cid=?", (request.client_order_id,)).fetchone()
            assert meta == ("PREPARED", "durable-before-post")
        observed.append(request.client_order_id)
        return original(request)

    monkeypatch.setattr(spot, "place_spot_order", inspect_intent)
    entry = buy(adapter, command_id="durable-before-post")
    assert observed == [entry.client_order_id]
    assert adapter.journal.get(adapter.account_id, "durable-before-post")["state"] == "FILLED"


def test_read_only_facade_recover_and_queries_do_not_modify_journal(setup):
    spot, _, make = setup
    writer = make()
    entry = buy(writer, command_id="read-only-entry")
    writer.stop_supervisor()
    reader = make(start=False, execution_mode="read_only")
    before = list(reader._db.iterdump())
    assert reader.recover() == {}
    assert reader.journal.get(reader.account_id, "read-only-entry")["state"] == "FILLED"
    assert reader.journal.owned(reader.account_id, owner=reader.owner_id)[0]["position_id"] == entry.client_order_id
    assert reader.journal.protections(reader.account_id)[0]["snapshot"]["native"] is False
    assert reader.get_owned_positions("BTCUSDT")["BTCUSDT"][0].size == 1
    assert list(reader._db.iterdump()) == before
    with pytest.raises(UnsupportedCapabilityError, match="read_only"):
        reader.pause_symbol("BTCUSDT", "not permitted")
    assert len(spot.requests) == 1


def test_verify_protection_rejects_foreign_id_without_fake_confirmation(setup):
    _, _, make = setup
    adapter = make()
    buy(adapter)
    assert adapter.verify_protection("BTCUSDT")
    with pytest.raises(UnsupportedCapabilityError, match="position_id"):
        adapter.verify_protection("BTCUSDT", position_id="manual-wallet-position")
    assert adapter.last_protection_confirmed is False


def test_unknown_cancel_cannot_be_retried_with_another_command_id(setup, monkeypatch):
    spot, _, make = setup
    adapter = make()
    entry = buy(adapter, order_type=OrderType.LIMIT, price=100)
    calls = []

    def unknown(symbol, order_id):
        calls.append(order_id)
        raise UnknownOrderStateError("cancel result неизвестен")

    monkeypatch.setattr(spot, "cancel_order", unknown)
    with pytest.raises(UnknownOrderStateError):
        adapter.cancel_order("BTCUSDT", entry.order_id, command_id="first-cancel")
    with pytest.raises(UnknownOrderStateError, match="повтор DELETE"):
        adapter.cancel_order("BTCUSDT", entry.order_id, command_id="different-cancel")
    assert len(calls) == 1


def test_auth_probe_checks_identity_before_calling_permissions(setup):
    spot, _, make = setup
    calls = []
    adapter = make(permission_checker=lambda: calls.append(True) or {"canTrade": True})
    spot._config.api_key = "another-fake-key"
    try:
        with pytest.raises(ExchangeStateUnavailableError, match="credentials"):
            adapter.check_prerequisites(authenticated=True)
        assert calls == []
    finally:
        spot._config.api_key = "fake-key"


def test_partial_close_base_fee_confirms_net_reduction_but_full_dust_does_not(setup):
    spot, _, make = setup
    adapter = make()
    spot.fee_asset, spot.fee_rate = "BTC", D("0.01")
    entry = buy(adapter)
    assert adapter.close_position("BTCUSDT", entry.client_order_id, .25, command_id="base-fee-partial")
    record = adapter.journal.get(adapter.account_id, "base-fee-partial")
    assert record["result"]["close_confirmed"] is True
    assert record["result"]["filled_quantity"] == "0.247"
    assert D(record["result"]["remaining_quantity"]) == D("0.74053")
    assert not adapter.closure_confirmed("BTCUSDT", entry.client_order_id)


@pytest.mark.parametrize("invalid_id", [None, True, "", "bad id"])
def test_malformed_trade_id_cannot_create_owned_lot(setup, monkeypatch, invalid_id):
    spot, _, make = setup
    adapter = make()
    original = spot.get_order_trades

    def malformed(symbol, oid):
        trades = [dict(t) for t in original(symbol, oid)]
        trades[0]["id"] = invalid_id
        return trades

    monkeypatch.setattr(spot, "get_order_trades", malformed)
    with pytest.raises(ExchangeStateUnavailableError, match="trade IDs"):
        buy(adapter)
    assert adapter._rows("lots") == []
    assert not adapter.last_protection_confirmed
