from src.prompts.strategies.base import BaseStrategy


class ScalpStrategy(BaseStrategy):

    def get_role(self) -> str:
        return "Ты — Algo-Scalper (адаптированный для крипто-фьючерсов)."

    def get_objective(self) -> str:
        return "Быстрые сделки с минимальным временем экспозиции. Цель: забрать дисбаланс, не угадывать тренд."

    def get_time_horizon(self) -> str:
        return "Horizon: 1-15 минут. Максимум 30 минут в позиции."

    def get_strategy_section(self, ctx: dict) -> str:
        # Извлекаем переменные из контекста
        current_price = ctx.get("current_price", 0)
        atr = ctx.get("atr", 0)
        rsi = ctx.get("rsi", 50)
        volume_ratio = ctx.get("volume_ratio", 1.0)
        volume_status = ctx.get("volume_status", "Норма")

        global_trend = ctx.get("global_trend", "N/A")
        local_trend = ctx.get("local_trend", "N/A")
        last_5_direction = ctx.get("last_5_direction", "MIXED")

        support = ctx.get("support", 0)
        resistance = ctx.get("resistance", 0)
        support_dist_pct = ctx.get("support_dist_pct", 0)
        resistance_dist_pct = ctx.get("resistance_dist_pct", 0)

        seb_upper = ctx.get("seb_upper", 0)
        seb_lower = ctx.get("seb_lower", 0)
        seb_status = ctx.get("seb_status", "INSIDE")
        trend_quality_desc = ctx.get("trend_quality_desc", "Low")

        long_sl = ctx.get("long_sl", 0)
        long_tp = ctx.get("long_tp", 0)
        short_sl = ctx.get("short_sl", 0)
        short_tp = ctx.get("short_tp", 0)

        # Расчет % потенциала для наглядности
        if current_price > 0:
            long_potential_pct = (long_tp - current_price) / current_price * 100
            short_potential_pct = (current_price - short_tp) / current_price * 100
            long_risk_pct = (current_price - long_sl) / current_price * 100
            short_risk_pct = (short_sl - current_price) / current_price * 100
        else:
            long_potential_pct = short_potential_pct = long_risk_pct = short_risk_pct = 0

        # Динамические предупреждения
        warnings = []
        if volume_ratio < 0.5:
            warnings.append("LOW VOLUME: Высокий риск ложных движений.")
        if "MIXED" in last_5_direction:
            warnings.append("CHOPPY: Последние свечи без направления — жди ясности.")
        if trend_quality_desc == "Low":
            warnings.append("WEAK TREND: R2 низкий, тренд нестабильный.")
        if abs(support_dist_pct) < 0.3 or abs(resistance_dist_pct) < 0.3:
            warnings.append("NEAR LEVEL: Цена у ключевого уровня — вероятен отбой или пробой.")

        warnings_block = ""
        if warnings:
            warnings_block = "\n**ТЕКУЩИЕ РИСКИ:**\n" + "\n".join(f"- {w}" for w in warnings)

        return f"""## 3. СТРАТЕГИЯ: ADAPTIVE SCALPING (1m TF)
*Контекст: Микро-структура рынка. Скорость важнее предсказаний. Рынок ГРЯЗНЫЙ — большинство сигналов ложные.*

---

### МЕНТАЛИТЕТ СКАЛЬПЕРА

**Реальность рынка:**
- 60-70% сетапов НЕ сработают идеально — это НОРМАЛЬНО
- Твоя задача: быстро распознать ошибку и выйти с минимальным убытком
- НЕ пытайся предсказать будущее — реагируй на ТЕКУЩИЙ дисбаланс
- "Укусить и убежать" — не жадничай

**Враги скальпера:**
1. Овертрейдинг (входить в каждое движение)
2. Надежда ("сейчас развернется")
3. Жадность ("еще немного подождать")
{warnings_block}

---

### СЕТАПЫ ДЛЯ ВХОДА

**1. MOMENTUM BREAKOUT (Пробой с импульсом)**

Условия для LONG:
- Цена пробивает resistance ({resistance:.2f}) или SEB Upper ({seb_upper:.2f})
- Volume Ratio >= 1.0x (сейчас: {volume_ratio:.2f}x — {volume_status})
- RSI растет, НО < 80 (сейчас: {rsi:.1f})
- Last 5 Direction: UP, STRONG UP или WEAK UP (сейчас: {last_5_direction})

Условия для SHORT:
- Цена пробивает support ({support:.2f}) или SEB Lower ({seb_lower:.2f})
- Volume Ratio >= 1.0x
- RSI падает, НО > 20
- Last 5 Direction: DOWN, STRONG DOWN или WEAK DOWN

**RSI для MOMENTUM:**
- RSI > 70 + Volume >= 1.0x + Trend Aligned = СИЛА (покупай импульс)
- RSI > 75 + Volume < 0.8x = возможно ИСТОЩЕНИЕ (осторожно)
- RSI 55-70 при UP тренде = оптимальная зона для LONG
- RSI 30-45 при DOWN тренде = оптимальная зона для SHORT

---

**2. LIQUIDITY GRAB (Сбор стопов + разворот)**

Условия для LONG (Fake Breakdown):
- Цена ПРОКОЛОЛА support ({support:.2f}) минимум на 0.1-0.3%
- SEB Status = BELOW_LOWER (сейчас: {seb_status})
- НО свеча ЗАКРЫВАЕТСЯ ВЫШЕ support (возврат в диапазон)
- Volume spike на проколе

Условия для SHORT (Fake Breakout):
- Цена ПРОКОЛОЛА resistance минимум на 0.1-0.3%
- SEB Status = ABOVE_UPPER
- Свеча закрывается НИЖЕ resistance

**Триггер:** Жди закрытия свечи ВНУТРИ диапазона после прокола.

---

**3. RANGE SCALP (Работа в боковике)**

Условия:
- SEB Status = INSIDE (цена внутри канала)
- Trend Quality = Low или Medium (нет сильного тренда — это ОК для range)
- Volume >= 0.6x (не мертвый рынок)

**LONG у нижней границы:**
- Цена касается support ({support:.2f}) или SEB Lower ({seb_lower:.2f})
- RSI < 45

**SHORT у верхней границы:**
- Цена касается resistance ({resistance:.2f}) или SEB Upper ({seb_upper:.2f})
- RSI > 55

**Вход:** От границы к центру диапазона.
**SL:** За границу диапазона (tight).
**TP:** До середины или противоположной границы.

---

**4. MEAN REVERSION (Возврат к среднему)**

Условия:
- SEB Status = ABOVE_UPPER или BELOW_LOWER (экстремум)
- Trend Quality = Low или Medium (нестабильный тренд)
- Volume затухает (< 0.8x)
- RSI в экстремуме (> 70 или < 30)

**Вход:** Против направления, к середине канала.
**ВНИМАНИЕ:** Рискованный сетап, но часто срабатывает. Используй confidence >= 0.65 и tight SL.

---

### УПРАВЛЕНИЕ ПОЗИЦИЕЙ (ATR-BASED)

**Для LONG:**
| Параметр | Уровень | % от цены |
|----------|---------|-----------|
| Entry | ~{current_price:.2f} | — |
| Stop Loss | {long_sl:.2f} | -{long_risk_pct:.2f}% |
| Take Profit | {long_tp:.2f} | +{long_potential_pct:.2f}% |

**Для SHORT:**
| Параметр | Уровень | % от цены |
|----------|---------|-----------|
| Entry | ~{current_price:.2f} | — |
| Stop Loss | {short_sl:.2f} | +{short_risk_pct:.2f}% |
| Take Profit | {short_tp:.2f} | -{short_potential_pct:.2f}% |

**Правила SL/TP:**
1. ВСЕГДА ставь SL — без исключений
2. Используй рекомендованные уровни (ATR-based)
3. Можешь УЖЕСТОЧИТЬ SL если видишь сильный уровень
4. НЕ расширяй SL дальше рекомендованного

---

### АДАПТАЦИЯ К СОСТОЯНИЮ РЫНКА

**Текущее состояние:**
- Тренд: Global={global_trend}, Local={local_trend}
- Импульс: {last_5_direction} ({volume_status})
- Качество тренда: {trend_quality_desc}
- SEB: {seb_status}

| Состояние | Volume | Действие |
|-----------|--------|----------|
| Trending + High Vol | >= 1.0x | Momentum Breakout |
| Trending + Normal Vol | 0.6-1.0x | Осторожный Momentum, tight SL |
| Ranging (INSIDE) | >= 0.5x | Range Scalp от границ |
| Extreme (ABOVE/BELOW) | Any | Liquidity Grab или Mean Reversion |
| Low Volume | < 0.3x | HOLD (мёртвый рынок) |

---

### КОГДА НЕ ВХОДИТЬ (HOLD)

**Жёсткие фильтры (HOLD если все выполнены):**
1. Volume Ratio < 0.3x (рынок мёртв)
2. Last 5 Direction = MIXED + Trend Quality = Low + RSI 45-55 (полный хаос)
3. Только что был резкий move > 3x ATR без отката (жди 2-3 свечи)

**Мягкие фильтры (HOLD если 2+ выполнены):**
- Цена точно в середине диапазона (>0.3% от обоих уровней)
- Volume < 0.5x И RSI 40-60
- MIXED direction + Low trend quality

**ВАЖНО:** Не пропускай сделку только потому что "нет идеального сетапа".
Если есть явный дисбаланс (RSI, уровень, объём) — рассмотри вход.

**Помни:** Скальпинг — это про РЕАКЦИЮ, не про предсказания."""

    def get_position_management(self, ctx: dict) -> str:
        """Специфичное управление позицией для скальпинга."""
        return """### УПРАВЛЕНИЕ ПОЗИЦИЕЙ (SCALP MODE)

**ПРАВИЛО:** Быстро входи, быстро выходи. НЕ жди "runners".

1. **В плюсе (+0.3% и выше):**
   - Фиксируй прибыль ИЛИ подтягивай SL в безубыток
   - НЕ жди больше если импульс затухает (volume падает)
   - Частичное закрытие (50%) — нормальная практика

2. **В небольшом минусе (до -0.5%):**
   - HOLD если сетап еще валиден (структура не сломана)
   - CLOSE если структура сломана (пробит уровень против тебя)

3. **Близко к SL:**
   - НЕ двигай SL дальше — это нарушение плана
   - Прими убыток, переходи к следующей сделке
   - Убыток по SL — это НОРМАЛЬНО, не ошибка

4. **Позиция "зависла" (нет движения):**
   - Если цена стоит на месте > 5 свечей — рассмотри CLOSE
   - "Мертвые деньги" = упущенные возможности"""

    def get_special_situations(self, ctx: dict) -> str:
        """Упрощенные специальные ситуации для скальпа."""
        return """### СПЕЦИАЛЬНЫЕ СИТУАЦИИ (SCALP)

**1. PANIC DUMP (RSI < 25, резкое падение > 2%):**
- НЕ шортить дно — скорее всего будет отскок
- Рассмотри counter-trend LONG на возврате выше support
- Жди подтверждение (зеленая свеча + volume)

**2. FAKEOUT / ЛОЖНЫЙ ПРОБОЙ:**
- Цена пробила уровень, но вернулась обратно
- Это Liquidity Grab — вход ПРОТИВ направления прокола
- SL: за экстремум прокола

**3. NEWS SPIKE (если есть новость):**
- Резкий move на новости — НЕ входи сразу
- Жди 2-3 свечи для понимания направления
- Или пропусти — волатильность слишком высокая"""
