"""
Обратная совместимость — старые импорты продолжают работать.
"""

# Indicators
from src.core.indicators import (
    calculate_ema, calculate_sma_series, calculate_ema_series,
    calculate_macd, calculate_rsi_series,
    calculate_atr, calculate_bollinger_bands,
    calculate_support_resistance, get_price_value,
    calculate_seb, calculate_seb_series,
    calculate_indicators, calculate_indicator_series,
)

# Signals
from src.core.signals import (
    BaseSignalGenerator,
    create_signal_generator,
    HybridSignalGenerator,
    AiscalpSignalGenerator,
    MacdxSignalGenerator,
    ScalpSignalGenerator,
    detect_rsi_divergence,
    map_quality_to_confidence,
    calculate_pnl_pct,
    PositionAdapter,
)

# Execution
from src.core.execution import (
    create_order, get_open_positions,
    calculate_dynamic_sl_tp, calculate_position_size,
    validate_prediction,
    validate_risk_parameters,
)

# Tracking
from src.core.tracking import TradeTracker, DecisionJournal

# Strategies
from src.core.strategies import (
    StrategyPipeline,
    create_pipeline,
    HybridPipeline,
    AiscalpPipeline,
    MacdxPipeline,
    SwingPipeline,
    TrailingStopManager,
    ScalpSession,
    ScalpVetoProcessor,
    GridExecutor,
    GridLevel,
    GridState,
    run_grid_worker,
    calculate_adx,
)

# Data
from src.core.data import (
    ensure_dirs,
    fetch_prices,
    fetch_news,
    fetch_htf_prices,
    process_symbol,
    AtomicJsonStore,
)

# Pipeline
from src.core.pipeline import PipelineOrchestrator

# Legacy — оставить на переходный период
from src.core.regime import MarketRegimeDetector, detect_regime
from src.core.session import get_session_info
from src.core.predict import get_prediction, parse_response
from src.core.plotter import plot_symbol
from src.core.lightweight_analyzer import LightweightAnalyzer

__all__ = [
    # Indicators
    "calculate_ema", "calculate_sma_series", "calculate_ema_series",
    "calculate_macd", "calculate_rsi_series",
    "calculate_atr", "calculate_bollinger_bands",
    "calculate_support_resistance", "get_price_value",
    "calculate_seb", "calculate_seb_series",
    "calculate_indicators", "calculate_indicator_series",
    # Signals
    "BaseSignalGenerator", "create_signal_generator",
    "HybridSignalGenerator", "AiscalpSignalGenerator",
    "MacdxSignalGenerator", "ScalpSignalGenerator",
    "detect_rsi_divergence", "map_quality_to_confidence",
    "calculate_pnl_pct", "PositionAdapter",
    # Execution
    "create_order", "get_open_positions",
    "calculate_dynamic_sl_tp", "calculate_position_size",
    "validate_prediction", "validate_risk_parameters",
    # Tracking
    "TradeTracker", "DecisionJournal",
    # Strategies
    "StrategyPipeline", "create_pipeline",
    "HybridPipeline", "AiscalpPipeline", "MacdxPipeline", "SwingPipeline",
    "TrailingStopManager", "ScalpSession", "ScalpVetoProcessor",
    "GridExecutor", "GridLevel", "GridState", "run_grid_worker", "calculate_adx",
    # Data
    "ensure_dirs", "fetch_prices", "fetch_news", "fetch_htf_prices",
    "process_symbol", "AtomicJsonStore",
    # Pipeline
    "PipelineOrchestrator",
    # Legacy
    "MarketRegimeDetector", "detect_regime",
    "get_session_info", "get_prediction", "parse_response",
    "plot_symbol", "LightweightAnalyzer",
]
