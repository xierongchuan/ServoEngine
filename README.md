# OpenProducer — AI-Powered Algorithmic Trading System

![License](https://img.shields.io/badge/license-Proprietary-red.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Status](https://img.shields.io/badge/status-active-success)

**OpenProducer** — профессиональная автоматизированная торговая система для торговли криптовалютными фьючерсами на бирже **BingX** (Standard & VST Futures).

Система использует модели искусственного интеллекта (**Gemini**, **Claude**, **DeepSeek** и другие через **OpenRouter**) для принятия торговых решений, комбинируя детерминированный технический анализ с AI-подтверждением, адаптивным риск-менеджментом и многопроцессной архитектурой.

## Команды

Все операции выполняются в **podman контейнерах** — не запускайте Python/npm напрямую на хосте.

```bash
# Запуск торгового бота
./scripts/run_trading_bot.sh

# Генерация графиков (внутри контейнера или venv)
python3 src/core/plotter.py 2H    # последние 2 часа
python3 src/core/plotter.py 1D    # последний день

# Мониторинг логов (интерактивное меню)
./scripts/monitor_logs.sh

# Telegram Panel
./scripts/start_panel.sh [ngrok|tunnel|prod]  # запуск (выбор режима)
./scripts/stop_panel.sh                       # остановка
./scripts/tunnel.sh start|stop|status|restart # управление Cloudflare туннелем

# Только контейнеры (без скриптов)
podman-compose up --build -d   # запустить
podman-compose down            # остановить
podman-compose logs -f         # логи

# Тесты (pytest не в requirements — устанавливается в контейнере)
podman run --rm -v .:/app:Z -w /app python:3.12-slim \
  sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"
```

---

## <a id="quick-start"></a>Быстрый старт

1. **Клонируйте и настройте env:**
```bash
git clone <repo> && cd OpenProducerBot
cp .env.example .env
# отредактируйте .env: MODE, API ключи, Telegram (опционально)
```

2. **Запустите бота:**
```bash
./scripts/run_trading_bot.sh
```

3. *(Опционально)* **Запустите панель:** `./scripts/start_panel.sh`

---

## <a id="features"></a>Ключевые возможности

## Содержание

1. [Важное предупреждение](#warning)
2. [Ключевые возможности](#features)
3. [Стратегии торговли](#strategies)
4. [Установка и настройка](#installation)
5. [Конфигурация](#configuration)
6. [Архитектура системы](#architecture)
7. [Telegram Panel](#telegram-panel)
8. [Мониторинг и логи](#monitoring)
9. [Устранение неполадок](#troubleshooting)

---

## <a id="warning"></a>Важное предупреждение

> [!CAUTION]
> **Торговля фьючерсами связана с экстремально высоким риском потери капитала.**
>
> Данное программное обеспечение предоставляется **"КАК ЕСТЬ"** в образовательных целях. Автор не несет ответственности за любые финансовые потери, понесенные в результате использования данного бота.
>
> 1. **ВСЕГДА** начинайте с демо-счета (BingX VST Futures).
> 2. **НИКОГДА** не торгуйте на деньги, которые не можете позволить себе потерять.
> 3. **НЕ ОСТАВЛЯЙТЕ** бота без присмотра на реальном счете на длительное время.

---

## <a id="features"></a>Ключевые возможности

### Интеллектуальный анализ
- **Multi-Model AI Core**: Поддержка Gemini, Claude, DeepSeek и других моделей через единый интерфейс OpenRouter.
- **Детерминированные сигналы + AI**: В режиме HYBRID/AISCALP сигналы генерируются математически (scoring system), AI лишь подтверждает или отклоняет.
- **Market Regime Detection**: Автоклассификация рынка (TRENDING / RANGING / VOLATILE / TRANSITIONAL) с адаптацией всех параметров.
- **Multi-Timeframe Analysis**: AISCALP стратегия использует HTF (1H) для определения глобального тренда и сессионную фильтрацию.
- **Smart Sampling**: Сжатие исторических данных для AI-контекста с сохранением экстремумов.

### Высокая производительность
- **True Multiprocessing**: Каждый торговый актив работает в отдельном изолированном процессе ОС.
- **WebSocket Cache**: Опциональный реальном-время кэш свечей через WebSocket с автоматическим fallback на REST.
- **Hot-Reload Config**: `config/active.json` и `config/trading.json` проверяются каждые 30 секунд, изменения применяются без перезапуска.
- **Dynamic Loop**: Частота анализа адаптируется под стиль (1.5s SCALP → 60s AISCALP → 4h SWING).

### Продвинутый риск-менеджмент
- **Dynamic SL/TP**: Расчёт на основе ATR + уровни поддержки/сопротивления + рыночный режим + качество сигнала.
- **Fee-Adjusted R/R**: Валидация risk/reward с учётом комиссий (maker/taker).
- **Dynamic Position Sizing**: Размер позиции от 3% до 20% баланса на основе quality/regime/streak.
- **Performance Tracking**: Отслеживание win rate, PnL, стриков по режимам с автоматическими рекомендациями по калибровке.

### Визуализация
- **Диапазоны графиков**: Конфигурируемые временные интервалы (от 15 минут до 30 дней).
- **Параллельная генерация**: Графики создаются в отдельном процессе (ProcessPoolExecutor).
- **Индикаторы**: Свечи + SMA (10/20/50/100/200) + RSI + SEB + уровни S/R + маркеры позиций и SL/TP.
- **Автообновление**: Chart worker периодически обновляет графики.

### Системные особенности
- **Singleton паттерн**: `MarketRegimeDetector` и `PerformanceTracker` инициализируются один раз на процесс
- **Кэширование на уровне класса**: `BingXClient` имеет кэширование на уровне класса (позиции 5s TTL, баланс 10s TTL)
- **Retry логика**: BingX API использует 3 попытки (1s→2s→4s), LLM — экспоненциальный backoff
- **Hot-reload**: config/active.json и config/trading.json проверяются каждые 30 секунд

---

## <a id="strategies"></a>Стратегии торговли

Настройка активной стратегии: `config/active.json` → `strategy`.

Стратегии реализованы как отдельные конфигурации в `config/strategies/` и промпт-шаблоны в `src/prompts/strategies/`. Полный список доступных стратегий и их вариантов (VETO, REGIME) можно увидеть в `src/prompts/strategies/__init__.py` (REGISTRY `STRATEGIES`).

**Основные типы:**
- **SCALP** — скальпинг (1m), dual-loop движок, трейлинг-стопы, брейкiven.
- **AISCALP** — дейтрейдинг (1m) с multi-TF анализом (1h HTF), сессионной фильтрацией.
- **SWING** — свинг-трейдинг (1h), многодневное удержание, milestone exits.
- **GRID** — сетка лимитных ордеров с управлением инвентарём.
- **HYBRID** — детерминированные сигналы + AI подтверждение.
- **MACDX** — полностью детерминированная MACD crossover стратегия (без AI).

**AI-интеграция** варьируется: некоторые стратегии используют AI для вето (APPROVE/REJECT), другие — для генерации/подтверждения, третьи — без AI. Параметры AI-фильтра (confidence thresholds, auto-approve quality) настраиваются в конфиге стратегии.

**Маркет-режим** (TRENDING, RANGING, VOLATILE, TRANSITIONAL) классифицируется автоматически и влияет на минимальный score, мультипликаторы SL/TP и размер позиции.

---

## <a id="installation"></a>Установка и настройка

### Предварительные требования
- **OS**: Linux (рекомендуется), macOS, Windows (через WSL)
- **Python**: 3.12+
- **Podman** или **Docker**: Для контейнерного запуска (рекомендуется)
- **Аккаунт BingX**: Для торговли (Standard Futures)
- **OpenRouter API key**: Для AI-анализа

> [!IMPORTANT]
> Все операции (запуск, тестирование, сборка) должны использовать **podman** контейнеры. Не запускайте Python или npm команды напрямую на хосте.

### Установка

1. **Клонируйте репозиторий:**
    ```bash
    git clone https://github.com/yourusername/OpenProducerBot.git
    cd OpenProducerBot
    ```

2. **Настройте переменные окружения:**
    ```bash
    cp .env.example .env
    ```
    Заполните `.env`:
    ```ini
    # Режим работы: demo (VST) или real
    MODE=demo
    EXCHANGE=bingx

    # AI API (OpenRouter)
    OPENROUTER_API_KEY=sk-your-key-here

    # BingX API
    BINGX_API_KEY=your_api_key
    BINGX_SECRET_KEY=your_secret_key

    # Telegram Panel (опционально)
    TELEGRAM_BOT_TOKEN=your_bot_token
    TELEGRAM_ADMIN_ID=your_telegram_id
    ```

3. **Запустите бота:**
    ```bash
    ./scripts/run_trading_bot.sh
    ```

    Скрипт автоматически соберёт Docker-образ с зависимостями и запустит бота в контейнере.

### Генерация графиков (опционально)

```bash
python3 src/core/plotter.py 2H    # за 2 часа
python3 src/core/plotter.py 1D    # за 1 день
python3 src/core/plotter.py 1W    # за 1 неделю
```

---

## <a id="configuration"></a>Конфигурация (`config/`)

Система использует модульную структуру конфигурации:

```
config/
  base.json           # Инфраструктура (биржа, AI, чарты) — редко меняется
  trading.json        # Торговые параметры (позиция, риски, фичи)
  strategies/         # Настройки стратегий
    scalp.json, aiscalp.json, swing.json, grid.json, hybrid.json, macdx.json
  profiles/           # Per-symbol переопределения
    default.json, btc_aggressive.json
  active.json         # Активная стратегия + символы + профили
```

### Структура конфигурации

Конфигурация хранится в `config/`:
- `base.json` — инфраструктура (биржа, AI, графики, TA) — редко меняется
- `trading.json` — торговые параметры (позиции, риски, режимы, динамический sizing)
- `strategies/*.json` — настройки стратегий (preset, signal_rules, ai_filter, exit_rules)
- `profiles/*.json` — переопределения для символов с наследованием (`_inherits`) и валидацией (`_strategy`)
- `active.json` — активная стратегия, символы, отключённые символы

**Порядок загрузки:** `.env` → defaults (`src/config.py`) → deep merge `config/` → preset overrides.

**Hot-reload:** `active.json` и `trading.json` проверяются каждые 30 секунд. Остальные файлы требуют перезапуска.

**Config loader:** `src/config_loader.py` реализует слияние, наследование, разрешение конфигурации для конкретного символа (`get_symbol_config(symbol)`).

---

## <a id="architecture"></a>Архитектура системы

**Мультипроцессная архитектура:** Каждый торговый символ работает в отдельном изолированном процессе. Основной процесс (`run.py` → `src/main.py`) создаёт рабочие процессы, Chart Worker и WebSocket Provider.

```
run.py → src/main.py (spawns processes)
  ├── Worker per symbol (src/core/process_worker.py)
  ├── Chart Worker (src/core/chart_worker.py)
  └── WebSocket Provider (src/exchanges/ws_data_provider.py)
```

**Стратегические конвейеры:** `process_worker` выбирает конвейер на основе активной стратегии:
- **SCALP** → `ScalpEngine` (dual-loop: fast 1.5s + slow 45s)
- **HYBRID** → условная AI-вето (auto-approve высококачественные сигналы)
- **AISCALP** → всегда через AI с multi-TF анализом
- **GRID** → `GridWorker` (лимитные ордера)
- **MACDX** → полностью детерминированный, без AI
- Остальные → линейный пайплайн: Collector → Analyzer → SignalGenerator → RiskManager → [AI опционально] → Executor → Monitor

**Ключевые модули:**
- **Ядро (`src/core/`)**: process_worker, collector, analyzer, regime detector, risk manager, trade tracker, decision journal, performance tracker, predict, executor, monitor, plotter, chart_worker, session.Стратегические генераторы сигналов: signal_generator (HYBRID), aiscalp_signal, scalp_engine, scalp_signal, macdx_signal, grid_worker.
- **Биржа (`src/exchanges/`)**: exchange_client, bingx_client (кэширование, ретраи), exchange_factory, ws_data_provider (WebSocket cache, REST backfill, exponential reconnect).
- **Промпты (`src/prompts/`)**: builder (модульная сборка), blocks (текстовые шаблоны), strategies (стратегические промпты, REGISTRY).
- **Панель (`src/telegram_panel/`)**: FastAPI backend + Telegram bot + React frontend. Запускается отдельным контейнером.
- **Утилиты (`src/utils/`)**: logger (per-symbol логирование), helpers, news_api, cleanup_cache.

**Дизайн-паттерны:** Factory, Strategy, Singleton, Template Method, Observer, Adapter.

---

## <a id="telegram-panel"></a>Telegram Panel

Панель управления (отдельный контейнер) для мониторинга и базового управления ботом через Telegram Mini App или веб-браузер.

### Запуск

```bash
# Интерактивный запуск (выбор режима)
./scripts/start_panel.sh

# Или с указанием режима
./scripts/start_panel.sh ngrok     # локальная разработка через ngrok
./scripts/start_panel.sh tunnel    # VPS tunnel (SSH + cloudflared)
./scripts/start_panel.sh prod      # продакшен, URL задан в .env

# Остановка
./scripts/stop_panel.sh
```

### Функции

**Telegram Bot:**
- `/start` — приветствие + кнопка Mini App
- `/status` — стратегия, активные позиции, символы
- `/trades` — история сделок с PnL, ROE%, комиссиями
- `/chart` — последний график
- `/logs` — последние строки логов
- `/config` — сводка конфигурации
- `/help` — все команды
- Админские команды: `/weblink`, `/reload`, `/stop`, `/resume`, `/close`

**Web Dashboard (React Mini App):**
- Dashboard — обзор позиций и стратегии
- Trades — история, фильтры, статистика (win rate, total PnL)
- Charts — галерея PNG графиков (автообновление)
- Logs — просмотр логов в реальном времени
- Journal — журнал AI-решений
- Settings — редактор конфигурации

**Real-time:** WebSocket (`/ws`) рассылает обновления при изменении файлов данных.
**Уведомления:** Автоалерты при открытии/закрытии сделок.
**Аутентификация:** Telegram HMAC initData + веб-токены (6h) для прямого доступа из браузера.

### Настройка

Переменные в `.env`:
```ini
TELEGRAM_BOT_TOKEN=бот_токен
TELEGRAM_ADMIN_ID=ваш_id
TELEGRAM_ALLOWED_IDS=id1,id2,...     # опционально, иначе используется ADMIN_ID
TELEGRAM_PANEL_URL=https://домен      # для prod режима (Mini App кнопка)
PANEL_PORT=8080
```

---

## <a id="monitoring"></a>Мониторинг и логи

### Логи по символам (`data/logs/*.log`)

Каждая торговая пара пишет лог в отдельный файл:

```bash
tail -f data/logs/BTCUSDT.log
```

### Главный лог (`data/steps.log`)

Общий лог запуска, остановки процессов и системных событий.

### Торговый лог (`data/trades.log`)

Сводная информация о совершённых сделках для всех пар.

### Данные (data/)

| Файл/Папка | Описание |
|------------|----------|
| `active_trades.json` | Открытые позиции (с entry regime/score/quality) |
| `trade_history.json` | Закрытые сделки (с net PnL и fees) |
| `decision_journal.json` | История AI-решений, trade plans, cooldowns |
| `calibration_suggestions.json` | Рекомендации по калибровке от PerformanceTracker |
| `prices/{SYMBOL}.json` | Кэш OHLCV свечей |
| `news/{SYMBOL}.json` | Кэш новостей |
| `logs/{SYMBOL}.log` | Логи по каждому символу |
| `charts/` | Сгенерированные PNG графики |
| `steps.log` | Главный лог запуска/остановки процессов |
| `trades.log` | Сводка всех сделок |

### Порядок загрузки конфигурации

Конфигурация загружается в следующем порядке (позже переопределяет ранее):
```
1. .env файл (переменные окружения)
2. hardcoded defaults (src/config.py)
3. config/base.json (инфраструктура)
4. config/trading.json (торговые параметры)
5. config/strategies/{strategy}.json (настройки стратегии)
6. config/profiles/{profile}.json (символьные профили)
7. config/active.json (runtime выбор стратегии и профилей)
```

```bash
# Мониторинг в реальном времени
./scripts/monitor_logs.sh

# Или вручную
tail -f data/logs/BTCUSDT.log   # конкретная пара
tail -f data/trades.log          # все сделки
```

---

## <a id="troubleshooting"></a>Устранение неполадок

### Бот не открывает сделки
1. **Проверьте `MIN_RISK_REWARD_RATIO`**: Сигналы могут отсеиваться из-за плохого R/R. Ищите в логах `[AUTO-FIX: Low R/R]`.
2. **Проверьте `MIN_CONFIDENCE_THRESHOLD`**: AI может давать low-confidence ответы. Текущий порог: 0.55.
3. **Проверьте режим рынка**: В RANGING/VOLATILE режимах min_score повышается (5-7).
4. **Проверьте `DISABLED_SYMBOLS`**: Символ может быть заблокирован.
5. **Проверьте баланс**: На VST счете должны быть средства.

### Ошибка `Signature Validation Failed` (BingX)
- Проверьте `BINGX_API_KEY` и `BINGX_SECRET_KEY` в `.env`.
- Убедитесь, что системное время синхронизировано (NTP).

### Ошибка `AI Provider Error`
- Закончились кредиты на балансе OpenRouter.
- API недоступен или модель перегружена.
- Проверьте `request_timeout` (по умолчанию 60 секунд).
- Настройте `fallback_models` для автоматического переключения.
- Проверьте `reasoning.exclude: true` для reasoning-моделей.

### Бот падает с ошибкой `Singleton instance already exists`
- Перезапустите все процессы: остановите бота и запустите заново
- Убедитесь что нет zombie процессов: `ps aux | grep python`

### Позиции не синхронизируются
- Проверьте баланс счёта через API: `get_balance()`
- Убедитесь что режим (demo/real) соответствует ожидаемому
- Проверьте логи на предмет ошибок авторизации

### Графики не генерируются
- Проверьте что matplotlib установлен в контейнере
- Проверьте права на запись в `data/charts/`
- Убедитесь что `chart_settings.enabled: true` в config/base.json

### Проблемы с Telegram Panel
- Убедитесь что `TELEGRAM_BOT_TOKEN` задан в `.env`.
- Проверьте что `TELEGRAM_ADMIN_ID` или `TELEGRAM_ALLOWED_IDS` настроены.
- Логи: `podman-compose logs -f`

---

## Contributing

Мы приветствуем Pull Requests!

1. Форкните проект
2. Создайте ветку (`git checkout -b feature/AmazingFeature`)
3. Закоммитьте изменения (`git commit -m 'feat: add AmazingFeature'`)
4. Запушьте ветку (`git push origin feature/AmazingFeature`)
5. Откройте Pull Request

**Commit style**: `feat:`, `fix:`, `test:`, `chore:`, `refactor:`

---

## Лицензия

**Strict Proprietary Software License.** Все права защищены.

Данное программное обеспечение является собственностью [https://github.com/xierongchuan](https://github.com/xierongchuan). Использование, копирование, модификация или распространение без письменного разрешения правообладателя запрещено.

См. файл [LICENSE.md](LICENSE.md) для получения полной информации.
