"""
Профилирование SignalGenerator.calculate_indicators для выявления узких мест.

Генерирует синтетические свечи и измеряет время расчёта индикаторов.
"""
import sys
import os
import time
import random
import cProfile
import pstats
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest.signals import SignalGenerator

def generate_synthetic_klines(n: int = 337) -> list:
    """Генерирует n синтетических свечей."""
    klines = []
    price = 60000.0
    for i in range(n):
        change = random.uniform(-200, 200)
        open_p = price
        close_p = price + change
        high_p = max(open_p, close_p) + random.uniform(0, 100)
        low_p = min(open_p, close_p) - random.uniform(0, 100)
        klines.append({
            "openPrice": open_p,
            "closePrice": close_p,
            "highPrice": high_p,
            "lowPrice": low_p,
            "volume": random.uniform(100, 10000),
            "snapshotTimeUTC": f"2024-01-01T{i:05d}",
        })
        price = close_p
    return klines


def benchmark_calculate_indicators(n_candles: int = 337):
    """Бенчмарк: calculate_indicators на каждой свече."""
    sg = SignalGenerator("MACDX", {})
    klines = generate_synthetic_klines(n_candles)

    start = time.time()
    for i in range(n_candles):
        sg.calculate_indicators(klines, i)
    elapsed = time.time() - start
    print(f"calculate_indicators x {n_candles} свечей: {elapsed:.2f}s ({elapsed/n_candles*1000:.1f}ms/свеча)")
    return elapsed


def profile_calculate_indicators(n_candles: int = 337):
    """cProfile: найти самые дорогие функции."""
    sg = SignalGenerator("MACDX", {})
    klines = generate_synthetic_klines(n_candles)

    pr = cProfile.Profile()
    pr.enable()
    for i in range(n_candles):
        sg.calculate_indicators(klines, i)
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    print(s.getvalue())


if __name__ == "__main__":
    random.seed(42)
    print("=== Бенчмарк (337 свечей) ===")
    benchmark_calculate_indicators(337)
    print("\n=== Профиль (337 свечей) ===")
    random.seed(42)
    profile_calculate_indicators(337)
