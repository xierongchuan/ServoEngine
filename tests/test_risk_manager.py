"""
Unit tests for Risk Manager module.
"""

import pytest
from src.core.risk_manager import (
    calculate_dynamic_sl_tp,
    calculate_position_size,
    validate_risk_parameters
)


class TestCalculateDynamicSLTP:
    """Тесты для calculate_dynamic_sl_tp"""

    def test_buy_signal_basic(self):
        """Тест базового BUY сигнала без коррекции S/R"""
        result = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=0.0,  # Нет поддержки
            resistance=0.0,  # Нет сопротивления
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=0.5  # Средний качество
        )

        # quality_sl_adj = 1.0 - (0.5 * 0.2) = 0.9
        # quality_tp_adj = 1.0 + (0.5 * 0.3) = 1.15
        # atr_sl = 50000 - (500 * 2.0 * 0.9) = 50000 - 900 = 49100
        # atr_tp = 50000 + (500 * 3.0 * 1.15) = 50000 + 1725 = 51725

        assert result["stop_loss"] == 49100.0
        assert result["take_profit"] == 51725.0
        assert result["risk_pct"] == round((900 / 50000) * 100, 2)
        assert result["reward_pct"] == round((1725 / 50000) * 100, 2)
        assert result["risk_reward"] > 1.0

    def test_buy_signal_with_support_adjustment(self):
        """Тест BUY с коррекцией SL по поддержке"""
        result = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=49500.0,  # Поддержка выше базового SL
            resistance=0.0,
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=0.5
        )

        # Базовый atr_sl = 49100, но support=49500 > atr_sl
        # SL должен быть: support - (atr * 0.3) = 49500 - 150 = 49350
        assert result["stop_loss"] == 49350.0

    def test_buy_signal_with_resistance_adjustment(self):
        """Тест BUY с коррекцией TP по сопротивлению"""
        result = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=0.0,
            resistance=51000.0,  # Сопротивление ниже базового TP
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=0.5
        )

        # Базовый atr_tp = 51725, но resistance=51000 < atr_tp
        # TP должен быть: resistance - (atr * 0.1) = 51000 - 50 = 50950
        assert result["take_profit"] == 50950.0

    def test_sell_signal_basic(self):
        """Тест базового SELL сигнала без коррекции S/R"""
        result = calculate_dynamic_sl_tp(
            signal="SELL",
            current_price=50000.0,
            atr=500.0,
            support=0.0,
            resistance=0.0,
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=0.5
        )

        # quality_sl_adj = 0.9, quality_tp_adj = 1.15
        # atr_sl = 50000 + (500 * 2.0 * 0.9) = 50900
        # atr_tp = 50000 - (500 * 3.0 * 1.15) = 48275

        assert result["stop_loss"] == 50900.0
        assert result["take_profit"] == 48275.0
        assert result["risk_reward"] > 1.0

    def test_sell_signal_with_resistance_adjustment(self):
        """Тест SELL с коррекцией SL по сопротивлению"""
        result = calculate_dynamic_sl_tp(
            signal="SELL",
            current_price=50000.0,
            atr=500.0,
            support=0.0,
            resistance=50500.0,  # Сопротивление ниже базового SL
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=0.5
        )

        # Базовый atr_sl = 50900, но resistance=50500 < atr_sl
        # SL должен быть: resistance + (atr * 0.3) = 50500 + 150 = 50650
        assert result["stop_loss"] == 50650.0

    def test_sell_signal_with_support_adjustment(self):
        """Тест SELL с коррекцией TP по поддержке"""
        result = calculate_dynamic_sl_tp(
            signal="SELL",
            current_price=50000.0,
            atr=500.0,
            support=49000.0,  # Поддержка выше базового TP
            resistance=0.0,
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=0.5
        )

        # Базовый atr_tp = 48275, но support=49000 > atr_tp
        # TP должен быть: support + (atr * 0.1) = 49000 + 50 = 49050
        assert result["take_profit"] == 49050.0

    def test_high_quality_signal(self):
        """Тест высокого качества сигнала (узкий SL, широкий TP)"""
        result = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=0.0,
            resistance=0.0,
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=1.0  # Максимальное качество
        )

        # quality_sl_adj = 1.0 - (1.0 * 0.2) = 0.8
        # quality_tp_adj = 1.0 + (1.0 * 0.3) = 1.3
        # SL должен быть уже, TP шире
        # atr_sl = 50000 - (500 * 2.0 * 0.8) = 49200
        # atr_tp = 50000 + (500 * 3.0 * 1.3) = 51950

        assert result["stop_loss"] == 49200.0
        assert result["take_profit"] == 51950.0

    def test_low_quality_signal(self):
        """Тест низкого качества сигнала (широкий SL, узкий TP)"""
        result = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=0.0,
            resistance=0.0,
            regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
            quality=0.0  # Минимальное качество
        )

        # quality_sl_adj = 1.0 - (0.0 * 0.2) = 1.0
        # quality_tp_adj = 1.0 + (0.0 * 0.3) = 1.0
        # atr_sl = 50000 - (500 * 2.0 * 1.0) = 49000
        # atr_tp = 50000 + (500 * 3.0 * 1.0) = 51500

        assert result["stop_loss"] == 49000.0
        assert result["take_profit"] == 51500.0

    def test_invalid_signal(self):
        """Тест обработки неверного направления сигнала"""
        with pytest.raises(ValueError):
            calculate_dynamic_sl_tp(
                signal="INVALID",
                current_price=50000.0,
                atr=500.0,
                support=0.0,
                resistance=0.0,
                regime={"sl_multiplier": 2.0, "tp_multiplier": 3.0},
                quality=0.5
            )

    def test_regime_multipliers(self):
        """Тест влияния мультипликаторов режима"""
        # Агрессивный режим (TRENDING)
        result_aggressive = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=0.0,
            resistance=0.0,
            regime={"sl_multiplier": 1.5, "tp_multiplier": 3.5},
            quality=0.5
        )

        # Консервативный режим (RANGING)
        result_conservative = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=0.0,
            resistance=0.0,
            regime={"sl_multiplier": 1.0, "tp_multiplier": 1.5},
            quality=0.5
        )

        # For BUY: higher sl_multiplier = wider stop = LOWER stop_loss price
        assert result_aggressive["stop_loss"] < result_conservative["stop_loss"]
        assert result_aggressive["take_profit"] > result_conservative["take_profit"]


class TestCalculatePositionSize:
    """Тесты для calculate_position_size"""

    def test_basic_calculation(self):
        """Тест базового расчета без производительности"""
        result = calculate_position_size(
            base_pct=10.0,
            quality=0.5,
            regime={"position_size_factor": 1.0},
            recent_performance=None
        )

        # quality_factor = 0.5 + (0.5 * 0.7) = 0.85
        # adjusted = 10.0 * 1.0 * 0.85 * 1.0 = 8.5
        assert result == 8.5

    def test_high_quality_signal(self):
        """Тест увеличения размера при высоком качестве"""
        result = calculate_position_size(
            base_pct=10.0,
            quality=1.0,
            regime={"position_size_factor": 1.0},
            recent_performance=None
        )

        # quality_factor = 0.5 + (1.0 * 0.7) = 1.2
        # adjusted = 10.0 * 1.0 * 1.2 * 1.0 = 12.0
        assert result == 12.0

    def test_low_quality_signal(self):
        """Тест снижения размера при низком качестве"""
        result = calculate_position_size(
            base_pct=10.0,
            quality=0.0,
            regime={"position_size_factor": 1.0},
            recent_performance=None
        )

        # quality_factor = 0.5 + (0.0 * 0.7) = 0.5
        # adjusted = 10.0 * 1.0 * 0.5 * 1.0 = 5.0
        assert result == 5.0

    def test_regime_factor(self):
        """Тест влияния фактора режима"""
        result_trending = calculate_position_size(
            base_pct=10.0,
            quality=0.5,
            regime={"position_size_factor": 1.2},
            recent_performance=None
        )

        result_volatile = calculate_position_size(
            base_pct=10.0,
            quality=0.5,
            regime={"position_size_factor": 0.6},
            recent_performance=None
        )

        # В тренде размер больше, в волатильном меньше
        assert result_trending > result_volatile

    def test_cold_streak(self):
        """Тест снижения размера при холодной полосе"""
        result = calculate_position_size(
            base_pct=10.0,
            quality=0.5,
            regime={"position_size_factor": 1.0},
            recent_performance={"win_rate": 0.2, "total_trades": 10}
        )

        # quality_factor = 0.85, perf_factor = 0.5 (cold streak)
        # adjusted = 10.0 * 1.0 * 0.85 * 0.5 = 4.25
        assert result == 4.25

    def test_hot_streak(self):
        """Тест увеличения размера при горячей полосе"""
        result = calculate_position_size(
            base_pct=10.0,
            quality=0.5,
            regime={"position_size_factor": 1.0},
            recent_performance={"win_rate": 0.7, "total_trades": 10}
        )

        # quality_factor = 0.85, perf_factor = 1.1 (hot streak)
        # adjusted = 10.0 * 1.0 * 0.85 * 1.1 = 9.35
        assert result == 9.35

    def test_insufficient_performance_data(self):
        """Тест игнорирования производительности при малой статистике"""
        result = calculate_position_size(
            base_pct=10.0,
            quality=0.5,
            regime={"position_size_factor": 1.0},
            recent_performance={"win_rate": 0.2, "total_trades": 3}  # < 5 сделок
        )

        # perf_factor должен быть 1.0 (игнорируется)
        # adjusted = 10.0 * 1.0 * 0.85 * 1.0 = 8.5
        assert result == 8.5

    def test_min_size_limit(self):
        """Тест нижней границы размера позиции"""
        result = calculate_position_size(
            base_pct=10.0,
            quality=0.0,  # Минимум
            regime={"position_size_factor": 0.5},  # Минимум
            recent_performance={"win_rate": 0.2, "total_trades": 10}  # Cold streak
        )

        # quality_factor = 0.5, regime = 0.5, perf = 0.5
        # adjusted = 10.0 * 0.5 * 0.5 * 0.5 = 1.25
        # Но минимум 3.0 по конфигу
        assert result == 3.0

    def test_max_size_limit(self):
        """Тест верхней границы размера позиции"""
        result = calculate_position_size(
            base_pct=15.0,
            quality=1.0,  # Максимум
            regime={"position_size_factor": 1.2},  # Максимум
            recent_performance={"win_rate": 0.8, "total_trades": 10}  # Hot streak
        )

        # quality_factor = 1.2, regime = 1.2, perf = 1.1
        # adjusted = 15.0 * 1.2 * 1.2 * 1.1 = 23.76
        # Но максимум 20.0 по конфигу
        assert result == 20.0


class TestValidateRiskParameters:
    """Тесты для validate_risk_parameters"""

    def test_valid_parameters(self):
        """Тест валидации корректных параметров"""
        sl_tp_result = {
            "stop_loss": 49000.0,
            "take_profit": 51500.0,
            "risk_reward": 2.5,
            "risk_pct": 2.0,
            "reward_pct": 3.0
        }

        assert validate_risk_parameters(sl_tp_result, min_rr_ratio=1.2) is True

    def test_invalid_risk_reward_ratio(self):
        """Тест отклонения при низком R/R"""
        sl_tp_result = {
            "stop_loss": 49500.0,
            "take_profit": 50500.0,
            "risk_reward": 1.0,  # Ниже минимума
            "risk_pct": 1.0,
            "reward_pct": 1.0
        }

        assert validate_risk_parameters(sl_tp_result, min_rr_ratio=1.2) is False

    def test_excessive_risk(self):
        """Тест отклонения при слишком высоком риске"""
        sl_tp_result = {
            "stop_loss": 40000.0,
            "take_profit": 60000.0,
            "risk_reward": 2.0,
            "risk_pct": 20.0,  # Слишком высокий риск
            "reward_pct": 20.0
        }

        assert validate_risk_parameters(sl_tp_result, min_rr_ratio=1.2) is False

    def test_zero_stop_loss(self):
        """Тест отклонения при нулевом SL"""
        sl_tp_result = {
            "stop_loss": 0.0,
            "take_profit": 51500.0,
            "risk_reward": 2.5,
            "risk_pct": 2.0,
            "reward_pct": 3.0
        }

        assert validate_risk_parameters(sl_tp_result) is False

    def test_zero_take_profit(self):
        """Тест отклонения при нулевом TP"""
        sl_tp_result = {
            "stop_loss": 49000.0,
            "take_profit": 0.0,
            "risk_reward": 2.5,
            "risk_pct": 2.0,
            "reward_pct": 3.0
        }

        assert validate_risk_parameters(sl_tp_result) is False

    def test_default_min_rr_from_config(self):
        """Тест использования минимального R/R из конфига по умолчанию"""
        sl_tp_result = {
            "stop_loss": 49500.0,
            "take_profit": 51500.0,
            "risk_reward": 1.5,
            "risk_pct": 1.0,
            "reward_pct": 4.0
        }

        # Конфиг MIN_RISK_REWARD_RATIO = 1.2, поэтому 1.5 должно пройти
        assert validate_risk_parameters(sl_tp_result) is True


class TestIntegrationScenarios:
    """Интеграционные тесты реальных сценариев"""

    def test_trending_market_scenario(self):
        """Сценарий трендового рынка с высоким качеством сигнала"""
        # Режим TRENDING: агрессивные параметры
        regime = {
            "sl_multiplier": 1.5,
            "tp_multiplier": 3.5,
            "position_size_factor": 1.2
        }

        # BUY в восходящем тренде с высоким качеством
        sl_tp = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=500.0,
            support=49200.0,
            resistance=52000.0,
            regime=regime,
            quality=0.8
        )

        # Проверяем валидацию
        assert validate_risk_parameters(sl_tp, min_rr_ratio=1.2)

        # Расчет размера позиции с hot streak
        position_size = calculate_position_size(
            base_pct=10.0,
            quality=0.8,
            regime=regime,
            recent_performance={"win_rate": 0.65, "total_trades": 15}
        )

        # В трендовом рынке с высоким качеством размер увеличивается
        assert position_size > 10.0
        assert sl_tp["risk_reward"] > 1.2

    def test_ranging_market_scenario(self):
        """Сценарий рыночного диапазона с умеренным качеством"""
        # Режим RANGING: консервативные параметры
        regime = {
            "sl_multiplier": 1.0,
            "tp_multiplier": 1.5,
            "position_size_factor": 0.8
        }

        # SELL в боковике
        sl_tp = calculate_dynamic_sl_tp(
            signal="SELL",
            current_price=50000.0,
            atr=300.0,
            support=49500.0,
            resistance=50500.0,
            regime=regime,
            quality=0.6
        )

        # Проверяем валидацию
        assert validate_risk_parameters(sl_tp, min_rr_ratio=1.2)

        # Размер позиции с нейтральной производительностью
        position_size = calculate_position_size(
            base_pct=10.0,
            quality=0.6,
            regime=regime,
            recent_performance={"win_rate": 0.5, "total_trades": 10}
        )

        # В боковике размер снижается
        assert position_size < 10.0

    def test_volatile_market_scenario(self):
        """Сценарий высокой волатильности с низким качеством"""
        # Режим VOLATILE: широкие стопы, малый размер
        regime = {
            "sl_multiplier": 2.5,
            "tp_multiplier": 2.5,
            "position_size_factor": 0.6
        }

        # BUY в волатильности с низким качеством
        sl_tp = calculate_dynamic_sl_tp(
            signal="BUY",
            current_price=50000.0,
            atr=1000.0,  # Высокая волатильность
            support=48000.0,
            resistance=53000.0,
            regime=regime,
            quality=0.4
        )

        # Размер позиции с cold streak
        position_size = calculate_position_size(
            base_pct=10.0,
            quality=0.4,
            regime=regime,
            recent_performance={"win_rate": 0.25, "total_trades": 8}
        )

        # В волатильности с плохой производительностью размер минимален
        assert position_size <= 3.0  # Должен быть на минимуме
        assert sl_tp["risk_pct"] > 2.0  # Широкие стопы
