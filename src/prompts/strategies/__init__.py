from src.prompts.strategies.scalp import ScalpStrategy
from src.prompts.strategies.intraday import IntradayStrategy
from src.prompts.strategies.swing import SwingStrategy
from src.prompts.strategies.grid import GridStrategy
from src.prompts.strategies.hybrid import HybridStrategy
from src.prompts.strategies.hybrid_veto import HybridVetoStrategy

STRATEGIES = {
    "SCALP": ScalpStrategy,
    "INTRADAY": IntradayStrategy,
    "SWING": SwingStrategy,
    "GRID": GridStrategy,
    "HYBRID": HybridStrategy,
    "HYBRID_VETO": HybridVetoStrategy,
}
