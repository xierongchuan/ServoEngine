import json
import os
import time
from src.config import *
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client

# Файл кэша для хранения hold_minutes по dealId
POSITION_CACHE_FILE = f"{DATA_DIR}/positions_cache.json"

def update_cache_with_working_order_ids(positions):
    """Обновляет кэш с workingOrderId для позиций"""
    cache = load_position_cache()

    for sym, pos_list in positions.items():
        for pos in pos_list:
            deal_id = pos.get("dealId", "")
            working_order_id = pos.get("workingOrderId", "")
            if deal_id and working_order_id and deal_id in cache:
                cache[working_order_id] = cache[deal_id]

    save_position_cache(cache)

def load_position_cache():
    """Загружает кэш позиций из файла"""
    try:
        if os.path.exists(POSITION_CACHE_FILE):
            with open(POSITION_CACHE_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        warning(f"⚠️ Ошибка загрузки кэша позиций: {e}")
        return {}

def save_position_cache(cache):
    """Сохраняет кэш позиций в файл"""
    try:
        with open(POSITION_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        error(f"❌ Ошибка сохранения кэша позиций: {e}")

def get_open_positions():
    """Получает открытые позиции через ExchangeClient"""
    client = get_exchange_client()
    try:
        positions = client.get_positions()
        
        # Enrich with hold_minutes from cache
        cache = load_position_cache()
        for sym, pos_list in positions.items():
            for pos in pos_list:
                deal_id = str(pos.get("dealId", ""))
                pos["hold_minutes"] = cache.get(deal_id, DEFAULT_HOLD_TIME_MINUTES)
                
        return positions
    except Exception as e:
        error(f"❌ Ошибка получения позиций: {str(e)}")
        return {}

def create_order(symbol, direction, price, hold_minutes=DEFAULT_HOLD_TIME_MINUTES):
    """Создает ордер с TP/SL через ExchangeClient"""
    client = get_exchange_client()
    
    try:
        price = float(price)
        
        # Calculate absolute TP/SL prices
        if direction.upper() == "BUY":
            tp_price = price * (1 + TAKE_PROFIT_PERCENT / 100)
            sl_price = price * (1 - STOP_LOSS_PERCENT / 100)
        else:
            tp_price = price * (1 - TAKE_PROFIT_PERCENT / 100)
            sl_price = price * (1 + STOP_LOSS_PERCENT / 100)
            
        # Rounding (optional, but good for APIs)
        tp_price = round(tp_price, 4) # 4 decimals is safe for most forex/crypto
        sl_price = round(sl_price, 4)
        
        info(f"🎯 Calculated TP: {tp_price}, SL: {sl_price}")

        order_id = client.place_order(
            symbol=symbol,
            side=direction,
            price=price,
            quantity=POSITION_SIZE,
            type="MARKET",
            sl=sl_price,
            tp=tp_price
        )
        
        if order_id:
            # Save to cache
            cache = load_position_cache()
            cache[str(order_id)] = hold_minutes
            save_position_cache(cache)
            
            log_trade(f"📌 {symbol}: открыт ордер {direction} по {price:.5f} "
                      f"(TP={tp_price}, SL={sl_price}, ID={order_id}, hold={hold_minutes}мин)")
            info(f"✅ {symbol}: открыт ордер {direction} по {price:.5f}")
            return str(order_id)
            
        return None

    except Exception as e:
        error(f"❌ Ошибка создания ордера {symbol}: {str(e)}")
        log_trade(f"❌ Ошибка создания ордера {symbol}: {str(e)}", level='ERROR')
        return None

def main(predictions):
    """Основная функция исполнения ордеров"""
    info("🚀 Начинаем исполнение ордеров...")
    
    client = get_exchange_client()
    if not client.check_prerequisites():
        return

    positions = get_open_positions()  # ОДИН раз получаем все позиции
    info(f"📊 Открытые позиции: {list(positions.keys())}")

    # Проверяем лимит позиций (максимум 5)
    MAX_POSITIONS = 5
    total_positions = sum(len(p) for p in positions.values())
    
    if total_positions >= MAX_POSITIONS:
        warning(f"⚠️ Достигнут лимит открытых позиций ({MAX_POSITIONS}). Новые позиции не открываем.")
        return

    for pred in predictions:
        symbol = pred["symbol"]
        current_price = pred["current_price"]

        # Проверяем общий лимит позиций (максимум 5)
        if total_positions >= MAX_POSITIONS:
            warning(f"⚠️ Достигнут лимит позиций ({MAX_POSITIONS}). Пропускаем {symbol}")
            continue

        # Проверяем, есть ли уже открытая позиция по данному символу (максимум 1 на актив)
        if symbol in positions and len(positions[symbol]) > 0:
            info(f"⚠️ У {symbol} уже есть {len(positions[symbol])} открытая(ых) позиция(й). Новую позицию не создаем.")
            continue

        # Открываем новые позиции
        if pred["confidence"] >= MIN_CONFIDENCE_THRESHOLD:
            hold_minutes = pred.get("hold_minutes", DEFAULT_HOLD_TIME_MINUTES)  # По умолчанию из конфигурации

            if pred["action"] == "buy":
                info(f"📈 {symbol}: сигнал BUY (confidence={pred['confidence']}, причина: {pred['reason']})")
                result = create_order(symbol, "BUY", current_price, hold_minutes)
                # Обновляем локальный список позиций и кэш после успешного создания
                if result:
                    positions = get_open_positions()
                    update_cache_with_working_order_ids(positions)
                    total_positions = sum(len(p) for p in positions.values())
            elif pred["action"] == "sell":
                info(f"📉 {symbol}: сигнал SELL (confidence={pred['confidence']}, причина: {pred['reason']})")
                result = create_order(symbol, "SELL", current_price, hold_minutes)
                # Обновляем локальный список позиций и кэш после успешного создания
                if result:
                    positions = get_open_positions()
                    update_cache_with_working_order_ids(positions)
                    total_positions = sum(len(p) for p in positions.values())
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