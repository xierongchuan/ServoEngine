import json
import os
from src.config import SYMBOLS, DATA_DIR, AI_THRESHOLDS
from src.utils.logger import info, error
from src.utils.symbols import get_filename

def calculate_indicators(prices):
    """Рассчитывает ключевые индикаторы"""
    # Валидация структуры данных
    if not prices:
        raise ValueError("Нет данных о ценах")

    try:
        closes = [float(candle["closePrice"]["bid"]) for candle in prices]
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Некорректная структура данных о ценах: {str(e)}")

    sma_period = AI_THRESHOLDS["SMA_PERIOD"]
    rsi_period = AI_THRESHOLDS["RSI_PERIOD"]

    # SMA (конфигурируемый период)
    if len(closes) >= sma_period:
        sma = sum(closes[-sma_period:]) / sma_period
    else:
        sma = sum(closes) / len(closes)

    # RSI - используем конфигурируемый период
    # RSI рассчитывается как скользящее окно по последним rsi_period значениям
    if len(closes) < rsi_period:
        # Недостаточно данных для RSI
        return round(sma, 5), 50.0

    # Берем последние rsi_period значений для расчета RSI
    recent_closes = closes[-rsi_period:]

    deltas = [recent_closes[i] - recent_closes[i-1] for i in range(1, len(recent_closes))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]

    # Используем среднее по последним rsi_period-1 значениям
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

def analyze_symbol(symbol):
    """Анализирует один символ и готовит промпт для DeepSeek"""
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)

    with open(f"{DATA_DIR}/news/{get_filename(symbol)}.json") as f:
        news = json.load(f)

    # Рассчитываем индикаторы
    sma, rsi = calculate_indicators(prices)

    # Текущая цена
    current_price = float(prices[-1]["closePrice"]["bid"])

    # Формируем сырые новостные данные для анализа ИИ
    news_text = ""
    for item in news:
        news_text += f"\n- [{item['timestamp']}] {item['title']}\n  {item['description']}\n"

    # Формируем промпт с сырыми новостными данными
    prompt = f"""
    Ты — профессиональный трейдер с 10-летним опытом. Проанализируй {symbol} строго по этим правилам:

    ### ДАННЫЕ (последние 300 свечей 5-минутного таймфрейма - 24 часа истории):
    - Текущая цена: {current_price:.5f}
    - SMA({AI_THRESHOLDS['SMA_PERIOD']}): {sma:.5f} | Тренд: {'восходящий' if current_price > sma else 'нисходящий'}
    - RSI({AI_THRESHOLDS['RSI_PERIOD']}): {rsi:.2f} | Состояние: {'перекупленность' if rsi > AI_THRESHOLDS['RSI_OVERBOUGHT'] else 'перепроданность' if rsi < AI_THRESHOLDS['RSI_OVERSOLD'] else 'нейтрально'}

    ### НОВОСТИ (самостоятельно проанализируй тональность каждой новости):
    {news_text.strip()}

    ### ПРАВИЛА АНАЛИЗА:
    1. Если RSI > {AI_THRESHOLDS['RSI_OVERBOUGHT']} И новостной фон негативный → сильный сигнал SELL
    2. Если RSI < {AI_THRESHOLDS['RSI_OVERSOLD']} И новостной фон позитивный → сильный сигнал BUY
    3. Если цена выше SMA({AI_THRESHOLDS['SMA_PERIOD']}) И новостной фон позитивный → умеренный сигнал BUY
    4. Если цена ниже SMA({AI_THRESHOLDS['SMA_PERIOD']}) И новостной фон негативный → умеренный сигнал SELL
    5. При конфликте индикаторов → приоритет у новостей
    6. Актуальные новости (последние 2-4 часа) имеют больший вес

    ### ТРЕБОВАНИЯ К ОТВЕТУ:
    - Действие ТОЛЬКО: buy/sell/close/hold
    - Confidence: 0.0-1.0 ({AI_THRESHOLDS['STRONG_SIGNAL_CONFIDENCE']}+ = сильный сигнал)
    - Время удержания: {', '.join(map(str, AI_THRESHOLDS['HOLD_TIMES']))} минут
    - Обязательно укажи причину из правил выше
    - ВАЖНО: НЕ используй markdown блоки ```json или ```
    - Возвращай ТОЛЬКО чистый JSON без форматирования

    Пример ответа (используй именно этот формат, без ```json):
    {{"action": "значение", "confidence": число, "hold_minutes": число, "reason": "причина"}}
    """
    
    return {
        "symbol": symbol,
        "current_price": current_price,
        "sma": sma,
        "rsi": rsi,
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