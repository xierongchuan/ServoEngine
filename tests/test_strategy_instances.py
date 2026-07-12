import json
from datetime import datetime, timedelta, timezone

from src.config_loader import get_strategy_instances, resolve_strategy_instance_config, resolve_symbol_config
from src.core.position_ownership import PositionOwnershipStore


def test_get_strategy_instances_from_new_active_schema(monkeypatch):
    active = {
        "strategy_instances": [
            {"id": "btc_macdx", "symbol": "BTCUSDT", "strategy": "MACDX", "profile": "default"},
            {"id": "btc_hybrid", "symbol": "BTCUSDT", "strategy": "HYBRID", "profile": "default"},
            {"id": "eth_aiscalp", "symbol": "ETHUSDT", "strategy": "AISCALP", "enabled": False},
        ],
        "disabled_symbols": [],
    }
    monkeypatch.setattr("src.config_loader.load_active_config", lambda: active)

    instances = get_strategy_instances("bingx")

    assert [i.id for i in instances] == ["btc_macdx", "btc_hybrid"]
    assert instances[0].symbol == "BTCUSDT"
    assert instances[0].strategy == "MACDX"


def test_get_strategy_instances_legacy_fallback(monkeypatch):
    active = {
        "strategy": "HYBRID",
        "symbols": {"bingx": ["BTCUSDT", "ETHUSDT"]},
        "symbol_profiles": {"BTCUSDT": "default", "ETHUSDT": "eth_conservative"},
        "disabled_symbols": [],
    }
    monkeypatch.setattr("src.config_loader.load_active_config", lambda: active)

    instances = get_strategy_instances("bingx")

    assert [i.id for i in instances] == ["btcusdt_hybrid", "ethusdt_hybrid"]
    assert [i.strategy for i in instances] == ["HYBRID", "HYBRID"]


def test_get_strategy_instances_explicit_empty_list_is_canonical(monkeypatch):
    active = {
        "strategy_instances": [],
        "strategy": "HYBRID",
        "symbols": {"bingx": ["BTCUSDT"]},
        "symbol_profiles": {"BTCUSDT": "default"},
    }
    monkeypatch.setattr("src.config_loader.load_active_config", lambda: active)

    assert get_strategy_instances("bingx") == []


def test_resolve_symbol_config_reads_profile_from_matching_instance(monkeypatch):
    active = {
        "strategy_instances": [
            {"id": "btc_macdx", "symbol": "BTC-USDT", "strategy": "MACDX", "profile": "macdx_1h"},
            {"id": "btc_scalp", "symbol": "BTCUSDT", "strategy": "SCALP", "profile": "scalp_no_ai"},
        ],
        # Устаревшее поле не должно перебить instance-level profile.
        "symbol_profiles": {"BTCUSDT": "default"},
    }
    monkeypatch.setattr("src.config_loader.load_base_config", lambda: {})
    monkeypatch.setattr("src.config_loader.load_trading_config", lambda: {})
    monkeypatch.setattr("src.config_loader.load_active_config", lambda: active)
    monkeypatch.setattr("src.config_loader.load_strategy_config", lambda strategy: {"strategy_name": strategy})
    monkeypatch.setattr("src.config_loader.load_profile_config", lambda name: {"profile_name": name})
    monkeypatch.setattr("src.config_loader.validate_profile_strategy_match", lambda *args: True)
    monkeypatch.setattr("src.config_loader._resolved_configs", {})

    resolved = resolve_symbol_config("BTCUSDT", "MACDX")

    assert resolved["profile_name"] == "macdx_1h"
    assert resolved["_resolved"]["profile"] == "macdx_1h"


def test_resolve_strategy_instance_config_uses_profile_overrides(monkeypatch):
    active = {
        "symbols": {"bingx": ["BTCUSDT"]},
        "disabled_symbols": [],
    }

    def fake_resolve_symbol_config(symbol, strategy=None, profile=None):
        assert symbol == "BTCUSDT"
        assert strategy == "SCALP"
        assert profile == "btc_aggressive"
        return {
            "exchange": {"fees": {"bingx": {"maker": 0.02, "taker": 0.05}}},
            "position": {"size_percent": 30, "min_trade_amount_usdt": 25},
            "risk": {"min_confidence_threshold": 0.61},
            "features": {"enable_parallel_mode": True, "aggressive_mode": True},
            "aggressive_settings": {"min_confidence": 0.6},
            "ai_thresholds": {"rsi_oversold": 25},
            "preset": {
                "timeframe": "1m",
                "chart_period": "15m",
                "plotter_period": "1h",
                "leverage": 15,
            },
            "signal_rules": {"min_score_for_signal": 3},
            "sl_tp": {"atr_sl_mult": 1.0},
            "loops": {"normal": 10},
        }

    monkeypatch.setattr("src.config_loader.load_active_config", lambda: active)
    monkeypatch.setattr(
        "src.config_loader.load_strategy_config",
        lambda strategy: {"preset": {"timeframe": "5m", "chart_period": "1D", "plotter_period": "6h"}},
    )
    monkeypatch.setattr("src.config_loader.resolve_symbol_config", fake_resolve_symbol_config)

    config = resolve_strategy_instance_config(
        {"id": "btc_scalp_fast", "symbol": "BTCUSDT", "strategy": "SCALP", "profile": "btc_aggressive"}
    )

    assert config["STRATEGY_INSTANCE_ID"] == "btc_scalp_fast"
    assert config["POSITION_SIZE_PERCENT"] == 30
    assert config["MIN_TRADE_AMOUNT_USDT"] == 25
    assert config["MIN_CONFIDENCE_THRESHOLD"] == 0.61
    assert config["STYLE_PRESETS"]["SCALP"]["leverage"] == 15
    assert config["DEFAULT_CHART_RANGE"] == "15m"
    assert config["SCALP_SETTINGS"]["signal_rules"]["min_score_for_signal"] == 3


def test_position_ownership_blocks_other_strategy(tmp_path):
    store = PositionOwnershipStore(path=str(tmp_path / "owners.json"))

    acquired, owner = store.try_acquire("BTCUSDT", "btc_macdx", "MACDX")
    assert acquired is True
    assert owner.owner_id == "btc_macdx"

    acquired, owner = store.try_acquire("BTC-USDT", "btc_hybrid", "HYBRID")
    assert acquired is False
    assert owner.owner_id == "btc_macdx"

    assert store.release_if_owner("BTCUSDT", "btc_hybrid") is False
    assert store.release_if_owner("BTCUSDT", "btc_macdx") is True

    acquired, owner = store.try_acquire("BTCUSDT", "btc_hybrid", "HYBRID")
    assert acquired is True
    assert owner.owner_id == "btc_hybrid"


def test_position_ownership_sync_removes_stale_owner(tmp_path):
    path = tmp_path / "owners.json"
    store = PositionOwnershipStore(path=str(path))
    store.try_acquire("BTCUSDT", "btc_macdx", "MACDX")
    data = json.loads(path.read_text())
    data["BTCUSDT"]["acquired_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    path.write_text(json.dumps(data))

    store.sync_with_positions({})

    assert store.get_owner("BTCUSDT") is None
    assert json.loads(path.read_text()) == {}


def test_position_ownership_sync_keeps_recent_pending_owner(tmp_path):
    store = PositionOwnershipStore(path=str(tmp_path / "owners.json"))
    store.try_acquire("BTCUSDT", "btc_macdx", "MACDX")

    store.sync_with_positions({})

    assert store.get_owner("BTCUSDT").owner_id == "btc_macdx"
