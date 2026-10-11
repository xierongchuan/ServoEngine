"""Legacy путь collector: единая реализация live/closed views и строгих потоков."""

from src.core.data.collector import (
    ensure_dirs,
    fetch_price_snapshot,
    fetch_prices,
    fetch_news,
    fetch_htf_prices,
    keep_closed_candles,
    process_symbol,
    main,
)

__all__ = [
    "ensure_dirs", "fetch_price_snapshot", "fetch_prices", "fetch_news", "fetch_htf_prices",
    "keep_closed_candles", "process_symbol", "main",
]

if __name__ == "__main__":
    main()
