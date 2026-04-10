"""
Verify whether the original and optimized RSI/MACD implementations produce
identical results. This helps identify the root cause of different backtest
outcomes.
"""
import math
import sys
import os

# Generate some realistic-looking price data
import random
random.seed(42)
prices = [80000.0]
for _ in range(400):
    prices.append(prices[-1] * (1 + random.gauss(0, 0.005)))

# ──────────────────────────────────────────────────────────────
# ORIGINAL RSI implementation (SMA of last N gains/losses)
# ──────────────────────────────────────────────────────────────
def rsi_original(closes, period):
    """Original: SMA of all gains/losses, then take last `period`."""
    if len(closes) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def rsi_values_original(closes, period):
    """Original _rsi_values: call _calculate_rsi on each sub-array."""
    values = []
    for i in range(period, len(closes)):
        values.append(rsi_original(closes[:i+1], period))
    return values


# ──────────────────────────────────────────────────────────────
# OPTIMIZED RSI implementation (Wilder's smoothing)
# ──────────────────────────────────────────────────────────────
def rsi_series_wilder(closes, period):
    """New: Wilder's smoothing — different algorithm!"""
    n = len(closes)
    if n < period + 1:
        return []
    values = []
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change > 0:
            avg_gain += change
        else:
            avg_loss -= change
    avg_gain /= period
    avg_loss /= period
    if avg_loss == 0:
        values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        values.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            values.append(100.0 - (100.0 / (1.0 + rs)))
    return values


# ──────────────────────────────────────────────────────────────
# FIXED RSI: O(n) sliding window matching original SMA algorithm
# ──────────────────────────────────────────────────────────────
def rsi_series_sma(closes, period):
    """O(n) sliding window that matches the original SMA-based RSI exactly."""
    n = len(closes)
    if n < period + 1:
        return []

    # Pre-compute all changes
    changes = [0.0] * n
    for i in range(1, n):
        changes[i] = closes[i] - closes[i - 1]

    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]

    values = []

    # First window: sum gains[1..period] and losses[1..period]
    # This corresponds to rsi_original(closes[:period+1], period)
    # which computes gains/losses for indices 1..period, then takes last `period`
    sum_gain = sum(gains[1:period + 1])
    sum_loss = sum(losses[1:period + 1])

    avg_gain = sum_gain / period
    avg_loss = sum_loss / period
    if avg_loss == 0:
        values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        values.append(100.0 - (100.0 / (1.0 + rs)))

    # Sliding window for subsequent values
    for i in range(period + 1, n):
        # Window moves: remove gains[i-period], add gains[i]
        sum_gain += gains[i] - gains[i - period]
        sum_loss += losses[i] - losses[i - period]
        avg_gain = sum_gain / period
        avg_loss = sum_loss / period
        if avg_loss == 0:
            values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            values.append(100.0 - (100.0 / (1.0 + rs)))

    return values


# ──────────────────────────────────────────────────────────────
# MACD implementations
# ──────────────────────────────────────────────────────────────
def calculate_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
    return ema


def macd_original(closes):
    """Original MACD: recalculate EMA from scratch for each subset."""
    if len(closes) < 26:
        return 0, 0, 0
    series = []
    for i in range(26, len(closes) + 1):
        subset = closes[:i]
        ema12 = calculate_ema(subset, 12)
        ema26 = calculate_ema(subset, 26)
        series.append(ema12 - ema26)
    if not series:
        return 0, 0, 0
    macd_line = series[-1]
    macd_signal = calculate_ema(series, 9) if len(series) >= 9 else macd_line
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist


def calculate_ema_series(closes, period):
    if len(closes) < period:
        return []
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    series = [ema]
    for price in closes[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
        series.append(ema)
    return series


def macd_optimized(closes):
    """Optimized MACD: compute EMA series once."""
    if len(closes) < 26:
        return 0, 0, 0
    ema12_series = calculate_ema_series(closes, 12)
    ema26_series = calculate_ema_series(closes, 26)
    offset = 26 - 12  # = 14
    macd_series = []
    for i in range(len(ema26_series)):
        macd_series.append(ema12_series[i + offset] - ema26_series[i])
    if not macd_series:
        return 0, 0, 0
    macd_line = macd_series[-1]
    if len(macd_series) >= 9:
        signal_multiplier = 2 / (9 + 1)
        signal_ema = sum(macd_series[:9]) / 9
        for val in macd_series[9:]:
            signal_ema = (val * signal_multiplier) + (signal_ema * (1 - signal_multiplier))
        macd_signal = signal_ema
    else:
        macd_signal = macd_line
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist


# ──────────────────────────────────────────────────────────────
# Compare
# ──────────────────────────────────────────────────────────────
print("=" * 60)
print("RSI COMPARISON")
print("=" * 60)

orig_rsi_vals = rsi_values_original(prices, 14)
wilder_rsi_vals = rsi_series_wilder(prices, 14)
sma_rsi_vals = rsi_series_sma(prices, 14)

print(f"Original RSI values: {len(orig_rsi_vals)}")
print(f"Wilder RSI values:   {len(wilder_rsi_vals)}")
print(f"SMA RSI values:      {len(sma_rsi_vals)}")
print()

# Compare first 5 values
n = min(5, len(orig_rsi_vals))
print(f"{'Index':>6} | {'Original':>12} | {'Wilder':>12} | {'SMA O(n)':>12} | {'Orig==Wilder':>12} | {'Orig==SMA':>12}")
print("-" * 80)
for i in range(n):
    eq_w = abs(orig_rsi_vals[i] - wilder_rsi_vals[i]) < 1e-10
    eq_s = abs(orig_rsi_vals[i] - sma_rsi_vals[i]) < 1e-10
    print(f"{i:>6} | {orig_rsi_vals[i]:>12.6f} | {wilder_rsi_vals[i]:>12.6f} | {sma_rsi_vals[i]:>12.6f} | {str(eq_w):>12} | {str(eq_s):>12}")

# Compare last 5 values
print("\n... last 5 values:")
for i in range(len(orig_rsi_vals) - 5, len(orig_rsi_vals)):
    eq_w = abs(orig_rsi_vals[i] - wilder_rsi_vals[i]) < 1e-10
    eq_s = abs(orig_rsi_vals[i] - sma_rsi_vals[i]) < 1e-10
    print(f"{i:>6} | {orig_rsi_vals[i]:>12.6f} | {wilder_rsi_vals[i]:>12.6f} | {sma_rsi_vals[i]:>12.6f} | {str(eq_w):>12} | {str(eq_s):>12}")

# Count differences
rsi_diff_count = sum(1 for i in range(len(orig_rsi_vals)) if abs(orig_rsi_vals[i] - wilder_rsi_vals[i]) > 0.1)
sma_diff_count = sum(1 for i in range(len(orig_rsi_vals)) if abs(orig_rsi_vals[i] - sma_rsi_vals[i]) > 1e-10)
print(f"\nRSI values where Wilder differs from Original by > 0.1: {rsi_diff_count}/{len(orig_rsi_vals)}")
print(f"RSI values where SMA O(n) differs from Original: {sma_diff_count}/{len(orig_rsi_vals)}")

# Count cases where RSI crosses thresholds differently
cross_diff = 0
for i in range(len(orig_rsi_vals)):
    orig_over_80 = orig_rsi_vals[i] > 80
    wilder_over_80 = wilder_rsi_vals[i] > 80
    orig_under_20 = orig_rsi_vals[i] < 20
    wilder_under_20 = wilder_rsi_vals[i] < 20
    if orig_over_80 != wilder_over_80 or orig_under_20 != wilder_under_20:
        cross_diff += 1
print(f"RSI threshold crossings that differ (>80 or <20): {cross_diff}")

print("\n" + "=" * 60)
print("MACD COMPARISON")
print("=" * 60)

ml_o, ms_o, mh_o = macd_original(prices)
ml_n, ms_n, mh_n = macd_optimized(prices)

print(f"Original:  line={ml_o:.10f}, signal={ms_o:.10f}, hist={mh_o:.10f}")
print(f"Optimized: line={ml_n:.10f}, signal={ms_n:.10f}, hist={mh_n:.10f}")
print(f"Line diff:   {abs(ml_o - ml_n):.2e}")
print(f"Signal diff: {abs(ms_o - ms_n):.2e}")
print(f"Hist diff:   {abs(mh_o - mh_n):.2e}")

# Check if MACD is identical
macd_identical = abs(ml_o - ml_n) < 1e-10 and abs(ms_o - ms_n) < 1e-10 and abs(mh_o - mh_n) < 1e-10
print(f"\nMACD identical: {macd_identical}")

print("\n" + "=" * 60)
print("CONCLUSION")
print("=" * 60)
if sma_diff_count == 0:
    print("✅ SMA-based O(n) RSI matches original exactly")
else:
    print("❌ SMA-based O(n) RSI has differences from original")
if rsi_diff_count > 0:
    print(f"❌ Wilder's RSI differs from original in {rsi_diff_count} values")
    print("   This is the root cause of different backtest results!")
if macd_identical:
    print("✅ MACD optimization is numerically identical")
else:
    print("⚠️  MACD has small floating-point differences")
