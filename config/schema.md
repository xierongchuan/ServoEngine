# Configuration Schema Documentation

## Overview

The configuration system is organized into the following categories:

### Directory Structure
```
config/
  base.json           # Global defaults and rarely-changed settings
  trading.json        # Core trading parameters (changed occasionally)
  strategies/         # Strategy-specific settings
    scalp.json
    aiscalp.json
    swing.json
    grid.json
    hybrid.json
    macdx.json
  profiles/           # Per-instance profiles (overrides)
    _templates/       # Profile templates (optional)
    default.json      # Default profile (works with any strategy)
    btc_aggressive.json
    eth_conservative.json
  active.json         # Runtime strategy instances + legacy fallback
```

## Configuration Resolution Order

`config/active.json` selects the enabled runtime instances. For each enabled `StrategyInstance`, configuration is resolved in the following order (later overrides earlier):
```
instance = active.strategy_instances[]

effective_config = deep_merge(
    base.json,           # 1. Infrastructure defaults
    trading.json,        # 2. Trading defaults
    strategies/{instance.strategy}.json,  # 3. Strategy-specific settings
    profiles/{instance.profile}.json      # 4. Instance-specific overrides
)
```

The legacy `strategy` + `symbols` format is converted to strategy instances internally and remains supported as a fallback.

## Единое исполнение (`trading.json → execution`)

`src.trading_settings.get_trading_settings()` возвращает отдельную копию проверенного снимка на процесс. Он не принимает operational env overrides и не обновляется при `reload_config()`/`clear_config_cache()`. Профили и стратегии не могут переопределить execution. Для другого счёта/endpoint нужен новый runtime, а не hot-reload.

```json
{
  "execution": {
    "account_id": "default",
    "exchange": "bingx",
    "market_type": "perpetual",
    "execution_mode": "read_only",
    "margin_mode": "isolated",
    "position_mode": "hedge",
    "settle_asset": "USDT",
    "grid_mode": "hedged",
    "spot": {
      "quote_budget_usdt": 0,
      "allow_local_protection": false
    },
    "protection": {
      "trigger_price": "mark",
      "failure_policy": "pause",
      "verification_timeout_seconds": 10,
      "recovery_attempts": 3
    },
    "market_data": {
      "max_age_seconds": 30,
      "reconcile_interval_seconds": 60,
      "history_limit": 500
    }
  }
}
```

| Поле | Значения / границы |
|---|---|
| account_id | Default `default`; 1–64 символа, regex `[a-z0-9][a-z0-9_-]{0,63}` (полное совпадение) |
| exchange | bingx, mexc |
| market_type | perpetual; spot только MEXC |
| execution_mode | read_only (default), demo, live |
| margin_mode | Только isolated; Cross отклоняется |
| position_mode | hedge, one_way; Spot — только one_way |
| settle_asset | Только USDT |
| grid_mode | inventory, hedged; hedged требует perpetual + hedge. Новые Perpetual GRID entries заблокированы независимо от режима |
| spot.quote_budget_usdt | Неотрицательное конечное число, default 0; **TOTAL бюджет аккаунта**, не каждого instance |
| spot.allow_local_protection | Строго bool, default false; явное принятие риска только локальной защиты |
| protection.trigger_price | last, mark; Spot — только last |
| protection.failure_policy | close, pause |
| protection.verification_timeout_seconds | Целое 1–300 |
| protection.recovery_attempts | Целое 0–10 |
| market_data.max_age_seconds | Целое 1–300, default 30 |
| market_data.reconcile_interval_seconds | Целое 1–3600, default 60 |
| market_data.history_limit | Целое 50–2000, default 500 |

Неполный execution дополняется безопасными defaults; неизвестные поля, секреты, bool вместо числа и неверные типы отклоняются. Повреждённый JSON не превращается в live или молчаливый fallback. BingX demo использует VST. MEXC demo нормализуется в read_only, `is_demo=False` честно отражает production endpoint; `live_trading_enabled=False` запрещает мутации. `MODE` сохраняется для legacy: live → real, demo → demo, read_only → read_only.

### Spot-бюджет и стабильный аккаунт

`execution_account_label(settings)` возвращает `<account_id>:<exchange>:<execution_mode>`, например `default:mexc:live`. Передавайте этот label в coordinator/Spot adapter явно как `account_id=`; он не содержит ключей, их хешей или секретов. Ротация ключей не меняет namespace; идентификатор нельзя автоматически пересоздавать при обновлении credentials. Другая физическая учётная запись требует остановки, сверки и отдельного явного ID. Instance/profile не вправе переопределять `ACCOUNT_ID` или `EXECUTION_ACCOUNT_ID`.

`spot.quote_budget_usdt` принимает конечные неотрицательные int/float/Decimal, но не bool, строки, NaN или Infinity. В проверенном dict остаётся JSON number: целый Decimal сохраняется как int, дробный — как конечный float без переполнения/обнуления. Это **общий выделенный quote-бюджет аккаунта в USDT**, не весь баланс кошелька и не бюджет каждого worker. Factory передаёт один TOTAL в общий account quote-pool Spot ledger; адаптер под SQLite-транзакцией выделяет свободные средства владельцу перед BUY, учитывая account-wide резервы. Свободный, не зарезервированный owner cash может передаваться другому instance; собственные лоты не передаются и ручные coins не импортируются. Нельзя создать отдельный полный TOTAL каждому worker. Переход с legacy per-instance funding при существующих средствах/лотах/командах и изменение уже выделенного общего бюджета требуют явной сверки/funding ledger, не удаления состояния.

Live Spot с бюджетом 0 и/или `allow_local_protection=false` **можно сохранить**. `validate_trading_start(settings)` отдельно запрещает старт, пока бюджет не станет > 0 и оператор явно не примет риск. Supervisor проверяет это до spawn; прямой main повторяет gate. Factory передаёт адаптеру общий бюджет и согласие, а исполнение должно передать настоящий SL до BUY. Локальный supervisor защиты должен быть работоспособен. Биржевых SL/TP у Spot нет: `local_only` / `local_registration` не означают подтверждение ордера на бирже. При остановке процесса/сети лоты остаются без защиты. MEXC Spot read_only использует публичный **protobuf WebSocket с REST backfill/reconciliation**; TF без WS поддерживаются точным публичным REST polling. Private precheck и торговые ордера в read_only не выполняются.

### API панели

- `GET /api/config/execution` → сохранённый проверенный dict (не snapshot процесса панели).
- `PUT /api/config/execution` → частичный или полный dict, без оболочки `execution`; ответ `{status, execution, needs_restart}`. Требует существующей авторизации панели.
- Валидация и read-modify-write выполняются под общим с supervisor `config/.trading.lock`; JSON заменяется атомарно. Supervisor удерживает lock от публикации starting до refresh, применения globals и spawn. PUT, начавшийся первым, завершается до чтения нового runtime; PUT, пришедший после start, видит starting/running и не меняет выбранный счёт. Невалидные данные: 422; небезопасная смена: 409. `/api/config/trading` применяет ту же проверку к execution, поэтому обход через общий редактор невозможен.
- **Любое изменение execution**, включая `account_id`, `spot`, `protection` и `market_data`, требует свежего статуса stopped и отсутствия pending start/restart. При активном runtime изменённый PUT получает 409; идемпотентное сохранение тех же значений допускается. Нет отложенного редактирования отдельных execution-полей во время торговли: **весь execution применяется только при новом запуске**.
- Управляемое состояние тоже блокирует изменение. Проверяются legacy JSON (`active_trades.json`, `position_owners.json`, `managed_state*.json`, `grid_state*.json`, `pending_orders*.json`, `unknown_orders*.json`, `order_journal*.json`, `execution_journal*.json`), реальные `data/grid/*.json` и SQLite-файлы `*.db`, `*.sqlite`, `*.sqlite3` внутри DATA_DIR (в том числе вложенные ledger с произвольными именами). `managed_state_block_reason()` / `has_managed_state()` проверяют **все аккаунты**, не только новый account label.
- Основной `execution.sqlite3` читается без конструктора журнала и миграций, в read-only транзакции с видимостью WAL. Схема v1: intents/wires в `PREPARED/ACK/UNKNOWN/PARTIAL` или неизвестном состоянии, положительное ownership.quantity, любые protection/blocks запрещают смену. Количества TEXT проверяются Decimal, не SQL CAST REAL. Терминальные intents/wires и история events, ownership=0 и инициализированная чистая БД не блокируют смену. Неизвестные версии/схемы, некорректные количества, повреждение или ошибка чтения блокируют её.
- Spot ledger: нетерминальные commands (терминальные — `FILLED/CANCELED/REJECTED/EXPIRED`), lots.qty > 0, pauses и оставшийся выделенный owners.cash > 0 блокируют смену. Ручные coins из wallets не импортируются в ownership. GRID v1 блокирует pending/неизвестные команды, owned_positions, ненулевой inventory и paused recovery; чистый реестр с терминальной историей допускается. Неизвестное состояние проверяется консервативно, не удаляйте journal/ledger/registry для обхода блокировок.
- API не закрывает позиции, не меняет режим реального счёта и не запускает runtime автоматически. Отсутствующий/stale runtime status тоже запрещает изменение.
- Новости остаются в `trading.features.enable_news` и поддерживают hot-reload. В env остаются секреты и настройки инфраструктуры, но не торговые переключатели.

### Текущая интеграция main/factory

**Perpetual GRID — fail-closed:** новые открывающие LIMIT запрещены до HTTP/PREPARED, пока не реализован безопасный lifecycle биржевого SL/TP при частичных fills и изменении объёма. `grid_mode` сохраняет выбор, но не включает такие входы. Существующее управляемое состояние можно сверять; отмена адресная и owned-only, уменьшение объёма сохраняет ownership/UNKNOWN/защитные проверки. Локальный emergency не гарантирует биржевую защиту. Legacy LIMIT могут исполниться; не удаляйте реестр и проверяйте такие заявки отдельно. Подробности: [CONTRACTS.md](../src/core/strategies/grid/CONTRACTS.md).

Отказ запуска market-data owner прерывает runtime до workers. При работающем owner обрыв WS вызывает backfill/reconciliation; это не тот же сценарий, что неудачный startup. Settings и подписки обновляются при фактическом изменении, а не принудительно каждые 30 секунд.

Передавать один snapshot в `ConfigFactory.create(..., settings=settings)` и `get_trading_client(settings=settings)`; эти аргументы необязательны для совместимости. Новому runtime нужно сверить позиции, активные ордера и режим реального счёта до запуска, не переключать account mode автоматически. Supervisor перед каждым новым стартом, когда нет живого runtime, вызывает явный `refresh_trading_settings(path)` и `prepare_runtime_config(settings)`. Parent globals обновляются до spawn. Child получает проверенный dict аргументом и устанавливает тот же снимок до импорта main, без повторного чтения execution. По умолчанию используется multiprocessing spawn, чтобы не наследовать старые clients/потоки/from-import globals. `reset_trading_settings()` и refresh предназначены только для границы остановленного runtime, не для hot-reload. Legacy BingX с отличающимся явным snapshot отклоняется: используйте новый клиент.

Main проверяет `validate_trading_start(settings)` и в `read_only` пропускает private precheck; торговые мутации закрыты read-only facade. Публичный data runtime обслуживает BingX/MEXC Perpetual и MEXC Spot через WS и REST, workers получают shared snapshots. Live Spot требует положительного общего quote-pool, явного согласия на локальную защиту и SL до BUY. Supervisor сам private precheck не вызывает и не превращает read_only в demo/live.

`get_trading_client()` уже подключает ExecutionCoordinator для Perpetual и SpotTradingAdapter для MEXC Spot, передаёт проверенные settings, account label, owner ID и общий durable путь, запускает Spot supervisor. `get_market_data_client()` остаётся отдельным read-only reader без coordinator/Spot ledger. `protection`, `market_data`, `grid_mode` передаются соответствующим исполнителям/providers; unsupported capabilities должны отклоняться, а не превращаться в биржевое подтверждение. Эта интеграция и offline-тесты не являются live-верификацией защиты или восстановления на реальном счёте. Локальные файлы не доказывают отсутствие биржевых ордеров; требуется exchange reconciliation в main и сохранение незавершённого управляемого состояния. Запуск и PUT execution сериализованы общим lock при общем mount config/. При ручной правке JSON supervisor повторно проверяет смену относительно предыдущего снимка в runtime_status и блокирует её при управляемом состоянии. Повторный старт того же снимка допускается для reconciliation. Не удаляйте состояние ради обхода guard. Символьный start/restart получает snapshot и runtime_pid работающего parent, а не отложенные настройки из файла; чужой снимок/PID при чтении команды отклоняется. `apply_legacy_runtime_config()` запрещает operational overrides из STRATEGY_INSTANCE. Если stop/kill не завершил процесс, статус остаётся stopping с PID; новый runtime не создаётся.

### Журнал логов: API и контекст

Логирование не меняет `execution` и не требует новых секретов. Main/worker вызывают `configure_logging(data_dir=...)`, затем `set_log_context(service=..., exchange=..., market_type=..., execution_mode=..., symbol=..., instance_id=...)` **до создания клиентов**. Для временного request/order контекста используется `log_context(...)`; aliases `instance`, `request`, `order` соответствуют `*_id`. После настройки root/Uvicorn handlers вызывается `protect_logging_handlers()` для очистки formatted console/traceback. Root и project namespaces захватываются без повторной записи одного LogRecord; capture не создаёт отсутствующие в клиенте REST INFO-сообщения.

- `GET /api/logs/events`: `level`, `category` (CSV), `exchange`, `symbol`, `instance`, `search`, `order`, `request`, `source`, `time_from`, `time_to`, `cursor`, `limit` (1–200, default 100). Время — ISO 8601 с timezone; границы включительные. Search — literal Unicode casefold substring, не SQL/regex. `order/request/instance` фильтруют соответствующие `*_id`.
- Ответ `{events, next_cursor, live_cursor, has_more, watermark}`. Первая страница содержит последние события в возрастающем sequence; `next_cursor` загружает более раннюю историю того же snapshot, `live_cursor` — новые события после него. Для live catch-up используйте новый `live_cursor`, пока `has_more=true`. Не заменяйте live cursor курсором истории. Курсор привязан к хранилищу и фильтрам; при смене фильтров начните без cursor. IDs постоянны при повторном чтении.
- `GET /api/logs/sources` → `{categories, levels, sources, exchanges, symbols, instances, legacy_sources, watermark}`. Categories/levels — допустимые значения, остальные списки — наблюдавшийся контекст; `legacy_sources` отдельно содержит `{source, filename}`. Символы нормализованы, например `BTC-USDT` → `BTCUSDT`.
- Категории: `runtime/exchange/market_data/strategy/llm/risk/order/trade/config/panel`. Явная `category` имеет приоритет над выводом из logger namespace. Events содержат UTC timestamp, UUID `event_id`, PID/run_id, category/level/logger/message и доступный контекст; exception очищается после форматирования.
- `GET /api/logs/system?lines=...`, `GET /api/logs/{symbol}?lines=...` сохраняют text-tail контракт `{source, lines}` (1–1000 строк). Structured API не смешивает text/JSONL-копии с SQLite-событиями. HTTP endpoints требуют panel auth и возвращают `Cache-Control: no-store`; 401/403 — доступ, 409 — cursor, 422 — фильтры, 503 — временная ошибка/ограничение чтения. Запросы выполняются вне event loop с deadline и ограничением размера страницы.
- WS `log_events` — coalesced уведомление о необходимости перечитать live cursor; не доставка каждого события. Disconnect/pause догоняется через cursor API. Очереди и send timeout ограничены; trailing debounce не теряет последний сигнал.

Хранилище — `<DATA_DIR>/logs/events.sqlite3` (SQLite/WAL); копии — per-process JSONL, сегменты default 8 MiB без shared rotating handler. Text append защищён межпроцессным lock. Retention по умолчанию **ничего не удаляет**, старые файлы не переписываются и не импортируются автоматически. При аварии SQLite очищенное событие может остаться только в JSONL; автоматического replay в API нет. Redaction новых событий не удаляет ранее записанные секреты из старых файлов: архивы требуют отдельного контроля доступа. Не включайте credentials в фильтры/URL и не используйте лог-журнал вместо durable execution journal.

При миграции удаляйте из deployment `.env` только перечисленные выше operational overrides; API keys, Telegram/OpenRouter secrets и инфраструктурные переменные сохраняются, файл остаётся с правами 600. Шаблон `.env.example` не заменяет рабочие секреты.

## Конфигурация ИИ (`base.json → ai`)

Каноническая секция объединяется с AI-overrides стратегий/профилей через deep merge. Секреты, Authorization, API-токены и пароли в JSON запрещены; ключи читаются только из окружения:

| Провайдер | Ключ | Default base_url | Default модель |
|---|---|---|---|
| `openrouter` | `OPENROUTER_API_KEY`, обязателен | `https://openrouter.ai/api/v1` | `google/gemini-3.1-flash-lite-preview` |
| `deepseek` | `DEEPSEEK_API_KEY`, обязателен | `https://api.deepseek.com` | `deepseek-flash` |
| `lmstudio` | `LM_STUDIO_API_KEY`, необязателен по контракту сервера | `http://host.containers.internal:1234/v1` | Пусто; при выборе провайдера нужен ID |

```json
{
  "ai": {
    "provider": "openrouter",
    "providers": {
      "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "google/gemini-3.1-flash-lite-preview"},
      "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "thinking": {"enabled": false, "effort": "high"}},
      "lmstudio": {"base_url": "http://host.containers.internal:1234/v1", "model": ""}
    },
    "temperature": 0.3,
    "max_tokens": 4096,
    "request_timeout": 60,
    "retry_count": 3,
    "retry_backoff_base": 2,
    "reasoning": {"enabled": true, "effort": "high", "exclude": true},
    "provider_routing": {"allow_fallbacks": true},
    "fallback_models": [],
    "overrides": {
      "veto": {"provider": null, "model": null, "temperature": 0.1, "max_tokens": 100},
      "regime": {"provider": null, "model": null, "temperature": 0.2, "max_tokens": 150}
    }
  }
}
```

Все профили сохраняются независимо. Cloud URL — HTTPS; LM Studio допускает HTTP. URL не должен содержать userinfo, query, fragment или пробелы. Provider/model overrides VETO/REGIME задаются независимо: `null` означает наследование. Роль передаётся вызывающим кодом явно, не определяется по температуре или бюджету. Base URL и ключ роли берутся из её итогового провайдера.

Общие границы: `temperature` 0–2; `max_tokens` целое 1–1 000 000 (реальный предел зависит от модели); `request_timeout` 1–300 с; `retry_count` целое 0–5; `retry_backoff_base` 1–10. Повторы только для timeout/connection и HTTP 429/500/502/503/504, с экспоненциальной задержкой до 60 с. Это не гарантия отсутствия повторной оплаты при потере ответа провайдера.

`reasoning`, `provider_routing`, `fallback_models` относятся только к OpenRouter; до 20 fallback ID. DeepSeek использует собственные `thinking.type` и `reasoning_effort`; effort `low/high/max`, при выключенном thinking отправляется `none`. Default thinking выключен. В thinking DeepSeek игнорирует температуру; `max_tokens` включает reasoning и финал. Общие OpenRouter-поля в DeepSeek/LM Studio не отправляются. Торговый парсер не принимает `reasoning_content`, незавершённые/tool/refusal ответы; ошибка приводит к HOLD, не к выдуманному сигналу.

### AI API панели

Все endpoints требуют существующей panel-авторизации:

- `GET /api/config/ai` → `{settings, providers}`; metadata содержит `id/label/requires_api_key/has_api_key/configured`, но не секреты.
- `PUT /api/config/ai` принимает полный AISettings либо частичный patch; ответ `{settings, needs_restart: true}`. Поддерживается обёртка `{settings}` без дополнительных полей. Запись атомарная под общим `.<filename>.lock` с другими writers base/legacy-конфига; повреждённое/отсутствующее persisted состояние не заменяется defaults.
- `GET /api/config/ai/models?provider=ID` → `{provider, models:[{id}]}`; только каталог сохранённого профиля.
- `POST /api/config/ai/probe` принимает `{provider, allow_paid_completion?: false}` → `{provider, ok, latency_ms, message, model?, error_code?}`. Default — GET `/models`. `allow_paid_completion:true` разрешает минимальный платный completion только явным API opt-in; интерфейс панели этот флаг не отправляет.

Transport запрещает redirects и ограничивает размер ответа 4 MiB. Probe/models используют ограниченную очередь; отменённый HTTP-клиент не освобождает slot до окончания сетевого thread. Errors не содержат raw response/prompt/signed URL. UI редактирует черновик отдельно, показывает адресные ошибки, умеет отменять изменения и сбрасывать только draft. Сохранение AI не запускает торговлю; новый snapshot применяется при контролируемом перезапуске. После смены ключей в `.env` пересоздайте контейнеры, не только runtime.

Legacy scalar `ai.model/base_url` и `AI_SETTINGS` мигрируются в выбранный профиль без потери явно заданных значений. Частичные слои стратегии/профиля не дополняются defaults до merge, чтобы не затереть наследование.

## Configuration Categories

### 1. Base Configuration (`config/base.json`)
Rarely changed, infrastructure-level settings:
- Exchange configuration (fees, API settings)
- Chart rendering settings
- Data ranges (CHART_RANGES, PLOTTER_RANGES)
- Technical analysis indicator parameters
- AI provider settings
- Error handling
- Position limits
- News settings

### 2. Trading Configuration (`config/trading.json`)
Moderately changed, controls trading behavior:
- POSITION_SIZE_PERCENT (default)
- MIN_TRADE_AMOUNT_USDT
- MIN_CONFIDENCE_THRESHOLD
- MIN_RISK_REWARD_RATIO
- Feature flags (ENABLE_NEWS, AGGRESSIVE_MODE, etc.)
- SMART_SAMPLING
- DYNAMIC_SIZING
- PERFORMANCE_TRACKING
- REGIME_SETTINGS

### 3. Strategy Configurations (`config/strategies/*.json`)
Frequently adjusted per-strategy:
Each file contains the complete configuration for one strategy:
- **Style preset** (timeframe, loop_interval, leverage, atr_mult, etc.)
- **Signal rules** (weights, thresholds, RSI zones)
- **AI filter settings**
- **Interaction rules**
- **Exit rules**
- **Risk limits** (for SCALP)

#### Available Strategies
| Strategy | Description | Timeframe | AI | Key Parameters |
|----------|-------------|-----------|-----|----------------|
| SCALP | Dual-loop scalp with trailing stops | 1m | Optional | min_score_for_signal, tier1_required, weights |
| AISCALP | Multi-timeframe AI decisions | 1m | Yes | sessions, multi_timeframe, ai_filter |
| HYBRID | Deterministic + AI confirmation | 5m | Yes | ai_filter, min_confidence_to_approve |
| MACDX | MACD crossover, no AI | 15m | No | macd_cross_weight, rsi_zone_weight |
| SWING | Multi-day holding | 1h | Optional | min_hold_hours, cooldown_after_close |
| GRID | Grid trading | 1m | Optional | grid_levels, grid_spacing_pct |

### 4. Profile Configurations (`config/profiles/*.json`)
Per-instance override system with strategy binding:

#### Profile Fields
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `_description` | string | No | Human-readable description |
| `_version` | string | No | Schema version (e.g. "1.0.0") |
| `_inherits` | string | No | Parent profile name to inherit from |
| `_strategy` | string | **Yes** for non-default | Strategy this profile belongs to (SCALP, MACDX, etc.) |

#### Profile Structure Example
```json
{
  "_description": "Aggressive profile for BTC",
  "_version": "1.0.0",
  "_inherits": "default",
  "_strategy": "SCALP",

  "preset": {
    "leverage": 15,
    "atr_sl_mult": 1.0,
    "atr_tp_mult": 2.5
  },

  "position": {
    "size_percent": 15
  },

  "signal_rules": {
    "min_score_for_signal": 3
  }
}
```

#### Special Profiles
- **`default`** - Universal profile with `_strategy: null`. Works with any strategy. Use when you don't need instance-specific overrides.
- **`macdx_5m` / `macdx_15m` / `macdx_30m` / `macdx_1h` / `macdx_1d`** - MACDX profiles with timeframe-specific history, polling, volatility, cooldown and ATR risk settings.
- **`_templates/`** - Future: reusable profile templates for common configurations (aggressive, conservative, balanced)

MACDX history and default chart window:

| Profile | Candles loaded | Approximate history |
|---------|----------------|---------------------|
| `macdx_5m` | 864 | 3 days |
| `macdx_15m` | 672 | 1 week |
| `macdx_30m` | 672 | 2 weeks |
| `macdx_1h` | 720 | 30 days |
| `macdx_1d` | 180 | 6 months |

Indicators always use the full loaded history. Telegram Panel applies its selected
range only after indicator warm-up, so zooming the chart does not reset MACD/EMA.

### 5. Active Configuration (`config/active.json`)
Runtime selection.

Preferred format for multi-strategy runtime:
```json
{
  "strategy_instances": [
    {
      "id": "btc_macdx",
      "symbol": "BTCUSDT",
      "strategy": "MACDX",
      "profile": "default",
      "enabled": true
    },
    {
      "id": "btc_hybrid",
      "symbol": "BTCUSDT",
      "strategy": "HYBRID",
      "profile": "default",
      "enabled": true
    },
    {
      "id": "eth_aiscalp",
      "symbol": "ETHUSDT",
      "strategy": "AISCALP",
      "profile": "default",
      "enabled": true
    }
  ],
  "disabled_symbols": []
}
```

Runtime rule: if multiple strategy instances trade the same symbol, only the instance that opened the current position owns it. Other instances for that symbol wait until the owner closes the position. Ownership is stored in `data/position_owners.json` and synchronized with real exchange positions.

`id` must be stable and unique. It is used in logs, Telegram Panel, position ownership, and runtime process names.

Legacy fallback format without `strategy_instances` is still read for migration.
New saves convert it to `strategy_instances`; `symbol_profiles` is not written anymore
because one symbol can have several strategy-specific profiles:
```json
{
  "strategy": "MACDX",
  "symbols": {
    "bingx": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
  },
  "symbol_profiles": {
    "BTCUSDT": "default",
    "ETHUSDT": "eth_conservative",
    "SOLUSDT": "default"
  },
  "disabled_symbols": []
}
```

## Profile-Strategy Binding

### Why Profiles Are Bound to Strategies

Different strategies use **incompatible parameter systems**:

| Strategy | Parameter System | Example |
|----------|------------------|--------|
| SCALP | Weighted scores (0-10+) | `min_score_for_signal: 4` = score threshold |
| HYBRID | AI confidence (0-1) | `min_score_for_signal: 4` = 40% confidence! |
| MACDX | Confirmation count | `min_score_for_signal: 4` = 4 confirmations |

Setting `min_score_for_signal: 3` means different things for different strategies!

### How It Works

1. Each profile has a `_strategy` field specifying which strategy it belongs to
2. When loading instance config, the system validates profile-strategy compatibility
3. If incompatible, an error is raised at startup:
   ```
   ❌ Profile 'btc_aggressive' belongs to strategy 'SCALP',
   but symbol is using strategy 'MACDX'.
   Use a profile compatible with MACDX or change strategy.
   ```

### Creating New Profiles

Always specify `_strategy` for strategy-specific profiles:

```json
{
  "_description": "Conservative profile for high-volatility pairs",
  "_version": "1.0.0",
  "_inherits": "default",
  "_strategy": "SCALP",

  "preset": {
    "leverage": 5,
    "atr_sl_mult": 2.0
  },
  "signal_rules": {
    "min_score_for_signal": 6
  }
}
```

## Profile Inheritance System

Profiles use a layered inheritance model:
1. `config/base.json` - Infrastructure defaults
2. `config/trading.json` - Trading defaults
3. `config/strategies/{strategy}.json` - Strategy-specific defaults
4. `config/profiles/{profile}.json` - Instance-specific overrides

### Inheritance Example
```json
// config/profiles/eth_conservative.json
{
  "_inherits": "default",
  "_strategy": "MACDX",
  "preset": {
    "leverage": 5
  }
}
```
This profile inherits from `default.json` and adds MACDX-specific overrides.

## Validation

The config loader validates:
- Required fields are present
- Types match schema
- Numeric ranges are valid
- No conflicting parameters at the same level
- Profile references exist
- **Profile matches the instance strategy**

## Hot-Reload

The system supports hot-reload for:
- `config/active.json` - Strategy and profile changes
- `config/trading.json` - Trading parameter adjustments

Changing `strategy_instances`, assigning profiles, enabling/disabling instances, and editing `disabled_symbols` is done through `config/active.json` and is checked by workers every 30 seconds.

Changes to `config/base.json` and strategy files require restart.
# Выбор биржи и рынка

Биржа и рынок выбираются через `.env`: `EXCHANGE=bingx|mexc` и `MARKET_TYPE=perpetual|spot`. Старый список `symbols.bingx` остаётся допустимым. Для нескольких продуктов используется вложенная схема `symbols.<exchange>.<market_type>`. Одновременное смешивание рынков в одном runtime не поддерживается; после смены нужен restart.

Комиссии в `base.json` поддерживают ту же вложенность `exchange.fees.<exchange>.<market_type>`, а старый плоский BingX-формат сохраняется.
