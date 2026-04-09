"""Валидатор предсказаний — проверка корректности перед исполнением."""

from typing import Dict


def validate_prediction(
    prediction: Dict,
    current_price: float,
    has_position: bool = False
) -> tuple:
    """
    Валидирует предсказание перед исполнением.

    Returns:
        (is_valid: bool, reason: str)
    """
    if not prediction:
        return False, "Empty prediction"

    action = prediction.get("action", "").lower()
    confidence = prediction.get("confidence", 0.0)
    symbol = prediction.get("symbol", "")

    # Required fields
    if not symbol:
        return False, "Missing symbol"

    if not action:
        return False, "Missing action"

    if confidence <= 0:
        return False, "Invalid confidence"

    # Action-specific validation
    if action in ("buy", "sell"):
        if has_position:
            return False, f"Already has position for {symbol}"
        if confidence < 0.5:
            return False, f"Confidence too low ({confidence:.2f})"

    elif action in ("close", "close_partial"):
        if not has_position:
            return False, f"No position to close for {symbol}"

    elif action == "hold":
        if not has_position:
            return False, f"No position to manage for {symbol}"

    else:
        return False, f"Unknown action: {action}"

    return True, "OK"
