"""
Trading Session Awareness Module.

Determines current trading session (Asian/European/US)
and overlap periods for AISCALP strategy.
"""

from datetime import datetime, timezone

from src.config import BOT_CONFIG
from src.utils.logger import info


def _get_current_utc_hour() -> int:
    """Returns current UTC hour. Extracted for testability."""
    return datetime.now(timezone.utc).hour


def get_session_info() -> dict:
    """
    Returns current trading session information.

    Returns:
        dict with keys:
            - current_hour_utc: int
            - active_sessions: list of session names
            - is_overlap: bool (2+ sessions active)
            - session_quality: str (HIGH/MEDIUM/LOW)
            - quality_score_adj: int (adjustment for signal scoring)
    """
    settings = BOT_CONFIG.get("AISCALP_SETTINGS", {}).get("sessions", {})

    if not settings.get("enabled", True):
        return _default_session_info()

    current_hour = _get_current_utc_hour()
    definitions = settings.get("definitions", {
        "ASIAN": {"start_utc": 0, "end_utc": 8},
        "EUROPEAN": {"start_utc": 7, "end_utc": 15},
        "US": {"start_utc": 13, "end_utc": 21},
    })

    # Determine active sessions
    active_sessions = []
    for name, times in definitions.items():
        start = times.get("start_utc", 0)
        end = times.get("end_utc", 0)
        if _hour_in_range(current_hour, start, end):
            active_sessions.append(name)

    is_overlap = len(active_sessions) >= 2

    # Quality classification
    overlap_bonus = settings.get("overlap_bonus", 1)

    if is_overlap:
        session_quality = "HIGH"
        quality_score_adj = overlap_bonus
    elif len(active_sessions) == 1:
        session_quality = "MEDIUM"
        quality_score_adj = 0
    else:
        session_quality = "LOW"
        quality_score_adj = -1

    result = {
        "current_hour_utc": current_hour,
        "active_sessions": active_sessions,
        "is_overlap": is_overlap,
        "session_quality": session_quality,
        "quality_score_adj": quality_score_adj,
    }

    sessions_str = "+".join(active_sessions) if active_sessions else "NONE"
    info(f"🕐 [SESSION] {current_hour}:00 UTC | {sessions_str} | Quality: {session_quality} (adj: {quality_score_adj:+d})")

    return result


def _hour_in_range(hour: int, start: int, end: int) -> bool:
    """Check if hour falls within [start, end) range, handling wrap-around."""
    if start <= end:
        return start <= hour < end
    else:
        # Wraps around midnight (e.g., 22 to 6)
        return hour >= start or hour < end


def _default_session_info() -> dict:
    """Default session info when sessions are disabled."""
    return {
        "current_hour_utc": _get_current_utc_hour(),
        "active_sessions": [],
        "is_overlap": False,
        "session_quality": "MEDIUM",
        "quality_score_adj": 0,
    }
