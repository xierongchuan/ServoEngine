"""PipelineOrchestrator — главный оркестратор торгового цикла.

Заменяет process_worker.py (749 строк if/elif/elif).
"""

import time
import os
import traceback
from typing import Any, Dict, Optional

from src.config import STRATEGY_STYLE, BOT_CONFIG, ERROR_HANDLING, STYLE_PRESETS
from src.config import should_reload_config, reload_bot_config
from src.core.strategies.factory import create_pipeline
from src.core.strategies.base import StrategyPipeline
from src.core.data.collector import process_symbol
from src.core.tracking.journal import DecisionJournal
from src.core.tracking.trade import TradeTracker
from src.core.performance import get_performance_tracker
from src.exchanges.exchange_factory import get_exchange_client
from src.core import executor, monitor
from src.utils.logger import info, error, warning, StageTimer


class PipelineOrchestrator:
    """Главный оркестратор торгового цикла."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or BOT_CONFIG
        self.strategy = self.config.get("STRATEGY_STYLE", STRATEGY_STYLE)
        self.pipeline = create_pipeline(self.strategy, self.config)
        self._tracker = TradeTracker()
        self._journal = DecisionJournal()
        self._client = get_exchange_client()
        self._cycle_count = 0

    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        """Один полный цикл для символа."""
        return self.pipeline.run_cycle(symbol, ws_cache, ws_ready)

    def reload(self):
        """Перезагрузка конфигурации и пересоздание пайплайна."""
        if should_reload_config():
            info("🔄 Config file changed, reloading...")
            reload_bot_config()
            from src.config import STRATEGY_STYLE as _new_style, BOT_CONFIG as _new_config
            self.config = _new_config
            self.strategy = self.config.get("STRATEGY_STYLE", _new_style)
            self.pipeline = create_pipeline(self.strategy, self.config)
            info(f"✅ Config reloaded (strategy={self.strategy})")

    def run_symbol_pipeline(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None):
        """
        Запускает бесконечный торговый цикл для ОДНОГО символа.
        Заменяет process_worker.py (749 строк if/elif/elif).
        """
        try:
            # Setup shared WebSocket cache
            if ws_cache is not None and ws_ready is not None:
                try:
                    from src.exchanges.ws_data_provider import set_shared_cache
                    set_shared_cache(ws_cache, ws_ready)
                except Exception as e:
                    warning(f"⚠️ Failed to set shared cache: {e}")

            # Setup logger
            from src.utils.logger import setup_symbol_logger
            setup_symbol_logger(symbol)

            info(f"🚀 [PROCESS START] Запущен бесконечный процесс для {symbol} (PID: {os.getpid()})")

            # SCALP mode — use dedicated engine
            if self.strategy == "SCALP":
                info(f"⚡ [{symbol}] SCALP mode — launching ScalpEngine")
                from src.core.scalp_engine import ScalpEngine
                engine = ScalpEngine(symbol, ws_cache=ws_cache, ws_ready=ws_ready)
                engine.run()
                return

            # === STARTUP SYNC ===
            try:
                real_positions_dict = self._client.get_positions()
                stale_count = self._tracker.force_sync_all(real_positions_dict)
                if stale_count > 0:
                    info(f"🧹 [{symbol}] Startup sync: cleaned {stale_count} stale trades")
            except Exception as e:
                warning(f"⚠️ [{symbol}] Startup sync failed: {e}")

            # === STARTUP: Clean old price data ===
            try:
                from src.utils.helpers import get_filename
                from src.config import DATA_DIR
                symbol_file = get_filename(symbol)
                for suffix in ["", "_htf"]:
                    old_file = f"{DATA_DIR}/prices/{symbol_file}{suffix}.json"
                    if os.path.exists(old_file):
                        os.remove(old_file)
                        info(f"🧹 [{symbol}] Removed old price file: {symbol_file}{suffix}.json")
            except Exception as e:
                warning(f"⚠️ [{symbol}] Price cleanup failed: {e}")

            # === STARTUP: Fetch commission rates ===
            try:
                commission = self._client.get_commission_rate(symbol)
                if commission:
                    from src.config import update_fee_rates
                    update_fee_rates(commission.maker, commission.taker)
                    info(f"💰 [{symbol}] Commission rates: maker={commission.maker}%, taker={commission.taker}%")
            except Exception as e:
                warning(f"⚠️ [{symbol}] Commission rate fetch failed: {e}")

            cycle_count = 0
            config_check_interval = 30
            last_config_check = time.time()
            preset = STYLE_PRESETS.get(self.strategy, {})

            while True:
                try:
                    start_time = time.time()

                    # 0. Periodic config hot-reload check
                    current_time = time.time()
                    if current_time - last_config_check >= config_check_interval:
                        self.reload()
                        last_config_check = current_time

                    info(f"▶️ [{symbol}] Начало торгового цикла")

                    # 1. Collect data
                    with StageTimer("Сбор данных", symbol, "📊"):
                        process_symbol(symbol)

                    # 2. Run pipeline
                    prediction = self.run_cycle(symbol, ws_cache, ws_ready)

                    if prediction is None:
                        elapsed = time.time() - start_time
                        info(f"✅ [{symbol}] Цикл завершён ({elapsed:.2f}s). 💤 Нет prediction")
                        self._sleep_cycle(symbol, preset, None, cycle_count)
                        cycle_count += 1
                        self._cycle_count += 1
                        continue

                    # 3. Journal
                    action = prediction.get("action", "hold")
                    current_price = prediction.get("current_price", 0)
                    self._journal.record(symbol, prediction, current_price)

                    # Trade plan
                    all_positions = self._client.get_positions()
                    normalized_symbol = symbol.replace("-", "")
                    symbol_positions = all_positions.get(normalized_symbol, [])
                    real_position = symbol_positions[0] if symbol_positions else None

                    if action in ("buy", "sell") and not real_position:
                        self._journal.set_trade_plan(symbol, prediction, current_price)
                    elif not real_position and self._journal.data.get(symbol, {}).get("trade_plan"):
                        self._journal.record_close(symbol)
                        self._journal.clear_trade_plan(symbol)

                    self._journal.trim_entries(symbol, self.strategy)

                    # 4. Execute
                    with StageTimer("Исполнение сигналов", symbol, "💰"):
                        executor.execute_prediction(prediction, all_positions=all_positions)

                    # 5. Save entry context
                    if action in ("buy", "sell") and not real_position and self.strategy in ("HYBRID", "AISCALP", "MACDX"):
                        try:
                            signal_data = prediction.get("details", {})
                            entry_ctx = {
                                "entry_regime": prediction.get("regime", "UNKNOWN"),
                                "entry_score": signal_data.get("score", 0),
                                "entry_quality": signal_data.get("quality", 0.0),
                                "entry_rsi": prediction.get("rsi", 0),
                                "entry_atr": prediction.get("atr", 0),
                                "entry_volume_ratio": prediction.get("volume_ratio", 0),
                            }
                            self._tracker.set_entry_context(symbol, entry_ctx)
                        except Exception as e:
                            warning(f"⚠️ [{symbol}] Failed to save entry context: {e}")

                    # 6. Monitor
                    with StageTimer("Мониторинг позиции", symbol, "👀"):
                        monitor.monitor_symbol(symbol, all_positions=all_positions)

                    # Sleep
                    elapsed = time.time() - start_time
                    self._sleep_cycle(symbol, preset, real_position, cycle_count)

                    cycle_count += 1
                    self._cycle_count += 1

                except KeyboardInterrupt:
                    info(f"🛑 [{symbol}] Остановка по запросу (KeyboardInterrupt)")
                    return
                except Exception as e:
                    error(f"❌ [{symbol}] Ошибка внутри торгового цикла: {str(e)}")
                    error(traceback.format_exc())
                    sleep_time = ERROR_HANDLING.get("cycle_error_fallback_sleep", 5)
                    time.sleep(sleep_time)

                # Flush PnL updates
                self._tracker.flush()

        except KeyboardInterrupt:
            print(f"🛑 [{symbol}] Process terminated.")
        except Exception as e:
            print(f"CRITICAL WORKER INIT ERROR {symbol}: {e}")
            traceback.print_exc()

    def _sleep_cycle(self, symbol: str, preset: Dict, real_position: Any, cycle_count: int):
        """Dynamic sleep with jitter, calibration, and funding rate checks."""
        import random

        if real_position:
            pos_interval = preset.get("position_check_interval", 5)
            sleep_time = pos_interval
            info(f"✅ [{symbol}] Цикл завершён. 👀 Позиция активна -> Sleep {sleep_time}s")
        else:
            sleep_time = preset.get("loop_interval", 60)
            info(f"✅ [{symbol}] Цикл завершён. 💤 Поиск ({self.strategy}) -> Sleep {sleep_time}s")

        # Jitter ±20%
        jitter = random.uniform(-0.2, 0.2) * sleep_time
        sleep_time = max(5, sleep_time + jitter)

        # Periodic calibration check
        if cycle_count % 50 == 0:
            try:
                perf_tracker = get_performance_tracker()
                suggestions = perf_tracker.should_adjust_thresholds()
                if suggestions:
                    perf_tracker.save_calibration_suggestions(suggestions)
                    info(f"📊 [{symbol}] Calibration check: {len(suggestions)} suggestions (cycle {cycle_count})")
            except Exception as e:
                warning(f"⚠️ [{symbol}] Calibration check failed: {e}")

        # Periodic funding rate check
        if cycle_count % 10 == 0:
            try:
                funding = self._client.get_funding_rate(symbol)
                if funding:
                    if hasattr(funding, 'funding_rate_pct'):
                        info(f"💸 [{symbol}] Funding rate: {funding.funding_rate_pct:+.4f}% | Next: {funding.next_funding_time}")
                    else:
                        info(f"💸 [{symbol}] Funding rate: {funding['funding_rate_pct']:+.4f}% | Next: {funding['next_funding_time']}")
            except Exception:
                pass

        time.sleep(sleep_time)
