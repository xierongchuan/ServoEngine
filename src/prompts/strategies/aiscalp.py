from src.prompts.strategies.base import BaseStrategy


class AiScalpStrategy(BaseStrategy):

    def get_role(self) -> str:
        return "Ты — AI Scalping Trader (внутридневной трейдер на 1m TF с мультитаймфреймовым анализом)."

    def get_objective(self) -> str:
        return "Ловить дневные движения в направлении HTF тренда. Держать позицию 4-12 часов."

    def get_time_horizon(self) -> str:
        return "Horizon: 4-12 часов. Закрыть к концу торговой сессии."

    def get_strategy_section(self, ctx: dict) -> str:
        ctx.get("global_trend", "N/A")
        ctx.get("local_trend", "N/A")
        last_5_direction = ctx.get("last_5_direction", "MIXED")
        support = ctx.get("support", 0)
        resistance = ctx.get("resistance", 0)
        seb_status = ctx.get("seb_status", "INSIDE")
        trend_quality_desc = ctx.get("trend_quality_desc", "Low")

        # === HTF CONTEXT BLOCK ===
        htf_block = ""
        htf_data = ctx.get("htf_data")
        if htf_data:
            htf_trend = htf_data.get("htf_trend", "NEUTRAL")
            daily_bias = htf_data.get("daily_bias", "NEUTRAL")
            htf_rsi = htf_data.get("htf_rsi", 50)
            daily_change = htf_data.get("daily_change_pct", 0)
            htf_block = f"""
---

### HIGHER TIMEFRAME (1H)
| HTF Trend | Daily Bias | HTF RSI | Change |
|-----------|------------|---------|--------|
| {htf_trend} | {daily_bias} | {htf_rsi:.1f} | {daily_change:+.2f}% |

**ПРАВИЛО: Торгуй ТОЛЬКО в направлении HTF. Контр-трендовые сделки — высокий риск.**
"""

        # === SESSION CONTEXT BLOCK ===
        session_block = ""
        session_data = ctx.get("session_data")
        if session_data:
            hour = session_data.get("current_hour_utc", 0)
            sessions = "+".join(session_data.get("active_sessions", [])) or "NONE"
            quality = session_data.get("session_quality", "MEDIUM")
            session_block = f"""
---

### TRADING SESSION
| Time UTC | Active Sessions | Quality |
|----------|----------------|---------|
| {hour}:00 | {sessions} | {quality} |

{"⚠️ Низкая ликвидность — используй осторожность." if quality == "LOW" else ""}
"""

        # === SIGNAL DECISION BLOCK ===
        signal_block = ""
        signal_data = ctx.get("signal_data")
        if signal_data:
            sig = signal_data.get("signal", "HOLD")
            reasons = signal_data.get("reasons", [])
            reasons_str = ", ".join(reasons[:5]) if reasons else "N/A"
            
            if sig == "HOLD":
                signal_block = f"""
---

### SYSTEM SIGNAL: HOLD
**Причина:** {reasons_str}

**Это означает:** Текущая рыночная ситуация не соответствует критериям для входа.
**Твоя роль:** Не просто согласиться с HOLD — оцени свечи и уровни. Если видишь ЧЕТКИЙ сетап с подтверждением — можешь открыть позицию против рекомендации.
"""
            else:
                signal_block = f"""
---

### SYSTEM SIGNAL: {sig}
**Оценка:** {reasons_str}

**Рекомендация системы:** {'Вход в LONG' if sig == 'BUY' else 'Вход в SHORT'}.
**Твоя роль:** Подтвердить или отклонить на основе свечного анализа и качества уровней.
"""

        # === CLOSE SIGNAL BLOCK ===
        close_block = ""
        det_close = ctx.get("close_signal")
        if det_close and det_close.get("should_close"):
            close_reason = det_close.get("reason", "Exit signal")
            close_urgency = det_close.get("urgency", "medium")
            urgency_note = "**СРОЧНО — рекомендуется закрыть.**" if close_urgency == "high" else "Рекомендуется оценить."
            close_block = f"""
---

### EXIT SIGNAL
**Система рекомендует CLOSE:** {close_reason}
Уровень срочности: {close_urgency}

{urgency_note}
Ты можешь переопределить если твой анализ показывает, что позиция ещё актуальна.
"""

        # === RISK WARNING BLOCK ===
        risk_block = ""
        risk_warning = ctx.get("risk_warning")
        if risk_warning:
            risk_block = f"""
---

### ⚠️ RISK WARNING
**{risk_warning}**
"""

        # === MARKET STATE FOR AI ANALYSIS ===
        # Только ключевые данные для ИИ-оценки
        
        warnings = []
        if "MIXED" in last_5_direction and trend_quality_desc == "Low":
            warnings.append("⚠️ MIXED направление + низкое качество тренда")
        
        warnings_block = "\n".join(warnings) if warnings else ""

        return f"""## 3. STRATEГИЯ: AI SCALPING (1m TF + 1H HTF)

{htf_block}{session_block}{signal_block}{close_block}{risk_block}
---

### РЫНОЧНАЯ ОЦЕНКА (твоя зона ответственности)

**Тебе даны ВСЕ данные. Система уже рассчитала формальные критерии.
Твоя задача — оценить качество и психологию рынка.**

{warnings_block}

---

### ЧТО ТЕБЕ НУЖНО ОЦЕНИТЬ

**1. Свечной паттерн и психология:**

Текущее направление: {last_5_direction}
- Это ИМПУЛЬС или НАКОПЛЕНИЕ?
- Свечи с сильными хвостами (ловля ножей)?
- Объём подтверждает движение?

**2. Качество тренда:**

SEB Status: {seb_status}
Trend Quality: {trend_quality_desc}
- Тренд устойчивый или слабый?
- Цена у верхней/нижней границы?

**3. Уровни поддержки/сопротивления:**

Support: {support:.2f}
Resistance: {resistance:.2f}
- Эти уровни ещё актуальны?
- Были ли протестированы недавно?

**4. Объём:**

Volume: {ctx.get("volume_status", "N/A")}
- Идёт ли в направлении сделки?

---

### КОГДА СИСТЕМА СКАЗАЛА HOLD

Не принимай HOLD автоматически. Оцени:

✅ **Можно открыть позицию несмотря на HOLD если:**
- Чёткий свечной паттерн разворота на важном уровне
- HTF тренд подтверждён (не нейтральный)
- Объём в направлении сделки

❌ **Держись подальше:**
- Цена в середине диапазона (далеко от уровней)
- Новости идёт против сделки
- Конфликт HTF и LTF направлений
- Low-liquidity сессия без чёткого сетапа
"""

    def get_position_management(self, ctx: dict) -> str:
        signal_data = ctx.get("signal_data", {})
        regime = signal_data.get("regime", "UNKNOWN")
        
        base_rules = """### УПРАВЛЕНИЕ ПОЗИЦИЕЙ

**ГЛАВНОЕ: Не закрывай раньше времени. Дай прибыли расти.**

| PnL | Действие |
|-----|----------|
| +0.5% → +1.5% | SL в безубыток, HOLD |
| +1.5% → +3% | Закрой 50%, trailing stop на остатке |
| > +3% | Trailing stop, дай забрать |
| < -1% | HOLD если структура цела, CLOSE если уровень сломан |
| Near SL | НЕ двигай SL, принимай убыток |

**При потере импульса (PnL > +2%):**
- RSI перекуплен/перепродан → частичное закрытие
- MACD разворот → закрытие"""

        if regime == "RANGE":
            base_rules += """

**⚠️ RANGE режим:**
- Более узкие цели
- Быстрее фиксировать прибыль
- Не держать через новости"""
        
        return base_rules

    def get_special_situations(self, ctx: dict) -> str:
        return """### SPECIAL SITUATIONS (AI SCALP)

**1. STRONG TREND DAY:**
- Цена движется в одном направлении весь день
- НЕ торгуй против тренда
- Можно добавлять на откатах

**2. FAKEOUT:**
- Пробой → возврат = вход ПРОТИВ пробоя
- SL за экстремумом ложного пробоя

**3. REVERSAL:**
- После сильного утреннего движения цена разворачивается
- Жди подтверждения (пробой ключевого уровня)
- Осторожно — ложные развороры часты

**4. HTF REVERSAL:**
- HTF тренд меняется while in position
- Затяни SL или закрой если в убытке
- НЕ добавляй против нового HTF"""
