#!/usr/bin/env python3
import time
import json
from datetime import datetime
from config import *
from utils import init_api_session, make_request, get_headers
import executor
from logger import info, error, warning, log_trade

def close_position(deal_id):
    """Закрывает позицию через API"""
    # Сессия уже инициализирована в get_open_positions(), нет необходимости инициализировать снова
    url = f"{API_BASE}positions/{deal_id}"
    headers = get_headers()

    try:
        response = make_request(url, method="delete", headers=headers)
        if response is None:
            error(f"❌ Не удалось закрыть позицию {deal_id} - пустой ответ от сервера")
            return False

        response.raise_for_status()

        # Удаляем из кэша (импортируем функцию из executor)
        try:
            from executor import load_position_cache, save_position_cache
            cache = load_position_cache()
            if deal_id in cache:
                del cache[deal_id]
                save_position_cache(cache)
        except Exception as e:
            warning(f"⚠️ Не удалось удалить {deal_id} из кэша: {e}")

        # Логируем закрытие в trades.log
        log_trade(f"✅ Позиция {deal_id} закрыта")

        info(f"✅ Позиция {deal_id} закрыта")
        return True
    except Exception as e:
        # Логируем ошибку в trades.log
        log_trade(f"❌ Ошибка закрытия позиции: {str(e)}", level='ERROR')

        error(f"❌ Ошибка закрытия позиции: {str(e)}")
        return False

def main():
    """Основная функция мониторинга"""
    info("👀 Запуск мониторинга открытых позиций...")
    positions = executor.get_open_positions()

    if not positions:
        info("📭 Нет открытых позиций для мониторинга")
        return

    info(f"📊 Отслеживаем позиции: {list(positions.keys())}")

    # Для каждого символа и его списка позиций
    for symbol, position_list in positions.items():
        # Теперь positions[symbol] это список позиций
        for i, position in enumerate(position_list):
            # Проверяем время удержания позиции
            try:
                created_time = position.get("created", "")
                deal_id = position.get("dealId", "")

                # Загружаем кэш и получаем hold_minutes из кэша для этой сделки
                # РЕШЕНИЕ: Используем workingOrderId (стабильный) вместо dealId (меняется)
                cache = executor.load_position_cache()
                working_order_id = position.get("workingOrderId", "")

                # Ищем в кэше по workingOrderId (приоритет)
                hold_minutes = cache.get(working_order_id, None)

                # Если не нашли по workingOrderId, ищем по dealId
                if hold_minutes is None:
                    hold_minutes = cache.get(deal_id, DEFAULT_HOLD_TIME_MINUTES)

                if created_time and deal_id:
                    # Парсим время создания позиции
                    created_date = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                    now = datetime.now(created_date.tzinfo) if created_date.tzinfo else datetime.now()
                    minutes_held = (now - created_date).total_seconds() / 60

                    # Закрываем позицию если она открыта дольше планируемого времени
                    # (Capital.com должен автоматически закрывать по TP/SL, но страхуемся)
                    if minutes_held > hold_minutes:
                        info(f"⏰ {symbol} [позиция {i+1}]: закрываем позицию, открыта {int(minutes_held)} мин (планировалось {hold_minutes} мин)")
                        log_trade(f"⏰ {symbol}: автоматическое закрытие позиции (открыта {int(minutes_held)} мин, планировалось {hold_minutes} мин)")
                        close_position(deal_id)
                    else:
                        info(f"⏳ {symbol} [позиция {i+1}]: позиция открыта {int(minutes_held)} мин, ждем до {hold_minutes} мин")
                else:
                    warning(f"⚠️ {symbol} [позиция {i+1}]: не удалось определить время или ID позиции")

            except Exception as e:
                warning(f"⚠️ {symbol} [позиция {i+1}]: ошибка проверки времени: {str(e)}")

if __name__ == "__main__":
    main()