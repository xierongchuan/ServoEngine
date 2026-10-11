# Сборка и развёртывание

Нужны Podman, Bash и Compose-провайдер с поддержкой профилей (`podman compose version`). Python и Node.js на хосте не нужны. Команды разработки выполняйте из корня проекта.

Образы: `localhost/servo-engine-bot:dev` и `localhost/servo-engine-panel:dev`. Код и frontend встроены; `.env` передаётся через `env_file`, а `config/`, `data/`, `charts/` монтируются с хоста. Каталог `config/` нужен целиком.

## Сборка и запуск

```bash
cp .env.example .env
chmod 600 .env
mkdir -p data/prices data/news data/logs charts
# Заполните .env и проверьте config/ перед запуском.
./scripts/build_images.sh
podman compose up --no-build -d bot             # Только бот
podman compose --profile panel up --no-build -d # Бот и панель
```

Без параметров сборка нативная. `uname -m`: `x86_64` → amd64, `aarch64` → arm64.

```bash
./scripts/build_images.sh --platform linux/amd64
./scripts/build_images.sh --platform linux/arm64
./scripts/build_images.sh --platform linux/amd64,linux/arm64 --tag v1.0.0
./scripts/build_images.sh --help
```

Для чужой архитектуры нужна настроенная QEMU/binfmt; альтернатива — нативная CI-сборка. Параметры: `--image-prefix`, `--tag`, `--push`, `--engine docker`. Docker использует buildx; multiarch требует `--push`.

## Перенос образов и .env на сервер

### На машине разработки

Сначала соберите **single-platform** образы под архитектуру сервера. Из корня проекта:

```bash
mkdir -p ../servo-deploy
chmod 700 ../servo-deploy
cp docker-compose.yml .env ../servo-deploy/
cp -r config ../servo-deploy/
chmod 600 ../servo-deploy/.env
podman save --format docker-archive -o ../servo-deploy/bot.tar localhost/servo-engine-bot:dev
podman save --format docker-archive -o ../servo-deploy/panel.tar localhost/servo-engine-panel:dev
scp -pr ../servo-deploy user@server:~/
```

Замените `user@server` на свой адрес. Проверьте серверную копию `.env`: ключи и URL панели. Биржа, рынок и режим торговли задаются в `config/trading.json → execution`, не в `.env`. Для этих архивов используйте `SERVO_IMAGE_PREFIX=localhost/servo-engine`, `SERVO_IMAGE_TAG=dev` или оставьте defaults. Префикс/тег должны совпадать с экспортированными образами.

### На сервере

```bash
cd ~/servo-deploy
chmod 700 .
chmod 600 .env
podman compose version
mkdir -p data/prices data/news data/logs charts
podman load -i bot.tar
podman load -i panel.tar
podman compose up --no-build -d bot             # Только бот
podman compose --profile panel up --no-build -d # Либо бот и панель
```

Исходники и Dockerfile не нужны; **`--no-build` обязателен**. `.env` содержит секреты: передавайте по SSH, не публикуйте в Git или по открытым ссылкам. `save` не включает рабочие данные; переносите `data/` и `charts/` отдельно после остановки писателей.

## Развёртывание через GHCR

CI проверяет обе архитектуры нативно. Push тега `v*` публикует multiarch-образы с тегами релиза, SHA и `latest`; ручной запуск — только SHA.

Перенесите на сервер `docker-compose.yml`, `.env` и полный `config/`. В `.env` укажите свой репозиторий и опубликованный тег:

```ini
SERVO_IMAGE_PREFIX=ghcr.io/owner/repo
SERVO_IMAGE_TAG=v1.0.0
```

```bash
mkdir -p data/prices data/news data/logs charts
chmod 600 .env
podman login ghcr.io                            # Для приватных образов
podman compose --profile panel pull
podman compose --profile panel up --no-build -d
```

Архитектура выбирается автоматически. Для запуска без панели используйте `pull bot` и `up --no-build -d bot`.

## Обновление

Перед обновлением остановите сервисы и сохраните состояние; для каждой копии выбирайте новое имя архива:

```bash
podman compose --profile panel stop bot panel
tar -czf servo-state-backup.tar.gz data config
```

После изменения кода в checkout:

```bash
./scripts/build_images.sh
podman compose --profile panel up --no-build --force-recreate -d
```

На сервере загрузите новые архивы либо измените `SERVO_IMAGE_TAG` и выполните `pull`, затем ту же команду пересоздания. Не заменяйте рабочие `.env` и `config/` без проверки. Для отката выберите предыдущий образ.

| Изменение | Действие |
|---|---|
| Код или зависимости | Пересобрать образы и пересоздать контейнеры |
| `.env` | Пересоздать контейнеры, без сборки |
| `active.json`, `trading.json` (кроме `execution`) | Hot-reload каждые 30 секунд; состав процессов может требовать перезапуска |
| `trading.json → execution` | Все поля (аккаунт, режимы, Spot-бюджет, protection/market_data) — только новый запуск; PUT разрешён при свежем `stopped` без pending запуска и управляемого состояния |
| Остальные JSON | Перезапустить runtime, без сборки |

Supervisor использует общий с панелью `config/.trading.lock`, явный refresh перед стартом и multiprocessing spawn с передачей проверенного снимка. Оба контейнера должны видеть один и тот же writable mount `config/`; не переносите `.trading.lock` отдельно и не удаляйте его во время работы. При незавершившемся stop/kill статус остаётся stopping, смена настроек и новый запуск запрещены.

Старый `.env` с `MODE=real` не включает live: без execution действует read_only. Режим выбирайте в панели. По умолчанию `execution_mode=read_only`; BingX VST включается явным `demo`, реальная торговля — явным `live`. У MEXC `demo` означает только чтение production API, не sandbox. Cross запрещён. При миграции удалите только устаревшие operational env (`MODE`, `EXCHANGE`, `MARKET_TYPE`, `ENABLE_NEWS`, `MEXC_ENABLE_LIVE_TRADING`, `MEXC_MARGIN_MODE`, `MEXC_POSITION_MODE`, `MEXC_SETTLE_ASSET`); новости перенесите в `features.enable_news`, исполнение — в `config/trading.json`. Не заменяйте рабочий `.env` шаблоном: секреты и инфраструктурные переменные оставьте неизменными, права `.env` — 600. После изменения env пересоздайте контейнеры. Подробный контракт и ограничения интеграции — [config/schema.md](config/schema.md).

`execution.account_id` — стабильный несекретный ID (default `default`), label `default:<exchange>:<execution_mode>` не меняется при ротации ключей. Сохраняйте durable `data/execution.sqlite3`, Spot ledger и `data/grid/` вместе с рабочим конфигом; незавершённое состояние запрещает смену аккаунта/источника. Чистая инициализированная БД с терминальной историей допустима, неизвестная схема или ошибка чтения блокирует настройки.

Для live MEXC Spot задайте `execution.spot.quote_budget_usdt > 0` и явно включите `allow_local_protection`. Это **общий TOTAL аккаунта** в durable quote-pool: Spot adapter выделяет владельцам только свободные средства с учётом резервов других instances, не создаёт полный бюджет каждому worker. Переход с legacy per-instance funding и изменение уже выделенного бюджета требуют явной сверки ledger. Настройки с бюджетом 0/без согласия можно сохранить, но supervisor и прямой main запретят запуск. Factory уже подключает Spot adapter/supervisor и Perpetual coordinator. SL/TP Spot только локальные: остановка контейнера/процесса или потеря сети оставляет лоты без биржевой защиты. MEXC Spot поддерживает публичный protobuf WS с REST backfill/reconciliation; неподдерживаемые WS TF работают через точный REST polling. Это описание текущей реализации, не подтверждение live-проверки на бирже.

**Perpetual GRID:** новые открывающие GTC LIMIT заблокированы до доказанного lifecycle биржевого SL/TP при partial fills и resize. Для BingX реализован отдельный `native_market` (durable локальные уровни → одна MARKET-позиция с обеими биржевыми защитами в исходном POST, без local SL). Он тоже отключён release-gate: VST `/order/test` принят, но active SL/TP/full close/cleanup ещё не подтверждены. Выбор режима не обходит этот gate. Старые managed LIMIT не отменяются автоматически и могут исполниться без биржевой защиты; перед обновлением адресно сверяйте их и позиции. Выбор режима сетки не снимает запрет. Сверка, owned cancellation/reduce и локальный emergency сохраняются с safety-проверками. Фактический статус интеграции: [API_INTEGRATION_STATUS.md](API_INTEGRATION_STATUS.md).

`restart` не обновляет образ. Не запускайте старый и новый бот одновременно с общей `data/` или одними API-ключами без плана миграции.

### Провайдеры ИИ

Ключи `.env`: `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `LM_STUDIO_API_KEY` (LM Studio — если сервер требует токен). Провайдер, независимые URL/model и VETO/REGIME задаются в **Настройки → ИИ** / `config/base.json → ai`, не через operational env. Смена AI JSON требует контролируемого перезапуска runtime; смена ключей — пересоздания контейнеров bot/panel, без замены рабочей конфигурации. Старый `.env` сохраните, добавляйте только нужные ключи.

Для LM Studio на хосте default `http://host.containers.internal:1234/v1`; localhost внутри контейнера указывает на сам контейнер. Разрешите доступ из Podman к интерфейсу сервера и укажите загруженный model ID. Не открывайте AI-порт в Интернет без авторизации/firewall. Панель отправляет только GET `/models` для диагностики; prompts и платные completions автоматически не отправляются.

### Миграция журналов

Bot и panel должны видеть одну `data/` (`SERVO_DATA_DIR` / `PANEL_DATA_DIR` при нестандартных путях). Новые structured-события сохраняются в `data/logs/events.sqlite3`, JSONL-сегменты — в `data/logs/events/`; `steps.log`, `trades.log` и символьные text-логи продолжают дописываться. Не удаляйте старую историю: она доступна отдельно через legacy API, без дублирования в `/api/logs/events`. Автоматического импорта text/аварийных JSONL и удаления старых сегментов нет; предусмотрите мониторинг диска и отдельную политику архивации. SQLite backup должен быть согласован с WAL; обычное копирование всей `data/` выполняйте после остановки писателей.

Все logs endpoints требуют авторизации. Metadata — `/api/logs/sources`, фильтрованные history/live страницы — `/api/logs/events`; контракт описан в [config/schema.md](config/schema.md#журнал-логов-api-и-контекст). WebSocket уведомляет об изменении, а не заменяет cursor API. Пересборка/recreate обновляет UI и backend одновременно; старые text endpoints сохранены.

## Управление и доступ

```bash
podman compose --profile panel ps
podman compose logs -f bot
podman compose --profile panel logs -f panel
podman compose --profile panel stop panel       # Только панель
podman compose --profile panel down             # Весь стек
```

- Панель: `127.0.0.1:${PANEL_PORT:-8080}`. Внешний доступ — через HTTPS-прокси/туннель с поддержкой `/api` и `/ws`; задайте `TELEGRAM_PANEL_URL`. Не передавайте токены по публичному HTTP.
- Rootless Podman запускайте владельцем `config/data/charts`, без `sudo`. Docker-пользователь задаётся числовыми `SERVO_UID`/`SERVO_GID`; каталоги должны быть ему доступны на запись.
- Общие SELinux mounts используют `:z`, не приватную `:Z`.
- Bot healthcheck проверяет supervisor и отсутствие `crashed`; остановленная из панели торговля остаётся healthy. Panel healthcheck проверяет `/api/health`, не биржу или Telegram. `unhealthy` сам по себе не вызывает перезапуск.

## Очистка локальных данных

```bash
./scripts/clean_project.sh --mode reset       # Только предварительный просмотр
```

Применение (`--apply`) допускается только после остановки bot, panel и других писателей. Команда сама ничего не останавливает/не запускает, не получает `.env` как окружение и работает без сети в отдельном Podman-контейнере. Сброс архивирует известные generated данные, историю, логи и control-файлы; торговые ledgers/ownership и rate-limit cooldown сохраняются. Pending/UNKNOWN и неизвестная SQLite блокируют reset. Инструкция и восстановление: [CLEANUP.md](CLEANUP.md).

## Проверки и зависимости

Smoke без исходников, ключей и внешней сети:

```bash
podman run --rm -i --network none localhost/servo-engine-bot:dev python - bot < tests/container_smoke.py
podman run --rm -i --network none localhost/servo-engine-panel:dev python - panel < tests/container_smoke.py
```

Полные тесты в готовом образе панели:

```bash
podman run --rm -v ./tests:/app/tests:ro,z -v ./scripts:/app/scripts:ro,z \
  localhost/servo-engine-panel:dev sh -c \
  'pip install -q pytest && python -m pytest tests/ -x -q'
```

Python-версии фиксирует `requirements.lock`, frontend — `npm ci`. Обновление lock в чистом контейнере; перед `:Z` остановите стек:

```bash
podman run --rm -v .:/app:Z -w /app python:3.12-slim-bookworm sh -c \
  'pip install -r requirements.txt -r src/telegram_panel/requirements.txt && pip freeze > requirements.lock'
git --no-pager diff -- requirements.lock
```

После обновления пересоберите и проверьте обе архитектуры. Побитовая воспроизводимость не гарантируется: базовые теги изменяемы, lock без хешей.
