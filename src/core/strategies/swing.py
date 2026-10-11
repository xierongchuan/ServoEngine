"""SWING pipeline — сбор данных → HTF → AI анализ → исполнение."""

from typing import Any, Dict, Optional

from src.core import analyzer, predict

from src.core.trade_tracker import TradeTracker

from src.utils.logger import StageTimer
from .base import StrategyPipeline


class SwingPipeline(StrategyPipeline):
    """Пайплайн SWING: сбор данных → анализ (HTF) → AI → исполнение."""

    def __init__(self, config: Dict):
        super().__init__(config)
        self._tracker = None if self.read_only else TradeTracker()

    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        strategy_style = self.config.get("STRATEGY_STYLE", "SWING")
        real_position = self._position_for_symbol(symbol, ws_cache, ws_ready)

        # Decision context
        decision_context = self._journal.get_context(symbol, strategy_style)

        # Analysis (with HTF for swing)
        with StageTimer("Анализ индикаторов", symbol, "🔍"):
            analysis_result = analyzer.analyze_symbol(
                symbol, position=real_position, decision_context=decision_context, config=self.config
            )

        if self._tracker is not None:
            self._tracker.sync_position(symbol, real_position, exchange_client=self._client)

        # AI prediction
        with StageTimer("AI Прогноз", symbol, "🧠"):
            prediction = predict.process_analysis(analysis_result, config=self.config)

        return self._annotate_prediction(prediction, analysis_result)
