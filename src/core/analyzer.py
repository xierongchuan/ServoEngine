import json
import os
from src.config import DATA_DIR, AI_THRESHOLDS, SYMBOLS, ENABLE_NEWS
from src.utils.logger import info, error
from src.utils.helpers import get_filename

def calculate_indicators(prices):
    """Рассчитывает ключевые индикаторы"""
    # Валидация структуры данных
    if not prices:
        raise ValueError("Нет данных о ценах")

    try:
        # Handle different price formats (Capital.com dict vs BingX float)
        closes = []
        for candle in prices:
            price_data = candle["closePrice"]
            if isinstance(price_data, dict):
                closes.append(float(price_data["bid"]))
            else:
                closes.append(float(price_data))
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

from src.exchanges.exchange_factory import get_exchange_client

def analyze_symbol(symbol, position=None):
    """Анализирует один символ и готовит промпт для DeepSeek"""
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)

    with open(f"{DATA_DIR}/news/{get_filename(symbol)}.json") as f:
        news = json.load(f)

    # Рассчитываем индикаторы
    sma, rsi = calculate_indicators(prices)

    # Текущая цена
    last_close = prices[-1]["closePrice"]
    if isinstance(last_close, dict):
        current_price = float(last_close["bid"])
    else:
        current_price = float(last_close)

    # Определяем тренд
    trend = "UP" if current_price > sma else "DOWN"

    # Формируем сырые новостные данные для анализа ИИ
    news_text = ""
    if ENABLE_NEWS:
        if news:
            for item in news:
                news_text += f"\n- [{item['timestamp']}] {item['title']}\n  {item['description']}\n"
        else:
            news_text = "НОВОСТИ НЕДОСТУПНЫ, НО ФУНКЦИЯ ВКЛЮЧЕНА."

    # Информация о текущей позиции
    position_text = "НЕТ ОТКРЫТОЙ ПОЗИЦИИ"
    if position:
        pnl_emoji = "🟢" if position['pnl'] >= 0 else "🔴"
        position_text = f"""
        ЕСТЬ ОТКРЫТАЯ ПОЗИЦИЯ:
        - Тип: {position['type'].upper()}
        - Вход: {position['entry']}
        - PnL: {position['pnl']} {pnl_emoji}
        - Размер: {position['size']}
        """

    # Определяем стратегию в зависимости от настроек новостей
    if ENABLE_NEWS:
        strategy_text = f"""
    **ЕСЛИ ЕСТЬ ПОЗИЦИЯ:**
    1.  **CLOSE**: Если тренд изменился против позиции ИЛИ достигнута цель по прибыли/убытку.
    2.  **HOLD**: Если тренд сохраняется и нет сигналов на выход.

    **ЕСЛИ НЕТ ПОЗИЦИИ (ВХОД):**
    1.  **BUY**: (Позитивные новости + RSI < 70) **ИЛИ** (Тренд UP + RSI < 45).
    2.  **SELL**: (Негативные новости + RSI > 30) **ИЛИ** (Тренд DOWN + RSI > 55).
    3.  **HOLD**: Противоречивые новости или нейтральный фон, и нет технических сигналов.
        """
        
        news_section = f"""
    ### НОВОСТНОЙ ФОН:
    {news_text.strip()}
        """
    else:
        strategy_text = f"""
    **ЕСЛИ ЕСТЬ ПОЗИЦИЯ:**
    1.  **CLOSE**: Если тренд изменился против позиции ИЛИ достигнута цель по прибыли/убытку.
    2.  **HOLD**: Если тренд сохраняется и нет сигналов на выход.

    **ЕСЛИ НЕТ ПОЗИЦИИ (ВХОД - ЧИСТАЯ ТЕХНИКА):**
    *Работаем по тренду на откатах (Trend Following + Pullback)*
    1.  **BUY**: Тренд UP (Цена > SMA) **И** RSI < 45 (Локальная перепроданность/Откат).
    2.  **SELL**: Тренд DOWN (Цена < SMA) **И** RSI > 55 (Локальная перекупленность/Коррекция).
    3.  **HOLD**: Если условия входа не выполнены.
        """
        news_section = ""

    # Формируем промпт
    prompt = f"""
    Ты — профессиональный алгоритмический трейдер. Твоя задача — принять торговое решение для {symbol} на основе предоставленных данных.

    ### ТЕКУЩАЯ ПОЗИЦИЯ:
    {position_text}

    ### РЫНОЧНЫЕ ДАННЫЕ (5m таймфрейм):
    - Цена: {current_price:.5f}
    - SMA({AI_THRESHOLDS['SMA_PERIOD']}): {sma:.5f}
    - RSI({AI_THRESHOLDS['RSI_PERIOD']}): {rsi:.2f}
    - ТЕКУЩИЙ ТРЕНД: {trend} (Цена {'выше' if trend == 'UP' else 'ниже'} SMA)
    {news_section}
    ### ТОРГОВАЯ СТРАТЕГИЯ:
    {strategy_text}

    ### ТВОЯ ЗАДАЧА:
    1.  Учти наличие открытой позиции.
    2.  Проверь условия входа согласно стратегии.
    3.  Прими решение: buy, sell, close, close_partial, или hold.

    ### ФОРМАТ ОТВЕТА (JSON ONLY):
    {{
        "action": "buy" | "sell" | "close" | "close_partial" | "hold",
        "confidence": 0.0-1.0 (0.8+ для сильных сигналов),
        "percentage": 0.1-1.0 (Только для close_partial, например 0.5 для 50%),
        "hold_minutes": {AI_THRESHOLDS['HOLD_TIMES'][-1]},
        "reason": "Краткое объяснение: Позиция, Тренд, RSI, Новости (если есть)."
    }}
    """
    
    return {
        "symbol": symbol,
        "current_price": current_price,
        "sma": sma,
        "rsi": rsi,
        "has_position": bool(position),
        "prompt": prompt.strip()
    }

def main():
    """Основная функция анализа"""
    results = []
    
    # Получаем открытые позиции
    try:
        client = get_exchange_client()
        positions = client.get_positions()
        info(f"📊 Получено {sum(len(p) for p in positions.values())} открытых позиций")
    except Exception as e:
        error(f"❌ Ошибка получения позиций: {str(e)}")
        positions = {}

    for symbol in SYMBOLS:
        try:
            # Ищем позицию для символа
            # BingX symbols might need mapping if they differ from config SYMBOLS
            # But client should handle normalization or we check carefully
            # For now assume exact match or simple mapping
            
            symbol_positions = positions.get(symbol, [])
            # Take the first position if multiple (simplified)
            current_position = symbol_positions[0] if symbol_positions else None
            
            results.append(analyze_symbol(symbol, position=current_position))
            info(f"🔍 Анализ {symbol} завершен")
        except Exception as e:
            error(f"❌ Ошибка анализа {symbol}: {str(e)}")
    return results

if __name__ == "__main__":
    import json
    print(json.dumps(main(), indent=2))