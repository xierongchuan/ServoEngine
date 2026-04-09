"""SWING pipeline — сбор данных → HTF → AI анализ → исполнение."""

from typing import Any, Dict, Optional

from src.core import analyzer, predict
from src.core.decision_journal import DecisionJournal
from src.core.trade_tracker import TradeTracker
from src.exchanges.exchange_factory import get_exchange_client
from src.utils.logger import StageTimer
from .base import StrategyPipeline


class SwingPipeline(StrategyPipeline):
    """Пайплайн SWING: сбор данных → анализ (HTF) → AI → исполнение."""

    def __init__(self, config: Dict):
        super().__init__(config)
        self._tracker = TradeTracker()
        self._journal = DecisionJournal()
        self._client = get_exchange_client()

    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        from src.config import STRATEGY_STYLE

        # Fetch positions
        all_positions = self._client.get_positions()
        normalized_symbol = symbol.replace("-", "")
        symbol_positions = all_positions.get(normalized_symbol, [])
        real_position = symbol_positions[0] if symbol_positions else None

        # Decision context
        decision_context = self._journal.get_context(symbol, STRATEGY_STYLE)

        # Analysis (with HTF for swing)
        with StageTimer("Анализ индикаторов", symbol, "🔍"):
            analysis_result = analyzer.analyze_symbol(
                symbol, position=real_position, decision_context=decision_context
            )

        self._tracker.sync_position(symbol, real_position, exchange_client=self._client)

        # AI prediction
        with StageTimer("AI Прогноз", symbol, "🧠"):
            prediction = predict.process_analysis(analysis_result)

        return prediction
