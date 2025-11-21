#!/usr/bin/env python3
import time
import json
from datetime import datetime
from src.config import *
from . import executor
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client

def close_position(symbol, deal_id, working_order_id=None):
    """Закрывает позицию через ExchangeClient"""
    client = get_exchange_client()
    
    try:
        result = client.close_position(symbol, deal_id)
        
        if result:
            # Удаляем из кэша (импортируем функцию из executor)
            try:
                from executor import load_position_cache, save_position_cache
                cache = load_position_cache()

                # Удаляем deal_id
                if str(deal_id) in cache:
                    del cache[str(deal_id)]

                # Удаляем working_order_id если он передан и отличается от deal_id
                if working_order_id and str(working_order_id) != str(deal_id):
                    if str(working_order_id) in cache:
                        del cache[str(working_order_id)]

                save_position_cache(cache)
                info(f"🗑️ Удалены записи из кэша: deal_id={str(deal_id)[:20]}...")
            except Exception as e:
                warning(f"⚠️ Не удалось удалить {deal_id} из кэша: {e}")

            # Логируем закрытие в trades.log
            log_trade(f"✅ Позиция {deal_id} ({symbol}) закрыта")
            info(f"✅ Позиция {deal_id} ({symbol}) закрыта")
            return True
        else:
            error(f"❌ Не удалось закрыть позицию {deal_id} ({symbol})")
            return False
            
    except Exception as e:
        # Логируем ошибку в trades.log
        log_trade(f"❌ Ошибка закрытия позиции {symbol}: {str(e)}", level='ERROR')
        error(f"❌ Ошибка закрытия позиции {symbol}: {str(e)}")
        return False

def main():
    """Основная функция мониторинга"""
    info("👀 Запуск мониторинга открытых позиций...")
    
    client = get_exchange_client()
    if not client.check_prerequisites():
        return

    all_positions = executor.get_open_positions()

    if not all_positions:
        info("📭 Нет открытых позиций для мониторинга")
        return

    # Фильтруем позиции - отслеживаем только те, что создал бот (есть в кэше)
    cache = executor.load_position_cache()
    positions = {}

    for sym, position_list in all_positions.items():
        filtered_positions = []
        for position in position_list:
            deal_id = str(position.get("dealId", ""))
            working_order_id = str(position.get("workingOrderId", ""))

            # Проверяем, есть ли эта позиция в кэше бота
            if deal_id in cache or (working_order_id and working_order_id in cache):
                filtered_positions.append(position)

        # Если нашлись позиции этого символа в кэше, добавляем их
        if filtered_positions:
            positions[sym] = filtered_positions

    if not positions:
        info("📭 Нет позиций бота для мониторинга (возможно, все закрыты вручную)")
        return

    info(f"📊 Отслеживаем позиции: {list(positions.keys())}")

    # Для каждого символа и его списка позиций
    for sym, position_list in positions.items():
        # Теперь positions[sym] это список позиций
        for i, position in enumerate(position_list):
            # Проверяем время удержания позиции
            try:
                created_time = position.get("created", "")
                deal_id = str(position.get("dealId", ""))

                # Получаем hold_minutes из кэша для этой сделки
                # РЕШЕНИЕ: Используем workingOrderId (стабильный) вместо dealId (меняется)
                working_order_id = str(position.get("workingOrderId", ""))

                # Ищем в кэше по workingOrderId (приоритет)
                hold_minutes = cache.get(working_order_id, None)

                # Если не нашли по workingOrderId, ищем по dealId
                if hold_minutes is None:
                    hold_minutes = cache.get(deal_id, DEFAULT_HOLD_TIME_MINUTES)

                if created_time and deal_id:
                    # Парсим время создания позиции
                    # Capital format: 2023-10-27T10:00:00 (no Z usually, but let's be safe)
                    # BingX format: timestamp (ms) or ISO?
                    # ExchangeClient should normalize this!
                    # But for now let's try to parse ISO.
                    try:
                        if isinstance(created_time, (int, float)):
                             # Timestamp in ms
                             created_date = datetime.fromtimestamp(created_time / 1000)
                        else:
                            created_date = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                    except ValueError:
                         # Fallback or error
                         warning(f"⚠️ Could not parse date: {created_time}")
                         continue
                         
                    now = datetime.now(created_date.tzinfo) if created_date.tzinfo else datetime.now()
                    minutes_held = (now - created_date).total_seconds() / 60

                    # Закрываем позицию если она открыта дольше планируемого времени
                    # (Capital.com должен автоматически закрывать по TP/SL, но страхуемся)
                    if minutes_held > hold_minutes:
                        info(f"⏰ {sym} [позиция {i+1}]: закрываем позицию, открыта {int(minutes_held)} мин (планировалось {hold_minutes} мин)")
                        log_trade(f"⏰ {sym}: автоматическое закрытие позиции (открыта {int(minutes_held)} мин, планировалось {hold_minutes} мин)")
                        close_position(sym, deal_id, working_order_id)
                    else:
                        info(f"⏳ {sym} [позиция {i+1}]: позиция открыта {int(minutes_held)} мин, ждем до {hold_minutes} мин")
                else:
                    warning(f"⚠️ {sym} [позиция {i+1}]: не удалось определить время или ID позиции")

            except Exception as e:
                warning(f"⚠️ {sym} [позиция {i+1}]: ошибка проверки времени: {str(e)}")

if __name__ == "__main__":
    main()