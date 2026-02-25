import time
import json
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

def create_order(symbol, direction, price, ai_sl=None, ai_tp=None, reason="Unknown", confidence=0.0, size_pct=None, order_type="MARKET"):
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
        _price_prec = POSITION_LIMITS.get("price_precision", 4)
        tp_price = round(tp_price, _price_prec)
        sl_price = round(sl_price, _price_prec)

        info(f"🎯 Calculated TP: {tp_price}, SL: {sl_price}")

        # Calculate quantity based on Balance Percentage
        balance_data = client.get_balance()
        info(f"🔍 [DEBUG] Raw balance_data in executor: {balance_data}")
        info(f"🔍 [DEBUG] balance_data type: {type(balance_data)}, keys: {balance_data.keys() if isinstance(balance_data, dict) else 'N/A'}")

        # Debug: print all fields for balance data
        if isinstance(balance_data, dict):
            info(f"🔍 [DEBUG] All balance_data fields: {json.dumps(balance_data, indent=2)}")
        elif isinstance(balance_data, list):
            info(f"🔍 [DEBUG] balance_data is list, first item: {balance_data[0] if balance_data else 'empty'}")

        # Extract equity/balance based on exchange response structure
        total_balance = 0.0

        if isinstance(balance_data, dict):
            # Try ALL possible field names for different API responses
            # VST Demo might use different field names
            for key in ["equity", "availableBalance", "balance", "availableMargin", "marginBalance", "walletBalance", "totalBalance", "totalWalletBalance"]:
                val = balance_data.get(key)
                if val is not None:
                    try:
                        parsed = float(val)
                        info(f"🔍 [DEBUG] Found balance field '{key}': {parsed}")
                        if parsed > 0:
                            total_balance = parsed
                            break
                    except (ValueError, TypeError):
                        pass

            # If still 0, try to get from nested data
            if total_balance == 0:
                nested_data = balance_data.get("data", {})
                if isinstance(nested_data, dict):
                    for key in ["equity", "availableBalance", "balance", "availableMargin", "marginBalance", "walletBalance"]:
                        val = nested_data.get(key)
                        if val is not None:
                            try:
                                parsed = float(val)
                                info(f"🔍 [DEBUG] Found balance in nested 'data': '{key}': {parsed}")
                                if parsed > 0:
                                    total_balance = parsed
                                    break
                            except (ValueError, TypeError):
                                pass
        elif isinstance(balance_data, list):
            for acc in balance_data:
                b = float(acc.get("equity", acc.get("balance", 0.0)))
                total_balance += b

        if total_balance <= 0:
            error(f"❌ Balance is 0 or could not be retrieved. Cannot calculate position size.")
            return None

        # Calculate trade amount in USDT (use dynamic size if provided)
        effective_size_pct = size_pct if size_pct else POSITION_SIZE_PERCENT
        trade_amount = total_balance * (effective_size_pct / 100.0)
        if size_pct:
            info(f"📐 Dynamic position size: {effective_size_pct:.1f}% (default: {POSITION_SIZE_PERCENT}%)")

        # Reserve fee from margin before sizing (round-trip: entry + exit)
        from src.config import TRADING_FEE_TAKER
        estimated_round_trip_fee = trade_amount * LEVERAGE * (TRADING_FEE_TAKER / 100.0) * 2.0
        fee_adjusted_amount = trade_amount - estimated_round_trip_fee
        if fee_adjusted_amount >= MIN_TRADE_AMOUNT_USDT:
            info(f"💰 Fee reserve: ${estimated_round_trip_fee:.2f} (taker={TRADING_FEE_TAKER}% × 2 × {LEVERAGE}x) | ${trade_amount:.2f} → ${fee_adjusted_amount:.2f}")
            trade_amount = fee_adjusted_amount
        else:
            info(f"💰 Fee reserve skipped (would reduce below min): ${estimated_round_trip_fee:.2f}")

        # Enforce Minimum Trade Amount
        if trade_amount < MIN_TRADE_AMOUNT_USDT:
            info(f"⚠️ Calculated amount ${trade_amount:.2f} is less than min ${MIN_TRADE_AMOUNT_USDT}. Using min amount.")
            trade_amount = MIN_TRADE_AMOUNT_USDT

        # Check if we have enough balance for this trade (simplified check)
        if trade_amount > total_balance:
             _safety = POSITION_LIMITS.get("balance_safety_margin", 0.95)
             warning(f"⚠️ Trade amount ${trade_amount:.2f} exceeds balance ${total_balance:.2f}. Adjusting to {_safety*100:.0f}% of balance.")
             trade_amount = total_balance * _safety

        # Apply leverage: trade_amount is MARGIN, quantity is the leveraged position
        notional_value = trade_amount * LEVERAGE
        quantity = notional_value / price

        # Round quantity to appropriate precision (e.g., 4 decimals for crypto)
        # In a real scenario, this should be symbol-specific
        _qty_prec = POSITION_LIMITS.get("quantity_precision", 4)
        quantity = round(quantity, _qty_prec)

        # Enhanced position size logging with leverage info
        info(f"🧮 Position Size:")
        info(f"   Balance: ${total_balance:.2f} | Margin: {effective_size_pct:.1f}% = ${trade_amount:.2f}")
        info(f"   Leverage: {LEVERAGE}x | Notional: ${notional_value:.2f}")
        info(f"   Quantity: {quantity} {symbol.replace('USDT', '')}")

        # Place order WITHOUT SL/TP first
        # We will set SL/TP separately using set_sl_tp to ensure reliability and correct mode handling
        order_id = client.place_order(
            symbol=symbol,
            side=direction,
            price=price,
            quantity=quantity,
            type=order_type,
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
                        success = client.set_sl_tp(symbol, pos_side, tp=tp_price, sl=sl_price, quantity=quantity)
                        if success:
                            info(f"✅ SL/TP set for {symbol} (TP: {tp_price}, SL: {sl_price})")
                        else:
                            error(f"❌ SL/TP FAILED for {symbol} — позиция открыта БЕЗ защиты!")
                            # Verify and retry once
                            open_orders = client.get_open_orders(symbol)
                            sl_tp_types = {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
                            existing = {o.get("type") for o in open_orders} & sl_tp_types
                            missing = sl_tp_types - existing
                            if missing:
                                warning(f"⚠️ {symbol}: Retry SL/TP — missing: {missing}")
                                retry_tp = tp_price if "TAKE_PROFIT_MARKET" in missing else None
                                retry_sl = sl_price if "STOP_MARKET" in missing else None
                                retry_ok = client.set_sl_tp(symbol, pos_side, tp=retry_tp, sl=retry_sl, quantity=quantity)
                                if retry_ok:
                                    info(f"✅ SL/TP retry succeeded for {symbol}")
                                else:
                                    error(f"❌ SL/TP retry FAILED for {symbol} — ТРЕБУЕТСЯ РУЧНАЯ УСТАНОВКА!")
                    else:
                        warning(f"⚠️ Client does not support set_sl_tp, SL/TP might not be set")
                except Exception as e:
                    error(f"❌ Failed to set SL/TP for new order {order_id}: {e}")

            # Invalidate positions cache so next cycle gets fresh data
            from src.exchanges.bingx_client import BingXClient
            BingXClient.invalidate_positions_cache()

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

def execute_prediction(prediction, all_positions=None):
    """
    Исполняет одно предсказание для одного символа.
    Используется в режиме multiprocessing.
    """
    symbol = prediction["symbol"]

    client = get_exchange_client()
    if not client.check_prerequisites():
         error(f"❌ {symbol}: Ошибка подключения к бирже")
         return

    # Получаем все текущие позиции для проверки лимитов и состояния
    try:
        positions = all_positions if all_positions is not None else get_open_positions()
    except Exception as e:
        error(f"❌ {symbol}: Ошибка получения позиций: {e}")
        return

    # Проверяем лимит позиций (максимум 5)
    MAX_POSITIONS = POSITION_LIMITS.get("max_positions", 5)
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
            # Position limit is per-symbol (max 1 per symbol)
            # has_position already ensures we don't open duplicate

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
                    confidence=prediction["confidence"],
                    size_pct=prediction.get("size_pct")
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
                        success = client.set_sl_tp(symbol, pos_side, tp=ai_tp, sl=ai_sl)
                        if success:
                            info(f"✅ {symbol}: SL/TP updated (SL: {ai_sl}, TP: {ai_tp})")
                        else:
                            error(f"❌ {symbol}: SL/TP update FAILED — позиция БЕЗ актуальной защиты!")
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
    MAX_POSITIONS = POSITION_LIMITS.get("max_positions", 5)
    total_positions = sum(len(p) for p in positions.values())

    # Determine confidence threshold
    confidence_threshold = MIN_CONFIDENCE_THRESHOLD
    if AGGRESSIVE_MODE:
        confidence_threshold = AGGRESSIVE_SETTINGS.get("MIN_CONFIDENCE", MIN_CONFIDENCE_THRESHOLD)
        info(f"🔥 Aggressive Mode Active: Using Confidence Threshold {confidence_threshold}")

    if total_positions >= MAX_POSITIONS:
        warning(f"⚠️ Достигнут лимит открытых позиций ({MAX_POSITIONS}). Новые позиции не открываем.")

    for pred in predictions:
        symbol = pred["symbol"]
        current_price = pred["current_price"]

        # Проверяем, есть ли уже открытая позиция по данному символу (максимум 1 на актив)
        has_position = symbol in positions and len(positions[symbol]) > 0

        # Проверяем общий лимит позиций — пропускаем только символы БЕЗ позиции
        if total_positions >= MAX_POSITIONS and not has_position:
            warning(f"⚠️ Достигнут лимит позиций ({MAX_POSITIONS}). Пропускаем {symbol}")
            continue

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
                result = create_order(symbol, "BUY", current_price, ai_sl=pred.get("stop_loss"), ai_tp=pred.get("take_profit"), reason=pred['reason'], confidence=pred['confidence'], size_pct=pred.get("size_pct"))
                # Обновляем локальный список позиций и кэш после успешного создания
                if result:
                    positions = get_open_positions()
                    # update_cache_with_working_order_ids(positions)
                    total_positions = sum(len(p) for p in positions.values())
            elif pred["action"] == "sell":
                info(f"📉 {symbol}: сигнал SELL (confidence={pred['confidence']}, причина: {pred['reason']})")
                result = create_order(symbol, "SELL", current_price, ai_sl=pred.get("stop_loss"), ai_tp=pred.get("take_profit"), reason=pred['reason'], confidence=pred['confidence'], size_pct=pred.get("size_pct"))
                # Обновляем локальный список позиций и кэш после успешного создания
                if result:
                    positions = get_open_positions()
                    # update_cache_with_working_order_ids(positions)
                    total_positions = sum(len(p) for p in positions.values())
            else:
                info(f"🔄 {symbol}: действие {pred['action']} не требует открытия позиции")

if __name__ == "__main__":
    import sys, json, predict, analyzer  # noqa: E402

    info("🔄 Запуск исполнения ордеров...")

    # Если запускается через пайплайн
    if not sys.stdin.isatty():
        predictions = json.load(sys.stdin)
    else:
        analyses = analyzer.main()
        predictions = predict.main(analyses)

    main(predictions)
