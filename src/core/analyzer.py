import json
import os
from src.config import DATA_DIR, AI_THRESHOLDS, SYMBOLS, ENABLE_NEWS, TECHNICAL_ANALYSIS
from src.utils.logger import info, error, warning
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


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """
    Рассчитывает MACD (Moving Average Convergence Divergence)
    Returns: (macd_line, signal_line, histogram)
    """
    if len(prices) < slow + signal:
        return 0, 0, 0

    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow

    # Calculate MACD history for signal line
    macd_history = []
    for i in range(slow, len(prices)):
        subset = prices[:i+1]
        ef = calculate_ema(subset, fast)
        es = calculate_ema(subset, slow)
        macd_history.append(ef - es)

    if len(macd_history) >= signal:
        signal_line = calculate_ema(macd_history, signal)
    else:
        signal_line = macd_line

    histogram = macd_line - signal_line
    return round(macd_line, 6), round(signal_line, 6), round(histogram, 6)


def calculate_bollinger_bands(prices, period=20, std_mult=2.0):
    """
    Рассчитывает Bollinger Bands
    Returns: (upper_band, middle_band, lower_band, width_percent)
    """
    if len(prices) < period:
        return 0, 0, 0, 0

    recent = prices[-period:]
    middle = sum(recent) / period

    # Calculate standard deviation
    variance = sum((p - middle) ** 2 for p in recent) / period
    std_dev = variance ** 0.5

    upper = middle + (std_mult * std_dev)
    lower = middle - (std_mult * std_dev)

    # Width as percentage
    width_pct = ((upper - lower) / middle * 100) if middle > 0 else 0

    return round(upper, 5), round(middle, 5), round(lower, 5), round(width_pct, 2)


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
        # Handle different price formats (dict vs float)
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

def calculate_support_resistance(prices, window=None):
    if window is None:
        window = TECHNICAL_ANALYSIS.get("sr_window", 20)
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

def calculate_seb_series(prices, length=None, mult=None):
    if length is None:
        length = TECHNICAL_ANALYSIS.get("seb_length", 20)
    if mult is None:
        mult = TECHNICAL_ANALYSIS.get("seb_multiplier", 2.0)
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

def calculate_seb(prices, length=None, mult=None):
    if length is None:
        length = TECHNICAL_ANALYSIS.get("seb_length", 20)
    if mult is None:
        mult = TECHNICAL_ANALYSIS.get("seb_multiplier", 2.0)
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

from src.exchanges.exchange_factory import get_exchange_client

def analyze_symbol_with_position(symbol, decision_context=""):
    """
    Анализирует один символ, самостоятельно получая информацию о текущей позиции.
    Используется в режиме multiprocessing.
    """
    try:
        client = get_exchange_client()
        positions = client.get_positions()
        symbol_positions = positions.get(symbol, [])
        current_position = symbol_positions[0] if symbol_positions else None

        return analyze_symbol(symbol, position=current_position, decision_context=decision_context)
    except Exception as e:
        error(f"❌ Ошибка получения позиции для {symbol}: {e}")
        return analyze_symbol(symbol, position=None, decision_context=decision_context)

def analyze_symbol(symbol, position=None, decision_context=""):
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

    # RSI series for divergence detection
    rsi_values = calculate_rsi_series(close_prices)

    # EMA расчёты
    _ema_periods = TECHNICAL_ANALYSIS.get("ema_periods", [9, 21])
    ema9 = calculate_ema(close_prices, _ema_periods[0])
    ema21 = calculate_ema(close_prices, _ema_periods[1] if len(_ema_periods) > 1 else 21)

    # ATR расчёт
    atr = calculate_atr(prices, 14)

    # ATR ratio for volatility filter (current vs average)
    if len(close_prices) >= 20:
        price_changes = [abs(close_prices[i] - close_prices[i-1]) for i in range(1, len(close_prices))]
        avg_atr = sum(price_changes[-20:]) / 20 if price_changes else atr
        atr_ratio = atr / avg_atr if avg_atr > 0 else 1.0
    else:
        atr_ratio = 1.0

    # MACD calculation
    macd_line, macd_signal, macd_hist = calculate_macd(close_prices)

    # Bollinger Bands calculation
    bb_upper, bb_middle, bb_lower, bb_width = calculate_bollinger_bands(close_prices)

    # Текущая цена
    last_close = prices[-1]["closePrice"]
    current_price = get_price_value(last_close)

    # Определяем тренды
    global_trend = "UP" if current_price > sma else "DOWN"
    local_trend = "BULLISH" if ema9 > ema21 else "BEARISH"
    trends_aligned = (global_trend == "UP" and local_trend == "BULLISH") or \
                     (global_trend == "DOWN" and local_trend == "BEARISH")

    # Анализ последних N свечей
    _trend_candles = TECHNICAL_ANALYSIS.get("trend_candle_count", 5)
    last_5_closes = close_prices[-_trend_candles:] if len(close_prices) >= _trend_candles else close_prices
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
    # === STANDARD ERROR BANDS (SEB) ===
    seb_linreg, seb_upper, seb_lower, seb_r_sq = calculate_seb(close_prices)
    seb_status = "INSIDE"
    if current_price > seb_upper:
        seb_status = "ABOVE_UPPER (Strong Impulse)"
    elif current_price < seb_lower:
        seb_status = "BELOW_LOWER (Strong Drop)"

    trend_quality_desc = "Low"
    if seb_r_sq > 0.8: trend_quality_desc = "High (Stable)"
    elif seb_r_sq > 0.5: trend_quality_desc = "Medium"

    # === ОБЪЁМ И ВОЛАТИЛЬНОСТЬ ===
    _vol_avg_window = TECHNICAL_ANALYSIS.get("volume_avg_window", 20)
    _vol_thresh = TECHNICAL_ANALYSIS.get("volume_thresholds", {})
    _vol_anomaly = _vol_thresh.get("anomaly", 2.0)
    _vol_elevated = _vol_thresh.get("elevated", 1.2)
    _vol_low = _vol_thresh.get("low", 0.5)

    volumes = [float(p.get('volume', 0)) for p in prices]
    if len(volumes) >= _vol_avg_window:
        avg_volume = sum(volumes[-_vol_avg_window:]) / _vol_avg_window
        current_volume = volumes[-1] if volumes else 0
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
    else:
        volume_ratio = 1.0

    if volume_ratio > _vol_anomaly:
        volume_status = "🔥 АНОМАЛЬНО ВЫСОКИЙ"
    elif volume_ratio > _vol_elevated:
        volume_status = "📈 Повышенный"
    elif volume_ratio < _vol_low:
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
    from src.config import TRADING_FEE, MIN_PARTIAL_CLOSE_PNL, LEVERAGE

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
        strategy_mode = "AGGRESSIVE"
    else:
        rsi_long_max = AI_THRESHOLDS.get('RSI_BUY_ENTRY_MAX', 65)
        rsi_long_forbidden = AI_THRESHOLDS.get('RSI_OVERBOUGHT', 70)
        rsi_short_min = AI_THRESHOLDS.get('RSI_SELL_ENTRY_MIN', 35)
        rsi_short_forbidden = AI_THRESHOLDS.get('RSI_OVERSOLD', 30)
        min_confidence = 0.7
        strategy_mode = "BALANCED"

    # === MOMENTUM STRATEGY SETTINGS ===
    from src.config import CHART_RANGES, DEFAULT_CHART_RANGE, SMART_SAMPLING, MOMENTUM_STRATEGY, STRATEGY_STYLE, STYLE_PRESETS

    # Get current style settings
    current_style = STYLE_PRESETS.get(STRATEGY_STYLE, STYLE_PRESETS["INTRADAY"])
    current_interval = current_style.get("timeframe", "5m")

    # Используем настройки из конфига (с приоритетом MOMENTUM_STRATEGY если задано вручную)
    atr_sl_mult = MOMENTUM_STRATEGY.get("atr_sl_multiplier", current_style.get("atr_sl_mult", 2.0))
    atr_tp_mult = MOMENTUM_STRATEGY.get("atr_tp_multiplier", current_style.get("atr_tp_mult", 3.0))

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

            # Log aggregation details
            if step > 1:
                agg_count = len(history_part) // step
                info(f"📊 Smart Sampling: Aggregating {len(history_part)} history candles (step={step}) → "
                     f"~{agg_count} aggregated + {recent_count} recent = ~{agg_count + recent_count} total")

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
        _max_news = TECHNICAL_ANALYSIS.get("news_items_in_prompt", 5)
        for item in news[:_max_news]:
            news_items.append(f"- [{item.get('timestamp', 'N/A')}] {item.get('title', 'N/A')}")
        news_section = f"""
---

## НОВОСТНОЙ ФОН (для контекста)
{chr(10).join(news_items)}

**ВАЖНО**: Новости — вторичный фактор. Технический анализ имеет приоритет.
"""

    # === MOMENTUM MARKET CHECK ===
    _mom_vol = TECHNICAL_ANALYSIS.get("momentum_volume_threshold", 1.2)
    _mom_trend_vol = TECHNICAL_ANALYSIS.get("momentum_trend_volume_threshold", 1.0)
    is_momentum_market = False
    if volume_ratio > _mom_vol:
        is_momentum_market = True
    elif trends_aligned and volume_ratio > _mom_trend_vol:
        is_momentum_market = True

    # === HYBRID MODE: Deterministic Signal Generation ===
    from src.config import STRATEGY_STYLE

    signal_data = None
    close_signal = None
    regime_data = None

    if STRATEGY_STYLE == "HYBRID":
        from src.core.signal_generator import generate_signal, should_close

        # Подготавливаем данные для генератора сигналов
        signal_input = {
            "global_trend": global_trend,
            "local_trend": local_trend,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "current_price": current_price,
            "support": support,
            "resistance": resistance,
            "ema9": ema9,
            "ema21": ema21,
            "last_5_direction": last_5_direction,
            # New indicators for improved scoring
            "atr": atr,
            "atr_ratio": atr_ratio,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_width": bb_width,
            # Close prices for regime detection
            "close_prices": close_prices,
            # RSI series for divergence detection
            "rsi_values": rsi_values,
        }

        # === Детекция рыночного режима ===
        try:
            from src.core.regime import detect_regime
            regime_data = detect_regime(signal_input)
            info(f"🌐 [HYBRID] Regime: {regime_data['regime']} (trend={regime_data.get('trend_strength', 0):.2f}, vol={regime_data.get('volatility_state', '?')}, dir={regime_data.get('directional_consistency', 0):.2f})")
        except Exception as e:
            warning(f"⚠️ [HYBRID] Regime detection failed: {e}")
            regime_data = None

        # Генерируем детерминированный сигнал (с учётом режима)
        signal_data = generate_signal(signal_input, regime=regime_data)
        info(f"🔧 [HYBRID] Generated signal: {signal_data['signal']} (score: {signal_data['score']}, quality: {signal_data.get('quality', 0):.2f})")

        # Проверяем условия закрытия позиции
        if position:
            close_signal = should_close(signal_input, position)
            if close_signal.get("should_close"):
                info(f"🔧 [HYBRID] Close signal: {close_signal['reason']}")

    # === СБОРКА ПРОМПТА ===
    from src.prompts.builder import PromptBuilder

    fee_context = (
        f"## 💰 КОМИССИИ И УБЫТКИ (CRITICAL)\n"
        f"*   **Биржевая комиссия:** {TRADING_FEE}% (за сделку).\n"
        f"*   **Round-Trip (Вход+Выход):** ~{TRADING_FEE * 2:.3f}%.\n"
        f"*   **Break-Even:** Цена должна пройти минимум {TRADING_FEE * 2.1:.3f}%, чтобы покрыть комиссию.\n"
        f"*   **ПРАВИЛО:** Не открывай сделки с потенциалом прибыли < {TRADING_FEE * 3:.3f}% (комиссия съест прибыль)."
    )

    prompt_ctx = {
        "fee_context": fee_context,
        "symbol": symbol,
        "current_interval": current_interval,
        "context_limit": context_limit,
        "strategy_mode": strategy_mode,
        "position_block": position_block,
        "pnl_context": pnl_context,
        "current_price": current_price,
        "global_trend": global_trend,
        "local_trend": local_trend,
        "rsi": rsi,
        "atr": atr,
        "volume_status": volume_status,
        "volume_ratio": volume_ratio,
        "last_5_direction": last_5_direction,
        "direction_desc": direction_desc,
        "seb_status": seb_status,
        "trend_quality_desc": trend_quality_desc,
        "seb_r_sq": seb_r_sq,
        "resistance": resistance,
        "resistance_dist_pct": resistance_dist_pct,
        "support": support,
        "support_dist_pct": support_dist_pct,
        "seb_upper": seb_upper,
        "seb_lower": seb_lower,
        "long_sl": max(support - atr * 0.5, current_price - atr * atr_sl_mult),
        "long_tp": min(resistance, current_price + atr * atr_tp_mult),
        "short_sl": min(resistance + atr * 0.5, current_price + atr * atr_sl_mult),
        "short_tp": max(support, current_price - atr * atr_tp_mult),
        "candle_history": candle_history,
        "news_section": news_section,
        "min_confidence": min_confidence,
        "is_momentum_market": is_momentum_market,
        "decision_history": decision_context,
        # HYBRID mode specific
        "signal_data": signal_data,
        "close_signal": close_signal,
    }

    prompt = PromptBuilder.build(STRATEGY_STYLE, prompt_ctx)

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
        "position": position,
        "prompt": prompt.strip(),
        "prompt_ctx": prompt_ctx,
        # HYBRID mode specific
        "signal_data": signal_data,
        "close_signal": close_signal,
        "regime": regime_data,
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
