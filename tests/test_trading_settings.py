"""Контракт исполнения: без сети, реальных ключей и торговых операций."""

import asyncio
from decimal import Decimal

import json
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from src import config_loader, trading_settings
from src.exchanges.config.base import ConfigFactory
from src.exchanges.config.mexc_config import MEXCFuturesConfig
from src.exchanges import exchange_factory
from src.telegram_panel.backend.routes import config_routes, runtime


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config_loader, "_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(trading_settings, "_runtime_settings", None)
    path = tmp_path / "trading.json"
    path.write_text(json.dumps({"execution": {}, "features": {"enable_news": False}}), encoding="utf-8")
    return path


def test_safe_defaults_and_detached_snapshot(settings_file):
    first = trading_settings.get_trading_settings()
    assert first["execution_mode"] == "read_only"
    first["protection"]["failure_policy"] = "close"
    settings_file.write_text('{"execution":{"execution_mode":"live"}}', encoding="utf-8")
    config_loader.reload_config()
    assert trading_settings.get_trading_settings()["execution_mode"] == "read_only"
    assert trading_settings.get_trading_settings()["protection"]["failure_policy"] == "pause"
    assert trading_settings.read_trading_settings(settings_file)["execution_mode"] == "live"


def test_operational_env_ignored(settings_file, monkeypatch):
    for key, value in {"EXCHANGE": "mexc", "MARKET_TYPE": "spot", "MODE": "real", "ENABLE_NEWS": "true",
                       "MEXC_ENABLE_LIVE_TRADING": "true", "MEXC_MARGIN_MODE": "cross",
                       "MEXC_POSITION_MODE": "one_way", "MEXC_SETTLE_ASSET": "BTC"}.items():
        monkeypatch.setenv(key, value)
    settings = trading_settings.get_trading_settings()
    assert settings == trading_settings.DEFAULT_TRADING_SETTINGS
    config = MEXCFuturesConfig()
    assert not config.live_trading_enabled
    assert config.margin_mode == "isolated"
    assert config.position_mode == "hedge"
    assert config.settle_asset == "USDT"
    legacy = config_loader.convert_to_legacy_format({}, "MACDX")
    assert legacy["ENABLE_NEWS"] is False


@pytest.mark.parametrize("patch", [
    {"margin_mode": "cross"}, {"exchange": "binance"}, {"exchange": []},
    {"market_type": "spot"}, {"execution_mode": "real"}, {"settle_asset": "BTC"},
    {"api_key": "fake"}, {"protection": None}, {"protection": {"trigger_price": "index"}},
    {"protection": {"_unknown": 1}}, {"market_data": {"max_age_seconds": True}},
    {"market_data": {"history_limit": 1.5}}, {"protection": {"recovery_attempts": -1}},
    {"position_mode": "one_way", "grid_mode": "hedged"},
    {"exchange": "mexc", "market_type": "spot", "position_mode": "hedge"},
    {"exchange": "mexc", "market_type": "spot", "protection": {"trigger_price": "mark"}},
])
def test_invalid_contract_rejected(patch):
    with pytest.raises(ValueError):
        trading_settings.validate_trading_settings(patch)


@pytest.mark.parametrize("market,position,grid", [("perpetual", "hedge", "hedged"), ("perpetual", "one_way", "inventory"), ("spot", "one_way", "inventory")])
def test_mexc_modes_and_demo_is_read_only(market, position, grid, monkeypatch):
    monkeypatch.setenv("MEXC_API_KEY", "fake-key")
    monkeypatch.setenv("MEXC_SECRET_KEY", "fake-secret")
    settings = trading_settings.validate_trading_settings({"exchange": "mexc", "market_type": market,
        "position_mode": position, "grid_mode": grid, "execution_mode": "demo"})
    assert settings["execution_mode"] == "read_only"
    config = ConfigFactory.create("mexc", market_type=market, settings=settings)
    assert config.is_demo is False
    assert config.base_url == "https://api.mexc.com"
    assert config.testnet_url == ""
    assert not config.live_trading_enabled
    assert "fake-secret" not in json.dumps(config.to_dict())
    config_live = ConfigFactory.create("mexc", settings={**settings, "execution_mode": "live"})
    assert config_live.live_trading_enabled


def test_legacy_config_constants_ignore_operational_env(monkeypatch):
    for key, value in {"EXCHANGE": "mexc", "MARKET_TYPE": "spot", "MODE": "real", "ENABLE_NEWS": "true"}.items():
        monkeypatch.setenv(key, value)
    code = ('from src import config; from src.trading_settings import get_trading_settings; '
            's=get_trading_settings(); assert config.EXCHANGE==s["exchange"]; '
            'assert config.MARKET_TYPE==s["market_type"]; '
            'assert config.MODE==("real" if s["execution_mode"]=="live" else s["execution_mode"]); '
            'assert config.ENABLE_NEWS == config.BOT_CONFIG["ENABLE_NEWS"]')
    subprocess.run([sys.executable, "-c", code], check=True, timeout=20, capture_output=True, text=True)


def test_alternate_mexc_config_never_inherits_bingx_live(monkeypatch):
    monkeypatch.setattr(trading_settings, "_runtime_settings", trading_settings.validate_trading_settings({"execution_mode": "live"}))
    assert not ConfigFactory.create("mexc").live_trading_enabled
    assert exchange_factory._selection("mexc") == ("mexc", "perpetual", False)
    with pytest.raises(ValueError):
        exchange_factory._selection("mexc", settings={"exchange": "bingx", "execution_mode": "live"})


def test_bingx_demo_endpoint_explicit():
    config = ConfigFactory.create("bingx", settings={"execution_mode": "demo"})
    assert config.is_demo
    assert config.base_url == "https://open-api-vst.bingx.com"
    with pytest.raises(ValueError):
        ConfigFactory.create("bingx", market_type="spot")


def test_factory_read_only_blocks_mutations_and_allows_reads(monkeypatch, settings_file):
    client = Mock()
    client.get_balance.return_value = 123
    monkeypatch.setattr(exchange_factory, "_create_client", lambda *args, **kwargs: client)
    exchange_factory.reset_client()
    selected = exchange_factory.get_trading_client()
    assert selected.get_balance() == 123
    assert selected.check_prerequisites() is False
    for method in ("place_order", "set_leverage", "set_sl_tp", "cancel_order", "cancel_all_orders", "close_position", "_make_request"):
        with pytest.raises(PermissionError):
            getattr(selected, method)("BTC-USDT")
        assert not getattr(client, method).called
    assert exchange_factory.get_trading_client() is selected
    # Исполнение нельзя получить как raw mock без доказанного safety-контракта.
    from src.exchanges.errors import ExchangeStateUnavailableError
    with pytest.raises(ExchangeStateUnavailableError):
        exchange_factory.get_trading_client(settings={"execution_mode": "live"})
    exchange_factory.reset_client()


def test_corrupted_settings_fail_closed(settings_file):
    settings_file.write_text('{"execution":', encoding="utf-8")
    with pytest.raises(ValueError):
        trading_settings.get_trading_settings()


def test_execution_not_overridden_by_profiles(settings_file, monkeypatch):
    monkeypatch.setattr(config_loader, "load_strategy_config", lambda strategy: {"execution": {"execution_mode": "live"}})
    monkeypatch.setattr(config_loader, "load_profile_config", lambda profile: {"execution": {"exchange": "mexc"}})
    config_loader.clear_config_cache()
    try:
        resolved = config_loader.resolve_symbol_config("BTC-USDT", strategy="MACDX")
        assert resolved["execution"] == trading_settings.DEFAULT_TRADING_SETTINGS
    finally:
        config_loader.clear_config_cache()


@pytest.fixture
def panel_files(settings_file, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "active.json").write_text('{}', encoding="utf-8")
    monkeypatch.setattr(config_routes, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_routes, "DATA_DIR", data_dir)
    monkeypatch.setattr(runtime, "STATUS_PATH", data_dir / "runtime_status.json")
    monkeypatch.setattr(runtime, "COMMAND_PATH", data_dir / "runtime_command.json")
    runtime.STATUS_PATH.write_text(json.dumps({"state": "stopped", "updated_at_ts": time.time(), "runtime_pid": None}), encoding="utf-8")
    return settings_file, data_dir


def request(body):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": []}, receive)


def test_api_save_preserves_other_sections_and_returns_saved_config(panel_files):
    path, _ = panel_files
    result = asyncio.run(config_routes.update_execution_config(request({"execution_mode": "demo"}), {}))
    assert result["needs_restart"]
    assert json.loads(path.read_text())["features"]["enable_news"] is False
    assert asyncio.run(config_routes.get_execution_config({}))["execution_mode"] == "demo"
    assert not asyncio.run(config_routes.update_execution_config(request({"execution_mode": "demo"}), {}))["needs_restart"]


@pytest.mark.parametrize("state", ["running", "starting", "stopping", "restarting", "crashed", "unavailable"])
def test_api_blocks_active_runtime_without_writing(panel_files, state):
    path, _ = panel_files
    original = path.read_bytes()
    runtime.STATUS_PATH.write_text(json.dumps({"state": state, "updated_at_ts": time.time()}), encoding="utf-8")
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request({"execution_mode": "live"}), {}))
    assert exc.value.status_code == 409
    assert path.read_bytes() == original


@pytest.mark.parametrize("filename,content", [
    ("active_trades.json", '{"BTCUSDT":{"size":1}}'),
    ("position_owners.json", '{"BTCUSDT":{"pending":true}}'),
    ("grid_state_btc.json", '{"active_orders":[1]}'),
    ("managed_state.json", '{"inventory":1}'),
    ("active_trades.json", '{broken'),
])
def test_api_blocks_managed_or_corrupt_state(panel_files, filename, content):
    path, data = panel_files
    original = path.read_bytes()
    (data / filename).write_text(content, encoding="utf-8")
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request({"exchange": "mexc"}), {}))
    assert exc.value.status_code == 409
    assert path.read_bytes() == original


@pytest.mark.parametrize("status", [None, {"state": "stopped", "updated_at_ts": 0}])
def test_api_blocks_missing_or_stale_status(panel_files, status):
    if status is None:
        runtime.STATUS_PATH.unlink()
    else:
        runtime.STATUS_PATH.write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request({"execution_mode": "live"}), {}))
    assert exc.value.status_code == 409


def test_api_blocks_pending_start(panel_files):
    runtime.COMMAND_PATH.write_text('{"id":"pending","action":"start"}', encoding="utf-8")
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request({"execution_mode": "live"}), {}))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("patch", [{"margin_mode": "cross"}, {"_secret": "fake"}, {"market_data": {"_secret": "fake"}}, [], {"execution_mode": True}])
def test_api_invalid_patch_is_atomic(panel_files, patch):
    path, _ = panel_files
    original = path.read_bytes()
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request(patch), {}))
    assert exc.value.status_code == 422
    assert path.read_bytes() == original


def test_generic_trading_endpoint_cannot_bypass_guard(panel_files):
    runtime.STATUS_PATH.write_text(json.dumps({"state": "running", "updated_at_ts": time.time()}), encoding="utf-8")
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_trading_config(request({"execution": {"execution_mode": "live"}}), {}))
    assert exc.value.status_code == 409
    result = asyncio.run(config_routes.update_trading_config(request({"features": {"enable_news": True}}), {}))
    assert result["config"]["features"]["enable_news"] is True
    assert result["config"]["execution"]["execution_mode"] == "read_only"


def test_execution_http_contract_uses_panel_auth(panel_files):
    app = FastAPI()
    app.include_router(config_routes.router)
    def denied_user():
        raise config_routes.HTTPException(status_code=401, detail="Нет авторизации")
    app.dependency_overrides[config_routes.get_current_user] = denied_user
    with TestClient(app) as client:
        assert client.get("/api/config/execution").status_code == 401
        assert client.put("/api/config/execution", json={"execution_mode": "live"}).status_code == 401
        app.dependency_overrides[config_routes.get_current_user] = lambda: {"auth_method": "test"}
        assert client.get("/api/config/execution").json()["execution_mode"] == "read_only"
        assert client.put("/api/config/execution", json={"margin_mode": "cross"}).status_code == 422
        response = client.put("/api/config/execution", json={"exchange": "mexc", "execution_mode": "demo"})
        assert response.status_code == 200
        assert response.json()["execution"]["execution_mode"] == "read_only"
        assert response.json()["needs_restart"]


@pytest.mark.parametrize("patch", [
    {"protection": {"recovery_attempts": 5}}, {"market_data": {"history_limit": 1000}},
    {"spot": {"quote_budget_usdt": 100.5}}, {"spot": {"allow_local_protection": True}},
    {"account_id": "another-account"},
])
def test_all_execution_changes_require_stopped_runtime(panel_files, patch):
    path, _ = panel_files
    original = path.read_bytes()
    runtime.STATUS_PATH.write_text(json.dumps({"state": "running", "updated_at_ts": time.time()}), encoding="utf-8")
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request(patch), {}))
    assert exc.value.status_code == 409
    assert path.read_bytes() == original


@pytest.mark.parametrize("key,value", [("exchange", "mexc"), ("market_type", "spot"), ("execution_mode", "live"), ("position_mode", "one_way")])
def test_each_account_switch_key_is_guarded(panel_files, key, value):
    path, _ = panel_files
    path.write_text(json.dumps({"execution": {"exchange": "mexc", "grid_mode": "inventory", "protection": {"trigger_price": "last"}}}), encoding="utf-8")
    patch = {key: value}
    if key == "exchange":
        patch[key] = "bingx"
    runtime.STATUS_PATH.write_text(json.dumps({"state": "running", "updated_at_ts": time.time()}), encoding="utf-8")
    if key == "market_type":
        patch["position_mode"] = "one_way"
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request(patch), {}))
    assert exc.value.status_code == 409


def test_mexc_read_only_allows_private_reads_but_not_mutations(monkeypatch):
    from src.exchanges.impl.mexc_transport import MEXCFuturesTransport
    from src.exchanges.errors import ExchangeAPIError
    monkeypatch.setenv("MEXC_API_KEY", "fake")
    monkeypatch.setenv("MEXC_SECRET_KEY", "fake")
    transport = MEXCFuturesTransport(ConfigFactory.create("mexc", settings={"exchange": "mexc"}))
    transport.assert_private_allowed()
    with pytest.raises(ExchangeAPIError):
        transport.assert_mutation_allowed()


def test_atomic_replace_failure_preserves_original(panel_files, monkeypatch):
    path, _ = panel_files
    original = path.read_bytes()
    def fail_replace(*args):
        raise OSError("Тестовый отказ записи")
    monkeypatch.setattr(config_routes.os, "replace", fail_replace)
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request({"execution_mode": "demo"}), {}))
    assert exc.value.status_code == 500
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".trading.json.*.tmp"))


@pytest.mark.parametrize("value,expected", [(0, 0), (12, 12), (12.25, 12.25),
    (Decimal("12.25"), 12.25), (Decimal("12"), 12), (Decimal("1e-20"), 1e-20),
        (Decimal("1.1e1000"), 11 * 10 ** 999)])
def test_spot_total_budget_accepts_json_numbers(value, expected):
    selected = trading_settings.validate_trading_settings({"spot": {"quote_budget_usdt": value}})
    assert selected["spot"] == {"quote_budget_usdt": expected, "allow_local_protection": False}
    assert json.loads(json.dumps(selected, allow_nan=False))["spot"]["quote_budget_usdt"] == expected


@pytest.mark.parametrize("value", [True, False, "10", None, -1, -0.1, float("nan"),
    float("inf"), Decimal("NaN"), Decimal("Infinity"), Decimal("-1"),
    Decimal("9" * 309 + ".1"), Decimal("1.1e-1000")])
def test_spot_budget_invalid_number_rejected(value):
    with pytest.raises(ValueError, match="quote_budget_usdt"):
        trading_settings.validate_trading_settings({"spot": {"quote_budget_usdt": value}})


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_local_protection_requires_actual_bool(value):
    with pytest.raises(ValueError, match="allow_local_protection"):
        trading_settings.validate_trading_settings({"spot": {"allow_local_protection": value}})


@pytest.mark.parametrize("account_id", ["", "UPPER", "../default", "key:secret", "a" * 65, "a\n", None, 1])
def test_account_id_rejects_invalid_namespace(account_id):
    with pytest.raises(ValueError, match="account_id"):
        trading_settings.validate_trading_settings({"account_id": account_id})


def test_account_label_is_stable_across_key_rotation(monkeypatch):
    selected = trading_settings.validate_trading_settings({"account_id": "account_2-test", "exchange": "mexc", "execution_mode": "live"})
    for key in ("fake-key-a", "fake-key-b"):
        monkeypatch.setenv("MEXC_API_KEY", key)
        assert trading_settings.execution_account_label(selected) == "account_2-test:mexc:live"
    assert trading_settings.execution_account_label({}) == "default:bingx:read_only"


def test_spot_save_disabled_then_start_requires_budget_and_explicit_consent(panel_files):
    result = asyncio.run(config_routes.update_execution_config(request({
        "exchange": "mexc", "market_type": "spot", "execution_mode": "live",
        "position_mode": "one_way", "grid_mode": "inventory", "protection": {"trigger_price": "last"},
    }), {}))
    selected = result["execution"]
    assert selected["spot"] == {"quote_budget_usdt": 0, "allow_local_protection": False}
    with pytest.raises(ValueError, match="бюджет"):
        trading_settings.validate_trading_start(selected)
    selected["spot"]["quote_budget_usdt"] = 100.25
    with pytest.raises(ValueError, match="риск"):
        trading_settings.validate_trading_start(selected)
    selected["spot"]["allow_local_protection"] = True
    assert trading_settings.validate_trading_start(selected) == selected
    selected["execution_mode"] = "read_only"
    selected["spot"] = {"quote_budget_usdt": 0, "allow_local_protection": False}
    assert trading_settings.validate_trading_start(selected) == selected


@pytest.mark.parametrize("patch", [{"spot": {"unknown": 1}}, {"spot": None}, {"spot": {"quote_budget_usdt": "10"}}, {"account_id": "bad:id"}])
def test_new_settings_api_invalid_patch_is_atomic(panel_files, patch):
    path, _ = panel_files
    original = path.read_bytes()
    with pytest.raises(config_routes.HTTPException) as exc:
        asyncio.run(config_routes.update_execution_config(request(patch), {}))
    assert exc.value.status_code == 422
    assert path.read_bytes() == original


def test_idempotent_execution_put_allowed_while_running(panel_files):
    runtime.STATUS_PATH.write_text(json.dumps({"state": "running", "updated_at_ts": time.time()}), encoding="utf-8")
    result = asyncio.run(config_routes.update_execution_config(request({"spot": {"quote_budget_usdt": 0}}), {}))
    assert not result["needs_restart"]
