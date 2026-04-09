"""
Модуль отслеживания производительности торговой системы.
Анализирует историю сделок для настройки параметров и оптимизации стратегии.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any

from src.config import DATA_DIR, BOT_CONFIG
from src.utils.logger import info, warning


class PerformanceTracker:
    """
    Отслеживает метрики производительности и предлагает корректировки параметров
    на основе анализа истории сделок.
    """

    def __init__(self):
        self.data_dir = DATA_DIR
        self.history_file = os.path.join(self.data_dir, "trade_history.json")
        self.config = BOT_CONFIG.get("PERFORMANCE_TRACKING", {})
        self.history = self._load_history()

    def _load_history(self) -> List[Dict[str, Any]]:
        """Загружает историю сделок из файла."""
        if not os.path.exists(self.history_file):
            return []

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    warning(f"[PerformanceTracker] Invalid history format: expected list, got {type(data)}")
                    return []
                return data
        except json.JSONDecodeError as e:
            warning(f"[PerformanceTracker] Failed to decode {self.history_file}: {e}")
            return []
        except Exception as e:
            warning(f"[PerformanceTracker] Error loading history: {e}")
            return []

    def _filter_trades(self, symbol: Optional[str] = None, last_n: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Фильтрует сделки по символу и берет последние N.

        Args:
            symbol: Фильтр по символу (None = все)
            last_n: Количество последних сделок (None = все)

        Returns:
            Отфильтрованный список сделок
        """
        trades = [t for t in self.history if t is not None]

        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]

        if last_n is not None and last_n > 0:
            trades = trades[-last_n:]

        return trades

    def _is_win(self, trade: Dict[str, Any]) -> bool:
        """Определяет, является ли сделка прибыльной."""
        if trade is None:
            return False
        pnl = trade.get("last_pnl", 0)
        try:
            return float(pnl) > 0
        except (ValueError, TypeError):
            return False

    def _calculate_win_rate(self, trades: List[Dict[str, Any]]) -> float:
        """Вычисляет винрейт (0-1)."""
        trades = [t for t in trades if t is not None]
        if not trades:
            return 0.0

        wins = sum(1 for t in trades if self._is_win(t))
        return wins / len(trades)

    def _calculate_avg_pnl(self, trades: List[Dict[str, Any]]) -> float:
        """Вычисляет средний PnL."""
        if not trades:
            return 0.0

        pnls = []
        for t in trades:
            try:
                pnl = float(t.get("last_pnl", 0))
                pnls.append(pnl)
            except (ValueError, TypeError):
                continue

        return sum(pnls) / len(pnls) if pnls else 0.0

    def _calculate_avg_hold_time(self, trades: List[Dict[str, Any]]) -> float:
        """Вычисляет среднее время удержания позиции в часах."""
        if not trades:
            return 0.0

        hold_times = []
        for t in trades:
            open_time_str = t.get("open_time")
            close_time_str = t.get("close_time")

            if not open_time_str or not close_time_str:
                continue

            try:
                open_time = datetime.fromisoformat(open_time_str.replace('Z', '+00:00'))
                close_time = datetime.fromisoformat(close_time_str.replace('Z', '+00:00'))
                duration = (close_time - open_time).total_seconds() / 3600
                hold_times.append(duration)
            except (ValueError, TypeError):
                continue

        return sum(hold_times) / len(hold_times) if hold_times else 0.0

    def _calculate_streak(self, trades: List[Dict[str, Any]]) -> int:
        """
        Вычисляет текущую серию (стрик).
        Положительное число = последовательные победы
        Отрицательное число = последовательные проигрыши
        """
        if not trades:
            return 0

        streak = 0
        last_result = None

        for t in reversed(trades):
            is_win = self._is_win(t)

            if last_result is None:
                last_result = is_win
                streak = 1 if is_win else -1
            elif is_win == last_result:
                streak = streak + 1 if is_win else streak - 1
            else:
                break

        return streak

    def _group_by_regime(self, trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Группирует сделки по режиму входа."""
        grouped = {}

        for t in trades:
            regime = t.get("entry_regime", "UNKNOWN")
            if regime not in grouped:
                grouped[regime] = []
            grouped[regime].append(t)

        return grouped

    def _group_by_score_range(self, trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Группирует сделки по диапазону скора."""
        grouped = {
            "4-5": [],
            "6-7": [],
            "8+": []
        }

        for t in trades:
            score = t.get("entry_score")
            if score is None:
                continue

            try:
                score = int(score)
                if 4 <= score <= 5:
                    grouped["4-5"].append(t)
                elif 6 <= score <= 7:
                    grouped["6-7"].append(t)
                elif score >= 8:
                    grouped["8+"].append(t)
            except (ValueError, TypeError):
                continue

        return grouped

    def get_stats(self, symbol: Optional[str] = None, last_n: int = 20) -> Dict[str, Any]:
        """
        Возвращает детальную статистику по сделкам.

        Args:
            symbol: Фильтр по символу (None = все)
            last_n: Количество последних сделок для анализа

        Returns:
            Словарь с метриками производительности
        """
        trades = self._filter_trades(symbol, last_n)

        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "avg_hold_time_hours": 0.0,
                "by_regime": {},
                "by_score_range": {},
                "streak": 0
            }

        # Общие метрики
        stats = {
            "total_trades": len(trades),
            "win_rate": self._calculate_win_rate(trades),
            "avg_pnl": self._calculate_avg_pnl(trades),
            "avg_hold_time_hours": self._calculate_avg_hold_time(trades),
            "streak": self._calculate_streak(trades)
        }

        # Группировка по режиму
        by_regime = {}
        for regime, regime_trades in self._group_by_regime(trades).items():
            if regime_trades:
                by_regime[regime] = {
                    "win_rate": self._calculate_win_rate(regime_trades),
                    "avg_pnl": self._calculate_avg_pnl(regime_trades),
                    "count": len(regime_trades)
                }
        stats["by_regime"] = by_regime

        # Группировка по диапазону скора
        by_score = {}
        for score_range, score_trades in self._group_by_score_range(trades).items():
            if score_trades:
                by_score[score_range] = {
                    "win_rate": self._calculate_win_rate(score_trades),
                    "avg_pnl": self._calculate_avg_pnl(score_trades),
                    "count": len(score_trades)
                }
        stats["by_score_range"] = by_score

        return stats

    def get_recent_performance(self, symbol: Optional[str] = None, last_n: int = 10) -> Dict[str, Any]:
        """
        Возвращает упрощенную статистику для динамического sizing.

        Args:
            symbol: Фильтр по символу (None = все)
            last_n: Количество последних сделок

        Returns:
            Словарь с ключевыми метриками
        """
        trades = self._filter_trades(symbol, last_n)

        return {
            "win_rate": self._calculate_win_rate(trades),
            "avg_pnl": self._calculate_avg_pnl(trades),
            "streak": self._calculate_streak(trades),
            "total_trades": len(trades)
        }

    def should_adjust_thresholds(self) -> List[Dict[str, Any]]:
        """
        Анализирует производительность и предлагает корректировки параметров.

        Returns:
            Список предложений по корректировке параметров
        """
        if not self.config.get("enabled", True):
            return []

        min_trades = self.config.get("min_trades_for_analysis", 10)
        self.config.get("win_rate_floor", 0.30)

        suggestions = []

        # Получаем HYBRID settings
        hybrid_settings = BOT_CONFIG.get("HYBRID_SETTINGS", {})
        signal_rules = hybrid_settings.get("signal_rules", {})
        current_min_score = signal_rules.get("min_score_for_signal", 5)

        # Получаем REGIME settings
        regime_settings = BOT_CONFIG.get("REGIME_SETTINGS", {})
        regime_params = regime_settings.get("regime_params", {})

        # Анализ по диапазонам скора
        stats = self.get_stats(last_n=50)
        by_score = stats.get("by_score_range", {})

        # Проверка низкого диапазона скоров (4-5)
        low_score_data = by_score.get("4-5", {})
        if low_score_data.get("count", 0) >= min_trades:
            low_wr = low_score_data.get("win_rate", 0)
            if low_wr < 0.30:
                suggestions.append({
                    "parameter": "min_score_for_signal",
                    "current": current_min_score,
                    "suggested": min(current_min_score + 1, 8),
                    "reason": f"Винрейт при скоре 4-5 составляет {low_wr*100:.1f}% ({low_score_data['count']} сделок)",
                    "confidence": 0.7,
                    "auto_apply": False
                })

        # Анализ по режимам
        by_regime = stats.get("by_regime", {})

        for regime, regime_data in by_regime.items():
            if regime_data.get("count", 0) >= max(5, min_trades // 2):
                regime_wr = regime_data.get("win_rate", 0)

                if regime_wr < 0.25 and regime in regime_params:
                    current_regime_min_score = regime_params[regime].get("min_score", 5)
                    suggestions.append({
                        "parameter": f"regime_params.{regime}.min_score",
                        "current": current_regime_min_score,
                        "suggested": min(current_regime_min_score + 1, 9),
                        "reason": f"Винрейт в режиме {regime} составляет {regime_wr*100:.1f}% ({regime_data['count']} сделок)",
                        "confidence": 0.6,
                        "auto_apply": False
                    })

        # Анализ низкого объема
        trades_with_volume = [t for t in self.history[-50:] if t.get("entry_volume_ratio") is not None]

        if len(trades_with_volume) >= min_trades:
            losses_with_low_volume = [
                t for t in trades_with_volume
                if not self._is_win(t) and t.get("entry_volume_ratio", 1.0) < 0.8
            ]

            total_losses = sum(1 for t in trades_with_volume if not self._is_win(t))

            if total_losses > 0:
                low_volume_loss_pct = len(losses_with_low_volume) / total_losses

                if low_volume_loss_pct > 0.60:
                    current_min_volume = signal_rules.get("min_volume_ratio", 0.5)
                    suggestions.append({
                        "parameter": "min_volume_ratio",
                        "current": current_min_volume,
                        "suggested": min(current_min_volume + 0.1, 1.0),
                        "reason": f"{low_volume_loss_pct*100:.1f}% проигрышей при volume_ratio < 0.8",
                        "confidence": 0.5,
                        "auto_apply": False
                    })

        # Логирование предложений
        if suggestions:
            info(f"[PerformanceTracker] Сгенерировано {len(suggestions)} предложений по настройке параметров")
            for s in suggestions:
                info(f"  • {s['parameter']}: {s['current']} → {s['suggested']} ({s['reason']})")

        return suggestions

    def save_calibration_suggestions(self, suggestions: List[Dict[str, Any]]) -> None:
        """
        Сохраняет предложения по калибровке в JSON-файл.

        Args:
            suggestions: Список предложений от should_adjust_thresholds()
        """
        output_path = os.path.join(self.data_dir, "calibration_suggestions.json")
        data = {
            "timestamp": datetime.now().isoformat(),
            "suggestions": suggestions
        }
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            info(f"[PerformanceTracker] Сохранено {len(suggestions)} предложений в {output_path}")
        except Exception as e:
            warning(f"[PerformanceTracker] Ошибка сохранения калибровки: {e}")


# Глобальный singleton instance
_tracker: Optional[PerformanceTracker] = None


def get_performance_tracker() -> PerformanceTracker:
    """
    Возвращает глобальный экземпляр PerformanceTracker (singleton).

    Returns:
        Экземпляр PerformanceTracker
    """
    global _tracker
    if _tracker is None:
        _tracker = PerformanceTracker()
    return _tracker
