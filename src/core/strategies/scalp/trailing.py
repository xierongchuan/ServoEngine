"""ATR-based trailing stop with breakeven capability."""

import time
from typing import Dict, Optional

from src.config import SCALP_SETTINGS
from src.utils.logger import info


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
        Returns: New SL price if it should be updated on exchange, None if no update needed.
        """
        if not self._pos_side:
            return None

        now = time.time()
        new_sl = None

        if self._pos_side == "BUY":
            if current_price > self._best_price:
                self._best_price = current_price

            pnl_pct = (current_price - self._entry_price) / self._entry_price * 100
            if self._be_enabled and not self._be_applied and pnl_pct >= self._be_trigger_pct:
                be_sl = self._entry_price * (1 + self._be_fee_buffer_pct / 100)
                if be_sl > self._current_sl:
                    self._current_sl = be_sl
                    self._be_applied = True
                    new_sl = self._current_sl
                    info(f"[TRAIL] Breakeven applied: SL\u2192{new_sl:.2f} (+{self._be_fee_buffer_pct}% buffer)")

            profit_in_atr = (self._best_price - self._entry_price) / atr if atr > 0 else 0
            if profit_in_atr >= self._trail_activation_mult:
                self._trailing_active = True
                trail_sl = self._best_price - atr * self._trail_distance_mult
                if trail_sl > self._current_sl:
                    self._current_sl = trail_sl
                    new_sl = self._current_sl
        else:
            if current_price < self._best_price:
                self._best_price = current_price

            pnl_pct = (self._entry_price - current_price) / self._entry_price * 100
            if self._be_enabled and not self._be_applied and pnl_pct >= self._be_trigger_pct:
                be_sl = self._entry_price * (1 - self._be_fee_buffer_pct / 100)
                if be_sl < self._current_sl:
                    self._current_sl = be_sl
                    self._be_applied = True
                    new_sl = self._current_sl
                    info(f"[TRAIL] Breakeven applied: SL\u2192{new_sl:.2f} (-{self._be_fee_buffer_pct}% buffer)")

            profit_in_atr = (self._entry_price - self._best_price) / atr if atr > 0 else 0
            if profit_in_atr >= self._trail_activation_mult:
                self._trailing_active = True
                trail_sl = self._best_price + atr * self._trail_distance_mult
                if trail_sl < self._current_sl:
                    self._current_sl = trail_sl
                    new_sl = self._current_sl

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
