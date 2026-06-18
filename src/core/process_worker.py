"""
Single-symbol worker process.
Runs the complete trading pipeline for ONE symbol in isolation.
"""

import time
import os

from src.utils.logger import info, warning, error
from src.config import BOT_CONFIG

# Implements the pipeline for a single process

def setup_trading_context(symbol: str) -> dict:
    """Initialize trading context variables."""
    return {
        'last_config_check': time.time(),
        'config_check_interval': 30,
        'cycle_count': 0,
        'last_funding_check': 0,
        'preset': None
    }

def check_config_reload(context: dict, symbol: str) -> bool:
    """Check if config needs reload and perform it."""
    from src.config import should_reload_config, reload_bot_config
    from src.utils.logger import info, error

    current_time = time.time()
    if current_time - context['last_config_check'] >= context['config_check_interval']:
        if should_reload_config():
            info(f"🔄 [{symbol}] Config file changed, reloading...")
            try:
                reload_bot_config()
                context['preset'] = None  # Force reload
                info(f"✅ [{symbol}] Config reloaded successfully")
                return True
            except Exception as e:
                error(f"❌ [{symbol}] Config reload failed: {e}")
        context['last_config_check'] = current_time
    return False

def load_preset_if_needed(context: dict, symbol: str) -> dict:
    """Load preset if not loaded or after config reload."""
    from src.config import get_strategy_preset
    from src.utils.logger import info

    if context['preset'] is None:
        context['preset'] = get_strategy_preset()
        info(f"📋 [{symbol}] Preset loaded: {context['preset']}")
    return context['preset']

def collect_market_data(symbol: str) -> tuple:
    """Collect prices and news data."""
    from src.core import collector
    from src.utils.helpers import get_filename
    import json
    from src.config import DATA_DIR

    prices = collector.fetch_prices(symbol)
    news = collector.fetch_news(symbol)

    # Save prices to file for analyzer
    symbol_file = get_filename(symbol)
    prices_file = f"{DATA_DIR}/prices/{symbol_file}.json"
    with open(prices_file, "w") as f:
        json.dump(prices, f)

    return prices, news

def analyze_market_data(symbol: str, prices: dict, positions: dict) -> dict:
    """Analyze market data and detect regime."""
    from src.core import analyzer, regime
    from src.utils.logger import StageTimer

    with StageTimer(f"[{symbol}] Analysis"):
        analysis = analyzer.analyze_symbol(symbol)
    return analysis

def run_trading_loop(symbol: str, tracker, journal):
    """
    Основной торговый цикл для символа.

    Args:
        symbol: Trading pair symbol (e.g., "BTC-USDT")
        tracker: TradeTracker instance for position tracking
        journal: DecisionJournal instance for logging decisions
    """
    from datetime import datetime
    from typing import Dict, Any, Optional
    from src.core import collector, analyzer, predict, executor, monitor
    from src.config import ERROR_HANDLING, STRATEGY_STYLE
    from src.config import should_reload_config, reload_bot_config
    from src.utils.logger import info, error, warning, StageTimer
    from src.core.trade_tracker import TradeTracker
    from src.core.decision_journal import DecisionJournal
    from src.config import DATA_DIR

    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)

    # Type hints
    tracker: TradeTracker
    journal: DecisionJournal

    # Initialize context
    context: Dict[str, Any] = {
        'last_config_check': time.time(),
        'config_check_interval': 30,
        'cycle_count': 0,
        'preset': None
    }

    # Startup variables
    time.time()
    preset = None

    while True:
        try:
            time.time()

            # Check config reload
            check_config_reload(context, symbol)

            # Load preset if needed
            preset = load_preset_if_needed(context, symbol)

            # Collect market data
            prices, news = collect_market_data(symbol)

            # Get positions
            symbol.replace("-", "")
            positions = executor.get_open_positions()

            # Analyze data
            analysis = analyze_market_data(symbol, prices, positions)

            # Basic decision logic - simplified version
            current_price = analysis.get('current_price', 0)
            rsi = analysis.get('rsi', 50)
            has_position = positions and any(pos for pos in positions.values())

            decision = {'action': 'HOLD', 'reason': 'Basic analysis'}

            # Simple RSI-based logic for demo
            if not has_position and rsi < 30:
                decision = {
                    'action': 'BUY',
                    'reason': f'RSI oversold ({rsi:.1f})',
                    'sl': current_price * 0.98,  # 2% stop loss
                    'tp': current_price * 1.04   # 4% take profit
                }
            elif has_position and rsi > 70:
                decision = {'action': 'CLOSE', 'reason': f'RSI overbought ({rsi:.1f})'}

            # Log decision
            journal.log_decision(symbol, 'BASIC', None, decision)

            # Execute if needed
            if decision['action'] != 'HOLD':
                decision['symbol'] = symbol
                executor.execute_prediction(decision)

            # Monitor positions
            monitor.monitor_symbol(symbol)

            # Simple sleep
            sleep_time = preset.get('loop_interval', 300)
            context['cycle_count'] += 1

            time.sleep(sleep_time)

        except KeyboardInterrupt:
            info(f"🛑 [{symbol}] Process terminated.")
        except Exception as e:
            error(f"❌ [{symbol}] Cycle error: {e}")
            if ERROR_HANDLING.get('crash_on_error', False):
                raise
            time.sleep(5)  # Brief pause before retry


def run_symbol_pipeline(symbol: str, ws_cache=None, ws_ready=None):
    """
    Запускает бесконечный торговый цикл для ОДНОГО символа.
    Этот код выполняется в отдельном процессе.

    Args:
        symbol: Trading pair symbol (e.g., "BTCUSDT")
        ws_cache: Shared WebSocket cache dict (multiprocessing.Manager proxy)
        ws_ready: Shared ready flags dict (multiprocessing.Manager proxy)
    """
    from src.core.pipeline import PipelineOrchestrator
    orchestrator = PipelineOrchestrator()
    orchestrator.run_symbol_pipeline(symbol, ws_cache, ws_ready)


def run_strategy_instance_pipeline(instance: dict, config: dict, ws_cache=None, ws_ready=None):
    """
    Запускает worker для одного StrategyInstance.

    Новый runtime передает явный конфиг инстанса. Старые модули внутри
    процесса получают его через изолированный legacy adapter, поэтому
    каждый worker работает со своим STRATEGY_STYLE/SYMBOLS.
    """
    import src.config as config_module
    from src.runtime import StrategyInstance, apply_legacy_runtime_config
    from src.exchanges.exchange_factory import reset_client
    from src.core.pipeline import PipelineOrchestrator

    strategy_instance = StrategyInstance.from_dict(instance)
    apply_legacy_runtime_config(config_module, config)
    reset_client()

    if strategy_instance.strategy == "GRID":
        from src.core.strategies.grid.worker import run_grid_worker
        run_grid_worker(strategy_instance.symbol, config.get("GRID_SETTINGS", {}), runtime_config=config)
        return

    orchestrator = PipelineOrchestrator(config=config)
    orchestrator.run_symbol_pipeline(strategy_instance.symbol, ws_cache, ws_ready)


def setup_worker(symbol: str, ws_cache=None, ws_ready=None):
    """
    Настраивает worker процесс для символа.
    """
    # 0. Setup shared WebSocket cache (if available)
    print(f"[PROCESS] ws_cache type: {type(ws_cache)}, ws_ready type: {type(ws_ready)}")
    if ws_cache is not None and ws_ready is not None:
        try:
            from src.exchanges.ws_data_provider import set_shared_cache
            set_shared_cache(ws_cache, ws_ready)
        except Exception as e:
            print(f"⚠️ Failed to set shared cache: {e}")

    # 1. Настройка логгера (один раз на старте процесса)
    from src.utils.logger import setup_symbol_logger, info
    setup_symbol_logger(symbol)

    info(f"🚀 [PROCESS START] Запущен процесс для {symbol} (PID: {os.getpid()})")

    # Check if SCALP mode — use dedicated engine
    from src.config import STRATEGY_STYLE, BOT_CONFIG
    if STRATEGY_STYLE == "SCALP":
        info(f"⚡ [{symbol}] SCALP mode — launching ScalpEngine")
        from src.core.scalp_engine import ScalpEngine
        engine = ScalpEngine(symbol, ws_cache=ws_cache, ws_ready=ws_ready)
        engine.run()  # Blocks forever
        return True

    # Check if MACDX mode — use MacdxPipeline with TradeCommand
    if STRATEGY_STYLE == "MACDX":
        info(f"📊 [{symbol}] MACDX mode — launching MacdxPipeline (TradeCommand)")
        from src.core.trade_tracker import TradeTracker
        from src.core.decision_journal import DecisionJournal
        tracker = TradeTracker()
        journal = DecisionJournal()
        run_macdx_loop(symbol, tracker, journal, ws_cache, ws_ready)
        return True

    return False


def get_executor():
    """Get executor instance."""
    from src.core.executor import Executor
    return Executor()

def run_macdx_loop(symbol: str, tracker, journal, ws_cache=None, ws_ready=None):
    """Запускает цикл MACDX пайплайна с исполнением через TradeCommand.

    Двухуровневая архитектура:
    - Основной цикл (loop_interval): анализ закрытых свечей, MACD, вход/выход
    - Быстрый цикл (position_check_interval): мониторинг при открытой позиции
      через WebSocket кэш (без REST запросов)
    """
    from src.core.strategies.macdx import MacdxPipeline
    from src.core.commands import CommandExecutor
    from src.core.signals.macdx import position_guard_check

    pipeline = MacdxPipeline(BOT_CONFIG)
    cmd_executor = CommandExecutor()

    # Загружаем конфиг MACDX для быстрого цикла
    macdx_settings = BOT_CONFIG.get("MACDX_SETTINGS", {})
    preset = macdx_settings.get("preset", {})
    exit_rules = macdx_settings.get("exit_rules", {})
    loop_interval = preset.get("loop_interval", 60)
    check_interval = preset.get("position_check_interval", 15)

    # exit_context хранится per-symbol в памяти процесса
    exit_context = {}

    while True:
        try:
            info(f"[{symbol}] Starting MACDX cycle")
            command = pipeline.generate_command(
                symbol, ws_cache, ws_ready, exit_context=exit_context
            )
            if command:
                info(f"[{symbol}] TradeCommand: {command.action.value} (conf={command.confidence:.2f})")
                result = cmd_executor.execute(command)
                if result.success:
                    info(f"[{symbol}] Command result: {result.message}")
                    # Очищаем exit_context при закрытии позиции
                    if command.action.value == "CLOSE":
                        exit_context.clear()
                else:
                    warning(f"[{symbol}] Command failed: {result.message}")
            else:
                info(f"[{symbol}] No command generated")

            # Быстрый мониторинг при открытой позиции через WebSocket кэш
            position = tracker.get_active_trade(symbol)
            if position and ws_cache and exit_rules.get("pump_guard", {}).get("enabled", True):
                remaining_time = loop_interval
                while remaining_time > 0:
                    sleep_time = min(check_interval, remaining_time)
                    time.sleep(sleep_time)
                    remaining_time -= check_interval

                    # Перепроверяем наличие позиции
                    position = tracker.get_active_trade(symbol)
                    if not position:
                        break

                    guard_result = position_guard_check(
                        symbol, position, exit_context,
                        ws_cache, exit_rules, preset
                    )
                    if guard_result.get("should_close"):
                        info(f"🚨 [{symbol}] GUARD CLOSE: {guard_result['reason']} (urgency: {guard_result.get('urgency', 'high')})")
                        from src.core.commands.models import TradeCommand
                        close_cmd = TradeCommand.close(
                            symbol=symbol,
                            current_price=0,  # будет обновлена executor'ом
                            reason=f"[MACDX-GUARD] {guard_result['reason']}",
                            confidence=0.95 if guard_result.get("urgency") == "critical" else 0.85,
                            strategy="MACDX",
                        )
                        close_result = cmd_executor.execute(close_cmd)
                        if close_result.success:
                            info(f"[{symbol}] Guard close result: {close_result.message}")
                            exit_context.clear()
                        else:
                            warning(f"[{symbol}] Guard close failed: {close_result.message}")
                        break
            else:
                time.sleep(loop_interval)
        except KeyboardInterrupt:
            break
        except Exception as e:
            error(f"[{symbol}] MACDX loop error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)
