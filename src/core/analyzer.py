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

def calculate_support_resistance(prices, window=20):
    """
    Определяет уровни поддержки и сопротивления на основе локальных минимумов и максимумов.
    :param prices: Список цен (Close)
    :param window: Окно для поиска локальных экстремумов
    :return: dict с 'supports' и 'resistances'
    """
    if len(prices) < window:
        return {"supports": [], "resistances": []}

    supports = []
    resistances = []

    # Простой алгоритм поиска локальных экстремумов
    for i in range(window, len(prices) - window):
        is_support = True
        is_resistance = True

        for j in range(i - window, i + window + 1):
            if prices[j] < prices[i]:
                is_support = False
            if prices[j] > prices[i]:
                is_resistance = False

        if is_support:
            supports.append(prices[i])
        if is_resistance:
            resistances.append(prices[i])

    # Фильтрация близких уровней (кластеризация) - упрощенно берем уникальные с округлением
    supports = sorted(list(set([round(x, 2) for x in supports])))
    resistances = sorted(list(set([round(x, 2) for x in resistances])))

    # Оставляем только ближайшие к текущей цене (например, 2 снизу и 2 сверху)
    current_price = prices[-1]
    nearest_supports = [s for s in supports if s < current_price][-2:]
    nearest_resistances = [r for r in resistances if r > current_price][:2]

    return {
        "supports": nearest_supports,
        "resistances": nearest_resistances
    }

def analyze_volume_profile(volumes, prices):
    """
    Анализирует профиль объема и волатильности.
    :param volumes: Список объемов
    :param prices: Список цен
    :return: Строка с описанием ситуации
    """
    if len(volumes) < 20:
        return "Недостаточно данных для анализа объема."

    avg_volume = sum(volumes[-20:]) / 20
    current_volume = volumes[-1]

    vol_ratio = current_volume / avg_volume if avg_volume > 0 else 0

    # Волатильность (ATR-like)
    high_low_diffs = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    avg_volatility = sum(high_low_diffs[-20:]) / 20 if high_low_diffs else 0
    current_volatility = abs(prices[-1] - prices[-2]) if len(prices) > 1 else 0

    volatility_ratio = current_volatility / avg_volatility if avg_volatility > 0 else 0

    description = []

    # Анализ объема
    if vol_ratio > 2.0:
        description.append(f"🔥 Аномально высокий объем ({vol_ratio:.1f}x от среднего). Это признак сильного интереса.")
    elif vol_ratio > 1.2:
        description.append(f"📊 Повышенный объем ({vol_ratio:.1f}x).")
    elif vol_ratio < 0.5:
        description.append(f"💤 Низкий объем ({vol_ratio:.1f}x). Рынок спит или выжидает.")
    else:
        description.append("Объем в норме.")

    # Анализ волатильности
    if volatility_ratio > 2.0:
        description.append(f"⚡ Высокая волатильность ({volatility_ratio:.1f}x). Возможна паника или эйфория.")
    elif volatility_ratio < 0.5:
        description.append(f"🐌 Низкая волатильность ({volatility_ratio:.1f}x). Сжатие пружины.")

    return " ".join(description)

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

    # Определяем стратегию в зависимости от настроек новостей и агрессивности
    from src.config import TRADING_FEE, AGGRESSIVE_MODE
    min_profit_breakeven = max(0.2, TRADING_FEE * 2.5)
    min_profit_partial = max(0.5, TRADING_FEE * 4.0)

    # Настройки порогов RSI в зависимости от режима
    if AGGRESSIVE_MODE:
        rsi_buy_cond = 60
        rsi_buy_forbidden = 80
        rsi_sell_cond = 40
        rsi_sell_forbidden = 20
        strategy_title = "АГРЕССИВНАЯ СТРАТЕГИЯ (AGGRESSIVE TREND FOLLOWING)"
        entry_desc = "Ищи возможности для входа по тренду. Допускаются входы при более высоком RSI, если тренд сильный."
    else:
        # В обычном режиме берем значения из конфигурации
        rsi_buy_cond = AI_THRESHOLDS['RSI_NEUTRAL_MAX']
        rsi_buy_forbidden = AI_THRESHOLDS['RSI_OVERBOUGHT']
        rsi_sell_cond = AI_THRESHOLDS['RSI_NEUTRAL_MIN']
        rsi_sell_forbidden = AI_THRESHOLDS['RSI_OVERSOLD']
        strategy_title = "КОНСЕРВАТИВНАЯ СТРАТЕГИЯ (STRICT TREND FOLLOWING)"
        entry_desc = "Входим ТОЛЬКО по тренду на глубоких откатах. Контртренд ЗАПРЕЩЕН."

    if ENABLE_NEWS:
        strategy_text = f"""
    **СТРАТЕГИЯ УПРАВЛЕНИЯ ПОЗИЦИЕЙ (ПРИОРИТЕТ №1):**
    1.  **SECURE PROFIT**: Если PnL > {min_profit_breakeven:.2f}% (комиссия {TRADING_FEE}% покрыта), РАССМОТРИ перенос Stop Loss в БЕЗУБЫТОК.
    2.  **CLOSE_PARTIAL**: Если PnL > {min_profit_partial:.2f}%, но импульс затухает -> ЗАКРОЙ 50% позиции.
    3.  **CLOSE**: Если тренд развернулся против позиции ИЛИ достигнут Take Profit.
    4.  **HOLD**: Если тренд сильный и PnL растет.

    **СТРАТЕГИЯ ВХОДА ({strategy_title}):**
    *{entry_desc}*
    1.  **BUY**: (Позитивные новости + RSI < {rsi_buy_cond}) **И** (Тренд UP + Откат). НЕ ПОКУПАЙ НА ХАЯХ (RSI > {rsi_buy_forbidden})!
    2.  **SELL**: (Негативные новости + RSI > {rsi_sell_cond}) **И** (Тренд DOWN + Отскок). НЕ ПРОДАВАЙ НА ДНЕ (RSI < {rsi_sell_forbidden})!
    3.  **HOLD**: Если нет четкого сигнала.
        """
        news_section = f"""
    ### НОВОСТНОЙ ФОН:
    {news_text.strip()}
        """
    else:
        strategy_text = f"""
    **СТРАТЕГИЯ УПРАВЛЕНИЯ ПОЗИЦИЕЙ (ПРИОРИТЕТ №1):**
    1.  **SECURE PROFIT**: Если PnL > {min_profit_breakeven:.2f}% (комиссия {TRADING_FEE}% покрыта), РАССМОТРИ перенос Stop Loss в БЕЗУБЫТОК.
    2.  **CLOSE_PARTIAL**: Если PnL > {min_profit_partial:.2f}%, но импульс затухает -> ЗАКРОЙ 50% позиции.
    3.  **CLOSE**: Если тренд сломан ИЛИ достигнут Take Profit.
    4.  **HOLD**: Если тренд сохраняется.

    **СТРАТЕГИЯ ВХОДА ({strategy_title}):**
    *{entry_desc}*
    1.  **BUY**:
        - Тренд: UP (Цена > SMA)
        - Условие: RSI < {rsi_buy_cond} (Откат или не перекуплен)
        - ЗАПРЕТ: Не покупай, если RSI > {rsi_buy_forbidden} (Перекупленность).
    2.  **SELL**:
        - Тренд: DOWN (Цена < SMA)
        - Условие: RSI > {rsi_sell_cond} (Коррекция или не перепродан)
        - ЗАПРЕТ: Не продавай, если RSI < {rsi_sell_forbidden} (Перепроданность).
    3.  **HOLD**: Если условия входа не идеальны.
        """
        news_section = ""

    from src.config import ENABLE_ADVANCED_ANALYSIS

    # Calculate advanced metrics if enabled
    advanced_analysis_text = ""
    if ENABLE_ADVANCED_ANALYSIS:
        # Extract numerical data from price dicts
        close_prices = [float(p['closePrice']) for p in prices]
        volumes = [float(p['volume']) for p in prices]

        sr_levels = calculate_support_resistance(close_prices)
        volume_context = analyze_volume_profile(volumes, close_prices)

        advanced_analysis_text = f"""
    ### РЫНОЧНАЯ СТРУКТУРА И ПСИХОЛОГИЯ:
    - Ближайшие Поддержки: {sr_levels['supports']}
    - Ближайшие Сопротивления: {sr_levels['resistances']}
    - Контекст Объема/Волатильности: {volume_context}

    ### ПСИХОЛОГИЧЕСКИЙ АНАЛИЗ:
    1. Оцени, кто контролирует рынок (Быки или Медведи)?
    2. Есть ли признаки "ловушки" для трейдеров или панических продаж?
    3. Учти уровни поддержки/сопротивления при расчете SL/TP.
    4. Соблюдай Risk/Reward Ratio минимум 1:1.5. Тейк-профит должен быть БОЛЬШЕ, чем Стоп-лосс (в % от цены входа). Не ставь огромные стопы ради безопасности.
        """

    # Формируем историю свечей для контекста ИИ
    def get_price_value(price_item):
        if isinstance(price_item, dict):
            return float(price_item["bid"])
        return float(price_item)

    history_lines = ["Timestamp | Open | High | Low | Close | Volume"]

    # Get context limit from config
    from src.config import CHART_RANGES, DEFAULT_CHART_RANGE
    context_limit = 500 # Default fallback
    if DEFAULT_CHART_RANGE in CHART_RANGES:
        context_limit = CHART_RANGES[DEFAULT_CHART_RANGE].get("ai_context_candles", 500)

    recent_prices = prices[-context_limit:]

    for p in recent_prices:
        ts = p.get("snapshotTimeUTC", "").replace("T", " ")
        o = get_price_value(p.get("openPrice", 0))
        h = get_price_value(p.get("highPrice", 0))
        l = get_price_value(p.get("lowPrice", 0))
        c = get_price_value(p.get("closePrice", 0))
        v = p.get("volume", 0)
        history_lines.append(f"{ts} | {o:.5f} | {h:.5f} | {l:.5f} | {c:.5f} | {v:.2f}")

    price_history_text = "\n".join(history_lines)

    # Get current interval
    current_interval = "5m" # Default
    if DEFAULT_CHART_RANGE in CHART_RANGES:
        current_interval = CHART_RANGES[DEFAULT_CHART_RANGE].get("interval", "5m")

    # Формируем промпт
    prompt = f"""
    Ты — профессиональный алгоритмический трейдер. Твоя задача — принять торговое решение для {symbol} на основе предоставленных данных.

    ### ТЕКУЩАЯ ПОЗИЦИЯ:
    {position_text}

    ### ИСТОРИЧЕСКИЕ ДАННЫЕ (CANDLES - Last {len(recent_prices)}):
    {price_history_text}

    ### РЫНОЧНЫЕ ДАННЫЕ ({current_interval} таймфрейм):
    - Цена: {current_price:.5f}
    - SMA({AI_THRESHOLDS['SMA_PERIOD']}): {sma:.5f}
    - RSI({AI_THRESHOLDS['RSI_PERIOD']}): {rsi:.2f}
    - ТЕКУЩИЙ ТРЕНД: {trend} (Цена {'выше' if trend == 'UP' else 'ниже'} SMA)
    {advanced_analysis_text}
    {news_section}
    ### ТОРГОВАЯ СТРАТЕГИЯ:
    {strategy_text}

    ### ТВОЯ ЗАДАЧА:
    1. Учти наличие открытой позиции.
    2. Проверь условия входа согласно стратегии.
    3. Рассчитай уровни Stop Loss и Take Profit на основе технических уровней (поддержка/сопротивление, SMA).
    4. Risk/Reward Ratio:
       - Соблюдай адекватное соотношение риска к прибыли.
       - Сделка должна быть оправдана с точки зрения риск-менеджмента.
       - Не открывай сделки, где риск неоправданно высок по сравнению с потенциальной прибылью.
    5. Прими решение: buy, sell, close, close_partial, или hold.

    ### ФОРМАТ ОТВЕТА (JSON ONLY):
    {{
        "action": "buy" | "sell" | "close" | "close_partial" | "hold",
        "confidence": 0.0-1.0 (0.8+ для сильных сигналов),
        "percentage": 0.1-1.0 (Только для close_partial, например 0.5 для 50%),
        "stop_loss": float (Цена стоп-лосса, ОБЯЗАТЕЛЬНО для buy/sell/hold),
        "take_profit": float (Цена тейк-профита, ОБЯЗАТЕЛЬНО для buy/sell/hold),

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
