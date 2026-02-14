# Performance Tracking Module

Модуль отслеживания производительности (`src/core/performance.py`) анализирует историю сделок и предоставляет обратную связь для настройки параметров торговой системы.

## Основные возможности

- **Детальная статистика**: винрейт, средний PnL, время удержания позиций
- **Анализ по режимам**: производительность в разных рыночных условиях (TRENDING, RANGING, VOLATILE, TRANSITIONAL)
- **Анализ по скорам**: эффективность сигналов разного качества (4-5, 6-7, 8+)
- **Автоматические рекомендации**: предложения по корректировке параметров на основе данных
- **Упрощенная статистика**: для динамического sizing позиций

## Конфигурация

В `bot_config.json`:

```json
{
  "PERFORMANCE_TRACKING": {
    "enabled": true,
    "min_trades_for_analysis": 10,
    "auto_calibration": false,
    "calibration_check_interval": 100,
    "win_rate_floor": 0.30,
    "max_auto_score_adjust": 2
  }
}
```

### Параметры

- `enabled` — включить/выключить трекинг
- `min_trades_for_analysis` — минимум сделок для анализа (по умолчанию 10)
- `auto_calibration` — автоматическое применение рекомендаций (в разработке, по умолчанию false)
- `calibration_check_interval` — интервал проверки калибровки (количество сделок)
- `win_rate_floor` — минимальный приемлемый винрейт (по умолчанию 30%)
- `max_auto_score_adjust` — максимальная корректировка min_score при автокалибровке

## Использование

### Базовое использование

```python
from src.core.performance import get_performance_tracker

# Получить singleton instance
tracker = get_performance_tracker()

# Детальная статистика (все символы, последние 20 сделок)
stats = tracker.get_stats(last_n=20)
print(f"Винрейт: {stats['win_rate']*100:.1f}%")
print(f"Средний PnL: ${stats['avg_pnl']:.2f}")
print(f"Серия: {stats['streak']:+d}")

# Статистика по режимам
for regime, data in stats['by_regime'].items():
    print(f"{regime}: WR={data['win_rate']*100:.1f}%, Count={data['count']}")

# Статистика по диапазонам скоров
for score_range, data in stats['by_score_range'].items():
    print(f"Score {score_range}: WR={data['win_rate']*100:.1f}%, Count={data['count']}")
```

### Статистика по символу

```python
# Только BTC-USDT, последние 10 сделок
btc_stats = tracker.get_stats(symbol="BTC-USDT", last_n=10)
```

### Упрощенная статистика для sizing

```python
# Для динамического sizing позиций
perf = tracker.get_recent_performance(symbol="BTC-USDT", last_n=10)

base_size = 10.0  # Базовый размер позиции в %
if perf['win_rate'] > 0.7:
    adjusted_size = base_size * 1.2  # Увеличиваем при хорошей производительности
elif perf['win_rate'] < 0.4:
    adjusted_size = base_size * 0.7  # Уменьшаем при плохой производительности
else:
    adjusted_size = base_size
```

### Получение рекомендаций

```python
# Получить предложения по настройке параметров
suggestions = tracker.should_adjust_thresholds()

for suggestion in suggestions:
    print(f"Параметр: {suggestion['parameter']}")
    print(f"Текущее значение: {suggestion['current']}")
    print(f"Предлагаемое: {suggestion['suggested']}")
    print(f"Причина: {suggestion['reason']}")
    print(f"Уверенность: {suggestion['confidence']*100:.0f}%")
```

## Структура данных

### get_stats() возвращает

```python
{
    "total_trades": int,               # Всего сделок в выборке
    "win_rate": float,                 # Винрейт (0-1)
    "avg_pnl": float,                  # Средний PnL в USD
    "avg_hold_time_hours": float,      # Среднее время удержания в часах
    "streak": int,                     # Текущая серия (+win, -loss)

    "by_regime": {
        "TRENDING": {
            "win_rate": float,
            "avg_pnl": float,
            "count": int
        },
        "RANGING": {...},
        "VOLATILE": {...},
        "TRANSITIONAL": {...}
    },

    "by_score_range": {
        "4-5": {
            "win_rate": float,
            "avg_pnl": float,
            "count": int
        },
        "6-7": {...},
        "8+": {...}
    }
}
```

### get_recent_performance() возвращает

```python
{
    "win_rate": float,        # Винрейт последних N сделок
    "avg_pnl": float,         # Средний PnL
    "streak": int,            # Текущая серия
    "sample_size": int        # Размер выборки
}
```

### should_adjust_thresholds() возвращает

```python
[
    {
        "parameter": str,              # Название параметра
        "current": float/int,          # Текущее значение
        "suggested": float/int,        # Предлагаемое значение
        "reason": str,                 # Причина рекомендации
        "confidence": float,           # Уверенность (0-1)
        "auto_apply": bool             # Можно ли применить автоматически
    }
]
```

## Логика рекомендаций

### Повышение min_score_for_signal

Если винрейт для диапазона скоров 4-5 < 30% при 10+ сделках:
```
min_score: 5 → 6
Причина: "Винрейт при скоре 4-5 составляет 28% (10 сделок)"
```

### Повышение min_score для режима

Если винрейт в режиме < 25% при 5+ сделках:
```
regime_params.RANGING.min_score: 6 → 7
Причина: "Винрейт в режиме RANGING составляет 22% (8 сделок)"
```

### Повышение min_volume_ratio

Если >60% проигрышей имели volume_ratio < 0.8 при 10+ сделках:
```
min_volume_ratio: 0.5 → 0.6
Причина: "65% проигрышей при volume_ratio < 0.8"
```

## Интеграция с trade_tracker.py

Для правильной работы анализа необходимо, чтобы `trade_tracker.py` записывал дополнительные поля при открытии сделки:

```python
trade_data = {
    # ... стандартные поля ...
    "entry_regime": regime,           # TRENDING/RANGING/VOLATILE/TRANSITIONAL
    "entry_score": score,             # Скор сигнала
    "entry_quality": quality,         # Качество сигнала (0-1)
    "entry_rsi": rsi,                 # RSI на момент входа
    "entry_atr": atr,                 # ATR на момент входа
    "entry_volume_ratio": vol_ratio   # Отношение объема к среднему
}
```

**Примечание**: Эти поля опциональны. Модуль gracefully обрабатывает их отсутствие в старых записях.

## Примеры

### Демонстрация

Запустите демонстрационный скрипт:

```bash
python3 examples/performance_demo.py
```

Он создаст примерные данные и покажет все возможности модуля.

### Тесты

Запустите тесты в контейнере:

```bash
podman run --rm -v .:/app:Z -w /app python:3.12-slim sh -c \
  "pip install -q pytest && python -m pytest tests/test_performance.py -v"
```

## Best Practices

1. **Минимальный размер выборки**: Всегда проверяйте `sample_size` или `count` перед принятием решений
2. **Фильтрация по символу**: Разные символы могут иметь разную производительность
3. **Временное окно**: Используйте `last_n` для анализа недавней производительности
4. **Автокалибровка**: Пока не реализована, используйте рекомендации вручную
5. **Логирование**: Модуль логирует рекомендации через `src.utils.logger`

## Roadmap

- [ ] Автоматическое применение рекомендаций (auto_calibration)
- [ ] Анализ по времени суток / дню недели
- [ ] Детекция аномалий в производительности
- [ ] Экспорт метрик в формате CSV/JSON
- [ ] Dashboard с визуализацией метрик
- [ ] A/B тестирование параметров
