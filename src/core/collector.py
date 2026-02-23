import os
import json
import time
from src.config import SYMBOLS, DATA_DIR, NEWS_SETTINGS, ENABLE_NEWS, CHART_RANGES, DEFAULT_CHART_RANGE, STYLE_PRESETS, STRATEGY_STYLE, BOT_CONFIG, parse_interval_minutes
from src.utils.logger import info, error
from src.utils.helpers import get_filename
from src.utils.news_api import get_news_for_symbol
from src.exchanges.exchange_factory import get_exchange_client

def ensure_dirs():
    """Создает необходимые директории"""
    os.makedirs(f"{DATA_DIR}/prices", exist_ok=True)
    os.makedirs(f"{DATA_DIR}/news", exist_ok=True)
    os.makedirs("charts", exist_ok=True)

def fetch_prices(symbol):
    """Получает 288 последних свечей (24 часа истории) для символа"""
    info(f"📊 Получение цен для {symbol}...")

    client = get_exchange_client()

    try:
        # Determine interval and limit from config
        chart_config = CHART_RANGES.get(DEFAULT_CHART_RANGE, {})

        # 1. Get Target Interval (from Strategy Style)
        current_preset = STYLE_PRESETS.get(STRATEGY_STYLE, STYLE_PRESETS["INTRADAY"])
        target_interval_str = current_preset.get("timeframe", "1m") # e.g. "5m"

        # 2. Get Duration in minutes (from Chart Range)
        # Chart Range dictates "How far back we look" (e.g. 1D = 1440 minutes)
        chart_days = chart_config.get("days", 0)
        chart_hours = chart_config.get("hours", 0)
        chart_minutes = chart_config.get("minutes", 0)
        total_duration_minutes = (chart_days * 1440) + (chart_hours * 60) + chart_minutes

        # Fallback if duration is 0 (shouldn't happen with valid config)
        if total_duration_minutes == 0:
             # Fallback to hardcoded candles count if provided, assuming 1m base or config error
             total_duration_minutes = chart_config.get("candles", 288) * 1 # Assume 1m worst case

        # 3. Calculate Limit (Duration / Interval)
        interval_minutes = parse_interval_minutes(target_interval_str)
        required_candles = int(total_duration_minutes // interval_minutes)

        # Add buffer (optional, e.g. +10%)
        limit = required_candles
        interval = target_interval_str

        info(f"📐 Config: Style={STRATEGY_STYLE}, Range={DEFAULT_CHART_RANGE} ({total_duration_minutes}m), TF={interval}, Candles={limit}")

        prices = client.get_kline_data(symbol, interval=interval, limit=limit)

        if not prices:
            raise ValueError(f"API вернул пустой список цен для {symbol}")

        # Basic validation of the first candle
        if not isinstance(prices[0], dict):
             raise ValueError(f"Некорректный формат данных о ценах: {type(prices[0])}")

        required_fields = ["closePrice", "snapshotTimeUTC"]
        for field in required_fields:
            if field not in prices[0]:
                # ExchangeClient implementations must normalize field names
                pass

        info(f"✅ Получено {len(prices)} свечей для {symbol}")
        # info(f"   Диапазон: {prices[0]['snapshotTimeUTC']} → {prices[-1]['snapshotTimeUTC']}") # Keys might differ
        return prices
    except Exception as e:
        error(f"❌ Ошибка получения цен для {symbol}: {str(e)}")
        raise

def fetch_news(symbol):
    """Получает новости для символа (только реальные!)"""
    # Используем новый модуль для получения новостей
    news = get_news_for_symbol(symbol)
    info(f"✅ Получено {len(news)} новостей для {symbol}")

    # Логируем источник новостей
    if news:
        source = news[0].get("source", "Unknown")
        info(f"📰 Источник: {source}")

    return news

def fetch_htf_prices(symbol):
    """Fetches higher-timeframe candles for INTRADAY multi-timeframe analysis."""
    mtf_cfg = BOT_CONFIG.get("INTRADAY_SETTINGS", {}).get("multi_timeframe", {})
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


def process_symbol(symbol):
    """Обрабатывает один символ: собирает цены и новости"""
    try:
        # Сбор цен
        prices = fetch_prices(symbol)
        symbol_file = get_filename(symbol)
        prices_file = f"{DATA_DIR}/prices/{symbol_file}.json"
        with open(prices_file, "w") as f:
            json.dump(prices, f)

        # HTF candles for INTRADAY multi-timeframe analysis
        if STRATEGY_STYLE == "INTRADAY":
            htf_prices = fetch_htf_prices(symbol)
            if htf_prices:
                htf_file = f"{DATA_DIR}/prices/{symbol_file}_htf.json"
                with open(htf_file, "w") as f:
                    json.dump(htf_prices, f)

        # Сбор новостей
        if ENABLE_NEWS:
            news = fetch_news(symbol)
        else:
            news = []

        news_file = f"{DATA_DIR}/news/{symbol_file}.json"
        with open(news_file, "w") as f:
            json.dump(news, f)

        info(f"📊 Данные для {symbol} успешно собраны")
        return True
    except Exception as e:
        error(f"❌ Ошибка обработки {symbol}: {str(e)}")
        return False

def main():
    """Основная функция сбора данных"""
    ensure_dirs()

    client = get_exchange_client()
    if not client.check_prerequisites():
        return

    # Check config for parallel collection
    from src.config import ENABLE_PARALLEL_COLLECTION

    if ENABLE_PARALLEL_COLLECTION:
        import concurrent.futures
        max_workers = min(len(SYMBOLS), 10) # Limit concurrency
        info(f"🚀 Запуск параллельного сбора данных для {len(SYMBOLS)} символов (потоков: {max_workers})...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
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
