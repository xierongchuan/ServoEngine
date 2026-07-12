import asyncio

import pytest

from src.telegram_panel.backend.routes import config_routes


def test_panel_strategy_instances_sync_derived_fields(tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "default.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles_dir)

    active = {
        "strategy_instances": [
            {"id": "btc_hybrid", "symbol": "BTC-USDT", "strategy": "hybrid", "profile": "default"},
            {"id": "btc_macdx", "symbol": "BTCUSDT", "strategy": "MACDX", "profile": "default"},
            {"id": "eth_grid", "symbol": "ETHUSDT", "strategy": "GRID", "profile": "default", "enabled": False},
        ],
        "disabled_symbols": [],
    }

    synced = config_routes._sync_active_derived_fields(active)

    assert [item["id"] for item in synced["strategy_instances"]] == ["btc_hybrid", "btc_macdx", "eth_grid"]
    assert synced["strategy_instances"][0]["symbol"] == "BTCUSDT"
    assert synced["symbols"]["bingx"] == ["BTCUSDT"]
    assert synced["strategy"] == "HYBRID"
    assert "symbol_profiles" not in synced


def test_panel_strategy_instance_requires_existing_profile(tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles_dir)

    with pytest.raises(config_routes.HTTPException) as exc:
        config_routes._validate_strategy_instance({
            "symbol": "BTCUSDT",
            "strategy": "HYBRID",
            "profile": "missing_profile",
        })

    assert exc.value.status_code == 400
    assert "Profile not found" in exc.value.detail


def test_panel_strategy_instance_rejects_incompatible_profile(tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "btc_aggressive.json").write_text(
        '{"_strategy": "SCALP"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles_dir)

    with pytest.raises(config_routes.HTTPException) as exc:
        config_routes._validate_strategy_instance({
            "symbol": "BTCUSDT",
            "strategy": "MACDX",
            "profile": "btc_aggressive",
        })

    assert exc.value.status_code == 400
    assert "belongs to strategy 'SCALP'" in exc.value.detail


def test_panel_strategy_instances_sync_removes_legacy_profile_map(tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "default.json").write_text("{}", encoding="utf-8")
    (profiles_dir / "macdx_safe.json").write_text('{"_strategy": "MACDX"}', encoding="utf-8")
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles_dir)

    active = {
        "strategy_instances": [
            {"id": "btc_macdx", "symbol": "BTCUSDT", "strategy": "MACDX", "profile": "macdx_safe"},
        ],
        "symbol_profiles": {"BTCUSDT": "default"},
    }

    synced = config_routes._sync_active_derived_fields(active)

    assert synced["strategy_instances"][0]["profile"] == "macdx_safe"
    assert "symbol_profiles" not in synced


def test_panel_explicit_empty_instances_do_not_restore_legacy_symbols(tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles_dir)

    active = {
        "strategy_instances": [],
        "strategy": "MACDX",
        "symbols": {"bingx": ["BTCUSDT"]},
        "symbol_profiles": {"BTCUSDT": "default"},
    }

    assert config_routes._get_strategy_instances(active) == []
    synced = config_routes._sync_active_derived_fields(active)
    assert "symbol_profiles" not in synced


def test_panel_profiles_response_groups_compatible_profiles(tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "default.json").write_text('{"_strategy": null}', encoding="utf-8")
    (profiles_dir / "eth_conservative.json").write_text('{"_strategy": "MACDX"}', encoding="utf-8")
    (profiles_dir / "btc_aggressive.json").write_text('{"_strategy": "SCALP"}', encoding="utf-8")
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(config_routes, "_use_new_config_system", lambda: True)

    response = asyncio.run(config_routes.list_profiles())

    assert response["profile_strategies"]["eth_conservative"] == "MACDX"
    assert "eth_conservative" in response["compatible_by_strategy"]["MACDX"]
    assert "eth_conservative" not in response["compatible_by_strategy"]["GRID"]
    assert "eth_conservative" not in response["compatible_by_strategy"]["AISCALP"]
    assert response["compatible_by_strategy"]["GRID"] == ["default"]


def test_panel_strategy_instances_legacy_fallback(tmp_path, monkeypatch):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "default.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_routes, "PROFILES_DIR", profiles_dir)

    active = {
        "strategy": "MACDX",
        "symbols": {"bingx": ["BTCUSDT", "ETH-USDT"]},
        "symbol_profiles": {"ETHUSDT": "default"},
    }

    instances = config_routes._get_strategy_instances(active)

    assert [item["id"] for item in instances] == ["btcusdt_macdx", "ethusdt_macdx"]
    assert [item["symbol"] for item in instances] == ["BTCUSDT", "ETHUSDT"]
    assert all(item["strategy"] == "MACDX" for item in instances)
