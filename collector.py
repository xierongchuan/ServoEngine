import os
import json
import time
from config import SYMBOLS, DATA_DIR, API_BASE, NEWS_SETTINGS
from utils import init_api_session, make_request, get_headers
from logger import info, error
from symbols import get_epic, get_filename
from news_api import get_news_for_symbol

def ensure_dirs():
    """Создает необходимые директории"""
    os.makedirs(f"{DATA_DIR}/prices", exist_ok=True)
    os.makedirs(f"{DATA_DIR}/news", exist_ok=True)
    os.makedirs("charts", exist_ok=True)

def fetch_prices(symbol):
    """Получает 288 последних свечей (24 часа истории) для символа"""
    info(f"📊 Получение цен для {symbol}...")

    # Получаем EPIC код из единого модуля
    epic = get_epic(symbol)
    info(f"   EPIC: {epic}")

    url = f"{API_BASE}prices/{epic}"
    params = {
        "resolution": "MINUTE_5",
        "max": 300  # 24 часа истории (60 свечей/час × 24 часа = 1440, берем 300 для производительности)
    }
    headers = get_headers()
    headers["Version"] = "2"  # Capital.com требует версию API

    info(f"   URL: {url}")
    info(f"   Параметры: {params}")

    response = make_request(url, params=params, headers=headers)
    if response is None:
        raise Exception(f"❌ Не удалось получить данные для {symbol}")

    data = response.json()

    # Валидация структуры ответа
    if "prices" not in data:
        error(f"❌ API вернул некорректные данные для {symbol}: нет поля 'prices'")
        error(f"   Ключи в ответе: {list(data.keys())}")
        raise ValueError(f"API вернул некорректные данные: нет поля 'prices'")

    prices = data["prices"]
    if not prices:
        error(f"❌ API вернул пустой список цен для {symbol}")
        raise ValueError(f"API вернул пустой список цен для {symbol}")

    # Проверяем структуру первой записи
    if not isinstance(prices[0], dict):
        error(f"❌ Некорректный формат данных о ценах для {symbol}")
        error(f"   Тип данных: {type(prices[0])}")
        raise ValueError(f"Некорректный формат данных о ценах")

    # Проверяем наличие обязательных полей
    required_fields = ["closePrice", "snapshotTimeUTC"]
    for field in required_fields:
        if field not in prices[0]:
            error(f"❌ В данных {symbol} отсутствует обязательное поле: {field}")
            error(f"   Доступные поля: {list(prices[0].keys())}")
            raise ValueError(f"В данных отсутствует обязательное поле: {field}")

    info(f"✅ Получено {len(prices)} свечей для {symbol}")
    info(f"   Диапазон: {prices[0]['snapshotTimeUTC']} → {prices[-1]['snapshotTimeUTC']}")

    return prices

def fetch_news(symbol):
    """Получает новости для символа (только реальные!)"""
    # Используем новый модуль для получения новостей
    news = get_news_for_symbol(symbol)
    info(f"✅ Получено {len(news)} новостей для {symbol}")

    # Логируем источник новостей
    if news:
        source = news[0].get("source", "Unknown")
        info(f"📰 Источник: {source}")

    return news

def main():
    """Основная функция сбора данных"""
    ensure_dirs()

    # Инициализируем API-сессию
    init_api_session()

    for symbol in SYMBOLS:
        # Сбор цен (может бросить исключение)
        try:
            prices = fetch_prices(symbol)
            symbol_file = get_filename(symbol)
            prices_file = f"{DATA_DIR}/prices/{symbol_file}.json"
            with open(prices_file, "w") as f:
                json.dump(prices, f)
        except Exception as e:
            error(f"❌ Ошибка получения цен для {symbol}: {str(e)}")
            continue

        # Сбор новостей (ОБЯЗАТЕЛЬНО получить, иначе падаем!)
        news = fetch_news(symbol)
        news_file = f"{DATA_DIR}/news/{symbol_file}.json"
        with open(news_file, "w") as f:
            json.dump(news, f)

        info(f"📊 Данные для {symbol} успешно собраны")

if __name__ == "__main__":
    main()