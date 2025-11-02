"""
Модуль для получения реальных новостей из различных API источников
Поддерживает: NewsAPI, Alpha Vantage, Finnhub
"""

import json
import time
import requests
from datetime import datetime, timedelta
from config import NEWSAPI_KEY, ALPHAVANTAGE_KEY, FINNHUB_KEY, NEWS_SETTINGS, SYMBOLS
from logger import info, error, warning

# Проверяем наличие TextBlob
try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False
    warning("⚠️ TextBlob не установлен. Анализ тональности будет упрощенным.")

def analyze_sentiment(text):
    """Анализирует тональность текста"""
    if TEXTBLOB_AVAILABLE:
        try:
            blob = TextBlob(text)
            polarity = blob.sentiment.polarity  # от -1 (негативно) до 1 (позитивно)
            # Нормализуем от -0.5 до 0.5, затем переводим в 0-1
            normalized = (polarity + 1) / 2
            return round(normalized, 2)
        except:
            pass

    # Упрощенный анализ без TextBlob
    positive_words = ["рост", "покупать", "buy", "увеличение", "прибыль", "bullish", "up", "gain", "rise"]
    negative_words = ["падение", "продажа", "sell", "снижение", "убыток", "bearish", "down", "fall", "drop"]

    text_lower = text.lower()
    pos_count = sum(1 for word in positive_words if word in text_lower)
    neg_count = sum(1 for word in negative_words if word in text_lower)

    if pos_count > neg_count:
        return 0.7
    elif neg_count > pos_count:
        return 0.3
    else:
        return 0.5  # нейтрально

def get_news_newsapi(symbol):
    """Получает новости через NewsAPI.org"""
    if not NEWSAPI_KEY:
        raise Exception("Отсутствует NEWSAPI_KEY")

    # Подготавливаем поисковый запрос
    query = symbol.replace("/", " OR ")
    if "USD" in symbol:
        base_currency = symbol.split("/")[0]
        query += f" OR {base_currency} USD"

    # Добавляем связанные термины для криптовалют
    if symbol == "BTC/USD":
        query += " OR bitcoin OR crypto"
    elif symbol == "SOL/USD":
        query += " OR solana OR blockchain"

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": NEWS_SETTINGS["max_news_items"],
        "apiKey": NEWSAPI_KEY
    }

    response = requests.get(url, params=params, timeout=NEWS_SETTINGS["news_timeout_seconds"])
    response.raise_for_status()

    data = response.json()
    news_items = []

    for article in data.get("articles", []):
        title = article.get("title", "")
        description = article.get("description", "") or ""
        published_at = article.get("publishedAt", "")

        # Анализируем тональность заголовка и описания
        sentiment = analyze_sentiment(f"{title}. {description}")

        news_items.append({
            "title": title,
            "description": description,
            "sentiment": sentiment,
            "timestamp": published_at,
            "source": "NewsAPI"
        })

    if not news_items:
        raise Exception(f"NewsAPI не вернул новости для {symbol}")

    return news_items

def get_news_alphavantage(symbol):
    """Получает новости через Alpha Vantage"""
    if not ALPHAVANTAGE_KEY:
        raise Exception("Отсутствует ALPHAVANTAGE_KEY")

    # Подготавливаем тикер для API
    if "/" in symbol:
        ticker = symbol.replace("/", "")
    else:
        ticker = symbol

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "sort": "LATEST",
        "limit": NEWS_SETTINGS["max_news_items"],
        "apikey": ALPHAVANTAGE_KEY
    }

    response = requests.get(url, params=params, timeout=NEWS_SETTINGS["news_timeout_seconds"])
    response.raise_for_status()

    data = response.json()
    news_items = []

    for article in data.get("feed", []):
        title = article.get("title", "")
        summary = article.get("summary", "")
        time_published = article.get("time_published", "")

        # Alpha Vantage возвращает тональность от -1 до 1
        sentiment_score = article.get("overall_sentiment_score", 0)
        # Преобразуем в диапазон 0-1
        sentiment = (sentiment_score + 1) / 2

        # Преобразуем время
        try:
            dt = datetime.strptime(time_published, "%Y%m%dT%H%M%S")
            timestamp = dt.isoformat()
        except:
            timestamp = datetime.now().isoformat()

        news_items.append({
            "title": title,
            "description": summary,
            "sentiment": round(sentiment, 2),
            "timestamp": timestamp,
            "source": "AlphaVantage"
        })

    if not news_items:
        raise Exception(f"Alpha Vantage не вернул новости для {ticker}")

    return news_items

def get_news_finnhub(symbol):
    """Получает новости через Finnhub"""
    if not FINNHUB_KEY:
        raise Exception("Отсутствует FINNHUB_KEY")

    # Подготавливаем тикер
    if "/" in symbol:
        ticker = symbol.replace("/", "")
    else:
        ticker = symbol

    # Для криптовалют используем специальный формат
    if symbol in ["BTC/USD", "SOL/USD"]:
        ticker = f"CRYPTO:{ticker}"

    url = "https://finnhub.io/api/v1/news"
    params = {
        "category": "general",
        "token": FINNHUB_KEY
    }

    response = requests.get(url, params=params, timeout=NEWS_SETTINGS["news_timeout_seconds"])
    response.raise_for_status()

    data = response.json()
    news_items = []

    # Finnhub не поддерживает фильтр по тикеру в бесплатном API
    # Поэтому ищем релевантные новости вручную
    relevant_keywords = []
    if symbol == "BTC/USD":
        relevant_keywords = ["bitcoin", "btc", "crypto"]
    elif symbol == "SOL/USD":
        relevant_keywords = ["solana", "sol", "blockchain"]
    else:
        relevant_keywords = [symbol.replace("/", "").lower()]

    count = 0
    for article in data:
        if count >= NEWS_SETTINGS["max_news_items"]:
            break

        title = article.get("headline", "").lower()
        summary = article.get("summary", "").lower()
        full_text = f"{title} {summary}"

        # Проверяем наличие ключевых слов
        if any(keyword in full_text for keyword in relevant_keywords):
            sentiment = analyze_sentiment(f"{article.get('headline', '')}. {article.get('summary', '')}")

            news_items.append({
                "title": article.get("headline", ""),
                "description": article.get("summary", ""),
                "sentiment": sentiment,
                "timestamp": article.get("datetime", int(time.time())),
                "source": "Finnhub"
            })
            count += 1

    if not news_items:
        raise Exception(f"Finnhub не вернул новости для {symbol}")

    return news_items

def fetch_real_news(symbol):
    """
    Получает реальные новости для символа через настроенный провайдер
    """
    provider = NEWS_SETTINGS["provider"].lower()

    try:
        if provider == "newsapi":
            info(f"📰 Получение новостей для {symbol} через NewsAPI...")
            return get_news_newsapi(symbol)
        elif provider == "alphavantage":
            info(f"📰 Получение новостей для {symbol} через Alpha Vantage...")
            return get_news_alphavantage(symbol)
        elif provider == "finnhub":
            info(f"📰 Получение новостей для {symbol} через Finnhub...")
            return get_news_finnhub(symbol)
        else:
            raise Exception(f"Неподдерживаемый провайдер: {provider}")

    except Exception as e:
        error(f"❌ Ошибка получения новостей через {provider}: {str(e)}")
        raise

def get_news_for_symbol(symbol):
    """
    Главная функция получения новостей
    ТОЛЬКО реальные новости! Демо-новости отключены полностью.
    """
    # Проверяем, нужно ли использовать реальные новости
    if not NEWS_SETTINGS["use_real_news"]:
        error(f"❌ Реальные новости отключены в config.py! Включите use_real_news: True")
        raise Exception(f"❌ Невозможно получить новости для {symbol}: реальные новости отключены")

    # Проверяем наличие API ключей
    provider = NEWS_SETTINGS["provider"].lower()
    has_api_key = False

    if provider == "newsapi" and NEWSAPI_KEY:
        has_api_key = True
    elif provider == "alphavantage" and ALPHAVANTAGE_KEY:
        has_api_key = True
    elif provider == "finnhub" and FINNHUB_KEY:
        has_api_key = True

    if not has_api_key:
        error(f"❌ Отсутствует API ключ для {provider}")
        raise Exception(f"❌ Невозможно получить новости для {symbol}: нет API ключа для {provider}")

    # Получаем реальные новости
    try:
        news = fetch_real_news(symbol)
        if not news:
            error(f"❌ Нет новостей от {provider} для {symbol}")
            raise Exception(f"❌ Невозможно получить новости для {symbol}: пустой результат от {provider}")
        return news
    except Exception as e:
        error(f"❌ Не удалось получить реальные новости: {str(e)}")
        raise

def main():
    """Тестирование получения новостей"""
    info("🧪 Тестирование модуля новостей...")

    for symbol in SYMBOLS:
        info(f"\n📊 Тестируем {symbol}:")
        try:
            news = get_news_for_symbol(symbol)
            info(f"✅ Получено {len(news)} новостей")
            for i, item in enumerate(news[:3], 1):
                info(f"   {i}. {item['title'][:50]}... (sentiment={item['sentiment']})")
        except Exception as e:
            error(f"❌ Ошибка: {str(e)}")

if __name__ == "__main__":
    main()
