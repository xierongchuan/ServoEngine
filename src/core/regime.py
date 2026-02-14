"""
Модуль детектирования рыночного режима.

Классифицирует состояние рынка на основе данных из analyzer:
- TRENDING: сильный тренд, высокая направленность
- RANGING: боковик, низкая волатильность
- VOLATILE: высокая волатильность, нестабильность
- TRANSITIONAL: переходное состояние
"""

from collections import deque
from typing import Dict, Optional, Tuple
from src.config import BOT_CONFIG
from src.utils.logger import info


class MarketRegimeDetector:
    """Детектор рыночного режима на основе технических метрик."""

    def __init__(self, config: Optional[Dict] = None):
        """
        Инициализация детектора.

        Args:
            config: Настройки детектора (если None, берутся из BOT_CONFIG)
        """
        self.config = config or BOT_CONFIG.get("REGIME_SETTINGS", {})

        # Параметры анализа
        self.lookback_candles = self.config.get("lookback_candles", 10)
        self.ema_spread_thresholds = self.config.get("ema_spread_thresholds", {
            "no_trend": 0.15,
            "weak": 0.5,
            "strong": 1.5
        })
        self.volatility_window = self.config.get("volatility_percentile_window", 100)
        self.regime_params = self.config.get("regime_params", {})

        # История BB width для расчета перцентилей
        self.bb_width_history: deque = deque(maxlen=self.volatility_window)

        info(f"MarketRegimeDetector инициализирован: lookback={self.lookback_candles}, "
             f"volatility_window={self.volatility_window}")

    def detect(self, analysis_data: dict) -> dict:
        """
        Определяет текущий рыночный режим.

        Args:
            analysis_data: Данные из analyzer (EMA, ATR, BB, RSI, volume_ratio, etc.)

        Returns:
            dict: Результат классификации режима с рекомендациями
        """
        # Метрика 1: Сила тренда (EMA spread)
        trend_strength, trend_category = self._calculate_trend_strength(
            analysis_data.get("ema9"),
            analysis_data.get("ema21")
        )

        # Метрика 2: Состояние волатильности (BB width + ATR ratio)
        volatility_state = self._calculate_volatility_state(
            analysis_data.get("bb_upper"),
            analysis_data.get("bb_lower"),
            analysis_data.get("close_prices", []),
            analysis_data.get("atr_ratio", 1.0)
        )

        # Метрика 3: Направленная согласованность (последние N свечей)
        directional_consistency, consistency_category = self._calculate_directional_consistency(
            analysis_data.get("close_prices", [])
        )

        # Определяем режим по матрице
        regime, confidence = self._classify_regime(
            trend_category,
            volatility_state,
            consistency_category,
            analysis_data.get("atr_ratio", 1.0)
        )

        # Получаем параметры для режима
        regime_config = self.regime_params.get(regime, {
            "min_score": 5,
            "sl_multiplier": 1.5,
            "tp_multiplier": 2.5,
            "position_size_factor": 1.0
        })

        result = {
            "regime": regime,
            "trend_strength": trend_strength,
            "volatility_state": volatility_state,
            "directional_consistency": directional_consistency,
            "confidence": confidence,
            "recommended_min_score": regime_config.get("min_score", 5),
            "sl_multiplier": regime_config.get("sl_multiplier", 1.5),
            "tp_multiplier": regime_config.get("tp_multiplier", 2.5),
            "position_size_factor": regime_config.get("position_size_factor", 1.0),
        }

        info(f"Режим рынка: {regime} (уверенность: {confidence:.2f}, "
             f"тренд: {trend_strength:.2f}, волатильность: {volatility_state}, "
             f"согласованность: {directional_consistency:.2f})")

        return result

    def _calculate_trend_strength(
        self,
        ema9: Optional[float],
        ema21: Optional[float]
    ) -> Tuple[float, str]:
        """
        Рассчитывает силу тренда через спред EMA.

        Args:
            ema9: Значение EMA-9
            ema21: Значение EMA-21

        Returns:
            Tuple[float, str]: Нормализованная сила (0-1), категория тренда
        """
        if ema9 is None or ema21 is None or ema21 == 0:
            return 0.0, "NO_TREND"

        # Процентный спред между EMA
        ema_spread_pct = abs(ema9 - ema21) / ema21 * 100

        # Пороги из конфига
        no_trend = self.ema_spread_thresholds["no_trend"]
        weak = self.ema_spread_thresholds["weak"]
        strong = self.ema_spread_thresholds["strong"]

        # Категоризация
        if ema_spread_pct < no_trend:
            category = "NO_TREND"
            normalized = 0.0
        elif ema_spread_pct < weak:
            category = "WEAK_TREND"
            normalized = 0.33
        elif ema_spread_pct < strong:
            category = "MODERATE_TREND"
            normalized = 0.66
        else:
            category = "STRONG_TREND"
            normalized = 1.0

        return normalized, category

    def _calculate_volatility_state(
        self,
        bb_upper: Optional[float],
        bb_lower: Optional[float],
        close_prices: list,
        atr_ratio: float
    ) -> str:
        """
        Определяет состояние волатильности через BB width и ATR ratio.

        Args:
            bb_upper: Верхняя граница Bollinger Bands
            bb_lower: Нижняя граница Bollinger Bands
            close_prices: История цен закрытия
            atr_ratio: Текущий ATR ratio

        Returns:
            str: COMPRESSED, NORMAL, или EXPANDED
        """
        if bb_upper is None or bb_lower is None or not close_prices:
            return "NORMAL"

        # Ширина BB
        current_bb_width = bb_upper - bb_lower

        # Добавляем в историю
        self.bb_width_history.append(current_bb_width)

        # Если недостаточно данных, используем только ATR ratio
        if len(self.bb_width_history) < 20:
            if atr_ratio < 0.7:
                return "COMPRESSED"
            elif atr_ratio > 2.0:
                return "EXPANDED"
            else:
                return "NORMAL"

        # Вычисляем перцентили BB width
        sorted_widths = sorted(self.bb_width_history)
        p20_index = int(len(sorted_widths) * 0.2)
        p80_index = int(len(sorted_widths) * 0.8)

        p20_value = sorted_widths[p20_index]
        p80_value = sorted_widths[p80_index]

        # Сжатие: BB width в нижних 20% И низкий ATR
        if current_bb_width <= p20_value and atr_ratio < 0.7:
            return "COMPRESSED"

        # Расширение: BB width в верхних 20% ИЛИ высокий ATR
        if current_bb_width >= p80_value or atr_ratio > 2.0:
            return "EXPANDED"

        return "NORMAL"

    def _calculate_directional_consistency(
        self,
        close_prices: list
    ) -> Tuple[float, str]:
        """
        Рассчитывает направленную согласованность последних свечей.

        Args:
            close_prices: История цен закрытия

        Returns:
            Tuple[float, str]: Согласованность (0-1), категория
        """
        if not close_prices or len(close_prices) < 2:
            return 0.0, "CHOPPY"

        # Берем последние N свечей
        lookback = min(self.lookback_candles, len(close_prices))
        recent_prices = close_prices[-lookback:]

        # Считаем движения вверх
        up_count = 0
        total_moves = len(recent_prices) - 1

        for i in range(1, len(recent_prices)):
            if recent_prices[i] > recent_prices[i - 1]:
                up_count += 1

        # Согласованность: 0.0 = идеально пополам (0.5), 1.0 = все в одну сторону
        if total_moves == 0:
            return 0.0, "CHOPPY"

        consistency = abs(up_count / total_moves - 0.5) * 2

        # Категоризация
        if consistency < 0.2:
            category = "CHOPPY"
        elif consistency < 0.6:
            category = "MIXED"
        else:
            category = "DIRECTIONAL"

        return consistency, category

    def _classify_regime(
        self,
        trend_category: str,
        volatility_state: str,
        consistency_category: str,
        atr_ratio: float
    ) -> Tuple[str, float]:
        """
        Классифицирует режим по матрице метрик.

        Args:
            trend_category: NO_TREND, WEAK_TREND, MODERATE_TREND, STRONG_TREND
            volatility_state: COMPRESSED, NORMAL, EXPANDED
            consistency_category: CHOPPY, MIXED, DIRECTIONAL
            atr_ratio: Текущий ATR ratio

        Returns:
            Tuple[str, float]: Режим, уверенность в классификации (0-1)
        """
        # Правило 1: Экстремальная волатильность -> VOLATILE
        if atr_ratio > 2.5:
            return "VOLATILE", 0.9

        # Правило 2: Сильный/умеренный тренд + норм./высокая волатильность + направленность -> TRENDING
        if trend_category in ["STRONG_TREND", "MODERATE_TREND"]:
            if volatility_state in ["NORMAL", "EXPANDED"]:
                if consistency_category == "DIRECTIONAL":
                    return "TRENDING", 0.85

        # Правило 3: Слабый/нет тренда + сжатие/норм. волатильность + чоп/смешанный -> RANGING
        if trend_category in ["WEAK_TREND", "NO_TREND"]:
            if volatility_state in ["COMPRESSED", "NORMAL"]:
                if consistency_category in ["CHOPPY", "MIXED"]:
                    return "RANGING", 0.8

        # Правило 4: Высокая волатильность без сильного тренда -> VOLATILE
        if volatility_state == "EXPANDED" and trend_category in ["NO_TREND", "WEAK_TREND"]:
            return "VOLATILE", 0.75

        # Правило 5: Сильный тренд но чопи консистенси -> TRANSITIONAL (возможный разворот)
        if trend_category in ["STRONG_TREND", "MODERATE_TREND"] and consistency_category == "CHOPPY":
            return "TRANSITIONAL", 0.7

        # Правило 6: Сжатие с направленностью -> TRANSITIONAL (возможный пробой)
        if volatility_state == "COMPRESSED" and consistency_category == "DIRECTIONAL":
            return "TRANSITIONAL", 0.65

        # Все остальные случаи -> TRANSITIONAL с низкой уверенностью
        return "TRANSITIONAL", 0.5


# Глобальный экземпляр детектора (ленивая инициализация)
_detector: Optional[MarketRegimeDetector] = None


def get_regime_detector() -> MarketRegimeDetector:
    """
    Возвращает глобальный экземпляр детектора режима.

    Returns:
        MarketRegimeDetector: Инициализированный детектор
    """
    global _detector
    if _detector is None:
        _detector = MarketRegimeDetector()
    return _detector


def detect_regime(analysis_data: dict) -> dict:
    """
    Convenience-функция для детекции режима.

    Args:
        analysis_data: Данные из analyzer

    Returns:
        dict: Результат классификации режима
    """
    detector = get_regime_detector()
    return detector.detect(analysis_data)
