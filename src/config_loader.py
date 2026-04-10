"""
Configuration Loader with Profile Inheritance System

This module provides a layered configuration system with:
- Base infrastructure settings (config/base.json)
- Trading parameters (config/trading.json)
- Strategy-specific settings (config/strategies/*.json)
- Per-symbol profiles (config/profiles/*.json)
- Active runtime configuration (config/active.json)

Configuration is resolved with inheritance:
  base -> trading -> strategy -> profile
"""

import os
import json
from copy import deepcopy
from typing import cast, Dict, Any, Optional, List


# --- Paths ---
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_PROJECT_ROOT, 'config')
_LEGACY_CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'bot_config.json')

# --- Cache State ---
_config_mtimes: Dict[str, float] = {}
_config_cache: Dict[str, Any] = {}
_resolved_configs: Dict[str, Any] = {}
_config_callbacks: List = []


def _get_config_path(name: str) -> str:
    """Get path to a config file."""
    if name == 'base':
        return os.path.join(_CONFIG_DIR, 'base.json')
    elif name == 'trading':
        return os.path.join(_CONFIG_DIR, 'trading.json')
    elif name == 'active':
        return os.path.join(_CONFIG_DIR, 'active.json')
    elif name.startswith('strategy:'):
        strategy_name = name.split(':')[1].lower()
        return os.path.join(_CONFIG_DIR, 'strategies', f'{strategy_name}.json')
    elif name.startswith('profile:'):
        profile_name = name.split(':')[1]
        return os.path.join(_CONFIG_DIR, 'profiles', f'{profile_name}.json')
    else:
        return os.path.join(_CONFIG_DIR, f'{name}.json')


def _load_json_file(path: str) -> Dict[str, Any]:
    """Load a JSON file, returns empty dict if not found."""
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Error loading config {path}: {e}")
    return {}


def _get_mtime(path: str) -> float:
    """Get modification time of a file."""
    try:
        return os.path.getmtime(path) if os.path.exists(path) else 0.0
    except OSError:
        return 0.0


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries. Override takes precedence.
    Lists are replaced entirely, not merged.
    """
    result = deepcopy(base)
    for key, value in override.items():
        if key.startswith('_'):
            # Skip meta fields like _description, _version, _inherits
            continue
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _use_new_config_system() -> bool:
    """Check if new config system is available (config/ directory exists)."""
    return os.path.isdir(_CONFIG_DIR) and os.path.exists(os.path.join(_CONFIG_DIR, 'active.json'))


def load_base_config() -> Dict[str, Any]:
    """Load base infrastructure configuration."""
    path = _get_config_path('base')
    return _load_json_file(path)


def load_trading_config() -> Dict[str, Any]:
    """Load trading parameters configuration."""
    path = _get_config_path('trading')
    return _load_json_file(path)


def load_active_config() -> Dict[str, Any]:
    """Load active runtime configuration."""
    path = _get_config_path('active')
    return _load_json_file(path)


def load_backtest_config() -> Dict[str, Any]:
    """Load backtest engine configuration."""
    path = _get_config_path('backtest')
    return _load_json_file(path)


def load_strategy_config(strategy: str) -> Dict[str, Any]:
    """Load strategy-specific configuration."""
    path = _get_config_path(f'strategy:{strategy}')
    return _load_json_file(path)


def load_profile_config(profile: str) -> Dict[str, Any]:
    """Load profile configuration with inheritance resolution."""
    if profile == 'default' or not profile:
        return {}

    path = _get_config_path(f'profile:{profile}')
    config = _load_json_file(path)

    # Handle profile inheritance
    inherits = config.get('_inherits')
    if inherits and inherits != 'default':
        parent = load_profile_config(inherits)
        config = deep_merge(parent, config)

    return config


def validate_profile_strategy_match(profile_name: str, strategy_name: str, profile_config: Optional[Dict[str, Any]] = None) -> bool:
    """
    Validate that profile is compatible with strategy.

    Args:
        profile_name: Name of the profile
        strategy_name: Name of the strategy
        profile_config: Optional pre-loaded profile config (avoids redundant file read)

    Returns:
        True if compatible, raises ValueError if not compatible

    Raises:
        ValueError: If profile belongs to different strategy
    """
    # Use provided profile config or load from file
    if profile_config is None:
        profile_path = _get_config_path(f'profile:{profile_name}')
        profile = _load_json_file(profile_path)
    else:
        profile = profile_config

    # Get the strategy this profile belongs to
    profile_strategy = profile.get('_strategy')

    # If profile has _strategy defined, it must match
    if profile_strategy:
        profile_strategy_lower = profile_strategy.lower()
        strategy_lower = strategy_name.lower()

        if profile_strategy_lower != strategy_lower:
            raise ValueError(
                f"❌ Profile '{profile_name}' belongs to strategy '{profile_strategy}', "
                f"but symbol is using strategy '{strategy_name}'. "
                f"Use a profile compatible with {strategy_name} or change strategy."
            )

    return True


def resolve_symbol_config(symbol: str, strategy: Optional[str] = None) -> Dict[str, Any]:
    """
    Resolve complete configuration for a symbol.

    Resolution order (later overrides earlier):
    1. Base config (infrastructure)
    2. Trading config (parameters)
    3. Strategy config (strategy-specific)
    4. Profile config (symbol-specific overrides)

    Args:
        symbol: Trading symbol (e.g., "BTCUSDT")
        strategy: Optional strategy override. If None, uses active.json strategy.

    Returns:
        Fully resolved configuration dictionary
    """
    # Check cache
    cache_key = f"{symbol}:{strategy or 'active'}"
    if cache_key in _resolved_configs:
        return _resolved_configs[cache_key]

    # Load configurations
    base = load_base_config()
    trading = load_trading_config()
    active = load_active_config()

    # Get strategy (from arg or active config)
    strategy = strategy or active.get('strategy', 'MACDX')
    if not strategy:
        strategy = 'MACDX'  # fallback
    strategy_config = load_strategy_config(cast(str, strategy))

    # Get profile for this symbol
    symbol_profiles = active.get('symbol_profiles', {})
    profile_name = symbol_profiles.get(symbol, 'default')

    # Load profile config (handles inheritance)
    profile_config = load_profile_config(profile_name)

    # Validate profile matches strategy AFTER loading (pass resolved config)
    if profile_name and profile_name != 'default':
        validate_profile_strategy_match(profile_name, strategy, profile_config)

    # Merge in order: base -> trading -> strategy -> profile
    config = deep_merge(base, trading)
    config = deep_merge(config, strategy_config)
    config = deep_merge(config, profile_config)

    # Add resolved metadata
    config['_resolved'] = {
        'symbol': symbol,
        'strategy': strategy,
        'profile': profile_name
    }

    # Cache result
    _resolved_configs[cache_key] = config
    return config


def get_strategy() -> str:
    """Get the active strategy name."""
    active = load_active_config()
    return active.get('strategy', 'MACDX')


def get_symbols(exchange: str = 'bingx') -> List[str]:
    """Get list of active symbols for an exchange."""
    active = load_active_config()
    symbols_config = active.get('symbols', {})
    return symbols_config.get(exchange, ['BTCUSDT'])


def get_disabled_symbols() -> List[str]:
    """Get list of disabled symbols."""
    active = load_active_config()
    return active.get('disabled_symbols', [])


def clear_config_cache():
    """Clear all configuration caches."""
    global _resolved_configs
    _resolved_configs = {}
    load_base_config.cache_clear() if hasattr(load_base_config, 'cache_clear') else None  # type: ignore


def should_reload_config() -> bool:
    """Check if any config file has been modified since last load."""
    files_to_check = [
        _get_config_path('active'),
        _get_config_path('trading'),
    ]

    for path in files_to_check:
        current_mtime = _get_mtime(path)
        cached_mtime = _config_mtimes.get(path, 0.0)
        if current_mtime > cached_mtime:
            return True

    return False


def reload_config():
    """Reload configuration from files."""
    global _config_mtimes

    # Clear caches
    clear_config_cache()

    # Update mtimes
    for name in ['active', 'trading', 'base']:
        path = _get_config_path(name)
        _config_mtimes[path] = _get_mtime(path)

    # Notify callbacks
    for callback in _config_callbacks:
        try:
            callback()
        except Exception as e:
            print(f"⚠️ Config reload callback error: {e}")


def register_config_callback(callback):
    """Register a function to be called when config is reloaded."""
    _config_callbacks.append(callback)


# --- Legacy Compatibility Layer ---

def load_legacy_config() -> Dict[str, Any]:
    """Load legacy bot_config.json for backward compatibility."""
    return _load_json_file(_LEGACY_CONFIG_PATH)


def convert_to_legacy_format(config: Dict[str, Any], strategy: str) -> Dict[str, Any]:
    """
    Convert new config format to legacy format for backward compatibility.
    This allows existing code to work without modification.
    """
    active = load_active_config()
    base = load_base_config()
    trading = load_trading_config()
    strategy_config = load_strategy_config(strategy)

    # Build legacy format
    legacy = {
        # From active
        'STRATEGY_STYLE': strategy,
        'EXCHANGE_SYMBOLS': active.get('symbols', {}),
        'DISABLED_SYMBOLS': active.get('disabled_symbols', []),

        # From base.exchange
        'EXCHANGE_FEES': base.get('exchange', {}).get('fees', {}),

        # From trading.position
        'POSITION_SIZE_PERCENT': trading.get('position', {}).get('size_percent', 10),
        'MIN_TRADE_AMOUNT_USDT': trading.get('position', {}).get('min_trade_amount_usdt', 10),

        # From trading.risk
        'MIN_CONFIDENCE_THRESHOLD': trading.get('risk', {}).get('min_confidence_threshold', 0.55),
        'MIN_RISK_REWARD_RATIO': trading.get('risk', {}).get('min_risk_reward_ratio', 1.2),
        'MIN_PARTIAL_CLOSE_PNL': trading.get('risk', {}).get('min_partial_close_pnl', 0.5),
        'TAKE_PROFIT_PERCENT': trading.get('risk', {}).get('take_profit_percent', 2.5),
        'STOP_LOSS_PERCENT': trading.get('risk', {}).get('stop_loss_percent', 1),

        # From trading.features
        'ENABLE_NEWS': trading.get('features', {}).get('enable_news', False),
        'ENABLE_ADVANCED_ANALYSIS': trading.get('features', {}).get('enable_advanced_analysis', True),
        'ENABLE_PARALLEL_MODE': trading.get('features', {}).get('enable_parallel_mode', True),
        'ENABLE_AI_SKIP_ON_RSI': trading.get('features', {}).get('enable_ai_skip_on_rsi', False),
        'ENABLE_LOW_VOLUME_FILTER': trading.get('features', {}).get('enable_low_volume_filter', False),
        'AGGRESSIVE_MODE': trading.get('features', {}).get('aggressive_mode', False),

        # From trading.aggressive_settings
        'AGGRESSIVE_SETTINGS': {
            k.upper(): v for k, v in trading.get('aggressive_settings', {}).items()
        },

        # From trading.ai_thresholds
        'AI_THRESHOLDS': {
            k.upper(): v for k, v in trading.get('ai_thresholds', {}).items()
        },

        # From base
        'CHART_RANGES': base.get('chart_ranges', {}),
        'PLOTTER_RANGES': base.get('plotter_ranges', {}),
        'TECHNICAL_ANALYSIS': base.get('technical_analysis', {}),
        'CHART_SETTINGS': base.get('chart_settings', {}),
        'CLEANUP_SETTINGS': base.get('cleanup_settings', {}),
        'POSITION_LIMITS': base.get('position_limits', {}),
        'ERROR_HANDLING': base.get('error_handling', {}),
        'NEWS_SETTINGS': base.get('news', {}),
        'DECISION_JOURNAL': base.get('decision_journal', {}),
        'VALIDATION': base.get('validation', {}),
        'AI_SETTINGS': base.get('ai', {}),

        # From trading
        'SMART_SAMPLING': trading.get('smart_sampling', {}),
        'REGIME_SETTINGS': {
            'enabled': trading.get('regime', {}).get('enabled', True),
            'lookback_candles': trading.get('regime', {}).get('lookback_candles', 10),
            'ema_spread_thresholds': trading.get('regime', {}).get('ema_spread_thresholds', {}),
            'volatility_percentile_window': trading.get('regime', {}).get('volatility_percentile_window', 100),
            'regime_params': trading.get('regime', {}).get('params', {})
        },
        'DYNAMIC_SIZING': trading.get('dynamic_sizing', {}),
        'PERFORMANCE_TRACKING': trading.get('performance_tracking', {}),
        'MOMENTUM_STRATEGY': {
            **trading.get('momentum', {}),
            # Add trend_consensus_required for backward compat
            'trend_consensus_required': trading.get('momentum', {}).get('trend_consensus_required', False)
        },

        # Build STYLE_PRESETS from all strategies
        'STYLE_PRESETS': _build_style_presets(),
    }

    # Add strategy-specific settings
    strategy_upper = strategy.upper()
    if strategy_upper == 'SCALP':
        legacy['SCALP_SETTINGS'] = _convert_scalp_config(strategy_config)
    elif strategy_upper == 'MACDX':
        legacy['MACDX_SETTINGS'] = {
            'enabled': True,
            'signal_rules': strategy_config.get('signal_rules', {}),
            'preset': strategy_config.get('preset', {})
        }
    elif strategy_upper == 'HYBRID':
        legacy['HYBRID_SETTINGS'] = {
            'enabled': True,
            'signal_rules': strategy_config.get('signal_rules', {}),
            'ai_filter': strategy_config.get('ai_filter', {}),
            'interaction_rules': strategy_config.get('interaction_rules', {})
        }
    elif strategy_upper == 'AISCALP':
        legacy['AISCALP_SETTINGS'] = _convert_aiscalp_config(strategy_config)
    elif strategy_upper == 'GRID':
        legacy['GRID_SETTINGS'] = {
            'enabled': True,
            **strategy_config.get('grid_settings', {})
        }

    return legacy


def _build_style_presets() -> Dict[str, Any]:
    """Build STYLE_PRESETS from all strategy configs."""
    presets = {}
    for strategy in ['SCALP', 'AISCALP', 'SWING', 'GRID', 'HYBRID', 'MACDX']:
        config = load_strategy_config(strategy)
        if config:
            preset = config.get('preset', {})
            preset['description'] = config.get('_description', '')
            presets[strategy] = preset
    return presets


def _convert_scalp_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert new SCALP config to legacy format."""
    return {
        'enabled': True,
        'signal_rules': config.get('signal_rules', {}),
        'sl_tp': config.get('sl_tp', {}),
        'breakeven': config.get('breakeven', {}),
        'time_exit': config.get('time_exit', {}),
        'risk_limits': config.get('risk_limits', {}),
        'loops': config.get('loops', {}),
        'regime_overrides': config.get('regime_overrides', {}),
        'interaction_rules': config.get('interaction_rules', {}),
        'ai_integration': config.get('ai_integration', {})
    }


def _convert_aiscalp_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert new AISCALP config to legacy format."""
    signal_rules = config.get('signal_rules', {})
    return {
        'signal_scoring': {
            'weights': signal_rules.get('weights', {}),
            'min_score_for_signal': signal_rules.get('min_score_for_signal', 5),
            'tier1_required': signal_rules.get('tier1_required', True),
            'conflict_friction_threshold': signal_rules.get('conflict_friction_threshold', 4),
            'min_volume_ratio': signal_rules.get('min_volume_ratio', 0.3),
            'min_atr_ratio': signal_rules.get('min_atr_ratio', 0.3),
            'rsi_long_zone': signal_rules.get('rsi_long_zone', [25, 55]),
            'rsi_short_zone': signal_rules.get('rsi_short_zone', [45, 75]),
            'sr_proximity_pct': signal_rules.get('sr_proximity_pct', 2.5),
            'macd_exit_pnl_threshold': signal_rules.get('macd_exit_pnl_threshold', -1.5)
        },
        'sessions': config.get('sessions', {}),
        'multi_timeframe': config.get('multi_timeframe', {}),
        'pre_filter': config.get('pre_filter', {}),
        'ai_filter': config.get('ai_filter', {}),
        'interaction_rules': config.get('interaction_rules', {})
    }


def get_legacy_compatible_config() -> Dict[str, Any]:
    """
    Get configuration in legacy format.

    If new config system is available, converts to legacy format.
    Otherwise, loads bot_config.json directly.
    """
    if _use_new_config_system():
        strategy = get_strategy()
        return convert_to_legacy_format({}, strategy)
    else:
        return load_legacy_config()
