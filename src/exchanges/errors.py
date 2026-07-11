"""Типизированные ошибки биржевого слоя."""


class ExchangeError(RuntimeError):
    """Базовая ошибка биржевого клиента."""


class UnsupportedCapabilityError(ExchangeError):
    """Операция не поддерживается выбранным торговым продуктом."""


class ExchangeStateUnavailableError(ExchangeError):
    """Состояние аккаунта неизвестно; торговля должна остановиться fail-closed."""


class UnknownOrderStateError(ExchangeError):
    """Биржа могла принять ордер, но подтвердить состояние не удалось."""


class ExchangeAPIError(ExchangeError):
    """Ответ API с кодом ошибки."""

    def __init__(self, message: str, code=None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
