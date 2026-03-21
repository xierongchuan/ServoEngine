"""
Lightweight Analyzer for SCALP mode.

Provides incremental O(1) indicator computation from WebSocket cache.
Instead of recalculating all indicators from scratch each cycle,
maintains running state and updates incrementally on each new candle.

Indicators:
- EMA(5, 13, 21) — Wilder's smoothing
- RSI(7) — incremental gain/loss tracking
- MACD(6, 13, 5) — from EMA deltas
- ATR(10) + ATR(5) — rolling true range
- Bollinger Bands(20, 2.0)
- VWAP — session-based (00:00 UTC reset)
- Volume ratio — current vs N-bar average
- Choppiness Index(14) — market choppiness detection
- CVD — cumulative volume delta approximation
"""

import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

from src.config import SCALP_SETTINGS
from src.utils.logger import info, warning


class LightweightAnalyzer:
    """Incremental indicator engine for scalp fast loop."""

    def __init__(self, symbol: str, config: Optional[Dict] = None):
        self.symbol = symbol
        cfg = config or SCALP_SETTINGS.get("signal_rules", {})

        # Indicator periods
        self._ema_fast_period = cfg.get("ema_periods", [5, 13])[0]
        self._ema_med_period = cfg.get("ema_periods", [5, 13])[1]
        self._ema_macro_period = cfg.get("ema_macro", 21)
        self._rsi_period = cfg.get("rsi_period", 7)
        self._macd_fast = cfg.get("macd_params", [6, 13, 5])[0]
        self._macd_slow = cfg.get("macd_params", [6, 13, 5])[1]
        self._macd_signal = cfg.get("macd_params", [6, 13, 5])[2]
        self._atr_period = cfg.get("atr_period", 10)
        self._atr_fast_period = cfg.get("atr_fast_period", 5)
        self._bb_period = cfg.get("bb_period", 20)
        self._bb_std = cfg.get("bb_std", 2.0)
        self._vol_avg_window = 10

        # Running state
        self._ema_fast: float = 0.0
        self._ema_med: float = 0.0
        self._ema_macro: float = 0.0
        self._rsi_avg_gain: float = 0.0
        self._rsi_avg_loss: float = 0.0
        self._rsi: float = 50.0
        self._ema_macd_fast: float = 0.0
        self._ema_macd_slow: float = 0.0
        self._ema_macd_signal: float = 0.0
        self._macd_line: float = 0.0
        self._macd_hist: float = 0.0
        self._atr: float = 0.0
        self._atr_fast: float = 0.0

        # BB state (rolling window)
        self._bb_window: deque = deque(maxlen=self._bb_period)
        self._bb_upper: float = 0.0
        self._bb_middle: float = 0.0
        self._bb_lower: float = 0.0
        self._bb_width: float = 0.0

        # VWAP state
        self._vwap_cum_vol: float = 0.0
        self._vwap_cum_tp_vol: float = 0.0
        self._vwap: float = 0.0
        self._vwap_date: str = ""  # Current session date for reset
        self._vwap_cum_sq_vol: float = 0.0  # For VWAP standard deviation

        # Volume state
        self._vol_window: deque = deque(maxlen=self._vol_avg_window)
        self._volume_ratio: float = 1.0

        # ATR rolling windows
        self._tr_window: deque = deque(maxlen=self._atr_period)
        self._tr_fast_window: deque = deque(maxlen=self._atr_fast_period)

        # Momentum tracking (last N closes)
        self._recent_closes: deque = deque(maxlen=5)

        # MACD crossover tracking
        self._prev_macd_hist: float = 0.0

        # Choppiness Index state (CI = 100 * log10(sum(ATR,n)/(HH-LL)) / log10(n))
        self._chop_period = 14
        self._chop_atr_window: deque = deque(maxlen=self._chop_period)
        self._chop_high_window: deque = deque(maxlen=self._chop_period)
        self._chop_low_window: deque = deque(maxlen=self._chop_period)
        self._choppiness: float = 50.0  # 0-100, > 61.8 = choppy

        # CVD (Cumulative Volume Delta) state
        self._cvd: float = 0.0  # Running CVD
        self._cvd_ema: float = 0.0  # EMA of CVD for trend
        self._cvd_ema_period: int = 10
        self._cvd_prev_ema: float = 0.0  # Previous CVD EMA for trend detection
        self._cvd_history: deque = deque(maxlen=20)  # Recent delta values for divergence

        # State flags
        self._bootstrapped = False
        self._prev_close: float = 0.0
        self._candle_count: int = 0
        self._last_timestamp: int = 0

    def bootstrap(self, candles: List[Dict]) -> bool:
        """
        Initialize indicators from historical candle buffer.
        Requires at least max(ema_macro, bb_period, atr_period) + 10 candles.

        Args:
            candles: List of candle dicts with openPrice, highPrice, lowPrice, closePrice, volume, timestamp

        Returns:
            True if bootstrap succeeded
        """
        min_candles = max(self._ema_macro_period, self._bb_period, self._atr_period) + 10
        if len(candles) < min_candles:
            warning(f"[SCALP-LA] {self.symbol}: Need {min_candles} candles for bootstrap, got {len(candles)}")
            return False

        closes = [float(c.get("closePrice", 0)) for c in candles]
        highs = [float(c.get("highPrice", 0)) for c in candles]
        lows = [float(c.get("lowPrice", 0)) for c in candles]
        volumes = [float(c.get("volume", 0)) for c in candles]

        # EMA bootstrap: SMA seed then apply multiplier
        self._ema_fast = sum(closes[:self._ema_fast_period]) / self._ema_fast_period
        self._ema_med = sum(closes[:self._ema_med_period]) / self._ema_med_period
        self._ema_macro = sum(closes[:self._ema_macro_period]) / self._ema_macro_period

        k_fast = 2.0 / (self._ema_fast_period + 1)
        k_med = 2.0 / (self._ema_med_period + 1)
        k_macro = 2.0 / (self._ema_macro_period + 1)

        for c in closes[self._ema_fast_period:]:
            self._ema_fast = c * k_fast + self._ema_fast * (1 - k_fast)
        for c in closes[self._ema_med_period:]:
            self._ema_med = c * k_med + self._ema_med * (1 - k_med)
        for c in closes[self._ema_macro_period:]:
            self._ema_macro = c * k_macro + self._ema_macro * (1 - k_macro)

        # MACD EMA bootstrap
        self._ema_macd_fast = sum(closes[:self._macd_fast]) / self._macd_fast
        self._ema_macd_slow = sum(closes[:self._macd_slow]) / self._macd_slow
        k_mf = 2.0 / (self._macd_fast + 1)
        k_ms = 2.0 / (self._macd_slow + 1)
        for c in closes[self._macd_fast:]:
            self._ema_macd_fast = c * k_mf + self._ema_macd_fast * (1 - k_mf)
        for c in closes[self._macd_slow:]:
            self._ema_macd_slow = c * k_ms + self._ema_macd_slow * (1 - k_ms)

        # MACD signal line bootstrap
        macd_history = []
        ema_f_temp = sum(closes[:self._macd_fast]) / self._macd_fast
        ema_s_temp = sum(closes[:self._macd_slow]) / self._macd_slow

        # Track signal line history for correct prev_histogram calculation
        signal_history = []
        prev_ema_macd_signal = 0.0

        for c in closes[self._macd_slow:]:
            ema_f_temp = c * k_mf + ema_f_temp * (1 - k_mf)
            ema_s_temp = c * k_ms + ema_s_temp * (1 - k_ms)
            macd_history.append(ema_f_temp - ema_s_temp)

        if len(macd_history) >= self._macd_signal:
            self._ema_macd_signal = sum(macd_history[:self._macd_signal]) / self._macd_signal
            k_sig = 2.0 / (self._macd_signal + 1)
            for i, m in enumerate(macd_history[self._macd_signal:]):
                prev_ema_macd_signal = self._ema_macd_signal  # Save previous before update
                self._ema_macd_signal = m * k_sig + self._ema_macd_signal * (1 - k_sig)
                # Store ALL signal line values (not skipping any)
                signal_history.append(self._ema_macd_signal)

        self._macd_line = self._ema_macd_fast - self._ema_macd_slow
        self._macd_hist = self._macd_line - self._ema_macd_signal

        # FIX: Use stored signal_history to get previous signal line
        # signal_history[-1] is current, signal_history[-2] is previous (n-1 period)
        if len(signal_history) >= 2:
            # Use the actual signal line value from (n-1) period that we stored
            self._prev_macd_hist = macd_history[-2] - signal_history[-2]
        elif len(macd_history) >= 2:
            # Not enough signal history, approximate with current signal
            self._prev_macd_hist = macd_history[-2] - self._ema_macd_signal
        else:
            self._prev_macd_hist = self._macd_hist

        # RSI bootstrap: first avg gain/loss over rsi_period, then smooth
        deltas = [closes[i] - closes[i - 1] for i in range(1, self._rsi_period + 1)]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        self._rsi_avg_gain = sum(gains) / self._rsi_period
        self._rsi_avg_loss = sum(losses) / self._rsi_period

        for i in range(self._rsi_period + 1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gain = delta if delta > 0 else 0.0
            loss = -delta if delta < 0 else 0.0
            self._rsi_avg_gain = (self._rsi_avg_gain * (self._rsi_period - 1) + gain) / self._rsi_period
            self._rsi_avg_loss = (self._rsi_avg_loss * (self._rsi_period - 1) + loss) / self._rsi_period

        self._rsi = self._compute_rsi()

        # ATR bootstrap
        for i in range(1, len(candles)):
            tr = self._true_range(highs[i], lows[i], closes[i - 1])
            self._tr_window.append(tr)
            self._tr_fast_window.append(tr)

        self._atr = sum(self._tr_window) / len(self._tr_window) if self._tr_window else 0.0
        self._atr_fast = sum(self._tr_fast_window) / len(self._tr_fast_window) if self._tr_fast_window else 0.0

        # BB bootstrap
        for c in closes[-self._bb_period:]:
            self._bb_window.append(c)
        self._update_bb()

        # VWAP bootstrap — use all candles from current UTC session
        self._vwap_date = self._get_session_date(candles[-1])
        self._vwap_cum_vol = 0.0
        self._vwap_cum_tp_vol = 0.0
        self._vwap_cum_sq_vol = 0.0
        for c in candles:
            cd = self._get_session_date(c)
            if cd != self._vwap_date:
                continue
            tp = (float(c["highPrice"]) + float(c["lowPrice"]) + float(c["closePrice"])) / 3.0
            vol = float(c.get("volume", 0))
            self._vwap_cum_tp_vol += tp * vol
            self._vwap_cum_vol += vol
            self._vwap_cum_sq_vol += tp * tp * vol
        self._vwap = self._vwap_cum_tp_vol / self._vwap_cum_vol if self._vwap_cum_vol > 0 else closes[-1]

        # Choppiness Index bootstrap (last chop_period candles)
        chop_start = max(1, len(candles) - self._chop_period)
        for i in range(chop_start, len(candles)):
            tr = self._true_range(highs[i], lows[i], closes[i - 1])
            self._chop_atr_window.append(tr)
            self._chop_high_window.append(highs[i])
            self._chop_low_window.append(lows[i])
        self._update_choppiness()

        # CVD bootstrap (all candles)
        opens = [float(c.get("openPrice", 0)) for c in candles]
        self._cvd = 0.0
        for i in range(len(candles)):
            delta = self._candle_volume_delta(opens[i], highs[i], lows[i], closes[i], volumes[i])
            self._cvd += delta
            self._cvd_history.append(delta)
        # CVD EMA seed
        if self._cvd_history:
            self._cvd_ema = self._cvd
            k_cvd = 2.0 / (self._cvd_ema_period + 1)
            # Replay last N deltas to get a proper EMA
            cvd_run = 0.0
            history_list = list(self._cvd_history)
            for d in history_list:
                cvd_run += d
            self._cvd_ema = cvd_run
            # Apply EMA smoothing over recent values
            if len(history_list) > self._cvd_ema_period:
                self._cvd_ema = sum(history_list[:self._cvd_ema_period])
                for d_val in history_list[self._cvd_ema_period:]:
                    cumulative = self._cvd_ema + d_val
                    self._cvd_ema = cumulative * k_cvd + self._cvd_ema * (1 - k_cvd)
            self._cvd_prev_ema = self._cvd_ema

        # Volume
        for v in volumes[-self._vol_avg_window:]:
            self._vol_window.append(v)
        avg_vol = sum(self._vol_window) / len(self._vol_window) if self._vol_window else 1.0
        self._volume_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

        # Recent closes for momentum
        for c in closes[-5:]:
            self._recent_closes.append(c)

        self._prev_close = closes[-1]
        self._candle_count = len(candles)
        self._last_timestamp = candles[-1].get("timestamp", 0)
        self._bootstrapped = True

        info(f"[SCALP-LA] {self.symbol}: Bootstrap OK ({len(candles)} candles). "
             f"EMA={self._ema_fast:.2f}/{self._ema_med:.2f}/{self._ema_macro:.2f} "
             f"RSI={self._rsi:.1f} ATR={self._atr:.4f} VWAP={self._vwap:.2f}")
        return True

    def update(self, candle: Dict) -> Dict:
        """
        Update indicators with a new or updated candle. O(1) per call.

        Args:
            candle: Dict with openPrice, highPrice, lowPrice, closePrice, volume, timestamp

        Returns:
            Dict with all current indicator values
        """
        if not self._bootstrapped:
            return self.get_snapshot()

        ts = candle.get("timestamp", 0)
        close = float(candle.get("closePrice", 0))
        high = float(candle.get("highPrice", 0))
        low = float(candle.get("lowPrice", 0))
        volume = float(candle.get("volume", 0))

        is_new_candle = ts != self._last_timestamp

        if is_new_candle:
            # New candle closed — full incremental update
            open_p = float(candle.get("openPrice", 0))
            self._update_emas(close)
            self._update_rsi(close)
            self._update_macd(close)
            self._update_atr(high, low, self._prev_close)
            self._bb_window.append(close)
            self._update_bb()
            self._update_vwap(candle)
            self._update_chop_data(high, low, self._prev_close)
            self._update_cvd(open_p, high, low, close, volume)
            self._vol_window.append(volume)
            avg_vol = sum(self._vol_window) / len(self._vol_window) if self._vol_window else 1.0
            self._volume_ratio = volume / avg_vol if avg_vol > 0 else 1.0
            self._recent_closes.append(close)
            self._prev_close = close
            self._candle_count += 1
            self._last_timestamp = ts
        else:
            # Same candle updating (live tick) — update BB last value, VWAP
            if self._bb_window:
                self._bb_window[-1] = close
                self._update_bb()

        return self.get_snapshot()

    def get_snapshot(self) -> Dict:
        """Return current indicator values as a dict."""
        # Momentum: count up/down candles in recent closes
        last_5 = list(self._recent_closes)
        if len(last_5) >= 2:
            up = sum(1 for i in range(1, len(last_5)) if last_5[i] > last_5[i - 1])
            total = len(last_5) - 1
            if up >= 3:
                momentum_dir = "UP"
            elif total - up >= 3:
                momentum_dir = "DOWN"
            else:
                momentum_dir = "MIXED"
        else:
            momentum_dir = "MIXED"

        # ATR ratio (spike detection)
        atr_ratio = self._atr_fast / self._atr if self._atr > 0 else 1.0

        # VWAP deviation (distance from VWAP in %)
        vwap_dist_pct = ((self._prev_close - self._vwap) / self._vwap * 100) if self._vwap > 0 else 0.0

        # VWAP standard deviation band
        if self._vwap_cum_vol > 0:
            vwap_var = (self._vwap_cum_sq_vol / self._vwap_cum_vol) - self._vwap ** 2
            vwap_std = max(0.0, vwap_var) ** 0.5
        else:
            vwap_std = 0.0

        # MACD crossover detection
        if self._prev_macd_hist <= 0 < self._macd_hist:
            macd_crossover = "BULLISH"
        elif self._prev_macd_hist >= 0 > self._macd_hist:
            macd_crossover = "BEARISH"
        else:
            macd_crossover = "NONE"

        # CVD trend: rising/falling/flat
        if self._cvd_ema > self._cvd_prev_ema * 1.001:
            cvd_trend = "RISING"
        elif self._cvd_ema < self._cvd_prev_ema * 0.999:
            cvd_trend = "FALLING"
        else:
            cvd_trend = "FLAT"

        return {
            "ema_fast": self._ema_fast,
            "ema_med": self._ema_med,
            "ema_macro": self._ema_macro,
            "rsi": self._rsi,
            "macd_line": self._macd_line,
            "macd_hist": self._macd_hist,
            "macd_signal_line": self._ema_macd_signal,
            "macd_crossover": macd_crossover,
            "atr": self._atr,
            "atr_fast": self._atr_fast,
            "atr_ratio": atr_ratio,
            "bb_upper": self._bb_upper,
            "bb_middle": self._bb_middle,
            "bb_lower": self._bb_lower,
            "bb_width": self._bb_width,
            "vwap": self._vwap,
            "vwap_dist_pct": vwap_dist_pct,
            "vwap_upper": self._vwap + vwap_std if vwap_std > 0 else self._vwap,
            "vwap_lower": self._vwap - vwap_std if vwap_std > 0 else self._vwap,
            "volume_ratio": self._volume_ratio,
            "current_price": self._prev_close,
            "momentum_dir": momentum_dir,
            "choppiness": self._choppiness,
            "cvd": self._cvd,
            "cvd_trend": cvd_trend,
            "candle_count": self._candle_count,
            "bootstrapped": self._bootstrapped,
        }

    # --- Private incremental methods ---

    def _update_emas(self, close: float):
        k_fast = 2.0 / (self._ema_fast_period + 1)
        k_med = 2.0 / (self._ema_med_period + 1)
        k_macro = 2.0 / (self._ema_macro_period + 1)
        self._ema_fast = close * k_fast + self._ema_fast * (1 - k_fast)
        self._ema_med = close * k_med + self._ema_med * (1 - k_med)
        self._ema_macro = close * k_macro + self._ema_macro * (1 - k_macro)

    def _update_rsi(self, close: float):
        delta = close - self._prev_close
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        self._rsi_avg_gain = (self._rsi_avg_gain * (self._rsi_period - 1) + gain) / self._rsi_period
        self._rsi_avg_loss = (self._rsi_avg_loss * (self._rsi_period - 1) + loss) / self._rsi_period
        self._rsi = self._compute_rsi()

    def _compute_rsi(self) -> float:
        if self._rsi_avg_loss == 0:
            return 100.0 if self._rsi_avg_gain > 0 else 50.0
        rs = self._rsi_avg_gain / self._rsi_avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _calculate_ema_from_values(self, values: list, period: int) -> float:
        """Calculate EMA from a list of values (not prices)."""
        if len(values) < period:
            return sum(values) / len(values) if values else 0.0

        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period  # Start with SMA

        for value in values[period:]:
            ema = (value - ema) * k + ema

        return ema

    def _update_macd(self, close: float):
        k_f = 2.0 / (self._macd_fast + 1)
        k_s = 2.0 / (self._macd_slow + 1)
        k_sig = 2.0 / (self._macd_signal + 1)
        self._ema_macd_fast = close * k_f + self._ema_macd_fast * (1 - k_f)
        self._ema_macd_slow = close * k_s + self._ema_macd_slow * (1 - k_s)
        self._macd_line = self._ema_macd_fast - self._ema_macd_slow
        self._ema_macd_signal = self._macd_line * k_sig + self._ema_macd_signal * (1 - k_sig)
        self._prev_macd_hist = self._macd_hist
        self._macd_hist = self._macd_line - self._ema_macd_signal

    def _update_atr(self, high: float, low: float, prev_close: float):
        tr = self._true_range(high, low, prev_close)
        self._tr_window.append(tr)
        self._tr_fast_window.append(tr)
        self._atr = sum(self._tr_window) / len(self._tr_window) if self._tr_window else 0.0
        self._atr_fast = sum(self._tr_fast_window) / len(self._tr_fast_window) if self._tr_fast_window else 0.0

    def _update_bb(self):
        if len(self._bb_window) < self._bb_period:
            return
        data = list(self._bb_window)
        mean = sum(data) / len(data)
        variance = sum((x - mean) ** 2 for x in data) / len(data)
        std = variance ** 0.5
        self._bb_middle = mean
        self._bb_upper = mean + self._bb_std * std
        self._bb_lower = mean - self._bb_std * std
        self._bb_width = ((self._bb_upper - self._bb_lower) / mean * 100) if mean > 0 else 0.0

    def _update_vwap(self, candle: Dict):
        session_date = self._get_session_date(candle)
        if session_date != self._vwap_date:
            # New session — reset VWAP
            self._vwap_cum_vol = 0.0
            self._vwap_cum_tp_vol = 0.0
            self._vwap_cum_sq_vol = 0.0
            self._vwap_date = session_date

        high = float(candle.get("highPrice", 0))
        low = float(candle.get("lowPrice", 0))
        close = float(candle.get("closePrice", 0))
        vol = float(candle.get("volume", 0))

        tp = (high + low + close) / 3.0
        self._vwap_cum_tp_vol += tp * vol
        self._vwap_cum_vol += vol
        self._vwap_cum_sq_vol += tp * tp * vol
        self._vwap = self._vwap_cum_tp_vol / self._vwap_cum_vol if self._vwap_cum_vol > 0 else close

    def _update_chop_data(self, high: float, low: float, prev_close: float):
        """Update choppiness index rolling data and recalculate."""
        tr = self._true_range(high, low, prev_close)
        self._chop_atr_window.append(tr)
        self._chop_high_window.append(high)
        self._chop_low_window.append(low)
        self._update_choppiness()

    def _update_choppiness(self):
        """Calculate Choppiness Index from rolling windows.

        CI = 100 * log10(sum(TR, n) / (HH - LL)) / log10(n)
        CI > 61.8 → choppy (mean-reverting), CI < 38.2 → trending
        """
        n = len(self._chop_atr_window)
        if n < 2:
            return

        atr_sum = sum(self._chop_atr_window)
        hh = max(self._chop_high_window)
        ll = min(self._chop_low_window)
        hl_range = hh - ll

        if hl_range <= 0 or atr_sum <= 0:
            self._choppiness = 50.0
            return

        ratio = atr_sum / hl_range
        if ratio <= 0:
            self._choppiness = 50.0
            return

        self._choppiness = 100.0 * math.log10(ratio) / math.log10(n)
        # Clamp to 0-100
        self._choppiness = max(0.0, min(100.0, self._choppiness))

    def _update_cvd(self, open_p: float, high: float, low: float, close: float, volume: float):
        """Update CVD with volume delta from candle classification."""
        delta = self._candle_volume_delta(open_p, high, low, close, volume)
        self._cvd += delta
        self._cvd_history.append(delta)

        # Update CVD EMA
        self._cvd_prev_ema = self._cvd_ema
        k = 2.0 / (self._cvd_ema_period + 1)
        self._cvd_ema = self._cvd * k + self._cvd_ema * (1 - k)

    @staticmethod
    def _candle_volume_delta(open_p: float, high: float, low: float, close: float, volume: float) -> float:
        """Approximate buy/sell volume from candle data.

        Uses the close position within the candle range to estimate
        what fraction of volume was buying vs selling.
        buy_ratio = (close - low) / (high - low)
        delta = volume * (2 * buy_ratio - 1)
        """
        hl_range = high - low
        if hl_range <= 0 or volume <= 0:
            return 0.0
        buy_ratio = (close - low) / hl_range
        return volume * (2.0 * buy_ratio - 1.0)

    @staticmethod
    def _true_range(high: float, low: float, prev_close: float) -> float:
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    @staticmethod
    def _get_session_date(candle: Dict) -> str:
        """Extract date string for VWAP session tracking."""
        ts = candle.get("timestamp", 0)
        if ts:
            return time.strftime('%Y-%m-%d', time.gmtime(ts / 1000))
        snap = candle.get("snapshotTimeUTC", "")
        return snap[:10] if snap else ""
