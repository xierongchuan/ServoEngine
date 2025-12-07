#!/usr/bin/env python3
"""
Главный файл запуска автоматизированной торговой системы.
Интегрирует Capital.com API для торговых операций и DeepSeek API для анализа рынка.

Автор: Claude Code
Версия: 1.0
"""

import time
import sys
import json
from datetime import datetime

from src.core import collector
from src.core import analyzer
from src.core import predict
from src.core import executor
from src.core import monitor
from src.core import plotter

from src.utils.logger import info, error
from src.exchanges.exchange_factory import get_exchange_client
from src.config import DEEPSEEK_API_KEY

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

    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "":
        errors.append("❌ DEEPSEEK_API_KEY не настроен. Установите переменную окружения DEEPSEEK_API_KEY")

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
                side = p.get('type', '?').upper()
                size = p.get('size', 0)
                pnl = p.get('pnl', 0)
                pos_details.append(f"{sym} ({side} {size} | PnL: {pnl})")
        info(f"📊 Текущие позиции: {', '.join(pos_details)}")

    # Шаг 2: Анализ данных
    print("\n🔍 ШАГ 2: Анализ технических индикаторов")
    info("🔍 ШАГ 2: Анализ технических индикаторов")
    analyses = analyzer.main()

    # Шаг 3: Прогнозирование
    print("\n🧠 ШАГ 3: Генерация прогнозов с помощью DeepSeek")
    info("🧠 ШАГ 3: Генерация прогнозов с помощью DeepSeek")
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
    from src.config import SYMBOLS
    from src.core.process_worker import run_symbol_pipeline

    print("\n🚀 Запуск мультипроцессного пайплайна...")
    info("🚀 Запуск мультипроцессного пайплайна...")

    processes = []

    for symbol in SYMBOLS:
        p = multiprocessing.Process(
            target=run_symbol_pipeline,
            args=(symbol,),
            name=f"Worker-{symbol}"
        )
        # ВАЖНО: Делаем процесс демоном, чтобы он умирал вместе с главным процессом
        p.daemon = True
        processes.append(p)
        p.start()
        print(f"   🔄 Запущен бесконечный процесс для {symbol} (PID: {p.pid})")
        info(f"🔄 Запущен бесконечный процесс для {symbol} (PID: {p.pid})")

    print("✅ Все процессы запущены. Нажмите Ctrl+C для остановки.")
    info("✅ Все процессы запущены")

    # Держим главный процесс живым, пока не нажмут Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка главного процесса...")
        info("🛑 Остановка главного процесса...")
        # Демоны будут убиты автоматически при выходе из main

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
