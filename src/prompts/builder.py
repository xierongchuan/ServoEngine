"""
PromptBuilder — сборка торгового промпта из модульных блоков и стратегий.
"""

import pathlib

from src.prompts.strategies import STRATEGIES

BLOCKS_DIR = pathlib.Path(__file__).parent / "blocks"

_block_cache: dict[str, str] = {}


def load_block(name: str) -> str:
    """Загружает текстовый блок из файла (с кешированием). Использует pathlib для обхода mock open."""
    if name not in _block_cache:
        _block_cache[name] = (BLOCKS_DIR / name).read_text(encoding="utf-8")
    return _block_cache[name]


class PromptBuilder:
    """Собирает финальный промпт из блоков и стратегии."""

    @staticmethod
    def build(style: str, ctx: dict) -> str:
        strategy_cls = STRATEGIES.get(style)
        if not strategy_cls:
            raise ValueError(f"Unknown strategy style: {style}. Available: {list(STRATEGIES.keys())}")

        strategy = strategy_cls()

        # Добавляем стратегические поля в контекст
        ctx["role_desc"] = strategy.get_role()
        ctx["objective"] = strategy.get_objective()
        ctx["time_horizon"] = strategy.get_time_horizon()
        ctx["strategy_style"] = style

        # Стратегическая секция (## 3.)
        strategy_section = strategy.get_strategy_section(ctx)

        # Decision history (опционально, если передан контекст)
        decision_block = ""
        if ctx.get("decision_history"):
            decision_block = load_block("decision_history.txt").format_map(ctx)

        # Проверяем override блоков от стратегии
        special_situations = strategy.get_special_situations(ctx)
        if special_situations is None:
            special_situations = load_block("special_situations.txt")

        position_management = strategy.get_position_management(ctx)
        if position_management is None:
            position_management = load_block("position_management.txt")

        risk_table = strategy.get_risk_table(ctx)
        if risk_table is None:
            risk_table = load_block("risk_table.txt").format_map(ctx)

        # Собираем блоки
        parts = [
            load_block("role.txt").format_map(ctx),
            load_block("principles.txt"),
            load_block("context_table.txt").format_map(ctx),
            decision_block,
            load_block("market_analysis.txt").format_map(ctx),
            strategy_section,
            special_situations,
            position_management,
            risk_table,
            load_block("candle_history.txt").format_map(ctx),
            load_block("response_format.txt").format_map(ctx),
        ]

        return "\n\n---\n\n".join(p for p in parts if p).strip()
