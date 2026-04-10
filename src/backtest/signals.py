import math
from typing import List, Dict, Any, Tuple
from ..core.predict import get_prediction

class SignalGenerator:
    """Генерирует сигналы для детерминированных стратегий, таких как MACDX."""

    def __init__(self, strategy: str = "MACDX", config: Dict[str, Any] = None):
        self.strategy = strategy
        self.config = config or {}
        # Параметры из конфига
        self.rules = self.config.get("signal_rules", {})
        # Кэш последних индикаторов для повторного использования в engine
        self.last_indicators: Dict[str, Any] = {}

    def calculate_indicators(self, klines: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
        """Рассчитывает индикаторы на основе последних свечей."""
        if index < 30:  # Минимум данных
            return {}

        closes = [k["closePrice"] for k in klines[:index+1]]
        highs = [k["highPrice"] for k in klines[:index+1]]
        lows = [k["lowPrice"] for k in klines[:index+1]]
        volumes = [k["volume"] for k in klines[:index+1]]

        # RSI (14) — инкрементальный расчёт серии за один проход
        rsi_values = self._calculate_rsi_series(closes, 14)
        rsi = rsi_values[-1] if rsi_values else 50

        # EMA9, EMA21
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)

        # MACD (12,26,9) — с hist_prev за один вызов
        macd_line, macd_signal, macd_hist, macd_hist_prev = self._calculate_macd_with_prev(closes)

        # BB width
        bb_width = self._calculate_bb_width(closes, 20)

        # ADX (14)
        adx = self._calculate_adx(highs, lows, closes, 14)

        # ATR (14)
        atr = self._calculate_atr(highs, lows, closes, 14)
        atr_ratio = atr / closes[-1] if closes[-1] > 0 else 0

        # Volume ratio
        volume_ratio = volumes[-1] / (sum(volumes[-20:]) / 20) if len(volumes) >= 20 else 1

        return {
            "rsi": rsi,
            "rsi_values": rsi_values,
            "ema9": ema9,
            "ema21": ema21,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "macd_hist_prev": macd_hist_prev,
            "bb_width": bb_width,
            "adx": adx,
            "atr": atr,
            "atr_ratio": atr_ratio,
            "volume_ratio": volume_ratio,
            "close_prices": closes
        }

    def generate_signal(self, klines: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
        """Генерирует сигнал, используя оригинальный код стратегии MACDX."""
        analysis = self.calculate_indicators(klines, index)
        self.last_indicators = analysis
        if not analysis:
            return {"action": "HOLD", "reason": "Недостаточно данных"}

        # Добавить current_price и другие ключи
        analysis["current_price"] = klines[index]["closePrice"]

        # Кэшированный импорт и создание SignalGenerator для стратегии
        try:
            if not hasattr(self, '_signal_gen_instance'):
                strategy_lower = self.strategy.lower()
                if strategy_lower == "macdx":
                    from ..core.signals.macdx import MacdxSignalGenerator as SignalGen
                elif strategy_lower == "hybrid":
                    from ..core.signals.hybrid import HybridSignalGenerator as SignalGen
                elif strategy_lower == "aiscalp":
                    from ..core.signals.aiscalp import AiscalpSignalGenerator as SignalGen
                else:
                    return {"action": "HOLD", "reason": f"Unsupported strategy: {self.strategy}"}
                self._signal_gen_instance = SignalGen(self.config)

            result = self._signal_gen_instance.generate(analysis)
        except ImportError:
            return {"action": "HOLD", "reason": f"Signal generator for {self.strategy} not found"}
        except Exception as e:
            return {"action": "HOLD", "reason": f"Error generating signal: {e}"}

        # Нормализовать ключи
        normalized = {
            "action": result.get("signal", "HOLD"),
            "score": result.get("score", 0),
            "reason": result.get("reasons", ["нет"])[0] if result.get("reasons") else "нет"
        }

        # Если есть AI, добавить подтверждение
        if self.strategy.upper() in ["HYBRID", "HYBRID_VETO"] and normalized["action"] in ["BUY", "SELL"]:
            signal = normalized["action"]
            score = normalized["score"]
            ai_decision = self._get_ai_confirmation(signal, score, klines, index)
            if ai_decision == "hold":
                normalized = {"action": "HOLD", "reason": f"AI rejected {signal.lower()} signal"}
            elif ai_decision == signal.lower():
                normalized["reason"] = (normalized.get("reason", "") + " (AI approved)").strip()
            else:
                normalized = {"action": "HOLD", "reason": f"AI changed signal to {ai_decision}"}

        return normalized

    # Вспомогательные методы для расчетов индикаторов

    def _calculate_rsi_series(self, closes: List[float], period: int) -> List[float]:
        """
        Инкрементальный расчёт RSI серии за один проход O(n).

        Использует скользящее окно (SMA последних `period` изменений),
        что совпадает с оригинальным алгоритмом _calculate_rsi,
        но без повторного расчёта на каждом подмассиве.
        """
        n = len(closes)
        if n < period + 1:
            return []

        # Предварительно вычислить все gains и losses
        gains = [0.0] * n
        losses = [0.0] * n
        for i in range(1, n):
            change = closes[i] - closes[i - 1]
            if change > 0:
                gains[i] = change
            else:
                losses[i] = -change

        values = []

        # Первое окно: сумма gains[1..period] и losses[1..period]
        sum_gain = sum(gains[1:period + 1])
        sum_loss = sum(losses[1:period + 1])

        avg_gain = sum_gain / period
        avg_loss = sum_loss / period
        if avg_loss == 0:
            values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            values.append(100.0 - (100.0 / (1.0 + rs)))

        # Скользящее окно для последующих значений
        for i in range(period + 1, n):
            sum_gain += gains[i] - gains[i - period]
            sum_loss += losses[i] - losses[i - period]
            avg_gain = sum_gain / period
            avg_loss = sum_loss / period
            if avg_loss == 0:
                values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                values.append(100.0 - (100.0 / (1.0 + rs)))

        return values

    def _calculate_rsi(self, closes: List[float], period: int) -> float:
        """Возвращает последнее значение RSI (обёртка для совместимости)."""
        series = self._calculate_rsi_series(closes, period)
        return series[-1] if series else 50.0

    def _calculate_ema(self, closes: List[float], period: int) -> float:
        if len(closes) < period:
            return closes[-1] if closes else 0
        multiplier = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        return ema

    def _calculate_ema_series(self, closes: List[float], period: int) -> List[float]:
        """Рассчитывает полную серию EMA за один проход O(n)."""
        if len(closes) < period:
            return []
        multiplier = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        series = [ema]
        for price in closes[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
            series.append(ema)
        return series

    def _calculate_macd_with_prev(self, closes: List[float]) -> Tuple[float, float, float, float]:
        """
        Рассчитывает MACD line, signal, histogram и предыдущий histogram за один проход.

        Использует инкрементальные EMA серии вместо пересчёта на каждом подмассиве.
        Возвращает (macd_line, macd_signal, macd_hist, macd_hist_prev).
        """
        if len(closes) < 26:
            return 0, 0, 0, 0

        # EMA12 и EMA26 серии — O(n) каждая
        ema12_series = self._calculate_ema_series(closes, 12)
        ema26_series = self._calculate_ema_series(closes, 26)

        # MACD line серия: EMA12[i] - EMA26[i] (выравнивание по индексам)
        # ema12_series начинается с индекса 12, ema26_series с индекса 26
        # MACD line начинается с индекса 26 (когда обе EMA доступны)
        offset = 26 - 12  # = 14
        macd_series = []
        for i in range(len(ema26_series)):
            macd_series.append(ema12_series[i + offset] - ema26_series[i])

        if not macd_series:
            return 0, 0, 0, 0

        macd_line = macd_series[-1]

        # Signal line — EMA(9) от MACD серии
        if len(macd_series) >= 9:
            signal_multiplier = 2 / (9 + 1)
            signal_ema = sum(macd_series[:9]) / 9
            for val in macd_series[9:]:
                signal_ema = (val * signal_multiplier) + (signal_ema * (1 - signal_multiplier))
            macd_signal = signal_ema
        else:
            macd_signal = macd_line

        macd_hist = macd_line - macd_signal

        # Предыдущий histogram (для определения пересечений)
        if len(macd_series) >= 2:
            # Пересчитать signal для macd_series[:-1]
            prev_series = macd_series[:-1]
            if len(prev_series) >= 9:
                signal_ema_prev = sum(prev_series[:9]) / 9
                for val in prev_series[9:]:
                    signal_ema_prev = (val * signal_multiplier) + (signal_ema_prev * (1 - signal_multiplier))
                macd_hist_prev = prev_series[-1] - signal_ema_prev
            else:
                macd_hist_prev = 0
        else:
            macd_hist_prev = 0

        return macd_line, macd_signal, macd_hist, macd_hist_prev

    def _calculate_bb_width(self, closes: List[float], period: int) -> float:
        if len(closes) < period:
            return 0
        sma = sum(closes[-period:]) / period
        variance = sum((x - sma) ** 2 for x in closes[-period:]) / period
        std = math.sqrt(variance)
        return (std / sma) * 100 if sma > 0 else 0

    def _calculate_adx(self, highs: List[float], lows: List[float], closes: List[float], period: int) -> float:
        # Упрощенный ADX (требует DM+/DM-)
        if len(highs) < period:
            return 0
        # Полная реализация сложна, упрощаем до среднего диапазона
        ranges = [highs[i] - lows[i] for i in range(len(highs))]
        return sum(ranges[-period:]) / period

    def _calculate_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int) -> float:
        if len(highs) < period:
            return 0
        trs = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _get_ai_confirmation(self, signal: str, score: int, klines: List[Dict[str, Any]], index: int) -> str:
        """Получить подтверждение от AI."""
        try:
            from ..prompts.strategies.hybrid import HybridStrategy
            strategy = HybridStrategy()
            current_price = klines[index]["closePrice"]
            rsi = self.calculate_indicators(klines, index).get("rsi", 50)
            volume_ratio = self.calculate_indicators(klines, index).get("volume_ratio", 1.0)

            ctx = {
                "signal_data": {
                    "signal": signal,
                    "score": score,
                    "max_score": self.rules.get("max_score", 8),
                    "quality": score / self.rules.get("max_score", 8),
                    "reasons": [f"Score {score}"],
                    "details": {"long_score": score if signal == "BUY" else 0, "short_score": score if signal == "SELL" else 0}
                },
                "current_price": current_price,
                "rsi": rsi,
                "volume_ratio": volume_ratio,
                "volume_status": "High" if volume_ratio > 1.0 else "Low",
                "global_trend": "UP",  # Упрощено
                "local_trend": "UP" if signal == "BUY" else "DOWN",
                "last_5_direction": "UP",  # Упрощено
                "support": current_price * 0.95,
                "resistance": current_price * 1.05,
                "seb_status": "INSIDE",
                "trend_quality_desc": "High",
                "long_sl": current_price * 0.99,
                "long_tp": current_price * 1.03,
                "short_sl": current_price * 1.01,
                "short_tp": current_price * 0.97
            }

            prompt = strategy.get_role() + "\n\n" + strategy.get_objective() + "\n\n" + strategy.get_time_horizon() + "\n\n" + strategy.get_strategy_section(ctx)
            prompt += "\n\nОтветь только JSON: {\"action\": \"buy\" или \"sell\" или \"hold\"}"

            response = get_prediction(prompt)
            # Парсить JSON из ответа
            import json
            try:
                ai_response = json.loads(response.strip())
                return ai_response.get("action", "hold").lower()
            except:
                # Если не JSON, искать buy/sell/hold
                response_lower = response.lower()
                if "buy" in response_lower and signal == "BUY":
                    return "buy"
                elif "sell" in response_lower and signal == "SELL":
                    return "sell"
                else:
                    return "hold"
        except Exception as e:
            print(f"AI error: {e}")
            return "hold"  # Если AI не работает, HOLD