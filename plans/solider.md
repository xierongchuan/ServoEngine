# SOLIDER — Рефакторинг `src/core/`

## Контекст

### Текущее состояние

Проект — мультистратегийный трейдинг-бот с 6 стратегиями (SCALP, HYBRID, AISCALP, MACDX, GRID, SWING).
`src/core/` содержит ~11,000 строк кода в 23 файлах, один плоский пакет без логического разделения.

### Выявленные проблемы

#### 1. God-файлы

| Файл | Строк | Что делает |
|------|-------|-----------|
| `analyzer.py` | 1,231 | Загрузка данных + расчёт ВСЕХ индикаторов + детекция трендов + генерация сигналов (HYBRID/AISCALP inline) + сборка промпта + возврат 30+ ключей |
| `scalp_engine.py` | 1,365 | Dual-loop оркестрация + TrailingStopManager + ScalpSession + AI veto + order book + performance tracking + calibration + логирование |
| `process_worker.py` | 749 | Главный пайплайн: `if STRATEGY_STYLE == "SCALP" / "HYBRID" / "AISCALP" / "MACDX" / "SWING" / "GRID"` — каждая ветка 50-150 строк дублированного кода |

#### 2. Глобальный конфиг вместо Dependency Injection

Почти каждый файл импортирует напрямую из `src.config`:

```python
# Было — 15 файлов с прямым импортом
from src.config import BOT_CONFIG, SCALP_SETTINGS, STRATEGY_STYLE, ...

class SignalGenerator:
    def __init__(self):
        self.settings = BOT_CONFIG.get("HYBRID_SETTINGS", {})  # хардкод
```

Последствия:
- Нельзя инстанциировать с разными настройками без изменения глобального состояния
- Тестирование требует мокирования глобальных модулей
- Невозможно использовать один компонент с разными параметрами одновременно
- Новая стратегия = править глобальный конфиг

#### 3. Дублирование кода

| Что дублируется | В скольких файлах | Строк |
|-----------------|-------------------|-------|
| `_detect_rsi_divergence()` | 3 (signal_generator, aiscalp_signal, macdx_signal) | ~120 |
| `_hold_result()` | 3 | ~45 |
| Quality → Confidence mapping | 4 | ~60 |
| PnL calculation `(current - entry) / entry * 100` | 8+ | ~40 |
| `hasattr(position, 'entry_price')` проверка | 10+ | ~80 |
| JSON + fcntl атомарная запись | 3 (trade_tracker, decision_journal, executor) | ~90 |
| SL/TP + sizing + validation пайплайн | 3 ветки в process_worker | ~150 |

#### 4. Нет абстракций

- Нет общего интерфейса для генераторов сигналов — каждый со своей сигнатурой
- Нет интерфейса для пайплайнов стратегий — `if/elif/elif` в process_worker
- Новая стратегия = править `process_worker.py` (нарушение Open/Closed Principle)

#### 5. Детерминированная логика в промптах

- `swing.py` и `swing_veto.py` содержат 50+ строк Python-вычислений (risk flags, R/R расчёт)
- `hybrid_veto.py` — 18 строк if/else для генерации risk flags
- Числовые пороги (`RSI > 75`, `Volume < 0.3x`) продублированы в промптах и в core

#### 6. Нет `core/swing_signal.py`

SWING стратегия полностью полагается на ИИ для принятия решений, но все критерии входа/выхода — детерминированные правила с числовыми порогами. Нет соответствующей реализации в core.

---

## Целевая архитектура

```
src/core/
│
├── indicators/                    # Чистые математические функции
│   ├── __init__.py                # Re-exports всех индикаторов
│   ├── trend.py                   # EMA, SMA, SEB (linear regression)
│   ├── momentum.py                # RSI, MACD, RSI divergence detection
│   ├── volatility.py              # ATR, Bollinger Bands
│   └── levels.py                  # Support/Resistance, Pivot Points, get_price_value
│
├── signals/                       # Генераторы торговых сигналов
│   ├── __init__.py                # Re-exports + factory
│   ├── base.py                    # BaseSignalGenerator (ABC)
│   ├── utils.py                   # Дедупликация: divergence, quality mapping, PnL, PositionAdapter
│   ├── factory.py                 # create_signal_generator(strategy, config)
│   ├── hybrid.py                  # SignalGenerator
│   ├── aiscalp.py                 # AiScalpSignalGenerator
│   ├── macdx.py                   # MACDXSignalGenerator
│   └── scalp.py                   # ScalpSignalGenerator
│
├── strategies/                    # Стратегии-оркестраторы
│   ├── __init__.py                # Re-exports + factory
│   ├── base.py                    # StrategyPipeline (ABC)
│   ├── factory.py                 # create_pipeline(strategy, config)
│   │
│   ├── scalp/                     # SCALP — разбитый God-класс
│   │   ├── __init__.py
│   │   ├── engine.py              # ScalpEngine (только оркестрация)
│   │   ├── trailing.py            # TrailingStopManager
│   │   ├── session.py             # ScalpSession
│   │   └── veto.py                # AI veto processing
│   │
│   ├── grid/                      # GRID
│   │   ├── __init__.py
│   │   ├── executor.py            # GridExecutor
│   │   ├── worker.py              # run_grid_worker
│   │   └── adx.py                 # _calculate_adx
│   │
│   ├── hybrid.py                  # HybridPipeline
│   ├── aiscalp.py                 # AiscalpPipeline
│   ├── macdx.py                   # MacdxPipeline
│   └── swing.py                   # SwingPipeline
│
├── execution/                     # Исполнение ордеров и управление рисками
│   ├── __init__.py                # Re-exports
│   ├── order.py                   # create_order, place_order, get_open_positions
│   ├── position.py                # PositionAdapter, PnL расчёты, SL/TP management
│   ├── risk.py                    # calculate_dynamic_sl_tp, calculate_position_size
│   └── validator.py               # validate_risk_parameters, validate_prediction
│
├── tracking/                      # Отслеживание позиций и аналитика
│   ├── __init__.py                # Re-exports
│   ├── trade.py                   # TradeTracker
│   ├── journal.py                 # DecisionJournal
│   ├── performance.py             # PerformanceTracker
│   ├── scalp_perf.py              # ScalpPerformanceTracker + ScalpCalibrator
│   └── monitor.py                 # monitor_symbol
│
├── data/                          # Работа с данными
│   ├── __init__.py                # Re-exports
│   ├── collector.py               # fetch_prices, fetch_news, process_symbol
│   └── storage.py                 # AtomicJsonStore (дедупликация JSON+fcntl)
│
├── regime.py                      # MarketRegimeDetector — оставить как есть
├── session.py                     # Trading sessions — оставить как есть
├── predict.py                     # AI client — оставить как есть
├── plotter.py                     # Charts — оставить как есть
├── lightweight_analyzer.py        # SCALP incremental indicators — оставить
├── pipeline.py                    # ← НОВЫЙ: замена process_worker.py
└── __init__.py                    # Re-exports для обратной совместимости
```

---

## Принципы

### SOLID

| Принцип | Применение |
|---------|-----------|
| **S** — Single Responsibility | Каждый файл < 300 строк, одна задача. God-файлы разбиты |
| **O** — Open/Closed | `StrategyPipeline` ABC — новая стратегия = новый класс, оркестратор не правится |
| **L** — Liskov Substitution | Все генераторы наследуют `BaseSignalGenerator` — взаимозаменяемы |
| **I** — Interface Segregation | Узкие интерфейсы: `ISignalGenerator`, `IPipeline`, `IExecutor` |
| **D** — Dependency Inversion | DI через конструктор вместо `from src.config import BOT_CONFIG` |

### DRY

Каждый блок логики — в одном месте:

| Было | Стало |
|------|-------|
| `_detect_rsi_divergence()` в 3 файлах | `signals/utils.py` — 1 функция |
| `_hold_result()` в 3 файлах | `BaseSignalGenerator._hold_result()` |
| Quality mapping в 4 файлах | `signals/utils.py` — 1 функция |
| PnL calculation в 8+ файлах | `signals/utils.py` — 1 функция |
| PositionAdapter в 10+ файлах | `signals/utils.py` — 1 класс |
| JSON+fcntl в 3 файлах | `data/storage.py` — 1 класс |

---

## Детальный план по этапам

### ЭТАП 1: `indicators/` — чистая математика

**Приоритет:** P0 | **Риск:** Нулевой | **Зависимости:** Нет

Индикаторы — чистые функции, не зависят ни от чего. Самый безопасный первый шаг.

#### `indicators/trend.py`

Перенести из `analyzer.py`:

```python
def calculate_ema(prices: list[float], period: int) -> float
def calculate_sma_series(values: list[float], period: int) -> list[float]
def calculate_ema_series(values: list[float], period: int) -> list[float]
def calculate_seb(prices: list[float], length: int = 50, mult: float = 2.0) -> tuple
def calculate_seb_series(prices: list[float], length: int = 50, mult: float = 2.0) -> tuple
```

#### `indicators/momentum.py`

Перенести из `analyzer.py`:

```python
def calculate_macd(prices: list[float], fast=12, slow=26, signal=9) -> tuple
def calculate_rsi_series(prices: list[float], period: int = 14) -> list[float]
def detect_rsi_divergence(prices: list[float], rsi_values: list[float], window: int = 20) -> tuple
```

#### `indicators/volatility.py`

Перенести из `analyzer.py`:

```python
def calculate_atr(prices_data: list[dict], period: int = 14) -> float
def calculate_bollinger_bands(prices: list[float], period: int = 20, std_mult: float = 2.0) -> tuple
```

#### `indicators/levels.py`

Перенести из `analyzer.py`:

```python
def get_price_value(price_item) -> float
def calculate_support_resistance(prices: list[float], window: int = 20) -> dict
```

#### `indicators/__init__.py`

```python
from .trend import calculate_ema, calculate_sma_series, calculate_ema_series, calculate_seb, calculate_seb_series
from .momentum import calculate_macd, calculate_rsi_series, detect_rsi_divergence
from .volatility import calculate_atr, calculate_bollinger_bands
from .levels import get_price_value, calculate_support_resistance
```

---

### ЭТАП 2: `signals/utils.py` — дедупликация

**Приоритет:** P0 | **Риск:** Низкий | **Зависимости:** Этап 1 (для divergence)

#### Общие утилиты

```python
def detect_rsi_divergence(prices, rsi_values, window=20) -> tuple[bool, bool]
    # Удалить из: signal_generator.py, aiscalp_signal.py, macdx_signal.py

def hold_result(max_score: int, reasons: list, details: dict, regime=None) -> dict
    # Удалить из: signal_generator.py, aiscalp_signal.py, macdx_signal.py

def map_quality_to_confidence(quality: float, has_signal: bool) -> float
    # quality >= 0.7 → 0.85, >= 0.4 → 0.70, else 0.55
    # Удалить из: signal_generator.py, aiscalp_signal.py, macdx_signal.py, scalp_signal.py

def calculate_pnl_pct(entry_price: float, current_price: float, direction: str) -> float
    # LONG: (current - entry) / entry * 100
    # SHORT: (entry - current) / entry * 100
    # Удалить из: 8+ файлов

class PositionAdapter:
    """Единая работа с позицией — dict или dataclass"""
    def __init__(self, position): ...
    @property
    def entry_price(self) -> float: ...
    @property
    def direction(self) -> str: ...  # "BUY" или "SELL"
    @property
    def is_long(self) -> bool: ...
```

---

### ЭТАП 3: `signals/base.py` — абстрактный базовый класс

**Приоритет:** P0 | **Риск:** Низкий | **Зависимости:** Этап 2

```python
from abc import ABC, abstractmethod

class BaseSignalGenerator(ABC):
    """Базовый класс для всех генераторов сигналов."""

    def __init__(self, settings: dict):
        self.settings = settings  # DI вместо глобального конфига

    @abstractmethod
    def generate(self, analysis: dict, **kwargs) -> dict:
        """
        Генерирует торговый сигнал.
        Returns: {signal, score, max_score, quality, confidence, reasons, details, regime}
        """

    @abstractmethod
    def should_close(self, analysis: dict, position: dict, **kwargs) -> dict:
        """
        Проверяет условия закрытия позиции.
        Returns: {should_close, reason, urgency}
        """

    # --- Общие методы ---

    def _map_quality(self, quality: float) -> float:
        """Маппинг quality → confidence."""
        if quality >= 0.7:
            return 0.85
        elif quality >= 0.4:
            return 0.70
        else:
            return 0.55

    def _hold_result(self, max_score: int, reasons: list, details: dict, regime=None) -> dict:
        """Стандартный HOLD результат."""
        return {
            "signal": "HOLD",
            "score": 0,
            "max_score": max_score,
            "quality": 0.0,
            "confidence": 0.0,
            "reasons": reasons,
            "filters_passed": False,
            "details": details,
            "regime": regime.get("regime", "UNKNOWN") if regime else "NO_REGIME",
        }
```

---

### ЭТАП 4: Перенести генераторы в `signals/`

**Приоритет:** P1 | **Риск:** Средний | **Зависимости:** Этапы 2, 3

Каждый генератор:
1. Наследует `BaseSignalGenerator`
2. Принимает `settings` через конструктор (DI)
3. Использует утилиты из `signals/utils.py`
4. Удаляет дублированный код

#### `signals/hybrid.py`

Источник: `signal_generator.py`

Изменения:
- `class SignalGenerator(BaseSignalGenerator)`
- Удалить `_detect_rsi_divergence` → использовать из utils
- Удалить `_hold_result` → использовать из base
- Удалить quality mapping → использовать из base
- Конструктор: `def __init__(self, settings: dict)`

#### `signals/aiscalp.py`

Источник: `aiscalp_signal.py`

Изменения:
- `class AiscalpSignalGenerator(BaseSignalGenerator)`
- Удалить дубликаты
- Конструктор: `def __init__(self, settings: dict)`

#### `signals/macdx.py`

Источник: `macdx_signal.py`

Изменения:
- `class MacdxSignalGenerator(BaseSignalGenerator)`
- Удалить дубликаты
- Конструктор: `def __init__(self, settings: dict)`

#### `signals/scalp.py`

Источник: `scalp_signal.py`

Изменения:
- `class ScalpSignalGenerator(BaseSignalGenerator)`
- Удалить дубликаты
- Конструктор: `def __init__(self, settings: dict)`

#### `signals/factory.py`

```python
def create_signal_generator(strategy: str, config: dict) -> BaseSignalGenerator:
    """Фабрика генераторов сигналов."""
    from .hybrid import HybridSignalGenerator
    from .aiscalp import AiscalpSignalGenerator
    from .macdx import MacdxSignalGenerator
    from .scalp import ScalpSignalGenerator

    generators = {
        "HYBRID": lambda: HybridSignalGenerator(config.get("HYBRID_SETTINGS", {})),
        "AISCALP": lambda: AiscalpSignalGenerator(config.get("AISCALP_SETTINGS", {})),
        "MACDX": lambda: MacdxSignalGenerator(config.get("MACDX_SETTINGS", {})),
        "SCALP": lambda: ScalpSignalGenerator(config.get("SCALP_SETTINGS", {})),
    }

    factory = generators.get(strategy)
    if not factory:
        raise ValueError(f"Unknown signal generator strategy: {strategy}")
    return factory()
```

#### `signals/__init__.py`

```python
from .base import BaseSignalGenerator
from .factory import create_signal_generator
from .hybrid import HybridSignalGenerator
from .aiscalp import AiscalpSignalGenerator
from .macdx import MacdxSignalGenerator
from .scalp import ScalpSignalGenerator
from .utils import detect_rsi_divergence, calculate_pnl_pct, PositionAdapter
```

---

### ЭТАП 5: `execution/`

**Приоритет:** P1 | **Риск:** Средний | **Зависимости:** Этап 2 (PositionAdapter)

#### `execution/order.py`

Источник: `executor.py`

```python
def create_order(symbol, direction, price, ai_sl, ai_tp, reason, confidence, size_pct, order_type)
def get_open_positions() -> dict
def _save_sl_tp(symbol, sl, tp)
```

#### `execution/position.py`

```python
# PositionAdapter (если не в signals/utils.py)
# PnL расчёты
# SL/TP management утилиты
```

#### `execution/risk.py`

Источник: `risk_manager.py`

```python
def calculate_dynamic_sl_tp(signal, current_price, atr, support, resistance, regime, quality)
def calculate_position_size(base_pct, quality, regime, recent_performance)
def validate_risk_parameters(sl_tp_result, min_rr_ratio, regime)
```

#### `execution/validator.py`

Источник: `predict.py`

```python
def validate_prediction(prediction, current_price, has_position)
```

#### `execution/__init__.py`

```python
from .order import create_order, get_open_positions
from .risk import calculate_dynamic_sl_tp, calculate_position_size, validate_risk_parameters
from .validator import validate_prediction
```

---

### ЭТАП 6: `tracking/`

**Приоритет:** P1 | **Риск:** Низкий | **Зависимости:** Нет

Простое перемещение файлов с обновлением импортов.

| Файл | Источник |
|------|----------|
| `tracking/trade.py` | `trade_tracker.py` |
| `tracking/journal.py` | `decision_journal.py` |
| `tracking/performance.py` | `performance.py` |
| `tracking/scalp_perf.py` | `scalp_performance.py` |
| `tracking/monitor.py` | `monitor.py` |

---

### ЭТАП 7: `strategies/scalp/` — разбить God-класс

**Приоритет:** P1 | **Риск:** Высокий | **Зависимости:** Этапы 4, 5

#### `strategies/scalp/trailing.py`

Источник: `ScalpEngine.TrailingStopManager` из `scalp_engine.py`

Просто перенести класс — уже отдельный, с хорошим SRP.

#### `strategies/scalp/session.py`

Источник: `ScalpEngine.ScalpSession` из `scalp_engine.py`

Просто перенести класс.

#### `strategies/scalp/veto.py`

Источник: методы `ScalpEngine`:
- `_process_veto`
- `_parse_regime_response`
- `_update_regime_deterministic`
- `_update_regime_ai`
- `_track_rejection`
- `_log_rejection_summary`
- `_track_veto_skip`
- `_log_veto_skip_summary`

```python
class ScalpVetoProcessor:
    def __init__(self, config: dict, signal_generator, performance_tracker):
        self.config = config
        self.signal_generator = signal_generator
        self.performance = performance_tracker

    def process_veto(self, analysis, position, regime) -> dict: ...
    def update_regime(self, analysis) -> dict: ...
    def track_rejection(self, reason: str) -> None: ...
```

#### `strategies/scalp/engine.py`

Остаётся в `ScalpEngine` после выноса:
- `__init__` — инициализация компонентов через DI
- `run` — запуск dual-loop
- `_fast_loop` — быстрый цикл
- `_slow_loop` — медленный цикл
- `_manage_position` — управление позицией
- `_check_entry` — проверка входа
- `_execute_entry` — исполнение входа
- `_close_position` — закрытие позиции
- `_partial_close` — частичное закрытие
- `_update_sl_on_exchange` — обновление SL
- `_sync_position` — синхронизация

---

### ЭТАП 8: `strategies/base.py` + пайплайны

**Приоритет:** P2 | **Риск:** Высокий | **Зависимости:** Этапы 4, 5, 7

#### `strategies/base.py`

```python
from abc import ABC, abstractmethod

class StrategyPipeline(ABC):
    """Базовый интерфейс для всех торговых пайплайнов."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def run_cycle(self, symbol: str, ws_cache, ws_ready) -> dict:
        """
        Один полный цикл торговли.
        Returns: prediction dict или None
        """
```

#### `strategies/hybrid.py`

Логика из `process_worker.py` ветка `HYBRID`:

```python
class HybridPipeline(StrategyPipeline):
    def run_cycle(self, symbol, ws_cache, ws_ready):
        # 1. Collect data
        # 2. Analyze (indicators + prompt)
        # 3. Generate signal (deterministic)
        # 4. Optional AI veto
        # 5. Calculate SL/TP, position size
        # 6. Execute
        # 7. Track
```

#### `strategies/aiscalp.py`

Логика из `process_worker.py` ветка `AISCALP`:

```python
class AiscalpPipeline(StrategyPipeline):
    def run_cycle(self, symbol, ws_cache, ws_ready):
        # 1. Collect data (incl. HTF)
        # 2. Analyze
        # 3. Pre-filter
        # 4. Regime detection
        # 5. Generate signal
        # 6. AI analysis (always)
        # 7. Calculate SL/TP, position size
        # 8. Execute
        # 9. Track
```

#### `strategies/macdx.py`

Логика из `process_worker.py` ветка `MACDX`:

```python
class MacdxPipeline(StrategyPipeline):
    def run_cycle(self, symbol, ws_cache, ws_ready):
        # 1. Collect data
        # 2. Analyze (indicators only, no prompt)
        # 3. Generate signal (deterministic, no AI)
        # 4. Calculate SL/TP
        # 5. Execute
        # 6. Track
```

#### `strategies/swing.py`

Логика из `process_worker.py` ветка `SWING`:

```python
class SwingPipeline(StrategyPipeline):
    def run_cycle(self, symbol, ws_cache, ws_ready):
        # 1. Collect data
        # 2. Analyze (with HTF)
        # 3. AI analysis
        # 4. Calculate SL/TP (wide)
        # 5. Execute
        # 6. Track
```

#### `strategies/factory.py`

```python
def create_pipeline(strategy: str, config: dict) -> StrategyPipeline:
    """Фабрика пайплайнов стратегий."""
    from .hybrid import HybridPipeline
    from .aiscalp import AiscalpPipeline
    from .macdx import MacdxPipeline
    from .swing import SwingPipeline
    from .scalp.engine import ScalpPipeline

    pipelines = {
        "SCALP": lambda: ScalpPipeline(config),
        "HYBRID": lambda: HybridPipeline(config),
        "AISCALP": lambda: AiscalpPipeline(config),
        "MACDX": lambda: MacdxPipeline(config),
        "SWING": lambda: SwingPipeline(config),
    }

    factory = pipelines.get(strategy)
    if not factory:
        raise ValueError(f"Unknown pipeline strategy: {strategy}")
    return factory()
```

---

### ЭТАП 9: `strategies/grid/`

**Приоритет:** P2 | **Риск:** Средний | **Зависимости:** Нет

| Файл | Источник |
|------|----------|
| `strategies/grid/executor.py` | `grid_executor.py` — просто перенести |
| `strategies/grid/worker.py` | `grid_worker.py` — перенести, вынести ADX |
| `strategies/grid/adx.py` | `_calculate_adx` из `grid_worker.py` |

---

### ЭТАП 10: `data/`

**Приоритет:** P2 | **Риск:** Низкий | **Зависимости:** Нет

#### `data/collector.py`

Источник: `collector.py`

```python
def fetch_prices(symbol, timeframe) -> list[dict]
def fetch_news(symbol) -> list[dict]
def fetch_htf_prices(symbol, timeframe) -> list[dict]
def process_symbol(symbol, config) -> None
```

#### `data/storage.py`

Новый — дедупликация JSON+fcntl из:
- `trade_tracker.py`
- `decision_journal.py`
- `executor.py`

```python
import fcntl
import json
import os

class AtomicJsonStore:
    """Атомарная запись JSON с блокировкой."""

    def __init__(self, filepath: str):
        self.filepath = filepath

    def read(self, default=None) -> any:
        if not os.path.exists(self.filepath):
            return default
        with open(self.filepath, 'r') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def write(self, data: any) -> None:
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        with open(self.filepath, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def append(self, item: any, key: str = None) -> None:
        data = self.read(default={})
        if key:
            data[key] = item
        else:
            if isinstance(data, list):
                data.append(item)
            else:
                data = [item]
        self.write(data)
```

---

### ЭТАП 11: `pipeline.py` — замена `process_worker.py`

**Приоритет:** P2 | **Риск:** Высокий | **Зависимости:** Этапы 4-10

```python
"""
PipelineOrchestrator — главный оркестратор торгового цикла.
Заменяет process_worker.py (749 строк if/elif/elif).
"""

from src.core.strategies.factory import create_pipeline
from src.core.data.collector import process_symbol
from src.config import STRATEGY_STYLE, BOT_CONFIG

class PipelineOrchestrator:
    def __init__(self, config: dict = None):
        self.config = config or BOT_CONFIG
        self.strategy = self.config.get("STRATEGY_STYLE", STRATEGY_STYLE)
        self.pipeline = create_pipeline(self.strategy, self.config)

    def run_cycle(self, symbol: str, ws_cache, ws_ready) -> dict:
        """Один полный цикл для символа."""
        return self.pipeline.run_cycle(symbol, ws_cache, ws_ready)

    def reload(self):
        """Перезагрузка конфигурации и пересоздание пайплайна."""
        from src.config import should_reload_config, reload_bot_config
        if should_reload_config():
            reload_bot_config()
            self.config = BOT_CONFIG
            self.strategy = self.config.get("STRATEGY_STYLE", STRATEGY_STYLE)
            self.pipeline = create_pipeline(self.strategy, self.config)
```

**Удаляет:** весь `process_worker.py` (749 строк с `if STRATEGY_STYLE == "SCALP" / "HYBRID" / ...`)

---

### ЭТАП 12: `analyzer.py` — разбить

**Приоритет:** P2 | **Риск:** Высокий | **Зависимости:** Этапы 1, 4, 10

После выноса индикаторов (Этап 1) и collector (Этап 10), `analyzer.py` остаётся тонкой обёрткой:

```python
"""
Analyzer — сборка контекста анализа и сборка промпта.
Индикаторы → indicators/, данные → data/, сигналы → signals/
"""

from src.core.indicators import *
from src.core.data.collector import fetch_htf_prices
from src.core.signals.factory import create_signal_generator
from src.prompts.builder import PromptBuilder

def analyze_symbol(symbol: str, position=None, decision_context: str = "", config: dict = None):
    """
    Тонкая обёртка:
    1. Загрузить данные
    2. Рассчитать индикаторы
    3. Сгенерировать сигнал (если HYBRID/AISCALP)
    4. Собрать контекст
    5. Собрать промпт
    """
    # ... ~200 строк вместо 1231
```

---

### ЭТАП 13: Обратная совместимость

**Приоритет:** P3 | **Риск:** Низкий | **Зависимости:** Все предыдущие

`src/core/__init__.py`:

```python
"""
Обратная совместимость — старые импорты продолжают работать.
"""

# Indicators
from src.core.indicators import (
    calculate_ema, calculate_sma_series, calculate_ema_series,
    calculate_macd, calculate_rsi_series,
    calculate_atr, calculate_bollinger_bands,
    calculate_support_resistance, get_price_value,
    calculate_seb, calculate_seb_series,
)

# Signals
from src.core.signals import (
    BaseSignalGenerator,
    create_signal_generator,
    HybridSignalGenerator,
    AiscalpSignalGenerator,
    MacdxSignalGenerator,
    ScalpSignalGenerator,
)

# Execution
from src.core.execution import (
    create_order, get_open_positions,
    calculate_dynamic_sl_tp, calculate_position_size,
    validate_prediction,
)

# Tracking
from src.core.tracking import TradeTracker, DecisionJournal, PerformanceTracker

# Legacy — оставить на переходный период
from src.core.regime import MarketRegimeDetector, detect_regime
from src.core.session import get_session_info
from src.core.predict import get_prediction, parse_response
from src.core.plotter import plot_symbol
from src.core.lightweight_analyzer import LightweightAnalyzer
```

---

## Метрики: Было → Стало

| Метрика | Было | Стало |
|---------|------|-------|
| Файлов | 23 | ~35 |
| Пакетов | 1 (плоский) | 7 логических |
| God-файлов (>500 строк) | 5 | 0 |
| Макс. размер файла | 1,365 строк | ~300 строк |
| Дублированного кода | ~500 строк в 6 местах | 0 |
| Файлов с глобальным конфигом | 15 | 0 (DI) |
| Генераторов с общим интерфейсом | 0 | 4 (BaseSignalGenerator) |
| Пайплайнов с общим интерфейсом | 0 | 5 (StrategyPipeline) |
| Новая стратегия = правок в core | Править process_worker.py | Добавить 1 класс |

---

## Порядок выполнения

| Этап | Пакет | Приоритет | Сложность | Зависимости |
|------|-------|-----------|-----------|-------------|
| 1 | `indicators/` | P0 | Низкая | Нет |
| 2 | `signals/utils.py` | P0 | Низкая | Этап 1 |
| 3 | `signals/base.py` | P0 | Низкая | Этап 2 |
| 4 | `signals/` генераторы | P1 | Средняя | Этапы 2, 3 |
| 5 | `execution/` | P1 | Средняя | Этап 2 |
| 6 | `tracking/` | P1 | Низкая | Нет |
| 7 | `strategies/scalp/` | P1 | Высокая | Этапы 4, 5 |
| 8 | `strategies/base.py` + пайплайны | P2 | Высокая | Этапы 4-7 |
| 9 | `strategies/grid/` | P2 | Средняя | Нет |
| 10 | `data/` | P2 | Низкая | Нет |
| 11 | `pipeline.py` | P2 | Высокая | Этапы 4-10 |
| 12 | `analyzer.py` refactor | P2 | Высокая | Этапы 1, 4, 10 |
| 13 | Обратная совместимость | P3 | Низкая | Все |

---

## Риски и митигация

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Сломать индикаторы | Низкая | Высокая | Чистые функции, тесты на каждом шаге |
| Сломать генераторы сигналов | Средняя | Высокая | По одному генератору за раз, тесты |
| Сломать SCALP engine | Высокая | Критическая | Выносить по одному классу, тестировать |
| Сломать пайплайн | Высокая | Критическая | Один пайплайн за раз, параллельный запуск |
| Обратная совместимость | Средняя | Средняя | Re-exports в `__init__.py`, постепенная миграция |

---

## Стратегия миграции

1. **Создать новые файлы** рядом со старыми
2. **Написать re-exports** в `src/core/__init__.py`
3. **Тестировать** — старые импорты работают, новые тоже
4. **Мигрировать** вызовы по одному файлу
5. **Удалить** старые файлы когда всё мигрировано

Никаких big-bang коммитов. Каждый этап — независимый, тестируемый, откатываемый.
