import os
import json
import time
from config import SYMBOLS, DATA_DIR, API_BASE
from utils import init_api_session, make_request, get_headers
from logger import info, error
from symbols import get_epic, get_filename

def ensure_dirs():
    """Создает необходимые директории"""
    os.makedirs(f"{DATA_DIR}/prices", exist_ok=True)
    os.makedirs(f"{DATA_DIR}/news", exist_ok=True)
    os.makedirs("charts", exist_ok=True)

def fetch_prices(symbol):
    """Получает 50 последних свечей для символа"""
    info(f"📊 Получение цен для {symbol}...")

    # Получаем EPIC код из единого модуля
    epic = get_epic(symbol)
    info(f"   EPIC: {epic}")

    url = f"{API_BASE}prices/{epic}"
    params = {
        "resolution": "MINUTE_5",
        "max": 50
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

    info(f"   ✅ Получено {len(prices)} свечей для {symbol}")
    info(f"   Диапазон: {prices[0]['snapshotTimeUTC']} → {prices[-1]['snapshotTimeUTC']}")

    return prices

def fetch_news(symbol):
    """Получает последние 10 новостей для символа"""
    # В реальной системе здесь будет запрос к News API
    # Для демо просто создаем заглушку с ключевыми словами
    positive_keywords = ["рост", "покупать", "buy", "увеличение", "прибыль"]
    negative_keywords = ["падение", "продажа", "sell", "снижение", "убыток"]
    
    # Генерируем случайные новости с тональностью
    news = []
    for i in range(10):
        if i % 3 == 0:  # Каждая 3-я новость негативная
            keyword = negative_keywords[i % len(negative_keywords)]
            sentiment = -0.7
            title = f"Анализ: {symbol} ожидает {keyword} на рынке"
        else:
            keyword = positive_keywords[i % len(positive_keywords)]
            sentiment = 0.6
            title = f"Эксперты прогнозируют {keyword} для {symbol}"
        
        news.append({
            "title": title,
            "sentiment": sentiment,
            "timestamp": time.time() - i * 300  # Каждая новость на 5 мин старше предыдущей
        })
    
    return news

def main():
    """Основная функция сбора данных"""
    ensure_dirs()
    
    # Инициализируем API-сессию
    init_api_session()
    
    for symbol in SYMBOLS:
        try:
            # Сбор цен
            prices = fetch_prices(symbol)

            # Безопасная запись файла с проверкой
            symbol_file = get_filename(symbol)
            prices_file = f"{DATA_DIR}/prices/{symbol_file}.json"
            with open(prices_file, "w") as f:
                json.dump(prices, f)

            # Сбор новостей
            news = fetch_news(symbol)

            # Безопасная запись файла с проверкой
            news_file = f"{DATA_DIR}/news/{symbol_file}.json"
            with open(news_file, "w") as f:
                json.dump(news, f)

            info(f"📊 Данные для {symbol} успешно собраны")
        except Exception as e:
            error(f"❌ Ошибка сбора данных для {symbol}: {str(e)}")

if __name__ == "__main__":
    main()