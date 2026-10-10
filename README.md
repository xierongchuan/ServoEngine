# Servo Engine

Торговый бот для BingX Perpetual и MEXC USDT-M Perpetual: технический анализ, AI через OpenRouter, риск-менеджмент и Telegram/Web-панель. MEXC Spot доступен через REST-клиент, но не подключён к торговому конвейеру.

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
| `MODE` | `demo` или `real` |
| `EXCHANGE` | `bingx` или `mexc` |
| `MARKET_TYPE` | `perpetual`; `spot` — только REST-интеграция MEXC |
| `BINGX_API_KEY`, `BINGX_SECRET_KEY` | Ключи BingX |
| `MEXC_API_KEY`, `MEXC_SECRET_KEY` | Ключи MEXC |
| `MEXC_ENABLE_LIVE_TRADING` | По умолчанию `false`; `true` разрешает реальные MEXC-операции |
| `OPENROUTER_API_KEY` | Для стратегий с AI |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID` | Для Telegram-панели |
| `TELEGRAM_PANEL_URL` | HTTPS-адрес панели |

```bash
./scripts/build_images.sh
podman compose up --no-build -d bot                 # Только бот
podman compose --profile panel up --no-build -d      # Бот и панель
```

Панель доступна на `127.0.0.1:8080`; внешний доступ — через HTTPS-прокси или туннель. Код встроен в образы, `.env`, `config/`, `data/` и `charts/` остаются на хосте.

**Сборка amd64/arm64, перенос с `.env` на сервер и обновления: [CONTAINERS.md](CONTAINERS.md).**

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
- Один runtime обслуживает одну пару `EXCHANGE` + `MARKET_TYPE`.
- Реальная торговля MEXC: `MODE=real`, `MARKET_TYPE=perpetual`, `MEXC_ENABLE_LIVE_TRADING=true` — только после read-only проверки.

Схема и профили: [config/schema.md](config/schema.md).

## Панель и данные

Панель показывает позиции, сделки, графики, логи и журнал решений; позволяет менять настройки и управлять торговым runtime. Supervisor остаётся живым после остановки торговли из панели.

Telegram-команды: `/start`, `/status`, `/trades`, `/chart`, `/logs`, `/config`, `/help`. Админские: `/weblink`, `/reload`, `/stop`, `/resume`, `/close`. Доступ задаётся `TELEGRAM_ADMIN_ID` или `TELEGRAM_ALLOWED_IDS`; веб-токены действуют 6 часов.

| Путь | Содержимое |
|---|---|
| `data/active_trades.json`, `data/position_owners.json` | Позиции и их владельцы |
| `data/trade_history.json`, `data/decision_journal.json` | Сделки и решения |
| `data/prices/`, `data/news/` | Рыночные данные |
| `data/logs/`, `data/steps.log`, `data/trades.log` | Логи |
| `charts/` | PNG-графики |

## Разработка

- `src/core/` — workers, стратегии и риск-менеджмент.
- `src/exchanges/` — REST/WS-клиенты бирж.
- `src/prompts/` — AI-промпты.
- `src/telegram_panel/` — FastAPI, Telegram-бот и React.

Сборка и тесты выполняются только в Podman; команды — в [CONTAINERS.md](CONTAINERS.md).

Если сделки не открываются, проверьте ключи, баланс, `enabled`, владельца позиции и причины отклонения сигналов в логах. При ошибках панели проверьте Telegram ID, HTTPS-адрес и логи `panel`. После изменения кода нужен rebuild + recreate, а не `restart`.

## Лицензия

Проприетарное ПО. Использование, копирование, модификация и распространение — только с письменного разрешения правообладателя. См. [LICENSE.md](LICENSE.md).
