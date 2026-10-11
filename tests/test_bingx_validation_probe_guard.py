"""Проверка archived payload никогда не меняет ledger и не отправляет торговую заявку."""

import json
import sqlite3

import pytest

from probe_bingx_order_validation import ValidationGuard, archived_request, main


@pytest.mark.parametrize("method,url,redirect", [
    ("POST", "https://open-api.bingx.com/openApi/swap/v2/trade/order/test", False),
    ("POST", "https://open-api-vst.bingx.com/openApi/swap/v2/trade/order", False),
    ("DELETE", "https://open-api-vst.bingx.com/openApi/swap/v2/trade/order", False),
    ("POST", "https://open-api-vst.bingx.com/openApi/swap/v2/trade/leverage", False),
    ("POST", "https://open-api-vst.bingx.com/openApi/swap/v2/trade/order/test", True),
    ("POST", "https://open-api-vst.bingx.com:444/openApi/swap/v2/trade/order/test", False),
    ("GET", "https://open-api-vst.bingx.com/unknown", False),
])
def test_validation_blocks_every_trading_mutation_and_redirect(method, url, redirect):
    with pytest.raises(RuntimeError):
        ValidationGuard().check(method, url, {"allow_redirects": redirect})


def test_validation_post_is_never_repeated():
    guard = ValidationGuard()
    url = "https://open-api-vst.bingx.com/openApi/swap/v2/trade/order/test"
    guard.check("POST", url, {"allow_redirects": False})
    with pytest.raises(RuntimeError):
        guard.check("POST", url, {"allow_redirects": False})
    assert guard.validations == 1
    guard.deadline = 0
    with pytest.raises(RuntimeError):
        guard.check("GET", "https://open-api-vst.bingx.com/openApi/swap/v2/server/time", {"allow_redirects": False})


def test_validation_requires_explicit_optin():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def test_archive_read_only_and_wal_failclosed(tmp_path):
    path = tmp_path / "probe.sqlite"
    params = {"symbol": "BTC-USDT", "type": "MARKET", "stopLoss": "saved", "takeProfit": "saved"}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE wires(account TEXT, command_id TEXT, event TEXT)")
        db.execute("INSERT INTO wires VALUES (?,?,?)", ("bounded-vst-probe", "vst-entry",
            json.dumps({"endpoint": "/openApi/swap/v2/trade/order", "request_params": params})))
    before = path.read_bytes()
    assert archived_request(path) == params
    assert before == path.read_bytes()
    wal = tmp_path / "probe.sqlite-wal"
    wal.write_bytes("неперенесённый WAL".encode())
    with pytest.raises(RuntimeError, match="WAL"):
        archived_request(path)
