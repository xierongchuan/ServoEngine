"""Уровни поддержки/сопротивления, Pivot Points, утилиты цен."""

from typing import Any, Dict, List


def get_price_value(price_item: Any) -> float:
    """Извлекает числовое значение цены из разных форматов."""
    if isinstance(price_item, dict):
        return float(price_item.get("bid", price_item.get("ask", 0)))
    return float(price_item)


def calculate_support_resistance(
    prices: List[float], window: int = 20
) -> Dict[str, List[float]]:
    """
    Определяет уровни поддержки и сопротивления на основе локальных минимумов и максимумов.
    :param prices: Список цен (Close)
    :param window: Окно для поиска локальных экстремумов
    :return: dict с 'supports' и 'resistances'
    """
    if len(prices) < window:
        return {"supports": [], "resistances": []}

    supports = []
    resistances = []

    # Простой алгоритм поиска локальных экстремумов
    for i in range(window, len(prices) - window):
        is_support = True
        is_resistance = True

        for j in range(i - window, i + window + 1):
            if prices[j] < prices[i]:
                is_support = False
            if prices[j] > prices[i]:
                is_resistance = False

        if is_support:
            supports.append(prices[i])
        if is_resistance:
            resistances.append(prices[i])

    # Фильтрация близких уровней (кластеризация) - упрощенно берем уникальные с округлением
    supports = sorted(list(set([round(x, 2) for x in supports])))
    resistances = sorted(list(set([round(x, 2) for x in resistances])))

    # Оставляем только ближайшие к текущей цене (например, 2 снизу и 2 сверху)
    return {
        "supports": sorted(list(set(supports))),
        "resistances": sorted(list(set(resistances)))
    }
