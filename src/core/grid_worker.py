"""
Grid Trading Worker - специализированный воркер для Grid стратегии.

Отличия от process_worker:
- Более быстрый цикл (5 сек vs 60+ сек)
- Периодические вызовы AI для ребалансировки (каждые 5 мин)
- Фокус на синхронизации сетки ордеров
"""

import time
import os
import traceback


def run_grid_worker(symbol: str, config: dict):
    """
    Запускает Grid Trading для одного символа.

    Args:
        symbol: Торговый символ (например, "BTCUSDT")
        config: Конфигурация GRID_SETTINGS из bot_config.json
    """
    try:
        # 1. Настройка логгера
        from src.utils.logger import setup_symbol_logger, info, error, warning

        setup_symbol_logger(symbol)
        info(f"[GRID] Starting Grid Worker for {symbol} (PID: {os.getpid()})")

        # 2. Импорты
        from src.core.grid_executor import GridExecutor
        from src.exchanges.exchange_factory import get_exchange_client
        from src.config import ERROR_HANDLING

        # 3. Инициализация
        client = get_exchange_client()
        grid = GridExecutor(symbol, config)

        # Параметры цикла
        check_interval = config.get("check_interval", 5)
        ai_rebalance_interval = config.get("ai_rebalance_interval", 300)  # 5 min
        last_ai_check = 0
        cycle_count = 0

        info(f"[GRID] Config: check_interval={check_interval}s, ai_rebalance={ai_rebalance_interval}s")
        info(f"[GRID] Grid levels={config.get('grid_levels', 5)}, spacing={config.get('grid_spacing_pct', 0.3)}%")

        # 4. Главный цикл
        while True:
            try:
                cycle_start = time.time()
                cycle_count += 1

                # 4.1 Получаем текущую цену
                ticker = client.get_ticker(symbol)
                bid = ticker.get("bid", 0)
                ask = ticker.get("ask", 0)
                ticker.get("last", 0)

                if bid <= 0 or ask <= 0:
                    warning(f"[GRID] Invalid ticker data: bid={bid}, ask={ask}. Skipping cycle.")
                    time.sleep(check_interval)
                    continue

                mid_price = (bid + ask) / 2

                # 4.2 Обновляем inventory
                inventory = grid.update_inventory()

                # 4.3 Проверяем emergency условия
                if grid.check_emergency_conditions(mid_price):
                    grid.emergency_close()
                    warning("[GRID] Emergency close triggered. Pausing for 60s...")
                    time.sleep(60)
                    continue

                # 4.4 Рассчитываем смещение центра на основе inventory
                inventory_offset = grid.calculate_inventory_offset()

                # 4.5 Периодически вызываем AI для корректировки spacing и проверки ADX
                spacing_mult = 1.0
                should_pause = False
                adx_info = {"adx": 0, "trend": "UNKNOWN"}
                current_time = time.time()

                if current_time - last_ai_check > ai_rebalance_interval:
                    spacing_mult, should_pause, adx_info = _ai_analyze_volatility(symbol, client, config)
                    last_ai_check = current_time
                    info(f"[GRID] Analysis: ADX={adx_info['adx']:.1f} ({adx_info['trend']}), spacing_mult={spacing_mult:.2f}")

                # 4.5.1 Если ADX показывает сильный тренд - ставим сетку на паузу
                if should_pause:
                    warning(f"[GRID] Strong trend detected (ADX={adx_info['adx']:.1f}) - pausing grid for 60s")
                    # Отменяем все ордера при паузе
                    client.cancel_all_orders(symbol)
                    time.sleep(60)
                    continue

                # 4.6 Обновляем центр сетки
                grid.state.center_price = mid_price

                # 4.7 Рассчитываем целевые уровни сетки
                target_prices = grid.calculate_grid_prices(
                    center_price=mid_price,
                    spacing_mult=spacing_mult,
                    inventory_offset=inventory_offset
                )

                # 4.8 Синхронизируем ордера
                grid.sync_orders(target_prices, mid_price)

                # 4.8.1 Проверяем исполненные ордера
                filled_orders = grid.check_filled_orders()
                if filled_orders:
                    info(f"[GRID] {len(filled_orders)} order(s) filled this cycle")

                # 4.9 Логирование
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


def _calculate_adx(klines: list, period: int = 14) -> dict:
    """
    Рассчитывает ADX (Average Directional Index) и +DI/-DI.

    Args:
        klines: Список свечей с highPrice, lowPrice, closePrice
        period: Период для расчета (обычно 14)

    Returns:
        {"adx": float, "plus_di": float, "minus_di": float, "trend": str}
    """
    if len(klines) < period + 1:
        return {"adx": 0, "plus_di": 0, "minus_di": 0, "trend": "UNKNOWN"}

    # 1. Рассчитываем +DM, -DM и TR
    plus_dm = []
    minus_dm = []
    tr_values = []

    for i in range(1, len(klines)):
        high = klines[i]["highPrice"]
        low = klines[i]["lowPrice"]
        prev_high = klines[i-1]["highPrice"]
        prev_low = klines[i-1]["lowPrice"]
        prev_close = klines[i-1]["closePrice"]

        # True Range
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_values.append(tr)

        # Directional Movement
        up_move = high - prev_high
        down_move = prev_low - low

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)

    # 2. Smoothed averages (Wilder's smoothing)
    def wilder_smooth(values, period):
        if len(values) < period:
            return []
        smoothed = [sum(values[:period])]
        for i in range(period, len(values)):
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + values[i])
        return smoothed

    smoothed_tr = wilder_smooth(tr_values, period)
    smoothed_plus_dm = wilder_smooth(plus_dm, period)
    smoothed_minus_dm = wilder_smooth(minus_dm, period)

    if not smoothed_tr or smoothed_tr[-1] == 0:
        return {"adx": 0, "plus_di": 0, "minus_di": 0, "trend": "UNKNOWN"}

    # 3. +DI и -DI
    plus_di_values = []
    minus_di_values = []
    dx_values = []

    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] > 0:
            plus_di = 100 * smoothed_plus_dm[i] / smoothed_tr[i]
            minus_di = 100 * smoothed_minus_dm[i] / smoothed_tr[i]
        else:
            plus_di = 0
            minus_di = 0

        plus_di_values.append(plus_di)
        minus_di_values.append(minus_di)

        # DX
        di_sum = plus_di + minus_di
        if di_sum > 0:
            dx = 100 * abs(plus_di - minus_di) / di_sum
        else:
            dx = 0
        dx_values.append(dx)

    # 4. ADX = smoothed DX
    if len(dx_values) >= period:
        adx_values = wilder_smooth(dx_values, period)
        adx = adx_values[-1] if adx_values else 0
    else:
        adx = sum(dx_values) / len(dx_values) if dx_values else 0

    plus_di = plus_di_values[-1] if plus_di_values else 0
    minus_di = minus_di_values[-1] if minus_di_values else 0

    # 5. Определяем тренд
    if adx < 20:
        trend = "RANGING"  # Боковик - идеально для Grid
    elif adx < 25:
        trend = "WEAK_TREND"
    elif adx < 40:
        if plus_di > minus_di:
            trend = "TRENDING_UP"
        else:
            trend = "TRENDING_DOWN"
    else:
        if plus_di > minus_di:
            trend = "STRONG_TREND_UP"
        else:
            trend = "STRONG_TREND_DOWN"

    return {
        "adx": round(adx, 2),
        "plus_di": round(plus_di, 2),
        "minus_di": round(minus_di, 2),
        "trend": trend
    }


def _ai_analyze_volatility(symbol: str, client, config: dict) -> tuple:
    """
    Анализирует волатильность и силу тренда для рекомендации spacing.

    Returns:
        tuple: (spacing_mult: float, should_pause: bool, adx_info: dict)
    """
    try:
        from src.utils.logger import info, warning

        # 1. Получаем последние свечи (нужно больше для ADX)
        klines = client.get_kline_data(symbol, interval="5m", limit=50)

        if not klines or len(klines) < 20:
            return 1.0, False, {"adx": 0, "trend": "UNKNOWN"}

        # 2. Рассчитываем ATR
        atr_values = []
        for i in range(1, min(15, len(klines))):
            high = klines[i]["highPrice"]
            low = klines[i]["lowPrice"]
            prev_close = klines[i-1]["closePrice"]

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
        adx_info = _calculate_adx(klines, period=14)
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
        from src.utils.logger import warning
        warning(f"[GRID] Volatility analysis failed: {e}")
        return 1.0, False, {"adx": 0, "trend": "UNKNOWN"}


def _call_llm_for_spacing(symbol: str, klines: list, atr_pct: float, config: dict) -> float:
    """
    Вызывает LLM для анализа и рекомендации spacing.
    """
    try:
        from src.core.predict import call_llm
        import json

        # Формируем контекст для LLM
        recent_prices = [k["closePrice"] for k in klines[-20:]]
        price_change_pct = ((recent_prices[-1] - recent_prices[0]) / recent_prices[0]) * 100

        prompt = f"""Ты - Grid Trading Bot анализатор. Оцени текущую рыночную ситуацию и рекомендуй коэффициент spacing для сетки.

Символ: {symbol}
ATR%: {atr_pct:.3f}%
Изменение цены за 20 свечей: {price_change_pct:.2f}%
Текущая цена: {recent_prices[-1]:.4f}

Правила:
- spacing_mult < 1.0: сужаем сетку (низкая волатильность, боковик)
- spacing_mult = 1.0: нормальная сетка
- spacing_mult > 1.0: расширяем сетку (высокая волатильность, тренд)
- Диапазон: 0.5 - 2.0

Ответь ТОЛЬКО JSON: {{"spacing_mult": X, "reason": "краткая причина"}}"""

        response = call_llm(prompt, max_tokens=100)

        if response:
            # Парсим JSON из ответа
            json_str = response.strip()
            if "```" in json_str:
                json_str = json_str.split("```")[1].replace("json", "").strip()

            data = json.loads(json_str)
            spacing_mult = float(data.get("spacing_mult", 1.0))

            # Ограничиваем диапазон
            spacing_mult = max(0.5, min(2.0, spacing_mult))

            from src.utils.logger import info
            info(f"[GRID] LLM spacing recommendation: {spacing_mult} - {data.get('reason', 'N/A')}")

            return spacing_mult

    except Exception as e:
        from src.utils.logger import warning
        warning(f"[GRID] LLM spacing call failed: {e}")

    return 1.0


def _graceful_shutdown(grid):
    """Graceful shutdown - отменяем ордера при остановке."""
    try:
        from src.utils.logger import info
        info("[GRID] Graceful shutdown - cancelling all orders...")
        grid.client.cancel_all_orders(grid.symbol)
        info("[GRID] Shutdown complete.")
    except Exception as e:
        from src.utils.logger import error
        error(f"[GRID] Error during shutdown: {e}")
