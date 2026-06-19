import os
import json
from src.config import DATA_DIR, TAKE_PROFIT_PERCENT, STOP_LOSS_PERCENT, POSITION_LIMITS, POSITION_SIZE_PERCENT, LEVERAGE, MIN_TRADE_AMOUNT_USDT, AGGRESSIVE_MODE, AGGRESSIVE_SETTINGS, MIN_CONFIDENCE_THRESHOLD
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client
from src.exchanges.dto.models import Balance, OrderType, OrderSide, PositionSide

# Файл кэша для хранения данных позиций (если нужно)


def _save_sl_tp(symbol: str, sl: float, tp: float):
    """Сохраняет SL/TP в active_trades.json для отображения на графике."""
    import fcntl
    path = os.path.join(DATA_DIR, "active_trades.json")
    try:
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                data = json.loads(f.read() or "{}")
                if symbol in data:
                    data[symbol]["sl"] = sl
                    data[symbol]["tp"] = tp
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=4, default=str)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        warning(f"⚠️ Failed to save SL/TP to active_trades: {e}")


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
        from src import config as runtime_config

        leverage = runtime_config.LEVERAGE
        price = float(price)

        # Calculate absolute TP/SL prices
        # Calculate absolute TP/SL prices
        # Handle both string and OrderSide enum
        direction_str = direction.value if hasattr(direction, 'value') else direction
        if direction_str.upper() == "BUY":
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
        if isinstance(balance_data, dict):
            info(f"🔍 [DEBUG] balance_data type: dict, keys: {balance_data.keys()}")
        elif isinstance(balance_data, Balance):
            info(f"🔍 [DEBUG] balance_data type: Balance dataclass, total={balance_data.total_balance}, available={balance_data.available_balance}")
        else:
            info(f"🔍 [DEBUG] balance_data type: {type(balance_data)}")

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
        elif isinstance(balance_data, Balance):
            # Обработка dataclass Balance
            total_balance = float(balance_data.total_balance)
            available = float(balance_data.available_balance)
            info(f"🔍 [DEBUG] Balance dataclass: total={total_balance}, available={available}, unrealized_pnl={balance_data.unrealized_pnl}")
            # Если available_balance = 0, но total > 0, используем total_balance (с учётом PnL)
            if available == 0 and total_balance > 0:
                info("⚠️ Available balance is 0, using total_balance for trading")
                total_balance = float(balance_data.total_with_pnl) if balance_data.total_with_pnl > 0 else total_balance

        if total_balance <= 0:
            error("❌ Balance is 0 or could not be retrieved. Cannot calculate position size.")
            return None

        # Calculate trade amount in USDT (use dynamic size if provided)
        effective_size_pct = size_pct if size_pct else POSITION_SIZE_PERCENT
        trade_amount = total_balance * (effective_size_pct / 100.0)
        if size_pct:
            info(f"📐 Dynamic position size: {effective_size_pct:.1f}% (default: {POSITION_SIZE_PERCENT}%)")

        # Reserve fee from margin before sizing (round-trip: entry + exit)
        from src.config import TRADING_FEE_TAKER
        estimated_round_trip_fee = trade_amount * leverage * (TRADING_FEE_TAKER / 100.0) * 2.0
        fee_adjusted_amount = trade_amount - estimated_round_trip_fee
        if fee_adjusted_amount >= MIN_TRADE_AMOUNT_USDT:
            info(f"💰 Fee reserve: ${estimated_round_trip_fee:.2f} (taker={TRADING_FEE_TAKER}% × 2 × {leverage}x) | ${trade_amount:.2f} → ${fee_adjusted_amount:.2f}")
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
        notional_value = trade_amount * leverage
        quantity = notional_value / price

        # Round quantity to appropriate precision (e.g., 4 decimals for crypto)
        # In a real scenario, this should be symbol-specific
        _qty_prec = POSITION_LIMITS.get("quantity_precision", 4)
        quantity = round(quantity, _qty_prec)

        # Enhanced position size logging with leverage info
        info("🧮 Position Size:")
        info(f"   Balance: ${total_balance:.2f} | Margin: {effective_size_pct:.1f}% = ${trade_amount:.2f}")
        info(f"   Leverage: {leverage}x | Notional: ${notional_value:.2f}")
        info(f"   Quantity: {quantity} {symbol.replace('USDT', '')}")

        # Convert order_type string to OrderType enum
        order_type_enum = OrderType.MARKET if order_type == "MARKET" else OrderType.LIMIT

        # Place order WITHOUT SL/TP first
        # We will set SL/TP separately using set_sl_tp to ensure reliability and correct mode handling
        order_id = client.place_order(
            symbol=symbol,
            side=direction,
            price=price,
            quantity=quantity,
            order_type=order_type_enum,
            sl=None,
            tp=None,
            leverage=leverage,
        )

        if order_id:
            # Invalidate positions cache so set_sl_tp fetches actual position size
            client.invalidate_cache("positions")

            # Set SL/TP immediately after order placement
            if tp_price or sl_price:
                info(f"🔄 Setting SL/TP for new order {order_id}...")
                try:
                    # Determine position side (use enum for new client)
                    direction_str = direction.value if hasattr(direction, 'value') else direction
                    pos_side = PositionSide.LONG if direction_str.upper() == "BUY" else PositionSide.SHORT

                    if hasattr(client, "set_sl_tp"):
                        # Don't pass quantity — let set_sl_tp fetch actual position size
                        # from exchange to avoid mismatch with filled quantity
                        success = client.set_sl_tp(symbol, pos_side, tp=tp_price, sl=sl_price)
                        if success:
                            info(f"✅ SL/TP set for {symbol} (TP: {tp_price}, SL: {sl_price})")
                            _save_sl_tp(symbol, sl_price, tp_price)
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
                                BingXClient.invalidate_positions_cache()
                                retry_ok = client.set_sl_tp(symbol, pos_side, tp=retry_tp, sl=retry_sl)
                                if retry_ok:
                                    info(f"✅ SL/TP retry succeeded for {symbol}")
                                    _save_sl_tp(symbol, sl_price, tp_price)
                                else:
                                    error(f"❌ SL/TP retry FAILED for {symbol} — ТРЕБУЕТСЯ РУЧНАЯ УСТАНОВКА!")
                    else:
                        warning("⚠️ Client does not support set_sl_tp, SL/TP might not be set")
                except Exception as e:
                    error(f"❌ Failed to set SL/TP for new order {order_id}: {e}")

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

def execute_prediction(prediction, all_positions=None, owner_id=None, strategy_id=None):
    """
    Исполняет одно предсказание для одного символа.
    Используется в режиме multiprocessing.
    """
    symbol = prediction["symbol"]
    owner_id = owner_id or prediction.get("strategy_instance_id") or prediction.get("strategy") or "legacy"
    strategy_id = strategy_id or prediction.get("strategy") or "UNKNOWN"

    client = get_exchange_client()
    if not client.check_prerequisites():
         error(f"❌ {symbol}: Ошибка подключения к бирже")
         return False

    # Получаем все текущие позиции для проверки лимитов и состояния
    try:
        positions = all_positions if all_positions is not None else get_open_positions()
    except Exception as e:
        error(f"❌ {symbol}: Ошибка получения позиций: {e}")
        return False

    from src.core.position_ownership import get_position_ownership_store
    ownership = get_position_ownership_store()
    ownership.sync_with_positions(positions)
    current_owner = ownership.get_owner(symbol)

    # Проверяем лимит позиций (максимум 5)
    POSITION_LIMITS.get("max_positions", 5)
    sum(len(p) for p in positions.values())

    # Normalize symbol key for positions dict (exchange clients may use denormalized keys like BTCUSDT)
    try:
        denorm_symbol = client.denormalize_symbol(symbol)
    except Exception:
        denorm_symbol = symbol.replace("-", "").replace("/", "")

    # Флаг, есть ли у нас позиция по этому символу
    symbol_positions = positions.get(denorm_symbol, [])
    has_position = len(symbol_positions) > 0
    current_pos = symbol_positions[0] if has_position else None

    if current_owner and current_owner.owner_id != owner_id:
        info(
            f"⏸️ {symbol}: символ занят стратегией {current_owner.owner_id} "
            f"({current_owner.strategy}), сигнал {prediction['action']} от {owner_id} пропущен"
        )
        return False

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
                acquired, existing_owner = ownership.try_acquire(symbol, owner_id, strategy_id)
                if not acquired:
                    info(
                        f"⏸️ {symbol}: вход заблокирован владельцем "
                        f"{existing_owner.owner_id if existing_owner else 'unknown'}"
                    )
                    return False

                direction = prediction["action"].upper()
                # Convert string to OrderSide enum
                side = OrderSide.BUY if direction == "BUY" else OrderSide.SELL
                info(f"🚀 {symbol}: Исполнение сигнала {direction} (confidence={prediction['confidence']})")

                order_id = create_order(
                    symbol,
                    side,
                    prediction["current_price"],
                    ai_sl=prediction.get("stop_loss"),
                    ai_tp=prediction.get("take_profit"),
                    reason=prediction["reason"],
                    confidence=prediction["confidence"],
                    size_pct=prediction.get("size_pct")
                )
                if not order_id:
                    ownership.release_if_owner(symbol, owner_id)
                    return False
                return True
            else:
                info(f"📉 {symbol}: Пропуск сигнала {prediction['action']} (confidence {prediction['confidence']} < {confidence_threshold})")
                return False
        else:
            # HOLD or unknown
            info(f"⏸️ {symbol}: {prediction['action'].upper()} ({prediction['reason']})")
            return True

    # 2. MANAGE EXISTING POSITION
    else:
        if not current_owner:
            acquired, current_owner = ownership.try_acquire(symbol, owner_id, strategy_id)
            if not acquired:
                info(f"⏸️ {symbol}: позиция уже закреплена за {current_owner.owner_id}")
                return False

        # Support both dict and Position dataclass
        if hasattr(current_pos, 'position_id'):
            deal_id = current_pos.position_id
        else:
            deal_id = current_pos.get("dealId", "")

        # CLOSE / PARTIAL CLOSE
        if prediction["action"] in ["close", "close_partial"] and prediction["confidence"] >= confidence_threshold:
            percentage = prediction.get("percentage", 1.0)
            if prediction["action"] == "close":
                percentage = 1.0

            info(f"📉 {symbol}: Сигнал на закрытие {prediction['action']} ({percentage*100}%)")

            if client.close_position(symbol, deal_id, percentage):
                info(f"✅ {symbol}: Позиция {deal_id} закрыта (частично: {percentage})")
                log_trade(f"✅ {symbol}: Позиция {deal_id} закрыта (частично: {percentage*100}%) | Причина: {prediction['reason']}")
                if percentage >= 1.0:
                    ownership.release_if_owner(symbol, owner_id)
                return True
            else:
                error(f"❌ {symbol}: Не удалось закрыть позицию {deal_id}")
                return False

        # UPDATE SL/TP (HOLD)
        elif prediction["action"] == "hold":
            ai_sl = prediction.get("stop_loss")
            ai_tp = prediction.get("take_profit")

            if ai_sl or ai_tp:
                # BingXClient needs positionSide (LONG/SHORT)
                # Support both dict and Position dataclass
                if hasattr(current_pos, 'is_long'):
                    pos_side = "LONG" if current_pos.is_long else "SHORT"
                else:
                    pos_type = current_pos.get("type", "").upper() # BUY/SELL
                    pos_side = "LONG" if pos_type == "BUY" else "SHORT"

                # Check if SL/TP actually changed significantly to avoid spam
                # (Simple check, can be improved)

                info(f"🔄 {symbol}: Проверка обновления SL/TP (SL: {ai_sl}, TP: {ai_tp})")
                try:
                    if hasattr(client, "set_sl_tp"):
                        success = client.set_sl_tp(symbol, pos_side, tp=ai_tp, sl=ai_sl)
                        if success:
                            info(f"✅ {symbol}: SL/TP updated (SL: {ai_sl}, TP: {ai_tp})")
                            return True
                        else:
                            error(f"❌ {symbol}: SL/TP update FAILED — позиция БЕЗ актуальной защиты!")
                            return False
                    else:
                        warning("⚠️ Client does not support set_sl_tp")
                        return False
                except Exception as e:
                    error(f"❌ {symbol}: Ошибка обновления SL/TP: {e}")
                    return False
            else:
                 info(f"⏸️ {symbol}: HOLD (Wait for signal)")
                 return True
        else:
             info(f"⏸️ {symbol}: Игнорируем сигнал {prediction['action']} при открытой позиции")
             return False


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
                # Support both dict and Position dataclass
                if hasattr(p, 'side'):
                    side = "LONG" if p.is_long else "SHORT"
                    size = float(p.size)
                    pnl = float(p.unrealized_pnl)
                else:
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

        # Normalize symbol key for positions dict (exchange clients may use denormalized keys like BTCUSDT)
        denorm = client.denormalize_symbol(symbol) if hasattr(client, "denormalize_symbol") else symbol.replace("-", "").replace("/", "")
        # Проверяем, есть ли уже открытая позиция по данному символу (максимум 1 на актив)
        has_position = denorm in positions and len(positions[denorm]) > 0

        # Проверяем общий лимит позиций — пропускаем только символы БЕЗ позиции
        if total_positions >= MAX_POSITIONS and not has_position:
            warning(f"⚠️ Достигнут лимит позиций ({MAX_POSITIONS}). Пропускаем {symbol}")
            continue

        if has_position:
            # Если позиция есть, проверяем сигналы на выход
            if pred["action"] in ["close", "close_partial"] and pred["confidence"] >= confidence_threshold:
                current_pos = positions[denorm][0] # Берем первую (обычно единственную)
                # Support both dict and Position dataclass
                if hasattr(current_pos, 'position_id'):
                    deal_id = current_pos.position_id
                else:
                    deal_id = current_pos.get("dealId", "")
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
                    current_pos = positions[denorm][0]
                    # Determine position side for set_sl_tp
                    # BingXClient needs positionSide (LONG/SHORT)
                    # Support both dict and Position dataclass
                    if hasattr(current_pos, 'is_long'):
                        pos_side = "LONG" if current_pos.is_long else "SHORT"
                    else:
                        pos_type = current_pos.get("type", "").upper() # BUY/SELL
                        pos_side = "LONG" if pos_type == "BUY" else "SHORT"

                    info(f"🔄 {symbol}: Updating SL/TP for existing position (SL: {ai_sl}, TP: {ai_tp})")
                    try:
                        if hasattr(client, "set_sl_tp"):
                            client.set_sl_tp(symbol, pos_side, tp=ai_tp, sl=ai_sl)
                        else:
                            warning("⚠️ Client does not support set_sl_tp")
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
    import sys
    import json
    import predict
    import analyzer  # noqa: E402

    info("🔄 Запуск исполнения ордеров...")

    # Если запускается через пайплайн
    if not sys.stdin.isatty():
        predictions = json.load(sys.stdin)
    else:
        analyses = analyzer.main()
        predictions = predict.main(analyses)

    main(predictions)
