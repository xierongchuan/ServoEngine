#!/usr/bin/env python3
from datetime import datetime
from src.config import *
from . import executor
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client
from src.core.signals.utils import PositionAdapter


def _format_pnl_with_fees(pnl, position):
    """Formats PnL string with estimated fee breakdown."""
    from src.config import TRADING_FEE_TAKER
    try:
        adapter = PositionAdapter(position)
        size = adapter.size
        entry = adapter.entry_price
        if size > 0 and entry > 0:
            position_value = size * entry
            round_trip_fee = position_value * (TRADING_FEE_TAKER / 100.0) * 2.0
            net_pnl = pnl - round_trip_fee
            return f"PnL: {pnl:.2f} (net: ~{net_pnl:.2f}, fee: ~{round_trip_fee:.2f})"
    except (ValueError, TypeError):
        pass
    return f"PnL: {pnl}"

def close_position(symbol, deal_id, working_order_id=None):
    """Закрывает позицию через ExchangeClient"""
    client = get_exchange_client()

    try:
        result = client.close_position(symbol, deal_id)

        if result:


            # Логируем закрытие в trades.log
            log_trade(f"✅ Позиция {deal_id} ({symbol}) закрыта")
            info(f"✅ Позиция {deal_id} ({symbol}) закрыта")
            return True
        else:
            error(f"❌ Не удалось закрыть позицию {deal_id} ({symbol})")
            return False

    except Exception as e:
        # Логируем ошибку в trades.log
        log_trade(f"❌ Ошибка закрытия позиции {symbol}: {str(e)}", level='ERROR')
        error(f"❌ Ошибка закрытия позиции {symbol}: {str(e)}")
        return False

def monitor_symbol(symbol, all_positions=None):
    """Мониторит позиции только для одного символа"""

    try:
        if all_positions is None:
            all_positions = executor.get_open_positions()
        if not all_positions:
            # No positions at all
            return

        position_list = all_positions.get(symbol, [])

        if not position_list:
            # No position for this symbol
            return

        # Log details for this symbol
        for i, position in enumerate(position_list):
            try:
                adapter = PositionAdapter(position)
                created_date = adapter.created_at
                pnl = adapter.unrealized_pnl

                if created_date:
                    # Timezone naive vs aware fix
                    if created_date.tzinfo:
                         now = datetime.now(created_date.tzinfo)
                    else:
                         now = datetime.now()

                    minutes_held = (now - created_date).total_seconds() / 60

                    pnl_str = _format_pnl_with_fees(pnl, position)
                    info(f"⏳ {symbol} [pos {i+1}]: открыта {int(minutes_held)} мин | {pnl_str}")
                else:
                    pnl_str = _format_pnl_with_fees(pnl, position)
                    info(f"ℹ️ {symbol} [pos {i+1}]: {pnl_str}")

            except Exception as e:
                warning(f"⚠️ {symbol}: ошибка мониторинга: {str(e)}")

    except Exception as e:
         error(f"❌ Ошибка мониторинга {symbol}: {e}")

def main():
    """Основная функция мониторинга"""
    info("👀 Запуск мониторинга открытых позиций...")

    client = get_exchange_client()
    if not client.check_prerequisites():
        return

    all_positions = executor.get_open_positions()

    if not all_positions:
        info("📭 Нет открытых позиций для мониторинга")
        return

    positions = all_positions

    info(f"📊 Отслеживаем позиции: {list(positions.keys())}")

    # Для каждого символа и его списка позиций
    for sym, position_list in positions.items():
        # Теперь positions[sym] это список позиций
        for i, position in enumerate(position_list):
            try:
                adapter = PositionAdapter(position)
                created_date = adapter.created_at
                pnl = adapter.unrealized_pnl

                if created_date:
                    now = datetime.now(created_date.tzinfo) if created_date.tzinfo else datetime.now()
                    minutes_held = (now - created_date).total_seconds() / 60

                    pnl_str = _format_pnl_with_fees(pnl, position)
                    info(f"⏳ {sym} [позиция {i+1}]: открыта {int(minutes_held)} мин | {pnl_str}")
                else:
                    pnl_str = _format_pnl_with_fees(pnl, position)
                    info(f"ℹ️ {sym} [позиция {i+1}]: {pnl_str}")

            except Exception as e:
                warning(f"⚠️ {sym} [позиция {i+1}]: ошибка мониторинга: {str(e)}")

if __name__ == "__main__":
    main()
