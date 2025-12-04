import json
import os
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta

# Проверяем наличие pandas
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from src.config import DATA_DIR, CHARTS_DIR, PLOTTER_RANGES, DEFAULT_PLOTTER_RANGE, CLEANUP_SETTINGS, SYMBOLS, AI_THRESHOLDS
from src.utils.logger import info, error
from src.utils.helpers import get_filename

def calculate_rsi(closes, period=14):
    """Рассчитывает RSI индикатор для всех точек как скользящее окно"""
    if len(closes) < period:
        return [50.0] * len(closes)

    rsi_values = []

    # Для первых period-1 значений используем None чтобы не показывать на графике
    for i in range(period - 1):
        rsi_values.append(None)

    # Рассчитываем RSI для каждой точки начиная с period
    for i in range(period - 1, len(closes)):
        # Берем окно из последних period значений
        window_closes = closes[i - period + 1:i + 1]

        # Рассчитываем дельты
        deltas = [window_closes[j] - window_closes[j-1] for j in range(1, len(window_closes))]

        # Разделяем на прибыли и убытки
        gains = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]

        # Средние значения по окну
        avg_gain = sum(gains) / len(deltas)
        avg_loss = sum(losses) / len(deltas)

        # Рассчитываем RSI
        if avg_loss == 0:
            rsi = 100.0
        elif avg_gain == 0:
            rsi = 0.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        rsi_values.append(round(rsi, 2))

    return rsi_values

def cleanup_old_files():
    """Удаляет старые файлы графиков и данных"""
    if not CLEANUP_SETTINGS["cleanup_old_charts"]:
        return

    try:
        retention_days = CLEANUP_SETTINGS["charts_retention_days"]
        cutoff_date = datetime.now() - timedelta(days=retention_days)

        # Очищаем старые графики
        if os.path.exists(CHARTS_DIR):
            removed_count = 0
            for filename in os.listdir(CHARTS_DIR):
                filepath = os.path.join(CHARTS_DIR, filename)
                if os.path.isfile(filepath):
                    file_time = datetime.fromtimestamp(os.path.getctime(filepath))
                    if file_time < cutoff_date:
                        os.remove(filepath)
                        removed_count += 1

            if removed_count > 0:
                info(f"🧹 Удалено {removed_count} старых графиков (старше {retention_days} дней)")

        # Очищаем старые данные о ценах
        if CLEANUP_SETTINGS["cleanup_old_data"]:
            retention_days = CLEANUP_SETTINGS["data_retention_days"]
            cutoff_date = datetime.now() - timedelta(days=retention_days)

            prices_dir = f"{DATA_DIR}/prices"
            if os.path.exists(prices_dir):
                removed_count = 0
                for filename in os.listdir(prices_dir):
                    filepath = os.path.join(prices_dir, filename)
                    if os.path.isfile(filepath):
                        file_time = datetime.fromtimestamp(os.path.getctime(filepath))
                        if file_time < cutoff_date:
                            os.remove(filepath)
                            removed_count += 1

                if removed_count > 0:
                    info(f"🧹 Удалено {removed_count} старых файлов данных (старше {retention_days} дней)")

    except Exception as e:
        error(f"❌ Ошибка при очистке старых файлов: {str(e)}")

def plot_symbol(symbol, time_range=None):
    """Строит график для символа и сохраняет как PNG"""
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)

    # Determine time range settings
    if time_range is None:
        time_range = DEFAULT_PLOTTER_RANGE

    range_config = PLOTTER_RANGES.get(time_range, PLOTTER_RANGES.get("1D"))

    # Calculate cutoff time
    now = datetime.now()
    cutoff_time = now

    if "days" in range_config:
        cutoff_time = now - timedelta(days=range_config["days"])
    elif "hours" in range_config:
        cutoff_time = now - timedelta(hours=range_config["hours"])
    elif "minutes" in range_config:
        cutoff_time = now - timedelta(minutes=range_config["minutes"])
    else:
        # Default fallback
        cutoff_time = now - timedelta(days=1)

    # Подготавливаем данные
    timestamps = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []

    for candle in prices:
        # Parse timestamp first to filter
        ts_str = candle["snapshotTimeUTC"]
        try:
            # Try parsing ISO format
            if ts_str.endswith('Z'):
                ts_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                ts_dt = datetime.fromisoformat(ts_str).replace(tzinfo=None)
        except ValueError:
             # Fallback for other formats if needed
             try:
                 from dateutil import parser
                 ts_dt = parser.parse(ts_str).replace(tzinfo=None)
             except:
                 continue # Skip if can't parse

        # Filter by time
        if ts_dt < cutoff_time:
            continue

        timestamps.append(ts_str)

        # Handle different price formats
        if isinstance(candle["closePrice"], dict):
            opens.append(float(candle["openPrice"]["bid"]))
            highs.append(float(candle["highPrice"]["bid"]))
            lows.append(float(candle["lowPrice"]["bid"]))
            closes.append(float(candle["closePrice"]["bid"]))
            volumes.append(float(candle.get("lastTradedVolume", 0)))
        else:
            opens.append(float(candle["openPrice"]))
            highs.append(float(candle["highPrice"]))
            lows.append(float(candle["lowPrice"]))
            closes.append(float(candle["closePrice"]))
            volumes.append(float(candle.get("volume", 0)))

    # Check if we have data after filtering
    if not timestamps:
        info(f"⚠️ Нет данных для {symbol} за период {time_range}")
        return

    # Конвертируем временные метки в datetime объекты
    if PANDAS_AVAILABLE:
        import pandas as pd
        dates = pd.to_datetime(timestamps)
    else:
        try:
            from dateutil import parser
            dates = [parser.parse(ts) for ts in timestamps]
        except ImportError:
            dates = [datetime.fromisoformat(ts.replace('Z', '+00:00')) for ts in timestamps]

    # Рассчитываем индикаторы
    # SMAs
    smas = {}
    sma_periods = [10, 20, 50, 100, 200]
    for period in sma_periods:
        if len(closes) >= period:
            smas[period] = [sum(closes[max(0, i-period+1):i+1])/min(period, i+1) for i in range(len(closes))]
        else:
            smas[period] = [sum(closes) / len(closes)] * len(closes)

    # RSI
    rsi_period = AI_THRESHOLDS["RSI_PERIOD"]
    rsi = calculate_rsi(closes, rsi_period)

    # Determine chart width based on duration
    # > 12h: 48 (Original)
    # > 4h and <= 12h: 36
    # <= 4h: 24

    chart_width = 48 # Default for > 12h

    # Calculate total minutes for comparison
    total_minutes = 0
    if "days" in range_config:
        total_minutes = range_config["days"] * 24 * 60
    elif "hours" in range_config:
        total_minutes = range_config["hours"] * 60
    elif "minutes" in range_config:
        total_minutes = range_config["minutes"]

    if total_minutes <= 4 * 60: # <= 4h
        chart_width = 24
    elif total_minutes <= 12 * 60: # <= 12h (and > 4h)
        chart_width = 36

    # Создаем фигуру с тремя subplot'ами (Цена, Объем, RSI)
    # Увеличиваем размер фигуры для высокого разрешения
    # Reduced height by 25% (18 -> 13.5)
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(chart_width, 13.5), gridspec_kw={'height_ratios': [3, 1, 1]}, sharex=True)

    # --- 1. График Цены (Candlesticks + SMAs) ---

    # Разделяем на растущие и падающие свечи для покраски
    up_dates, up_opens, up_closes, up_highs, up_lows = [], [], [], [], []
    down_dates, down_opens, down_closes, down_highs, down_lows = [], [], [], [], []

    # Для объема
    up_volumes, down_volumes = [], []
    up_vol_dates, down_vol_dates = [], []

    for i in range(len(dates)):
        if closes[i] >= opens[i]:
            up_dates.append(dates[i])
            up_opens.append(opens[i])
            up_closes.append(closes[i])
            up_highs.append(highs[i])
            up_lows.append(lows[i])
            up_volumes.append(volumes[i])
            up_vol_dates.append(dates[i])
        else:
            down_dates.append(dates[i])
            down_opens.append(opens[i])
            down_closes.append(closes[i])
            down_highs.append(highs[i])
            down_lows.append(lows[i])
            down_volumes.append(volumes[i])
            down_vol_dates.append(dates[i])

    # Цвета свечей
    col_up = '#26a69a'   # Green
    col_down = '#ef5350' # Red
    # Determine width based on interval
    interval = range_config.get("interval", "1m")

    # Width mapping (approximate days per candle)
    # 1m = 1/(24*60) = 0.00069
    # 5m = 0.0035
    # 15m = 0.01
    # 1h = 0.04
    # 4h = 0.16
    # 1d = 0.8

    width_map = {
        "1m": 0.0006,
        "5m": 0.0025,
        "15m": 0.007,
        "30m": 0.015,
        "1h": 0.03,
        "4h": 0.12,
        "1d": 0.7,
        "1w": 5.0
    }

    width = width_map.get(interval, 0.0025)

    # Рисуем растущие свечи
    if up_dates:
        # Тело свечи
        heights = [c - o for c, o in zip(up_closes, up_opens)]
        ax1.bar(up_dates, heights, width, bottom=up_opens, color=col_up, edgecolor=col_up)
        # Тени свечи
        ax1.vlines(up_dates, up_lows, up_highs, color=col_up, linewidth=1)

    # Рисуем падающие свечи
    if down_dates:
        # Тело свечи
        heights = [o - c for o, c in zip(down_opens, down_closes)]
        ax1.bar(down_dates, heights, width, bottom=down_closes, color=col_down, edgecolor=col_down)
        # Тени свечи
        ax1.vlines(down_dates, down_lows, down_highs, color=col_down, linewidth=1)

    # Рисуем SMA с градиентом (Hot palette: Yellow -> Red)
    sma_colors = {
        10: '#fff7bc',  # Light Yellow
        20: '#fec44f',  # Orange-Yellow
        50: '#fe9929',  # Orange
        100: '#d95f0e', # Red-Orange
        200: '#993404'  # Dark Red
    }

    for period in sma_periods:
        ax1.plot(dates, smas[period], label=f"SMA({period})", color=sma_colors[period], linewidth=1.5, alpha=0.9)

    # Оформление графика цены
    ax1.set_title(f"{symbol} - {datetime.now().strftime('%Y-%m-%d %H:%M')} ({time_range})", fontsize=20, pad=20)
    ax1.set_ylabel("Цена", fontsize=14, labelpad=10)
    ax1.legend(fontsize=12, loc='upper left')
    ax1.grid(alpha=0.2)

    # Текущая цена
    current_price = closes[-1]
    ax1.text(0.98, 0.98, f"Цена: {current_price:.5f}",
             transform=ax1.transAxes, fontsize=14,
             verticalalignment='top', horizontalalignment='right',
             bbox=dict(boxstyle='round', facecolor='#d62728', alpha=0.1))

    # --- 2. График Объема ---
    if up_vol_dates:
        ax2.bar(up_vol_dates, up_volumes, width, color=col_up, alpha=0.5)
    if down_vol_dates:
        ax2.bar(down_vol_dates, down_volumes, width, color=col_down, alpha=0.5)

    ax2.set_ylabel("Объем", fontsize=12, labelpad=10)
    ax2.grid(alpha=0.2)

    # --- 3. График RSI ---
    ax3.plot(dates, rsi, label=f"RSI({rsi_period})", color="#2ca02c", linewidth=2)
    ax3.axhline(y=70, color='r', linestyle=':', alpha=0.7, label="Перекупленность (70)")
    ax3.axhline(y=30, color='g', linestyle=':', alpha=0.7, label="Перепроданность (30)")
    ax3.fill_between(dates, 70, 100, alpha=0.1, color='red')
    ax3.fill_between(dates, 0, 30, alpha=0.1, color='green')

    # Оформление RSI
    ax3.set_ylabel("RSI", fontsize=12, labelpad=10)
    ax3.set_xlabel("Время", fontsize=14, labelpad=10)
    ax3.set_ylim(0, 100)
    ax3.legend(fontsize=11, loc='upper left')
    ax3.grid(alpha=0.2)

    # Форматирование оси X
    # Форматирование оси X
    def custom_date_formatter(x, pos):
        dt = mdates.num2date(x)
        # Если время 00:00, показываем дату (день и месяц)
        if dt.hour == 0 and dt.minute == 0:
            return dt.strftime('%d %b')
        return dt.strftime('%H:%M')

    ax3.xaxis.set_major_formatter(plt.FuncFormatter(custom_date_formatter))
    fig.autofmt_xdate()

    # Убираем отступы по краям (слева и справа)
    if len(dates) > 0:
        ax1.set_xlim(dates[0], dates[-1])

    # Сохраняем с высоким разрешением (перезаписываем файл)
    filename = f"{CHARTS_DIR}/{get_filename(symbol)}.png"
    plt.savefig(filename, dpi=200, bbox_inches='tight')
    plt.close()

    info(f"🖼️ График для {symbol} сохранен как {filename} (Range: {time_range})")

def main():
    """Основная функция генерации графиков"""
    from src.config import ENABLE_PARALLEL_PROCESSING
    import concurrent.futures
    import multiprocessing
    import sys

    # Allow passing range as argument
    if len(sys.argv) > 1:
        chart_range = sys.argv[1]
    else:
        chart_range = DEFAULT_PLOTTER_RANGE

    info(f"📊 Генерация графиков (Range: {chart_range})...")

    # Убеждаемся что директория существует
    os.makedirs(CHARTS_DIR, exist_ok=True)

    # Очищаем старые файлы
    cleanup_old_files()

    if ENABLE_PARALLEL_PROCESSING:
        # Use ProcessPoolExecutor for CPU-bound plotting tasks
        # Matplotlib is NOT thread-safe, so we MUST use processes
        max_workers = multiprocessing.cpu_count()
        info(f"🚀 Запуск параллельной генерации графиков (процессов: {max_workers})...")

        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {executor.submit(plot_symbol, symbol, chart_range): symbol for symbol in SYMBOLS}

            for future in concurrent.futures.as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    future.result()
                except Exception as e:
                    error(f"❌ Ошибка генерации графика для {symbol}: {str(e)}")
    else:
        # Sequential execution
        info("🐌 Запуск последовательной генерации графиков...")
        for symbol in SYMBOLS:
            try:
                plot_symbol(symbol, chart_range)
            except Exception as e:
                error(f"❌ Ошибка генерации графика для {symbol}: {str(e)}")

if __name__ == "__main__":
    main()
