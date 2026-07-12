#!/usr/bin/env python3
"""
Главный файл запуска автоматизированной торговой системы.
Интегрирует API выбранной биржи для торговых операций и AI API для анализа рынка.

Автор: Claude Code
Версия: 1.0
"""

import time
import sys
from datetime import datetime

from src.core import collector
from src.core import analyzer
from src.core import predict
from src.core import executor
from src.core import monitor
from src.core import plotter
from src.exchanges.dto.models import PositionSide

from src.utils.logger import info, warning, error
from src.exchanges.exchange_factory import get_exchange_client
from src.config import AI_API_KEY, AI_PROVIDER

def print_banner():
    """Печатает приветственное сообщение"""
    print("=" * 80)
    print("🤖 АВТОМАТИЗИРОВАННАЯ ТОРГОВАЯ СИСТЕМА")
    print("=" * 80)
    print(f"📅 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 80)

    info("|========== НАЧАЛО НОВОГО ТОРГОВОГО ЦИКЛА ==========|")

def check_prerequisites():
    """Проверяет наличие всех необходимых условий для запуска"""
    print("\n🔍 Проверка предварительных условий...")

    errors = []

    ai_required = True
    try:
        from src.config_loader import get_strategy_instances, resolve_strategy_instance_config
        ai_required = False
        for instance in get_strategy_instances():
            config = resolve_strategy_instance_config(instance)
            strategy = instance.strategy.upper()
            if strategy == "SCALP":
                ai_cfg = config.get("SCALP_SETTINGS", {}).get("ai_integration", {})
                ai_required = ai_required or bool(
                    ai_cfg.get("regime_enabled", True) or ai_cfg.get("veto_enabled", True)
                )
            elif strategy not in {"MACDX", "GRID"}:
                ai_required = True
    except Exception as exc:
        warning(f"⚠️ Не удалось определить потребность runtime в ИИ: {exc}")

    if ai_required and (not AI_API_KEY or AI_API_KEY == ""):
        errors.append(f"❌ API Key для провайдера {AI_PROVIDER} не настроен. Установите соответствующую переменную окружения.")

    # Validation: Check for conflicting strategies (REMOVED: Smart Filter handles this now)
    # Conflict check removed to allow Smart Filter logic
    pass

    # Check exchange prerequisites
    client = get_exchange_client()
    if not client.capabilities.automated_strategy:
        errors.append(
            "❌ Выбранный рынок не поддерживает автоматический futures pipeline; "
            "MEXC Spot доступен только через отдельный REST-клиент"
        )
    if not client.check_prerequisites():
        errors.append("❌ Ошибка проверки предварительных условий биржи")

    if errors:
        print("\n".join(errors))
        print("\n⚠️ Для настройки экспортируйте необходимые переменные окружения.")
        error("❌ Проверка предварительных условий не пройдена")
        return False

    print("✅ Все предварительные условия выполнены")
    info("✅ Все предварительные условия выполнены")
    return True

def run_pipeline():
    """Запускает полный цикл торговой системы"""
    print("\n🚀 Запуск торгового пайплайна...")
    print("-" * 80)
    info("🚀 Запуск торгового пайплайна...")

    # Шаг 1: Сбор данных
    print("\n📊 ШАГ 1: Сбор данных о ценах и новостях")
    info("📊 ШАГ 1: Сбор данных о ценах и новостях")
    collector.main()

    # Log open positions at startup
    from src.core.executor import get_open_positions
    positions = get_open_positions()
    if not positions:
        info("📊 Текущие позиции: Нет")
    else:
        pos_details = []
        for sym, pos_list in positions.items():
            for p in pos_list:
                side = 'BUY' if p.side == PositionSide.LONG else 'SELL'
                size = p.size
                pnl = p.unrealized_pnl
                pos_details.append(f"{sym} ({side} {size} | PnL: {pnl})")
        info(f"📊 Текущие позиции: {', '.join(pos_details)}")

    # Шаг 2: Анализ данных
    print("\n🔍 ШАГ 2: Анализ технических индикаторов")
    info("🔍 ШАГ 2: Анализ технических индикаторов")
    analyses = analyzer.main()

    # Шаг 3: Прогнозирование
    print(f"\n🧠 ШАГ 3: Генерация прогнозов с помощью {AI_PROVIDER}")
    info(f"🧠 ШАГ 3: Генерация прогнозов с помощью {AI_PROVIDER}")
    predictions = predict.main(analyses)

    # Шаг 4: Исполнение ордеров
    print("\n💰 ШАГ 4: Исполнение торговых сигналов")
    info("💰 ШАГ 4: Исполнение торговых сигналов")
    executor.main(predictions)

    # Шаг 5: Мониторинг открытых позиций
    print("\n👀 ШАГ 5: Мониторинг открытых позиций")
    info("👀 ШАГ 5: Мониторинг открытых позиций")
    monitor.main()

    # Шаг 6: Генерация графиков
    print("\n📈 ШАГ 6: Генерация графиков")
    info("📈 ШАГ 6: Генерация графиков")
    plotter.main()

    print("\n" + "=" * 80)
    print("✅ ТОРГОВЫЙ ЦИКЛ ЗАВЕРШЕН")
    print("=" * 80)
    info("✅ ТОРГОВЫЙ ЦИКЛ ЗАВЕРШЕН")

def run_multiprocess_pipeline():
    """Запускает отдельный процесс для каждого символа (Multiprocessing)"""
    import multiprocessing
    import os
    import signal
    from src.config import CHART_SETTINGS
    from src.config_loader import clear_config_cache, get_strategy_instances, resolve_strategy_instance_config
    from src.core.process_worker import run_strategy_instance_pipeline
    from src.core.chart_worker import run_chart_worker
    from src.symbol_runtime_control import COMMAND_PATH, STATUS_PATH, atomic_write_json, read_json, utc_now
    from src.runtime import normalize_symbol_key

    print("\n🚀 Запуск мультипроцессного пайплайна...")
    info("🚀 Запуск мультипроцессного пайплайна...")

    def load_instances():
        clear_config_cache()
        return get_strategy_instances()

    instances = load_instances()
    if not instances:
        warning("⚠️ Нет включённых strategy instances; runtime ждёт команду запуска символа")

    instance_configs = {
        instance.id: resolve_strategy_instance_config(instance)
        for instance in instances
    }
    symbols = sorted({instance.symbol for instance in instances})
    strategies = ", ".join(f"{i.id}:{i.symbol}:{i.strategy}" for i in instances)
    info(f"📋 Strategy instances: {strategies}")

    # 0. Start WebSocket data provider BEFORE workers
    ws_cache = None
    ws_ready = None
    try:
        from src.exchanges.ws_provider_factory import start_ws_provider

        intervals = set()
        for config in instance_configs.values():
            style = config.get("STRATEGY_STYLE")
            preset = config.get("STYLE_PRESETS", {}).get(style, {})
            intervals.add(preset.get("timeframe", "5m"))

        if len(intervals) == 1:
            ws_interval = next(iter(intervals))
            print(f"   📡 Запуск WebSocket провайдера (interval={ws_interval})...")
            info(f"📡 Запуск WebSocket провайдера для {len(symbols)} символов")

            ws_cache, ws_ready = start_ws_provider(symbols, interval=ws_interval)

            if ws_cache is None:
                print("   ℹ️ Для выбранного рынка используется REST market-data fallback")
                info("📡 WebSocket отключён для выбранного рынка; используем REST")
            else:
                # Backfill выполняется синхронно внутри provider.
                print("   ⏳ Загрузка исторических данных...")
                time.sleep(3)
                print("   ✅ WebSocket провайдер запущен")
                info("✅ WebSocket провайдер запущен и кэш заполнен")
        else:
            info(f"📡 WebSocket кэш отключён: разные timeframe у инстансов ({sorted(intervals)}). Используем REST.")

    except Exception as e:
        print(f"   ⚠️ WebSocket провайдер недоступен: {e}")
        info(f"⚠️ WebSocket провайдер недоступен, используем REST: {e}")

    workers = {}
    chart_process = None
    last_symbol_command_id = None
    existing_symbol_command = read_json(COMMAND_PATH)
    if existing_symbol_command and existing_symbol_command.get("id"):
        last_symbol_command_id = str(existing_symbol_command["id"])

    def start_worker(instance):
        clear_config_cache()
        config = resolve_strategy_instance_config(instance)
        p = multiprocessing.Process(
            target=run_strategy_instance_pipeline,
            args=(instance.to_dict(), config, ws_cache, ws_ready),
            name=f"Worker-{instance.id}"
        )
        worker_type = instance.strategy

        p.daemon = True
        p.start()
        workers[instance.id] = {
            "process": p,
            "instance": instance,
            "started_at": utc_now(),
            "last_exit_code": None,
        }
        print(f"   🔄 Запущен {worker_type} процесс {instance.id} для {instance.symbol} (PID: {p.pid})")
        info(f"🔄 Запущен {worker_type} процесс {instance.id} для {instance.symbol} (PID: {p.pid})")
        return p

    def stop_worker(instance_id, reason="manual"):
        worker = workers.get(instance_id)
        if not worker:
            info(f"ℹ️ Worker {instance_id} уже остановлен")
            return

        process = worker["process"]
        instance = worker["instance"]
        if not process.is_alive():
            process.join(timeout=0)
            worker["last_exit_code"] = process.exitcode
            workers.pop(instance_id, None)
            info(f"ℹ️ Worker {instance_id} уже завершён (exit={process.exitcode})")
            return

        info(f"⏹️ Останавливаю worker {instance_id} для {instance.symbol} через {reason} (PID: {process.pid})")
        if process.pid:
            try:
                os.kill(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            except Exception as exc:
                warning(f"⚠️ Не удалось отправить SIGINT worker {instance_id}: {exc}")

        process.join(timeout=10)
        if process.is_alive():
            warning(f"⚠️ Worker {instance_id} не остановился штатно, отправляю terminate")
            process.terminate()
            process.join(timeout=5)
        if process.is_alive():
            warning(f"⚠️ Worker {instance_id} всё ещё жив, отправляю kill")
            process.kill()
            process.join(timeout=3)

        worker["last_exit_code"] = process.exitcode
        workers.pop(instance_id, None)
        info(f"✅ Worker {instance_id} остановлен через {reason} (exit={process.exitcode})")

    def refresh_workers_state():
        stopped = []
        for instance_id, worker in list(workers.items()):
            process = worker["process"]
            if process.is_alive():
                continue
            process.join(timeout=0)
            worker["last_exit_code"] = process.exitcode
            stopped.append((instance_id, process.exitcode))
            workers.pop(instance_id, None)
        for instance_id, exit_code in stopped:
            warning(f"⚠️ Worker {instance_id} завершился (exit={exit_code})")

    def select_command_targets(command, active_instances):
        instance_ids = command.get("instance_ids")
        if isinstance(instance_ids, list) and instance_ids:
            return sorted({str(item).lower() for item in instance_ids if str(item).strip()})
        instance_id = str(command.get("instance_id") or "").lower()
        symbol = command.get("symbol")
        if instance_id:
            return [instance_id]
        if symbol:
            symbol_key = normalize_symbol_key(symbol)
            target_ids = {
                item.id for item in active_instances.values()
                if normalize_symbol_key(item.symbol) == symbol_key
            }
            target_ids.update(
                instance_id
                for instance_id, worker in workers.items()
                if normalize_symbol_key(worker["instance"].symbol) == symbol_key
            )
            return sorted(target_ids)
        return []

    def handle_symbol_runtime_command():
        nonlocal last_symbol_command_id
        command = read_json(COMMAND_PATH)
        if not command:
            return
        command_id = str(command.get("id") or "")
        if not command_id or command_id == last_symbol_command_id:
            return

        action = str(command.get("action") or "").lower()
        requested_by = str(command.get("requested_by") or "panel")
        active_instances = {instance.id: instance for instance in load_instances()}
        target_ids = select_command_targets(command, active_instances)
        last_symbol_command_id = command_id

        if action not in {"start", "stop", "restart"}:
            warning(f"⚠️ Неизвестная команда symbol runtime: {action}")
            return
        if not target_ids:
            warning(f"⚠️ Команда {action} не нашла target: {command}")
            return

        info(f"🎛️ Symbol runtime command: {action} targets={target_ids} by={requested_by}")
        if action in {"stop", "restart"}:
            for target_id in target_ids:
                stop_worker(target_id, reason=requested_by)
        if action in {"start", "restart"}:
            for target_id in target_ids:
                instance = active_instances.get(target_id)
                if not instance:
                    warning(f"⚠️ Worker {target_id} не запущен: instance отсутствует или выключен в active.json")
                    continue
                if target_id in workers and workers[target_id]["process"].is_alive():
                    info(f"ℹ️ Worker {target_id} уже запущен")
                    continue
                start_worker(instance)

    def write_symbol_runtime_status():
        payload = {
            "control_enabled": True,
            "runtime_pid": os.getpid(),
            "workers": [
                {
                    "id": instance_id,
                    "symbol": worker["instance"].symbol,
                    "strategy": worker["instance"].strategy,
                    "profile": worker["instance"].profile,
                    "pid": worker["process"].pid,
                    "alive": worker["process"].is_alive(),
                    "started_at": worker.get("started_at"),
                }
                for instance_id, worker in sorted(workers.items())
            ],
            "last_command_id": last_symbol_command_id,
            "updated_at": utc_now(),
            "updated_at_ts": time.time(),
        }
        try:
            atomic_write_json(STATUS_PATH, payload)
        except Exception as exc:
            warning(f"⚠️ Не удалось записать symbol runtime status: {exc}")

    # 1. Запускаем торговые процессы (по одному на strategy instance)
    for instance in instances:
        start_worker(instance)

        # Staggered start: задержка между запуском процессов чтобы не перегрузить API
        if len(instances) > 5:
            time.sleep(2)  # 2 секунды между запусками при >5 символов

    # 2. Запускаем отдельный процесс для графиков (если включен)
    if CHART_SETTINGS.get("enabled", True):
        chart_p = multiprocessing.Process(
            target=run_chart_worker,
            name="Worker-Charts"
        )
        # ВАЖНО: chart_p НЕ может быть демоном, так как он сам порождает процессы (ProcessPoolExecutor)
        chart_p.daemon = False
        chart_process = chart_p
        chart_p.start()
        print(f"   🎨 Запущен процесс генерации графиков (PID: {chart_p.pid})")
        info(f"🎨 Запущен процесс генерации графиков (PID: {chart_p.pid})")
    else:
        print("   🎨 Генерация графиков отключена (CHART_SETTINGS.enabled=false)")
        info("🎨 Генерация графиков отключена через конфиг")

    print("✅ Все процессы запущены. Нажмите Ctrl+C для остановки.")
    info("✅ Все процессы запущены")

    # Держим главный процесс живым, пока не нажмут Ctrl+C
    try:
        while True:
            refresh_workers_state()
            handle_symbol_runtime_command()
            write_symbol_runtime_status()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка главного процесса...")
        info("🛑 Остановка главного процесса...")
    finally:
        # Stop WebSocket provider
        try:
            from src.exchanges.ws_provider_factory import stop_ws_provider
            stop_ws_provider()
            info("📡 WebSocket провайдер остановлен")
        except Exception:
            pass

        # Graceful shutdown of ALL processes
        for instance_id in list(workers.keys()):
            stop_worker(instance_id, reason="runtime_shutdown")
        if chart_process and chart_process.is_alive():
            chart_process.terminate()

        time.sleep(0.5)

        # Force kill if still alive
        if chart_process and chart_process.is_alive():
            print(f"   💀 Force killing PID: {chart_process.pid}")
            chart_process.kill()

def main():
    """Главная функция"""
    print_banner()

    # Проверяем предварительные условия
    if not check_prerequisites():
        sys.exit(1)

    try:
        # Запускаем торговый пайплайн в режиме мультипроцессинга
        run_multiprocess_pipeline()

        completion_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n✨ Система успешно завершила работу в {completion_time}")
        info(f"✨ Система успешно завершила работу в {completion_time}")

    except KeyboardInterrupt:
        print("\n\n⚠️ Получен сигнал прерывания от пользователя")
        error("⚠️ Получен сигнал прерывания от пользователя")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {str(e)}")
        error(f"❌ Критическая ошибка: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
