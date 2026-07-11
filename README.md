# Servo Engine — AI-Powered Algorithmic Trading System

![License](https://img.shields.io/badge/license-Proprietary-red.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Status](https://img.shields.io/badge/status-active-success)

**Servo Engine** — автоматизированная торговая система для **BingX Perpetual** и **MEXC USDT‑M Perpetual**, а также REST-интеграция **MEXC Spot**.

Система использует модели искусственного интеллекта (**Gemini**, **Claude**, **DeepSeek** и другие через **OpenRouter**) для принятия торговых решений, комбинируя детерминированный технический анализ с AI-подтверждением, адаптивным риск-менеджментом и многопроцессной архитектурой.

---

## Содержание

1. [Важное предупреждение](#warning)
2. [Быстрый старт](#quick-start)
3. [Ключевые возможности](#features)
4. [Стратегии торговли](#strategies)
5. [Установка и настройка](#installation)
6. [Конфигурация](#configuration)
7. [Архитектура системы](#architecture)
8. [Telegram Panel](#telegram-panel)
9. [Мониторинг и логи](#monitoring)
10. [Устранение неполадок](#troubleshooting)

---

## <a id="warning"></a>Важное предупреждение

> [!CAUTION]
> **Торговля фьючерсами связана с экстремально высоким риском потери капитала.**
>
> Данное программное обеспечение предоставляется **"КАК ЕСТЬ"** в образовательных целях. Автор не несет ответственности за любые финансовые потери, понесенные в результате использования данного бота.
>
> 1. **ВСЕГДА** начинайте с демо-счета BingX либо MEXC в read-only режиме. У MEXC нет API sandbox.
> 2. **НИКОГДА** не торгуйте на деньги, которые не можете позволить себе потерять.
> 3. **НЕ ОСТАВЛЯЙТЕ** бота без присмотра на реальном счете на длительное время.

---

## <a id="quick-start"></a>Быстрый старт

### 1. Клонирование и настройка

```bash
git clone <repo> && cd ServoEngine
cp .env.example .env
```

Отредактируйте `.env`:

| Переменная | Описание |
|------------|----------|
| `MODE` | `demo` (VST) или `real` |
| `EXCHANGE` | `bingx` или `mexc` |
| `MARKET_TYPE` | `perpetual` или `spot` (для BingX всегда `perpetual`) |
| `OPENROUTER_API_KEY` | API ключ OpenRouter |
| `BINGX_API_KEY` | API ключ BingX |
| `BINGX_SECRET_KEY` | Секретный ключ BingX |
| `MEXC_API_KEY` / `MEXC_SECRET_KEY` | Ключи MEXC без withdrawal permission |
| `MEXC_ENABLE_LIVE_TRADING` | Явный предохранитель реальных MEXC-мутаций; default `false` |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота (опционально) |
| `TELEGRAM_ADMIN_ID` | Ваш Telegram ID (опционально) |

### 2. Запуск бота

```bash
./scripts/run_trading_bot.sh
```

### 3. Telegram Panel (опционально)

```bash
./scripts/start_panel.sh [ngrok|tunnel|prod]
./scripts/restart_panel.sh [ngrok|prod]  # Рестарт с обязательной пересборкой
```

---

## <a id="features"></a>Ключевые возможности

### Интеллектуальный анализ

- **Multi-Model AI Core**: Поддержка Gemini, Claude, DeepSeek и других моделей через единый интерфейс OpenRouter.
- **Детерминированные сигналы + AI**: В режиме HYBRID/AISCALP сигналы генерируются математически (scoring system), AI лишь подтверждает или отклоняет.
- **Market Regime Detection**: Автоклассификация рынка (TRENDING / RANGING / VOLATILE / TRANSITIONAL) с адаптацией всех параметров.
- **Multi-Strategy Runtime**: Один символ может иметь несколько strategy instances с разными стратегиями и профилями.
- **Position Ownership Lock**: Если один instance открыл позицию по символу, остальные instances этого символа ждут закрытия позиции и не открывают конкурирующие сделки.
- **Multi-Timeframe Analysis**: AISCALP стратегия использует HTF (1H) для определения глобального тренда и сессионную фильтрацию.
- **Smart Sampling**: Сжатие исторических данных для AI-контекста с сохранением экстремумов.

### Высокая производительность

- **True Multiprocessing**: Каждый enabled strategy instance работает в отдельном изолированном процессе ОС.
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

---

## <a id="strategies"></a>Стратегии торговли

Активные торговые запуски задаются в `config/active.json` через `strategy_instances`.
Каждый instance содержит `id`, `symbol`, `strategy`, `profile` и `enabled`.

Стратегии реализованы как отдельные конфигурации в `config/strategies/` и промпт-шаблоны в `src/prompts/strategies/`.

### Доступные стратегии

| Стратегия | Таймфрейм | AI | Описание |
|-----------|-----------|-----|----------|
| **SCALP** | 1m | Optional | Скальпинг, dual-loop движок, трейлинг-стопы |
| **SCALP_VETO** | 1m | Veto | SCALP с AI-вето на сигналы |
| **SCALP_REGIME** | 1m | Regime | SCALP с адаптацией под рыночный режим |
| **AISCALP** | 1m + HTF | Full | Дейтрейдинг с multi-TF анализом |
| **SWING** | 1h | Optional | Свинг-трейдинг, многодневное удержание |
| **SWING_VETO** | 1h | Veto | SWING с AI-вето |
| **GRID** | 1m | Optional | Сетка лимитных ордеров |
| **HYBRID** | 5m | Yes | Детерминированные сигналы + AI подтверждение |
| **HYBRID_VETO** | 5m | Veto | HYBRID с более строгим AI-вето |
| **MACDX** | 15m | No | MACD crossover, полностью детерминированный |

### Маркет-режим

Автоматическая классификация (TRENDING, RANGING, VOLATILE, TRANSITIONAL) влияет на:
- Минимальный score для открытия сделки
- Мультипликаторы SL/TP
- Размер позиции

### Одновременные стратегии на одном символе

Пример: `BTCUSDT` может одновременно запускать `MACDX` и `HYBRID`, а `ETHUSDT` — один или несколько других instances.
При этом открытая позиция по символу имеет владельца (`strategy_instance_id`). Пока позиция открыта, остальные instances этого символа не открывают новую позицию. Состояние владения хранится в `data/position_owners.json` и сверяется с реальными позициями биржи.

---

## <a id="installation"></a>Установка и настройка

### Предварительные требования

- **OS**: Linux (рекомендуется), macOS, Windows (через WSL)
- **Python**: 3.12+
- **Podman**: Для контейнерного запуска, сборки и тестов
- **Аккаунт BingX** либо прошедший KYC аккаунт MEXC для Futures
- **OpenRouter API key**: Для AI-анализа

> [!IMPORTANT]
> Все операции (запуск, тестирование, сборка) должны использовать **podman** контейнеры. Не запускайте Python или npm команды напрямую на хосте.

### Структура проекта

```
config/
├── base.json           # Инфраструктура (биржа, AI, чарты)
├── trading.json        # Торговые параметры (позиция, риски)
├── strategies/         # Настройки стратегий
│   ├── scalp.json, aiscalp.json, swing.json
│   ├── grid.json, hybrid.json, macdx.json
├── profiles/           # Профили переопределений для strategy instances
│   ├── default.json
└── active.json         # Runtime strategy instances + legacy fallback

src/
├── core/               # Ядро: worker, collector, analyzer, risk manager
├── exchanges/          # BingX, MEXC Spot/Futures, REST/WS providers
├── prompts/            # Промпты: builder, strategies
├── telegram_panel/     # FastAPI + React панель
└── utils/              # Logger, helpers
```

### Команды управления

```bash
# Запуск
./scripts/run_trading_bot.sh              # Торговый бот
./scripts/start_panel.sh [ngrok|tunnel|prod]  # Telegram Panel
./scripts/restart_panel.sh [ngrok|prod]   # Рестарт панели с обязательным build
./scripts/stop_panel.sh                   # Остановка панели
./scripts/tunnel.sh start|stop|status      # Cloudflare туннель
./scripts/monitor_logs.sh                  # Интерактивный мониторинг логов

# Контейнеры напрямую
podman-compose up --build -d               # Запуск
podman-compose down                         # Остановка
podman-compose logs -f                     # Логи

# Важно: не используйте podman-compose restart для панели —
# он не делает build и может оставить старую frontend-сборку.
# Рестарт панели: ./scripts/restart_panel.sh [ngrok|prod]

# Генерация графиков через podman
podman run --rm -v .:/app:Z -w /app python:3.12-slim \
  sh -c "pip install -q requests pandas matplotlib && python src/core/plotter.py 2H"

# Тесты
podman run --rm -v .:/app:Z -w /app python:3.12-slim \
  sh -c "pip install -q requests pandas matplotlib pytest && python -m pytest tests/ -x -q"
```

`run.py` по умолчанию запускает runtime supervisor. Он остаётся живым внутри контейнера
trading bot и управляет дочерним торговым runtime через команды `start`, `stop`,
`restart` из Telegram Panel. Панель не запускает Podman и не убивает контейнеры:
она пишет команду в `data/runtime_command.json`, а код trading bot выполняет её и
публикует состояние в `data/runtime_status.json`.

Для старого прямого запуска без supervisor:

```bash
SERVO_RUNTIME_SUPERVISOR=0 ./scripts/run_trading_bot.sh
```

---

## <a id="configuration"></a>Конфигурация

> [!TIP]
> Подробная документация по схеме конфигурации: [config/schema.md](config/schema.md)

### Порядок загрузки

Для каждого enabled strategy instance конфигурация собирается в следующем порядке (позже переопределяет ранее):

```
1. .env файл (переменные окружения)
2. hardcoded defaults (src/config.py)
3. config/base.json (инфраструктура)
4. config/trading.json (торговые параметры)
5. config/active.json (выбор instance: symbol + strategy + profile)
6. config/strategies/{instance.strategy}.json (настройки стратегии)
7. config/profiles/{instance.profile}.json (переопределения профиля)
```

### Hot-reload

`active.json` и `trading.json` проверяются каждые 30 секунд. Изменения применяются без перезапуска. Остальные файлы требуют перезапуска бота.

### MEXC

Один процесс обслуживает только одну пару `EXCHANGE` + `MARKET_TYPE`; её смена требует restart. Для полной автоторговли используйте `EXCHANGE=mexc`, `MARKET_TYPE=perpetual`, `MODE=real` и только после read-only проверки включите `MEXC_ENABLE_LIVE_TRADING=true`. Поддерживаются USDT‑M perpetual, hedge/one-way и isolated/cross.

MEXC Spot предоставляет market data, asset balances, комиссии и Market/Limit/test REST-ордера через `MEXCSpotClient`, но намеренно не подключён к futures strategy pipeline: Spot-баланс не считается позицией бота, short/leverage/funding/TP-SL запрещены. Для ключа включайте только нужные account/order permissions, отключите withdrawal и задайте IP whitelist. Допустимые пары берутся из `config/active.json`, например:

```json
{"symbols": {"bingx": ["BTCUSDT"], "mexc": {"perpetual": ["BTCUSDT"], "spot": ["BTCUSDT"]}}}
```

### Config loader

`src/config_loader.py` реализует:
- Deep merge конфигов
- Наследование профилей (`_inherits`)
- Валидацию (`_strategy`)
- Разрешение конфигурации для конкретного strategy instance (`resolve_strategy_instance_config(instance)`)
- Legacy fallback из старой схемы `strategy` + `symbols`

---

## <a id="architecture"></a>Архитектура системы

### Мультипроцессная архитектура

Каждый enabled strategy instance работает в отдельном изолированном процессе. Несколько instances могут использовать один символ, но позиция по символу имеет единственного владельца:

```
run.py → src/main.py (spawns processes)
  ├── Worker per strategy instance (src/core/process_worker.py)
  ├── Chart Worker (src/core/chart_worker.py)
  └── WebSocket Provider (src/exchanges/ws_data_provider.py)
```

### Стратегические конвейеры

`process_worker` выбирает конвейер на основе стратегии конкретного instance:

- **SCALP** → `ScalpEngine` (dual-loop: fast 1.5s + slow 45s)
- **HYBRID** → условная AI-вето (auto-approve высококачественные сигналы)
- **AISCALP** → всегда через AI с multi-TF анализом
- **GRID** → `GridWorker` (лимитные ордера)
- **MACDX** → полностью детерминированный, без AI
- Остальные → линейный пайплайн

### Ключевые модули

| Директория | Назначение |
|------------|------------|
| `src/core/` | process_worker, collector, analyzer, regime detector, risk manager, executor |
| `src/runtime.py` | модель `StrategyInstance` и legacy-конвертация runtime config |
| `src/core/position_ownership.py` | владение открытой позицией между instances одного символа |
| `src/exchanges/` | bingx_client (кэширование, retry), ws_data_provider (WebSocket) |
| `src/prompts/` | builder, blocks, strategies (REGISTRY) |
| `src/telegram_panel/` | FastAPI backend + Telegram bot + React frontend |
| `src/utils/` | logger, helpers, news_api |

### Дизайн-паттерны

Factory, Strategy, Singleton, Template Method, Observer, Adapter.

---

## <a id="telegram-panel"></a>Telegram Panel

Панель управления (отдельный контейнер) для мониторинга и базового управления ботом.

### Запуск

```bash
./scripts/start_panel.sh              # Интерактивный выбор режима
./scripts/start_panel.sh ngrok        # Локальная разработка через ngrok
./scripts/start_panel.sh tunnel       # VPS tunnel (SSH + cloudflared)
./scripts/start_panel.sh prod         # Продакшен (URL в .env)
./scripts/restart_panel.sh prod       # Рестарт с обязательной пересборкой
```

### Команды Telegram бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие + кнопка Mini App |
| `/status` | Strategy instances, позиции, символы |
| `/trades` | История сделок с PnL |
| `/chart` | Последний график |
| `/logs` | Последние строки логов |
| `/config` | Сводка конфигурации |
| `/help` | Справка по командам |

**Админские:** `/weblink`, `/reload`, `/stop`, `/resume`, `/close`

### Web Dashboard (Mini App)

- **Dashboard** — обзор позиций, символов и активных instances
- **Trades** — история, фильтры, статистика
- **Charts** — галерея PNG графиков
- **Logs** — просмотр логов в реальном времени
- **Journal** — журнал AI-решений
- **Settings** — Runtime control, Runtime instances, Position & Risk, Profiles, AI Settings

В `Settings → Runtime` доступны кнопки `Старт`, `Стоп`, `Рестарт` для торгового
runtime. `Стоп` останавливает торговые worker-процессы, но оставляет supervisor
живым, чтобы следующий `Старт` можно было выполнить из панели без терминала.

### Настройка

```ini
TELEGRAM_BOT_TOKEN=бот_токен
TELEGRAM_ADMIN_ID=ваш_id
TELEGRAM_ALLOWED_IDS=id1,id2,...     # опционально
TELEGRAM_PANEL_URL=https://домен      # для prod режима
PANEL_PORT=8080
```

**Аутентификация:** Telegram HMAC initData + веб-токены (6h).

---

## <a id="monitoring"></a>Мониторинг и логи

### Структура данных (`data/`)

| Файл/Папка | Описание |
|------------|----------|
| `active_trades.json` | Открытые позиции (с entry regime/score/quality) |
| `position_owners.json` | Владелец открытой позиции по символу (`strategy_instance_id`) |
| `trade_history.json` | Закрытые сделки (с net PnL и fees) |
| `decision_journal.json` | История AI-решений, trade plans, cooldowns |
| `calibration_suggestions.json` | Рекомендации от PerformanceTracker |
| `prices/{SYMBOL}.json` | Кэш OHLCV свечей |
| `news/{SYMBOL}.json` | Кэш новостей |
| `logs/{SYMBOL}.log` | Логи по каждому символу |
| `charts/` | Сгенерированные PNG графики |
| `steps.log` | Главный лог запуска/остановки процессов |
| `trades.log` | Сводка всех сделок |

### Просмотр логов

```bash
# Интерактивный мониторинг
./scripts/monitor_logs.sh

# Вручную
tail -f data/logs/BTCUSDT.log   # конкретная пара
tail -f data/trades.log          # все сделки
tail -f data/steps.log           # системные события
```

---

## <a id="troubleshooting"></a>Устранение неполадок

### Бот не открывает сделки

1. **Проверьте `MIN_RISK_REWARD_RATIO`**: Сигналы могут отсеиваться. Ищите `[AUTO-FIX: Low R/R]` в логах.
2. **Проверьте `MIN_CONFIDENCE_THRESHOLD`**: AI может давать low-confidence ответы (по умолчанию 0.55).
3. **Проверьте режим рынка**: В RANGING/VOLATILE режимах min_score повышается (5-7).
4. **Проверьте `disabled_symbols` и `enabled` у instance**: Символ или конкретный strategy instance может быть выключен.
5. **Проверьте баланс**: На VST счете должны быть средства.
6. **Проверьте ownership**: Если другая стратегия уже открыла позицию по этому символу, остальные instances ждут её закрытия.

### Signature Validation Failed (BingX)

- Проверьте `BINGX_API_KEY` и `BINGX_SECRET_KEY` в `.env`.
- Убедитесь, что системное время синхронизировано (NTP).

### AI Provider Error

- Закончились кредиты на балансе OpenRouter.
- API недоступен или модель перегружена.
- Проверьте `request_timeout` (по умолчанию 60 секунд).
- Настройте `fallback_models` для автоматического переключения.
- Проверьте `reasoning.exclude: true` для reasoning-моделей.

### Singleton instance already exists

- Перезапустите все процессы: остановите бота и запустите заново.
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
