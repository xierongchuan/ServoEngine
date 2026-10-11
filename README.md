# Servo Engine

Торговый бот для BingX Perpetual, MEXC USDT-M Perpetual и MEXC Spot: технический анализ, AI через OpenRouter, официальный DeepSeek или LM Studio, риск-менеджмент и Telegram/Web-панель. Factory подключает durable ExecutionCoordinator для Perpetual и SpotTradingAdapter с локальной защитой для Spot. Наличие реализации и offline-тестов не означает проверенную live-торговлю на реальном счёте.

> Торговля может привести к потере капитала. ПО предоставляется «как есть». Начинайте с BingX demo или MEXC read-only: у MEXC нет API sandbox. Не выдавайте ключам право вывода средств.

## Быстрый старт

Нужны Podman и Compose-провайдер с поддержкой профилей. Python и Node.js работают внутри образов. Команды выполняйте из корня проекта.

```bash
podman compose version
cp .env.example .env
chmod 600 .env
mkdir -p data/prices data/news data/logs charts
```

Заполните `.env` и проверьте `config/`:

| Переменная | Значение |
|---|---|
| `BINGX_API_KEY`, `BINGX_SECRET_KEY` | Ключи BingX |
| `MEXC_API_KEY`, `MEXC_SECRET_KEY` | Ключи MEXC |
| `OPENROUTER_API_KEY` | Авторизация OpenRouter |
| `DEEPSEEK_API_KEY` | Авторизация официального DeepSeek |
| `LM_STUDIO_API_KEY` | Необязательный токен LM Studio, если сервер требует авторизацию |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID` | Для Telegram-панели |
| `TELEGRAM_PANEL_URL` | HTTPS-адрес панели |

```bash
./scripts/build_images.sh
podman compose up --no-build -d bot                 # Только бот
podman compose --profile panel up --no-build -d      # Бот и панель
```

Панель доступна на `127.0.0.1:8080`; внешний доступ — через HTTPS-прокси или туннель. Код встроен в образы, `.env`, `config/`, `data/` и `charts/` остаются на хосте.

**Сборка amd64/arm64, перенос с `.env` на сервер и обновления: [CONTAINERS.md](CONTAINERS.md).**

Фактический статус REST/WS, результаты проверок и нерешённые ограничения: [API_INTEGRATION_STATUS.md](API_INTEGRATION_STATUS.md).

## Управление

```bash
podman compose logs -f bot
podman compose --profile panel logs -f panel
podman compose --profile panel ps
podman compose --profile panel stop panel           # Не останавливает бота
podman compose --profile panel down                 # Останавливает весь стек
```

Альтернативные скрипты:

- `scripts/run_trading_bot.sh` — бот в foreground вместо Compose.
- `scripts/start_panel.sh [ngrok|tunnel|prod]` — только панель и настройка URL.
- `scripts/restart_panel.sh [ngrok|prod]`, `scripts/stop_panel.sh` — только панель.
- `scripts/monitor_logs.sh` — просмотр логов.

**Не запускайте два бота с общей `data/` или одними API-ключами без отдельного плана миграции.**

## Стратегии и конфигурация

Стратегии: `SCALP`, `AISCALP`, `SWING`, `GRID`, `HYBRID`, `MACDX`; также есть VETO/REGIME-варианты. `MACDX` работает без AI.

В `config/active.json` задаются `strategy_instances`: `id`, `symbol`, `strategy`, `profile`, `enabled`. Каждый активный instance работает в отдельном процессе. Открытая позиция по символу имеет одного владельца; остальные instances ждут её закрытия.

Конфигурация объединяется в порядке:

```text
base.json → trading.json → strategies/{strategy}.json → profiles/{profile}.json
```

- `active.json` выбирает instances, стратегии и профили.
- `active.json` и `trading.json` проверяются каждые 30 секунд; изменение состава процессов может потребовать перезапуска.
- Остальные JSON требуют перезапуска runtime; изменение `.env` — пересоздания контейнера.
- Один runtime обслуживает одну пару `execution.exchange` + `execution.market_type`.
- Единый источник исполнения — секция `execution` в `config/trading.json`, редактор в панели: **Настройки → Исполнение и риск**. По умолчанию `read_only`; BingX `demo` нужно выбрать явно. MEXC не имеет sandbox: `demo` нормализуется в `read_only`, production API остаётся доступным для чтения.
- `execution` фиксируется на процесс и **не меняется при hot-reload**. На stop/start supervisor явно перечитывает и проверяет settings, обновляет parent globals и передаёт снимок новому child через spawn. Все изменения требуют нового запуска runtime; сохранение не запускает торговлю и не закрывает позиции. PUT и старт сериализованы общим `config/.trading.lock`.
- Любые изменения `execution` (включая аккаунт, Spot-бюджет, защиту и рыночные данные) блокируются API, пока runtime не имеет свежего статуса `stopped` или есть незавершённое управляемое состояние в journal, Spot ledger, GRID registry или legacy JSON. Чистая инициализированная БД с терминальной историей не блокирует настройки. Не удаляйте файлы состояния ради обхода блокировки; сначала сверяйте позиции и ордера с биржей.
- Cross запрещён: только `isolated`, расчёт в `USDT`. Perpetual поддерживает `hedge`/`one_way`; Spot использует `one_way` и сетку `inventory`.
- **Новые входы Perpetual GRID заблокированы**, включая demo: безопасный жизненный цикл биржевой защиты частичных GTC LIMIT-исполнений/изменения объёма не доказан. Для BingX реализована отдельная модель `native_market`: локальный durable touch → одна MARKET-позиция с attached SL/TP в исходном POST, без локального SL. Она тоже отключена release-gate до полной проверки активной пары и закрытия на VST; `order/test code=0` не снимает gate. Выбор `grid_mode` не снимает запрет. Сверка, адресная отмена собственных заявок и уменьшение собственного объёма доступны с прежними safety-проверками. Legacy LIMIT могут исполниться без биржевой защиты — проверьте их на бирже. См. [контракт GRID](src/core/strategies/grid/CONTRACTS.md).
- Реальная торговля MEXC: `execution.exchange=mexc`, `market_type=perpetual`, `execution_mode=live` — только после read-only проверки и явного запуска.
- `execution.account_id=default` задаёт стабильный несекретный ID; label `default:<exchange>:<execution_mode>` не зависит от API-ключа. Ротация ключей не меняет namespace и не переносит владение.
- Spot: `spot.quote_budget_usdt=0` и `spot.allow_local_protection=false` по умолчанию. Бюджет — **TOTAL аккаунта**, общий quote-pool в `spot_ledger.sqlite3`, а не полная сумма каждому worker. Адаптер атомарно выделяет свободный bot quote владельцу перед BUY и учитывает резервы остальных instances; весь баланс кошелька не импортируется. Отключённый live Spot-конфиг можно сохранить, но запуск требует бюджета > 0 и явного принятия риска локальной SL/TP. Биржевых защитных ордеров нет; остановка процесса/сети оставляет лоты без защиты. Публичные данные MEXC Spot поддерживают protobuf WebSocket и REST backfill/reconciliation; TF без WS используют точный REST polling, в том числе в read_only.
- Старый `.env` не выбирает биржу/рынок/исполнение: даже `MODE=real` не включает live. Без секции execution используется безопасный `read_only`; нужный режим выбирайте в панели.
- Operational env `MODE`, `EXCHANGE`, `MARKET_TYPE`, `ENABLE_NEWS`, `MEXC_ENABLE_LIVE_TRADING`, `MEXC_MARGIN_MODE`, `MEXC_POSITION_MODE`, `MEXC_SETTLE_ASSET` больше не переопределяют конфигурацию. Уберите их из старого `.env`; новости задаются `features.enable_news`. В окружении остаются ключи и инфраструктура контейнеров/панели.

При ошибке запуска владельца market-data runtime останавливается до запуска торговых workers. Уже работающий data runtime при обрыве WS использует REST-восстановление; устаревшие/неполные свечи не разрешают новые входы. Изменение своей позиции вручную не означает автоматическое принятие нового объёма ботом: неоднозначность вызывает durable-паузу, карточка панели показывает причину, но не снимает её без доказанной сверки.

Контракт `src.trading_settings.get_trading_settings()` описан в [config/schema.md](config/schema.md). Main повторяет `validate_trading_start(settings)`; factory передаёт один execution snapshot, стабильный account label и owner ID в coordinator/Spot adapter. Публичный market-data runtime обслуживает WS и REST, а workers читают shared snapshots. Проверка локальных файлов не заменяет сверку реального счёта: незавершённые и UNKNOWN-ордера требуют reconciliation, не удаления состояния. До live-запуска отдельно проверьте права ключей, режим счёта, защиту и восстановление; здесь нет обещания их проверки на реальной бирже.

Схема и профили: [config/schema.md](config/schema.md).

## Настройки ИИ

В панели: **Настройки → ИИ**. OpenRouter, официальный DeepSeek и LM Studio имеют независимые `base_url` и модели; смена провайдера не стирает остальные профили. Температура, лимит токенов, таймаут и повторы — в расширенных настройках. Для VETO и REGIME можно отдельно выбрать провайдер/модель либо наследовать основной профиль. Полная схема — [config/schema.md](config/schema.md#конфигурация-ии-basejson--ai).

- Ключи задаются только в `.env`: `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `LM_STUDIO_API_KEY`. Панель показывает наличие, не значение. После изменения ключей пересоздайте контейнеры; рабочий `.env` не заменяйте шаблоном.
- DeepSeek default: `https://api.deepseek.com`, `deepseek-flash`; thinking выключен. При включении reasoning расходует общий лимит токенов и может не оставить финального ответа для короткого VETO. Сохранённые собственные адреса/старые ID моделей автоматически не заменяются.
- LM Studio: из Podman обычно `http://host.containers.internal:1234/v1`; сервер должен слушать доступный контейнеру интерфейс, а не только loopback хоста. Выберите ID загруженной модели. Токен необязателен только если авторизация сервера выключена; не публикуйте порт наружу без защиты.
- «Проверить соединение» и «Обновить модели» выполняют **GET `/models` по сохранённому профилю**, без prompt и платной генерации. Изменённые URL/model нужно сначала сохранить. Доступный каталог не доказывает качество или совместимость completion.
- OpenRouter reasoning/routing/fallbacks не отправляются другим провайдерам. Reasoning-only, обрезанные, tool/refusal и невалидные ответы не становятся торговым сигналом: безопасный результат — HOLD.
- Сохранение не запускает торговлю; после изменения AI-секции нужен контролируемый перезапуск runtime. Настройки профиля стратегии могут переопределять базовый AI-снимок.

## Панель и данные

Панель показывает позиции, сделки, графики, логи и журнал решений; позволяет менять настройки и управлять торговым runtime. Supervisor остаётся живым после остановки торговли из панели.

Telegram-команды: `/start`, `/status`, `/trades`, `/chart`, `/logs`, `/config`, `/help`. Админские: `/weblink`, `/reload`, `/stop`, `/resume`, `/close`. Доступ задаётся `TELEGRAM_ADMIN_ID` или `TELEGRAM_ALLOWED_IDS`; веб-токены действуют 6 часов.

| Путь | Содержимое |
|---|---|
| `data/active_trades.json`, `data/position_owners.json` | Позиции и их владельцы |
| `data/trade_history.json`, `data/decision_journal.json` | Сделки и решения |
| `data/prices/`, `data/news/`, `data/market_data/`, `data/instances/*/prices/` | Рыночные данные |
| `data/logs/`, `data/steps.log`, `data/trades.log` | Логи |
| `charts/` | PNG-графики |

### Очистка проекта

Рабочие данные, кэши и runtime lock-файлы исключены из Git и контекста сборки. Безопасная команда очистки работает в Podman и по умолчанию **только показывает план**:

```bash
./scripts/clean_project.sh                    # Просмотр восстанавливаемого кэша
./scripts/clean_project.sh --mode reset       # Просмотр сброса истории/логов с архивом
```

Для применения нужен `--apply` и остановка всех писателей, включая bot и panel. `reset` архивирует выбранные файлы в `.cache/cleanup-backups/`; `.env`, `config/`, торговые журналы/владение и rate limiter сохраняются. Открытое/UNKNOWN-состояние блокирует сброс. **Торговая память не является временными данными.** Порядок чистого запуска, ограничения и восстановление: [CLEANUP.md](CLEANUP.md).

### Структурированные логи

`GET /api/logs/events` возвращает события с постоянным `event_id`, UTC-временем, PID и контекстом биржи/символа/instance/request/order. Категории: `runtime`, `exchange`, `market_data`, `strategy`, `llm`, `risk`, `order`, `trade`, `config`, `panel`. Панель поддерживает фильтры, историю, pause live и независимый follow.

Источник событий — `data/logs/events.sqlite3` (SQLite/WAL), JSONL-копии — `data/logs/events/`. Text-файлы остаются совместимыми и не подмешиваются в structured history. `GET /api/logs/sources` возвращает категории, уровни, источники, биржи, символы, instances и отдельно `legacy_sources`. Полный контракт курсоров/фильтров — [config/schema.md](config/schema.md#журнал-логов-api-и-контекст).

Старая история сохраняется без автоматического импорта и удаления. JSONL ротируются по сегментам, но retention пока не удаляет их; следите за свободным местом. Новые записи очищаются от секретов, но это не переписывает ранее сохранённые text-логи. При переносе/резервном копировании SQLite остановите писателей или используйте согласованный SQLite backup, не копируйте только DB без активного WAL.

## Разработка

- `src/core/` — workers, стратегии и риск-менеджмент.
- `src/exchanges/` — REST/WS-клиенты бирж.
- `src/prompts/` — AI-промпты.
- `src/telegram_panel/` — FastAPI, Telegram-бот и React.

Сборка и тесты выполняются только в Podman; команды — в [CONTAINERS.md](CONTAINERS.md).

Если сделки не открываются, проверьте ключи, баланс, `enabled`, владельца позиции и причины отклонения сигналов в логах. При ошибках панели проверьте Telegram ID, HTTPS-адрес и логи `panel`. После изменения кода нужен rebuild + recreate, а не `restart`.

## Лицензия

Проприетарное ПО. Использование, копирование, модификация и распространение — только с письменного разрешения правообладателя. См. [LICENSE.md](LICENSE.md).
