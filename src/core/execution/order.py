"""Утилиты для работы с ордерами — создание, SL/TP, получение позиций."""

import os
import json
from typing import Dict, Optional

from src.config import *
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client
from src.exchanges.dto.models import Balance, OrderType, PositionSide


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


def get_open_positions() -> Dict:
    """Получает открытые позиции через ExchangeClient."""
    client = get_exchange_client()
    try:
        positions = client.get_positions()
        return positions
    except Exception as e:
        error(f"❌ Ошибка получения позиций: {str(e)}")
        return {}


def create_order(
    symbol: str,
    direction,
    price: float,
    ai_sl: Optional[float] = None,
    ai_tp: Optional[float] = None,
    reason: str = "Unknown",
    confidence: float = 0.0,
    size_pct: Optional[float] = None,
    order_type: str = "MARKET",
) -> Optional[str]:
    """Создает ордер с TP/SL через ExchangeClient."""
    client = get_exchange_client()

    try:
        price = float(price)

        direction_str = direction.value if hasattr(direction, 'value') else direction
        if direction_str.upper() == "BUY":
            tp_price = price * (1 + TAKE_PROFIT_PERCENT / 100)
            sl_price = price * (1 - STOP_LOSS_PERCENT / 100)
        else:
            tp_price = price * (1 - TAKE_PROFIT_PERCENT / 100)
            sl_price = price * (1 + STOP_LOSS_PERCENT / 100)

        if ai_tp:
            info(f"🤖 Using AI Take Profit: {ai_tp} (Calculated: {tp_price:.4f})")
            tp_price = float(ai_tp)
        if ai_sl:
            info(f"🤖 Using AI Stop Loss: {ai_sl} (Calculated: {sl_price:.4f})")
            sl_price = float(ai_sl)

        _price_prec = POSITION_LIMITS.get("price_precision", 4)
        tp_price = round(tp_price, _price_prec)
        sl_price = round(sl_price, _price_prec)

        info(f"🎯 Calculated TP: {tp_price}, SL: {sl_price}")

        balance_data = client.get_balance()
        total_balance = 0.0

        if isinstance(balance_data, dict):
            for key in ["equity", "availableBalance", "balance", "availableMargin", "marginBalance", "walletBalance", "totalBalance", "totalWalletBalance"]:
                val = balance_data.get(key)
                if val is not None:
                    try:
                        parsed = float(val)
                        if parsed > 0:
                            total_balance = parsed
                            break
                    except (ValueError, TypeError):
                        pass

            if total_balance == 0:
                nested_data = balance_data.get("data", {})
                if isinstance(nested_data, dict):
                    for key in ["equity", "availableBalance", "balance", "availableMargin", "marginBalance", "walletBalance"]:
                        val = nested_data.get(key)
                        if val is not None:
                            try:
                                parsed = float(val)
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
            total_balance = float(balance_data.total_balance)
            available = float(balance_data.available_balance)
            if available == 0 and total_balance > 0:
                total_balance = float(balance_data.total_with_pnl) if balance_data.total_with_pnl > 0 else total_balance

        if total_balance <= 0:
            error("❌ Balance is 0 or could not be retrieved. Cannot calculate position size.")
            return None

        effective_size_pct = size_pct if size_pct else POSITION_SIZE_PERCENT
        trade_amount = total_balance * (effective_size_pct / 100.0)

        from src.config import TRADING_FEE_TAKER
        estimated_round_trip_fee = trade_amount * LEVERAGE * (TRADING_FEE_TAKER / 100.0) * 2.0
        fee_adjusted_amount = trade_amount - estimated_round_trip_fee
        if fee_adjusted_amount >= MIN_TRADE_AMOUNT_USDT:
            info(f"💰 Fee reserve: ${estimated_round_trip_fee:.2f} | ${trade_amount:.2f} → ${fee_adjusted_amount:.2f}")
            trade_amount = fee_adjusted_amount

        if trade_amount < MIN_TRADE_AMOUNT_USDT:
            info(f"⚠️ Calculated amount ${trade_amount:.2f} is less than min ${MIN_TRADE_AMOUNT_USDT}. Using min amount.")
            trade_amount = MIN_TRADE_AMOUNT_USDT

        if trade_amount > total_balance:
            _safety = POSITION_LIMITS.get("balance_safety_margin", 0.95)
            warning(f"⚠️ Trade amount ${trade_amount:.2f} exceeds balance ${total_balance:.2f}. Adjusting to {_safety*100:.0f}% of balance.")
            trade_amount = total_balance * _safety

        notional_value = trade_amount * LEVERAGE
        quantity = notional_value / price
        _qty_prec = POSITION_LIMITS.get("quantity_precision", 4)
        quantity = round(quantity, _qty_prec)

        info("🧮 Position Size:")
        info(f"   Balance: ${total_balance:.2f} | Margin: {effective_size_pct:.1f}% = ${trade_amount:.2f}")
        info(f"   Leverage: {LEVERAGE}x | Notional: ${notional_value:.2f}")
        info(f"   Quantity: {quantity} {symbol.replace('USDT', '')}")

        order_type_enum = OrderType.MARKET if order_type == "MARKET" else OrderType.LIMIT

        order_id = client.place_order(
            symbol=symbol,
            side=direction,
            price=price,
            quantity=quantity,
            order_type=order_type_enum,
            sl=None,
            tp=None
        )

        if order_id:
            client.invalidate_cache("positions")

            if tp_price or sl_price:
                info(f"🔄 Setting SL/TP for new order {order_id}...")
                try:
                    direction_str = direction.value if hasattr(direction, 'value') else direction
                    pos_side = PositionSide.LONG if direction_str.upper() == "BUY" else PositionSide.SHORT

                    if hasattr(client, "set_sl_tp"):
                        success = client.set_sl_tp(symbol, pos_side, tp=tp_price, sl=sl_price)
                        if success:
                            info(f"✅ SL/TP set for {symbol} (TP: {tp_price}, SL: {sl_price})")
                            _save_sl_tp(symbol, sl_price, tp_price)
                        else:
                            error(f"❌ SL/TP FAILED for {symbol} — позиция открыта БЕЗ защиты!")
                            open_orders = client.get_open_orders(symbol)
                            sl_tp_types = {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
                            existing = {o.get("type") for o in open_orders} & sl_tp_types
                            missing = sl_tp_types - existing
                            if missing:
                                warning(f"⚠️ {symbol}: Retry SL/TP — missing: {missing}")
                                retry_tp = tp_price if "TAKE_PROFIT_MARKET" in missing else None
                                retry_sl = sl_price if "STOP_MARKET" in missing else None
                                client.set_sl_tp(symbol, pos_side, tp=retry_tp, sl=retry_sl)
                    else:
                        warning("⚠️ Client does not support set_sl_tp")
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
