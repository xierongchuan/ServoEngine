from src.prompts.strategies.scalp import ScalpStrategy
from src.prompts.strategies.scalp_veto import ScalpVetoStrategy
from src.prompts.strategies.scalp_regime import ScalpRegimeStrategy
from src.prompts.strategies.aiscalp import AiScalpStrategy
from src.prompts.strategies.swing import SwingStrategy
from src.prompts.strategies.swing_veto import SwingVetoStrategy
from src.prompts.strategies.grid import GridStrategy
from src.prompts.strategies.hybrid import HybridStrategy
from src.prompts.strategies.hybrid_veto import HybridVetoStrategy
from src.prompts.strategies.macdx import MACDXStrategy

STRATEGIES = {
    "SCALP": ScalpStrategy,
    "SCALP_VETO": ScalpVetoStrategy,
    "SCALP_REGIME": ScalpRegimeStrategy,
    "AISCALP": AiScalpStrategy,
    "SWING": SwingStrategy,
    "SWING_VETO": SwingVetoStrategy,
    "GRID": GridStrategy,
    "HYBRID": HybridStrategy,
    "HYBRID_VETO": HybridVetoStrategy,
    "MACDX": MACDXStrategy,
}
