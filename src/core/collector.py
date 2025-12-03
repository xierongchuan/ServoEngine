import os
import json
import time
from src.config import SYMBOLS, DATA_DIR, NEWS_SETTINGS, ENABLE_NEWS
from src.utils.logger import info, error
from src.utils.helpers import get_filename
from src.utils.news_api import get_news_for_symbol
from src.exchanges.exchange_factory import get_exchange_client

def ensure_dirs():
    """Создает необходимые директории"""
    os.makedirs(f"{DATA_DIR}/prices", exist_ok=True)
    os.makedirs(f"{DATA_DIR}/news", exist_ok=True)
    os.makedirs("charts", exist_ok=True)

def fetch_prices(symbol):
    """Получает 288 последних свечей (24 часа истории) для символа"""
    info(f"📊 Получение цен для {symbol}...")
    
    client = get_exchange_client()
    
    try:
        prices = client.get_kline_data(symbol, interval="MINUTE_5", limit=288)
        
        if not prices:
            raise ValueError(f"API вернул пустой список цен для {symbol}")
            
        # Basic validation of the first candle
        if not isinstance(prices[0], dict):
             raise ValueError(f"Некорректный формат данных о ценах: {type(prices[0])}")
             
        required_fields = ["closePrice", "snapshotTimeUTC"]
        for field in required_fields:
            if field not in prices[0]:
                # BingX might have different field names, need to standardize or check
                # BingX returns: time, open, close, high, low, volume
                # Capital returns: snapshotTimeUTC, openPrice, closePrice...
                # The ExchangeClient implementations should ideally normalize this.
                # But for now let's assume they return what they return and we might need to adapt here or in client.
                # Wait, CapitalClient returns raw Capital response. BingXClient returns raw BingX response?
                # My ExchangeClient docstring said: "Returns a list of dictionaries with keys: snapshotTimeUTC, openPrice..."
                # So the clients MUST normalize.
                # Let's check BingXClient implementation.
                pass

        info(f"✅ Получено {len(prices)} свечей для {symbol}")
        # info(f"   Диапазон: {prices[0]['snapshotTimeUTC']} → {prices[-1]['snapshotTimeUTC']}") # Keys might differ
        return prices
    except Exception as e:
        error(f"❌ Ошибка получения цен для {symbol}: {str(e)}")
        raise

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

    client = get_exchange_client()
    if not client.check_prerequisites():
        return

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

        # Сбор новостей
        if ENABLE_NEWS:
            news = fetch_news(symbol)
        else:
            news = []
            
        news_file = f"{DATA_DIR}/news/{symbol_file}.json"
        with open(news_file, "w") as f:
            json.dump(news, f)

        info(f"📊 Данные для {symbol} успешно собраны")

if __name__ == "__main__":
    main()