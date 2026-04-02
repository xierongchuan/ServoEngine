"""Управление рисками — динамический SL/TP, размер позиции, валидация."""

from typing import Dict, Optional

from src.config import BOT_CONFIG, TRADING_FEE_TAKER
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
    """Расчет динамических уровней SL/TP с учетом ATR, S/R и качества сигнала."""
    sl_mult = regime.get("sl_multiplier", 1.5)
    tp_mult = regime.get("tp_multiplier", 3.0)

    quality_sl_adj = 1.0 - (quality * 0.2)
    quality_tp_adj = 1.0 + (quality * 0.3)

    info(f"🎯 Расчет SL/TP | Signal: {signal}, Price: {current_price:.4f}, ATR: {atr:.4f}")
    info(f"   Режим: SL_mult={sl_mult}, TP_mult={tp_mult} | Quality: {quality:.2f}")

    if signal == "BUY":
        atr_sl = current_price - (atr * sl_mult * quality_sl_adj)
        atr_tp = current_price + (atr * tp_mult * quality_tp_adj)

        if support > 0 and support < current_price and support > atr_sl:
            sl = support - (atr * 0.3)
            info(f"   SL скорректирован по поддержке: {atr_sl:.4f} → {sl:.4f}")
        else:
            sl = atr_sl

        if sl >= current_price:
            sl = current_price - (atr * sl_mult)
            info(f"   SL sanity fallback (BUY): {sl:.4f}")

        if resistance > 0 and resistance > current_price and resistance < atr_tp:
            tp = resistance - (atr * 0.1)
            info(f"   TP скорректирован по сопротивлению: {atr_tp:.4f} → {tp:.4f}")
        else:
            tp = atr_tp

        if tp <= current_price:
            tp = current_price + (atr * tp_mult)
            info(f"   TP sanity fallback (BUY): {tp:.4f}")

    elif signal == "SELL":
        atr_sl = current_price + (atr * sl_mult * quality_sl_adj)
        atr_tp = current_price - (atr * tp_mult * quality_tp_adj)

        if resistance > 0 and resistance > current_price and resistance < atr_sl:
            sl = resistance + (atr * 0.3)
            info(f"   SL скорректирован по сопротивлению: {atr_sl:.4f} → {sl:.4f}")
        else:
            sl = atr_sl

        if sl <= current_price:
            sl = current_price + (atr * sl_mult)
            info(f"   SL sanity fallback (SELL): {sl:.4f}")

        if support > 0 and support < current_price and support > atr_tp:
            tp = support + (atr * 0.1)
            info(f"   TP скорректирован по поддержке: {atr_tp:.4f} → {tp:.4f}")
        else:
            tp = atr_tp

        if tp >= current_price:
            tp = current_price - (atr * tp_mult)
            info(f"   TP sanity fallback (SELL): {tp:.4f}")

    else:
        raise ValueError(f"Неверный сигнал: {signal}. Ожидается 'BUY' или 'SELL'")

    risk = abs(current_price - sl)
    reward = abs(tp - current_price)
    risk_reward = round(reward / risk, 2) if risk > 0 else 0.0
    risk_pct = round((risk / current_price) * 100, 2)
    reward_pct = round((reward / current_price) * 100, 2)

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
    """Расчет динамического размера позиции с учетом качества, режима и производительности."""
    sizing_config = BOT_CONFIG.get("DYNAMIC_SIZING", {})

    if not sizing_config.get("enabled", True):
        info(f"📊 Динамическое изменение размера отключено, используется базовый размер: {base_pct}%")
        return base_pct

    min_size_pct = sizing_config.get("min_size_pct", 3.0)
    max_size_pct = sizing_config.get("max_size_pct", 20.0)

    regime_factor = regime.get("position_size_factor", 1.0)

    quality_base = sizing_config.get("quality_base", 0.5)
    quality_weight = sizing_config.get("quality_weight", 0.7)
    quality_factor = quality_base + (quality * quality_weight)

    perf_factor = 1.0
    if recent_performance:
        win_rate = recent_performance.get("win_rate", 0.5)
        total_trades = recent_performance.get("total_trades", 0)

        min_trades = sizing_config.get("min_trades_for_streak", 5)
        if total_trades >= min_trades:
            cold_threshold = sizing_config.get("cold_streak_threshold", 0.3)
            hot_threshold = sizing_config.get("hot_streak_threshold", 0.6)
            if win_rate < cold_threshold:
                perf_factor = sizing_config.get("cold_streak_factor", 0.5)
                warning(f"⚠️ Cold streak (win_rate={win_rate:.1%}), снижаем размер позиции")
            elif win_rate > hot_threshold:
                perf_factor = sizing_config.get("hot_streak_factor", 1.1)
                info(f"🔥 Hot streak (win_rate={win_rate:.1%}), увеличиваем размер позиции")

    adjusted_pct = base_pct * regime_factor * quality_factor * perf_factor
    adjusted_pct = max(min_size_pct, min(max_size_pct, adjusted_pct))
    adjusted_pct = round(adjusted_pct, 2)

    info(f"📊 Размер позиции: {base_pct}% × {regime_factor:.2f} (режим) × {quality_factor:.2f} (качество) × {perf_factor:.2f} (streak) = {adjusted_pct}%")

    return adjusted_pct


def validate_risk_parameters(
    sl_tp_result: Dict[str, float],
    min_rr_ratio: Optional[float] = None,
    regime: Optional[Dict] = None
) -> bool:
    """Валидация параметров риска перед открытием позиции."""
    if regime and "min_risk_reward_ratio" in regime:
        min_rr_ratio = float(regime["min_risk_reward_ratio"])
    elif min_rr_ratio is None:
        min_rr_ratio = float(BOT_CONFIG.get("MIN_RISK_REWARD_RATIO", 1.2))

    rr = sl_tp_result.get("risk_reward", 0.0)
    risk_pct = sl_tp_result.get("risk_pct", 0.0)
    reward_pct = sl_tp_result.get("reward_pct", 0.0)

    round_trip_fee_pct = TRADING_FEE_TAKER * 2.0
    effective_rr = (reward_pct - round_trip_fee_pct) / (risk_pct + round_trip_fee_pct) if (risk_pct + round_trip_fee_pct) > 0 else 0.0
    effective_rr = round(effective_rr, 2)

    if effective_rr < min_rr_ratio:
        warning(f"❌ Валидация провалена: Gross R/R={rr}, Net R/R={effective_rr} < {min_rr_ratio}")
        return False

    if risk_pct > 10.0:
        warning(f"❌ Валидация провалена: Risk={risk_pct}% слишком высок (>10%)")
        return False

    if sl_tp_result.get("stop_loss", 0) == 0 or sl_tp_result.get("take_profit", 0) == 0:
        warning("❌ Валидация провалена: SL или TP равен нулю")
        return False

    info(f"✅ Валидация пройдена: Gross R/R={rr}, Net R/R={effective_rr} >= {min_rr_ratio}, Risk={risk_pct}%")
    return True
