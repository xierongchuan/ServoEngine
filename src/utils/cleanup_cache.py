#!/usr/bin/env python3
"""
Скрипт для очистки кэша позиций от закрытых/несуществующих записей
"""
import json
import executor
from logger import info, warning, error

def cleanup_position_cache():
    """Очищает кэш от записей, которых нет в открытых позициях"""
    info("🧹 Начинаем очистку кэша позиций...")
    
    # Загружаем текущий кэш
    cache = executor.load_position_cache()
    if not cache:
        info("📭 Кэш пуст, нечего очищать")
        return
    
    info(f"📋 Текущий размер кэша: {len(cache)} записей")
    
    # Получаем список открытых позиций
    positions = executor.get_open_positions()
    
    # Собираем все dealId и workingOrderId из открытых позиций
    valid_ids = set()
    for symbol, pos_list in positions.items():
        for pos in pos_list:
            deal_id = pos.get("dealId", "")
            working_order_id = pos.get("workingOrderId", "")
            if deal_id:
                valid_ids.add(deal_id)
            if working_order_id:
                valid_ids.add(working_order_id)
    
    info(f"🎯 Открытых позиций: {len(positions)}")
    info(f"🔑 Уникальных ID в открытых позициях: {len(valid_ids)}")
    
    # Удаляем записи, которых нет в открытых позициях
    removed_count = 0
    ids_to_remove = []
    
    for cached_id in list(cache.keys()):
        if cached_id not in valid_ids:
            ids_to_remove.append(cached_id)
            del cache[cached_id]
            removed_count += 1
    
    # Сохраняем обновлённый кэш
    if removed_count > 0:
        executor.save_position_cache(cache)
        info(f"✅ Удалено {removed_count} осиротевших записей из кэша:")
        for _id in ids_to_remove:
            info(f"   - {_id}")
    else:
        info("✨ Все записи в кэше актуальны")
    
    info(f"📊 Финальный размер кэша: {len(cache)} записей")

if __name__ == "__main__":
    try:
        cleanup_position_cache()
    except Exception as e:
        error(f"❌ Ошибка очистки кэша: {str(e)}")
