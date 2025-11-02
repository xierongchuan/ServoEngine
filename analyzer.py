import json
import os
from config import SYMBOLS, DATA_DIR
from logger import info, error
from symbols import get_filename

def calculate_indicators(prices):
    """Рассчитывает ключевые индикаторы"""
    # Валидация структуры данных
    if not prices:
        raise ValueError("Нет данных о ценах")

    try:
        closes = [float(candle["closePrice"]["bid"]) for candle in prices]
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Некорректная структура данных о ценах: {str(e)}")

    # SMA(20)
    if len(closes) >= 20:
        sma = sum(closes[-20:]) / 20
    else:
        sma = sum(closes) / len(closes)

    # RSI(14) - используем все доступные данные для расчета
    if len(closes) < 2:
        # Недостаточно данных для RSI
        return round(sma, 5), 50.0

    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]

    # Используем среднее по всем доступным данным
    avg_gain = sum(gains) / len(deltas) if deltas else 0
    avg_loss = sum(losses) / len(deltas) if deltas else 0

    # Предотвращаем деление на ноль
    if avg_loss == 0:
        if avg_gain == 0:
            rsi = 50.0  # Нейтральное значение если нет движения
        else:
            rsi = 100.0  # Максимальное значение если только рост
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    return round(sma, 5), round(rsi, 2)

def get_news_sentiment(news):
    """Анализирует тональность новостей"""
    sentiment = sum(item["sentiment"] for item in news) / len(news)
    return round(sentiment, 2)

def analyze_symbol(symbol):
    """Анализирует один символ и готовит промпт для DeepSeek"""
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)

    with open(f"{DATA_DIR}/news/{get_filename(symbol)}.json") as f:
        news = json.load(f)
    
    # Рассчитываем индикаторы
    sma, rsi = calculate_indicators(prices)
    sentiment = get_news_sentiment(news)
    
    # Формируем ключевые слова из новостей
    positive_count = sum(1 for item in news if item["sentiment"] > 0)
    negative_count = len(news) - positive_count
    keywords = [item["title"].split()[0] for item in news[:3]]
    
    # Текущая цена
    current_price = float(prices[-1]["closePrice"]["bid"])
    
    # Формируем промпт
    prompt = f"""
    Ты — профессиональный трейдер с 10-летним опытом. Проанализируй {symbol} строго по этим правилам:

    ### ДАННЫЕ (последние 50 свечей 5-минутного таймфрейма):
    - Текущая цена: {current_price:.5f}
    - SMA(20): {sma:.5f} | Тренд: {'восходящий' if current_price > sma else 'нисходящий'}
    - RSI(14): {rsi:.2f} | Состояние: {'перекупленность' if rsi > 70 else 'перепроданность' if rsi < 30 else 'нейтрально'}
    - Новости: {positive_count} позитивных, {negative_count} негативных (ключевые: {', '.join(keywords)})

    ### ПРАВИЛА АНАЛИЗА:
    1. Если RSI > 70 И новостной фон негативный → сильный сигнал SELL
    2. Если RSI < 30 И новостной фон позитивный → сильный сигнал BUY
    3. Если цена выше SMA(20) И 3+ позитивных новости → умеренный сигнал BUY
    4. Если цена ниже SMA(20) И 3+ негативных новости → умеренный сигнал SELL
    5. При конфликте индикаторов → приоритет у новостей

    ### ТРЕБОВАНИЯ К ОТВЕТУ:
    - Действие ТОЛЬКО: buy/sell/close/hold
    - Confidence: 0.0-1.0 (0.8+ = сильный сигнал)
    - Время удержания: 15/30/60 минут
    - Обязательно укажи причину из правил выше

    Верни ТОЛЬКО валидный JSON без пояснений:
    {{"action": "значение", "confidence": число, "hold_minutes": число, "reason": "причина"}}
    """
    
    return {
        "symbol": symbol,
        "current_price": current_price,
        "sma": sma,
        "rsi": rsi,
        "sentiment": sentiment,
        "prompt": prompt.strip()
    }

def main():
    """Основная функция анализа"""
    results = []
    for symbol in SYMBOLS:
        try:
            results.append(analyze_symbol(symbol))
            info(f"🔍 Анализ {symbol} завершен")
        except Exception as e:
            error(f"❌ Ошибка анализа {symbol}: {str(e)}")
    return results

if __name__ == "__main__":
    import json
    print(json.dumps(main(), indent=2))