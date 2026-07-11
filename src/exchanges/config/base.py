"""
Abstract configuration for exchange clients.
Provides unified interface for exchange-specific settings.

Этот модуль определяет контракт для конфигураций бирж.
При добавлении новой биржи нужно реализовать этот интерфейс.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass
import os


@dataclass
class ExchangeConfigBase:
    """
    Базовая конфигурация для всех бирж.
    """
    # API настройки
    api_key: Optional[str] = None
    secret_key: Optional[str] = None

    # URLs
    base_url: str = ""
    ws_url: str = ""
    testnet_url: str = ""

    # Настройки по умолчанию
    default_leverage: int = 10
    max_leverage: int = 125
    default_position_side: str = "BOTH"  # LONG, SHORT, BOTH

    # Таймауты
    request_timeout: float = 6.0

    # Кэширование
    positions_cache_ttl: int = 10  # секунд
    balance_cache_ttl: int = 10  # секунд

    @property
    def is_demo_mode(self) -> bool:
        """Проверка демо режима"""
        return "test" in self.base_url.lower() or "demo" in self.base_url.lower()


class ExchangeConfig(ABC):
    """
    Абстрактный класс конфигурации биржи.

    Каждая биржа должна реализовать этот интерфейс.
    """

    def __init__(self, is_demo: bool = False):
        self._is_demo = is_demo
        self._load_from_env()

    @property
    @abstractmethod
    def name(self) -> str:
        """Название биржи"""
        pass

    @property
    def is_demo(self) -> bool:
        """Демо режим"""
        return self._is_demo

    @property
    @abstractmethod
    def api_key(self) -> Optional[str]:
        """API Key"""
        pass

    @property
    @abstractmethod
    def secret_key(self) -> Optional[str]:
        """Secret Key"""
        pass

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Base URL для REST API"""
        pass

    @property
    @abstractmethod
    def ws_url(self) -> str:
        """WebSocket URL"""
        pass

    @property
    @abstractmethod
    def testnet_url(self) -> str:
        """Testnet URL"""
        pass

    @property
    @abstractmethod
    def supported_intervals(self) -> Dict[str, str]:
        """
        Маппинг интервалов.
        { универсальный: биржевой }
        напр. {"1m": "1min", "5m": "5min"}
        """
        pass

    @property
    def default_leverage(self) -> int:
        """Плечо по умолчанию"""
        return 10

    @property
    def max_leverage(self) -> int:
        """Максимальное плечо"""
        return 125

    @property
    def request_timeout(self) -> float:
        """Таймаут запроса в секундах"""
        return 6.0

    @property
    def positions_cache_ttl(self) -> int:
        """TTL кэша позиций в секундах"""
        return 10

    @property
    def balance_cache_ttl(self) -> int:
        """TTL кэша баланса в секундах"""
        return 10

    @property
    def env_prefix(self) -> str:
        """
        Префикс переменных окружения.
        Переопределить в подклассе (напр. "BINGX", "BINANCE")
        """
        return self.name.upper()

    def get_env(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Получить значение из переменных окружения"""
        return os.getenv(f"{self.env_prefix}_{key}", default)

    def _load_from_env(self) -> None:
        """
        Загрузить настройки из переменных окружения.
        Переопределить в подклассе для специфичных переменных.
        """
        pass

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь (без секретов)"""
        return {
            "name": self.name,
            "is_demo": self.is_demo,
            "base_url": self.base_url,
            "ws_url": self.ws_url,
            "has_api_key": bool(self.api_key),
            "default_leverage": self.default_leverage,
            "max_leverage": self.max_leverage,
        }


class ConfigFactory:
    """
    Фабрика для создания конфигурации биржи.

    Регистрация бирж:
        ConfigFactory.register("bingx", BingXConfig)
        ConfigFactory.register("binance", BinanceConfig)

    Создание:
        config = ConfigFactory.create("bingx", is_demo=True)
    """

    _configs: Dict[str, type[ExchangeConfig]] = {}
    _initialized: bool = False

    @classmethod
    def register(
        cls,
        name: str,
        config_class: type[ExchangeConfig],
        market_type: Optional[str] = None,
    ) -> None:
        """
        Зарегистрировать конфигурацию биржи.

        Args:
            name: Название биржи (bingx, binance, bybit)
            config_class: Класс конфигурации
        """
        name_lower = name.lower()
        key = f"{name_lower}:{market_type.lower()}" if market_type else name_lower
        if key in cls._configs:
            raise ValueError(f"Config for '{key}' already registered")
        cls._configs[key] = config_class

    @classmethod
    def create(
        cls,
        exchange_name: str,
        is_demo: bool = False,
        market_type: Optional[str] = None,
    ) -> ExchangeConfig:
        """
        Создать конфигурацию биржи.

        Args:
            exchange_name: Название биржи
            is_demo: Использовать демо режим

        Returns:
            Конфигурация биржи

        Raises:
            ValueError: Если биржа не зарегистрирована
        """
        name = exchange_name.lower()
        market = (market_type or ("perpetual" if name == "mexc" else "")).lower()
        key = f"{name}:{market}" if market else name

        # Инициализация стандартных бирж при первом вызове
        if not cls._initialized:
            cls._init_default_configs()

        if key not in cls._configs:
            raise ValueError(
                f"Exchange '{exchange_name}' market '{market or 'default'}' not supported. "
                f"Available: {list(cls._configs.keys())}"
            )

        return cls._configs[key](is_demo=is_demo)

    @classmethod
    def _init_default_configs(cls) -> None:
        """Инициализировать стандартные конфигурации"""
        # Импорт здесь чтобы избежать циклических зависимостей
        try:
            from .bingx_config import BingXConfig
            cls.register("bingx", BingXConfig)
        except ImportError:
            # Игнорируем если модуль ещё не создан
            pass

        try:
            from .mexc_config import MEXCFuturesConfig, MEXCSpotConfig
            cls.register("mexc", MEXCFuturesConfig, "perpetual")
            cls.register("mexc", MEXCSpotConfig, "spot")
        except ImportError:
            pass

        cls._initialized = True

    @classmethod
    def get_supported_exchanges(cls) -> list:
        """Получить список поддерживаемых бирж"""
        if not cls._initialized:
            cls._init_default_configs()
        return list(cls._configs.keys())

    @classmethod
    def reset(cls) -> None:
        """Сбросить зарегистрированные конфигурации (для тестирования)"""
        cls._configs.clear()
        cls._initialized = False


def create_config(
    exchange_name: str,
    is_demo: bool = False,
    market_type: Optional[str] = None,
) -> ExchangeConfig:
    """
    Удобная функция для создания конфигурации.

    Args:
        exchange_name: Название биржи
        is_demo: Демо режим

    Returns:
        Конфигурация биржи
    """
    return ConfigFactory.create(exchange_name, is_demo, market_type)
