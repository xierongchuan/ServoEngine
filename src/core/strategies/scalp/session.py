"""ScalpSession — risk limits, cooldowns, daily/hourly loss tracking."""

import time
from typing import Dict, Optional

from src.config import SCALP_SETTINGS
from src.utils.logger import info, warning


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
        Returns: (allowed: bool, reason: str)
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
