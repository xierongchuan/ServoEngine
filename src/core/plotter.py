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

from src.config import DATA_DIR, CHARTS_DIR, PLOTTER_RANGES, DEFAULT_PLOTTER_RANGE, CLEANUP_SETTINGS, SYMBOLS, AI_THRESHOLDS, AGGRESSIVE_MODE, BOT_CONFIG, CHART_SETTINGS, TECHNICAL_ANALYSIS
from src.utils.logger import info, error
from src.utils.helpers import get_filename
from src.exchanges.market_data import (
    StreamKey, canonical_symbol, canonical_interval, evaluate_snapshot,
    normalize_candle, interval_start, next_start, timestamp_ms,
)
from src.exchanges.market_data_storage import (
    atomic_publish, list_market_snapshots, read_market_snapshot,
)

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

def resample_data(dates, opens, highs, lows, closes, volumes, timeframe, *, source_interval="1m", now=None):
    """Только явная агрегация полных периодов; пропуски и несовместимый TF — ошибка."""
    timeframe, source_interval = canonical_interval(timeframe), canonical_interval(source_interval)
    columns = (dates, opens, highs, lows, closes, volumes)
    if len({len(values) for values in columns}) != 1:
        raise ValueError("Массивы графика имеют разную длину")
    key = StreamKey("bingx", "perpetual", "BTCUSDT", source_interval)
    indexed = {}
    for date, o, h, l, c, v in zip(*columns):
        row = normalize_candle({"timestamp": timestamp_ms(date), "open": o, "high": h,
                                "low": l, "close": c, "volume": v}, key, now=now)
        indexed[row["timestamp"]] = row
    groups = {}
    ordered = sorted(indexed)
    for previous, current in zip(ordered, ordered[1:]):
        if next_start(previous, source_interval) != current:
            raise ValueError("Агрегация истории с пропусками запрещена")
    for ts in ordered:
        start = interval_start(ts, timeframe)
        if next_start(ts, source_interval) > next_start(start, timeframe):
            raise ValueError("Исходный TF не делит целевой период")
        groups.setdefault(start, []).append(indexed[ts])
    result = ([], [], [], [], [], [])
    for start, rows in sorted(groups.items()):
        if rows[0]["timestamp"] != start or next_start(rows[-1]["timestamp"], source_interval) != next_start(start, timeframe):
            raise ValueError("Нельзя выдать неполный период за агрегированную свечу")
        values = (datetime.fromtimestamp(start / 1000, timezone.utc), rows[0]["openPrice"],
                  max(row["highPrice"] for row in rows), min(row["lowPrice"] for row in rows),
                  rows[-1]["closePrice"], sum(row["volume"] for row in rows))
        for column, value in zip(result, values):
            column.append(value)
    return result

def cleanup_old_files():
    """Удаляет старые файлы графиков и данных"""
    if not CLEANUP_SETTINGS["cleanup_old_charts"]:
        return

    try:
        retention_days = CLEANUP_SETTINGS["charts_retention_days"]
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

        # Очищаем старые графики
        if os.path.exists(CHARTS_DIR):
            removed_count = 0
            for filename in os.listdir(CHARTS_DIR):
                filepath = os.path.join(CHARTS_DIR, filename)
                if os.path.isfile(filepath):
                    file_time = datetime.fromtimestamp(os.path.getctime(filepath), tz=timezone.utc)
                    if file_time < cutoff_date:
                        os.remove(filepath)
                        removed_count += 1

            if removed_count > 0:
                info(f"🧹 Удалено {removed_count} старых графиков (старше {retention_days} дней)")

        # Очищаем старые данные о ценах
        if CLEANUP_SETTINGS["cleanup_old_data"]:
            retention_days = CLEANUP_SETTINGS["data_retention_days"]
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

            prices_dir = f"{DATA_DIR}/prices"
            if os.path.exists(prices_dir):
                removed_count = 0
                for filename in os.listdir(prices_dir):
                    filepath = os.path.join(prices_dir, filename)
                    if os.path.isfile(filepath):
                        file_time = datetime.fromtimestamp(os.path.getctime(filepath), tz=timezone.utc)
                        if file_time < cutoff_date:
                            os.remove(filepath)
                            removed_count += 1

                if removed_count > 0:
                    info(f"🧹 Удалено {removed_count} старых файлов данных (старше {retention_days} дней)")

    except Exception as e:
        error(f"❌ Ошибка при очистке старых файлов: {str(e)}")

def plot_symbol(symbol, time_range=None, current_position=None, *, stream_key=None,
                snapshot=None, config=None, data_dir=None, charts_dir=None):
    """Нативные live свечи: никаких неявных преобразований TF из глобального config."""
    config = config or {}
    data_dir = data_dir or config.get("DATA_DIR", DATA_DIR)
    charts_dir = charts_dir or config.get("CHARTS_DIR", CHARTS_DIR)
    chart_settings = config.get("CHART_SETTINGS", CHART_SETTINGS)
    if stream_key is not None:
        stream_key = StreamKey.from_value(stream_key)
        if canonical_symbol(symbol) != stream_key.symbol:
            raise ValueError("Символ графика не совпадает с источником")
        snapshot = snapshot or read_market_snapshot(data_dir, stream_key, allow_stale=True)
        if snapshot is None:
            info(f"⚠️ Нет snapshot для графика {stream_key.id}")
            return
    if snapshot is None:
        candidates = [value for value in list_market_snapshots(data_dir)
                      if value["stream"]["symbol"] == canonical_symbol(symbol)]
        if len(candidates) > 1:
            error(f"❌ Неоднозначный источник графика {symbol}; передайте StreamKey")
            return
        snapshot = candidates[0] if candidates else None
    if snapshot is not None:
        snapshot = evaluate_snapshot(snapshot)
        source_key = StreamKey.from_value(snapshot["stream"])
        if source_key.symbol != canonical_symbol(symbol) or (stream_key and source_key != stream_key):
            raise ValueError("Источник snapshot не совпадает с графиком")
        stream_key = source_key
        prices = snapshot["candles"]
        if current_position and (current_position.get("exchange") != source_key.exchange or
                                 current_position.get("market_type") != source_key.market_type):
            current_position = None
    else:
        price_file = f"{data_dir}/prices/{get_filename(symbol)}.json"
        if not os.path.exists(price_file):
            info(f"📊 Файл {price_file} ещё не создан")
            return
        with open(price_file) as f:
            prices = json.load(f)
    if not prices:
        return

    # Determine time range settings
    if time_range is None:
        time_range = DEFAULT_PLOTTER_RANGE

    plotter_ranges = config.get("PLOTTER_RANGES", PLOTTER_RANGES)
    range_config = plotter_ranges.get(time_range)

    if not range_config:
        # Try case-insensitive match
        for key, val in plotter_ranges.items():
            if key.lower() == time_range.lower():
                range_config = val
                time_range = key # Update to canonical name for display
                break

    if not range_config:
        info(f"⚠️ Range '{time_range}' not found in config. Using default 1D.")
        range_config = plotter_ranges.get("1D") or {"days": 1}
        time_range = "1D"

    # Calculate cutoff time
    # Use timezone-aware UTC
    now = (datetime.fromtimestamp(prices[-1]["timestamp"] / 1000, timezone.utc)
       if snapshot is not None else datetime.now(timezone.utc))
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
            if snapshot is not None:
                ts_dt = datetime.fromtimestamp(candle["timestamp"] / 1000, timezone.utc)
            elif ts_str.endswith('Z'):
                ts_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            else:
                ts_dt = datetime.fromisoformat(ts_str)
                ts_dt = ts_dt.replace(tzinfo=timezone.utc) if ts_dt.tzinfo is None else ts_dt.astimezone(timezone.utc)
        except ValueError:
            try:
                from dateutil import parser
                ts_dt = parser.parse(ts_str)
                ts_dt = ts_dt.replace(tzinfo=timezone.utc) if ts_dt.tzinfo is None else ts_dt.astimezone(timezone.utc)
            except Exception:
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

    # В том числе legacy данные сортируем/дедуплицируем, но не угадываем их таймфрейм.
    indexed = {dt: row for dt, row in zip(all_dates, zip(all_opens, all_highs, all_lows, all_closes, all_volumes))}
    all_dates = sorted(indexed)
    all_opens, all_highs, all_lows, all_closes, all_volumes = map(list, zip(*(indexed[dt] for dt in all_dates)))

    # Calculate indicators on FULL filtered/resampled dataset
    # SMAs
    all_smas = {}
    sma_periods = chart_settings.get("sma_periods", [10, 20, 50, 100, 200])
    for period in sma_periods:
        if len(all_closes) >= period:
            all_smas[period] = [sum(all_closes[max(0, i-period+1):i+1])/min(period, i+1) for i in range(len(all_closes))]
        else:
            all_smas[period] = [sum(all_closes) / len(all_closes)] * len(all_closes)

    # RSI
    rsi_period = config.get("AI_THRESHOLDS", AI_THRESHOLDS).get("RSI_PERIOD", 14)
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

        # Use UTC naive for plotting to ensure matplotlib doesn't auto-convert to system local
        ts_naive = ts_dt.replace(tzinfo=None)
        dates.append(ts_naive)

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
    seb_length = TECHNICAL_ANALYSIS.get("seb_length", 20)
    seb_mult = TECHNICAL_ANALYSIS.get("seb_multiplier", 2.0)

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
    _chart_height = chart_settings.get("chart_height", 13.5)
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(chart_width, _chart_height), gridspec_kw={'height_ratios': [3, 1, 1]}, sharex=True)

    # ... (Prepare plotting data arrays for filtered range) ...
    plot_seb_upper = []
    plot_seb_lower = []
    plot_seb_mid = []

    # Loop over all_dates to extract the relevant slice for plotting (filtering by cutoff_time)
    # Note: 'dates' list is already filtered above, but we need to filter SEB arrays index-wise

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
    if stream_key is not None:
        timestamp = prices[-1]["timestamp"]
        width = (next_start(timestamp, stream_key.interval) - timestamp) / 86_400_000 * 0.8
    elif len(dates) > 1:
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
        ax1.plot(dates, smas[period], label=f"SMA({period})", color=sma_colors.get(period, '#9e9e9e'), linewidth=1.5, alpha=0.9)

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
    source_label = (f"{stream_key.exchange}/{stream_key.market_type} {stream_key.interval}"
                    if stream_key else "legacy: источник/TF неизвестен")
    data_status = ("STALE" if snapshot and snapshot["stale"] else
                   "ERROR" if snapshot and snapshot.get("error") else
                   "GAPS" if snapshot and snapshot.get("gaps") else
                   "RECOVERING" if snapshot and not snapshot["ready"] else
                   "LIVE" if snapshot else "UNKNOWN")
    ax1.set_title(f"{symbol} — {source_label} ({time_range}) [{data_status}]", fontsize=20, pad=40)

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
            pos_text = "POS: LONG"
            pos_color = '#00c853' # Green
        elif side == "SHORT":
            pos_text = "POS: SHORT"
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

    volume_label = "Объём (контракты)" if stream_key and stream_key.exchange == "mexc" and stream_key.market_type == "perpetual" else "Объём"
    ax2.set_ylabel(volume_label, fontsize=12, labelpad=10)
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
    # Используем стандартный AutoDateFormatter с явным UTC
    locator = mdates.AutoDateLocator()
    formatter = mdates.AutoDateFormatter(locator, tz=timezone.utc)
    # Настраиваем форматтер чтобы показывать часы и минуты
    formatter.scaled[1/(24*60)] = '%H:%M' # Minutes
    formatter.scaled[1/24] = '%H:%M'      # Hours
    formatter.scaled[1] = '%d %b'         # Days

    ax3.xaxis.set_major_locator(locator)
    ax3.xaxis.set_major_formatter(formatter)

    fig.autofmt_xdate()

    # Убираем отступы по краям (слева и справа)
    if len(dates) > 1:
        ax1.set_xlim(dates[0], dates[-1])

    # Источники/TF не перезаписывают PNG друг друга; FileResponse видит целый inode.
    basename = (f"{stream_key.exchange}_{stream_key.market_type}_{get_filename(stream_key.symbol)}_{stream_key.interval}"
                if stream_key else get_filename(symbol))
    filename = os.path.join(charts_dir, f"{basename}.png")
    _dpi = chart_settings.get("dpi", 200)
    try:
        atomic_publish(filename, lambda stream: fig.savefig(stream, format="png", dpi=_dpi, bbox_inches="tight"))
    finally:
        plt.close(fig)

    info(f"🖼️ График для {symbol} сохранён как {filename} (диапазон: {time_range})")
    return filename

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
