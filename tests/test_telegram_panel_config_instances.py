import pytest

from src.telegram_panel.backend.routes import config_routes


def test_panel_strategy_instances_sync_legacy_fields(tmp_path, monkeypatch):
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

    synced = config_routes._sync_active_legacy_fields(active)

    assert [item["id"] for item in synced["strategy_instances"]] == ["btc_hybrid", "btc_macdx", "eth_grid"]
    assert synced["strategy_instances"][0]["symbol"] == "BTCUSDT"
    assert synced["symbols"]["bingx"] == ["BTCUSDT"]
    assert synced["strategy"] == "HYBRID"
    assert synced["symbol_profiles"]["BTCUSDT"] == "default"


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
