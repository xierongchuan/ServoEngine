import json
import os
from src.config import DATA_DIR, AI_THRESHOLDS, SYMBOLS, ENABLE_NEWS
from src.utils.logger import info, error
from src.utils.helpers import get_filename


def get_price_value(price_item):
    """Извлекает числовое значение цены из разных форматов"""
    if isinstance(price_item, dict):
        return float(price_item.get("bid", price_item.get("ask", 0)))
    return float(price_item)


def calculate_ema(prices, period):
    """Рассчитывает Exponential Moving Average"""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # Start with SMA

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 5)


def calculate_atr(prices_data, period=14):
    """
    Рассчитывает Average True Range
    :param prices_data: Список свечей с high, low, close
    :param period: Период для расчета ATR
    :return: ATR значение
    """
    if len(prices_data) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(prices_data)):
        high = get_price_value(prices_data[i].get("highPrice", 0))
        low = get_price_value(prices_data[i].get("lowPrice", 0))
        prev_close = get_price_value(prices_data[i-1].get("closePrice", 0))

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # ATR = SMA of True Range
    if len(true_ranges) >= period:
        atr = sum(true_ranges[-period:]) / period
    else:
        atr = sum(true_ranges) / len(true_ranges)

    return round(atr, 5)


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

def analyze_symbol_with_position(symbol):
    """
    Анализирует один символ, самостоятельно получая информацию о текущей позиции.
    Используется в режиме multiprocessing.
    """
    try:
        client = get_exchange_client()
        # Получаем все позиции (к сожалению, API часто не имеет метода get_position(symbol))
        # Но мы можем отфильтровать
        positions = client.get_positions()
        symbol_positions = positions.get(symbol, [])
        current_position = symbol_positions[0] if symbol_positions else None

        return analyze_symbol(symbol, position=current_position)
    except Exception as e:
        error(f"❌ Ошибка получения позиции для {symbol}: {e}")
        # Пробуем анализировать без позиции
        return analyze_symbol(symbol, position=None)

def analyze_symbol(symbol, position=None):
    """
    Анализирует один символ и готовит оптимизированный промпт для AI.
    Стратегия: Momentum Breakout с трендовым фильтром
    Таймфрейм: 3 минуты - несколько часов
    """
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)

    with open(f"{DATA_DIR}/news/{get_filename(symbol)}.json") as f:
        news = json.load(f)

    # === РАСЧЁТ ИНДИКАТОРОВ ===
    sma, rsi = calculate_indicators(prices)

    # Извлекаем числовые цены для расчётов
    close_prices = [get_price_value(p.get("closePrice", 0)) for p in prices]

    # EMA расчёты
    ema9 = calculate_ema(close_prices, 9)
    ema21 = calculate_ema(close_prices, 21)

    # ATR расчёт
    atr = calculate_atr(prices, 14)

    # Текущая цена
    last_close = prices[-1]["closePrice"]
    current_price = get_price_value(last_close)

    # Определяем тренды
    global_trend = "UP" if current_price > sma else "DOWN"
    local_trend = "BULLISH" if ema9 > ema21 else "BEARISH"
    trends_aligned = (global_trend == "UP" and local_trend == "BULLISH") or \
                     (global_trend == "DOWN" and local_trend == "BEARISH")

    # Анализ последних 5 свечей
    last_5_closes = close_prices[-5:] if len(close_prices) >= 5 else close_prices
    if len(last_5_closes) >= 2:
        up_candles = sum(1 for i in range(1, len(last_5_closes)) if last_5_closes[i] > last_5_closes[i-1])
        down_candles = len(last_5_closes) - 1 - up_candles
        if up_candles >= 4:
            last_5_direction = "STRONG UP"
            direction_desc = "4+ зелёных свечей"
        elif up_candles >= 3:
            last_5_direction = "UP"
            direction_desc = "Преимущественно рост"
        elif down_candles >= 4:
            last_5_direction = "STRONG DOWN"
            direction_desc = "4+ красных свечей"
        elif down_candles >= 3:
            last_5_direction = "DOWN"
            direction_desc = "Преимущественно падение"
        else:
            last_5_direction = "MIXED"
            direction_desc = "Боковик/неопределённость"
    else:
        last_5_direction = "N/A"
        direction_desc = "Недостаточно данных"

    # === УРОВНИ ПОДДЕРЖКИ/СОПРОТИВЛЕНИЯ ===
    sr_levels = calculate_support_resistance(close_prices)
    support = sr_levels['supports'][-1] if sr_levels['supports'] else current_price * 0.99
    resistance = sr_levels['resistances'][0] if sr_levels['resistances'] else current_price * 1.01

    # Pivot Point (классический)
    if len(prices) >= 2:
        prev_high = get_price_value(prices[-2].get("highPrice", current_price))
        prev_low = get_price_value(prices[-2].get("lowPrice", current_price))
        prev_close = get_price_value(prices[-2].get("closePrice", current_price))
        pivot = (prev_high + prev_low + prev_close) / 3
    else:
        pivot = current_price

    # Расстояния до уровней
    resistance_dist_pct = ((resistance - current_price) / current_price * 100) if current_price > 0 else 0
    support_dist_pct = ((current_price - support) / current_price * 100) if current_price > 0 else 0
    pivot_dist_pct = ((current_price - pivot) / current_price * 100) if current_price > 0 else 0

    # === ОБЪЁМ И ВОЛАТИЛЬНОСТЬ ===
    volumes = [float(p.get('volume', 0)) for p in prices]
    if len(volumes) >= 20:
        avg_volume = sum(volumes[-20:]) / 20
        current_volume = volumes[-1] if volumes else 0
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
    else:
        volume_ratio = 1.0

    if volume_ratio > 2.0:
        volume_status = "🔥 АНОМАЛЬНО ВЫСОКИЙ"
    elif volume_ratio > 1.2:
        volume_status = "📈 Повышенный"
    elif volume_ratio < 0.5:
        volume_status = "💤 Низкий"
    else:
        volume_status = "✅ Норма"

    # Волатильность относительно ATR
    if len(close_prices) >= 2:
        current_move = abs(close_prices[-1] - close_prices[-2])
        volatility_ratio = current_move / atr if atr > 0 else 1.0
    else:
        volatility_ratio = 1.0

    if volatility_ratio > 2.0:
        volatility_status = "⚡ ВЫСОКАЯ"
    elif volatility_ratio < 0.5:
        volatility_status = "🐌 Низкая (сжатие)"
    else:
        volatility_status = "✅ Норма"

    # === RSI ИНТЕРПРЕТАЦИЯ ===
    if rsi >= 80:
        rsi_interpretation = "🔴 КРИТИЧЕСКИ ПЕРЕКУПЛЕН"
    elif rsi >= 70:
        rsi_interpretation = "🟠 Перекуплен"
    elif rsi >= 60:
        rsi_interpretation = "🟡 Умеренно высокий"
    elif rsi >= 40:
        rsi_interpretation = "🟢 Нейтральная зона"
    elif rsi >= 30:
        rsi_interpretation = "🟡 Умеренно низкий"
    elif rsi >= 20:
        rsi_interpretation = "🟠 Перепродан"
    else:
        rsi_interpretation = "🔴 КРИТИЧЕСКИ ПЕРЕПРОДАН"

    # === ПОЗИЦИЯ ===
    from src.config import TRADING_FEE, MIN_PARTIAL_CLOSE_PNL, LEVERAGE, EXCHANGE

    position_block = "**Статус:** НЕТ ОТКРЫТОЙ ПОЗИЦИИ"
    pnl_context = ""

    if position:
        pnl_usdt = float(position['pnl'])
        size_coin = float(position['size'])
        entry_price = float(position['entry'])
        pos_type = position['type'].upper()

        # Расчёт PnL метрик
        position_value = size_coin * entry_price
        margin = position_value / LEVERAGE if LEVERAGE > 0 else position_value
        fee_rate = TRADING_FEE / 100.0
        total_fee = position_value * fee_rate * 2.0
        net_pnl = pnl_usdt - total_fee
        roe_percent = (pnl_usdt / margin * 100) if margin > 0 else 0

        pnl_emoji = "🟢" if pnl_usdt >= 0 else "🔴"

        position_block = f"""**Статус:** ЕСТЬ ОТКРЫТАЯ ПОЗИЦИЯ
| Параметр | Значение |
|----------|----------|
| Тип | {pos_type} |
| Цена входа | {entry_price:.2f} |
| Размер | {size_coin} |
| PnL (USDT) | {pnl_usdt:.2f} {pnl_emoji} |
| ROE | {roe_percent:.2f}% |
| Комиссия (est) | ~{total_fee:.2f} USDT |
| Чистый PnL | ~{net_pnl:.2f} USDT |"""

        if pnl_usdt > 0 and roe_percent < MIN_PARTIAL_CLOSE_PNL * 2:
            pnl_context = f"""
> ⚠️ **LOW PROFIT WARNING**: Чистый PnL слишком мал для partial close.
> Рекомендация: HOLD для роста или CLOSE если тренд сломан."""

    # === КОНФИГУРАЦИЯ СТРАТЕГИИ ===
    from src.config import AGGRESSIVE_MODE, AGGRESSIVE_SETTINGS

    if AGGRESSIVE_MODE:
        rsi_long_max = AGGRESSIVE_SETTINGS.get("RSI_BUY_COND", 65)
        rsi_long_forbidden = AGGRESSIVE_SETTINGS.get("RSI_BUY_FORBIDDEN", 80)
        rsi_short_min = AGGRESSIVE_SETTINGS.get("RSI_SELL_COND", 35)
        rsi_short_forbidden = AGGRESSIVE_SETTINGS.get("RSI_SELL_FORBIDDEN", 20)
        min_confidence = AGGRESSIVE_SETTINGS.get("MIN_CONFIDENCE", 0.6)
        min_rr = AGGRESSIVE_SETTINGS.get("MIN_RISK_REWARD_RATIO", 1.3)
        strategy_mode = "AGGRESSIVE"
    else:
        rsi_long_max = AI_THRESHOLDS.get('RSI_BUY_ENTRY_MAX', 65)
        rsi_long_forbidden = AI_THRESHOLDS.get('RSI_OVERBOUGHT', 70)
        rsi_short_min = AI_THRESHOLDS.get('RSI_SELL_ENTRY_MIN', 35)
        rsi_short_forbidden = AI_THRESHOLDS.get('RSI_OVERSOLD', 30)
        min_confidence = 0.7
        min_rr = 1.5
        strategy_mode = "BALANCED"

    min_profit_breakeven = max(0.2, TRADING_FEE * 2.5)
    min_profit_partial = max(0.5, TRADING_FEE * 4.0)

    # === HOLD MINUTES ESTIMATION ===
    from src.config import CHART_RANGES, DEFAULT_CHART_RANGE, SMART_SAMPLING, MOMENTUM_STRATEGY

    current_interval = "1m"
    if DEFAULT_CHART_RANGE in CHART_RANGES:
        current_interval = CHART_RANGES[DEFAULT_CHART_RANGE].get("interval", "1m")

    # Парсинг интервала в минуты
    interval_minutes = 1
    if current_interval.endswith("m"):
        interval_minutes = int(current_interval[:-1])
    elif current_interval.endswith("h"):
        interval_minutes = int(current_interval[:-1]) * 60

    # Средний ход за свечу
    if len(close_prices) >= 10:
        moves = [abs(close_prices[i] - close_prices[i-1]) for i in range(-9, 0)]
        avg_move_per_candle = sum(moves) / len(moves) if moves else atr
    else:
        avg_move_per_candle = atr

    # Расчёт hold_minutes
    distance_to_tp_pct = max(resistance_dist_pct, support_dist_pct)
    if avg_move_per_candle > 0 and current_price > 0:
        distance_to_tp_abs = (distance_to_tp_pct / 100) * current_price
        est_candles = distance_to_tp_abs / avg_move_per_candle if avg_move_per_candle > 0 else 30
        hold_minutes_est = int(est_candles * interval_minutes * 1.2)
    else:
        hold_minutes_est = 30

    # Используем настройки из конфига
    min_hold = MOMENTUM_STRATEGY.get("min_hold_minutes", 3)
    max_hold = MOMENTUM_STRATEGY.get("max_hold_minutes", 480)
    atr_sl_mult = MOMENTUM_STRATEGY.get("atr_sl_multiplier", 1.5)
    atr_tp_mult = MOMENTUM_STRATEGY.get("atr_tp_multiplier", 2.5)
    max_candles = MOMENTUM_STRATEGY.get("max_candles_in_prompt", 50)
    min_vol_ratio = MOMENTUM_STRATEGY.get("min_volume_ratio", 0.7)
    trend_consensus_req = MOMENTUM_STRATEGY.get("trend_consensus_required", False)
    momentum_entry = MOMENTUM_STRATEGY.get("momentum_entry_enabled", True)
    momentum_candles = MOMENTUM_STRATEGY.get("momentum_consecutive_candles", 3)
    hold_minutes_est = max(min_hold, min(max_hold, hold_minutes_est))

    # === ИСТОРИЯ СВЕЧЕЙ (Smart Sampling) ===
    context_limit = 500
    if DEFAULT_CHART_RANGE in CHART_RANGES:
        context_limit = CHART_RANGES[DEFAULT_CHART_RANGE].get("ai_context_candles", 500)

    if SMART_SAMPLING.get("enabled", True):
        recent_count = SMART_SAMPLING.get("recent_candles", 30)
        step = SMART_SAMPLING.get("history_step", 10)

        full_context_prices = prices[-context_limit:]

        if len(full_context_prices) > recent_count:
            recent_part = full_context_prices[-recent_count:]
            history_part = full_context_prices[:-recent_count]

            sampled_history = []
            for i in range(0, len(history_part), step):
                chunk = history_part[i:i+step]
                if not chunk:
                    continue

                agg_open = get_price_value(chunk[0].get("openPrice", 0))
                agg_close = get_price_value(chunk[-1].get("closePrice", 0))
                agg_high = max(get_price_value(c.get("highPrice", 0)) for c in chunk)
                agg_low = min(get_price_value(c.get("lowPrice", 0)) for c in chunk)
                agg_vol = sum(float(c.get("volume", 0)) for c in chunk)

                sampled_history.append({
                    "snapshotTimeUTC": chunk[0].get("snapshotTimeUTC"),
                    "openPrice": agg_open,
                    "highPrice": agg_high,
                    "lowPrice": agg_low,
                    "closePrice": agg_close,
                    "volume": agg_vol
                })

            final_prices = sampled_history + recent_part
        else:
            final_prices = full_context_prices
    else:
        final_prices = prices[-context_limit:]

    # Формируем таблицу свечей
    candle_lines = []
    for p in final_prices:  # Все свечи после smart sampling
        ts = p.get("snapshotTimeUTC", "")[-8:] if p.get("snapshotTimeUTC") else ""
        o = get_price_value(p.get("openPrice", 0))
        h = get_price_value(p.get("highPrice", 0))
        l = get_price_value(p.get("lowPrice", 0))
        c = get_price_value(p.get("closePrice", 0))
        v = float(p.get("volume", 0))
        body = "🟢" if c > o else "🔴" if c < o else "⚪"
        candle_lines.append(f"{ts} | {o:.2f} | {h:.2f} | {l:.2f} | {c:.2f} | {v:.1f} | {body}")

    candle_history = "\n".join(candle_lines)

    # === НОВОСТИ (если включены) ===
    news_section = ""
    if ENABLE_NEWS and news:
        news_items = []
        for item in news[:5]:  # Максимум 5 новостей
            news_items.append(f"- [{item.get('timestamp', 'N/A')}] {item.get('title', 'N/A')}")
        news_section = f"""
---

## НОВОСТНОЙ ФОН (для контекста)
{chr(10).join(news_items)}

**ВАЖНО**: Новости — вторичный фактор. Технический анализ имеет приоритет.
"""

    # === ФОРМИРУЕМ ОПТИМИЗИРОВАННЫЙ ПРОМПТ ===
    prompt = f"""## РОЛЬ И ЗАДАЧА
Ты — профессиональный алгоритм HFT-торговли (High Frequency Trading), специализирующийся на волатильных крипто-рынках.
Твоя цель: Максимизация профита через захват сильных импульсов (Momentum) при разумном контроле рисков.
Твой стиль: Активный, решительный, точный. Избегай чрезмерной осторожности ("passive trading"), если рынок дает явный сигнал.


**ТВОИ ПРИНЦИПЫ:**
1.  **Trend is King:** Не торгуй против сильного импульса.
2.  **Let Winners Run:** НИКОГДА не закрывай прибыльную сделку рано, если тренд не сломан. Игнорируй RSI > 70/80 в сильном тренде.
3.  **Trailing Stop:** Вместо тейк-профита старайся двигать Stop Loss вслед за ценой.
4.  **Speed:** Входи в сделку немедленно при подтверждении условий.
5.  **No Hallucinations:** Опирайся ТОЛЬКО на предоставленные цифры.

---

## 1. ТОРГОВЫЙ КОНТЕКСТ
| Параметр | Значение | Детали |
|----------|----------|--------|
| Пары | {symbol} | Биржа: {EXCHANGE.upper()} |
| Таймфрейм | {current_interval} | Леверидж: {LEVERAGE}x |
| Режим | **{strategy_mode}** | {"🔥 АГРЕССИВНЫЙ" if strategy_mode == "AGGRESSIVE" else "🛡️ СБАЛАНСИРОВАННЫЙ"} |

{position_block}
{pnl_context}

---

## 2. АНАЛИЗ РЫНКА (DATA-DRIVEN)

### A. Ценовая структура
| Метрика | Значение | Интерпретация алгоритма |
|---------|----------|-------------------------|
| Цена | {current_price:.2f} | Актуальная рыночная цена |
| Тренд (Global) | {global_trend} | Цена относительно SMA({AI_THRESHOLDS.get('SMA_PERIOD', 20)}) |
| Тренд (Local) | {local_trend} | EMA(9) vs EMA(21) |
| RSI(14) | {rsi:.1f} | {rsi_interpretation} |
| ATR(14) | {atr:.2f} | Текущая волатильность |

### B. Сила импульса (Momentum)
| Индикатор | Статус | Значение |
|-----------|--------|----------|
| Volume | {volume_status} | {volume_ratio:.2f}x от среднего |
| Volatility | {volatility_status} | {volatility_ratio:.2f}x от ATR |
| Candle Pattern | {last_5_direction} | {direction_desc} |
| Consensus | {"✅ ПОДТВЕРЖДЕН" if trends_aligned else "⚠️ РАСХОЖДЕНИЕ"} | {"Сильный сигнал" if trends_aligned else "Повышенный риск"} |

### C. Ключевые уровни
- **Сопротивление:** {resistance:.2f} (+{resistance_dist_pct:.2f}%)
- **Поддержка:** {support:.2f} (-{support_dist_pct:.2f}%)
- **Pivot Point:** {pivot:.2f} ({pivot_dist_pct:+.2f}%)

---

## 3. ВЫБОР СТРАТЕГИИ (Decision Matrix)

Ты должен выбрать ОДНУ из двух стратегий в зависимости от рынка:

### � STRATEGY A: MOMENTUM BREAKOUT (Пробой импульса)
*Используй, когда рынок летит на объемах.*
**Условия входа (LONG):**
1.  **Trend:** Сильный аптренд.
2.  **Setup:** Пробой сопротивления или обновление хая.
3.  **Volume:** 🔼 РАСТЁТ (> 1.2x). Это топливо пробоя.
4.  **RSI:** 50-75 (Может быть высоким).
5.  **Momentum Override:** Если `Momentum Entry` = ВКЛЮЧЕН и есть серия из {momentum_candles} зеленых свечей -> ИГНОРИРУЙ RSI до {rsi_long_forbidden}.

### ⚓ STRATEGY B: EMA PULLBACK (Откат к средней)
*Используй, когда тренд берет паузу (коррекция).*
**Условия входа (LONG):**
1.  **Trend:** Аптренд сохраняется (EMA9 > EMA21), но цена снижается.
2.  **Setup:** Цена касается (или близка) к **EMA9** или **EMA21**.
3.  **Volume:** 🔽 ПАДАЕТ или Низкий (< 1.0x). Это "здоровая" коррекция без паники.
4.  **RSI:** 40-55 (Остыл, но не ушел в медвежью зону < 40).
5.  **Trigger:** Свеча начинает отскакивать от EMA (тень снизу).

---

### SCENARIO: SHORT (Зеркально)
- **Breakout Short:** Пробой поддержки, Высокий объем, RSI низкий (но падает), Momentum Override (серия красных свечей).
- **Pullback Short:** Откат вверх к EMA9/21, Низкий объем, RSI 45-60 (остыл), Отскок вниз от EMA.


---

### D. СПЕЦИАЛЬНЫЕ СИТУАЦИИ (REVERSAL & CORRECTIONS)

#### 1. SHARP DROP & BOUNCE (V-Shape / Отскок)
**Ситуация:** Резкое падение цены (Panic Dump) и RSI < 25 (или < 20).
**Анализ:** Риск "продажи дна" (Selling the bottom).
**Реакция:**
- **НЕ ВХОДИ В SHORT**, если цена уже упала на > 2% за короткое время и RSI экстремально низок.
- Ожидай **ОТСКОК (Bounce)** к EMA9/EMA21.
- Если видишь разворотную свечу (Hammer/Pinbar) на высоком объеме -> Рассмотри **SCALP LONG** (Counter-trend).

#### 2. FALSE BREAKOUT (Fakeout / Ложный пробой)
**Ситуация:** Цена пробила Уровень (Support/Resistance), но свеча закрылась с длинным хвостом обратно за уровень.
**Реакция:**
- Это сигнал **НЕУДАВШЕГОСЯ ПРОБОЯ**.
- Торгуй в ПРОТИВОПОЛОЖНУЮ сторону от пробоя (Reversal).
- Stop Loss: Сразу за "хвостом" (экстремумом) ложного пробоя.

#### 3. CORRECTION vs REVERSAL (Коррекция или Разворот?)
**Ситуация:** Тренд идет вверх, но началась красная серия свечей.
- **Healthy Correction:** Цена плавно опускается к EMA9/EMA21, Объем ПАДАЕТ. -> **HOLD LONG / BUY DIP**.
- **Trend Reversal:** Цена резко пробивает EMA21 вниз на РАСТУЩЕМ объеме. -> **CLOSE LONG / SELL**.

---

### E. УПРАВЛЕНИЕ ПОЗИЦИЕЙ (STRATEGY FOR OPEN POSITIONS)

**ГЛАВНОЕ ПРАВИЛО:** НЕ ЗАКРЫВАЙ СДЕЛКУ ПРЕЖДЕВРЕМЕННО. "Let winners run".

1.  **Low Profit (< 0.5% ROE) / Loss:**
    -   **HOLD**, если тренд сохраняется.
    -   **CLOSE**, только если явный разворот против тебя (см. Breakout/Reversal).
    -   *Не выходи из сделки только потому, что "скучно" или "RSI высок".*

2.  **Medium Profit (5-15% ROE):**
    -   Передвинь **Stop Loss в БЕЗУБЫТОК**.
    -   **HOLD** для дальнейшего роста.

3.  **High Profit (> 15% ROE):**
    -   Используй **TRAILING STOP**: Двигай Stop Loss вслед за ценой (под EMA21 или локальный минимум).
    -   **НЕ ДЕЛАЙ CLOSE**, пока цена не выбьет этот скользящий стоп. Позволь забрать максимум движения.

---

## 4. РИСК-МЕНЕДЖМЕНТ (Расчеты)

**Рекомендуемые параметры сделки (ADAPTIVE ATR):**

| Тип | Stop Loss (SL) | Take Profit (TP) | Логика |
|-----|----------------|------------------|--------|
| **LONG** | ~{max(support - atr*0.5, current_price - atr*atr_sl_mult):.2f} | ~{min(resistance, current_price + atr*atr_tp_mult):.2f} | SL ниже локальной поддержки или ATR |
| **SHORT**| ~{min(resistance + atr*0.5, current_price + atr*atr_sl_mult):.2f} | ~{max(support, current_price - atr*atr_tp_mult):.2f} | SL выше локального хая или ATR |

*Ты можешь корректировать эти уровни, если видишь более сильные технические уровни в `History`.*

---

## 5. ИСТОРИЯ ЦЕН (Context)
```
Time | Open | High | Low | Close | Vol | Pattern
{candle_history}
```
{news_section}
---

## ФОРМАТ ОТВЕТА

**CRITICAL INSTRUCTION:**
Return **ONLY** valid JSON.
DO NOT write "Here is the JSON...".
DO NOT write "Evaluation...".
DO NOT use markdown code blocks (```json ... ```).
JUST THE RAW JSON OBJECT.

{{
    "action": "buy" | "sell" | "close" | "close_partial" | "hold",
    "confidence": float (0.0-1.0, threshold: {min_confidence}),
    "percentage": float (0.3-1.0 for close_partial),
    "stop_loss": float (Price),
    "take_profit": float (Price),
    "hold_minutes": int ({min_hold}-{max_hold}), // Recommended: ~{hold_minutes_est} min
    "reason": "СТРОГО: [SETUP_TYPE] | [KEY_REASON] | [RISK_LEVEL]"
}}


Пример reason:
- "Momentum Breakout | 3 Green Candles + Vol Surge | Risk: Low"
- "Trend Pullback | EMA9 Support Rejection | Risk: Medium"
- "Hold | Choppy Market, No Clear Trend | Risk: High"
"""

    return {
        "symbol": symbol,
        "current_price": current_price,
        "sma": sma,
        "rsi": rsi,
        "ema9": ema9,
        "ema21": ema21,
        "atr": atr,
        "global_trend": global_trend,
        "local_trend": local_trend,
        "has_position": bool(position),
        "position": position,  # <-- Added full position object
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
