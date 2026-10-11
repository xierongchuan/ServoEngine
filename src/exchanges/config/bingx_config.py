"""
BingX exchange configuration.
"""

import os
from copy import deepcopy
from typing import Optional, Dict

from .base import ExchangeConfig


class BingXConfig(ExchangeConfig):
    """
    Конфигурация для BingX биржи.
    """

    @property
    def name(self) -> str:
        return "bingx"

    @property
    def env_prefix(self) -> str:
        return "BINGX"

    @property
    def api_key(self) -> Optional[str]:
        return self._api_key

    @property
    def secret_key(self) -> Optional[str]:
        return self._secret_key

    @property
    def base_url(self) -> str:
        if self._is_demo:
            return "https://open-api-vst.bingx.com"
        return "https://open-api.bingx.com"

    @property
    def ws_url(self) -> str:
        # BingX WebSocket URL одинаковый для demo и real (публичные данные)
        return "wss://open-api-ws.bingx.com/market"

    @property
    def testnet_url(self) -> str:
        return "https://open-api-vst.bingx.com"

    @property
    def supported_intervals(self) -> Dict[str, str]:
        """REST kline interval mapping for BingX Swap API.

        The current swap v3 klines endpoint accepts compact intervals such as
        5m, 15m, and 1h. WebSocket stream names use a different long format
        and are mapped separately in bingx_ws_data_provider.
        """
        return {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "8h": "8h",
            "12h": "12h",
            "1d": "1d",
            "3d": "3d",
            "1w": "1w",
            "1M": "1M",
        }

    @property
    def default_leverage(self) -> int:
        return 10

    @property
    def max_leverage(self) -> int:
        return 100  # BingX max leverage

    @property
    def positions_cache_ttl(self) -> int:
        # Общий аккаунт: ручные изменения требуют свежего снимка.
        return 0

    @property
    def balance_cache_ttl(self) -> int:
        return 10

    def _load_from_env(self) -> None:
        """Загрузить настройки из переменных окружения"""
        self._api_key = os.getenv("BINGX_API_KEY", "")
        self._secret_key = os.getenv("BINGX_SECRET_KEY", "")

        # Также поддерживаем старые переменные (для совместимости)
        if not self._api_key:
            self._api_key = os.getenv("BINGX_KEY", "")
        if not self._secret_key:
            self._secret_key = os.getenv("BINGX_SECRET", "")

    def __init__(self, is_demo: Optional[bool] = None, settings=None, *, trading_settings=None):
        from src.trading_settings import get_trading_settings, validate_trading_settings
        if settings is not None and trading_settings is not None:
            raise ValueError("BingX: передайте settings или trading_settings, не оба")
        supplied = trading_settings if trading_settings is not None else settings
        self._runtime_settings = supplied is None and is_demo is None
        if supplied is None:
            supplied = get_trading_settings()
            if is_demo is True:
                supplied = dict(supplied, exchange="bingx", market_type="perpetual", execution_mode="demo")
            elif is_demo is False:
                # Старый флаг real не является разрешением на live.
                supplied = dict(supplied, exchange="bingx", market_type="perpetual", execution_mode="read_only")
        self._settings = validate_trading_settings(supplied)
        if self._settings["exchange"] != "bingx" or self._settings["market_type"] != "perpetual":
            raise ValueError("BingXConfig требует BingX perpetual")
        super().__init__(self._settings["execution_mode"] == "demo")

    @property
    def trading_settings(self):
        if self._runtime_settings:
            from src.trading_settings import get_trading_settings
            return get_trading_settings()
        return deepcopy(self._settings)

    def to_dict(self) -> dict:
        base = super().to_dict()
        base.update({
            "intervals": list(self.supported_intervals.keys()),
            "trading_settings": self.trading_settings,
        })
        return base
