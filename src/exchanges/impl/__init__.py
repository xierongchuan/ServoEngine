"""
Implementation package for exchange clients.

Содержит реализации конкретных бирж (BingX, Binance, etc.)
"""

# При импорте автоматически регистрируются конфигурации
from ..config.base import ConfigFactory

__all__ = ["ConfigFactory"]
