#!/usr/bin/env python3
import time
import json
from datetime import datetime
from config import *
from utils import init_api_session, make_request, get_headers
import executor
from logger import info, error, warning, log_trade

def close_position(deal_id):
    """Закрывает позицию через API"""
    # Сессия уже инициализирована в get_open_positions(), нет необходимости инициализировать снова
    url = f"{API_BASE}positions/{deal_id}"
    headers = get_headers()

    try:
        response = make_request(url, method="delete", headers=headers)
        if response is None:
            error(f"❌ Не удалось закрыть позицию {deal_id} - пустой ответ от сервера")
            return False

        response.raise_for_status()

        # Логируем закрытие в trades.log
        log_trade(f"✅ Позиция {deal_id} закрыта")

        info(f"✅ Позиция {deal_id} закрыта")
        return True
    except Exception as e:
        # Логируем ошибку в trades.log
        log_trade(f"❌ Ошибка закрытия позиции: {str(e)}", level='ERROR')

        error(f"❌ Ошибка закрытия позиции: {str(e)}")
        return False

def main():
    """Основная функция мониторинга"""
    info("\n👀 Запуск мониторинга открытых позиций...")
    positions = executor.get_open_positions()

    if not positions:
        info("📭 Нет открытых позиций для мониторинга")
        return

    info(f"📊 Отслеживаем позиции: {list(positions.keys())}")

    # Для каждой открытой позиции проверяем условия закрытия
    for symbol, position in positions.items():
        # Проверяем время удержания позиции
        try:
            created_time = position.get("created", "")
            if created_time:
                # Парсим время создания позиции
                created_date = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                now = datetime.now(created_date.tzinfo) if created_date.tzinfo else datetime.now()
                minutes_held = (now - created_date).total_seconds() / 60

                # Закрываем позицию если она открыта дольше 60 минут
                # (Capital.com должен автоматически закрывать по TP/SL, но страхуемся)
                if minutes_held > 60:
                    info(f"⏰ {symbol}: закрываем позицию, открыта {int(minutes_held)} минут")
                    log_trade(f"⏰ {symbol}: автоматическое закрытие позиции (открыта {int(minutes_held)} минут)")
                    close_position(position["dealId"])
                else:
                    info(f"⏳ {symbol}: позиция открыта {int(minutes_held)} мин, ждем до 60 мин")
            else:
                warning(f"⚠️ {symbol}: не удалось определить время открытия позиции")

        except Exception as e:
            warning(f"⚠️ {symbol}: ошибка проверки времени: {str(e)}")

if __name__ == "__main__":
    main()