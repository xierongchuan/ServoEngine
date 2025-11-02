import json
import os
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import pandas as pd
from config import SYMBOLS, DATA_DIR, CHARTS_DIR
from logger import info, error
from symbols import get_filename

def plot_symbol(symbol):
    """Строит график для символа и сохраняет как PNG"""
    # Загружаем данные
    with open(f"{DATA_DIR}/prices/{get_filename(symbol)}.json") as f:
        prices = json.load(f)
    
    # Подготавливаем данные
    timestamps = [candle["snapshotTimeUTC"] for candle in prices]
    closes = [float(candle["closePrice"]["bid"]) for candle in prices]
    
    # Конвертируем временные метки в datetime объекты
    dates = pd.to_datetime(timestamps)
    
    # Строим график
    plt.figure(figsize=(14, 7))
    plt.plot(dates, closes, label="Цена", color="#1f77b4", linewidth=2)
    
    # Добавляем индикаторы
    if len(closes) >= 20:
        sma = [sum(closes[max(0, i-19):i+1])/min(20, i+1) for i in range(len(closes))]
        plt.plot(dates, sma, label="SMA(20)", color="#ff7f0e", linestyle="--", linewidth=1.5)
    
    # Оформление
    plt.title(f"{symbol} - {datetime.now().strftime('%Y-%m-%d %H:%M')}", fontsize=16, pad=20)
    plt.xlabel("Время", fontsize=12, labelpad=10)
    plt.ylabel("Цена", fontsize=12, labelpad=10)
    plt.legend(fontsize=10)
    plt.grid(alpha=0.2)
    
    # Форматирование оси X
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    plt.gcf().autofmt_xdate()  # Поворот меток
    
    # Добавляем текущую цену в заголовок
    current_price = closes[-1]
    plt.suptitle(f"Текущая цена: {current_price:.5f}", fontsize=14, y=0.96, color="#d62728")
    
    # Сохраняем
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{CHARTS_DIR}/{get_filename(symbol)}_{timestamp}.png"
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()

    info(f"🖼️ График для {symbol} сохранен как {filename}")

def main():
    """Основная функция генерации графиков"""
    info("\n📊 Генерация графиков...")

    # Убеждаемся что директория существует
    os.makedirs(CHARTS_DIR, exist_ok=True)

    for symbol in SYMBOLS:
        try:
            plot_symbol(symbol)
        except Exception as e:
            error(f"❌ Ошибка генерации графика для {symbol}: {str(e)}")

if __name__ == "__main__":
    main()