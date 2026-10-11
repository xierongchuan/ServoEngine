"""Базовый интерфейс для всех торговых пайплайнов."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from src.core.commands.models import TradeCommand


class StrategyPipeline(ABC):
    """
    Базовый интерфейс для всех торговых пайплайнов.

    Стратегия генерирует TradeCommand через generate_command().
    Для обратной совместимости run_cycle() продолжает возвращать dict.
    """

    def __init__(self, config: Dict):
        from src.core.data.reader import runtime_data_config, is_read_only
        from src.core.data.journal import create_decision_journal
        self.config = runtime_data_config(config)
        self.read_only = is_read_only(self.config)
        self._client = None
        self._journal = create_decision_journal(self.config)

    def _position_for_symbol(self, symbol, ws_cache=None, ws_ready=None):
        from src.core.data.reader import bind_shared_market_cache, trading_settings_for_config
        from src.exchanges.market_data import canonical_symbol
        bind_shared_market_cache(ws_cache, ws_ready)
        if self.read_only:
            return None
        if self._client is None:
            from src.exchanges.exchange_factory import get_exchange_client
            self._client = get_exchange_client(settings=trading_settings_for_config(self.config))
        positions = self._client.get_positions()
        rows = positions.get(canonical_symbol(symbol), [])
        return rows[0] if rows else None

    def _annotate_prediction(self, prediction, analysis):
        if prediction is not None:
            from src.core.data.reader import validate_analysis_market_data
            prediction["market_data"] = analysis.get("market_data")
            if analysis.get("htf_data") is not None:
                prediction["htf_data"] = analysis["htf_data"]
            prediction["strategy_instance_id"] = self.config.get("STRATEGY_INSTANCE_ID")
            prediction["execution_mode"] = self.config["EXECUTION_MODE"]
            prediction["execution_allowed"] = (not self.read_only and prediction.get("execution_allowed", True)
                                                and validate_analysis_market_data({"symbol": prediction.get("symbol"), **analysis}, self.config))
        return prediction

    def generate_command(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[TradeCommand]:
        """
        Генерирует торговую команду (TradeCommand).

        Это основной метод для интеграции с бэктестами и новыми движками.
        По умолчанию делегирует в run_cycle() и конвертирует результат.
        Стратегии могут переопределить этот метод для нативной генерации TradeCommand.

        Returns: TradeCommand или None
        """
        prediction = self.run_cycle(symbol, ws_cache, ws_ready)
        if prediction is None:
            return None
        return TradeCommand.from_dict(prediction)

    @abstractmethod
    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        """
        Один полный цикл торговли.
        Returns: prediction dict или None
        """
