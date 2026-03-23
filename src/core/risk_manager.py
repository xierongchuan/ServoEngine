"""
Risk Manager - динамическое управление стоп-лоссом, тейк-профитом и размером позиции.

Модуль обеспечивает:
- Динамический расчет SL/TP на основе ATR с валидацией по S/R уровням
- Адаптивный размер позиции с учетом качества сигнала и режима рынка
- Учет последних результатов (hot/cold streak)
"""

from typing import Dict, Optional
from src.config import BOT_CONFIG
from src.utils.logger import info, warning


def calculate_dynamic_sl_tp(
    signal: str,
    current_price: float,
    atr: float,
    support: float,
    resistance: float,
    regime: Dict,
    quality: float
) -> Dict[str, float]:
    """
    Расчет динамических уровней SL/TP с учетом ATR, S/R и качества сигнала.

    Args:
        signal: Направление сигнала ('BUY' или 'SELL')
        current_price: Текущая цена актива
        atr: Average True Range (основной якорь для расчета)
        support: Уровень поддержки
        resistance: Уровень сопротивления
        regime: Словарь с параметрами режима рынка (sl_multiplier, tp_multiplier)
        quality: Качество сигнала [0.0-1.0] для корректировки агрессивности

    Returns:
        Dict с полями:
            - stop_loss: Уровень стоп-лосса
            - take_profit: Уровень тейк-профита
            - risk_reward: Соотношение риск/прибыль
            - risk_pct: Риск в процентах от цены
            - reward_pct: Потенциальная прибыль в процентах
    """
    # Получаем мультипликаторы из режима рынка
    # Используем значения из HYBRID config по умолчанию: atr_sl_mult=1.5, atr_tp_mult=3
    sl_mult = regime.get("sl_multiplier", 1.5)
    tp_mult = regime.get("tp_multiplier", 3.0)

    # Корректировка на основе качества сигнала
    # Высокое качество → более узкий SL (0.8-1.0)
    quality_sl_adj = 1.0 - (quality * 0.2)
    # Высокое качество → более широкий TP (1.0-1.3)
    quality_tp_adj = 1.0 + (quality * 0.3)

    info(f"🎯 Расчет SL/TP | Signal: {signal}, Price: {current_price:.4f}, ATR: {atr:.4f}")
    info(f"   Режим: SL_mult={sl_mult}, TP_mult={tp_mult} | Quality: {quality:.2f} (SL_adj={quality_sl_adj:.2f}, TP_adj={quality_tp_adj:.2f})")

    if signal == "BUY":
        # Базовые уровни на основе ATR
        atr_sl = current_price - (atr * sl_mult * quality_sl_adj)
        atr_tp = current_price + (atr * tp_mult * quality_tp_adj)

        # Валидация SL по уровню поддержки (только если поддержка НИЖЕ текущей цены)
        if support > 0 and support < current_price and support > atr_sl:
            # Поддержка выше базового SL → устанавливаем SL с буфером ниже поддержки
            sl = support - (atr * 0.3)
            info(f"   SL скорректирован по поддержке: {atr_sl:.4f} → {sl:.4f} (support={support:.4f})")
        else:
            sl = atr_sl

        # Sanity check: SL must be below current price for BUY
        if sl >= current_price:
            sl = current_price - (atr * sl_mult)
            info(f"   SL sanity fallback (BUY): SL was >= price, reset to {sl:.4f}")

        # Валидация TP по уровню сопротивления (только если сопротивление ВЫШЕ текущей цены)
        if resistance > 0 and resistance > current_price and resistance < atr_tp:
            # Сопротивление ниже базового TP → устанавливаем TP с буфером до сопротивления
            tp = resistance - (atr * 0.1)
            info(f"   TP скорректирован по сопротивлению: {atr_tp:.4f} → {tp:.4f} (resistance={resistance:.4f})")
        else:
            tp = atr_tp

        # Sanity check: TP must be above current price for BUY
        if tp <= current_price:
            tp = current_price + (atr * tp_mult)
            info(f"   TP sanity fallback (BUY): TP was <= price, reset to {tp:.4f}")

    elif signal == "SELL":
        # Базовые уровни на основе ATR (зеркальная логика)
        atr_sl = current_price + (atr * sl_mult * quality_sl_adj)
        atr_tp = current_price - (atr * tp_mult * quality_tp_adj)

        # Валидация SL по уровню сопротивления (только если сопротивление ВЫШЕ текущей цены)
        if resistance > 0 and resistance > current_price and resistance < atr_sl:
            # Сопротивление ниже базового SL → устанавливаем SL с буфером выше сопротивления
            sl = resistance + (atr * 0.3)
            info(f"   SL скорректирован по сопротивлению: {atr_sl:.4f} → {sl:.4f} (resistance={resistance:.4f})")
        else:
            sl = atr_sl

        # Sanity check: SL must be above current price for SELL
        if sl <= current_price:
            sl = current_price + (atr * sl_mult)
            info(f"   SL sanity fallback (SELL): SL was <= price, reset to {sl:.4f}")

        # Валидация TP по уровню поддержки (только если поддержка НИЖЕ текущей цены)
        if support > 0 and support < current_price and support > atr_tp:
            # Поддержка выше базового TP → устанавливаем TP с буфером до поддержки
            tp = support + (atr * 0.1)
            info(f"   TP скорректирован по поддержке: {atr_tp:.4f} → {tp:.4f} (support={support:.4f})")
        else:
            tp = atr_tp

        # Sanity check: TP must be below current price for SELL
        if tp >= current_price:
            tp = current_price - (atr * tp_mult)
            info(f"   TP sanity fallback (SELL): TP was >= price, reset to {tp:.4f}")

    else:
        raise ValueError(f"Неверный сигнал: {signal}. Ожидается 'BUY' или 'SELL'")

    # Расчет метрик риска и прибыли
    risk = abs(current_price - sl)
    reward = abs(tp - current_price)
    risk_reward = round(reward / risk, 2) if risk > 0 else 0.0
    risk_pct = round((risk / current_price) * 100, 2)
    reward_pct = round((reward / current_price) * 100, 2)

    # Округление цен до 4 знаков
    sl = round(sl, 4)
    tp = round(tp, 4)

    info(f"✅ Результат: SL={sl:.4f} (-{risk_pct}%), TP={tp:.4f} (+{reward_pct}%), R/R={risk_reward}")

    return {
        "stop_loss": sl,
        "take_profit": tp,
        "risk_reward": risk_reward,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct
    }


def calculate_position_size(
    base_pct: float,
    quality: float,
    regime: Dict,
    recent_performance: Optional[Dict] = None
) -> float:
    """
    Расчет динамического размера позиции с учетом качества, режима и производительности.

    Args:
        base_pct: Базовый процент от баланса (из конфига POSITION_SIZE_PERCENT)
        quality: Качество сигнала [0.0-1.0]
        regime: Словарь с параметрами режима (position_size_factor)
        recent_performance: Опциональный словарь с последними результатами:
            - win_rate: Процент выигрышных сделок [0.0-1.0]
            - total_trades: Количество сделок

    Returns:
        Скорректированный процент от баланса, ограниченный min/max значениями из конфига
    """
    sizing_config = BOT_CONFIG.get("DYNAMIC_SIZING", {})

    # Проверяем, включено ли динамическое изменение размера
    if not sizing_config.get("enabled", True):
        info(f"📊 Динамическое изменение размера отключено, используется базовый размер: {base_pct}%")
        return base_pct

    # Получаем границы из конфига
    min_size_pct = sizing_config.get("min_size_pct", 3.0)
    max_size_pct = sizing_config.get("max_size_pct", 20.0)

    # Фактор режима рынка (0.5-1.2)
    regime_factor = regime.get("position_size_factor", 1.0)

    # Фактор качества сигнала (0.5-1.2)
    quality_base = sizing_config.get("quality_base", 0.5)
    quality_weight = sizing_config.get("quality_weight", 0.7)
    quality_factor = quality_base + (quality * quality_weight)

    # Фактор производительности (адаптация к streak)
    perf_factor = 1.0
    if recent_performance:
        win_rate = recent_performance.get("win_rate", 0.5)
        total_trades = recent_performance.get("total_trades", 0)

        # Учитываем производительность только при достаточной статистике
        min_trades = sizing_config.get("min_trades_for_streak", 5)
        if total_trades >= min_trades:
            cold_threshold = sizing_config.get("cold_streak_threshold", 0.3)
            hot_threshold = sizing_config.get("hot_streak_threshold", 0.6)
            if win_rate < cold_threshold:
                perf_factor = sizing_config.get("cold_streak_factor", 0.5)  # Cold streak - снижаем риск
                warning(f"⚠️ Cold streak обнаружен (win_rate={win_rate:.1%}), снижаем размер позиции")
            elif win_rate > hot_threshold:
                perf_factor = sizing_config.get("hot_streak_factor", 1.1)  # Hot streak - увеличиваем размер
                info(f"🔥 Hot streak обнаружен (win_rate={win_rate:.1%}), увеличиваем размер позиции")

    # Итоговый расчет с применением всех факторов
    adjusted_pct = base_pct * regime_factor * quality_factor * perf_factor

    # Применяем ограничения
    adjusted_pct = max(min_size_pct, min(max_size_pct, adjusted_pct))
    adjusted_pct = round(adjusted_pct, 2)

    info(f"📊 Размер позиции: {base_pct}% × {regime_factor:.2f} (режим) × {quality_factor:.2f} (качество) × {perf_factor:.2f} (streak) = {adjusted_pct}%")
    info(f"   Границы: [{min_size_pct}% - {max_size_pct}%]")

    return adjusted_pct


def validate_risk_parameters(
    sl_tp_result: Dict[str, float],
    min_rr_ratio: float = None
) -> bool:
    """
    Валидация параметров риска перед открытием позиции.

    Args:
        sl_tp_result: Результат от calculate_dynamic_sl_tp
        min_rr_ratio: Минимальное соотношение R/R (из конфига если None)

    Returns:
        True если параметры прошли валидацию, False иначе
    """
    if min_rr_ratio is None:
        min_rr_ratio = BOT_CONFIG.get("MIN_RISK_REWARD_RATIO", 1.2)

    rr = sl_tp_result.get("risk_reward", 0.0)
    risk_pct = sl_tp_result.get("risk_pct", 0.0)
    reward_pct = sl_tp_result.get("reward_pct", 0.0)

    # Fee-adjusted R/R calculation
    from src.config import TRADING_FEE_TAKER
    round_trip_fee_pct = TRADING_FEE_TAKER * 2.0
    effective_rr = (reward_pct - round_trip_fee_pct) / (risk_pct + round_trip_fee_pct) if (risk_pct + round_trip_fee_pct) > 0 else 0.0
    effective_rr = round(effective_rr, 2)

    # Проверка минимального R/R (using fee-adjusted value)
    if effective_rr < min_rr_ratio:
        warning(f"❌ Валидация провалена: Gross R/R={rr}, Net R/R={effective_rr} < {min_rr_ratio} (fee={round_trip_fee_pct:.3f}%, reward={reward_pct:.3f}%, risk={risk_pct:.3f}%)")
        return False

    # Проверка разумности риска (не более 10% от цены)
    if risk_pct > 10.0:
        warning(f"❌ Валидация провалена: Risk={risk_pct}% слишком высок (>10%)")
        return False

    # Проверка на нулевые значения
    if sl_tp_result.get("stop_loss", 0) == 0 or sl_tp_result.get("take_profit", 0) == 0:
        warning("❌ Валидация провалена: SL или TP равен нулю")
        return False

    info(f"✅ Валидация пройдена: Gross R/R={rr}, Net R/R={effective_rr} >= {min_rr_ratio}, Risk={risk_pct}%, Fee={round_trip_fee_pct:.3f}%")
    return True
