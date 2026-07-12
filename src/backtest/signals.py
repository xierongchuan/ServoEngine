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
        self._scalp_analyzer = None
        self._scalp_signal_gen = None
        self._scalp_last_index = -1

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
        recent_moves = [
            abs(closes[i] - closes[i - 1])
            for i in range(max(1, len(closes) - 20), len(closes))
        ]
        average_move = sum(recent_moves) / len(recent_moves) if recent_moves else atr
        atr_ratio = atr / average_move if average_move > 0 else 1.0
        atr_percent = atr / closes[-1] * 100 if closes[-1] > 0 else 0.0

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
                last_5_direction = "STRONG_UP"
            elif up_candles >= 3:
                last_5_direction = "UP"
            elif down_candles >= 4:
                last_5_direction = "STRONG_DOWN"
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
            "atr_percent": atr_percent,
            "volume_ratio": volume_ratio,
            "close_prices": closes,
            "open_prices": opens,
            "last_5_direction": last_5_direction,
            "candle_time": klines[index].get("snapshotTimeUTC", index),
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
        if self.strategy.lower() == "scalp":
            return self._generate_scalp_signal(klines, index, position)

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

        # Рассчитать SL/TP если есть сигнал на вход
        if normalized["action"] in ("BUY", "SELL"):
            preset = self.config.get("preset", {})
            sl_multiplier = preset.get("sl_multiplier")
            tp_multiplier = preset.get("tp_multiplier")
            current_price = klines[index]["closePrice"]
            atr = analysis.get("atr", 0)
            if sl_multiplier and tp_multiplier and atr > 0:
                sl_distance = atr * float(sl_multiplier)
                tp_distance = atr * float(tp_multiplier)
                if normalized["action"] == "BUY":
                    normalized["stop_loss"] = current_price - sl_distance
                    normalized["take_profit"] = current_price + tp_distance
                else:
                    normalized["stop_loss"] = current_price + sl_distance
                    normalized["take_profit"] = current_price - tp_distance
            else:
                # Совместимость со старыми процентными конфигами.
                sl_pct = preset.get("sl_percent")
                tp_pct = preset.get("tp_percent")
                if sl_pct and tp_pct:
                    if normalized["action"] == "BUY":
                        normalized["stop_loss"] = current_price * (1 - sl_pct / 100)
                        normalized["take_profit"] = current_price * (1 + tp_pct / 100)
                    else:
                        normalized["stop_loss"] = current_price * (1 + sl_pct / 100)
                        normalized["take_profit"] = current_price * (1 - tp_pct / 100)

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

    def _generate_scalp_signal(self, klines, index, position=None) -> Dict[str, Any]:
        """Свечной replay реального SCALP analyzer/signal generator без look-ahead."""
        from ..core.lightweight_analyzer import LightweightAnalyzer
        from ..core.scalp_signal import ScalpSignalGenerator
        from ..core.regime import detect_regime

        if index < 31:
            return {"action": "HOLD", "reason": "Недостаточно данных"}

        def normalized(item):
            candle = dict(item)
            candle.setdefault("timestamp", candle.get("snapshotTimeUTC", index))
            return candle

        if self._scalp_analyzer is None:
            self._scalp_analyzer = LightweightAnalyzer(
                self.config.get("_resolved", {}).get("symbol", "BACKTEST"),
                config=self.config.get("signal_rules", {}),
            )
            self._scalp_signal_gen = ScalpSignalGenerator(config=self.config)
            history = [normalized(item) for item in klines[:index + 1]]
            if not self._scalp_analyzer.bootstrap(history):
                return {"action": "HOLD", "reason": "Bootstrap SCALP не готов"}
            self._scalp_last_index = index
        elif index > self._scalp_last_index:
            for candle_index in range(self._scalp_last_index + 1, index + 1):
                self._scalp_analyzer.update(normalized(klines[candle_index]))
            self._scalp_last_index = index

        indicators = self._scalp_analyzer.get_snapshot()
        self.last_indicators = indicators
        regime = detect_regime({
            "ema9": indicators.get("ema_fast", 0),
            "ema21": indicators.get("ema_med", 0),
            "bb_upper": indicators.get("bb_upper", 0),
            "bb_lower": indicators.get("bb_lower", 0),
            "close_prices": list(self._scalp_analyzer._recent_closes),
            "atr_ratio": indicators.get("atr_ratio", 1),
        })

        if position:
            exit_signal = self._scalp_signal_gen.check_exit(indicators, position)
            if exit_signal.get("should_close"):
                return {"action": "CLOSE", "reason": exit_signal.get("reason", "SCALP exit")}
            return {"action": "HOLD", "reason": "Позиция сопровождается"}

        result = self._scalp_signal_gen.generate(indicators, regime=regime, ob_imbalance=0.0)
        action = result.get("signal", "HOLD")
        quality = float(result.get("quality", 0.0))
        auto_quality = self.config.get("signal_rules", {}).get("auto_execute_quality", 0.6)
        ai_cfg = self.config.get("ai_integration", {})
        if not ai_cfg.get("veto_enabled", True):
            auto_quality = self.config.get("signal_rules", {}).get("no_ai_execute_quality", 0.4)
        if action not in {"BUY", "SELL"} or quality < auto_quality:
            return {
                "action": "HOLD", "score": result.get("score", 0),
                "reason": (result.get("reasons") or ["Нет сигнала"])[0],
            }
        price = float(indicators["current_price"])
        atr = float(indicators.get("atr", 0))
        sl_mult = self.config.get("sl_tp", {}).get("sl_atr_mult", 1.0)
        tp_mult = self.config.get("sl_tp", {}).get("tp_atr_mult", 3.0)
        risk_cfg = self.config.get("risk_limits", {})
        leverage = max(float(self.config.get("preset", {}).get("leverage", 1)), 1.0)
        stop_fraction = atr * sl_mult / price if price > 0 else 0
        risk_pct = float(risk_cfg.get("risk_per_trade_pct", 0.35))
        size_pct = risk_pct / (leverage * stop_fraction) if stop_fraction > 0 else risk_cfg.get("base_position_pct", 5)
        size_pct *= 0.8 + quality * 0.4
        size_pct = max(
            float(risk_cfg.get("min_position_pct", 2)),
            min(float(risk_cfg.get("max_position_pct", 7)), size_pct),
        )
        return {
            "action": action,
            "score": result.get("score", 0),
            "quality": quality,
            "size_pct": size_pct,
            "reason": (result.get("reasons") or ["SCALP"])[0],
            "stop_loss": price - atr * sl_mult if action == "BUY" else price + atr * sl_mult,
            "take_profit": price + atr * tp_mult if action == "BUY" else price - atr * tp_mult,
        }

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
