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

Замените `user@server` на свой адрес. Проверьте серверную копию `.env`: ключи, режим торговли и URL панели. Для этих архивов используйте `SERVO_IMAGE_PREFIX=localhost/servo-engine`, `SERVO_IMAGE_TAG=dev` или оставьте defaults. Префикс/тег должны совпадать с экспортированными образами.

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
| `active.json`, `trading.json` | Hot-reload каждые 30 секунд; состав процессов может требовать перезапуска |
| Остальные JSON | Перезапустить runtime, без сборки |

`restart` не обновляет образ. Не запускайте старый и новый бот одновременно с общей `data/` или одними API-ключами без плана миграции.

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
