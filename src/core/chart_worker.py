"""Локальные PNG из каталога source-specific snapshots, без зависимости от списка WS при старте."""

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from src.config import DATA_DIR, CHARTS_DIR, SYMBOLS, CHART_SETTINGS, DEFAULT_PLOTTER_RANGE
from src.core import plotter
from src.exchanges.market_data import StreamKey, canonical_symbol
from src.exchanges.market_data_storage import AtomicJsonStore, list_market_snapshots
from src.utils.logger import info, error, warning


def load_active_trades(data_dir=None):
    try:
        value = AtomicJsonStore(os.path.join(data_dir or DATA_DIR, "active_trades.json")).read(default={})
        if isinstance(value, list):
            return {canonical_symbol(row["symbol"]): row for row in value if isinstance(row, dict) and row.get("symbol")}
        return {canonical_symbol(symbol): row for symbol, row in value.items()}
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        warning(f"⚠️ Не удалось прочитать позиции для графиков: {exc}")
        return {}


def chart_tasks(data_dir, positions=None):
    """Snapshot catalog — источник истины; новые символы/TF не требуют перезапуска chart worker."""
    positions = positions or {}
    tasks = []
    for snapshot in list_market_snapshots(data_dir):
        key = StreamKey.from_value(snapshot["stream"])
        trade = positions.get(key.symbol)
        # Без source metadata нельзя доказать принадлежность позиции графику другой биржи.
        if trade and (trade.get("exchange") != key.exchange or trade.get("market_type") != key.market_type):
            trade = None
        tasks.append((key, snapshot, trade))
    return tasks


def run_chart_worker(config=None, stop_event=None):
    """Main может передать полный config и process-safe Event для штатной остановки."""
    config = config or {}
    data_dir = config.get("DATA_DIR", os.getenv("SERVO_DATA_DIR", DATA_DIR))
    charts_dir = config.get("CHARTS_DIR", CHARTS_DIR)
    settings = config.get("CHART_SETTINGS", CHART_SETTINGS)
    interval = max(0.2, float(settings.get("update_interval", 10)))
    executor = None
    info(f"🎨 Процесс графиков запущен, PID={os.getpid()}")
    try:
        while stop_event is None or not stop_event.is_set():
            started = time.monotonic()
            tasks = chart_tasks(data_dir, load_active_trades(data_dir))
            kwargs = {"config": config, "data_dir": data_dir, "charts_dir": charts_dir}
            chart_range = config.get("DEFAULT_PLOTTER_RANGE", DEFAULT_PLOTTER_RANGE)
            parallel = settings.get("parallel", config.get("ENABLE_PARALLEL_MODE", True))
            if tasks and parallel:
                if executor is None:
                    count = max(1, min(int(settings.get("max_workers", 4)), os.cpu_count() or 1))
                    executor = ProcessPoolExecutor(max_workers=count)
                futures = {
                    executor.submit(plotter.plot_symbol, key.symbol, chart_range, position,
                                    stream_key=key, snapshot=snapshot, **kwargs): key
                    for key, snapshot, position in tasks
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        error(f"❌ Ошибка графика {futures[future].id}: {exc}")
            else:
                for key, snapshot, position in tasks:
                    try:
                        plotter.plot_symbol(key.symbol, chart_range, position, stream_key=key, snapshot=snapshot, **kwargs)
                    except Exception as exc:
                        error(f"❌ Ошибка графика {key.id}: {exc}")
            if not tasks:
                info("🎨 Каталог market-data ещё пуст; ждём atomic snapshots")
            elapsed = time.monotonic() - started
            delay = max(float(settings.get("min_sleep", 0.5)), interval - elapsed)
            if stop_event is not None:
                stop_event.wait(delay)
            else:
                time.sleep(delay)
    except KeyboardInterrupt:
        info("🛑 Процесс графиков остановлен")
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    run_chart_worker()
