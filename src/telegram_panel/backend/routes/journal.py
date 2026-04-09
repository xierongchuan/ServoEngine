import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from ..services.auth import get_current_user
from ..services.data_reader import DataReader
from ..config import CONFIG_PATH

router = APIRouter(prefix="/api/journal", tags=["journal"])
reader = DataReader()

_CONFIG_DIR = CONFIG_PATH.parent


def _get_strategy_cooldown() -> tuple[str, float]:
    """Get active strategy name and cooldown hours from config."""
    active_path = _CONFIG_DIR / "active.json"
    if active_path.exists():
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                active = json.load(f)
            strategy = active.get("strategy", "MACDX")
            # Read preset from strategy file
            strat_path = _CONFIG_DIR / "strategies" / f"{strategy.lower()}.json"
            if strat_path.exists():
                with open(strat_path, "r", encoding="utf-8") as f:
                    strat = json.load(f)
                cooldown = strat.get("preset", {}).get("cooldown_after_close_hours", 0)
                return strategy, cooldown
            return strategy, 0
        except Exception:
            pass
    # Legacy fallback
    config = reader.read_config()
    strategy = config.get("STRATEGY_STYLE", "AISCALP")
    presets = config.get("STYLE_PRESETS", {})
    cooldown = presets.get(strategy, {}).get("cooldown_after_close_hours", 0)
    return strategy, cooldown


@router.get("")
async def get_journal(_user: dict = Depends(get_current_user)) -> dict:
    return reader.read_journal()


@router.get("/stats")
async def get_journal_stats(_user: dict = Depends(get_current_user)) -> dict:
    journal = reader.read_journal()
    _, cooldown_hours = _get_strategy_cooldown()

    now = datetime.now()
    symbols_stats = {}
    total_entries = 0
    total_active_plans = 0
    all_confidences: list[float] = []

    for symbol, data in journal.items():
        entries = data.get("entries", [])
        trade_plan = data.get("trade_plan")
        last_close_time = data.get("last_close_time")

        entry_count = len(entries)
        total_entries += entry_count

        action_dist = {"buy": 0, "sell": 0, "hold": 0, "close": 0}
        confidences: list[float] = []
        last_action_time = None

        for e in entries:
            action = e.get("action", "hold")
            action_dist[action] = action_dist.get(action, 0) + 1
            if e.get("confidence"):
                confidences.append(e["confidence"])
                all_confidences.append(e["confidence"])
            if e.get("time"):
                last_action_time = e["time"]

        in_cooldown = False
        cooldown_remaining = 0.0
        if last_close_time and cooldown_hours > 0:
            try:
                close_dt = datetime.strptime(last_close_time, "%Y-%m-%d %H:%M:%S")
                hours_since = (now - close_dt).total_seconds() / 3600
                if hours_since < cooldown_hours:
                    in_cooldown = True
                    cooldown_remaining = round(cooldown_hours - hours_since, 2)
            except (ValueError, TypeError):
                pass

        position_age_hours = None
        if trade_plan and trade_plan.get("time"):
            try:
                entry_dt = datetime.strptime(trade_plan["time"], "%Y-%m-%d %H:%M:%S")
                position_age_hours = round((now - entry_dt).total_seconds() / 3600, 2)
            except (ValueError, TypeError):
                pass

        has_active_plan = trade_plan is not None
        if has_active_plan:
            total_active_plans += 1

        symbols_stats[symbol] = {
            "entry_count": entry_count,
            "action_distribution": action_dist,
            "avg_confidence": round(sum(confidences) / len(confidences), 2) if confidences else 0,
            "last_action_time": last_action_time,
            "has_active_plan": has_active_plan,
            "in_cooldown": in_cooldown,
            "cooldown_remaining_hours": cooldown_remaining,
            "position_age_hours": position_age_hours,
            "last_close_time": last_close_time,
        }

    return {
        "total_entries": total_entries,
        "active_plans_count": total_active_plans,
        "avg_confidence": round(sum(all_confidences) / len(all_confidences), 2) if all_confidences else 0,
        "symbols": symbols_stats,
    }


@router.get("/{symbol}")
async def get_symbol_journal(
    symbol: str, _user: dict = Depends(get_current_user)
) -> dict:
    journal = reader.read_journal()
    data = journal.get(symbol)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No journal entries for {symbol}")
    return data
