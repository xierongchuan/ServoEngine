"""
Grid Trading Strategy - стратегия для сбора спреда через сетку лимитных ордеров.
"""

from src.prompts.strategies.base import BaseStrategy


class GridStrategy(BaseStrategy):
    """
    Grid Trading стратегия.

    Зарабатывает на волатильности через сетку лимитных ордеров.
    AI используется для:
    - Анализа волатильности и корректировки spacing
    - Рекомендации по смещению сетки
    - Определения условий для pause/resume
    """

    def get_role(self) -> str:
        return "Ты — Grid Trading Bot для криптовалютных фьючерсов."

    def get_objective(self) -> str:
        return "Зарабатывать на волатильности через сетку лимитных ордеров. Управлять inventory риском."

    def get_time_horizon(self) -> str:
        return "Horizon: Непрерывный. Сетка работает пока активна. Отдельные сделки: минуты-часы."

    def get_strategy_section(self, ctx: dict) -> str:
        """
        Генерирует секцию стратегии для промпта.

        ctx содержит:
        - grid_levels: количество уровней
        - grid_spacing_pct: шаг в процентах
        - inventory: текущий inventory
        - inventory_limit: лимит
        - atr_pct: текущий ATR в процентах
        - current_price: текущая цена
        - trend: текущий тренд
        """
        grid_levels = ctx.get("grid_levels", 5)
        spacing_pct = ctx.get("grid_spacing_pct", 0.3)
        inventory = ctx.get("inventory", 0)
        inventory_limit = ctx.get("inventory_limit", 100)
        atr_pct = ctx.get("atr_pct", 0.5)
        ctx.get("current_price", 0)
        trend = ctx.get("trend", "NEUTRAL")

        inventory_pct = (inventory / inventory_limit * 100) if inventory_limit > 0 else 0

        return f"""## 3. СТРАТЕГИЯ: GRID TRADING

### ПРИНЦИП РАБОТЫ
Grid Trading размещает лимитные ордера на фиксированных уровнях выше и ниже текущей цены.
При движении цены ордера исполняются, создавая прибыль на колебаниях.

### ТЕКУЩИЕ ПАРАМЕТРЫ СЕТКИ
| Параметр | Значение |
|----------|----------|
| Уровней с каждой стороны | {grid_levels} |
| Шаг между уровнями | {spacing_pct}% |
| Текущий inventory | {inventory:.4f} ({inventory_pct:.1f}% от лимита) |
| Лимит inventory | {inventory_limit} |
| ATR% (волатильность) | {atr_pct:.3f}% |
| Текущий тренд | {trend} |

### ЛОГИКА УПРАВЛЕНИЯ СЕТКОЙ

**1. Inventory Management (КРИТИЧНО):**
- Inventory = Net Position (положительный = long, отрицательный = short)
- При накоплении inventory смещаем сетку ПРОТИВ накопления:
  - Long inventory → смещаем центр ВНИЗ (sell ближе к цене)
  - Short inventory → смещаем центр ВВЕРХ (buy ближе к цене)
- При достижении лимита: ПРЕКРАЩАЕМ ордера в сторону накопления

**2. Адаптация к волатильности:**
- Низкая волатильность (ATR < 0.3%): СУЖАЕМ spacing (spacing_mult < 1.0)
- Нормальная волатильность: spacing_mult = 1.0
- Высокая волатильность (ATR > 1%): РАСШИРЯЕМ spacing (spacing_mult > 1.0)
- Сильный тренд: ПАУЗА сетки или смещение в сторону тренда

**3. Risk Management:**
- Emergency close при inventory > 150% от лимита
- Emergency close при PnL < -5%
- Graceful shutdown: отмена всех ордеров при остановке

### РЕШЕНИЯ ДЛЯ AI

Проанализируй текущую ситуацию и определи:

1. `grid_action`: Действие с сеткой
   - "maintain" — продолжить с текущими параметрами
   - "shift_up" — сместить центр вверх (при short inventory)
   - "shift_down" — сместить центр вниз (при long inventory)
   - "pause" — приостановить сетку (сильный тренд, высокий риск)
   - "widen" — расширить spacing (высокая волатильность)
   - "narrow" — сузить spacing (низкая волатильность)

2. `spacing_mult`: Множитель spacing (0.5 - 2.0)
   - < 1.0: более агрессивная сетка (узкий spacing)
   - = 1.0: стандартный spacing
   - > 1.0: более консервативная сетка (широкий spacing)

3. `center_offset_pct`: Смещение центра в процентах (-2% до +2%)
   - Отрицательное: смещение вниз
   - Положительное: смещение вверх

4. `confidence`: Уверенность в рекомендации (0.0 - 1.0)

5. `reason`: Краткое обоснование (1-2 предложения)

### ФОРМАТ ОТВЕТА

```json
{{
  "grid_action": "maintain|shift_up|shift_down|pause|widen|narrow",
  "spacing_mult": 1.0,
  "center_offset_pct": 0.0,
  "confidence": 0.8,
  "reason": "Краткое обоснование решения"
}}
```

### ПРИМЕРЫ РЕШЕНИЙ

**Пример 1: Нормальные условия**
- ATR = 0.5%, inventory = 10%, тренд = NEUTRAL
- Решение: maintain, spacing_mult = 1.0

**Пример 2: Накоплен long inventory**
- ATR = 0.4%, inventory = +60%, тренд = UP
- Решение: shift_down, center_offset = -0.3% (приближаем sell)

**Пример 3: Высокая волатильность**
- ATR = 1.5%, inventory = 0%, тренд = NEUTRAL
- Решение: widen, spacing_mult = 1.5

**Пример 4: Сильный тренд**
- ATR = 2%, inventory = -30%, тренд = STRONG_DOWN
- Решение: pause (опасно торговать против тренда)
"""

    def get_position_management(self, ctx: dict) -> str | None:
        """Grid-специфичное управление позициями."""
        return """### GRID POSITION MANAGEMENT

**Inventory Rules:**
- Inventory = сумма всех исполненных buy минус sell
- Positive inventory = net long exposure
- Negative inventory = net short exposure

**Rebalancing:**
- Периодически (каждые 5 минут) анализируем и корректируем
- При inventory > 50% лимита: агрессивное смещение
- При inventory > 80% лимита: приостановка ордеров в сторону накопления

**Emergency:**
- При inventory > 150% лимита: экстренное закрытие
- При PnL < -5%: экстренное закрытие
"""

    def get_special_situations(self, ctx: dict) -> str | None:
        """Grid-специфичные особые ситуации."""
        return """### GRID SPECIAL SITUATIONS

**1. Flash Crash / Pump:**
- Мгновенное исполнение нескольких уровней
- Действие: PAUSE сетки, дождаться стабилизации

**2. Low Liquidity:**
- Большой spread bid/ask (> 0.5%)
- Действие: WIDEN spacing, уменьшить размер ордеров

**3. Trending Market:**
- Цена движется в одном направлении > 3 уровней
- Действие: PAUSE или смещение в сторону тренда

**4. High Funding Rate:**
- Funding > 0.1% (8-часовой)
- Действие: Учитывать при оценке прибыльности
"""

    def get_risk_table(self, ctx: dict) -> str | None:
        """Grid-специфичная таблица рисков."""
        return """### GRID RISK TABLE

| Риск | Индикатор | Действие |
|------|-----------|----------|
| Inventory overflow | inv > 100% limit | Pause one side |
| Trending market | 3+ levels hit one side | Shift or pause |
| High volatility | ATR > 2% | Widen spacing |
| Low volatility | ATR < 0.2% | Narrow spacing |
| Flash crash | > 5 levels in 1 min | Emergency pause |
| API errors | 3+ consecutive fails | Pause & alert |
"""
