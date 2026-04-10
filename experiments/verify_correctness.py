"""
Проверка корректности оптимизированных индикаторов.

Сравнивает результаты оптимизированных функций с эталонными реализациями.
"""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest.signals import SignalGenerator


def test_rsi_series_vs_naive():
    """Сравнение _calculate_rsi_series с наивным покомпонентным RSI."""
    random.seed(42)
    sg = SignalGenerator("MACDX", {})

    closes = [60000.0]
    for _ in range(200):
        closes.append(closes[-1] + random.uniform(-200, 200))

    # Наивный RSI (оригинальная формула)
    def naive_rsi(closes_subset, period):
        if len(closes_subset) < period + 1:
            return 50
        gains = []
        losses = []
        for i in range(1, len(closes_subset)):
            change = closes_subset[i] - closes_subset[i-1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # Наивные RSI values
    naive_values = []
    for i in range(14, len(closes)):
        naive_values.append(naive_rsi(closes[:i+1], 14))

    # Оптимизированные RSI values
    optimized_values = sg._calculate_rsi_series(closes, 14)

    # Все значения должны совпадать точно (оба используют SMA скользящего окна)
    assert len(naive_values) == len(optimized_values), \
        f"Длины не совпадают: {len(naive_values)} vs {len(optimized_values)}"
    print(f"✅ Длины серий совпадают: {len(optimized_values)}")

    max_diff = 0
    mismatches = 0
    for i in range(len(naive_values)):
        diff = abs(naive_values[i] - optimized_values[i])
        max_diff = max(max_diff, diff)
        if diff > 1e-10:
            mismatches += 1

    assert mismatches == 0, \
        f"RSI не совпадает в {mismatches} точках, макс. отклонение: {max_diff:.6f}"
    print(f"✅ RSI совпадает точно во всех {len(optimized_values)} точках (макс. отклонение: {max_diff:.2e})")


def test_macd_with_prev():
    """Проверка MACD с предыдущим histogram."""
    random.seed(42)
    sg = SignalGenerator("MACDX", {})

    closes = [60000.0]
    for _ in range(200):
        closes.append(closes[-1] + random.uniform(-200, 200))

    # Оптимизированный
    macd_line, macd_signal, macd_hist, macd_hist_prev = sg._calculate_macd_with_prev(closes)

    # Эталонный (старый метод — через _calculate_ema)
    def old_macd(closes):
        if len(closes) < 26:
            return 0, 0, 0
        series = []
        for i in range(26, len(closes) + 1):
            subset = closes[:i]
            ema12 = sg._calculate_ema(subset, 12)
            ema26 = sg._calculate_ema(subset, 26)
            series.append(ema12 - ema26)
        ml = series[-1]
        ms = sg._calculate_ema(series, 9) if len(series) >= 9 else ml
        mh = ml - ms
        return ml, ms, mh

    ref_line, ref_signal, ref_hist = old_macd(closes)
    ref_line_prev, ref_signal_prev, ref_hist_prev = old_macd(closes[:-1])

    print(f"\nMACD Line:    opt={macd_line:.6f} ref={ref_line:.6f} diff={abs(macd_line-ref_line):.8f}")
    print(f"MACD Signal:  opt={macd_signal:.6f} ref={ref_signal:.6f} diff={abs(macd_signal-ref_signal):.8f}")
    print(f"MACD Hist:    opt={macd_hist:.6f} ref={ref_hist:.6f} diff={abs(macd_hist-ref_hist):.8f}")
    print(f"MACD HistPrev:opt={macd_hist_prev:.6f} ref={ref_hist_prev:.6f} diff={abs(macd_hist_prev-ref_hist_prev):.8f}")

    assert abs(macd_line - ref_line) < 0.01, f"MACD line отличается: {macd_line} vs {ref_line}"
    assert abs(macd_signal - ref_signal) < 0.01, f"MACD signal отличается"
    assert abs(macd_hist - ref_hist) < 0.01, f"MACD hist отличается"
    assert abs(macd_hist_prev - ref_hist_prev) < 0.01, f"MACD hist_prev отличается"
    print("✅ MACD совпадает с эталоном")


if __name__ == "__main__":
    test_rsi_series_vs_naive()
    test_macd_with_prev()
    print("\n✅ Все проверки корректности пройдены!")
