"""Сбор данных — цены, новости, HTF."""

import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config import (
    SYMBOLS, DATA_DIR, ENABLE_NEWS, CHART_RANGES,
    DEFAULT_CHART_RANGE, STYLE_PRESETS, STRATEGY_STYLE, BOT_CONFIG,
    parse_interval_minutes, ENABLE_PARALLEL_COLLECTION,
)
from src.utils.logger import info, error, warning
from src.utils.helpers import get_filename
from src.utils.news_api import get_news_for_symbol
from src.exchanges.exchange_factory import get_market_data_client as get_exchange_client


def _runtime_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Вернуть актуальный runtime config.

    В multiprocess runtime значения в src.config обновляются после импорта этого
    модуля, поэтому нельзя полагаться на импортированные константы.
    """
    if config is not None:
        return config

    import src.config as config_module
    return config_module.BOT_CONFIG


def ensure_dirs():
    """Создает необходимые директории."""
    os.makedirs(f"{DATA_DIR}/prices", exist_ok=True)
    os.makedirs(f"{DATA_DIR}/news", exist_ok=True)
    os.makedirs("charts", exist_ok=True)


def _timestamp_seconds(value: Any) -> Optional[float]:
    """Привести timestamp свечи к Unix seconds."""
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        numeric = float(text)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def keep_closed_candles(prices: List[Dict], timeframe: str, now: Optional[float] = None) -> List[Dict]:
    """Убрать незакрытую последнюю свечу, чтобы MACD не перерисовывался внутри таймфрейма."""
    if not prices:
        return prices
    start = _timestamp_seconds(prices[-1].get("snapshotTimeUTC", prices[-1].get("timestamp")))
    if start is None:
        return prices
    duration = parse_interval_minutes(timeframe) * 60
    current = datetime.now(timezone.utc).timestamp() if now is None else now
    return prices[:-1] if start + duration > current else prices


def fetch_prices(symbol: str, config: Optional[Dict[str, Any]] = None) -> List[Dict]:
    """Получает свечи для символа на основе конфигурации стратегии."""
    info(f"📊 Получение цен для {symbol}...")

    client = get_exchange_client()

    try:
        runtime_config = _runtime_config(config)
        chart_ranges = runtime_config.get("CHART_RANGES", CHART_RANGES)
        default_chart_range = runtime_config.get("DEFAULT_CHART_RANGE", DEFAULT_CHART_RANGE)
        style_presets = runtime_config.get("STYLE_PRESETS", STYLE_PRESETS)
        strategy_style = runtime_config.get("STRATEGY_STYLE", STRATEGY_STYLE)

        # Determine interval and limit from config
        chart_config = chart_ranges.get(default_chart_range, {})

        # 1. Get Target Interval (from Strategy Style)
        current_preset = (
            style_presets.get(strategy_style)
            or style_presets.get("AISCALP")
            or {"timeframe": "1m"}
        )
        target_interval_str = current_preset.get("timeframe", "1m")

        # 2. Get Duration in minutes (from Chart Range)
        chart_days = chart_config.get("days", 0)
        chart_hours = chart_config.get("hours", 0)
        chart_minutes = chart_config.get("minutes", 0)
        total_duration_minutes = (chart_days * 1440) + (chart_hours * 60) + chart_minutes

        if total_duration_minutes == 0:
            total_duration_minutes = chart_config.get("candles", 288) * 1

        # 3. Calculate Limit (Duration / Interval)
        interval_minutes = parse_interval_minutes(target_interval_str)
        required_candles = max(1, int(total_duration_minutes // interval_minutes))

        limit = required_candles
        interval = target_interval_str

        info(f"📐 Config: Style={strategy_style}, Range={default_chart_range} ({total_duration_minutes}m), TF={interval}, Candles={limit}")

        prices = client.get_kline_data(symbol, interval=interval, limit=limit)

        if not prices:
            warning(f"⚠️ get_kline_data вернул пустой список для {symbol}")
            raise ValueError(f"API вернул пустой список цен для {symbol}")

        if not isinstance(prices[0], dict):
            raise ValueError(f"Некорректный формат данных о ценах: {type(prices[0])}")

        if strategy_style == "MACDX":
            prices = keep_closed_candles(prices, interval)
            if not prices:
                raise ValueError("Нет закрытых свечей для MACDX")

        info(f"✅ Получено {len(prices)} свечей для {symbol}")
        return prices
    except Exception as e:
        error(f"❌ Ошибка получения цен для {symbol}: {str(e)}")
        raise


def fetch_news(symbol: str) -> List[Dict]:
    """Получает новости для символа."""
    news = get_news_for_symbol(symbol)
    info(f"✅ Получено {len(news)} новостей для {symbol}")

    if news:
        source = news[0].get("source", "Unknown")
        info(f"📰 Источник: {source}")

    return news


def fetch_htf_prices(symbol: str, config: Optional[Dict[str, Any]] = None) -> Optional[List[Dict]]:
    """Fetches higher-timeframe candles for AISCALP multi-timeframe analysis."""
    runtime_config = _runtime_config(config)
    mtf_cfg = runtime_config.get("AISCALP_SETTINGS", {}).get("multi_timeframe", {})
    if not mtf_cfg.get("enabled", True):
        return None

    htf_interval = mtf_cfg.get("htf_timeframe", "1h")
    htf_candles = mtf_cfg.get("htf_candles", 48)

    client = get_exchange_client()

    try:
        info(f"📊 Получение HTF ({htf_interval}) свечей для {symbol}...")
        prices = client.get_kline_data(symbol, interval=htf_interval, limit=htf_candles)

        if not prices:
            raise ValueError(f"API вернул пустой список HTF цен для {symbol}")

        info(f"✅ Получено {len(prices)} HTF свечей для {symbol}")
        return prices
    except Exception as e:
        error(f"❌ Ошибка получения HTF цен для {symbol}: {str(e)}")
        return None


def process_symbol(symbol: str, config: Optional[Dict[str, Any]] = None) -> bool:
    """Обрабатывает один символ: собирает цены и новости."""
    try:
        runtime_config = _runtime_config(config)
        data_dir = runtime_config.get("DATA_DIR", DATA_DIR)
        enable_news = runtime_config.get("ENABLE_NEWS", ENABLE_NEWS)
        os.makedirs(f"{data_dir}/prices", exist_ok=True)
        os.makedirs(f"{data_dir}/news", exist_ok=True)

        # Сбор цен
        prices = fetch_prices(symbol, runtime_config)
        symbol_file = get_filename(symbol)
        prices_file = f"{data_dir}/prices/{symbol_file}.json"
        with open(prices_file, "w") as f:
            json.dump(prices, f)

        # Сбор новостей
        if enable_news:
            news = fetch_news(symbol)
        else:
            news = []

        news_file = f"{data_dir}/news/{symbol_file}.json"
        with open(news_file, "w") as f:
            json.dump(news, f)

        info(f"📊 Данные для {symbol} успешно собраны")
        return True
    except Exception as e:
        error(f"❌ Ошибка обработки {symbol}: {str(e)}")
        return False


def main():
    """Основная функция сбора данных."""
    ensure_dirs()

    if ENABLE_PARALLEL_COLLECTION:
        import concurrent.futures
        max_workers = min(len(SYMBOLS), 10)
        info(f"🚀 Запуск параллельного сбора данных для {len(SYMBOLS)} символов (потоков: {max_workers})...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {executor.submit(process_symbol, symbol): symbol for symbol in SYMBOLS}

            for future in concurrent.futures.as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    future.result()
                except Exception as exc:
                    error(f"❌ Необработанная ошибка при сборе {symbol}: {exc}")
    else:
        info(f"🐌 Запуск последовательного сбора данных для {len(SYMBOLS)} символов...")
        for symbol in SYMBOLS:
            process_symbol(symbol)


if __name__ == "__main__":
    main()
