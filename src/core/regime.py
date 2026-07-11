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
        # Метрика 1: Сила и направление тренда (EMA spread)
        trend_strength, trend_category, trend_direction = self._calculate_trend_strength(
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
            analysis_data.get("atr_ratio", 1.0),
            trend_strength,
            directional_consistency
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
            "trend_direction": trend_direction,
            "volatility_state": volatility_state,
            "directional_consistency": directional_consistency,
            "confidence": confidence,
            "recommended_min_score": regime_config.get("min_score", 5),
            "sl_multiplier": regime_config.get("sl_multiplier", 1.5),
            "tp_multiplier": regime_config.get("tp_multiplier", 2.5),
            "position_size_factor": regime_config.get("position_size_factor", 1.0),
        }

        info(f"Режим рынка: {regime} (уверенность: {confidence:.2f}, "
              f"тренд: {trend_strength:.2f} {trend_direction}, волатильность: {volatility_state}, "
              f"согласованность: {directional_consistency:.2f})")

        return result

    def _calculate_trend_strength(
        self,
        ema9: Optional[float],
        ema21: Optional[float]
    ) -> Tuple[float, str, str]:
        """
        Рассчитывает силу и направление тренда через спред EMA.

        Args:
            ema9: Значение EMA-9
            ema21: Значение EMA-21

        Returns:
            Tuple[float, str, str]: Нормализованная сила (0-1), категория тренда, направление
        """
        if ema9 is None or ema21 is None or ema21 == 0:
            return 0.0, "NO_TREND", "NEUTRAL"

        # Определяем направление тренда
        if ema9 > ema21:
            direction = "BULLISH"  # EMA9 выше EMA21 = бычий тренд
        elif ema9 < ema21:
            direction = "BEARISH"  # EMA9 ниже EMA21 = медвежий тренд
        else:
            direction = "NEUTRAL"  # EMA равны

        # Процентный спред между EMA
        ema_spread_pct = abs(ema9 - ema21) / ema21 * 100

        # Пороги из конфига
        no_trend = self.ema_spread_thresholds["no_trend"]
        weak = self.ema_spread_thresholds["weak"]
        strong = self.ema_spread_thresholds["strong"]

        # Категоризация и нормализация
        if ema_spread_pct < no_trend:
            category = "NO_TREND"
            normalized = 0.0
        elif ema_spread_pct < weak:
            category = "WEAK_TREND"
            # Линейная нормализация в диапазоне [no_trend, weak] -> [0.0, 0.5]
            normalized = (ema_spread_pct - no_trend) / (weak - no_trend) * 0.5
        elif ema_spread_pct < strong:
            category = "MODERATE_TREND"
            # Линейная нормализация в диапазоне [weak, strong] -> [0.5, 0.8]
            normalized = 0.5 + (ema_spread_pct - weak) / (strong - weak) * 0.3
        else:
            category = "STRONG_TREND"
            # Для очень сильных трендов фиксируем 1.0
            normalized = 1.0

        return normalized, category, direction

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
        atr_ratio: float,
        trend_strength: float = 0.0,
        directional_consistency: float = 0.0
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
            # Уверенность плавно растёт от 0.8 при ATR=2.5 до 1.0 при ATR=5.0.
            normalized_excess = min(max((atr_ratio - 2.5) / 2.5, 0.0), 1.0)
            confidence = 0.8 + normalized_excess * 0.2
            return "VOLATILE", min(confidence, 1.0)

        # Правило 2: Сильный/умеренный тренд + норм./высокая волатильность + направленность -> TRENDING
        if trend_category in ["STRONG_TREND", "MODERATE_TREND"]:
            if volatility_state in ["NORMAL", "EXPANDED"]:
                if consistency_category == "DIRECTIONAL":
                    # Высокая уверенность при сильном тренде и направленности
                    base_confidence = 0.8
                    confidence = base_confidence + (trend_strength * directional_consistency * 0.2)
                    return "TRENDING", min(confidence, 1.0)

        # Правило 2b: Умеренный тренд + норм. волатильность + смешанный -> TRENDING (lower confidence)
        if trend_category == "MODERATE_TREND" and volatility_state == "NORMAL":
            if consistency_category == "MIXED":
                # Средняя уверенность
                confidence = 0.6 + (trend_strength * 0.2) + (directional_consistency * 0.1)
                return "TRENDING", confidence

        # Правило 2c: Слабый тренд + направленность + норм. волатильность -> TRENDING (emerging trend)
        if trend_category == "WEAK_TREND" and consistency_category == "DIRECTIONAL":
            if volatility_state == "NORMAL":
                # Низкая уверенность для emerging тренда
                confidence = 0.5 + (trend_strength * 0.2) + (directional_consistency * 0.2)
                return "TRENDING", confidence

        # Правило 3: Слабый/нет тренда + сжатие/норм. волатильность + чоп/смешанный -> RANGING
        if trend_category in ["WEAK_TREND", "NO_TREND"]:
            if volatility_state in ["COMPRESSED", "NORMAL"]:
                if consistency_category in ["CHOPPY", "MIXED"]:
                    # Динамическая уверенность: выше при более слабом тренде и более хаотичных движениях
                    base_confidence = 0.7
                    trend_factor = 1 - trend_strength  # 1 когда тренд=0, 0 когда тренд=1
                    consistency_factor = 1 - directional_consistency  # 1 когда consistency=0, 0 когда consistency=1
                    confidence = base_confidence + (trend_factor * consistency_factor * 0.3)
                    return "RANGING", min(confidence, 1.0)

        # Правило 3b: Нет тренда + норм. волатильность + направленность -> RANGING (short-lived move in range)
        if trend_category == "NO_TREND" and volatility_state == "NORMAL":
            if consistency_category == "DIRECTIONAL":
                # Низкая уверенность, так как направленность противоречит ranging
                confidence = 0.5 + (directional_consistency * 0.1)
                return "RANGING", confidence

        # Правило 4: Высокая волатильность без сильного тренда -> VOLATILE
        if volatility_state == "EXPANDED" and trend_category in ["NO_TREND", "WEAK_TREND"]:
            # Уверенность растёт с уровнем волатильности
            base_confidence = 0.7
            volatility_factor = min(atr_ratio / 3.0, 1.0)  # Нормализуем ATR ratio
            confidence = base_confidence + (volatility_factor * 0.3)
            return "VOLATILE", min(confidence, 1.0)

        # Правило 5: Сильный тренд но чопи консистенси -> RANGING (trend exhaustion, trade reversals)
        if trend_category in ["STRONG_TREND", "MODERATE_TREND"] and consistency_category == "CHOPPY":
            # Уверенность основана на силе тренда (выше при более сильном тренде)
            base_confidence = 0.5
            confidence = base_confidence + (trend_strength * 0.3)
            return "RANGING", confidence

        # Правило 6: Сжатие с направленностью -> TRENDING (pre-breakout)
        if volatility_state == "COMPRESSED" and consistency_category == "DIRECTIONAL":
            # Средняя уверенность для pre-breakout
            confidence = 0.5 + (directional_consistency * 0.3)
            return "TRENDING", confidence

        # Правило 7: Сильный тренд + сжатие + смешанный -> TRENDING (consolidation in trend)
        if trend_category in ["STRONG_TREND", "MODERATE_TREND"] and volatility_state == "COMPRESSED":
            # Уверенность основана на силе тренда
            confidence = 0.5 + (trend_strength * 0.3)
            return "TRENDING", confidence

        # Все остальные случаи -> RANGING (safer default than TRANSITIONAL)
        # Низкая уверенность для catch-all правила
        return "RANGING", 0.4


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
