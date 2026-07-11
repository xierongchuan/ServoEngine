"""Grid Trading Worker — специализированный воркер для Grid стратегии."""

import time
import os
import traceback
from typing import Tuple, Dict

from src.utils.logger import info, error, warning
from src.exchanges.exchange_factory import get_exchange_client
from src.config import ERROR_HANDLING
from src.core.position_ownership import get_position_ownership_store
from src.runtime import normalize_symbol_key
from .executor import GridExecutor
from .adx import calculate_adx


def run_grid_worker(symbol: str, config: dict, runtime_config: dict = None):
    """
    Запускает Grid Trading для одного символа.

    Args:
        symbol: Торговый символ (например, "BTCUSDT")
        config: Конфигурация GRID_SETTINGS из bot_config.json
    """
    try:
        # 1. Настройка логгера
        from src.utils.logger import setup_symbol_logger
        setup_symbol_logger(symbol)
        info(f"[GRID] Starting Grid Worker for {symbol} (PID: {os.getpid()})")

        # 2. Инициализация
        client = get_exchange_client()
        grid = GridExecutor(symbol, config)
        runtime_config = runtime_config or {}
        instance_id = runtime_config.get("STRATEGY_INSTANCE_ID", f"{normalize_symbol_key(symbol)}_grid".lower())
        strategy_id = runtime_config.get("STRATEGY_STYLE", "GRID")
        ownership = get_position_ownership_store()

        # Параметры цикла
        check_interval = config.get("check_interval", 5)
        ai_rebalance_interval = config.get("ai_rebalance_interval", 300)  # 5 min
        last_ai_check = 0
        cycle_count = 0

        info(f"[GRID] Config: check_interval={check_interval}s, ai_rebalance={ai_rebalance_interval}s")
        info(f"[GRID] Grid levels={config.get('grid_levels', 5)}, spacing={config.get('grid_spacing_pct', 0.3)}%")

        # 3. Главный цикл
        while True:
            try:
                cycle_start = time.time()
                cycle_count += 1

                # 3.1 Получаем текущую цену
                ticker = client.get_ticker(symbol)
                bid, ask, _last = _extract_ticker_prices(ticker)

                if bid <= 0 or ask <= 0:
                    warning(f"[GRID] Invalid ticker data: bid={bid}, ask={ask}. Skipping cycle.")
                    time.sleep(check_interval)
                    continue

                mid_price = (bid + ask) / 2

                positions = client.get_positions()
                ownership.sync_with_positions(positions)
                owner = ownership.get_owner(symbol)
                if owner and owner.owner_id != instance_id:
                    info(
                        f"⏸️ [GRID] {symbol}: символ занят стратегией {owner.owner_id} "
                        f"({owner.strategy}); {instance_id} ждёт освобождения"
                    )
                    time.sleep(check_interval)
                    continue

                acquired, owner = ownership.try_acquire(symbol, instance_id, strategy_id)
                if not acquired:
                    info(f"⏸️ [GRID] {symbol}: вход заблокирован владельцем {owner.owner_id if owner else 'unknown'}")
                    time.sleep(check_interval)
                    continue

                _attach_position_id_if_present(ownership, symbol, instance_id, positions)

                # 3.2 Обновляем inventory
                inventory = grid.update_inventory()

                # 3.3 Проверяем emergency условия
                if grid.check_emergency_conditions(mid_price):
                    if grid.emergency_close():
                        ownership.release_if_owner(symbol, instance_id)
                    warning("[GRID] Emergency close triggered. Pausing for 60s...")
                    time.sleep(60)
                    continue

                # 3.4 Рассчитываем смещение центра на основе inventory
                inventory_offset = grid.calculate_inventory_offset()

                # 3.5 Периодически вызываем AI для корректировки spacing и проверки ADX
                spacing_mult = 1.0
                should_pause = False
                adx_info = {"adx": 0, "trend": "UNKNOWN"}
                current_time = time.time()

                if current_time - last_ai_check > ai_rebalance_interval:
                    spacing_mult, should_pause, adx_info = _analyze_volatility(symbol, client, config)
                    last_ai_check = current_time
                    info(f"[GRID] Analysis: ADX={adx_info['adx']:.1f} ({adx_info['trend']}), spacing_mult={spacing_mult:.2f}")

                # 3.5.1 Если ADX показывает сильный тренд - ставим сетку на паузу
                if should_pause:
                    warning(f"[GRID] Strong trend detected (ADX={adx_info['adx']:.1f}) - pausing grid for 60s")
                    grid.cancel_managed_orders()
                    time.sleep(60)
                    continue

                # 3.6 Обновляем центр сетки
                grid.state.center_price = mid_price

                # 3.7 Рассчитываем целевые уровни сетки
                target_prices = grid.calculate_grid_prices(
                    center_price=mid_price,
                    spacing_mult=spacing_mult,
                    inventory_offset=inventory_offset
                )

                # 3.8 Синхронизируем ордера
                grid.sync_orders(target_prices, mid_price)

                # 3.8.1 Проверяем исполненные ордера
                filled_orders = grid.check_filled_orders()
                if filled_orders:
                    positions = client.get_positions()
                    ownership.sync_with_positions(positions)
                    _attach_position_id_if_present(ownership, symbol, instance_id, positions)
                    info(f"[GRID] {len(filled_orders)} order(s) filled this cycle")

                # 3.9 Логирование
                elapsed = time.time() - cycle_start
                stats = grid.get_stats()

                if cycle_count % 12 == 0:  # Каждую минуту (при 5s интервале)
                    info(f"[GRID] {symbol}: mid={mid_price:.4f}, spread={(ask-bid):.6f}, "
                         f"inv={inventory:.4f} ({stats['inventory_pct']:.1f}%), "
                         f"ADX={adx_info['adx']:.1f} ({adx_info['trend']}), "
                         f"orders={stats['active_orders']}, "
                         f"fills={stats['total_filled_buy']}B/{stats['total_filled_sell']}S, "
                         f"net_pnl=${stats['net_pnl']:.2f}, cycle={elapsed:.2f}s")

            except KeyboardInterrupt:
                info(f"[GRID] {symbol} stopped by user (KeyboardInterrupt)")
                _graceful_shutdown(grid)
                _release_grid_owner_if_flat(ownership, client, symbol, instance_id)
                return

            except Exception as e:
                error(f"[GRID] Error in cycle: {e}")
                error(traceback.format_exc())
                sleep_time = ERROR_HANDLING.get("cycle_error_fallback_sleep", 5)
                time.sleep(sleep_time)
                continue

            # Пауза между циклами
            time.sleep(check_interval)

    except KeyboardInterrupt:
        print(f"[GRID] {symbol} terminated.")
    except Exception as e:
        print(f"[GRID] CRITICAL INIT ERROR {symbol}: {e}")
        traceback.print_exc()


def _analyze_volatility(symbol: str, client, config: dict) -> Tuple[float, bool, Dict]:
    """
    Анализирует волатильность и силу тренда для рекомендации spacing.

    Returns:
        tuple: (spacing_mult: float, should_pause: bool, adx_info: dict)
    """
    try:
        # 1. Получаем последние свечи (нужно больше для ADX)
        klines = client.get_kline_data(symbol, interval="5m", limit=50)

        if not klines or len(klines) < 20:
            return 1.0, False, {"adx": 0, "trend": "UNKNOWN"}

        # 2. Рассчитываем ATR
        atr_values = []
        for i in range(1, min(15, len(klines))):
            high = klines[i]["highPrice"]
            low = klines[i]["lowPrice"]
            prev_close = klines[i - 1]["closePrice"]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            atr_values.append(tr)

        current_atr = sum(atr_values) / len(atr_values) if atr_values else 0
        current_price = klines[-1]["closePrice"]
        atr_pct = (current_atr / current_price) * 100 if current_price > 0 else 0

        # 3. Рассчитываем ADX
        adx_info = calculate_adx(klines, period=14)
        adx = adx_info["adx"]
        trend = adx_info["trend"]

        # 4. Определяем spacing_mult на основе ATR
        base_atr_pct = 0.5

        if atr_pct < base_atr_pct * 0.5:
            spacing_mult = 0.7
        elif atr_pct < base_atr_pct:
            spacing_mult = 0.9
        elif atr_pct > base_atr_pct * 2:
            spacing_mult = 1.5
        elif atr_pct > base_atr_pct * 1.5:
            spacing_mult = 1.2
        else:
            spacing_mult = 1.0

        # 5. Корректируем на основе ADX
        should_pause = False

        if adx >= 40:
            # Очень сильный тренд - ПАУЗА
            should_pause = True
            info(f"[GRID] ADX={adx:.1f} ({trend}) - PAUSE recommended (strong trend)")

        elif adx >= 30:
            # Сильный тренд - расширяем spacing на 50%
            spacing_mult *= 1.5
            info(f"[GRID] ADX={adx:.1f} ({trend}) - widening spacing x1.5")

        elif adx >= 25:
            # Умеренный тренд - расширяем spacing на 20%
            spacing_mult *= 1.2
            info(f"[GRID] ADX={adx:.1f} ({trend}) - widening spacing x1.2")

        elif adx < 20:
            # Боковик - идеально для Grid, можно сузить spacing
            spacing_mult *= 0.9
            info(f"[GRID] ADX={adx:.1f} ({trend}) - ranging market, good for grid")

        # Ограничиваем диапазон
        spacing_mult = max(0.5, min(2.5, spacing_mult))

        return spacing_mult, should_pause, adx_info

    except Exception as e:
        warning(f"[GRID] Volatility analysis failed: {e}")
        return 1.0, False, {"adx": 0, "trend": "UNKNOWN"}


def _extract_ticker_prices(ticker) -> Tuple[float, float, float]:
    """Возвращает bid/ask/last из DTO Ticker или legacy dict."""
    if hasattr(ticker, "bid_price"):
        return (
            float(getattr(ticker, "bid_price", 0) or 0),
            float(getattr(ticker, "ask_price", 0) or 0),
            float(getattr(ticker, "last_price", 0) or 0),
        )

    return (
        float(ticker.get("bid", 0) or ticker.get("bid_price", 0) or 0),
        float(ticker.get("ask", 0) or ticker.get("ask_price", 0) or 0),
        float(ticker.get("last", 0) or ticker.get("last_price", 0) or 0),
    )


def _attach_position_id_if_present(ownership, symbol: str, owner_id: str, positions: Dict) -> None:
    """Сохраняет position_id владельца, если позиция уже появилась на бирже."""
    position = _first_symbol_position(symbol, positions)
    if not position:
        return

    position_id = getattr(position, "position_id", None)
    if position_id is None and isinstance(position, dict):
        position_id = position.get("position_id") or position.get("dealId") or position.get("positionId")
    if position_id:
        ownership.update_position_id(symbol, owner_id, str(position_id))


def _release_grid_owner_if_flat(ownership, client, symbol: str, owner_id: str) -> None:
    """Освобождает GRID ownership после остановки, если позиции уже нет."""
    try:
        positions = client.get_positions()
        if not _first_symbol_position(symbol, positions):
            ownership.release_if_owner(symbol, owner_id)
    except Exception as e:
        warning(f"[GRID] Failed to release owner on shutdown: {e}")


def _first_symbol_position(symbol: str, positions: Dict):
    target_symbol = normalize_symbol_key(symbol)
    for position_symbol, symbol_positions in positions.items():
        if normalize_symbol_key(position_symbol) == target_symbol and symbol_positions:
            return symbol_positions[0]
    return None


def _graceful_shutdown(grid: GridExecutor):
    """Graceful shutdown - отменяем ордера при остановке."""
    try:
        info("[GRID] Graceful shutdown - cancelling managed orders...")
        grid.cancel_managed_orders()
        info("[GRID] Shutdown complete.")
    except Exception as e:
        error(f"[GRID] Error during shutdown: {e}")
