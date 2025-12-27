import json
import os
import matplotlib
matplotlib.use('Agg') # Force headless backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta, timezone

# Проверяем наличие pandas
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from src.config import DATA_DIR, CHARTS_DIR, PLOTTER_RANGES, DEFAULT_PLOTTER_RANGE, CLEANUP_SETTINGS, SYMBOLS, AI_THRESHOLDS, AGGRESSIVE_MODE
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

def plot_symbol(symbol, time_range=None, current_position=None):
    """Строит график для символа и сохраняет как PNG"""
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)

    # ... (rest of data loading logic remains, just signature changed above) ...

    # [SKIP UNCHANGED LINES TO RETAIN CONTEXT IF NEEDED, BUT HERE I PASTE FULL START TO BE SAFE OR USE CHUNK]
    # actually I can't skip too much without breaking tool usage rules about context match.
    # The tool requires exact match.

    # I will split this into two edits for safety.
    # Edit 1: Signature change.
    # Edit 2: Plotting logic.


    # Determine time range settings
    if time_range is None:
        time_range = DEFAULT_PLOTTER_RANGE

    range_config = PLOTTER_RANGES.get(time_range)

    if not range_config:
        # Try case-insensitive match
        for key, val in PLOTTER_RANGES.items():
            if key.lower() == time_range.lower():
                range_config = val
                time_range = key # Update to canonical name for display
                break

    if not range_config:
        info(f"⚠️ Range '{time_range}' not found in config. Using default 1D.")
        range_config = PLOTTER_RANGES.get("1D")
        time_range = "1D"

    # Calculate cutoff time
    # Use timezone-aware UTC
    now = datetime.now(timezone.utc)
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

    # Parse all data first
    all_dates = []
    all_opens = []
    all_highs = []
    all_lows = []
    all_closes = []
    all_volumes = []

    for candle in prices:
        ts_str = candle["snapshotTimeUTC"]
        try:
            if ts_str.endswith('Z'):
                ts_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            else:
                ts_dt = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        except ValueError:
             try:
                 from dateutil import parser
                 ts_dt = parser.parse(ts_str).replace(tzinfo=timezone.utc)
             except:
                 continue

        all_dates.append(ts_dt)

        if isinstance(candle["closePrice"], dict):
            all_opens.append(float(candle["openPrice"]["bid"]))
            all_highs.append(float(candle["highPrice"]["bid"]))
            all_lows.append(float(candle["lowPrice"]["bid"]))
            all_closes.append(float(candle["closePrice"]["bid"]))
            all_volumes.append(float(candle.get("lastTradedVolume", 0)))
        else:
            all_opens.append(float(candle["openPrice"]))
            all_highs.append(float(candle["highPrice"]))
            all_lows.append(float(candle["lowPrice"]))
            all_closes.append(float(candle["closePrice"]))
            all_volumes.append(float(candle.get("volume", 0)))

    if not all_dates:
        info(f"⚠️ Нет данных для {symbol}")
        return

    # Calculate indicators on FULL dataset
    # SMAs
    all_smas = {}
    sma_periods = [10, 20, 50, 100, 200]
    for period in sma_periods:
        if len(all_closes) >= period:
            all_smas[period] = [sum(all_closes[max(0, i-period+1):i+1])/min(period, i+1) for i in range(len(all_closes))]
        else:
            all_smas[period] = [sum(all_closes) / len(all_closes)] * len(all_closes)

    # RSI
    rsi_period = AI_THRESHOLDS["RSI_PERIOD"]
    all_rsi = calculate_rsi(all_closes, rsi_period)

    # Now filter for display
    dates = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    smas = {p: [] for p in sma_periods}
    rsi = []

    for i, ts_dt in enumerate(all_dates):
        if ts_dt < cutoff_time:
            continue

        # Convert to Local Time (Naive) for plotting
        ts_local = ts_dt.astimezone().replace(tzinfo=None)
        dates.append(ts_local)

        opens.append(all_opens[i])
        highs.append(all_highs[i])
        lows.append(all_lows[i])
        closes.append(all_closes[i])
        volumes.append(all_volumes[i])

        for p in sma_periods:
            smas[p].append(all_smas[p][i])

        rsi.append(all_rsi[i])

    # Check if we have data after filtering
    if not dates:
        info(f"⚠️ Нет данных для {symbol} за период {time_range}")
        return

    # Determine chart width based on number of candles
    # We want roughly 0.15 inches per candle, but within reasonable bounds
    # This prevents "fat" candles on short ranges and "squashed" candles on long ranges
    # Calculate Standard Error Bands for Plotting (using same logic as analyzer)
    import numpy as np

    # Needs to align with chart dates, so we calculate over the whole dataset first
    seb_linreg = []
    seb_upper = []
    seb_lower = []

    # SEB Parameters
    seb_length = 20
    seb_mult = 2.0

    for i in range(len(all_closes)):
        if i < seb_length:
            seb_linreg.append(np.nan)
            seb_upper.append(np.nan)
            seb_lower.append(np.nan)
        else:
            y = np.array(all_closes[i-seb_length:i])
            x = np.arange(seb_length)
            A = np.vstack([x, np.ones(len(x))]).T
            m, c = np.linalg.lstsq(A, y, rcond=None)[0]

            # Predict next point (which corresponds to 'i')
            # Actually, standard is to fit on previous points.
            # Analyzer fits on LAST 20 points ending at current.
            # So LinReg value at 'current' index is m * (length-1) + c

            reg_val = m * (seb_length - 1) + c

            # StdErr
            residuals = y - (m * x + c)
            std_err = np.sqrt(np.sum(residuals**2) / (seb_length - 2))

            seb_linreg.append(reg_val)
            seb_upper.append(reg_val + (seb_mult * std_err))
            seb_lower.append(reg_val - (seb_mult * std_err))


    num_candles = len(dates)
    chart_width = max(10, min(30, num_candles * 0.15))

    # Creates subplot figure (PRICE, VOLUME, RSI)
    # Reduced height by 25% (18 -> 13.5)
    plt.close('all') # Fix for RuntimeWarning: More than 20 figures have been opened
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(chart_width, 13.5), gridspec_kw={'height_ratios': [3, 1, 1]}, sharex=True)

    # ... (Prepare plotting data arrays for filtered range) ...
    plot_seb_upper = []
    plot_seb_lower = []
    plot_seb_mid = []

    # Loop over all_dates to extract the relevant slice for plotting (filtering by cutoff_time)
    # Note: 'dates' list is already filtered above, but we need to filter SEB arrays index-wise

    current_plot_index = 0
    for i, ts_dt in enumerate(all_dates):
        if ts_dt < cutoff_time:
            continue

        # Sync with the 'dates' loop above
        plot_seb_upper.append(seb_upper[i])
        plot_seb_lower.append(seb_lower[i])
        plot_seb_mid.append(seb_linreg[i])


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
    # Determine width dynamically based on minimum time difference
    # This ensures candles never overlap and always have appropriate spacing
    if len(dates) > 1:
        # Calculate differences between consecutive dates in days (matplotlib format)
        # dates contains datetime objects, so diff is timedelta
        diffs = [(dates[i+1] - dates[i]).total_seconds() / (24*3600) for i in range(len(dates)-1)]
        min_diff = min(diffs)
        # Use 80% of the minimum gap
        width = min_diff * 0.8
    else:
        # Fallback if only 1 candle
        interval = range_config.get("interval", "1m")
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

    # Рисуем Standard Error Bands (SEB)
    ax1.plot(dates, plot_seb_mid, label="LinReg (SEB)", color="purple", linestyle="-.", linewidth=1.5, alpha=0.8)
    ax1.plot(dates, plot_seb_upper, color="purple", linestyle=":", linewidth=1, alpha=0.5)
    ax1.plot(dates, plot_seb_lower, color="purple", linestyle=":", linewidth=1, alpha=0.5)
    ax1.fill_between(dates, plot_seb_upper, plot_seb_lower, color="purple", alpha=0.05, label="SEB (2.0)")

    # Отображение текущей позиции
    if current_position and current_position.get("status") == "OPEN":
        try:
            entry_price = float(current_position.get("entry_price", 0))
            side = current_position.get("side", "UNKNOWN")
            pnl = float(current_position.get("last_pnl", 0))

            if entry_price > 0:
                pos_color = '#00e676' if side.upper() == 'LONG' else '#ff1744'
                label = f"{side} @ {entry_price} (PnL: {pnl})"
                ax1.axhline(y=entry_price, color=pos_color, linestyle='--', linewidth=2, label=label)

                # Visualizing SL/TP
                sl_price = float(current_position.get('sl', 0) or 0)
                tp_price = float(current_position.get('tp', 0) or 0)

                if sl_price > 0:
                    ax1.axhline(y=sl_price, color='red', linestyle=':', linewidth=1.5, label=f"SL: {sl_price}")
                if tp_price > 0:
                    ax1.axhline(y=tp_price, color='green', linestyle=':', linewidth=1.5, label=f"TP: {tp_price}")
        except Exception as e:
            info(f"⚠️ Ошибка отображения позиции на графике: {e}")

    # Оформление графика цены
    ax1.set_title(f"{symbol} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({time_range})", fontsize=20, pad=40)

    # --- STATUS BADGES ---
    # 1. Trading Mode Badge
    mode_text = "MODE: AGGRESSIVE" if AGGRESSIVE_MODE else "MODE: NORMAL"
    mode_color = '#ff6d00' if AGGRESSIVE_MODE else '#2962ff' # Orange vs Blue

    # Place ABOVE the chart (y > 1.0) to align with title and avoid overlap
    ax1.text(0.0, 1.02, mode_text, transform=ax1.transAxes,
             fontsize=10, fontweight='bold', color='white',
             verticalalignment='bottom', horizontalalignment='left',
             bbox=dict(boxstyle='round,pad=0.3', facecolor=mode_color, alpha=0.9, edgecolor='none'))

    # 2. Position Status Badge
    pos_text = "NO POSITION"
    pos_color = '#757575' # Gray

    if current_position and current_position.get("status") == "OPEN":
        side = current_position.get("side", "UNKNOWN").upper()
        if side == "LONG":
            pos_text = f"POS: LONG"
            pos_color = '#00c853' # Green
        elif side == "SHORT":
            pos_text = f"POS: SHORT"
            pos_color = '#d50000' # Red

    # Place next to Mode badge (offset x)
    ax1.text(0.25, 1.02, pos_text, transform=ax1.transAxes,
             fontsize=10, fontweight='bold', color='white',
             verticalalignment='bottom', horizontalalignment='left',
             bbox=dict(boxstyle='round,pad=0.3', facecolor=pos_color, alpha=0.9, edgecolor='none'))
    # ---------------------
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
    rsi_overbought = AI_THRESHOLDS.get("RSI_OVERBOUGHT", 70)
    rsi_oversold = AI_THRESHOLDS.get("RSI_OVERSOLD", 30)

    ax3.plot(dates, rsi, label=f"RSI({rsi_period})", color="#2ca02c", linewidth=2)
    ax3.axhline(y=rsi_overbought, color='r', linestyle=':', alpha=0.7, label=f"Перекупленность ({rsi_overbought})")
    ax3.axhline(y=rsi_oversold, color='g', linestyle=':', alpha=0.7, label=f"Перепроданность ({rsi_oversold})")
    ax3.fill_between(dates, rsi_overbought, 100, alpha=0.1, color='red')
    ax3.fill_between(dates, 0, rsi_oversold, alpha=0.1, color='green')

    # Оформление RSI
    ax3.set_ylabel("RSI", fontsize=12, labelpad=10)
    ax3.set_xlabel("Время", fontsize=14, labelpad=10)
    ax3.set_ylim(0, 100)
    ax3.legend(fontsize=11, loc='upper left')
    ax3.grid(alpha=0.2)

    # Форматирование оси X
    # Используем стандартный AutoDateFormatter, он лучше адаптируется
    import matplotlib.dates as mdates
    locator = mdates.AutoDateLocator()
    formatter = mdates.AutoDateFormatter(locator)
    # Настраиваем форматтер чтобы показывать часы и минуты
    formatter.scaled[1/(24*60)] = '%H:%M' # Minutes
    formatter.scaled[1/24] = '%H:%M'      # Hours
    formatter.scaled[1] = '%d %b'         # Days

    ax3.xaxis.set_major_locator(locator)
    ax3.xaxis.set_major_formatter(formatter)

    fig.autofmt_xdate()

    # Убираем отступы по краям (слева и справа)
    if len(dates) > 0:
        ax1.set_xlim(dates[0], dates[-1])

    # Сохраняем с высоким разрешением (перезаписываем файл)
    filename = f"{CHARTS_DIR}/{get_filename(symbol)}.png"
    plt.savefig(filename, dpi=200, bbox_inches='tight')
    plt.close(fig) # Explicitly close the specific figure object

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
