#!/usr/bin/env python3
"""
Скрипт для проверки частичного закрытия позиции на BingX.
"""

import sys
import os
import time
import pytest

pytestmark = pytest.mark.live

# Добавляем корневую директорию в путь
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exchanges.bingx_client import BingXClient
from src.utils.logger import info, error, warning

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def test_partial_close():
    client = BingXClient()
    
    log("🔍 Проверка подключения к BingX...")
    if not client.check_prerequisites():
        log("❌ API ключи не настроены")
        return

    symbol = "BTC-USDT"
    
    # 1. Получаем текущую цену
    klines = client.get_kline_data(symbol, limit=1)
    if not klines:
        log("❌ Не удалось получить цену")
        return
    
    current_price = klines[-1]["closePrice"]
    log(f"📈 Текущая цена {symbol}: {current_price}")

    # 2. Открываем позицию MARKET (минимальный объем 0.0004 для теста, чтобы можно было закрыть половину)
    # Min qty for BTC is usually 0.0001, so 0.0002 is safe for 50% close (0.0001 remaining)
    quantity = 0.0002 
    
    log(f"🚀 ТЕСТ 1: Открытие MARKET позиции на {quantity} BTC...")
    order_id = client.place_order(symbol, "BUY", current_price, quantity, type="MARKET")
    
    if not order_id:
        log("❌ Не удалось создать позицию")
        return

    log(f"✅ Позиция открыта! Order ID: {order_id}")
    time.sleep(3) # Ждем обновления позиции

    # 3. Находим позицию
    positions = client.get_positions()
    log(f"📊 Все позиции: {positions}")
    
    target_pos = None
    # BingXClient returns symbols with '/', e.g. BTC/USDT
    lookup_symbol = symbol.replace("-", "/")
    
    if lookup_symbol in positions:
        # Берем последнюю открытую
        target_pos = positions[lookup_symbol][-1]
    
    if not target_pos:
        log("❌ Позиция не найдена!")
        return
        
    log(f"✅ Позиция найдена: {target_pos['size']} BTC (ID: {target_pos['dealId']})")
    
    # 4. Частичное закрытие (50%)
    log("📉 ТЕСТ 2: Закрытие 50% позиции...")
    closed = client.close_position(symbol, target_pos['dealId'], percentage=0.5)
    
    if not closed:
        log("❌ Не удалось закрыть позицию частично")
        return
        
    log("✅ Частичное закрытие отправлено. Ждем обновления...")
    time.sleep(3)
    
    # 5. Проверка остатка
    positions = client.get_positions()
    remaining_pos = None
    if lookup_symbol in positions:
        for p in positions[lookup_symbol]:
            if str(p['dealId']) == str(target_pos['dealId']):
                remaining_pos = p
                break
    
    if not remaining_pos:
        log("❌ Позиция исчезла полностью! (Ожидался остаток)")
    else:
        expected_size = float(f"{quantity * 0.5:.4f}")
        actual_size = remaining_pos['size']
        log(f"🔍 Остаток позиции: {actual_size} BTC")
        
        if abs(actual_size - expected_size) < 0.00001:
            log("✅ Успех! Остаток соответствует ожидаемому (50%).")
        else:
            log(f"⚠️ Остаток отличается от ожидаемого ({expected_size})")

    # 6. Закрытие остатка (100%)
    log("🗑️ ТЕСТ 3: Полное закрытие остатка...")
    if remaining_pos:
        closed_full = client.close_position(symbol, remaining_pos['dealId'], percentage=1.0)
        if closed_full:
            log("✅ Остаток закрыт.")
        else:
            log("❌ Не удалось закрыть остаток.")
    
    log("\n🎉 ТЕСТ ЗАВЕРШЕН!")

if __name__ == "__main__":
    test_partial_close()
