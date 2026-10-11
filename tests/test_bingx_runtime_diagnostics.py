"""Регрессии control-шума и подтверждённых публичных форматов BingX, без сети."""

import json
import os
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src import symbol_runtime_control as control
from src.exchanges.bingx_ws_data_provider import BingXWebSocketDataProvider
from src.exchanges.market_data import StreamKey, StreamStore, normalize_candle
from src.exchanges.public_market_rest import PublicRESTFetcher
from src.trading_settings import get_trading_settings
from test_market_data_layer import Clock, T, raw
from test_market_data_ws_runtime import FakeSocket


def command(action="start", **changes):
    return {"id": "command-1", "action": action, "instance_id": "btc_macdx",
            "runtime_pid": os.getpid(), "execution": get_trading_settings(), **changes}


@pytest.mark.parametrize("action", ["start", "restart"])
def test_rejected_revision_warns_once_without_modifying_file(tmp_path, monkeypatch, action):
    path = tmp_path / "symbol_runtime_command.json"
    control.atomic_write_json(path, command(action, runtime_pid=os.getpid() + 10000))
    original = path.read_bytes()
    warnings = Mock()
    monkeypatch.setattr(control, "warning", warnings)
    reader = control.SymbolCommandReader(path)
    for _ in range(10):
        assert reader.read() is None
    warnings.assert_called_once()
    assert "другому runtime" in warnings.call_args.args[0]
    assert path.read_bytes() == original

    fresh = command(action, id="fresh-command")
    control.atomic_write_json(path, fresh)
    assert reader.read() == fresh
    assert reader.read() is None
    warnings.assert_called_once()


def test_new_invalid_revisions_keep_execution_and_json_checks(tmp_path, monkeypatch):
    path = tmp_path / "symbol_runtime_command.json"
    warnings = Mock()
    monkeypatch.setattr(control, "warning", warnings)
    reader = control.SymbolCommandReader(path)
    assert reader.read() is None
    settings = deepcopy(get_trading_settings())
    settings["execution_mode"] = "live" if settings["execution_mode"] == "read_only" else "read_only"
    control.atomic_write_json(path, command(execution=settings))
    assert reader.read() is None
    assert reader.read() is None
    warnings.assert_called_once()
    assert "не может менять execution" in warnings.call_args.args[0]

    path.write_text("{", encoding="utf-8")
    assert reader.read() is None
    assert reader.read() is None
    assert warnings.call_count == 2
    fresh = command(id="in-place")
    path.write_text(json.dumps(fresh), encoding="utf-8")
    assert reader.read() == fresh
    assert reader.read() is None
    path.unlink()
    assert reader.read() is None
    control.atomic_write_json(path, fresh)
    assert reader.read() == fresh
    assert reader.read() is None
    assert warnings.call_count == 2


def test_hour_subscription_ack_and_captured_wire_merge_use_60min():
    start = 1791727200000
    clock = Clock(start / 1000 + 120)
    key = StreamKey("bingx", "perpetual", "ETH-USDT", "1h")
    store = StreamStore(settings={"history_limit": 3, "min_history": 2}, clock=clock)
    rest = Mock(return_value=[raw(start - 3600000), raw(start)])
    provider = BingXWebSocketDataProvider(store=store, rest_fetcher=rest, publisher=Mock(),
                                         clock=clock, monotonic=clock)
    socket = FakeSocket()
    try:
        provider.sync_subscriptions([key])
        provider._ws = socket
        provider._on_open(socket)
        request = socket.sent[0]
        assert request["dataType"] == "ETH-USDT@kline_60min"
        provider._on_message(socket, json.dumps({"id": request["id"], "code": 0}))
        assert key in provider._acked
        provider.reconcile_once(key)
        calls = rest.call_count
        provider._on_message(socket, json.dumps({
            "dataType": "ETH-USDT@kline_60min",
            "data": {"t": start, "T": 1791730799999, "i": "60min", "s": "ETH-USDT",
                     "o": "10", "h": "12", "l": "9", "c": "11.5", "v": "2"},
        }))
        snapshot = store.snapshot(key)
        assert snapshot["stream"] == key.to_dict()
        assert [row["timestamp"] for row in snapshot["candles"]] == [start - 3600000, start]
        assert snapshot["candles"][-1]["closePrice"] == 11.5
        assert snapshot["candles"][-1]["source"] == "ws"
        assert snapshot["subscribed"]
        assert rest.call_count == calls
    finally:
        provider.stop()


def test_captured_daily_ws_window_is_not_relabelled_as_utc_rest():
    key = StreamKey("bingx", "perpetual", "LTCUSDT", "1d")
    wire = {"t": 1791648000000, "T": 1791734399999, "i": "1day", "s": "LTC-USDT",
            "o": "80", "h": "82", "l": "79", "c": "81", "v": "100"}
    original = deepcopy(wire)
    with pytest.raises(ValueError, match="границей 1d"):
        normalize_candle(wire, key, source="ws", now=1791727320)
    assert wire == original
    utc_start = 1791676800000
    candle = normalize_candle(raw(utc_start), key, source="rest", now=1791727320)
    assert candle["timestamp"] == utc_start
    assert candle["source"] == "rest"


@pytest.mark.parametrize("interval", ["1h", "1d", "1w", "1M"])
def test_bingx_rest_preserves_exact_interval_and_utc_timezone(interval):
    response = Mock(status_code=200, headers={})
    response.json.return_value = {"code": 0, "data": []}
    request = Mock(return_value=response)
    fetcher = PublicRESTFetcher(request=request, rate_limiters={"bingx": Mock()})
    key = StreamKey("bingx", "perpetual", "LTCUSDT", interval)
    assert fetcher._page(key, T, T + 1, 1) == []
    params = request.call_args.kwargs["params"]
    assert params["symbol"] == "LTC-USDT"
    assert params["interval"] == interval
    assert params["timeZone"] == 0
