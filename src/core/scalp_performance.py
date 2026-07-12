"""
SCALP Performance Tracker & Calibrator.

ScalpPerformanceTracker — records per-trade entry/exit context for SCALP mode,
groups by regime, pattern, score range, and tracks A/B stats (AI veto vs direct).

ScalpCalibrator — analyzes performance data and generates calibration suggestions
for min_score, regime thresholds, and pattern-specific parameters.

Persistence: data/scalp_performance.json, data/scalp_calibration.json
"""

import json
import os
import time
import fcntl
from datetime import datetime
from typing import Dict, List, Any

from src.config import DATA_DIR
from src.utils.logger import info, warning


class ScalpPerformanceTracker:
    """
    Records and analyzes per-trade SCALP performance data.

    Each trade record includes:
      - symbol, side, entry_price
      - regime, pattern, score, quality
      - ai_veto_used (bool), choppiness, cvd_trend
      - exit_reason, hold_time_sec, pnl_pct
      - entry_time, exit_time
    """

    def __init__(self):
        self._file = os.path.join(DATA_DIR, "scalp_performance.json")
        self._trades: List[Dict[str, Any]] = self._load()
        # In-flight entries (keyed by symbol, waiting for exit)
        self._pending: Dict[str, Dict[str, Any]] = {}

    def _load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self._file):
            return []
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._trades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            warning(f"[ScalpPerf] Save error: {e}")

    def _append_trade(self, record: Dict[str, Any]):
        """Межпроцессное append без потери сделок других symbol workers."""
        lock_path = f"{self._file}.lock"
        try:
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            with open(lock_path, "a+", encoding="utf-8") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                trades = self._load()
                trades.append(record)
                temp_path = f"{self._file}.{os.getpid()}.tmp"
                with open(temp_path, "w", encoding="utf-8") as handle:
                    json.dump(trades, handle, indent=2, ensure_ascii=False)
                os.replace(temp_path, self._file)
                self._trades = trades
                fcntl.flock(lock, fcntl.LOCK_UN)
        except Exception as exc:
            warning(f"[ScalpPerf] Atomic append error: {exc}")

    def record_entry(self, symbol: str, context: Dict[str, Any]):
        """
        Record trade entry context.

        Expected keys: regime, pattern, score, quality, ai_veto_used,
                       choppiness, cvd_trend, entry_atr, side, entry_price
        """
        self._pending[symbol] = {
            "symbol": symbol,
            "side": context.get("side", ""),
            "entry_price": context.get("entry_price", 0.0),
            "regime": context.get("regime", "UNKNOWN"),
            "pattern": context.get("pattern", "generic"),
            "score": context.get("score", 0),
            "quality": context.get("quality", 0.0),
            "ai_veto_used": context.get("ai_veto_used", False),
            "choppiness": context.get("choppiness", 50.0),
            "cvd_trend": context.get("cvd_trend", "FLAT"),
            "entry_atr": context.get("entry_atr", 0.0),
            "entry_time": datetime.now().isoformat(),
            "entry_ts": time.time(),
        }

    def record_exit(self, symbol: str, pnl_pct: float, exit_reason: str,
                    price_pnl_pct: float = 0.0, net_pnl_usdt: float = 0.0):
        """Record trade exit and archive to history."""
        pending = self._pending.pop(symbol, None)
        if not pending:
            # No entry context — still record what we can
            pending = {
                "symbol": symbol,
                "entry_time": "",
                "entry_ts": 0.0,
                "regime": "UNKNOWN",
                "pattern": "unknown",
                "score": 0,
                "quality": 0.0,
                "ai_veto_used": False,
            }

        entry_ts = pending.get("entry_ts", 0.0)
        hold_time_sec = time.time() - entry_ts if entry_ts > 0 else 0.0

        record = {**pending}
        record["pnl_pct"] = pnl_pct
        record["price_pnl_pct"] = price_pnl_pct
        record["net_pnl_usdt"] = net_pnl_usdt
        record["exit_reason"] = exit_reason
        record["hold_time_sec"] = round(hold_time_sec, 1)
        record["exit_time"] = datetime.now().isoformat()
        # Remove internal timestamp
        record.pop("entry_ts", None)

        self._append_trade(record)

        info(f"[ScalpPerf] {symbol}: recorded exit pnl={pnl_pct:.2f}% "
             f"regime={record['regime']} pattern={record['pattern']} "
             f"ai_veto={record['ai_veto_used']}")

    def get_stats(self, last_n: int = 50) -> Dict[str, Any]:
        """
        Returns aggregated stats from the last N trades.

        Returns dict with:
          total_trades, win_rate, avg_pnl, avg_hold_sec,
          by_regime, by_pattern, by_score_range, ab_comparison
        """
        trades = self._trades[-last_n:] if last_n > 0 else self._trades

        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "avg_hold_sec": 0.0,
                "by_regime": {},
                "by_pattern": {},
                "by_score_range": {},
                "ab_comparison": {},
            }

        wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
        pnls = [t.get("pnl_pct", 0) for t in trades]
        holds = [t.get("hold_time_sec", 0) for t in trades if t.get("hold_time_sec", 0) > 0]

        stats = {
            "total_trades": len(trades),
            "win_rate": wins / len(trades) if trades else 0.0,
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "avg_hold_sec": sum(holds) / len(holds) if holds else 0.0,
            "by_regime": self._group_stats(trades, "regime"),
            "by_pattern": self._group_stats(trades, "pattern"),
            "by_score_range": self._group_stats_by_score(trades),
            "ab_comparison": self._ab_comparison(trades),
        }
        return stats

    def _group_stats(self, trades: List[Dict], key: str) -> Dict[str, Dict]:
        """Group trades by a field and compute per-group stats."""
        groups: Dict[str, List] = {}
        for t in trades:
            val = t.get(key, "UNKNOWN")
            groups.setdefault(val, []).append(t)

        result = {}
        for group_name, group_trades in groups.items():
            wins = sum(1 for t in group_trades if t.get("pnl_pct", 0) > 0)
            pnls = [t.get("pnl_pct", 0) for t in group_trades]
            result[group_name] = {
                "count": len(group_trades),
                "win_rate": wins / len(group_trades),
                "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            }
        return result

    def _group_stats_by_score(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Group trades by score range: 3-4, 5-6, 7+."""
        buckets = {"3-4": [], "5-6": [], "7+": []}
        for t in trades:
            score = t.get("score", 0)
            if score <= 4:
                buckets["3-4"].append(t)
            elif score <= 6:
                buckets["5-6"].append(t)
            else:
                buckets["7+"].append(t)

        result = {}
        for label, group in buckets.items():
            if group:
                wins = sum(1 for t in group if t.get("pnl_pct", 0) > 0)
                pnls = [t.get("pnl_pct", 0) for t in group]
                result[label] = {
                    "count": len(group),
                    "win_rate": wins / len(group),
                    "avg_pnl": sum(pnls) / len(pnls),
                }
        return result

    def _ab_comparison(self, trades: List[Dict]) -> Dict[str, Any]:
        """
        A/B comparison: trades with AI veto vs direct execution.

        Returns stats for both groups + delta.
        """
        ai_trades = [t for t in trades if t.get("ai_veto_used")]
        direct_trades = [t for t in trades if not t.get("ai_veto_used")]

        def _calc(group):
            if not group:
                return {"count": 0, "win_rate": 0.0, "avg_pnl": 0.0}
            wins = sum(1 for t in group if t.get("pnl_pct", 0) > 0)
            pnls = [t.get("pnl_pct", 0) for t in group]
            return {
                "count": len(group),
                "win_rate": wins / len(group),
                "avg_pnl": sum(pnls) / len(pnls),
            }

        ai_stats = _calc(ai_trades)
        direct_stats = _calc(direct_trades)

        result = {
            "ai_veto": ai_stats,
            "direct": direct_stats,
        }

        # Delta (positive = AI is better)
        if ai_stats["count"] > 0 and direct_stats["count"] > 0:
            result["delta_win_rate"] = ai_stats["win_rate"] - direct_stats["win_rate"]
            result["delta_avg_pnl"] = ai_stats["avg_pnl"] - direct_stats["avg_pnl"]

        return result

    @property
    def trade_count(self) -> int:
        return len(self._trades)


class ScalpCalibrator:
    """
    Analyzes SCALP performance data and generates calibration suggestions.

    Suggestions target:
      - min_score adjustments (raise if low scores lose)
      - regime-specific thresholds (avoid bad regimes)
      - pattern performance (disable bad patterns)
      - AI veto effectiveness (enable/disable recommendation)
    """

    def __init__(self, tracker: ScalpPerformanceTracker):
        self._tracker = tracker
        self._output_file = os.path.join(DATA_DIR, "scalp_calibration.json")
        self._min_trades = 15  # Min trades before generating suggestions

    def check_and_suggest(self) -> List[Dict[str, Any]]:
        """
        Analyze recent performance and generate suggestions.

        Returns list of suggestion dicts:
          {parameter, current, suggested, reason, confidence}
        """
        if self._tracker.trade_count < self._min_trades:
            return []

        stats = self._tracker.get_stats(last_n=100)
        suggestions = []

        # 1. Score range analysis
        suggestions.extend(self._analyze_score_ranges(stats))

        # 2. Regime analysis
        suggestions.extend(self._analyze_regimes(stats))

        # 3. Pattern analysis
        suggestions.extend(self._analyze_patterns(stats))

        # 4. A/B AI veto analysis
        suggestions.extend(self._analyze_ab(stats))

        if suggestions:
            self._save_suggestions(suggestions)
            info(f"[ScalpCalibrator] Generated {len(suggestions)} suggestions")
            for s in suggestions:
                info(f"  - {s['parameter']}: {s.get('current', '?')} -> {s['suggested']} ({s['reason']})")

        return suggestions

    def _analyze_score_ranges(self, stats: Dict) -> List[Dict]:
        """Suggest min_score increase if low scores have poor win rate."""
        suggestions = []
        by_score = stats.get("by_score_range", {})

        low = by_score.get("3-4", {})
        if low.get("count", 0) >= 5 and low.get("win_rate", 1.0) < 0.35:
            suggestions.append({
                "parameter": "signal_rules.min_score_for_signal",
                "current": 4,
                "suggested": 5,
                "reason": f"Score 3-4 win rate {low['win_rate']*100:.0f}% "
                          f"({low['count']} trades, avg PnL {low['avg_pnl']:.2f}%)",
                "confidence": 0.7,
            })

        mid = by_score.get("5-6", {})
        if mid.get("count", 0) >= 5 and mid.get("win_rate", 1.0) < 0.40:
            suggestions.append({
                "parameter": "signal_rules.min_score_for_signal",
                "current": 4,
                "suggested": 6,
                "reason": f"Score 5-6 win rate {mid['win_rate']*100:.0f}% "
                          f"({mid['count']} trades, avg PnL {mid['avg_pnl']:.2f}%)",
                "confidence": 0.6,
            })

        return suggestions

    def _analyze_regimes(self, stats: Dict) -> List[Dict]:
        """Suggest regime-specific min_score increase for bad-performing regimes."""
        suggestions = []
        by_regime = stats.get("by_regime", {})

        for regime, data in by_regime.items():
            if data.get("count", 0) >= 5 and data.get("win_rate", 1.0) < 0.35:
                suggestions.append({
                    "parameter": f"regime_overrides.{regime}.min_score",
                    "current": "default",
                    "suggested": 7,
                    "reason": f"{regime} win rate {data['win_rate']*100:.0f}% "
                              f"({data['count']} trades, avg PnL {data['avg_pnl']:.2f}%)",
                    "confidence": 0.6,
                })

        return suggestions

    def _analyze_patterns(self, stats: Dict) -> List[Dict]:
        """Flag patterns with consistently negative PnL."""
        suggestions = []
        by_pattern = stats.get("by_pattern", {})

        for pattern, data in by_pattern.items():
            if data.get("count", 0) >= 5 and data.get("avg_pnl", 0) < -0.1:
                suggestions.append({
                    "parameter": f"pattern_filter.{pattern}",
                    "current": "enabled",
                    "suggested": "review",
                    "reason": f"Pattern '{pattern}' avg PnL {data['avg_pnl']:.2f}% "
                              f"({data['count']} trades, WR {data['win_rate']*100:.0f}%)",
                    "confidence": 0.5,
                })

        return suggestions

    def _analyze_ab(self, stats: Dict) -> List[Dict]:
        """Suggest AI veto enable/disable based on A/B comparison."""
        suggestions = []
        ab = stats.get("ab_comparison", {})

        ai = ab.get("ai_veto", {})
        direct = ab.get("direct", {})

        # Need enough data in both groups
        if ai.get("count", 0) < 5 or direct.get("count", 0) < 5:
            return suggestions

        delta_wr = ab.get("delta_win_rate", 0)
        delta_pnl = ab.get("delta_avg_pnl", 0)

        if delta_wr < -0.10 and delta_pnl < -0.05:
            # AI is hurting performance
            suggestions.append({
                "parameter": "ai_integration.veto_enabled",
                "current": True,
                "suggested": False,
                "reason": f"AI veto WR {ai['win_rate']*100:.0f}% vs direct {direct['win_rate']*100:.0f}% "
                          f"(delta WR {delta_wr*100:+.0f}%, delta PnL {delta_pnl:+.2f}%)",
                "confidence": 0.5,
            })
        elif delta_wr > 0.10 and delta_pnl > 0.05:
            # AI is helping — reinforce
            suggestions.append({
                "parameter": "ai_integration.veto_enabled",
                "current": True,
                "suggested": True,
                "reason": f"AI veto improving: WR {ai['win_rate']*100:.0f}% vs direct {direct['win_rate']*100:.0f}% "
                          f"(delta WR {delta_wr*100:+.0f}%, delta PnL {delta_pnl:+.2f}%)",
                "confidence": 0.7,
            })

        return suggestions

    def _save_suggestions(self, suggestions: List[Dict]):
        try:
            os.makedirs(os.path.dirname(self._output_file), exist_ok=True)
            data = {
                "timestamp": datetime.now().isoformat(),
                "trade_count": self._tracker.trade_count,
                "suggestions": suggestions,
            }
            with open(self._output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            warning(f"[ScalpCalibrator] Save error: {e}")
