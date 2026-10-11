"""Некорректный WS не отравляет REST fallback и не создаёт запрос на каждый тик."""

import json
import logging
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.exchanges.bingx_ws_data_provider import BingXWebSocketDataProvider
from src.exchanges.market_data import StreamKey, StreamStore, normalize_candle
from test_market_data_layer import Clock, T, raw
from test_market_data_ws_runtime import FakeSocket


@pytest.fixture
def stream():
    clock = Clock(T / 1000 + 135)
    settings = {"history_limit": 3, "min_history": 3, "max_age_seconds": 30,
                "reconcile_interval_seconds": 60, "heartbeat_interval_seconds": 1000}
    key = StreamKey("bingx", "perpetual", "ZECUSDT", "30m")
    rows = [raw(T - 3600000), raw(T - 1800000), raw(T)]
    rest = Mock(return_value=rows)
    store = StreamStore(settings=settings, clock=clock)
    provider = BingXWebSocketDataProvider(store=store, rest_fetcher=rest, publisher=Mock(),
                                         clock=clock, monotonic=clock)
    socket = FakeSocket()
    provider.sync_subscriptions([key])
    provider._ws = socket
    provider._on_open(socket)
    provider.acknowledge(key)
    provider.reconcile_once(key)
    provider.request_reconcile = Mock(wraps=provider.request_reconcile)
    try:
        yield provider, store, key, socket, clock, rest
    finally:
        provider.stop()


def send(provider, socket, row, channel="ZEC-USDT@kline_30min"):
    provider._on_message(socket, json.dumps({"dataType": channel, "data": row}))


def bad(ts=T):
    return {**raw(ts), "high": 10.5}


def test_repeated_invalid_frames_preserve_rest_and_do_not_amplify_requests(stream, caplog):
    provider, store, key, socket, clock, rest = stream
    caplog.set_level(logging.INFO, logger="src.exchanges.ws.runtime")
    before = store.snapshot(key)
    send(provider, socket, bad())
    failed = store.snapshot(key)
    assert not failed["ready"] and failed["status"] == "error"
    assert failed["candles"] == before["candles"]
    assert failed["last_valid_update"] == before["last_valid_update"]
    assert "OHLCV-инварианты" in failed["ws_error"]
    assert "H=10.5" in failed["ws_error"]
    provider.request_reconcile.assert_called_once_with(key)
    assert rest.call_count == 1

    clock.now += 1
    provider.reconcile_once(key)
    restored = store.snapshot(key)
    assert restored["ready"] and restored["status"] == "rest"
    assert restored["candles"][-1]["source"] == "rest"
    assert restored["ws_error"]
    for _ in range(100):
        clock.now += 0.01
        send(provider, socket, bad())
    current = store.snapshot(key)
    for field in ("candles", "last_valid_update", "version", "status", "ws_error", "ready"):
        assert current[field] == restored[field]
    assert current["age_seconds"] > restored["age_seconds"]
    assert provider._ws_errors[key]["rejected"] == 101
    assert provider.request_reconcile.call_count == 1
    assert rest.call_count == 2
    assert sum("Отклонена некорректная свеча" in record.message for record in caplog.records) == 1

    clock.now += 1
    send(provider, socket, raw(T, close=11.5))
    recovered = store.snapshot(key)
    assert recovered["ready"] and recovered["status"] == "live"
    assert recovered["candles"][-1]["closePrice"] == 11.5
    assert recovered["ws_error"] is None and key not in provider._ws_errors
    assert any("101 отклонённых свечей" in record.message for record in caplog.records)

    send(provider, socket, bad())
    assert not store.snapshot(key)["ready"]
    assert provider.request_reconcile.call_count == 2
    assert sum("Отклонена некорректная свеча" in record.message for record in caplog.records) == 2


def test_rest_failure_and_staleness_are_not_hidden_by_rejection_dedup(stream):
    provider, store, key, socket, clock, rest = stream
    send(provider, socket, bad())
    rest.side_effect = ValueError("Тестовый отказ REST")
    provider.reconcile_once(key)
    assert not store.snapshot(key)["ready"]
    assert store.snapshot(key)["status"] == "error"
    for _ in range(40):
        clock.now += 1
        send(provider, socket, bad())
    assert not store.snapshot(key)["ready"]
    assert store.snapshot(key)["stale"]
    assert provider.request_reconcile.call_count == 1
    provider._last_transport_message = clock()
    provider.watchdog_once()
    assert provider.request_reconcile.call_count == 2
    rest.side_effect = None
    provider.reconcile_once(key)
    assert store.snapshot(key)["ready"]
    assert store.snapshot(key)["status"] == "rest"
    assert key in provider._ws_errors


def test_invalid_ticks_do_not_refresh_rest_fallback_age(stream):
    provider, store, key, socket, clock, rest = stream
    send(provider, socket, bad())
    provider.reconcile_once(key)
    before = store.snapshot(key)
    assert before["ready"] and before["status"] == "rest"
    clock.now += 31
    send(provider, socket, bad())
    current = store.snapshot(key)
    assert current["stale"] and not current["ready"]
    assert current["last_valid_update"] == before["last_valid_update"]
    assert rest.call_count == 2
    assert provider.request_reconcile.call_count == 1


def test_historical_or_replayed_frame_cannot_end_ws_fault(stream):
    provider, store, key, socket, clock, rest = stream
    event = int(clock() * 1000)
    send(provider, socket, {**raw(T, close=11.5), "event_time_ms": event})
    send(provider, socket, bad())
    provider.reconcile_once(key)
    # Без нового времени получения этот кадр ещё не доказывает восстановление.
    # Следующее сообщение с тем же event_time уже является replay для reducer.
    send(provider, socket, {**raw(T, close=11.5), "event_time_ms": event})
    before = store.snapshot(key)
    clock.now += 1
    send(provider, socket, raw(T - 1800000, close=11.7))
    send(provider, socket, {**raw(T, close=11.6), "event_time_ms": event})
    current = store.snapshot(key)
    assert current["status"] == "rest" and current["ws_error"]
    assert current["last_valid_update"] == before["last_valid_update"]
    assert key in provider._ws_errors


def test_invalid_symbol_is_isolated_from_other_stream_and_lifecycle(stream):
    provider, store, key, socket, clock, rest = stream
    other = StreamKey("bingx", "perpetual", "BTCUSDT", "30m")
    provider.sync_subscriptions([key, other])
    provider.acknowledge(other)
    provider.reconcile_once(other)
    other_before = store.snapshot(other)
    send(provider, socket, bad())
    for _ in range(10):
        send(provider, socket, bad())
    assert store.snapshot(other) == other_before
    clock.now += 1
    send(provider, socket, raw(T, close=11.8), "BTC-USDT@kline_30min")
    assert store.snapshot(other)["ready"]
    assert store.snapshot(other)["candles"][-1]["closePrice"] == 11.8
    assert key in provider._ws_errors and other not in provider._ws_errors
    provider.sync_subscriptions([other])
    assert key not in provider._ws_errors
    provider.sync_subscriptions([key, other])
    assert key not in provider._ws_errors
    provider.reconcile_once(key)
    send(provider, socket, bad())
    assert provider._ws_errors[key]["rejected"] == 1


@pytest.mark.parametrize("changes", [
    {"high": 10.5}, {"low": 10.5}, {"open": 0}, {"close": -1}, {"volume": -1},
])
def test_ohlcv_diagnostics_contain_only_validated_numeric_fields(changes):
    key = StreamKey("bingx", "perpetual", "ZECUSDT", "30m")
    row = {**raw(T), **changes, "untrusted": "Bearer hidden-value"}
    original = deepcopy(row)
    with pytest.raises(ValueError, match="OHLCV-инварианты") as raised:
        normalize_candle(row, key, now=T / 1000 + 135)
    message = str(raised.value)
    assert all(name in message for name in ("t=", "O=", "H=", "L=", "C=", "V="))
    assert "hidden-value" not in message and "untrusted" not in message
    assert row == original
