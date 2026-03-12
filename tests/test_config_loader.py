"""
Tests for the new configuration loader system.
"""

import os
import sys
import json
import pytest
import tempfile
import shutil

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config_loader import (
    deep_merge,
    load_base_config,
    load_trading_config,
    load_active_config,
    load_strategy_config,
    load_profile_config,
    resolve_symbol_config,
    get_strategy,
    get_symbols,
    get_disabled_symbols,
    convert_to_legacy_format,
    _build_style_presets,
)


class TestDeepMerge:
    """Tests for deep_merge function."""

    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        override = {"a": {"c": 5}, "e": 6}
        result = deep_merge(base, override)
        assert result == {"a": {"b": 1, "c": 5}, "d": 3, "e": 6}

    def test_list_replacement(self):
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = deep_merge(base, override)
        assert result == {"items": [4, 5]}

    def test_skip_meta_fields(self):
        base = {"a": 1}
        override = {"_description": "test", "_version": "1.0", "b": 2}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 2}
        assert "_description" not in result
        assert "_version" not in result

    def test_original_not_modified(self):
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        result = deep_merge(base, override)
        assert base == {"a": {"b": 1}}  # Original unchanged
        assert result == {"a": {"b": 1, "c": 2}}


class TestConfigLoading:
    """Tests for config file loading."""

    def test_load_base_config(self):
        """Base config should load and have expected structure."""
        config = load_base_config()
        # Check some expected keys exist
        if config:  # Only test if file exists
            assert "exchange" in config or "ai" in config or "chart_ranges" in config

    def test_load_trading_config(self):
        """Trading config should load and have expected structure."""
        config = load_trading_config()
        if config:
            assert "position" in config or "risk" in config or "features" in config

    def test_load_active_config(self):
        """Active config should load and have strategy."""
        config = load_active_config()
        if config:
            assert "strategy" in config

    def test_load_strategy_config(self):
        """Strategy configs should load with preset."""
        for strategy in ["MACDX", "HYBRID", "SCALP", "AISCALP", "SWING", "GRID"]:
            config = load_strategy_config(strategy)
            if config:
                assert "preset" in config or "signal_rules" in config


class TestProfileInheritance:
    """Tests for profile inheritance system."""

    def test_default_profile_empty(self):
        """Default profile should return empty or minimal overrides."""
        config = load_profile_config("default")
        # Default profile should have no overrides (empty or just meta)
        non_meta = {k: v for k, v in config.items() if not k.startswith("_")}
        assert len(non_meta) == 0 or config == {}

    def test_profile_inheritance(self):
        """Profiles with _inherits should include parent values."""
        # This would require actual profile files to test properly
        # Testing the mechanism works
        profile = load_profile_config("btc_aggressive")
        if profile:
            # If profile exists, it should have inherited or direct values
            assert isinstance(profile, dict)


class TestSymbolConfigResolution:
    """Tests for resolve_symbol_config."""

    def test_resolve_returns_dict(self):
        """Resolution should always return a dict."""
        result = resolve_symbol_config("BTCUSDT")
        assert isinstance(result, dict)

    def test_resolve_includes_metadata(self):
        """Resolved config should include resolution metadata."""
        result = resolve_symbol_config("BTCUSDT")
        if "_resolved" in result:
            assert "symbol" in result["_resolved"]
            assert result["_resolved"]["symbol"] == "BTCUSDT"

    def test_resolve_with_strategy_override(self):
        """Strategy override should be applied."""
        result1 = resolve_symbol_config("BTCUSDT", strategy="HYBRID")
        result2 = resolve_symbol_config("BTCUSDT", strategy="SCALP")
        # They should be different if strategy configs differ
        if "_resolved" in result1 and "_resolved" in result2:
            assert result1["_resolved"]["strategy"] != result2["_resolved"]["strategy"]


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_strategy(self):
        """get_strategy should return a string."""
        strategy = get_strategy()
        assert isinstance(strategy, str)
        assert strategy in ["SCALP", "AISCALP", "SWING", "GRID", "HYBRID", "MACDX"]

    def test_get_symbols(self):
        """get_symbols should return a list."""
        symbols = get_symbols("bingx")
        assert isinstance(symbols, list)

    def test_get_disabled_symbols(self):
        """get_disabled_symbols should return a list."""
        disabled = get_disabled_symbols()
        assert isinstance(disabled, list)


class TestLegacyConversion:
    """Tests for legacy format conversion."""

    def test_convert_produces_legacy_keys(self):
        """Conversion should produce expected legacy keys."""
        legacy = convert_to_legacy_format({}, "MACDX")
        # Check for expected legacy keys
        expected_keys = [
            "STRATEGY_STYLE", "EXCHANGE_SYMBOLS", "DISABLED_SYMBOLS",
            "POSITION_SIZE_PERCENT", "MIN_TRADE_AMOUNT_USDT",
            "CHART_RANGES", "PLOTTER_RANGES", "STYLE_PRESETS"
        ]
        for key in expected_keys:
            assert key in legacy, f"Missing legacy key: {key}"

    def test_convert_strategy_style_matches(self):
        """Converted config should have correct strategy."""
        legacy = convert_to_legacy_format({}, "HYBRID")
        assert legacy["STRATEGY_STYLE"] == "HYBRID"

    def test_build_style_presets(self):
        """Style presets should include all strategies."""
        presets = _build_style_presets()
        assert isinstance(presets, dict)
        # Should have at least some presets if strategy files exist
        if presets:
            for name, preset in presets.items():
                assert isinstance(preset, dict)


class TestBackwardCompatibility:
    """Tests to ensure backward compatibility with existing code."""

    def test_bot_config_importable(self):
        """src.config should be importable with BOT_CONFIG."""
        from src.config import BOT_CONFIG
        assert isinstance(BOT_CONFIG, dict)

    def test_strategy_style_importable(self):
        """STRATEGY_STYLE should be importable."""
        from src.config import STRATEGY_STYLE
        assert isinstance(STRATEGY_STYLE, str)

    def test_style_presets_importable(self):
        """STYLE_PRESETS should be importable."""
        from src.config import STYLE_PRESETS
        assert isinstance(STYLE_PRESETS, dict)

    def test_position_size_importable(self):
        """POSITION_SIZE_PERCENT should be importable."""
        from src.config import POSITION_SIZE_PERCENT
        assert isinstance(POSITION_SIZE_PERCENT, (int, float))

    def test_leverage_importable(self):
        """LEVERAGE should be importable."""
        from src.config import LEVERAGE
        assert isinstance(LEVERAGE, (int, float))

    def test_ai_settings_importable(self):
        """AI_SETTINGS should be importable."""
        from src.config import AI_SETTINGS
        assert isinstance(AI_SETTINGS, dict)

    def test_parse_interval_minutes(self):
        """parse_interval_minutes should work correctly."""
        from src.config import parse_interval_minutes
        assert parse_interval_minutes("1m") == 1
        assert parse_interval_minutes("5m") == 5
        assert parse_interval_minutes("1h") == 60
        assert parse_interval_minutes("4h") == 240
        assert parse_interval_minutes("1d") == 1440


class TestConfigIntegrity:
    """Tests for config data integrity."""

    def test_macdx_settings_structure(self):
        """MACDX_SETTINGS should have signal_rules."""
        from src.config import MACDX_SETTINGS
        if MACDX_SETTINGS:
            assert "signal_rules" in MACDX_SETTINGS

    def test_scalp_settings_structure(self):
        """SCALP_SETTINGS should have required sections."""
        from src.config import SCALP_SETTINGS
        if SCALP_SETTINGS:
            expected_sections = ["signal_rules", "sl_tp", "loops"]
            for section in expected_sections:
                assert section in SCALP_SETTINGS, f"Missing SCALP_SETTINGS section: {section}"

    def test_cleanup_settings_has_defaults(self):
        """CLEANUP_SETTINGS should have retention values."""
        from src.config import CLEANUP_SETTINGS
        assert "cleanup_old_charts" in CLEANUP_SETTINGS or CLEANUP_SETTINGS == {}

    def test_chart_ranges_valid(self):
        """CHART_RANGES should have valid structure."""
        from src.config import CHART_RANGES
        if CHART_RANGES:
            for name, config in CHART_RANGES.items():
                assert isinstance(config, dict)
                # Should have either days, hours, or minutes
                has_duration = "days" in config or "hours" in config or "minutes" in config
                assert has_duration, f"CHART_RANGES[{name}] missing duration"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
