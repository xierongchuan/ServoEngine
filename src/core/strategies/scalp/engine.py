"""SCALP pipeline — делегирует в ScalpEngine (dual-loop architecture)."""

from typing import Any, Dict, Optional

from src.utils.logger import warning
from src.core.strategies.base import StrategyPipeline


class ScalpPipeline(StrategyPipeline):
    """Пайплайн SCALP — не имеет run_cycle, использует ScalpEngine с dual-loop."""

    def __init__(self, config: Dict):
        super().__init__(config)

    def run_cycle(self, symbol: str, ws_cache: Any = None, ws_ready: Any = None) -> Optional[Dict]:
        """SCALP не поддерживает run_cycle — используйте ScalpEngine.run() вместо этого."""
        warning("[SCALP] run_cycle not supported — SCALP uses dedicated ScalpEngine with dual-loop architecture")
        return None
