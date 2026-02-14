"""
HybridStrategy - AI acts as a filter, not a signal generator.

In HYBRID mode:
1. Deterministic system generates BUY/SELL signals based on rules
2. AI receives the signal + market context
3. AI can only APPROVE or REJECT the signal
4. AI CANNOT generate its own signals

This removes randomness and makes the system testable.
"""

from src.prompts.strategies.base import BaseStrategy


class HybridStrategy(BaseStrategy):

    def get_role(self) -> str:
        return "Ты — AI-фильтр торговых сигналов. Ты НЕ генерируешь сигналы, только ПОДТВЕРЖДАЕШЬ или ОТКЛОНЯЕШЬ."

    def get_objective(self) -> str:
        return "Оценить качество сигнала от детерминированной системы. Отсеять явно плохие входы."

    def get_time_horizon(self) -> str:
        return "Horizon: 4-12 часов (внутридневная торговля)."

    def get_strategy_section(self, ctx: dict) -> str:
        # Извлекаем данные сигнала
        signal_data = ctx.get("signal_data", {})
        signal = signal_data.get("signal", "HOLD")
        score = signal_data.get("score", 0)
        max_score = signal_data.get("max_score", 10)
        quality = signal_data.get("quality", 0.0)
        reasons = signal_data.get("reasons", [])
        details = signal_data.get("details", {})

        # Рыночный контекст
        current_price = ctx.get("current_price", 0)
        rsi = ctx.get("rsi", 50)
        volume_ratio = ctx.get("volume_ratio", 1.0)
        volume_status = ctx.get("volume_status", "Норма")
        global_trend = ctx.get("global_trend", "N/A")
        local_trend = ctx.get("local_trend", "N/A")
        last_5_direction = ctx.get("last_5_direction", "MIXED")

        support = ctx.get("support", 0)
        resistance = ctx.get("resistance", 0)
        seb_status = ctx.get("seb_status", "INSIDE")
        trend_quality_desc = ctx.get("trend_quality_desc", "Low")

        long_sl = ctx.get("long_sl", 0)
        long_tp = ctx.get("long_tp", 0)
        short_sl = ctx.get("short_sl", 0)
        short_tp = ctx.get("short_tp", 0)

        # Формируем строку причин
        reasons_str = "\n".join(f"  - {r}" for r in reasons) if reasons else "  - No specific reasons"

        # Детали сигнала
        long_score = details.get("long_score", 0)
        short_score = details.get("short_score", 0)

        # Regime data
        regime = signal_data.get("regime", "UNKNOWN")

        return f"""## 3. РЕЖИМ: HYBRID (AI-ФИЛЬТР)

**ВАЖНО:** Ты НЕ генерируешь сигналы. Детерминированная система уже сгенерировала сигнал.
The signal has ALREADY passed quantitative filters (RSI, volume, trend, MACD, BB, S/R).
Your job: assess RISK the scoring system might have missed.

---

### ПОЛУЧЕННЫЙ СИГНАЛ ОТ СИСТЕМЫ

| Параметр | Значение |
|----------|----------|
| **Сигнал** | **{signal}** |
| **Score** | {score}/{max_score} |
| **Quality** | {quality:.2f} |
| **Regime** | {regime} |
| **LONG Score** | {long_score} |
| **SHORT Score** | {short_score} |

**Причины сигнала:**
{reasons_str}

---

### ТЕКУЩИЙ КОНТЕКСТ РЫНКА

| Индикатор | Значение | Статус |
|-----------|----------|--------|
| Price | {current_price:.2f} | — |
| RSI | {rsi:.1f} | {"⚠️ Overbought" if rsi > 70 else "⚠️ Oversold" if rsi < 30 else "✓ Normal"} |
| Volume | {volume_ratio:.2f}x | {volume_status} |
| Global Trend | {global_trend} | — |
| Local Trend | {local_trend} | — |
| Last 5 Candles | {last_5_direction} | — |
| Trend Quality | {trend_quality_desc} | — |
| SEB Status | {seb_status} | — |
| Support | {support:.2f} | — |
| Resistance | {resistance:.2f} | — |

---

### ТВОЯ ЗАДАЧА

**Если сигнал {signal}:**

1. **APPROVE** — если контекст подтверждает сигнал:
   - Тренд соответствует направлению
   - Нет явных противоречий (RSI экстремум против сигнала)
   - Volume достаточный
   - Нет очевидных red flags

2. **REJECT** — если видишь явные проблемы:
   - RSI > 75 для BUY (перекуплен)
   - RSI < 25 для SELL (перепродан)
   - Тренд явно против сигнала
   - Volume < 0.3x (мёртвый рынок)
   - Цена в середине range без направления

---

### ПРАВИЛА ДЛЯ AI-ФИЛЬТРА

**ТЫ МОЖЕШЬ:**
- Подтвердить сигнал (action = "{signal.lower()}")
- Отклонить сигнал (action = "hold")
- Предложить SL/TP уровни

**ТЫ НЕ МОЖЕШЬ:**
- Генерировать противоположный сигнал (если система дала BUY, ты не можешь дать SELL)
- Придумывать свои сигналы (если система дала HOLD, ты даёшь HOLD)

---

### SL/TP РЕКОМЕНДАЦИИ

**Если APPROVE для {signal}:**

{"**LONG:**" if signal == "BUY" else "**SHORT:**"}
| Параметр | Уровень |
|----------|---------|
| Entry | ~{current_price:.2f} |
| Stop Loss | {(long_sl if signal == "BUY" else short_sl):.2f} |
| Take Profit | {(long_tp if signal == "BUY" else short_tp):.2f} |

---

### ПРИМЕРЫ РЕШЕНИЙ

**APPROVE пример:**
- Система дала BUY, RSI=55, Volume=1.2x, Trend=UP → APPROVE

**REJECT пример:**
- Система дала BUY, но RSI=78, Volume=0.2x → REJECT (overbought + low volume)

---

**ПОМНИ:** Твоя роль — страховка от очевидно плохих входов, не генерация сигналов."""

    def get_position_management(self, ctx: dict) -> str:
        """Position management for existing positions."""
        close_signal = ctx.get("close_signal") or {}
        should_close = close_signal.get("should_close", False)
        close_reason = close_signal.get("reason", "")
        urgency = close_signal.get("urgency", "low")

        if should_close:
            return f"""### СИГНАЛ НА ЗАКРЫТИЕ ПОЗИЦИИ

**Детерминированная система рекомендует ЗАКРЫТЬ позицию.**

| Параметр | Значение |
|----------|----------|
| Рекомендация | CLOSE |
| Причина | {close_reason} |
| Срочность | {urgency.upper()} |

**Твоя задача:** Подтвердить или отклонить закрытие.

- **APPROVE** (action = "close"): Согласен с закрытием
- **REJECT** (action = "hold"): Считаю что нужно держать дальше"""

        return """### УПРАВЛЕНИЕ ПОЗИЦИЕЙ (HYBRID MODE)

Если есть открытая позиция, система проверяет условия выхода:
- RSI экстремумы
- Разворот тренда
- Достижение профита

Ты можешь подтвердить или отклонить рекомендацию на закрытие."""

    def get_special_situations(self, ctx: dict) -> str:
        signal_data = ctx.get("signal_data", {})
        max_score = signal_data.get("max_score", 10)

        return f"""### КОГДА ТОЧНО REJECT

1. **RSI экстремум против сигнала:**
   - BUY при RSI > 75 → REJECT
   - SELL при RSI < 25 → REJECT

2. **Мёртвый рынок:**
   - Volume < 0.3x → REJECT любой сигнал

3. **Conflicting trends:**
   - BUY при Global=DOWN + Local=BEARISH → REJECT
   - SELL при Global=UP + Local=BULLISH → REJECT

4. **Choppy market:**
   - Last 5 Direction = MIXED + Low quality → REJECT

### КОГДА ТОЧНО APPROVE

1. **Всё выровнено:**
   - Тренд, RSI, Volume — все в одном направлении → APPROVE

2. **Высокий score:**
   - Score >= 8/{max_score} обычно означает сильный сигнал → APPROVE
   - Quality > 0.7 — высокая конвикция → APPROVE

3. **Momentum подтверждён:**
   - Last 5 Direction совпадает с сигналом + Volume > 1.0x → APPROVE"""
