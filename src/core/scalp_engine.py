"""
ScalpEngine — Dual-loop scalping engine for SCALP mode.

Architecture:
  Fast Loop (main thread, 1.5s): position management + signal detection
  Slow Loop (daemon thread, 45s): full analysis, regime detection, AI veto

Key classes:
  ScalpEngine — orchestrator with dual-loop architecture
  TrailingStopManager — ATR-based trailing stop with breakeven
  ScalpSession — risk limits, cooldowns, daily/hourly loss tracking
"""

import json
import os
import time
import threading
import traceback
from typing import Dict, Optional

from src.config import SCALP_SETTINGS, ERROR_HANDLING, DATA_DIR
from src.config import should_reload_config, reload_bot_config
from src.config import AI_VETO_OVERRIDE, AI_REGIME_OVERRIDE, AI_MODEL
from src.utils.logger import info, error, warning
from src.utils.helpers import get_filename


class TrailingStopManager:
    """ATR-based trailing stop with breakeven capability."""

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or SCALP_SETTINGS.get("sl_tp", {})
        self._sl_atr_mult = cfg.get("sl_atr_mult", 1.0)
        self._tp_atr_mult = cfg.get("tp_atr_mult", 3.0)
        self._trail_activation_mult = cfg.get("trailing_activation_mult", 1.5)
        self._trail_distance_mult = cfg.get("trailing_distance_mult", 0.5)

        be_cfg = SCALP_SETTINGS.get("breakeven", {})
        self._be_enabled = be_cfg.get("enabled", True)
        self._be_trigger_pct = be_cfg.get("trigger_pct", 0.3)
        self._be_fee_buffer_pct = be_cfg.get("fee_buffer_pct", 0.05)

        # State per position
        self._initial_sl: float = 0.0
        self._initial_tp: float = 0.0
        self._current_sl: float = 0.0
        self._trailing_active: bool = False
        self._best_price: float = 0.0
        self._be_applied: bool = False
        self._entry_price: float = 0.0
        self._pos_side: str = ""

        # Throttle: min change 0.05%, max 1 update per 5s
        self._last_sl_update_time: float = 0.0
        self._min_sl_change_pct: float = 0.05
        self._min_update_interval: float = 5.0

    def init_position(self, side: str, entry_price: float, atr: float):
        """Initialize trailing stop for a new position."""
        self._pos_side = side
        self._entry_price = entry_price
        self._best_price = entry_price

        if side == "BUY":
            self._initial_sl = entry_price - atr * self._sl_atr_mult
            self._initial_tp = entry_price + atr * self._tp_atr_mult
        else:
            self._initial_sl = entry_price + atr * self._sl_atr_mult
            self._initial_tp = entry_price - atr * self._tp_atr_mult

        self._current_sl = self._initial_sl
        self._trailing_active = False
        self._be_applied = False
        self._last_sl_update_time = 0.0

        info(f"[TRAIL] Init: {side} entry={entry_price:.2f} SL={self._current_sl:.2f} "
             f"TP={self._initial_tp:.2f} ATR={atr:.4f}")

    def update(self, current_price: float, atr: float) -> Optional[float]:
        """
        Update trailing stop based on current price.

        Returns:
            New SL price if it should be updated on exchange, None if no update needed.
        """
        if not self._pos_side:
            return None

        now = time.time()
        new_sl = None

        if self._pos_side == "BUY":
            # Track best price
            if current_price > self._best_price:
                self._best_price = current_price

            # Check breakeven trigger
            pnl_pct = (current_price - self._entry_price) / self._entry_price * 100
            if self._be_enabled and not self._be_applied and pnl_pct >= self._be_trigger_pct:
                be_sl = self._entry_price * (1 + self._be_fee_buffer_pct / 100)
                if be_sl > self._current_sl:
                    self._current_sl = be_sl
                    self._be_applied = True
                    new_sl = self._current_sl
                    info(f"[TRAIL] Breakeven applied: SL→{new_sl:.2f} (+{self._be_fee_buffer_pct}% buffer)")

            # Check trailing activation
            profit_in_atr = (self._best_price - self._entry_price) / atr if atr > 0 else 0
            if profit_in_atr >= self._trail_activation_mult:
                self._trailing_active = True
                trail_sl = self._best_price - atr * self._trail_distance_mult
                if trail_sl > self._current_sl:
                    self._current_sl = trail_sl
                    new_sl = self._current_sl

        else:  # SELL
            if current_price < self._best_price:
                self._best_price = current_price

            pnl_pct = (self._entry_price - current_price) / self._entry_price * 100
            if self._be_enabled and not self._be_applied and pnl_pct >= self._be_trigger_pct:
                be_sl = self._entry_price * (1 - self._be_fee_buffer_pct / 100)
                if be_sl < self._current_sl:
                    self._current_sl = be_sl
                    self._be_applied = True
                    new_sl = self._current_sl
                    info(f"[TRAIL] Breakeven applied: SL→{new_sl:.2f} (-{self._be_fee_buffer_pct}% buffer)")

            profit_in_atr = (self._entry_price - self._best_price) / atr if atr > 0 else 0
            if profit_in_atr >= self._trail_activation_mult:
                self._trailing_active = True
                trail_sl = self._best_price + atr * self._trail_distance_mult
                if trail_sl < self._current_sl:
                    self._current_sl = trail_sl
                    new_sl = self._current_sl

        # Throttle SL updates
        if new_sl is not None:
            if now - self._last_sl_update_time < self._min_update_interval:
                return None
            change_pct = abs(new_sl - self._initial_sl) / self._entry_price * 100
            if change_pct < self._min_sl_change_pct and self._last_sl_update_time > 0:
                return None
            self._last_sl_update_time = now

        return new_sl

    def reset(self):
        """Reset state when position is closed."""
        self._pos_side = ""
        self._entry_price = 0.0
        self._best_price = 0.0
        self._initial_sl = 0.0
        self._initial_tp = 0.0
        self._current_sl = 0.0
        self._trailing_active = False
        self._be_applied = False

    @property
    def current_sl(self) -> float:
        return self._current_sl

    @property
    def initial_tp(self) -> float:
        return self._initial_tp

    @property
    def is_trailing(self) -> bool:
        return self._trailing_active


class ScalpSession:
    """Tracks session state, risk limits, cooldowns, and session-aware sizing for a symbol."""

    def __init__(self, symbol: str, config: Optional[Dict] = None,
                 session_config: Optional[Dict] = None):
        self.symbol = symbol
        cfg = config or SCALP_SETTINGS.get("risk_limits", {})

        self._max_consec_losses = cfg.get("max_consecutive_losses", 5)
        self._consec_cooldown_min = cfg.get("consecutive_loss_cooldown_minutes", 30)
        self._daily_loss_limit = cfg.get("daily_loss_limit_pct", 3.0)
        self._hourly_loss_limit = cfg.get("hourly_loss_limit_pct", 1.0)
        self._max_trades_per_hour = cfg.get("max_trades_per_hour", 6)
        self._max_trades_per_day = cfg.get("max_trades_per_day", 50)
        self._min_cooldown_sec = cfg.get("min_cooldown_seconds", 120)

        # Session-aware trading config
        sa_cfg = session_config or SCALP_SETTINGS.get("session_awareness", {})
        self._session_aware_enabled = sa_cfg.get("enabled", False)
        self._peak_hours = sa_cfg.get("peak_hours_utc", [14, 19])
        self._normal_hours = sa_cfg.get("normal_hours_utc", [8, 14])
        self._reduced_size_factor = sa_cfg.get("reduced_size_factor", 0.5)
        self._weekend_size_factor = sa_cfg.get("weekend_size_factor", 0.5)

        # Session state
        self._consecutive_losses: int = 0
        self._daily_pnl_pct: float = 0.0
        self._hourly_pnl_pct: float = 0.0
        self._trades_today: int = 0
        self._trades_this_hour: int = 0
        self._last_trade_time: float = 0.0
        self._paused_until: float = 0.0
        self._session_date: str = ""
        self._session_hour: int = -1
        self._wins: int = 0
        self._losses: int = 0

    def can_trade(self) -> tuple:
        """
        Check if trading is allowed.

        Returns:
            (allowed: bool, reason: str)
        """
        now = time.time()

        # Check pause
        if now < self._paused_until:
            remaining = (self._paused_until - now) / 60
            return False, f"Paused ({remaining:.1f}min left)"

        # Reset daily/hourly counters
        self._check_reset()

        # Cooldown between trades
        if self._last_trade_time > 0 and now - self._last_trade_time < self._min_cooldown_sec:
            remaining = self._min_cooldown_sec - (now - self._last_trade_time)
            return False, f"Cooldown ({remaining:.0f}s)"

        # Daily loss limit
        if self._daily_pnl_pct <= -self._daily_loss_limit:
            return False, f"Daily loss limit ({self._daily_pnl_pct:.1f}%)"

        # Hourly loss limit
        if self._hourly_pnl_pct <= -self._hourly_loss_limit:
            return False, f"Hourly loss limit ({self._hourly_pnl_pct:.1f}%)"

        # Trade count limits
        if self._trades_today >= self._max_trades_per_day:
            return False, f"Daily trade limit ({self._trades_today})"

        if self._trades_this_hour >= self._max_trades_per_hour:
            return False, f"Hourly trade limit ({self._trades_this_hour})"

        return True, "OK"

    def record_entry(self):
        """Record a trade entry."""
        self._last_trade_time = time.time()
        self._trades_today += 1
        self._trades_this_hour += 1

    def record_exit(self, pnl_pct: float):
        """Record a trade exit with its P/L."""
        self._daily_pnl_pct += pnl_pct
        self._hourly_pnl_pct += pnl_pct

        if pnl_pct >= 0:
            self._consecutive_losses = 0
            self._wins += 1
        else:
            self._consecutive_losses += 1
            self._losses += 1

        # Check consecutive loss pause
        if self._consecutive_losses >= self._max_consec_losses:
            pause_sec = self._consec_cooldown_min * 60
            self._paused_until = time.time() + pause_sec
            warning(f"[SCALP-SESSION] {self.symbol}: {self._consecutive_losses} consecutive losses, "
                    f"pausing {self._consec_cooldown_min}min")

        info(f"[SCALP-SESSION] {self.symbol}: Trade closed PnL={pnl_pct:.2f}% | "
             f"Daily={self._daily_pnl_pct:.2f}% | W/L={self._wins}/{self._losses} | "
             f"Consec losses={self._consecutive_losses}")

    def _check_reset(self):
        """Reset counters on new day/hour."""
        now = time.gmtime()
        current_date = time.strftime('%Y-%m-%d', now)
        current_hour = now.tm_hour

        if current_date != self._session_date:
            self._session_date = current_date
            self._daily_pnl_pct = 0.0
            self._trades_today = 0
            self._wins = 0
            self._losses = 0
            info(f"[SCALP-SESSION] {self.symbol}: New day, counters reset")

        if current_hour != self._session_hour:
            self._session_hour = current_hour
            self._hourly_pnl_pct = 0.0
            self._trades_this_hour = 0

    def get_session_size_factor(self) -> float:
        """
        Return position size multiplier based on time of day and day of week.

        Peak hours (14-19 UTC, US market): 1.0
        Normal hours (8-14 UTC, EU market): 1.0
        Off-peak hours (other): reduced_size_factor
        Weekend (Sat/Sun): weekend_size_factor
        """
        if not self._session_aware_enabled:
            return 1.0

        now = time.gmtime()
        hour = now.tm_hour
        weekday = now.tm_wday  # 0=Monday, 6=Sunday

        # Weekend check (Saturday=5, Sunday=6)
        if weekday >= 5:
            return self._weekend_size_factor

        # Time-of-day check
        peak_start, peak_end = self._peak_hours[0], self._peak_hours[1]
        normal_start, normal_end = self._normal_hours[0], self._normal_hours[1]

        if peak_start <= hour < peak_end:
            return 1.0  # Peak hours — full size
        elif normal_start <= hour < normal_end:
            return 1.0  # Normal hours — full size
        else:
            return self._reduced_size_factor  # Off-peak — reduced

    @property
    def stats(self) -> Dict:
        return {
            "trades_today": self._trades_today,
            "daily_pnl_pct": self._daily_pnl_pct,
            "wins": self._wins,
            "losses": self._losses,
            "consecutive_losses": self._consecutive_losses,
        }


class ScalpEngine:
    """
    Dual-loop scalping engine.

    Fast loop (1.5s, main thread):
      - Get candles from WS cache
      - Compute incremental indicators
      - If position: trailing stop, breakeven, time exit, exit signals
      - If no position: generate signal, execute or queue for veto

    Slow loop (45s, daemon thread):
      - Sync position from exchange
      - Full analysis (regime detection)
      - Process AI veto for pending signals
      - Update session stats
    """

    def __init__(self, symbol: str, ws_cache=None, ws_ready=None):
        self.symbol = symbol
        self._ws_cache = ws_cache
        self._ws_ready = ws_ready

        cfg = SCALP_SETTINGS
        loop_cfg = cfg.get("loops", {})
        self._fast_interval = loop_cfg.get("fast_interval", 1.5)
        self._slow_interval = loop_cfg.get("slow_interval", 45)

        time_exit_cfg = cfg.get("time_exit", {})
        self._max_hold_minutes = time_exit_cfg.get("max_hold_minutes", 15)
        self._be_timeout_minutes = time_exit_cfg.get("breakeven_timeout_minutes", 8)

        ai_cfg = cfg.get("ai_integration", {})
        self._regime_ai_enabled = ai_cfg.get("regime_enabled", True)
        self._regime_interval_sec = ai_cfg.get("regime_interval_seconds", 300)
        # AI model overrides from AI_SETTINGS.overrides
        self._regime_model = AI_REGIME_OVERRIDE.get("model", None) or AI_MODEL
        self._regime_temperature = AI_REGIME_OVERRIDE.get("temperature", 0.2)
        self._regime_max_tokens = AI_REGIME_OVERRIDE.get("max_tokens", 150)
        self._veto_enabled = ai_cfg.get("veto_enabled", True)
        self._veto_model = AI_VETO_OVERRIDE.get("model", None) or AI_MODEL
        self._veto_temperature = AI_VETO_OVERRIDE.get("temperature", 0.1)
        self._veto_max_tokens = AI_VETO_OVERRIDE.get("max_tokens", 100)
        self._veto_staleness_sec = ai_cfg.get("veto_staleness_seconds", 10)
        self._veto_max_cycles = ai_cfg.get("veto_max_stale_cycles", 2)
        self._borderline_quality = ai_cfg.get("borderline_quality_threshold", 0.3)

        # Limit order config
        limit_cfg = cfg.get("limit_entries", {})
        self._limit_orders_enabled = limit_cfg.get("enabled", False)
        self._limit_offset_bps = limit_cfg.get("offset_bps", 1.0)  # Offset from mid-price in basis points
        self._limit_timeout_sec = limit_cfg.get("timeout_seconds", 5)

        # Partial TP config
        partial_cfg = cfg.get("partial_tp", {})
        self._partial_tp_enabled = partial_cfg.get("enabled", False)
        self._partial_tp_atr_mult = partial_cfg.get("atr_mult", 1.5)  # TP1 at 1.5x ATR
        self._partial_tp_pct = partial_cfg.get("close_pct", 0.5)  # Close 50% at TP1

        # Components
        self._trailing = TrailingStopManager()
        self._session = ScalpSession(symbol)

        # Spread filter
        self._spread_max_bps = SCALP_SETTINGS.get("signal_rules", {}).get("spread_max_bps", 5.0)

        # Shared state between fast/slow loops (protected by lock)
        self._lock = threading.Lock()
        self._regime: Optional[Dict] = None
        self._position: Optional[Dict] = None
        self._position_open_time: float = 0.0
        self._pending_veto: Optional[Dict] = None
        self._pending_veto_cycle: int = 0  # Fast loop cycle when veto was queued
        self._ob_imbalance: float = 0.0
        self._ob_spread_bps: float = 0.0
        self._running = True

        # AI regime advisor state
        self._last_ai_regime_time: float = 0.0
        self._ai_regime_duration: int = 0  # How many cycles in current AI regime
        self._ai_regime_label: str = "UNKNOWN"

        # Fast loop cycle counter (for veto staleness)
        self._fast_cycle: int = 0

        # Partial TP state
        self._partial_tp_done: bool = False
        self._entry_atr: float = 0.0

        # Calibration interval (slow loop cycles between checks)
        perf_cfg = cfg.get("performance", {})
        self._calibration_interval = perf_cfg.get("calibration_interval_cycles", 100)
        self._slow_cycle: int = 0

        # === Logging state (Task 1: Fast loop status) ===
        self._last_status_cycle: int = 0
        self._status_interval_cycles: int = 45  # ~67.5s at 1.5s/cycle
        self._last_score: int = 0
        self._last_max_score: int = 0

        # === Signal rejection tracking (Task 4) ===
        self._rejection_counts: Dict[str, int] = {}
        self._rejection_window_cycles: int = 60  # Summary every ~90s
        self._last_rejection_log_cycle: int = 0

        # === Veto skip tracking (Task 3) ===
        self._veto_skip_counter: int = 0
        self._veto_skip_reasons: Dict[str, int] = {}
        self._last_veto_skip_log_cycle: int = 0
        self._veto_skip_log_interval: int = 40  # ~60s

        # Lazy-loaded components
        self._analyzer = None
        self._signal_gen = None
        self._client = None
        self._tracker = None
        self._perf_tracker = None
        self._calibrator = None

    def run(self):
        """Main entry point — runs until killed."""
        info(f"[SCALP] {self.symbol}: ScalpEngine starting (fast={self._fast_interval}s, slow={self._slow_interval}s)")

        # Initialize components
        self._init_components()

        # Start slow loop in daemon thread
        slow_thread = threading.Thread(target=self._slow_loop, daemon=True)
        slow_thread.start()

        # Run fast loop on main thread
        self._fast_loop()

    def _init_components(self):
        """Lazy-initialize all dependencies."""
        from src.core.lightweight_analyzer import LightweightAnalyzer
        from src.core.scalp_signal import ScalpSignalGenerator
        from src.exchanges.exchange_factory import get_exchange_client
        from src.core.trade_tracker import TradeTracker

        from src.core.scalp_performance import ScalpPerformanceTracker, ScalpCalibrator

        self._analyzer = LightweightAnalyzer(self.symbol)
        self._signal_gen = ScalpSignalGenerator()
        self._client = get_exchange_client()
        self._tracker = TradeTracker()
        self._perf_tracker = ScalpPerformanceTracker()
        self._calibrator = ScalpCalibrator(self._perf_tracker)

        # Bootstrap analyzer from WS cache or REST
        candles = self._get_candles(300)
        if candles:
            self._analyzer.bootstrap(candles)
        else:
            warning(f"[SCALP] {self.symbol}: No candles for bootstrap, will retry in slow loop")

        # Initial position sync
        self._sync_position()

    def _fast_loop(self):
        """Fast loop: 1.5s interval. Position management + signal detection."""
        info(f"[SCALP] {self.symbol}: Fast loop started")

        while self._running:
            try:
                start = time.time()

                # 1. Get latest candle from WS cache
                candles = self._get_candles(5)
                if not candles:
                    time.sleep(self._fast_interval)
                    continue

                # 2. Update indicators incrementally
                latest = candles[-1]
                indicators = self._analyzer.update(latest)

                if not indicators.get("bootstrapped"):
                    time.sleep(self._fast_interval)
                    continue

                indicators["current_price"]

                with self._lock:
                    position = self._position
                    regime = self._regime

                # 3. Position management (if in position)
                if position:
                    self._manage_position(indicators, position)
                else:
                    # 4. Signal detection (if no position)
                    self._check_entry(indicators, regime)

                self._fast_cycle += 1

                # 5. Periodic status log (Task 1: every ~45 cycles = ~67.5s)
                if self._fast_cycle - self._last_status_cycle >= self._status_interval_cycles:
                    self._log_fast_loop_status(indicators, regime)
                    self._last_status_cycle = self._fast_cycle

                elapsed = time.time() - start
                sleep_time = max(0.1, self._fast_interval - elapsed)
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                info(f"[SCALP] {self.symbol}: Fast loop stopped (KeyboardInterrupt)")
                self._running = False
                return
            except Exception as e:
                error(f"[SCALP] {self.symbol}: Fast loop error: {e}")
                error(traceback.format_exc())
                time.sleep(ERROR_HANDLING.get("cycle_error_fallback_sleep", 5))

    def _slow_loop(self):
        """Slow loop: 45s interval. Full analysis + AI + sync."""
        info(f"[SCALP] {self.symbol}: Slow loop started")

        while self._running:
            try:
                # 1. Config hot-reload
                if should_reload_config():
                    info(f"[SCALP] {self.symbol}: Config changed, reloading...")
                    reload_bot_config()

                # 2. Sync position from exchange
                self._sync_position()

                # 3. Deterministic regime detection (every cycle)
                self._update_regime_deterministic()

                # 4. AI regime advisor (L2, every regime_interval seconds)
                if self._regime_ai_enabled:
                    self._update_regime_ai()

                # 5. Update order book cache (OB imbalance + spread)
                self._update_order_book()

                # 6. Process pending AI veto (L3)
                if self._veto_enabled and self._pending_veto:
                    self._process_veto()

                # 7. Re-bootstrap analyzer if needed
                if self._analyzer and not self._analyzer._bootstrapped:
                    candles = self._get_candles(300)
                    if candles:
                        self._analyzer.bootstrap(candles)

                # 8. Dump candles to price file (for chart worker)
                self._dump_prices()

                # 9. Log session stats
                stats = self._session.stats
                info(f"[SCALP] {self.symbol}: Session: trades={stats['trades_today']} "
                     f"PnL={stats['daily_pnl_pct']:.2f}% W/L={stats['wins']}/{stats['losses']}")

                # 10. Periodic calibration check
                self._slow_cycle += 1
                if (self._calibrator and self._calibration_interval > 0
                        and self._slow_cycle % self._calibration_interval == 0):
                    try:
                        suggestions = self._calibrator.check_and_suggest()
                        if suggestions:
                            info(f"[SCALP] {self.symbol}: Calibrator generated {len(suggestions)} suggestions")
                    except Exception as e:
                        warning(f"[SCALP] {self.symbol}: Calibration error: {e}")

                # 11. Flush pending trade tracker writes
                if self._tracker:
                    self._tracker.flush()

            except Exception as e:
                error(f"[SCALP] {self.symbol}: Slow loop error: {e}")
                error(traceback.format_exc())

            time.sleep(self._slow_interval)

    def _manage_position(self, indicators: Dict, position: Dict):
        """Fast-loop position management: trailing, time exit, exit signals."""
        current_price = indicators["current_price"]
        atr = indicators.get("atr", 0)

        entry_price = float(position.get("entry", position.get("avgPrice", 0)))
        pos_type = position.get("type", "").upper()

        # PnL
        if pos_type == "BUY":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        # 1. Time exit check
        hold_time_sec = time.time() - self._position_open_time
        hold_time_min = hold_time_sec / 60

        if hold_time_min >= self._max_hold_minutes:
            info(f"[SCALP] {self.symbol}: TIME EXIT ({hold_time_min:.1f}min >= {self._max_hold_minutes}min)")
            self._close_position(position, f"Time exit ({hold_time_min:.0f}min)")
            return

        # 2. Partial TP check (close portion at TP1, let rest run)
        if self._partial_tp_enabled and not self._partial_tp_done and self._entry_atr > 0:
            tp1_dist = self._entry_atr * self._partial_tp_atr_mult
            if pos_type == "BUY" and current_price >= entry_price + tp1_dist:
                self._partial_close(position, self._partial_tp_pct, f"TP1 at {self._partial_tp_atr_mult}x ATR")
                self._partial_tp_done = True
            elif pos_type == "SELL" and current_price <= entry_price - tp1_dist:
                self._partial_close(position, self._partial_tp_pct, f"TP1 at {self._partial_tp_atr_mult}x ATR")
                self._partial_tp_done = True

        # 3. Trailing stop update
        new_sl = self._trailing.update(current_price, atr)
        if new_sl is not None:
            self._update_sl_on_exchange(position, new_sl)

        # 4. Trailing stop hit check (client-side)
        trail_hit = False
        if pos_type == "BUY" and current_price <= self._trailing.current_sl:
            trail_hit = True
        elif pos_type == "SELL" and current_price >= self._trailing.current_sl:
            trail_hit = True

        if trail_hit and self._trailing.is_trailing:
            info(f"[SCALP] {self.symbol}: TRAILING STOP HIT at {current_price:.2f} "
                 f"(SL={self._trailing.current_sl:.2f})")
            self._close_position(position, f"Trailing stop ({pnl_pct:.1f}%)")
            return

        # 5. Deterministic exit signal check
        exit_signal = self._signal_gen.check_exit(indicators, position)
        if exit_signal.get("should_close"):
            info(f"[SCALP] {self.symbol}: EXIT SIGNAL: {exit_signal['reason']}")
            self._close_position(position, exit_signal["reason"])
            return

    def _check_entry(self, indicators: Dict, regime: Optional[Dict]):
        """Fast-loop entry check: generate signal, possibly execute or queue."""
        # Session check
        can_trade, reason = self._session.can_trade()
        if not can_trade:
            self._track_rejection(f"session:{reason}")
            return

        # Spread filter — reject signals when spread is too wide
        with self._lock:
            ob_imbalance = self._ob_imbalance
            spread_bps = self._ob_spread_bps

        if spread_bps > self._spread_max_bps > 0:
            self._track_rejection("spread")
            return  # Spread too wide, skip this cycle

        # Generate signal (uses cached OB imbalance from slow loop)
        signal = self._signal_gen.generate(indicators, regime=regime, ob_imbalance=ob_imbalance)

        # Track last score for status logging (Task 1)
        self._last_score = signal.get("score", 0)
        self._last_max_score = signal.get("max_score", 0)

        if signal["signal"] == "HOLD":
            # Track why HOLD (low score)
            min_required = signal.get("details", {}).get("min_score_required", "?")
            self._track_rejection(f"score<{min_required}")
            return

        quality = signal["quality"]
        auto_quality = SCALP_SETTINGS.get("signal_rules", {}).get("auto_execute_quality", 0.6)

        if quality >= auto_quality:
            # High quality → direct execution
            info(f"[SCALP] {self.symbol}: AUTO-EXECUTE {signal['signal']} "
                 f"Q:{quality:.2f} score:{signal['score']}")
            self._execute_entry(signal, indicators)
        elif self._veto_enabled and quality >= self._borderline_quality:
            # Borderline → queue for AI veto (only if above borderline threshold)
            with self._lock:
                self._pending_veto = {
                    "signal": signal,
                    "indicators": indicators.copy(),
                    "time": time.time(),
                    "cycle": self._fast_cycle,
                }
            info(f"[SCALP] {self.symbol}: Queued for AI veto: {signal['signal']} Q:{quality:.2f}")
        else:
            # Veto not used - track why (Task 3)
            if not self._veto_enabled:
                self._track_veto_skip("veto_disabled")
            else:
                self._track_veto_skip("quality_below_borderline")

    def _execute_entry(self, signal: Dict, indicators: Dict, ai_veto_used: bool = False):
        """Place a trade based on signal."""
        direction = signal["signal"]
        current_price = indicators["current_price"]
        atr = indicators.get("atr", 0)

        if atr <= 0:
            warning(f"[SCALP] {self.symbol}: Cannot execute, ATR is 0")
            return

        # Initialize trailing stop (sets initial SL/TP)
        self._trailing.init_position(direction, current_price, atr)

        sl = self._trailing.current_sl
        tp = self._trailing.initial_tp

        # Dynamic position size
        base_pct = SCALP_SETTINGS.get("risk_limits", {}).get("base_position_pct", 5.0)
        quality = signal.get("quality", 0.5)

        # Simple quality-based sizing for scalp
        size_pct = base_pct * (0.7 + quality * 0.6)  # 70%-130% of base
        size_pct = max(3.0, min(10.0, size_pct))

        # Session-aware size adjustment
        session_factor = self._session.get_session_size_factor()
        if session_factor < 1.0:
            size_pct *= session_factor
            size_pct = max(3.0, size_pct)

        # Determine order type (limit or market)
        entry_type = "MARKET"
        entry_price = current_price
        if self._limit_orders_enabled:
            # Calculate limit price with offset from current price
            offset = current_price * self._limit_offset_bps / 10000
            if direction == "BUY":
                entry_price = current_price - offset  # Slightly below for buy
            else:
                entry_price = current_price + offset  # Slightly above for sell
            entry_type = "LIMIT"

        from src.core.executor import create_order
        order_id = create_order(
            self.symbol,
            direction,
            entry_price,
            ai_sl=sl,
            ai_tp=tp,
            reason=f"[SCALP] {signal.get('pattern', 'generic')} Q:{quality:.2f} [{signal.get('regime', '?')}]",
            confidence=signal.get("confidence", 0.7),
            size_pct=size_pct,
            order_type=entry_type,
        )

        if order_id:
            # For limit orders, wait for fill or cancel
            if entry_type == "LIMIT":
                filled = self._wait_for_fill(order_id)
                if not filled:
                    info(f"[SCALP] {self.symbol}: Limit order not filled in {self._limit_timeout_sec}s, cancelled")
                    self._trailing.reset()
                    return

            self._session.record_entry()
            self._position_open_time = time.time()
            self._partial_tp_done = False
            self._entry_atr = atr

            # Save entry context for performance tracking
            with self._lock:
                current_regime = self._regime
            regime_label = current_regime.get("regime", "UNKNOWN") if current_regime else "UNKNOWN"
            entry_ctx = {
                "side": direction,
                "entry_price": entry_price,
                "regime": regime_label,
                "pattern": signal.get("pattern", "generic"),
                "score": signal.get("score", 0),
                "quality": signal.get("quality", 0.0),
                "ai_veto_used": ai_veto_used,
                "choppiness": indicators.get("choppiness", 50.0),
                "cvd_trend": indicators.get("cvd_trend", "FLAT"),
                "entry_atr": atr,
            }
            if self._perf_tracker:
                self._perf_tracker.record_entry(self.symbol, entry_ctx)
            if self._tracker:
                self._tracker.set_entry_context(self.symbol, {
                    "entry_regime": regime_label,
                    "entry_score": signal.get("score", 0),
                    "entry_quality": signal.get("quality", 0.0),
                    "entry_atr": atr,
                    "entry_volume_ratio": indicators.get("volume_ratio", 1.0),
                })

            # Sync position after entry
            time.sleep(1.0)
            self._sync_position()
            info(f"[SCALP] {self.symbol}: Entry OK ({entry_type}): {direction} @ {entry_price:.2f} "
                 f"SL={sl:.2f} TP={tp:.2f} size={size_pct:.1f}%")
        else:
            error(f"[SCALP] {self.symbol}: Entry FAILED for {direction}")
            self._trailing.reset()

    def _close_position(self, position: Dict, reason: str):
        """Close the current position."""
        deal_id = position.get("dealId", "")
        entry_price = float(position.get("entry", position.get("avgPrice", 0)))
        pos_type = position.get("type", "").upper()

        try:
            result = self._client.close_position(self.symbol, deal_id, percentage=1.0)
            if result:
                # Calculate PnL for session tracking
                current_price = float(position.get("markPrice", entry_price))
                if pos_type == "BUY":
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - current_price) / entry_price * 100

                self._session.record_exit(pnl_pct)
                self._trailing.reset()
                self._partial_tp_done = False
                self._entry_atr = 0.0

                # Record exit in performance tracker
                if self._perf_tracker:
                    self._perf_tracker.record_exit(self.symbol, pnl_pct, reason)

                with self._lock:
                    self._position = None
                    self._position_open_time = 0.0

                from src.utils.logger import log_trade
                log_trade(f"[SCALP] {self.symbol}: CLOSED {pos_type} | PnL={pnl_pct:.2f}% | {reason}")
                info(f"[SCALP] {self.symbol}: Position closed ({reason}), PnL={pnl_pct:.2f}%")
            else:
                error(f"[SCALP] {self.symbol}: Failed to close position {deal_id}")
        except Exception as e:
            error(f"[SCALP] {self.symbol}: Close error: {e}")

    def _wait_for_fill(self, order_id: str) -> bool:
        """Wait for a limit order to fill, cancel if timeout.

        Returns True if filled, False if cancelled due to timeout.
        """
        start = time.time()
        while time.time() - start < self._limit_timeout_sec:
            time.sleep(0.5)
            self._sync_position()
            with self._lock:
                if self._position:
                    return True  # Position appeared → order filled

        # Timeout — cancel the order
        try:
            self._client.cancel_all_orders(self.symbol)
        except Exception as e:
            warning(f"[SCALP] {self.symbol}: Failed to cancel limit order: {e}")
        return False

    def _partial_close(self, position: Dict, pct: float, reason: str):
        """Close a percentage of the current position."""
        deal_id = position.get("dealId", "")
        try:
            result = self._client.close_position(self.symbol, deal_id, percentage=pct)
            if result:
                info(f"[SCALP] {self.symbol}: Partial close {pct*100:.0f}% ({reason})")
            else:
                warning(f"[SCALP] {self.symbol}: Partial close failed")
        except Exception as e:
            warning(f"[SCALP] {self.symbol}: Partial close error: {e}")

    def _update_sl_on_exchange(self, position: Dict, new_sl: float):
        """Update stop loss on exchange (throttled)."""
        pos_type = position.get("type", "").upper()
        pos_side = "LONG" if pos_type == "BUY" else "SHORT"

        try:
            if hasattr(self._client, "set_sl_tp"):
                self._client.set_sl_tp(self.symbol, pos_side, sl=new_sl)
                info(f"[SCALP] {self.symbol}: SL updated to {new_sl:.2f} "
                     f"(trailing={'YES' if self._trailing.is_trailing else 'NO'})")
        except Exception as e:
            warning(f"[SCALP] {self.symbol}: SL update failed: {e}")

    def _update_order_book(self):
        """Update cached order book imbalance and spread (slow loop)."""
        try:
            ob = self._client.get_order_book(self.symbol, limit=10)
            if not ob:
                return

            from src.core.scalp_signal import calculate_ob_imbalance, calculate_ob_spread_bps
            imbalance = calculate_ob_imbalance(ob)

            # Calculate bid-ask spread in basis points
            spread_bps = calculate_ob_spread_bps(ob)

            with self._lock:
                self._ob_imbalance = imbalance
                self._ob_spread_bps = spread_bps

        except Exception as e:
            warning(f"[SCALP] {self.symbol}: OB update error: {e}")

    def _sync_position(self):
        """Sync position state from exchange."""
        try:
            positions = self._client.get_positions()
            symbol_positions = positions.get(self.symbol, [])
            real_position = symbol_positions[0] if symbol_positions else None

            with self._lock:
                old_pos = self._position
                self._position = real_position

                # Detect position closed externally
                if old_pos and not real_position:
                    info(f"[SCALP] {self.symbol}: Position closed externally")
                    self._trailing.reset()
                    self._position_open_time = 0.0

                # Detect new position (opened externally or sync after entry)
                if not old_pos and real_position:
                    self._position_open_time = self._position_open_time or time.time()
                    # Initialize trailing if not already done
                    if not self._trailing._pos_side:
                        entry = float(real_position.get("entry", real_position.get("avgPrice", 0)))
                        atr = self._analyzer.get_snapshot().get("atr", 0) if self._analyzer else 0
                        if entry > 0 and atr > 0:
                            side = real_position.get("type", "BUY").upper()
                            self._trailing.init_position(side, entry, atr)

            # Sync with trade tracker
            if self._tracker:
                self._tracker.sync_position(self.symbol, real_position, exchange_client=self._client)

        except Exception as e:
            warning(f"[SCALP] {self.symbol}: Position sync error: {e}")

    def _update_regime_deterministic(self):
        """Run deterministic regime detection from current indicators (every slow loop cycle)."""
        try:
            if not self._analyzer or not self._analyzer._bootstrapped:
                return

            snapshot = self._analyzer.get_snapshot()

            from src.core.regime import detect_regime
            regime_input = {
                "ema9": snapshot["ema_fast"],
                "ema21": snapshot["ema_med"],
                "bb_upper": snapshot["bb_upper"],
                "bb_lower": snapshot["bb_lower"],
                "close_prices": list(self._analyzer._recent_closes),
                "atr_ratio": snapshot["atr_ratio"],
            }
            regime = detect_regime(regime_input)

            with self._lock:
                self._regime = regime

        except Exception as e:
            warning(f"[SCALP] {self.symbol}: Regime detection error: {e}")

    def _update_regime_ai(self):
        """Run AI regime advisor (L2) if interval has elapsed."""
        now = time.time()
        if now - self._last_ai_regime_time < self._regime_interval_sec:
            return  # Not time yet

        if not self._analyzer or not self._analyzer._bootstrapped:
            return

        try:
            from src.prompts.strategies.scalp_regime import ScalpRegimeStrategy
            from src.core.predict import get_prediction

            snapshot = self._analyzer.get_snapshot()

            # Build context for L2 prompt
            ema_spread = 0.0
            if snapshot["ema_med"] > 0:
                ema_spread = (snapshot["ema_fast"] - snapshot["ema_med"]) / snapshot["ema_med"] * 100

            # Count up/down candles from recent closes
            closes = list(self._analyzer._recent_closes)
            up_candles = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
            down_candles = max(0, len(closes) - 1 - up_candles)

            # BB width percentile (approximate from current width)
            bb_percentile = 50  # Default — no history tracking for percentile in lightweight analyzer

            regime_ctx = {
                "symbol": self.symbol,
                "ema_spread": ema_spread,
                "rsi": snapshot.get("rsi", 50),
                "macd_hist": snapshot.get("macd_hist", 0.0),
                "bb_width": snapshot.get("bb_width", 0.0),
                "bb_percentile": bb_percentile,
                "atr_ratio": snapshot.get("atr_ratio", 1.0),
                "volume_ratio": snapshot.get("volume_ratio", 1.0),
                "support": snapshot.get("vwap_lower", 0),
                "resistance": snapshot.get("vwap_upper", 0),
                "up_candles": up_candles,
                "down_candles": down_candles,
                "prev_regime": self._ai_regime_label,
                "duration": self._ai_regime_duration,
            }

            prompt = ScalpRegimeStrategy().get_strategy_section(regime_ctx)
            raw_response = get_prediction(
                prompt,
                model=self._regime_model,
                max_tokens=self._regime_max_tokens,
                temperature=self._regime_temperature,
            )

            # Parse AI regime response
            ai_result = self._parse_regime_response(raw_response)

            if ai_result:
                new_regime = ai_result.get("regime", "UNKNOWN")
                confidence = ai_result.get("confidence", 0.0)

                # Track duration
                if new_regime == self._ai_regime_label:
                    self._ai_regime_duration += 1
                else:
                    self._ai_regime_duration = 1
                self._ai_regime_label = new_regime

                # Merge AI params into current regime if confidence is high enough
                # Always log AI regime result with full details (Task 2)
                ai_log = (f"[SCALP-L2] {self.symbol}: AI regime={new_regime} conf={confidence:.2f} "
                          f"bias={ai_result.get('bias', '?')} mode={ai_result.get('scalp_mode', '?')} "
                          f"note={ai_result.get('note', '')}")

                if confidence >= 0.6:
                    ai_params = ai_result.get("params", {})
                    with self._lock:
                        if self._regime:
                            # Override regime label with AI classification
                            self._regime["regime"] = new_regime
                            self._regime["ai_confidence"] = confidence
                            self._regime["ai_bias"] = ai_result.get("bias", "neutral")
                            self._regime["ai_scalp_mode"] = ai_result.get("scalp_mode", "")
                            # Merge params (min_score, size_factor, sl_mult, tp_mult)
                            if ai_params:
                                for key in ("min_score", "size_factor", "sl_mult", "tp_mult"):
                                    if key in ai_params:
                                        self._regime[f"recommended_{key}"] = ai_params[key]

                    info(f"{ai_log} [APPLIED]")
                else:
                    info(f"{ai_log} [LOW_CONF-IGNORED]")

            self._last_ai_regime_time = now

        except Exception as e:
            warning(f"[SCALP] {self.symbol}: AI regime advisor error: {e}")
            self._last_ai_regime_time = now  # Don't retry immediately on error

    def _parse_regime_response(self, raw_response) -> Optional[Dict]:
        """Parse L2 regime advisor JSON response."""
        import json
        import re

        try:
            if isinstance(raw_response, dict):
                return raw_response

            cleaned = re.sub(r'```json\s*', '', raw_response)
            cleaned = re.sub(r'```', '', cleaned)

            start = cleaned.find('{')
            if start == -1:
                return None

            brace_count = 0
            end = -1
            for i in range(start, len(cleaned)):
                if cleaned[i] == '{':
                    brace_count += 1
                elif cleaned[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break

            if end == -1:
                return None

            data = json.loads(cleaned[start:end])

            # Validate required fields
            if "regime" not in data:
                return None

            # Normalize
            data["regime"] = data["regime"].upper()
            if data["regime"] not in ("TRENDING", "RANGING", "VOLATILE", "TRANSITIONAL"):
                data["regime"] = "UNKNOWN"

            data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.5))))

            return data

        except Exception as e:
            warning(f"[SCALP-L2] {self.symbol}: Regime parse error: {e}")
            return None

    def _process_veto(self):
        """Process pending AI veto request (slow loop).

        Staleness checks:
        1. Time-based: discard if age > veto_staleness_sec
        2. Cycle-based: discard if fast_cycle advanced > veto_max_cycles
        3. Signal-changed: discard if current signal direction differs from queued
        """
        with self._lock:
            pending = self._pending_veto
            self._pending_veto = None
            current_cycle = self._fast_cycle

        if not pending:
            return

        # --- Staleness check 1: time-based ---
        age = time.time() - pending["time"]
        if age > self._veto_staleness_sec:
            info(f"[SCALP] {self.symbol}: Veto stale (time: {age:.1f}s > {self._veto_staleness_sec}s)")
            return

        # --- Staleness check 2: cycle-based ---
        cycles_elapsed = current_cycle - pending.get("cycle", current_cycle)
        if cycles_elapsed > self._veto_max_cycles:
            info(f"[SCALP] {self.symbol}: Veto stale (cycles: {cycles_elapsed} > {self._veto_max_cycles})")
            return

        # Don't veto if we now have a position
        with self._lock:
            if self._position:
                return

        signal = pending["signal"]
        indicators = pending["indicators"]

        # --- Staleness check 3: signal direction changed ---
        try:
            current_snap = self._analyzer.get_snapshot() if self._analyzer else None
            if current_snap:
                with self._lock:
                    current_regime = self._regime
                current_signal = self._signal_gen.generate(
                    current_snap, regime=current_regime,
                    ob_imbalance=self._ob_imbalance,
                )
                if current_signal["signal"] != signal["signal"]:
                    info(f"[SCALP] {self.symbol}: Veto stale (signal changed: "
                         f"{signal['signal']} → {current_signal['signal']})")
                    return
        except Exception:
            pass  # If re-check fails, proceed with original signal

        try:
            from src.prompts.strategies.scalp_veto import ScalpVetoStrategy
            from src.core.predict import get_prediction, parse_response

            veto_ctx = {
                "symbol": self.symbol,
                "signal": signal["signal"],
                "score": signal["score"],
                "max_score": signal["max_score"],
                "quality": signal["quality"],
                "regime": signal.get("regime", "UNKNOWN"),
                "rsi": indicators.get("rsi", 50),
                "volume_ratio": indicators.get("volume_ratio", 1.0),
                "momentum_dir": indicators.get("momentum_dir", "MIXED"),
                "pattern": signal.get("pattern", "generic"),
            }

            prompt = ScalpVetoStrategy().get_strategy_section(veto_ctx)
            raw_response = get_prediction(
                prompt,
                model=self._veto_model,
                max_tokens=self._veto_max_tokens,
                temperature=self._veto_temperature,
            )
            ai_result = parse_response(raw_response)

            if ai_result and ai_result.get("action"):
                ai_action = ai_result.get("action", "hold").upper()
                if ai_action == signal["signal"]:
                    info(f"[SCALP-L3] {self.symbol}: AI APPROVED {signal['signal']}")
                    self._execute_entry(signal, indicators, ai_veto_used=True)
                else:
                    info(f"[SCALP-L3] {self.symbol}: AI REJECTED {signal['signal']} "
                         f"(AI said {ai_action}: {ai_result.get('reason', '?')})")
            else:
                warning(f"[SCALP-L3] {self.symbol}: Veto parse failed, discarding signal")

        except Exception as e:
            warning(f"[SCALP-L3] {self.symbol}: Veto error: {e}")

    def _dump_prices(self):
        """Dump WS cache candles to data/prices/ for chart worker."""
        try:
            candles = self._get_candles(600)
            if not candles:
                return
            prices_dir = os.path.join(DATA_DIR, "prices")
            os.makedirs(prices_dir, exist_ok=True)
            prices_file = os.path.join(prices_dir, f"{get_filename(self.symbol)}.json")
            with open(prices_file, "w") as f:
                json.dump(candles, f)
        except Exception as e:
            warning(f"[SCALP] {self.symbol}: Price dump failed: {e}")

    def _get_candles(self, limit: int = 5) -> list:
        """Get candles from WS cache, fallback to REST."""
        from src.exchanges.bingx_ws_data_provider import get_klines_from_shared_cache, is_cache_ready

        if is_cache_ready(self.symbol):
            candles = get_klines_from_shared_cache(self.symbol, limit)
            if candles:
                # DEBUG: Log cache read every 60 cycles (~90 seconds)
                if self._fast_cycle % 60 == 0:
                    last_price = candles[-1].get('closePrice', 0) if candles else 0
                    info(f"[SCALP-CACHE] {self.symbol}: Got {len(candles)} candles from cache, last_price={last_price}")
                return candles

        # REST fallback (slower, should be rare)
        try:
            candles = self._client.get_kline_data(self.symbol, interval="1m", limit=limit)
            if candles and self._fast_cycle % 60 == 0:
                info(f"[SCALP-CACHE] {self.symbol}: REST fallback, got {len(candles)} candles")
            return candles if candles else []
        except Exception as e:
            warning(f"[SCALP] {self.symbol}: Candle fetch failed: {e}")
            return []

    # =========================================================================
    # Logging Methods (Tasks 1, 3, 4)
    # =========================================================================

    def _log_fast_loop_status(self, indicators: Dict, regime: Optional[Dict]):
        """
        Periodic status log for fast loop monitoring (Task 1).
        Format: [SCALP] BTC-USDT: 70140.00 RSI=55 EMA↑ score=2/6 spread=1.2bps [RANGING]
        """
        price = indicators.get("current_price", 0)
        rsi = indicators.get("rsi", 50)
        ema_fast = indicators.get("ema_fast", 0)
        ema_med = indicators.get("ema_med", 0)

        # EMA trend direction
        if ema_fast > ema_med and ema_med > 0:
            ema_trend = "EMA↑"
        elif ema_fast < ema_med and ema_med > 0:
            ema_trend = "EMA↓"
        else:
            ema_trend = "EMA→"

        # Last signal score
        if self._last_max_score > 0:
            score_str = f"{self._last_score}/{self._last_max_score}"
        else:
            score_str = "-"

        # Spread
        with self._lock:
            spread_bps = self._ob_spread_bps
        spread_str = f"{spread_bps:.1f}bps"

        # Regime
        regime_label = regime.get("regime", "?") if regime else "?"

        info(f"[SCALP] {self.symbol}: {price:.2f} RSI={rsi:.0f} {ema_trend} "
             f"score={score_str} spread={spread_str} [{regime_label}]")

    def _track_rejection(self, reason: str):
        """Track signal rejection reason for periodic summary (Task 4)."""
        self._rejection_counts[reason] = self._rejection_counts.get(reason, 0) + 1

        # Log periodically
        if self._fast_cycle - self._last_rejection_log_cycle >= self._rejection_window_cycles:
            self._log_rejection_summary()
            self._last_rejection_log_cycle = self._fast_cycle

    def _log_rejection_summary(self):
        """Log summary of signal rejections (Task 4)."""
        if not self._rejection_counts:
            info(f"[SCALP] {self.symbol}: Last {self._rejection_window_cycles} cycles: all HOLD (no signals)")
            return

        total = sum(self._rejection_counts.values())
        hold_count = max(0, self._rejection_window_cycles - total)
        parts = [f"HOLD:{hold_count}"]

        for reason, count in sorted(self._rejection_counts.items(), key=lambda x: -x[1]):
            parts.append(f"{reason}:{count}")

        info(f"[SCALP] {self.symbol}: Last {self._rejection_window_cycles} cycles: {', '.join(parts)}")

        # Reset counts
        self._rejection_counts = {}

    def _track_veto_skip(self, reason: str):
        """Track why veto wasn't used for periodic summary (Task 3)."""
        self._veto_skip_counter += 1
        self._veto_skip_reasons[reason] = self._veto_skip_reasons.get(reason, 0) + 1

        # Log periodically
        if self._fast_cycle - self._last_veto_skip_log_cycle >= self._veto_skip_log_interval:
            self._log_veto_skip_summary()
            self._last_veto_skip_log_cycle = self._fast_cycle

    def _log_veto_skip_summary(self):
        """Log summary of veto skips (Task 3)."""
        if not self._veto_skip_reasons:
            return

        reasons_str = ", ".join(f"{k}:{v}" for k, v in self._veto_skip_reasons.items())
        info(f"[SCALP-L3] {self.symbol}: Veto skip summary ({self._veto_skip_counter} cycles): {reasons_str}")

        # Reset counters
        self._veto_skip_counter = 0
        self._veto_skip_reasons = {}
