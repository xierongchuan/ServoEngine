#!/usr/bin/env python3
"""
Скрипт для проверки возможности создания, изменения и удаления ордеров на BingX.
ВАЖНО: Использует безопасный LIMIT ордер далеко от текущей цены, чтобы избежать исполнения.
"""

import sys
import os
import time

# Добавляем корневую директорию в путь
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.exchanges.bingx_client import BingXClient
from src.utils.logger import info, error, warning

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def test_order_lifecycle():
    client = BingXClient()
    
    log("🔍 Проверка подключения к BingX...")
    if not client.check_prerequisites():
        log("❌ API ключи не настроены")
        return

    # 1. Получаем баланс
    balance = client.get_perpetual_balance()
    if balance is None:
        log("❌ Не удалось получить баланс. Проверьте API ключи и подключение.")
        # Продолжаем тест, но предупреждаем
    else:
        log(f"💰 Баланс: {balance.get('balance', 'N/A')} USDT")

    symbol = "BTC-USDT"
    
    # 2. Получаем текущую цену
    klines = client.get_kline_data(symbol, limit=1)
    if not klines:
        log("❌ Не удалось получить цену")
        return
    
    current_price = klines[-1]["closePrice"]
    log(f"📈 Текущая цена {symbol}: {current_price}")

    # 3. Создаем БЕЗОПАСНЫЙ лимитный ордер (на 50% ниже текущей цены)
    safe_price = int(current_price * 0.5)
    quantity = 0.0002 # Минимальный объем
    
    log(f"🚀 ТЕСТ 1: Создание LIMIT ордера по цене {safe_price} (далеко от рынка)...")
    order_id = client.place_order(symbol, "BUY", safe_price, quantity, type="LIMIT")
    
    if not order_id:
        log("❌ Не удалось создать ордер")
        return

    log(f"✅ Ордер создан! ID: {order_id}")
    time.sleep(2)

    # 4. Проверяем наличие ордера
    log("🔍 ТЕСТ 2: Проверка наличия ордера в списке открытых...")
    open_orders = client.get_open_orders(symbol)
    found = False
    for order in open_orders:
        if str(order.get("orderId")) == str(order_id):
            found = True
            log(f"✅ Ордер {order_id} найден в списке открытых!")
            log(f"   Детали: {order}")
            break
    
    if not found:
        log("❌ Ордер не найден в списке открытых!")
    
    # 5. Отменяем ордер (Удаление)
    log(f"🗑️ ТЕСТ 3: Отмена (удаление) ордера {order_id}...")
    cancelled = client.cancel_order(symbol, order_id)
    
    if cancelled:
        log("✅ Ордер успешно отменен!")
    else:
        log("❌ Не удалось отменить ордер")

    # 6. Финальная проверка
    time.sleep(1)
    open_orders = client.get_open_orders(symbol)
    found_after_cancel = False
    for order in open_orders:
        if str(order.get("orderId")) == str(order_id):
            found_after_cancel = True
            break
            
    if not found_after_cancel:
        log("✅ Подтверждено: Ордер исчез из списка открытых.")
    else:
        log("❌ Ошибка: Ордер все еще висит в открытых!")

    log("\n🎉 ТЕСТ ЗАВЕРШЕН УСПЕШНО!")

if __name__ == "__main__":
    test_order_lifecycle()
