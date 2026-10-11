"""Оркестратор: изолированный анализ и journal; исполнение только вне read_only."""

import os
import random
import time
import traceback
from typing import Any, Dict, Optional

from src.config import STRATEGY_STYLE, BOT_CONFIG, ERROR_HANDLING, STYLE_PRESETS
from src.config import should_reload_config, reload_bot_config
from src.core.strategies.factory import create_pipeline
from src.core.data.collector import process_symbol
from src.core.data.reader import (
    bind_shared_market_cache, runtime_data_config, is_read_only, validate_analysis_market_data,
        trading_settings_for_config,
)
from src.core.tracking.trade import TradeTracker
from src.core.position_ownership import get_position_ownership_store
from src.core.performance import get_performance_tracker
from src.exchanges.exchange_factory import get_exchange_client
from src.core import executor, monitor
from src.runtime import normalize_symbol_key
from src.utils.logger import info, error, warning, StageTimer


class PipelineOrchestrator:
    def __init__(self, config: Optional[Dict] = None, *, ws_cache=None, ws_ready=None):
        # Shared cache привязывается до создания стратегии и любого execution client.
        bind_shared_market_cache(ws_cache, ws_ready)
        self.config = runtime_data_config(BOT_CONFIG if config is None else config)
        self.read_only = is_read_only(self.config)
        self.strategy = self.config.get("STRATEGY_STYLE", STRATEGY_STYLE)
        self.instance = self.config.get("STRATEGY_INSTANCE", {})
        self.instance_id = self.config.get("STRATEGY_INSTANCE_ID", self.instance.get("id", f"legacy_{self.strategy}".lower()))
        self.pipeline = create_pipeline(self.strategy, self.config)
        self._tracker = None if self.read_only else TradeTracker()
        self._journal = self.pipeline._journal
        self._client = None
        self._ownership = None if self.read_only else get_position_ownership_store()
        self._cycle_count = 0
        self._last_real_position = None

    def _execution_client(self):
        if self.read_only:
            raise RuntimeError("Торговый клиент запрещён в read_only")
        if self._client is None:
            self._client = get_exchange_client(settings=trading_settings_for_config(self.config))
        return self._client

    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        """Анализ без исполнения; shared cache всегда привязывается первым."""
        bind_shared_market_cache(ws_cache, ws_ready)
        if self.read_only and self.strategy == "SCALP":
            # Dedicated ScalpEngine находится вне этого оркестратора; readonly даёт технический контекст.
            from src.core.analyzer import analyze_symbol
            analysis = analyze_symbol(symbol, config=self.config)
            return {**analysis, "action": "hold", "confidence": 0.0, "execution_allowed": False,
                    "reason": "SCALP read_only: технический анализ без запуска торгового движка"}
        return self.pipeline.run_cycle(symbol, ws_cache, ws_ready)

    def reload(self):
        if not should_reload_config():
            return
        info("🔄 Перезагрузка конфигурации стратегии")
        old = self.config
        if self.instance:
            from src.config_loader import resolve_strategy_instance_config
            config = resolve_strategy_instance_config(self.instance)
        else:
            reload_bot_config()
            from src.config import BOT_CONFIG as new_config
            config = dict(new_config)
        # Runtime injections и выбор аккаунта не теряются при hot-reload стратегии.
        for key in ("PUBLIC_RATE_LIMITERS", "DATA_DIR", "EXCHANGE", "MARKET_TYPE", "EXECUTION_MODE"):
            if key in old:
                config[key] = old[key]
        self.config = runtime_data_config(config)
        self.read_only = is_read_only(self.config)
        self.strategy = self.config.get("STRATEGY_STYLE", self.strategy)
        self.instance = self.config.get("STRATEGY_INSTANCE", self.instance)
        self.instance_id = self.config.get("STRATEGY_INSTANCE_ID", self.instance_id)
        if not self.read_only:
            import src.config as config_module
            from src.runtime import apply_legacy_runtime_config
            apply_legacy_runtime_config(config_module, self.config)
        self.pipeline = create_pipeline(self.strategy, self.config)
        self._journal = self.pipeline._journal
        info(f"✅ Конфигурация {self.instance_id} обновлена ({self.strategy})")

    def run_iteration(self, symbol: str, ws_cache=None, ws_ready=None):
        """Один полный ограниченный цикл; доступен для main и offline-регрессий."""
        bind_shared_market_cache(ws_cache, ws_ready)
        real_position, all_positions = None, {}
        normalized = normalize_symbol_key(symbol)
        if not self.read_only:
            client = self._execution_client()
            if hasattr(client, "reconcile_if_due"):
                client.reconcile_if_due(symbol)
            all_positions = client.get_positions()
            self._ownership.sync_with_positions(all_positions)
            positions = all_positions.get(normalized, [])
            real_position = positions[0] if positions else None
            self._last_real_position = real_position
            owner = self._ownership.get_owner(symbol)
            if owner and owner.owner_id != self.instance_id:
                info(f"⏸️ [{symbol}] Стратегия {owner.owner_id} владеет позицией; {self.instance_id} ожидает")
                return None
        with StageTimer("Сбор данных", symbol, "📊"):
            if not process_symbol(symbol, self.config):
                warning(f"⚠️ [{symbol}] Цикл пропущен: нет пригодных рыночных данных")
                return None
        prediction = self.run_cycle(symbol, ws_cache, ws_ready)
        if prediction is None:
            return None
        prediction["strategy_instance_id"] = self.instance_id
        prediction.setdefault("strategy", self.strategy)
        prediction["execution_mode"] = self.config["EXECUTION_MODE"]
        valid = validate_analysis_market_data(prediction, self.config)
        prediction["execution_allowed"] = not self.read_only and valid and prediction.get("execution_allowed", True)
        current_price = prediction.get("current_price", 0)
        if not getattr(self.pipeline, "records_decisions", False):
            self._journal.record(symbol, prediction, current_price)
        self._journal.trim_entries(symbol, self.strategy)
        if self.read_only:
            info(f"🔎 [{symbol}] {self.instance_id}: анализ={prediction.get('action', 'hold')}, исполнение запрещено (read_only)")
            return prediction
        if not prediction["execution_allowed"]:
            warning(f"⚠️ [{symbol}] Исполнение пропущено: snapshot устарел или сигнал запрещён")
            return prediction
        all_positions = self._execution_client().get_positions()
        self._ownership.sync_with_positions(all_positions)
        positions = all_positions.get(normalized, [])
        real_position = positions[0] if positions else None
        self._last_real_position = real_position
        if not real_position and self._journal.data.get(symbol, {}).get("trade_plan"):
            self._journal.record_close(symbol)
            self._journal.clear_trade_plan(symbol)
        # Private синхронизация и journal могли занять время: проверяем данные у границы исполнения.
        if not validate_analysis_market_data(prediction, self.config):
            prediction["execution_allowed"] = False
            warning(f"⚠️ [{symbol}] Исполнение отменено: данные устарели во время синхронизации")
            return prediction
        with StageTimer("Исполнение сигналов", symbol, "💰"):
            executed = executor.execute_prediction(prediction, all_positions=all_positions,
                                                   owner_id=self.instance_id, strategy_id=self.strategy)
        action = prediction.get("action", "hold")
        if executed:
            if action in ("buy", "sell") and not real_position:
                self._journal.set_trade_plan(symbol, prediction, current_price)
            elif action == "close":
                self._journal.record_close(symbol)
                self._journal.clear_trade_plan(symbol)
        if executed and action in ("buy", "sell") and not real_position and self.strategy in ("HYBRID", "AISCALP", "MACDX"):
            details = prediction.get("details", {})
            try:
                self._tracker.set_entry_context(symbol, {
                    "entry_regime": prediction.get("regime", "UNKNOWN"), "entry_score": details.get("score", 0),
                    "entry_quality": details.get("quality", 0.0), "entry_rsi": prediction.get("rsi", 0),
                    "entry_atr": prediction.get("atr", 0), "entry_volume_ratio": prediction.get("volume_ratio", 0),
                })
            except Exception as exc:
                warning(f"⚠️ [{symbol}] Контекст входа не сохранён: {exc}")
        with StageTimer("Мониторинг позиции", symbol, "👀"):
            monitor.monitor_symbol(symbol, all_positions=all_positions)
        return prediction

    def run_symbol_pipeline(self, symbol: str, ws_cache=None, ws_ready=None, *, stop_event=None):
        bind_shared_market_cache(ws_cache, ws_ready)
        from src.utils.logger import setup_symbol_logger
        setup_symbol_logger(symbol)
        info(f"🚀 Запущен {self.instance_id}/{symbol} ({self.strategy}), PID={os.getpid()}, режим={self.config['EXECUTION_MODE']}")
        if self.strategy == "SCALP" and not self.read_only:
            from src.core.scalp_engine import ScalpEngine
            ScalpEngine(symbol, ws_cache=ws_cache, ws_ready=ws_ready).run()
            return
        if not self.read_only:
            try:
                client = self._execution_client()
                self._tracker.force_sync_all(client.get_positions())
                commission = client.get_commission_rate(symbol)
                if commission:
                    from src.config import update_fee_rates
                    update_fee_rates(commission.maker, commission.taker)
            except Exception as exc:
                warning(f"⚠️ [{symbol}] Начальная синхронизация аккаунта не выполнена: {exc}")
        # Общие и чужие price files не удаляем: пригодность определяет atomic reader.
        last_check = time.monotonic()
        while stop_event is None or not stop_event.is_set():
            try:
                if time.monotonic() - last_check >= 30:
                    self.reload()
                    last_check = time.monotonic()
                self.run_iteration(symbol, ws_cache, ws_ready)
                preset = self.config.get("STYLE_PRESETS", STYLE_PRESETS).get(self.strategy, {})
                self._sleep_cycle(symbol, preset, self._last_real_position, self._cycle_count, stop_event=stop_event)
                self._cycle_count += 1
                if self._tracker is not None:
                    self._tracker.flush()
            except KeyboardInterrupt:
                info(f"🛑 [{symbol}] Цикл остановлен")
                return
            except Exception as exc:
                error(f"❌ [{symbol}] Ошибка цикла: {exc}")
                error(traceback.format_exc())
                delay = ERROR_HANDLING.get("cycle_error_fallback_sleep", 5)
                if stop_event is not None:
                    stop_event.wait(delay)
                else:
                    time.sleep(delay)

    def _sleep_cycle(self, symbol, preset, real_position, cycle_count, *, stop_event=None):
        interval = preset.get("position_check_interval", 5) if real_position else preset.get("loop_interval", 60)
        delay = max(5, interval * random.uniform(0.8, 1.2))
        if not self.read_only and cycle_count % 50 == 0:
            try:
                tracker = get_performance_tracker()
                suggestions = tracker.should_adjust_thresholds()
                if suggestions:
                    tracker.save_calibration_suggestions(suggestions)
            except Exception as exc:
                warning(f"⚠️ [{symbol}] Калибровка не выполнена: {exc}")
        if not self.read_only and cycle_count % 10 == 0:
            try:
                self._execution_client().get_funding_rate(symbol)
            except Exception as exc:
                warning(f"⚠️ [{symbol}] Funding rate недоступен: {exc}")
        if stop_event is not None:
            stop_event.wait(delay)
        else:
            time.sleep(delay)
