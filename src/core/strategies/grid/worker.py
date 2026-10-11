"""GRID-worker: public reference в read_only, private вызовы только для исполнения."""

import math
import time
import traceback

from src.core.grid_executor import GridExecutor, get_grid_client
from src.exchanges.market_data import (
    StreamKey, candle_gaps, canonical_symbol, closed_candles, interval_start, next_start, normalize_candle,
)
from src.exchanges.public_market_rest import PublicRESTFetcher, exchange_symbol
from src.exchanges.ws_provider_factory import get_market_snapshot, set_shared_cache
from src.trading_settings import get_trading_settings, validate_trading_settings
from src.utils.log_context import set_log_context
from src.utils.logger import error, info, warning
from .adx import calculate_adx


def _extract_ticker_prices(ticker):
    def number(*names):
        for name in names:
            value = ticker.get(name) if isinstance(ticker, dict) else getattr(ticker, name, None)
            if value is not None:
                return float(value or 0)
        return 0.0
    return number("bid_price", "bid"), number("ask_price", "ask"), number("last_price", "last")


def _market_candles(symbol, settings, *, interval="5m", limit=50):
    key = StreamKey(settings["exchange"], settings["market_type"], symbol, interval)
    snapshot = get_market_snapshot(key, max_age_seconds=settings["market_data"]["max_age_seconds"])
    if snapshot is not None:
        rows = closed_candles(snapshot["candles"], interval)
        if len(rows) >= limit:
            return rows[-limit:]
    # Fallback использует только public REST, без private-клиента и старого symbol cache.
    now = time.time()
    rows = PublicRESTFetcher(settings=settings["market_data"])(key, limit=limit + 1)
    rows = closed_candles([normalize_candle(row, key, source="rest", now=now) for row in rows], interval, now=now)[-limit:]
    if (len(rows) < limit or candle_gaps(rows, interval)
            or next_start(rows[-1]["timestamp"], interval) != interval_start(int(now * 1000), interval)):
        raise ValueError("GRID: public REST не содержит свежую непрерывную историю")
    return rows


def _analyze_volatility(symbol, client, config, settings=None):
    settings = settings or get_trading_settings()
    try:
        rows = _market_candles(symbol, settings)
        if len(rows) < 30:
            return 1.0, True, {"adx": 0, "trend": "UNKNOWN"}
        klines = [{"highPrice": float(r["highPrice"] if "highPrice" in r else r["high"]),
                   "lowPrice": float(r["lowPrice"] if "lowPrice" in r else r["low"]),
                   "closePrice": float(r["closePrice"] if "closePrice" in r else r["close"])} for r in rows]
        ranges = [max(k["highPrice"] - k["lowPrice"], abs(k["highPrice"] - prev["closePrice"]),
                      abs(k["lowPrice"] - prev["closePrice"])) for prev, k in zip(klines[-15:-1], klines[-14:])]
        atr_pct = sum(ranges) / len(ranges) / klines[-1]["closePrice"] * 100
        multiplier = 0.7 if atr_pct < 0.25 else 0.9 if atr_pct < 0.5 else 1.5 if atr_pct > 1 else 1.2 if atr_pct > 0.75 else 1.0
        adx_info = calculate_adx(klines, period=14)
        adx = adx_info["adx"]
        multiplier *= 1.5 if adx >= 30 else 1.2 if adx >= 25 else 0.9 if adx < 20 else 1.0
        return max(0.5, min(2.5, multiplier)), adx >= 40, adx_info
    except Exception as exc:
        warning(f"⚠️ [GRID] Нет достоверных данных волатильности: {exc}")
        return 1.0, True, {"adx": 0, "trend": "UNKNOWN"}


def _reference_price(grid, ticker, last):
    if grid.settings["market_type"] == "spot" or grid.settings["protection"]["trigger_price"] == "last":
        return last if math.isfinite(last) and last > 0 else 0.0
    value = ticker.get("mark_price", 0) if isinstance(ticker, dict) else getattr(ticker, "mark_price", 0)
    mark = float(value or 0)
    if math.isfinite(mark) and mark > 0:
        return mark
    # BingX Ticker DTO не содержит markPrice; используем отдельный PUBLIC endpoint.
    key = StreamKey(grid.settings["exchange"], grid.settings["market_type"], grid.symbol, "1m")
    fetcher = PublicRESTFetcher(settings=grid.settings["market_data"])
    if key.exchange == "bingx":
        payload = fetcher._get(key, "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex",
                               {"symbol": exchange_symbol(key)})
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError("GRID: public premiumIndex не подтверждён")
        raw = payload.get("data")
        field = "markPrice"
    else:
        payload = fetcher._get(key, "https://contract.mexc.com/api/v1/contract/ticker",
                               {"symbol": exchange_symbol(key)})
        if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("code") != 0:
            raise ValueError("GRID: public MEXC ticker не подтверждён")
        raw = payload.get("data")
        field = "fairPrice"
    if not isinstance(raw, dict) or canonical_symbol(raw.get("symbol", "")) != key.symbol:
        raise ValueError("GRID: источник public mark reference не совпадает")
    mark = float(raw.get(field, 0))
    if not math.isfinite(mark) or mark <= 0:
        raise ValueError("GRID: некорректная mark reference")
    return mark


def run_grid_cycle(grid, *, spacing_mult=1.0, should_pause=False):
    """Один цикл выделен для проверки отсутствия private вызовов в read_only."""
    ticker = grid.client.get_ticker(grid.symbol)
    bid, ask, last = _extract_ticker_prices(ticker)
    mid = (bid + ask) / 2 if 0 < bid <= ask else last
    if not math.isfinite(mid) or mid <= 0:
        raise ValueError("GRID: нет достоверной публичной цены")
    ticker_symbol = ticker.get("symbol") if isinstance(ticker, dict) else getattr(ticker, "symbol", None)
    if ticker_symbol and canonical_symbol(ticker_symbol) != canonical_symbol(grid.symbol):
        raise ValueError("GRID: тикер относится к другому символу")
    grid.state.center_price = mid
    grid.state.reference_source = grid.settings["protection"]["trigger_price"]
    try:
        reference = _reference_price(grid, ticker, last)
    except Exception as exc:
        warning(f"⚠️ [GRID] Public reference недоступна: {exc}")
        reference = 0.0
    grid.state.reference_price = reference
    if getattr(grid, "native_market", False) and not grid.read_only:
        grid.native_cycle(mid, reference, spacing_mult=spacing_mult, should_pause=should_pause)
        return {"mid_price": mid, "reference_price": reference, "target_prices": grid.state.native_levels,
                "paused": should_pause or bool(grid.paused_reason),
                "entries_blocked_reason": grid.entries_blocked_reason}
    if not grid.read_only:
        if grid.paused_reason:
            grid.reconcile()
            return {"mid_price": mid, "target_prices": grid.calculate_grid_prices(mid), "paused": True}
        grid.check_filled_orders()
        if grid.paused_reason:
            return {"mid_price": mid, "paused": True}
        grid.update_inventory()
        if not math.isfinite(reference) or reference <= 0:
            grid._pause("Нет достоверной public reference; исполнение GRID остановлено")
            return {"mid_price": mid, "reference_price": reference, "paused": True}
        if grid.check_emergency_conditions(reference):
            closed = grid.emergency_close()
            grid._pause("Emergency завершён; требуется решение оператора" if closed else "Emergency требует сверки")
            return {"mid_price": mid, "paused": True}
    targets = grid.calculate_grid_prices(mid, spacing_mult, grid.calculate_inventory_offset())
    if should_pause:
        if not grid.read_only:
            grid.cancel_managed_orders()
    else:
        grid.sync_orders(targets, mid)
    return {"mid_price": mid, "reference_price": reference, "target_prices": targets,
            "paused": should_pause or bool(grid.paused_reason),
            "entries_blocked_reason": grid.entries_blocked_reason}


def run_grid_worker(symbol, config, runtime_config=None, *, ws_cache=None, ws_ready=None):
    runtime = runtime_config
    if runtime is None:
        from src.config import BOT_CONFIG
        runtime = BOT_CONFIG
    settings = validate_trading_settings(runtime.get("execution") or get_trading_settings())
    instance_id = runtime.get("STRATEGY_INSTANCE_ID", f"{StreamKey(settings['exchange'], settings['market_type'], symbol, '1m').symbol.lower()}_grid")
    # Контекст должен существовать до factory: coordinator привязывает ownership к instance.
    set_log_context(service="bot", exchange=settings["exchange"], market_type=settings["market_type"],
                    symbol=symbol, instance_id=instance_id, strategy="GRID", category="strategy")
    from src.utils.logger import setup_symbol_logger
    setup_symbol_logger(symbol)
    if ws_cache is not None:
        set_shared_cache(ws_cache, ws_ready)
    grid = None
    try:
        client = get_grid_client(settings, runtime, instance_id)
        grid_config = dict(config)
        if settings["exchange"] == "bingx" and settings["market_type"] == "perpetual":
            # Рабочая модель perpetual GRID: local touch, MARKET с native SL/TP, не local SL.
            grid_config.setdefault("entry_execution", "native_market")
        grid = GridExecutor(symbol, grid_config, {**runtime, "STRATEGY_INSTANCE_ID": instance_id}, client=client)
        interval = max(1, float(config.get("check_interval", 5)))
        analysis_interval = max(interval, float(config.get("ai_rebalance_interval", 120)))
        last_analysis = 0
        spacing_mult, should_pause, adx_info = 1.0, False, {"adx": 0, "trend": "UNKNOWN"}
        cycle = 0
        info(f"[GRID] Запущен worker {symbol}, instance={instance_id}, режим={settings['execution_mode']}")
        while True:
            try:
                if time.monotonic() - last_analysis >= analysis_interval:
                    spacing_mult, should_pause, adx_info = _analyze_volatility(symbol, client, config, settings)
                    last_analysis = time.monotonic()
                result = run_grid_cycle(grid, spacing_mult=spacing_mult, should_pause=should_pause)
                cycle += 1
                if cycle % 12 == 0:
                    stats = grid.get_stats()
                    info(f"[GRID] {symbol}: цена={result['mid_price']:.4f}, net={stats['inventory']:.4f}, "
                         f"gross={stats['gross_inventory']:.4f}, заявок={stats['active_orders']}, "
                         f"ADX={adx_info['adx']:.1f}, пауза={result['paused']}, "
                         f"запрет входов={stats['entries_blocked_reason'] or 'нет'}")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                error(f"❌ [GRID] Ошибка цикла: {exc}")
                error(traceback.format_exc())
            time.sleep(interval)
    except KeyboardInterrupt:
        info(f"[GRID] Worker {symbol} остановлен пользователем")
    except Exception as exc:
        error(f"❌ [GRID] Ошибка инициализации {symbol}: {exc}")
        raise
    finally:
        if grid is not None:
            try:
                _graceful_shutdown(grid)
            finally:
                grid.close()


def _graceful_shutdown(grid):
    if not grid.read_only:
        grid.cancel_managed_orders()
    info(f"[GRID] Worker {grid.symbol} завершён; durable-состояние сохранено")
