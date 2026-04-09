#!/usr/bin/env python3
"""
Главный файл запуска автоматизированной торговой системы.
Интегрирует BingX API для торговых операций и AI API для анализа рынка.

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

from src.utils.logger import info, error
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

    if not AI_API_KEY or AI_API_KEY == "":
        errors.append(f"❌ API Key для провайдера {AI_PROVIDER} не настроен. Установите соответствующую переменную окружения.")

    # Validation: Check for conflicting strategies (REMOVED: Smart Filter handles this now)
    # Conflict check removed to allow Smart Filter logic
    pass

    # Check exchange prerequisites
    client = get_exchange_client()
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
    from src.config import SYMBOLS, STRATEGY_STYLE, BOT_CONFIG, STYLE_PRESETS, CHART_SETTINGS
    from src.core.process_worker import run_symbol_pipeline
    from src.core.chart_worker import run_chart_worker

    print("\n🚀 Запуск мультипроцессного пайплайна...")
    info("🚀 Запуск мультипроцессного пайплайна...")
    info(f"📋 Режим стратегии: {STRATEGY_STYLE}")

    # 0. Start WebSocket data provider BEFORE workers
    ws_cache = None
    ws_ready = None
    try:
        from src.exchanges.ws_data_provider import start_ws_provider

        # Get timeframe from strategy preset
        current_preset = STYLE_PRESETS.get(STRATEGY_STYLE, {})
        ws_interval = current_preset.get("timeframe", "5m")

        print(f"   📡 Запуск WebSocket провайдера (interval={ws_interval})...")
        info(f"📡 Запуск WebSocket провайдера для {len(SYMBOLS)} символов")

        ws_cache, ws_ready = start_ws_provider(SYMBOLS, interval=ws_interval)

        # Wait for initial REST backfill to complete
        print("   ⏳ Загрузка исторических данных...")
        time.sleep(3)  # Give time for backfill

        print("   ✅ WebSocket провайдер запущен")
        info("✅ WebSocket провайдер запущен и кэш заполнен")

    except Exception as e:
        print(f"   ⚠️ WebSocket провайдер недоступен: {e}")
        info(f"⚠️ WebSocket провайдер недоступен, используем REST: {e}")

    processes = []

    # 1. Запускаем торговые процессы (по одному на символ)
    for symbol in SYMBOLS:
        # Выбираем воркер в зависимости от стратегии
        if STRATEGY_STYLE == "GRID":
            from src.core.grid_worker import run_grid_worker
            grid_config = BOT_CONFIG.get("GRID_SETTINGS", {})
            p = multiprocessing.Process(
                target=run_grid_worker,
                args=(symbol, grid_config),
                name=f"GridWorker-{symbol}"
            )
            worker_type = "Grid"
        else:
            p = multiprocessing.Process(
                target=run_symbol_pipeline,
                args=(symbol, ws_cache, ws_ready),
                name=f"Worker-{symbol}"
            )
            worker_type = STRATEGY_STYLE

        p.daemon = True
        processes.append(p)
        p.start()
        print(f"   🔄 Запущен {worker_type} процесс для {symbol} (PID: {p.pid})")
        info(f"🔄 Запущен {worker_type} процесс для {symbol} (PID: {p.pid})")

        # Staggered start: задержка между запуском процессов чтобы не перегрузить API
        if len(SYMBOLS) > 5:
            time.sleep(2)  # 2 секунды между запусками при >5 символов

    # 2. Запускаем отдельный процесс для графиков (если включен)
    if CHART_SETTINGS.get("enabled", True):
        chart_p = multiprocessing.Process(
            target=run_chart_worker,
            name="Worker-Charts"
        )
        # ВАЖНО: chart_p НЕ может быть демоном, так как он сам порождает процессы (ProcessPoolExecutor)
        chart_p.daemon = False
        processes.append(chart_p)
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
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка главного процесса...")
        info("🛑 Остановка главного процесса...")
    finally:
        # Stop WebSocket provider
        try:
            from src.exchanges.ws_data_provider import stop_ws_provider
            stop_ws_provider()
            info("📡 WebSocket провайдер остановлен")
        except Exception:
            pass

        # Graceful shutdown of ALL processes
        for p in processes:
            if p.is_alive():
                p.terminate()

        time.sleep(0.5)

        # Force kill if still alive
        for p in processes:
            if p.is_alive():
                print(f"   💀 Force killing PID: {p.pid}")
                p.kill()

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
