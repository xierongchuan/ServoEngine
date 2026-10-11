"""Opt-in probe не может выполнить production mutation/WS даже при override URL."""

from types import SimpleNamespace

import pytest

from probe_grid_bingx_demo import VSTProbeGuard, main, response_shape


@pytest.mark.parametrize("method,url,kwargs", [
    ("POST", "https://open-api.bingx.com/openApi/swap/v2/trade/order", {"allow_redirects": False}),
    ("GET", "https://open-api.bingx.com/x?signature=secret", {"allow_redirects": False}),
    ("GET", "https://open-api.bingx.com/x", {"headers": {"X-BX-APIKEY": "fake"}, "allow_redirects": False}),
    ("POST", "https://open-api-vst.bingx.com/unknown", {"allow_redirects": False}),
    ("GET", "https://open-api-vst.bingx.com/x", {"allow_redirects": True}),
    ("GET", "https://open-api-vst.bingx.com.evil.example/x", {"allow_redirects": False}),
    ("GET", "https://open-api-vst.bingx.com:444/x", {"allow_redirects": False}),
])
def test_probe_http_rejects_production_and_redirects(method, url, kwargs):
    with pytest.raises(RuntimeError):
        VSTProbeGuard().check_http(method, url, kwargs)


@pytest.mark.parametrize("url,redirect", [
    ("wss://open-api-swap.bingx.com/swap-market?listenKey=fake", 0),
    ("wss://vst-open-api-ws.bingx.com/swap-market", 1),
    ("wss://vst-open-api-ws.bingx.com.evil.example/swap-market", 0),
    ("ws://vst-open-api-ws.bingx.com/swap-market", 0),
    ("wss://vst-open-api-ws.bingx.com/other", 0),
])
def test_probe_ws_rejects_production_or_redirect(url, redirect):
    with pytest.raises(RuntimeError):
        VSTProbeGuard.check_ws(url, {"redirect_limit": redirect})


def test_probe_has_no_network_without_explicit_optin():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def test_probe_deadline_and_request_budget_fail_closed(monkeypatch):
    guard = VSTProbeGuard()
    guard.deadline = 0
    with pytest.raises(RuntimeError):
        guard.check_http("GET", "https://open-api-vst.bingx.com/x", {"allow_redirects": False})
    guard.deadline = float("inf")
    guard.reads = 150
    with pytest.raises(RuntimeError):
        guard.check_http("GET", "https://open-api-vst.bingx.com/x", {"allow_redirects": False})


def test_probe_never_repeats_listenkey_post():
    guard = VSTProbeGuard()
    guard.client = SimpleNamespace(settings={"execution_mode": "demo"}, _local=SimpleNamespace(intent={"operation": "grid_entry"}))
    url = "https://open-api-vst.bingx.com/openApi/user/auth/userDataStream"
    kwargs = {"allow_redirects": False, "data": ""}
    guard.check_http("POST", url, kwargs)
    with pytest.raises(RuntimeError):
        guard.check_http("POST", url, kwargs)
    assert guard.counts["listen"] == 1


def test_listen_diagnostic_can_never_send_order_or_leverage():
    guard = VSTProbeGuard(listen_only=True)
    guard.client = SimpleNamespace(settings={"execution_mode": "demo"}, _local=SimpleNamespace(intent={"operation": "grid_listen"}))
    for endpoint in ("order", "leverage"):
        with pytest.raises(RuntimeError):
            guard.check_http("POST", f"https://open-api-vst.bingx.com/openApi/swap/v2/trade/{endpoint}",
                             {"allow_redirects": False, "data": "symbol=BTC-USDT"})
    assert sum(guard.counts.values()) == 0


def test_response_shape_never_serializes_secret_values():
    import json
    summary = response_shape({"listenKey": "SECRET-TOP", "code": 0, "data": {"listenKey": "SECRET-NESTED"}})
    assert summary["fields"] == ["code", "data", "listenKey"] and summary["code"] == 0
    assert "SECRET" not in json.dumps(summary)
    assert "SECRET" not in json.dumps(response_shape({"code": "SECRET"}))
