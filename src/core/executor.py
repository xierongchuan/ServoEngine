import json
import os
import time
from src.config import *
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client

# Файл кэша для хранения данных позиций (если нужно)


def get_open_positions():
    """Получает открытые позиции через ExchangeClient"""
    client = get_exchange_client()
    try:
        positions = client.get_positions()



        return positions
    except Exception as e:
        error(f"❌ Ошибка получения позиций: {str(e)}")
        return {}

def create_order(symbol, direction, price, ai_sl=None, ai_tp=None, reason="Unknown", confidence=0.0):
    """Создает ордер с TP/SL через ExchangeClient"""
    client = get_exchange_client()

    try:
        price = float(price)

        # Calculate absolute TP/SL prices
        # Calculate absolute TP/SL prices
        if direction.upper() == "BUY":
            tp_price = price * (1 + TAKE_PROFIT_PERCENT / 100)
            sl_price = price * (1 - STOP_LOSS_PERCENT / 100)
        else:
            tp_price = price * (1 - TAKE_PROFIT_PERCENT / 100)
            sl_price = price * (1 + STOP_LOSS_PERCENT / 100)

        # Override with AI values if provided
        if ai_tp:
            info(f"🤖 Using AI Take Profit: {ai_tp} (Calculated: {tp_price:.4f})")
            tp_price = float(ai_tp)
        if ai_sl:
            info(f"🤖 Using AI Stop Loss: {ai_sl} (Calculated: {sl_price:.4f})")
            sl_price = float(ai_sl)

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

        # Place order WITHOUT SL/TP first
        # We will set SL/TP separately using set_sl_tp to ensure reliability and correct mode handling
        order_id = client.place_order(
            symbol=symbol,
            side=direction,
            price=price,
            quantity=quantity,
            type="MARKET",
            sl=None,
            tp=None
        )

        if order_id:
            # Set SL/TP immediately after order placement
            if tp_price or sl_price:
                info(f"🔄 Setting SL/TP for new order {order_id}...")
                try:
                    # Determine position side
                    pos_side = "LONG" if direction.upper() == "BUY" else "SHORT"

                    if hasattr(client, "set_sl_tp"):
                        client.set_sl_tp(symbol, pos_side, tp=tp_price, sl=sl_price, quantity=quantity)
                        info(f"✅ SL/TP set for {symbol} (TP: {tp_price}, SL: {sl_price})")
                    else:
                        warning(f"⚠️ Client does not support set_sl_tp, SL/TP might not be set")
                except Exception as e:
                    error(f"❌ Failed to set SL/TP for new order {order_id}: {e}")

            # Save to cache (optional, if we need to track other things)
            # cache = load_position_cache()
            # cache[str(order_id)] = ...
            # save_position_cache(cache)

            log_trade(f"📌 {symbol}: открыт ордер {direction} по {price:.5f} "
                      f"(Qty={quantity}, TP={tp_price}, SL={sl_price}, ID={order_id}) "
                      f"| Conf: {confidence:.2f} | Reason: {reason}")
            info(f"✅ {symbol}: открыт ордер {direction} по {price:.5f}")
            return str(order_id)

        return None

    except Exception as e:
        error(f"❌ Ошибка создания ордера {symbol}: {str(e)}")
        log_trade(f"❌ Ошибка создания ордера {symbol}: {str(e)}", level='ERROR')
        return None

def execute_prediction(prediction):
    """
    Исполняет одно предсказание для одного символа.
    Используется в режиме multiprocessing.
    """
    symbol = prediction["symbol"]
    # action = prediction["action"] # Unused variable

    client = get_exchange_client()
    if not client.check_prerequisites():
         error(f"❌ {symbol}: Ошибка подключения к бирже")
         return

    # Получаем все текущие позиции для проверки лимитов и состояния
    try:
        positions = get_open_positions()
    except Exception as e:
        error(f"❌ {symbol}: Ошибка получения позиций: {e}")
        return

    # Проверяем лимит позиций (максимум 5)
    MAX_POSITIONS = 5
    total_positions = sum(len(p) for p in positions.values())

    # Флаг, есть ли у нас позиция по этому символу
    symbol_positions = positions.get(symbol, [])
    has_position = len(symbol_positions) > 0
    current_pos = symbol_positions[0] if has_position else None

    # Determine confidence threshold
    confidence_threshold = MIN_CONFIDENCE_THRESHOLD
    if AGGRESSIVE_MODE:
        confidence_threshold = AGGRESSIVE_SETTINGS.get("MIN_CONFIDENCE", MIN_CONFIDENCE_THRESHOLD)

    # 1. OPEN NEW POSITION
    if not has_position:
        if prediction["action"] in ["buy", "sell"]:
            # Check limits
            if total_positions >= MAX_POSITIONS:
                warning(f"⚠️ {symbol}: Лимит позиций ({MAX_POSITIONS}) достигнут. Пропуск сигнала.")
                return

            if prediction["confidence"] >= confidence_threshold:
                direction = prediction["action"].upper()
                info(f"🚀 {symbol}: Исполнение сигнала {direction} (confidence={prediction['confidence']})")

                create_order(
                    symbol,
                    direction,
                    prediction["current_price"],
                    ai_sl=prediction.get("stop_loss"),
                    ai_tp=prediction.get("take_profit"),
                    reason=prediction["reason"],
                    confidence=prediction["confidence"]
                )
            else:
                info(f"📉 {symbol}: Пропуск сигнала {prediction['action']} (confidence {prediction['confidence']} < {confidence_threshold})")
        else:
            # HOLD or unknown
            info(f"⏸️ {symbol}: {prediction['action'].upper()} ({prediction['reason']})")

    # 2. MANAGE EXISTING POSITION
    else:
        deal_id = current_pos["dealId"]

        # CLOSE / PARTIAL CLOSE
        if prediction["action"] in ["close", "close_partial"] and prediction["confidence"] >= confidence_threshold:
            percentage = prediction.get("percentage", 1.0)
            if prediction["action"] == "close":
                percentage = 1.0

            info(f"📉 {symbol}: Сигнал на закрытие {prediction['action']} ({percentage*100}%)")

            if client.close_position(symbol, deal_id, percentage):
                info(f"✅ {symbol}: Позиция {deal_id} закрыта (частично: {percentage})")
                log_trade(f"✅ {symbol}: Позиция {deal_id} закрыта (частично: {percentage*100}%) | Причина: {prediction['reason']}")
            else:
                error(f"❌ {symbol}: Не удалось закрыть позицию {deal_id}")

        # UPDATE SL/TP (HOLD)
        elif prediction["action"] == "hold":
            ai_sl = prediction.get("stop_loss")
            ai_tp = prediction.get("take_profit")

            if ai_sl or ai_tp:
                # BingXClient needs positionSide (LONG/SHORT)
                pos_type = current_pos["type"].upper() # BUY/SELL
                pos_side = "LONG" if pos_type == "BUY" else "SHORT"

                # Check if SL/TP actually changed significantly to avoid spam
                # (Simple check, can be improved)

                info(f"🔄 {symbol}: Проверка обновления SL/TP (SL: {ai_sl}, TP: {ai_tp})")
                try:
                    if hasattr(client, "set_sl_tp"):
                        client.set_sl_tp(symbol, pos_side, tp=ai_tp, sl=ai_sl)
                    else:
                        warning(f"⚠️ Client does not support set_sl_tp")
                except Exception as e:
                    error(f"❌ {symbol}: Ошибка обновления SL/TP: {e}")
            else:
                 info(f"⏸️ {symbol}: HOLD (Wait for signal)")
        else:
             info(f"⏸️ {symbol}: Игнорируем сигнал {prediction['action']} при открытой позиции")


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

    # Determine confidence threshold
    confidence_threshold = MIN_CONFIDENCE_THRESHOLD
    if AGGRESSIVE_MODE:
        confidence_threshold = AGGRESSIVE_SETTINGS.get("MIN_CONFIDENCE", MIN_CONFIDENCE_THRESHOLD)
        info(f"🔥 Aggressive Mode Active: Using Confidence Threshold {confidence_threshold}")

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
            if pred["action"] in ["close", "close_partial"] and pred["confidence"] >= confidence_threshold:
                current_pos = positions[symbol][0] # Берем первую (обычно единственную)
                deal_id = current_pos["dealId"]
                percentage = pred.get("percentage", 1.0)

                if pred["action"] == "close":
                    percentage = 1.0

                info(f"📉 {symbol}: сигнал {pred['action'].upper()} ({percentage*100}%) (confidence={pred['confidence']}, причина: {pred['reason']})")

                # Используем client напрямую или через monitor (лучше напрямую здесь)
                if client.close_position(symbol, deal_id, percentage):
                    info(f"✅ {symbol}: позиция {deal_id} закрыта (частично: {percentage})")
                    log_trade(f"✅ {symbol}: позиция {deal_id} закрыта (частично: {percentage*100}%) | Причина: {pred['reason']}")

                    # Обновляем кэш (удаляем если полное закрытие)


                    # Обновляем локальный список
                    positions = get_open_positions()
                    total_positions = sum(len(p) for p in positions.values())
                else:
                    error(f"❌ {symbol}: не удалось закрыть позицию {deal_id}")
                    log_trade(f"❌ {symbol}: ошибка закрытия позиции {deal_id}", level='ERROR')
            else:
                info(f"⚠️ У {symbol} уже есть открытая позиция. Новых входов не делаем. Сигнал: {pred['action']}")

            # Check for SL/TP updates on HOLD
            if pred["action"] == "hold":
                ai_sl = pred.get("stop_loss")
                ai_tp = pred.get("take_profit")

                if ai_sl or ai_tp:
                    current_pos = positions[symbol][0]
                    # Determine position side for set_sl_tp
                    # BingXClient needs positionSide (LONG/SHORT)
                    pos_type = current_pos["type"].upper() # BUY/SELL
                    pos_side = "LONG" if pos_type == "BUY" else "SHORT"

                    info(f"🔄 {symbol}: Updating SL/TP for existing position (SL: {ai_sl}, TP: {ai_tp})")
                    try:
                        if hasattr(client, "set_sl_tp"):
                            client.set_sl_tp(symbol, pos_side, tp=ai_tp, sl=ai_sl)
                        else:
                            warning(f"⚠️ Client does not support set_sl_tp")
                    except Exception as e:
                        error(f"❌ Failed to update SL/TP for {symbol}: {e}")

            continue

        # Открываем новые позиции
        if pred["confidence"] >= confidence_threshold:


            if pred["action"] == "buy":
                info(f"📈 {symbol}: сигнал BUY (confidence={pred['confidence']}, причина: {pred['reason']})")
                result = create_order(symbol, "BUY", current_price, ai_sl=pred.get("stop_loss"), ai_tp=pred.get("take_profit"), reason=pred['reason'], confidence=pred['confidence'])
                # Обновляем локальный список позиций и кэш после успешного создания
                if result:
                    positions = get_open_positions()
                    # update_cache_with_working_order_ids(positions)
                    total_positions = sum(len(p) for p in positions.values())
            elif pred["action"] == "sell":
                info(f"📉 {symbol}: сигнал SELL (confidence={pred['confidence']}, причина: {pred['reason']})")
                result = create_order(symbol, "SELL", current_price, ai_sl=pred.get("stop_loss"), ai_tp=pred.get("take_profit"), reason=pred['reason'], confidence=pred['confidence'])
                # Обновляем локальный список позиций и кэш после успешного создания
                if result:
                    positions = get_open_positions()
                    # update_cache_with_working_order_ids(positions)
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
