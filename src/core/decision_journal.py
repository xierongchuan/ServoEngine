"""
Decision Journal — хранит историю решений AI между итерациями.
Позволяет AI видеть свои предыдущие решения и initial trade plan.
"""

import json
import os
from datetime import datetime

from src.config import DATA_DIR, DECISION_JOURNAL
from src.utils.logger import info, warning

JOURNAL_FILE = os.path.join(DATA_DIR, "decision_journal.json")

# Конфигурация из bot_config.json
JOURNAL_ENABLED = DECISION_JOURNAL.get("enabled", True)
ENTRY_LIMITS = DECISION_JOURNAL.get("max_entries", {"SCALP": 5, "INTRADAY": 10, "SWING": 10})


class DecisionJournal:

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        try:
            if os.path.exists(JOURNAL_FILE):
                with open(JOURNAL_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            warning(f"[DecisionJournal] Failed to load: {e}")
        return {}

    def _save(self):
        try:
            with open(JOURNAL_FILE, "w") as f:
                json.dump(self.data, f, indent=2, default=str)
        except Exception as e:
            warning(f"[DecisionJournal] Failed to save: {e}")

    def _ensure_symbol(self, symbol: str):
        if symbol not in self.data:
            self.data[symbol] = {"entries": [], "trade_plan": None}

    def record(self, symbol: str, prediction: dict, current_price: float, current_pnl: float | None = None):
        """Записывает решение AI после итерации."""
        if not JOURNAL_ENABLED:
            return
        self._ensure_symbol(symbol)

        action = prediction.get("action", "hold")
        pnl_str = f"{current_pnl:+.2f}%" if current_pnl is not None else "—"

        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "confidence": round(prediction.get("confidence", 0), 2),
            "price": round(current_price, 2),
            "sl": prediction.get("stop_loss"),
            "tp": prediction.get("take_profit"),
            "pnl": pnl_str,
            "reason": self._shorten_reason(prediction.get("reason", "")),
        }

        self.data[symbol]["entries"].append(entry)
        self._save()

    def set_trade_plan(self, symbol: str, prediction: dict, entry_price: float):
        """Фиксирует initial plan при открытии позиции."""
        if not JOURNAL_ENABLED:
            return
        self._ensure_symbol(symbol)
        self.data[symbol]["trade_plan"] = {
            "action": prediction.get("action"),
            "entry_price": round(entry_price, 2),
            "planned_sl": prediction.get("stop_loss"),
            "planned_tp": prediction.get("take_profit"),
            "reason": prediction.get("reason", ""),
            "confidence": prediction.get("confidence", 0),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save()
        info(f"📋 [DecisionJournal] Trade plan set for {symbol}")

    def clear_trade_plan(self, symbol: str):
        """Очищает plan при закрытии позиции."""
        self._ensure_symbol(symbol)
        self.data[symbol]["trade_plan"] = None
        self._save()

    def get_context(self, symbol: str, style: str) -> str:
        """Возвращает отформатированную строку для вставки в промпт."""
        if not JOURNAL_ENABLED:
            return ""
        self._ensure_symbol(symbol)

        limit = ENTRY_LIMITS.get(style, 10)
        entries = self.data[symbol]["entries"][-limit:]
        trade_plan = self.data[symbol].get("trade_plan")

        if not entries and not trade_plan:
            return ""

        lines = []

        # Таблица решений
        if entries:
            lines.append("Time|Action|Conf|Price|PnL|Reason")
            for e in entries:
                lines.append(
                    f"{e['time']}|{e['action']}|{e['confidence']}|{e['price']}|{e['pnl']}|{e['reason']}"
                )

        decision_history = "\n".join(lines) if lines else "Нет предыдущих решений."

        # Trade plan
        if trade_plan:
            tp_sl = f"SL: {trade_plan['planned_sl']}" if trade_plan.get("planned_sl") else "SL: N/A"
            tp_tp = f"TP: {trade_plan['planned_tp']}" if trade_plan.get("planned_tp") else "TP: N/A"
            trade_plan_block = (
                f"Вход: {trade_plan['action'].upper()} @ {trade_plan['entry_price']} | {tp_sl} | {tp_tp}\n"
                f"Причина: {trade_plan['reason']} | Confidence: {trade_plan['confidence']}\n"
                f"Время: {trade_plan['time']}"
            )
        else:
            trade_plan_block = "Нет активного плана."

        return f"{decision_history}\n\n{trade_plan_block}"

    def trim_entries(self, symbol: str, style: str):
        """Обрезает старые записи до лимита стратегии."""
        self._ensure_symbol(symbol)
        limit = ENTRY_LIMITS.get(style, 10)
        entries = self.data[symbol]["entries"]
        if len(entries) > limit * 2:
            self.data[symbol]["entries"] = entries[-limit:]
            self._save()

    @staticmethod
    def _shorten_reason(reason: str) -> str:
        """Сокращает reason до ~40 символов."""
        if not reason:
            return "—"
        # Берём первую часть до |
        parts = reason.split("|")
        short = parts[0].strip()
        if len(short) > 40:
            short = short[:37] + "..."
        return short
