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

import collector
import analyzer
import predict
import executor
import monitor
import plotter

from logger import info, error

def print_banner():
    """Печатает приветственное сообщение"""
    print("=" * 80)
    print("🤖 АВТОМАТИЗИРОВАННАЯ ТОРГОВАЯ СИСТЕМА")
    print("=" * 80)
    print(f"📅 Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 80)

def check_prerequisites():
    """Проверяет наличие всех необходимых условий для запуска"""
    print("\n🔍 Проверка предварительных условий...")

    from config import DEEPSEEK_API_KEY, USERNAME, PASSWORD, CAP_API_KEY

    errors = []

    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "":
        errors.append("❌ DEEPSEEK_API_KEY не настроен. Установите переменную окружения DEEPSEEK_API_KEY")

    if not USERNAME or not PASSWORD or USERNAME == "" or PASSWORD == "":
        errors.append("❌ Учетные данные Capital.com не настроены. Установите DEMO_USERNAME и DEMO_PASSWORD")

    if not CAP_API_KEY or CAP_API_KEY == "":
        errors.append("❌ CAP_API_KEY не настроен. Получите API ключ в Settings > API Integrations на Capital.com")

    if errors:
        print("\n".join(errors))
        print("\n⚠️ Для настройки экспортируйте переменные:")
        print("   export DEEPSEEK_API_KEY='ваш_ключ'")
        print("   export DEMO_USERNAME='ваш_email'")
        print("   export DEMO_PASSWORD='ваш_пароль'")
        print("   export CAP_API_KEY='ваш_api_ключ_из_capital_com'")
        print("\n📋 Как получить CAP_API_KEY:")
        print("   1. Войдите в аккаунт Capital.com")
        print("   2. Settings > API Integrations")
        print("   3. Generate API key")
        print("   4. Скопируйте API ключ и установите его в CAP_API_KEY")
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

def main():
    """Главная функция"""
    print_banner()

    # Проверяем предварительные условия
    if not check_prerequisites():
        sys.exit(1)

    try:
        # Запускаем торговый пайплайн
        run_pipeline()

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
