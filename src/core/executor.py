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

        # Calculate quantity based on Balance Percentage
        balance_data = client.get_balance()

        # Extract equity/balance based on exchange response structure
        # BingX get_balance (perpetual) returns dict with 'balance' or 'equity'
        # Capital.com returns list of accounts

        total_balance = 0.0

        if isinstance(balance_data, dict):
            # BingX Perpetual: {'balance': 1000, 'equity': 1000, ...}
            # BingX Standard: {'balance': 1000, ...}
            # Try equity first, then balance
            total_balance = float(balance_data.get("equity", balance_data.get("balance", 0.0)))
        elif isinstance(balance_data, list):
            # Capital.com or BingX Spot/Standard list
            # Sum up available balances or take the first one
            for acc in balance_data:
                # Capital: 'balance' or 'available'
                # BingX Spot: 'balance'
                b = float(acc.get("equity", acc.get("balance", 0.0)))
                total_balance += b

        if total_balance <= 0:
            error(f"❌ Balance is 0 or could not be retrieved. Cannot calculate position size.")
            return None

        # Calculate trade amount in USDT
        trade_amount = total_balance * (POSITION_SIZE_PERCENT / 100.0)

        # Enforce Minimum Trade Amount
        if trade_amount < MIN_TRADE_AMOUNT_USDT:
            info(f"⚠️ Calculated amount ${trade_amount:.2f} is less than min ${MIN_TRADE_AMOUNT_USDT}. Using min amount.")
            trade_amount = MIN_TRADE_AMOUNT_USDT

        # Check if we have enough balance for this trade (simplified check)
        if trade_amount > total_balance:
             warning(f"⚠️ Trade amount ${trade_amount:.2f} exceeds balance ${total_balance:.2f}. Adjusting to 95% of balance.")
             trade_amount = total_balance * 0.95

        quantity = trade_amount / price

        # Round quantity to appropriate precision (e.g., 4 decimals for crypto)
        # In a real scenario, this should be symbol-specific
        quantity = round(quantity, 4)

        info(f"🧮 Calculated quantity: {quantity} (Balance: ${total_balance:.2f} | Amount: ${trade_amount:.2f} [{POSITION_SIZE_PERCENT}%])")

        order_id = client.place_order(
            symbol=symbol,
            side=direction,
            price=price,
            quantity=quantity,
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
    if not positions:
        info("📊 Открытые позиции: Нет")
    else:
        pos_details = []
        for sym, pos_list in positions.items():
            for p in pos_list:
                side = p.get('type', '?').upper()
                size = p.get('size', 0)
                pnl = p.get('pnl', 0)
                pos_details.append(f"{sym} ({side} {size} | PnL: {pnl})")

        info(f"📊 Открытые позиции: {', '.join(pos_details)}")

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
        has_position = symbol in positions and len(positions[symbol]) > 0

        if has_position:
            # Если позиция есть, проверяем сигналы на выход
            if pred["action"] in ["close", "close_partial"] and pred["confidence"] >= MIN_CONFIDENCE_THRESHOLD:
                current_pos = positions[symbol][0] # Берем первую (обычно единственную)
                deal_id = current_pos["dealId"]
                percentage = pred.get("percentage", 1.0)

                if pred["action"] == "close":
                    percentage = 1.0

                info(f"📉 {symbol}: сигнал {pred['action'].upper()} ({percentage*100}%) (confidence={pred['confidence']}, причина: {pred['reason']})")

                # Используем client напрямую или через monitor (лучше напрямую здесь)
                if client.close_position(symbol, deal_id, percentage):
                    info(f"✅ {symbol}: позиция {deal_id} закрыта (частично: {percentage})")
                    # Обновляем кэш (удаляем если полное закрытие)
                    if percentage == 1.0:
                         # Импорт внутри функции чтобы избежать циклических зависимостей если они есть,
                         # но здесь мы в executor, так что используем свои функции
                         cache = load_position_cache()
                         if str(deal_id) in cache:
                             del cache[str(deal_id)]
                         save_position_cache(cache)

                    # Обновляем локальный список
                    positions = get_open_positions()
                    total_positions = sum(len(p) for p in positions.values())
                else:
                    error(f"❌ {symbol}: не удалось закрыть позицию {deal_id}")
            else:
                info(f"⚠️ У {symbol} уже есть открытая позиция. Новых входов не делаем. Сигнал: {pred['action']}")

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
