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
        "supports": sorted(list(set(supports))),
        "resistances": sorted(list(set(resistances)))
    }

def calculate_sma_series(values, period):
    """Calculates SMA series for a list of values."""
    if len(values) < period:
        return [0] * len(values)

    sma_values = []
    # Pad finding initial SMA
    pass_len = len(values)

    # Efficient rolling sum
    # First `period` are 0 or partial (we just use 0 padding for alignment simplicity)
    sma_values = [0] * (period - 1)

    # Calculate initial window
    current_sum = sum(values[:period])
    sma_values.append(current_sum / period)

    # Rolling
    for i in range(period, pass_len):
        current_sum = current_sum - values[i-period] + values[i]
        sma_values.append(current_sum / period)

    return sma_values

def calculate_rsi_series(prices, period=14):
    """Calculates RSI series."""
    if len(prices) < period + 1:
        return [50.0] * len(prices)

    rsi_values = [50.0] * len(prices) # Default 50

    gains = []
    losses = []

    # Calculate changes
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    # First Average Gain/Loss
    if len(deltas) < period:
        return rsi_values

    # Initial avg
    seed_gains = [d for d in deltas[:period] if d > 0]
    seed_losses = [-d for d in deltas[:period] if d < 0]

    avg_gain = sum(seed_gains) / period
    avg_loss = sum(seed_losses) / period

    # First RSI at index `period`
    if avg_loss == 0:
        rs = 100
        first_rsi = 100
    else:
        rs = avg_gain / avg_loss
        first_rsi = 100 - (100 / (1 + rs))

    rsi_values[period] = first_rsi

    # Smoothing
    for i in range(period + 1, len(prices)):
        delta = prices[i] - prices[i-1]
        gain = delta if delta > 0 else 0
        loss = -delta if delta < 0 else 0

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        rsi_values[i] = rsi

    return rsi_values

def calculate_seb_series(prices, length=20, mult=2.0):
    """
    Calculates Standard Error Bands (Linear Regression + StdErr) for the END of the series only,
    but returns arrays populated with the LAST regression line values for visualization purposes
    OR calculates rolling SEB if we want TRUE history (expensive).

    For the AI Prompt, user wants to see what the bands were AT THAT TIME?
    Or where the current bands lie?
    Usually -> Band Value AT THAT MOMENT. That requires Rolling Linear Regression.

    Simplification for performance: We will implement Rolling Linear Regression.
    """
    import numpy as np

    n = len(prices)
    linreg_series = [0.0] * n
    upper_series = [0.0] * n
    lower_series = [0.0] * n

    if n < length:
        return linreg_series, upper_series, lower_series

    prices_arr = np.array(prices)
    x = np.arange(length)

    # Rolling calculation
    # Ideally should use efficient algo, but loop is fine for < 1000 candles
    for i in range(length, n + 1):
        # Window: prices_arr[i-length : i]
        y = prices_arr[i-length : i]

        # Linreg for this window. We only need the ENDPOINT value (at x = length-1) to plot the current 'live' value
        # Coeffs
        m, c = np.polyfit(x, y, 1) # polyfit is slightly faster/easier than lstsq for 1D

        # Value at the last point of window
        reg_val = m * (length - 1) + c

        # Std Err
        residuals = y - (m * x + c)
        std_err = np.sqrt(np.sum(residuals**2) / (length - 2))

        linreg_series[i-1] = reg_val
        upper_series[i-1] = reg_val + (mult * std_err)
        lower_series[i-1] = reg_val - (mult * std_err)

    return linreg_series, upper_series, lower_series

def calculate_seb(prices, length=20, mult=2.0):
    """
    Calculates Standard Error Bands (Linear Regression + StdErr) for LAST point only.
    Use calculate_seb_series for full history.
    """
    import numpy as np

    if len(prices) < length:
        return 0, 0, 0, 0

    y = np.array(prices[-length:])
    x = np.arange(length)

    # Linear Regression (Least Squares)
    A = np.vstack([x, np.ones(len(x))]).T
    m, c = np.linalg.lstsq(A, y, rcond=None)[0]

    # Regression Line
    linreg = m * x + c

    # Standard Error
    residuals = y - linreg
    std_err = np.sqrt(np.sum(residuals**2) / (length - 2))

    # Bands
    linreg_current = linreg[-1]
    upper_seb = linreg_current + (mult * std_err)
    lower_seb = linreg_current - (mult * std_err)

    # R-Squared (Trend Quality)
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y - np.mean(y))**2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

    return linreg_current, upper_seb, lower_seb, r_squared

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

    # === STANDARD ERROR BANDS (SEB) ===
    seb_linreg, seb_upper, seb_lower, seb_r_sq = calculate_seb(close_prices)
    seb_width_pct = ((seb_upper - seb_lower) / seb_linreg * 100) if seb_linreg > 0 else 0

    seb_status = "INSIDE"
    if current_price > seb_upper:
        seb_status = "ABOVE_UPPER (Strong Impulse)"
    elif current_price < seb_lower:
        seb_status = "BELOW_LOWER (Strong Drop)"

    trend_quality_desc = "Low"
    if seb_r_sq > 0.8: trend_quality_desc = "High (Stable)"
    elif seb_r_sq > 0.5: trend_quality_desc = "Medium"

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

        # SL/TP Info
        sl_price = float(position.get('sl', 0))
        tp_price = float(position.get('tp', 0))

        sl_info = f"{sl_price:.2f}" if sl_price > 0 else "N/A"
        tp_info = f"{tp_price:.2f}" if tp_price > 0 else "N/A"

        position_block = f"""**Статус:** ЕСТЬ ОТКРЫТАЯ ПОЗИЦИЯ
| Параметр | Значение |
|----------|----------|
| Тип | {pos_type} |
| Цена входа | {entry_price:.2f} |
| Маржа | {margin:.2f} USDT |
| Размер | {size_coin} ({position_value:.2f} USDT) |
| PnL (Gross)| {pnl_usdt:.2f} {pnl_emoji} |
| ROE | {roe_percent:.2f}% |
| Комиссии | ~{total_fee:.2f} USDT (Est. Round-Trip) |
| PnL (Net) | ~{net_pnl:.2f} USDT (После комиссий) |
| Stop Loss | {sl_info} |
| Take Profit | {tp_info} |"""


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
        MIN_RISK_REWARD_RATIO = AGGRESSIVE_SETTINGS.get("MIN_RISK_REWARD_RATIO", 1.3)
        strategy_mode = "AGGRESSIVE"
    else:
        rsi_long_max = AI_THRESHOLDS.get('RSI_BUY_ENTRY_MAX', 65)
        rsi_long_forbidden = AI_THRESHOLDS.get('RSI_OVERBOUGHT', 70)
        rsi_short_min = AI_THRESHOLDS.get('RSI_SELL_ENTRY_MIN', 35)
        rsi_short_forbidden = AI_THRESHOLDS.get('RSI_OVERSOLD', 30)
        min_confidence = 0.7
        MIN_RISK_REWARD_RATIO = 1.5
        strategy_mode = "BALANCED"

    min_profit_breakeven = max(0.2, TRADING_FEE * 2.5)
    min_profit_partial = max(0.5, TRADING_FEE * 4.0)

    # === MOMENTUM STRATEGY SETTINGS ===
    from src.config import CHART_RANGES, DEFAULT_CHART_RANGE, SMART_SAMPLING, MOMENTUM_STRATEGY, STRATEGY_STYLE, STYLE_PRESETS

    current_interval = "1m"
    if DEFAULT_CHART_RANGE in CHART_RANGES:
        current_interval = CHART_RANGES[DEFAULT_CHART_RANGE].get("interval", "1m")

    # Get current style settings
    current_style = STYLE_PRESETS.get(STRATEGY_STYLE, STYLE_PRESETS["INTRADAY"])
    style_desc = current_style.get("description", "")

    # Используем настройки из конфига (с приоритетом MOMENTUM_STRATEGY если задано вручную)
    atr_sl_mult = MOMENTUM_STRATEGY.get("atr_sl_multiplier", current_style.get("atr_sl_mult", 2.0))
    atr_tp_mult = MOMENTUM_STRATEGY.get("atr_tp_multiplier", current_style.get("atr_tp_mult", 3.0))

    max_candles = MOMENTUM_STRATEGY.get("max_candles_in_prompt", 50)
    min_vol_ratio = MOMENTUM_STRATEGY.get("min_volume_ratio", 0.7)
    trend_consensus_req = MOMENTUM_STRATEGY.get("trend_consensus_required", False)
    momentum_entry = MOMENTUM_STRATEGY.get("momentum_entry_enabled", True)
    momentum_candles = MOMENTUM_STRATEGY.get("momentum_consecutive_candles", 3)

    # === ИСТОРИЯ СВЕЧЕЙ (Smart Sampling) ===
    # === DATA PREPARATION & SAMPLING ===
    # Strategy: Use ALL available fetched data for sampling/indicators to ensure "warmup".
    # Only trim to context_limit at the very end for the Prompt Table.

    context_limit = 500
    if DEFAULT_CHART_RANGE in CHART_RANGES:
        context_limit = CHART_RANGES[DEFAULT_CHART_RANGE].get("ai_context_candles", 500)

    is_smart_sampling = SMART_SAMPLING.get("enabled", True)

    if is_smart_sampling:
        recent_count = SMART_SAMPLING.get("recent_candles", 30)
        step = SMART_SAMPLING.get("history_step", 1)  # Default to 1 (Passthrough) if not set

        # We take the FULL fetched prices buffer
        full_buffer = prices

        if len(full_buffer) > recent_count:
            recent_part = full_buffer[-recent_count:]
            history_part = full_buffer[:-recent_count]

            sampled_history = []
            # Sample the history part
            for i in range(0, len(history_part), step):
                chunk = history_part[i:i+step]
                if not chunk: continue

                # Aggregate chunk
                agg_open = chunk[0].get("openPrice", 0)
                agg_close = chunk[-1].get("closePrice", 0)
                agg_high = max(chunk, key=lambda x: get_price_value(x.get("highPrice", 0))).get("highPrice", 0)
                agg_low = min(chunk, key=lambda x: get_price_value(x.get("lowPrice", 0))).get("lowPrice", 0)
                agg_vol = sum(float(x.get("volume", 0)) for x in chunk)

                # Use timestamp of the LAST candle in chunk (aligned with close)
                agg_ts = chunk[-1].get("snapshotTimeUTC", "")

                sampled_history.append({
                    "snapshotTimeUTC": agg_ts,
                    "openPrice": agg_open,
                    "highPrice": agg_high,
                    "lowPrice": agg_low,
                    "closePrice": agg_close,
                    "volume": agg_vol
                })

            calculation_data = sampled_history + recent_part
        else:
            calculation_data = full_buffer
    else:
        # If disabled, use all fetched data directly (no decimation)
        calculation_data = prices

    # === CALCULATE HISTORY SERIES (On Full Calculation Data) ===
    # This ensures indicators (RSI, SMA) have "warmup" data and don't start with 0.
    hist_closes = [get_price_value(p.get("closePrice", 0)) for p in calculation_data]
    hist_rsi = calculate_rsi_series(hist_closes)
    hist_sma = calculate_sma_series(hist_closes, AI_THRESHOLDS["SMA_PERIOD"])
    _, hist_seb_upper, hist_seb_lower = calculate_seb_series(hist_closes)

    # === SLICING FOR PROMPT CONTEXT ===
    # Now we trim the data to fit the AI Context Limit (e.g. 336 candles),
    # discarding the "warmup" head which served its purpose.

    if len(calculation_data) > context_limit:
        # Take the LAST 'context_limit' candles
        final_prices = calculation_data[-context_limit:]

        # Slice the indicators to match
        final_rsi = hist_rsi[-context_limit:]
        final_sma = hist_sma[-context_limit:]
        final_seb_u = hist_seb_upper[-context_limit:]
        final_seb_l = hist_seb_lower[-context_limit:]
    else:
        final_prices = calculation_data
        final_rsi = hist_rsi
        final_sma = hist_sma
        final_seb_u = hist_seb_upper
        final_seb_l = hist_seb_lower

    # Формируем таблицу свечей
    candle_lines = []
    for i, p in enumerate(final_prices):
        ts = p.get("snapshotTimeUTC", "")[-8:] if p.get("snapshotTimeUTC") else ""
        o = get_price_value(p.get("openPrice", 0))
        h = get_price_value(p.get("highPrice", 0))
        l = get_price_value(p.get("lowPrice", 0))
        c = get_price_value(p.get("closePrice", 0))
        v = float(p.get("volume", 0))

        # Access indicators by index (they are now aligned with final_prices)
        row_rsi = final_rsi[i]
        row_sma = final_sma[i]
        row_seb_u = final_seb_u[i]
        row_seb_l = final_seb_l[i]

        body = "🟢" if c > o else "🔴" if c < o else "⚪"

        # Format: Time|O|H|L|C|Vol|RSI|SMA|SEB_U|SEB_L|Pat
        # Using pipe for clear limitation. Formatting floats to save space but keep precision.
        candle_lines.append(f"{ts}|{o:.2f}|{h:.2f}|{l:.2f}|{c:.2f}|{v:.1f}|{row_rsi:.1f}|{row_sma:.2f}|{row_seb_u:.2f}|{row_seb_l:.2f}|{body}")

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

    # === DYNAMIC ROLE & STRATEGY SELECTION ===

    # 1. Define Role based on Style
    if STRATEGY_STYLE == "SWING":
        role_desc = "Ты — профессиональный Свинг-Трейдер (Swing Trader)."
        objective = "Поиск крупных движений (Days/Weeks), игнорирование внутридневного шума."
        time_horizon = "Horizon: 2-14 Days."

        # Swing Strategy
        strategy_section = f"""
## 3. СТРАТЕГИЯ: 🌊 SWING TRADING (MULTI-DAY)
*Контекст: Анализ 14 дней истории (1H свечи). Фокус на глобальном тренде.*

**Твоя Задача:**
Строить позицию для удержания от 2 до 10 дней.

**Условия входа (LONG):**
1.  **Macro Trend:** Цена выше SMA(200) или долгосрочный аптренд.
2.  **Structure:** Higher Highs + Higher Lows на графике.
3.  **Setup:** Откат к сильной поддержке или пробой консолидации.
4.  **Confirm:** Нет противоречия с новостным фоном.

**Условия выхода:**
1.  **TP:** Минимум 3-5% (или 3R). Давай прибыли течь.
2.  **SL:** Слом рыночной структуры (Structure Break).
3.  **Noise:** НЕ реагируй на одиночные контр-свечи, если структура сохраняется.
"""

    elif STRATEGY_STYLE == "SCALP":
        role_desc = "Ты — высокочастотный HFT-алгоритм (Scalper)."
        objective = "Захват быстрых импульсов (Minutes), жесткий риск-менеджмент."
        time_horizon = "Horizon: 5-30 Minutes."

        # Scalp Strategy (Momentum emphasis)
        strategy_section = f"""
## 3. СТРАТЕГИЯ: ⚡ HFT SCALPING (MOMENTUM)
*Контекст: Агрессивная торговля на малых ТФ. Нужна высокая точность.*

**Твоя Задача:**
Входить в импульсные движения (Breakout) и быстро забирать профит.

**Условия входа:**
1.  **Momentum:** Резкий всплеск объема (>1.5x) и волатильности.
2.  **Flow:** Серия одноцветных свечей (3+).
3.  **Level:** Пробой локального экстремума.
4.  **Override:** Игнорируй RSI Oversold/Overbought, если импульс сильный.

**Условия выхода:**
1.  **TP:** Скальперский (0.5% - 1.5%).
2.  **SL:** Жесткий. Выход сразу, если импульс затух.
"""

    else: # INTRADAY
        role_desc = "Ты — внутридневной трейдер (Intraday Trader)."
        objective = "Торговля внутри сессии, закрытие всех позиций к концу дня."
        time_horizon = "Horizon: 4-12 Hours."

        if is_momentum_market:
            # Intraday Momentum
             strategy_section = f"""
## 3. СТРАТЕГИЯ: 🔥 INTRADAY BREAKOUT
*Контекст: Рынок активен, работаем по тренду дня.*

**Твоя Задача:**
Найти точку входа в продолжение дневного тренда.

**Условия:**
1.  Пробой уровня сопротивления/поддержки дня.
2.  Подтверждение объемом.
3.  Удержание сделки до конца сессии или разворота.
"""
        else:
            # Intraday Pullback
             strategy_section = f"""
## 3. СТРАТЕГИЯ: ⚓ INTRADAY PULLBACK (ОТКАТ)
*Контекст: Спокойный рынок, работаем от коррекций.*

**Твоя Задача:**
Купить на низах (support/EMA) растущего тренда.

**Условия:**
1.  Касание EMA или уровня поддержки.
2.  Снижение объема на откате.
3.  RSI вернулся в нейтральную зону.
"""

    # === ФОРМИРУЕМ ОПТИМИЗИРОВАННЫЙ ПРОМПТ ===
    prompt = f"""## РОЛЬ И ЗАДАЧА
{role_desc}
Цель: {objective}
Стиль: **{STRATEGY_STYLE}**
{time_horizon}

**ТВОИ ПРИНЦИПЫ:**
1.  **Systematic:** Строго следуй алгоритму стратегии.
2.  **Risk Averse:** Лучше пропустить сделку, чем войти с плохим R:R.
3.  **Data Driven:** Никаких галлюцинаций. Только цифры из таблиц ниже.

---

## 1. ТОРГОВЫЙ КОНТЕКСТ
| Параметр | Значение |
|----------|----------|
| Пары | {symbol} |
| Интервал | {current_interval} |
| История | {context_limit} свечей |
| Стиль | **{STRATEGY_STYLE}** |
| Режим | **{strategy_mode}** |

{position_block}
{pnl_context}

---

## 2. АНАЛИЗ РЫНКА
### A. Индикаторы
| Метрика | Значение |
|---------|----------|
| Цена | {current_price:.2f} |
| Тренд (Global) | {global_trend} |
| Тренд (Local) | {local_trend} |
| RSI(14) | {rsi:.1f} |
| ATR(14) | {atr:.2f} |

### B. Импульс
| Индикатор | Статус | Значение |
|-----------|--------|----------|
| Volume | {volume_status} | {volume_ratio:.2f}x |
| Pattern | {last_5_direction} | {direction_desc} |
| SEB (Trend) | {seb_status} | Quality: {trend_quality_desc} (R²={seb_r_sq:.2f}) |

### C. Уровни
- **Resistance:** {resistance:.2f} (+{resistance_dist_pct:.2f}%)
- **Support:** {support:.2f} (-{support_dist_pct:.2f}%)
- **SE Bands:** Upper={seb_upper:.2f}, Lower={seb_lower:.2f}

---

{strategy_section}

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
Time|Open|High|Low|Close|Vol|RSI|SMA|SEB_U|SEB_L|Pattern
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
        "volume_ratio": volume_ratio,
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
