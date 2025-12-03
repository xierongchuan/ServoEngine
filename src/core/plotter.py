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

from src.config import DATA_DIR, CHARTS_DIR, CHART_RANGES, DEFAULT_CHART_RANGE, CLEANUP_SETTINGS
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

def plot_symbol(symbol):
    """Строит график для символа и сохраняет как PNG"""
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)

    # Подготавливаем данные
    timestamps = [candle["snapshotTimeUTC"] for candle in prices]
    
    # Handle different price formats (Capital.com dict vs BingX float)
    closes = []
    for candle in prices:
        price_data = candle["closePrice"]
        if isinstance(price_data, dict):
            closes.append(float(price_data["bid"]))
        else:
            closes.append(float(price_data))

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
    sma_period = AI_THRESHOLDS["SMA_PERIOD"]
    rsi_period = AI_THRESHOLDS["RSI_PERIOD"]

    # SMA
    if len(closes) >= sma_period:
        sma = [sum(closes[max(0, i-sma_period+1):i+1])/min(sma_period, i+1)
               for i in range(len(closes))]
    else:
        sma = [sum(closes) / len(closes)] * len(closes)

    # RSI
    rsi = calculate_rsi(closes, rsi_period)

    # Создаем фигуру с двумя subplot'ами
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [3, 1]})

    # Верхний график - цена и SMA
    ax1.plot(dates, closes, label="Цена", color="#1f77b4", linewidth=2)
    ax1.plot(dates, sma, label=f"SMA({sma_period})", color="#ff7f0e", linestyle="--", linewidth=1.5)

    # Оформление верхнего графика
    ax1.set_title(f"{symbol} - {datetime.now().strftime('%Y-%m-%d %H:%M')}", fontsize=16, pad=20)
    ax1.set_ylabel("Цена", fontsize=12, labelpad=10)
    ax1.legend(fontsize=10, loc='upper left')
    ax1.grid(alpha=0.2)

    # Добавляем текущую цену (перемещаем в правый верхний угол)
    current_price = closes[-1]
    ax1.text(0.98, 0.98, f"Текущая цена: {current_price:.5f}",
             transform=ax1.transAxes, fontsize=12,
             verticalalignment='top', horizontalalignment='right',
             bbox=dict(boxstyle='round', facecolor='#d62728', alpha=0.1))

    # Нижний график - RSI
    ax2.plot(dates, rsi, label=f"RSI({rsi_period})", color="#2ca02c", linewidth=2)
    ax2.axhline(y=70, color='r', linestyle=':', alpha=0.7, label="Перекупленность (70)")
    ax2.axhline(y=30, color='g', linestyle=':', alpha=0.7, label="Перепроданность (30)")
    ax2.fill_between(dates, 70, 100, alpha=0.1, color='red')
    ax2.fill_between(dates, 0, 30, alpha=0.1, color='green')

    # Оформление нижнего графика
    ax2.set_ylabel("RSI", fontsize=12, labelpad=10)
    ax2.set_xlabel("Время", fontsize=12, labelpad=10)
    ax2.set_ylim(0, 100)
    ax2.legend(fontsize=9, loc='upper left')
    ax2.grid(alpha=0.2)

    # Форматирование оси X для обоих графиков
    for ax in [ax1, ax2]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        fig.autofmt_xdate()  # Поворот меток для всего figure

    # Сохраняем
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{CHARTS_DIR}/{get_filename(symbol)}_{timestamp}.png"
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()

    info(f"🖼️ График для {symbol} сохранен как {filename}")

def main():
    """Основная функция генерации графиков"""
    info("📊 Генерация графиков...")

    # Убеждаемся что директория существует
    os.makedirs(CHARTS_DIR, exist_ok=True)

    # Очищаем старые файлы
    cleanup_old_files()

    for symbol in SYMBOLS:
        try:
            plot_symbol(symbol)
        except Exception as e:
            error(f"❌ Ошибка генерации графика для {symbol}: {str(e)}")

if __name__ == "__main__":
    main()