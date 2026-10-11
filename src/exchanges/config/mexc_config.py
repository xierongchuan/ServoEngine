"""Конфигурации MEXC Spot и USDT-M perpetual."""

import os
from typing import Any, Dict, Optional

from src.trading_settings import get_trading_settings, validate_trading_settings

from .base import ExchangeConfig


class _MEXCConfigBase(ExchangeConfig):
    def __init__(self, is_demo: bool = False, settings: Optional[Dict[str, Any]] = None):
        selected = validate_trading_settings(settings) if settings is not None else get_trading_settings()
        if settings is None and selected["exchange"] != "mexc":
            selected["execution_mode"] = "read_only"
        selected["exchange"] = "mexc"
        selected["market_type"] = "spot" if isinstance(self, MEXCSpotConfig) else "perpetual"
        if selected["market_type"] == "spot" and settings is None:
            selected.update(position_mode="one_way", grid_mode="inventory")
            selected["protection"]["trigger_price"] = "last"
        if is_demo:
            selected["execution_mode"] = "read_only"
        self._settings = validate_trading_settings(selected)
        # read_only работает с production API; is_demo не должен притворяться sandbox.
        super().__init__(is_demo=False)

    @property
    def execution_mode(self) -> str:
        return self._settings["execution_mode"]

    @property
    def name(self) -> str:
        return "mexc"

    @property
    def env_prefix(self) -> str:
        return "MEXC"

    @property
    def api_key(self) -> Optional[str]:
        return self._api_key

    @property
    def secret_key(self) -> Optional[str]:
        return self._secret_key

    @property
    def base_url(self) -> str:
        # У MEXC нет официального API sandbox: URL всегда production.
        return "https://api.mexc.com"

    @property
    def testnet_url(self) -> str:
        return ""

    @property
    def live_trading_enabled(self) -> bool:
        return self.execution_mode == "live"

    @property
    def settle_asset(self) -> str:
        return self._settings["settle_asset"]

    def _load_from_env(self) -> None:
        self._api_key = os.getenv("MEXC_API_KEY", "")
        self._secret_key = os.getenv("MEXC_SECRET_KEY", "")


class MEXCFuturesConfig(_MEXCConfigBase):
    @property
    def ws_url(self) -> str:
        return "wss://contract.mexc.com/edge"

    @property
    def supported_intervals(self) -> Dict[str, str]:
        return {
            "1m": "Min1", "5m": "Min5", "15m": "Min15", "30m": "Min30",
            "1h": "Min60", "4h": "Hour4", "8h": "Hour8", "1d": "Day1",
            "1w": "Week1", "1M": "Month1",
        }

    @property
    def default_leverage(self) -> int:
        return 10

    @property
    def max_leverage(self) -> int:
        return 500

    @property
    def margin_mode(self) -> str:
        return self._settings["margin_mode"]

    @property
    def position_mode(self) -> str:
        return self._settings["position_mode"]


class MEXCSpotConfig(_MEXCConfigBase):
    @property
    def ws_url(self) -> str:
        return "wss://wbs-api.mexc.com/ws"

    @property
    def supported_intervals(self) -> Dict[str, str]:
        return {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "60m", "4h": "4h", "1d": "1d", "1w": "1W", "1M": "1M",
        }

    @property
    def default_leverage(self) -> int:
        return 1

    @property
    def max_leverage(self) -> int:
        return 1
