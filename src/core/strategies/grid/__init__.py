"""Компоненты GRID с ленивыми импортами, без создания клиента при импорте."""

__all__ = ["GridExecutor", "GridLevel", "GridState", "run_grid_worker", "calculate_adx"]


def __getattr__(name):
    if name in {"GridExecutor", "GridLevel", "GridState"}:
        from . import executor
        return getattr(executor, name)
    if name == "run_grid_worker":
        from .worker import run_grid_worker
        return run_grid_worker
    if name == "calculate_adx":
        from .adx import calculate_adx
        return calculate_adx
    raise AttributeError(name)
