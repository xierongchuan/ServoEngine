import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core.predict import get_prediction


class SignalGenerator:
    """
    Генерирует сигналы для стратегий через единый интерфейс.

    Работает как адаптер между стратегией и BacktestEngine:
    - Рассчитывает индикаторы
    - Делегирует генерацию сигнала в реальный генератор стратегии
    - Проверяет условия выхода (should_close) когда есть открытая позиция

    Это обеспечивает strategy-agnostic DTO поток:
    SignalGenerator.generate_signal() → {action, score, reason} → TradeCommand
    """

    def __init__(self, strategy: str = "MACDX", config: Dict[str, Any] = None):
        self.strategy = strategy
        self.config = config or {}
        # Параметры из конфига
        self.rules = self.config.get("signal_rules", {})
        # Кэш последних индикаторов для повторного использования в engine
        self.last_indicators: Dict[str, Any] = {}
        # Exit context — состояние выхода, сохраняется между свечами
        self._exit_context: Dict[str, Any] = {}
        # Cooldown tracking для backtest
        self._last_close_time: Optional[str] = None
        self._cooldown_hours: float = (
            config.get("preset", {}).get("cooldown_after_close_hours", 0)
            if config
            else 0
        )

    def calculate_indicators(
        self, klines: List[Dict[str, Any]], index: int
    ) -> Dict[str, Any]:
        """Рассчитывает индикаторы на основе последних свечей."""
        if index < 30:  # Минимум данных
            return {}

        closes = [k["closePrice"] for k in klines[: index + 1]]
        highs = [k["highPrice"] for k in klines[: index + 1]]
        lows = [k["lowPrice"] for k in klines[: index + 1]]
        volumes = [k["volume"] for k in klines[: index + 1]]

        # RSI (14) — инкрементальный расчёт серии за один проход
        rsi_values = self._calculate_rsi_series(closes, 14)
        rsi = rsi_values[-1] if rsi_values else 50

        # EMA9, EMA21
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)

        # MACD (12,26,9) — с hist_prev за один вызов
        macd_line, macd_signal, macd_hist, macd_hist_prev, macd_hist_prev_prev = (
            self._calculate_macd_with_prev(closes)
        )

        # BB width
        bb_width = self._calculate_bb_width(closes, 20)

        # ADX (14)
        adx = self._calculate_adx(highs, lows, closes, 14)

        # ATR (14)
        atr = self._calculate_atr(highs, lows, closes, 14)
        atr_ratio = atr / closes[-1] if closes[-1] > 0 else 0

        # Volume ratio
        volume_ratio = (
            volumes[-1] / (sum(volumes[-20:]) / 20) if len(volumes) >= 20 else 1
        )

        opens = [k["openPrice"] for k in klines[: index + 1]]

        # Last 5 direction
        last_5_closes = closes[-5:] if len(closes) >= 5 else closes
        if len(last_5_closes) >= 2:
            up_candles = sum(
                1
                for i in range(1, len(last_5_closes))
                if last_5_closes[i] > last_5_closes[i - 1]
            )
            down_candles = len(last_5_closes) - 1 - up_candles
            if up_candles >= 4:
                last_5_direction = "STRONG UP"
            elif up_candles >= 3:
                last_5_direction = "UP"
            elif down_candles >= 4:
                last_5_direction = "STRONG DOWN"
            elif down_candles >= 3:
                last_5_direction = "DOWN"
            else:
                last_5_direction = "MIXED"
        else:
            last_5_direction = "MIXED"

        return {
            "rsi": rsi,
            "rsi_values": rsi_values,
            "ema9": ema9,
            "ema21": ema21,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "macd_hist_prev": macd_hist_prev,
            "macd_hist_2prev": macd_hist_prev_prev,
            "bb_width": bb_width,
            "adx": adx,
            "atr": atr,
            "atr_ratio": atr_ratio,
            "volume_ratio": volume_ratio,
            "close_prices": closes,
            "open_prices": opens,
            "last_5_direction": last_5_direction,
        }

    def _get_signal_gen(self):
        """Ленивая инициализация генератора сигналов стратегии."""
        # Всегда пересоздавать для учёта новых весов из конфига
        strategy_lower = self.strategy.lower()
        if strategy_lower == "macdx":
            from ..core.signals.macdx import MacdxSignalGenerator as SignalGen
        elif strategy_lower == "hybrid":
            from ..core.signals.hybrid import HybridSignalGenerator as SignalGen
        elif strategy_lower == "aiscalp":
            from ..core.signals.aiscalp import AiscalpSignalGenerator as SignalGen
        else:
            self._signal_gen_instance = None
            return None
        self._signal_gen_instance = SignalGen(self.config)
        return self._signal_gen_instance

    def set_last_close_time(self, close_time: str):
        """Устанавливает время последнего закрытия для cooldown в backtest."""
        self._last_close_time = close_time

    def generate_signal(
        self,
        klines: List[Dict[str, Any]],
        index: int,
        position: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Генерирует единый сигнал через DTO — входы и выходы через один интерфейс.

        Когда position != None и стратегия поддерживает should_close(),
        сначала проверяются условия выхода. Если should_close() срабатывает,
        возвращается {action: "CLOSE", reason: ...}. Иначе — обычный сигнал.

        Это позволяет BacktestEngine оставаться strategy-agnostic:
        engine вызывает generate_signal() → получает единый dict → конвертирует в TradeCommand.

        Args:
            klines: массив свечей
            index: индекс текущей свечи
            position: открытая позиция {side: "LONG"/"SHORT", entry_price: float} или None
        """
        analysis = self.calculate_indicators(klines, index)
        self.last_indicators = analysis
        if not analysis:
            return {"action": "HOLD", "reason": "Недостаточно данных"}

        analysis["current_price"] = klines[index]["closePrice"]

        # Cooldown check для backtest
        if self._cooldown_hours > 0 and self._last_close_time:
            try:
                # Поддержка ISO и классического формата
                close_str = self._last_close_time.replace("T", " ")
                close_dt = datetime.strptime(close_str, "%Y-%m-%d %H:%M:%S")
                kline_time = klines[index].get("snapshotTimeUTC", "")
                if kline_time:
                    # Backtest использует snapshotTimeUTC в формате ISO
                    if isinstance(kline_time, (int, float)):
                        kline_dt = datetime.fromtimestamp(kline_time / 1000)
                    else:
                        kline_dt = datetime.strptime(
                            str(kline_time).replace("T", " "), "%Y-%m-%d %H:%M:%S"
                        )
                    hours_since = (kline_dt - close_dt).total_seconds() / 3600
                    if hours_since < self._cooldown_hours:
                        remaining = self._cooldown_hours - hours_since
                        return {
                            "action": "HOLD",
                            "reason": f"Cooldown: {remaining:.1f}h remaining",
                            "score": 0,
                        }
            except Exception:
                pass

        try:
            gen = self._get_signal_gen()
            if gen is None:
                return {
                    "action": "HOLD",
                    "reason": f"Unsupported strategy: {self.strategy}",
                }

            # Передать last_close_time в генератор для cooldown
            if self._last_close_time and hasattr(gen, "set_last_close_time"):
                gen.set_last_close_time(self._last_close_time)

            # Проверить условия выхода, если есть открытая позиция
            if position and hasattr(gen, "should_close"):
                close_signal = self._check_exit(gen, analysis, position)
                if close_signal:
                    return close_signal

            result = gen.generate(analysis)
        except ImportError:
            return {
                "action": "HOLD",
                "reason": f"Signal generator for {self.strategy} not found",
            }
        except Exception as e:
            return {"action": "HOLD", "reason": f"Error generating signal: {e}"}

        # Нормализовать ключи
        normalized = {
            "action": result.get("signal", "HOLD"),
            "score": result.get("score", 0),
            "reason": result.get("reasons", ["нет"])[0]
            if result.get("reasons")
            else "нет",
        }

        # Если есть AI, добавить подтверждение
        if self.strategy.upper() in ["HYBRID", "HYBRID_VETO"] and normalized[
            "action"
        ] in ["BUY", "SELL"]:
            signal = normalized["action"]
            score = normalized["score"]
            ai_decision = self._get_ai_confirmation(signal, score, klines, index)
            if ai_decision == "hold":
                normalized = {
                    "action": "HOLD",
                    "reason": f"AI rejected {signal.lower()} signal",
                }
            elif ai_decision == signal.lower():
                normalized["reason"] = (
                    normalized.get("reason", "") + " (AI approved)"
                ).strip()
            else:
                normalized = {
                    "action": "HOLD",
                    "reason": f"AI changed signal to {ai_decision}",
                }

        return normalized

    def _check_exit(
        self, gen, analysis: Dict[str, Any], position: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Проверяет условия выхода через should_close() стратегии.

        Формирует position dict в формате, совместимом с should_close(),
        и передаёт exit_context для отслеживания состояния между свечами.

        Returns:
            {action: "CLOSE", reason: ...} если нужно закрыть, иначе None
        """
        side = position.get("side", "LONG")
        entry_price = position.get("entry_price", 0)

        # Формируем position dict совместимый с should_close()
        bt_position = {
            "type": side.replace("LONG", "BUY").replace("SHORT", "SELL"),
            "entry": entry_price,
            "avgPrice": entry_price,
        }

        try:
            close_signal = gen.should_close(
                analysis, bt_position, exit_context=self._exit_context
            )

            if close_signal.get("should_close"):
                reason = close_signal.get("reason", "Strategy exit")
                return {
                    "action": "CLOSE",
                    "reason": reason,
                    "score": 0,
                    "urgency": close_signal.get("urgency", "medium"),
                }
        except Exception as e:
            # Если should_close() падает — не блокируем работу, просто логируем
            pass

        return None

    def reset_exit_context(self):
        """Сбрасывает exit_context (вызывается после закрытия позиции)."""
        self._exit_context.clear()

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
        sum_gain = sum(gains[1 : period + 1])
        sum_loss = sum(losses[1 : period + 1])

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

    def _calculate_macd_with_prev(
        self, closes: List[float]
    ) -> Tuple[float, float, float, float, float]:
        """
        Рассчитывает MACD line, signal, histogram и предыдущий histogram за один проход.

        Использует инкрементальные EMA серии вместо пересчёта на каждом подмассиве.
        Возвращает (macd_line, macd_signal, macd_hist, macd_hist_prev).
        """
        if len(closes) < 26:
            return 0, 0, 0, 0, 0

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
            return 0, 0, 0, 0, 0

        macd_line = macd_series[-1]

        # Signal line — EMA(9) от MACD серии
        if len(macd_series) >= 9:
            signal_multiplier = 2 / (9 + 1)
            signal_ema = sum(macd_series[:9]) / 9
            for val in macd_series[9:]:
                signal_ema = (val * signal_multiplier) + (
                    signal_ema * (1 - signal_multiplier)
                )
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
                    signal_ema_prev = (val * signal_multiplier) + (
                        signal_ema_prev * (1 - signal_multiplier)
                    )
                macd_hist_prev = prev_series[-1] - signal_ema_prev
            else:
                macd_hist_prev = 0
        else:
            macd_hist_prev = 0

        # Histogram 2 свечи назад
        if len(macd_series) >= 3:
            prev_prev_series = macd_series[:-2]
            if len(prev_prev_series) >= 9:
                signal_ema_prev_prev = sum(prev_prev_series[:9]) / 9
                for val in prev_prev_series[9:]:
                    signal_ema_prev_prev = (val * signal_multiplier) + (
                        signal_ema_prev_prev * (1 - signal_multiplier)
                    )
                macd_hist_prev_prev = prev_prev_series[-1] - signal_ema_prev_prev
            else:
                macd_hist_prev_prev = 0
        else:
            macd_hist_prev_prev = 0

        return macd_line, macd_signal, macd_hist, macd_hist_prev, macd_hist_prev_prev

    def _calculate_bb_width(self, closes: List[float], period: int) -> float:
        if len(closes) < period:
            return 0
        sma = sum(closes[-period:]) / period
        variance = sum((x - sma) ** 2 for x in closes[-period:]) / period
        std = math.sqrt(variance)
        return (std / sma) * 100 if sma > 0 else 0

    def _calculate_adx(
        self, highs: List[float], lows: List[float], closes: List[float], period: int
    ) -> float:
        """Рассчитывает настоящий ADX с использованием core/indicators."""
        if len(highs) < period + 1:
            return 0

        klines = []
        for i in range(len(highs)):
            klines.append(
                {
                    "highPrice": highs[i],
                    "lowPrice": lows[i],
                    "closePrice": closes[i],
                }
            )

        try:
            from src.core.indicators import calculate_adx

            result = calculate_adx(klines, period)
            return result.get("adx", 0)
        except Exception:
            ranges = [highs[i] - lows[i] for i in range(len(highs))]
            return sum(ranges[-period:]) / period

    def _calculate_atr(
        self, highs: List[float], lows: List[float], closes: List[float], period: int
    ) -> float:
        if len(highs) < period:
            return 0
        trs = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _get_ai_confirmation(
        self, signal: str, score: int, klines: List[Dict[str, Any]], index: int
    ) -> str:
        """Получить подтверждение от AI."""
        try:
            from ..prompts.strategies.hybrid import HybridStrategy

            strategy = HybridStrategy()
            current_price = klines[index]["closePrice"]
            rsi = self.calculate_indicators(klines, index).get("rsi", 50)
            volume_ratio = self.calculate_indicators(klines, index).get(
                "volume_ratio", 1.0
            )

            ctx = {
                "signal_data": {
                    "signal": signal,
                    "score": score,
                    "max_score": self.rules.get("max_score", 8),
                    "quality": score / self.rules.get("max_score", 8),
                    "reasons": [f"Score {score}"],
                    "details": {
                        "long_score": score if signal == "BUY" else 0,
                        "short_score": score if signal == "SELL" else 0,
                    },
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
                "short_tp": current_price * 0.97,
            }

            prompt = (
                strategy.get_role()
                + "\n\n"
                + strategy.get_objective()
                + "\n\n"
                + strategy.get_time_horizon()
                + "\n\n"
                + strategy.get_strategy_section(ctx)
            )
            prompt += '\n\nОтветь только JSON: {"action": "buy" или "sell" или "hold"}'

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
