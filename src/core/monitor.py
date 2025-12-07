#!/usr/bin/env python3
import time
import json
from datetime import datetime
from src.config import *
from . import executor
from src.utils.logger import info, error, warning, log_trade
from src.exchanges.exchange_factory import get_exchange_client

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

def monitor_symbol(symbol):
    """Мониторит позиции только для одного символа"""

    # Can reuse executor.get_open_positions or client.get_positions directly
    # Since we want consistency, let's reuse executor if possible or just call client
    # but executor imports monitor (cycle risk?), monitor imports executor...
    # Current monitor imports executor.

    try:
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
                created_time = position.get("created", "")
                pnl = position.get("pnl", 0)

                if created_time:
                    try:
                        if isinstance(created_time, (int, float)):
                             # Timestamp in ms
                             created_date = datetime.fromtimestamp(created_time / 1000)
                        else:
                            created_date = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                    except ValueError:
                         warning(f"⚠️ {symbol}: Could not parse date: {created_time}")
                         continue

                    # Timezone naive vs aware fix
                    if created_date.tzinfo:
                         now = datetime.now(created_date.tzinfo)
                    else:
                         now = datetime.now()

                    minutes_held = (now - created_date).total_seconds() / 60

                    info(f"⏳ {symbol} [pos {i+1}]: открыта {int(minutes_held)} мин | PnL: {pnl}")
                else:
                    info(f"ℹ️ {symbol} [pos {i+1}]: PnL: {pnl}")

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
                created_time = position.get("created", "")
                deal_id = str(position.get("dealId", ""))
                pnl = position.get("pnl", 0)

                if created_time:
                    # Парсим время создания позиции
                    try:
                        if isinstance(created_time, (int, float)):
                             # Timestamp in ms
                             created_date = datetime.fromtimestamp(created_time / 1000)
                        else:
                            created_date = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                    except ValueError:
                         warning(f"⚠️ Could not parse date: {created_time}")
                         continue

                    now = datetime.now(created_date.tzinfo) if created_date.tzinfo else datetime.now()
                    minutes_held = (now - created_date).total_seconds() / 60

                    info(f"⏳ {sym} [позиция {i+1}]: открыта {int(minutes_held)} мин | PnL: {pnl}")
                else:
                    info(f"ℹ️ {sym} [позиция {i+1}]: PnL: {pnl}")

            except Exception as e:
                warning(f"⚠️ {sym} [позиция {i+1}]: ошибка мониторинга: {str(e)}")

if __name__ == "__main__":
    main()
