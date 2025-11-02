import json
import os
import time
from config import *
from utils import init_api_session, make_request, get_headers
from logger import info, error, warning, log_trade
from symbols import get_epic, get_symbol

def get_open_positions():
    """Получает открытые позиции напрямую из Capital.com API"""
    init_api_session()  # Убедимся, что сессия активна
    url = f"{API_BASE}positions"
    headers = get_headers()

    try:
        response = make_request(url, headers=headers)
        if response is None:
            return {}

        positions = {}

        for position in response.json().get("positions", []):
            market = position.get("market", {})
            epic = market.get("epic", "")

            # Преобразуем EPIC в символ через единый модуль
            symbol = get_symbol(epic)
            
            if symbol in SYMBOLS and position.get("position", {}).get("status") == "OPEN":
                pos = position["position"]
                positions[symbol] = {
                    "type": pos["direction"].lower(),
                    "entry": pos["openLevel"],
                    "dealId": pos["dealId"],
                    "created": pos["createdDate"]
                }
        return positions
    except Exception as e:
        error(f"❌ Ошибка получения позиций: {str(e)}")
        return {}

def create_order(symbol, direction, price):
    """Создает ордер с TP/SL через Capital.com"""
    init_api_session()  # Убедимся, что сессия активна
    url = f"{API_BASE}positions/otc"
    headers = get_headers()

    # Получаем EPIC код через единый модуль
    epic = get_epic(symbol)

    # Валидация цены
    try:
        price = float(price)
        if price <= 0:
            raise ValueError(f"Некорректная цена: {price}")
    except (TypeError, ValueError) as e:
        raise ValueError(f"Цена должна быть положительным числом: {str(e)}")

    # Расчет расстояния в пунктах (зависит от актива)
    # Для форекса: пункты (вторая цифра после запятой)
    if symbol in ["EUR/USD", "GBP/USD", "USD/JPY"]:
        # Конвертируем проценты в пункты (1% = 10 пунктов для EUR/USD)
        tp_distance = str(int(TAKE_PROFIT_PERCENT * 10))
        sl_distance = str(int(STOP_LOSS_PERCENT * 10))
    # Для криптовалют: абсолютное значение в USD
    elif symbol == "BTC/USD":
        tp_distance = str(int(price * TAKE_PROFIT_PERCENT / 100))
        sl_distance = str(int(price * STOP_LOSS_PERCENT / 100))
    # Для акций и товаров: также используем абсолютное значение
    else:
        tp_distance = str(int(price * TAKE_PROFIT_PERCENT / 100))
        sl_distance = str(int(price * STOP_LOSS_PERCENT / 100))

    # Проверяем разумность значений
    if int(tp_distance) < 1 or int(sl_distance) < 1:
        raise ValueError(f"Слишком маленькие TP/SL: TP={tp_distance}, SL={sl_distance}")
    
    payload = {
        "epic": epic,
        "direction": direction,
        "orderType": "MARKET",
        "size": POSITION_SIZE,
        "currencyCode": "USD",
        "forceOpen": True,
        "guaranteedStop": False,
        "stopLoss": {
            "distance": sl_distance,
            "type": "BID"
        },
        "takeProfit": {
            "distance": tp_distance,
            "type": "BID"
        }
    }
    
    try:
        response = make_request(url, method="post", json=payload, headers=headers)
        if response is None:
            raise Exception("❌ Не удалось создать ордер - пустой ответ от сервера")

        deal_id = response.json()["dealId"]

        # Логируем сделку в trades.log
        log_trade(f"📌 {symbol}: открыт ордер {direction} по {price:.5f} "
                  f"(TP={tp_distance}, SL={sl_distance}, ID={deal_id})")

        info(f"✅ {symbol}: открыт ордер {direction} по {price:.5f} (TP={tp_distance}, SL={sl_distance})")
        return deal_id
    except Exception as e:
        # Логируем ошибку в trades.log
        log_trade(f"❌ Ошибка создания ордера {symbol}: {str(e)}", level='ERROR')

        error(f"❌ Ошибка создания ордера {symbol}: {str(e)}")
        return None

def main(predictions):
    """Основная функция исполнения ордеров"""
    info("\n🚀 Начинаем исполнение ордеров...")
    positions = get_open_positions()
    info(f"📊 Открытые позиции: {list(positions.keys())}")

    # Проверяем лимит позиций (максимум 5)
    MAX_POSITIONS = 5
    if len(positions) >= MAX_POSITIONS:
        warning(f"⚠️ Достигнут лимит открытых позиций ({MAX_POSITIONS}). Новые позиции не открываем.")
        return

    for pred in predictions:
        symbol = pred["symbol"]
        current_price = pred["current_price"]

        # Проверяем лимит перед каждой новой позицией
        current_positions = get_open_positions()
        if len(current_positions) >= MAX_POSITIONS:
            warning(f"⚠️ Достигнут лимит позиций ({MAX_POSITIONS}). Пропускаем {symbol}")
            continue

        # Открываем новые позиции
        if symbol not in positions and pred["confidence"] > 0.6:
            if pred["action"] == "buy":
                info(f"📈 {symbol}: сигнал BUY (confidence={pred['confidence']}, причина: {pred['reason']})")
                create_order(symbol, "BUY", current_price)
            elif pred["action"] == "sell":
                info(f"📉 {symbol}: сигнал SELL (confidence={pred['confidence']}, причина: {pred['reason']})")
                create_order(symbol, "SELL", current_price)
            else:
                info(f"🔄 {symbol}: действие {pred['action']} не требует открытия позиции")

if __name__ == "__main__":
    import sys, json, predict, analyzer

    info("🔄 Запуск исполнения ордеров...")
    
    # Если запускается через пайплайн
    if not sys.stdin.isatty():
        predictions = json.load(sys.stdin)
    else:
        analyses = analyzer.main()
        predictions = predict.main(analyses)
    
    main(predictions)