# Состояние интеграции API

Проверено 2026-10-11. Это описание реализации и выполненных проверок, **не сертификация live-торговли и не универсальный SDK всех методов бирж**.

## Реализовано

- Единый `config/trading.json → execution`, редактор панели, безопасный default `read_only`; operational env не выбирают торговый режим, Cross запрещён.
- Раздельные публичные readers и owner/account-specific trading clients. Main передаёт проверенный snapshot через multiprocessing spawn.
- Строгие REST-схемы, typed errors, Decimal instrument rules, quantity/price steps, контрактные множители MEXC, isolated/position-mode проверки и адресные операции.
- Durable intents/wires/ownership/protection/blocks: SQLite/WAL, PREPARED до отправки, стабильные IDs, UNKNOWN без слепой переотправки. ACK не считается fill.
- Perpetual-входы требуют подтверждения исполнения и защиты; ручные заявки не присваиваются и не отменяются массово. Ручное изменение собственного объёма/side/ID вызывает консервативную паузу.
- Private HTTP redirects запрещены. 3xx мутации — UNKNOWN, без повторного POST/DELETE. Ключи, подписи, listenKey и Location не выводятся в журналы.
- BingX синхронизирует clock offset после явного timestamp-отказа одним bounded GET. GET может повториться с новой подписью, мутация не повторяется. Raw UNKNOWN очищается только по доказанной durable-сверке.
- Общие межпроцессные weighted budgets/cooldown. Public REST учитывает HTTP и известные временные body-ошибки, включая BingX `100410`; неизвестные бизнес-коды не повторяются.
- Public WS BingX/MEXC Perpetual, protobuf WS MEXC Spot: heartbeat, reconnect, subscription ACK, REST backfill/reconciliation, dedup, gaps и freshness.
- Точные потоки `(exchange, market_type, symbol, interval)`. Стратегии используют закрытые свечи; график также текущую live-свечу. Исторические коррекции и volume-only updates обновляются в UI.
- Structured logs: категории, UTC, PID, symbol/instance/order/request context, redaction, history/live cursors и фильтры панели. Text mirrors и старая история сохраняются.
- Panel WS: одноразовый ticket с проверками Origin/expiry/revocation. Карточка исполнения показывает pending/blocks/ownership, не снимая safety-паузы.
- ИИ: OpenRouter, официальный DeepSeek, LM Studio; отдельные base_url/model, explicit VETO/REGIME roles, независимые provider payloads, HOLD при невалидном/незавершённом финальном ответе. Авторизация через отдельные env-ключи; секреты не принимаются в JSON/UI.
- Отдельная вкладка ИИ: сохранение/отмена/reset draft, каталог моделей, GET-only probe сохранённого профиля, адресные ошибки, отмена/stale response handling. Нет автоматического запуска торговли или платного completion.
- Общий межпроцессный lock AI/base/legacy writers, строгая проверка persisted конфигурации; повреждённый файл не заменяется defaults. `.env.example` обновлён под все три AI-провайдера.
- BingX native GRID: durable локальные уровни, один attached MARKET POST, exact parent `li` из private WS и строгая сверка пары SL/TP/full fill/close/cleanup. **Путь реализован, но отключён release-gate до полной VST-верификации.** GTC GRID и MEXC native entry тоже запрещены.
- Реальный VST listenKey parser поддерживает top-level `{listenKey:...}` без code/data. Успешный ACK listenKey не считается ACK entry: явный отказ единственного торгового POST сохраняется отдельным REJECTED wire и может восстановиться после сбоя без нового POST. Сообщение API очищается от секретов и ограничивается по длине.

## Выполненные проверки

Все Python/Node/build-проверки — в Podman.

| Проверка | Результат |
|---|---|
| Полный offline pytest | **2590 passed, 6 skipped, 58 subtests passed** |
| Frontend Node regression tests | **64 passed** |
| Frontend `tsc && vite build` | Успешно |
| Оба образа, `podman build --format docker` | Успешно, healthcheck сохранён |
| Автономный старт/healthcheck/SIGTERM без сети и ключей | Supervisor в stopped и panel успешно проверены |
| Настоящий read_only runtime после создания логовой БД | В обоих образах: running, BTC MACDX worker alive, затем stopped по SIGTERM; сеть отключена |
| `git diff --check` | Без ошибок |
| BingX public WS | BTC-USDT, 1m/5m, live candles и heartbeat подтверждены |
| MEXC Futures public WS | BTC_USDT, 1m/5m на отдельных shards, live candles и heartbeat подтверждены |
| MEXC Spot public WS | BTCUSDT, 1m/5m, protobuf candles, ACK/PONG подтверждены |
| BingX VST GET через `.env` | Server clock, balance VST, positions, BTC open orders, instrument rules подтверждены |
| BTC instrument rules VST vs production | Минимальный объём, notional и precision совпали на момент проверки |
| BingX VST private listenKey + WSS | Реальный top-level ответ разобран; private WSS handshake подтверждён, без entry |
| Один BingX VST attached MARKET entry | Биржа отклонила `109400`; fill отсутствует, final BTC flat/open orders=0; durable UNKNOWN/блокировка сохранены |
| VST `/order/test` с архивным entry payload | HTTP 200, code=0; один validation POST, два GET; ledger не изменялся |
| OpenRouter `/models` через `.env` | Успешно, 440 моделей; сохранённая основная модель найдена |
| DeepSeek `/models` | Не выполнялся: DEEPSEEK_API_KEY отсутствует |
| LM Studio `/models` | connection_error: сервер по сохранённому адресу недоступен |

Шесть live-тестов отключены штатным gate; `--run-live` не использовался. Pytest сообщает стороннюю Starlette/httpx deprecation; frontend build — устаревшую Browserslist-базу. Визуальная проверка в настоящем браузере/на мобильном устройстве не выполнялась.

**Реальных сделок не было.** На VST был один отклонённый demo entry, отдельный leverage POST до 1 и listenKey POST. Закрытий/DELETE не было. `/order/test` по официальному контракту не создаёт заявку и не списывает средства; его успех **не доказывает** активацию SL/TP и не снимает старый UNKNOWN. Причина исходного `109400` не установлена: прежний отчёт сохранил code/схему, но не msg. Повторного entry для угадывания причины не отправляли.

Платные AI completions не выполнялись; GET `/models` не отправлял торговые prompts. Успешный каталог не подтверждает генерацию сигнала.

### Исправления запуска и панели

Read-only диагностика работающего стека выявила две конкретные причины ошибок: guard смены execution не распознавал инфраструктурную `data/logs/events.sqlite3`, поэтому supervisor отказывал в первом запуске; история была сохранена как `{}`, а API вызывал у неё `.reverse()`.

- Guard распознаёт логовую БД и rate limiter по известной версии и точным наборам таблиц/колонок, а не по имени или каталогу. Читает committed WAL без `immutable`, миграций и удаления файлов. Неизвестная/изменённая infrastructure-схема, pending execution journal и владение по-прежнему блокируют смену исполнения.
- История нормализуется из списка и legacy mapping только в памяти; некорректные записи пропускаются, исходный файл не переписывается. Пустой `{}` больше не вызывает ошибку history/stats API.
- Панель разделяет успешное сохранение и применение worker-команды: обновляет карточки после PUT, при stopped/crashed не отправляет start/restart worker и объясняет необходимость общего «Старт». Отказ команды после успешного сохранения — предупреждение, не предложение повторить PUT. Ручной worker restart доступен только при свежем running с PID и execution snapshot.
- Worker API возвращает 409 при отсутствии актуального runtime, 422 для JSON не-объекта и безопасный 503 при ошибке записи. Общий «Стоп» доступен и при crashed, чтобы подтвердить stopped перед изменением execution.
- 404 графика показывается как ожидание данных с автоматическим повтором и повторным поиском источников; auth/другие ошибки остаются ошибками. Фиктивные свечи не создаются, backend 404 не скрывается.
- Ошибки API сохраняют request ID, шаблон маршрута и последние места traceback без текста исключения, query, locals или исходных строк.

Регрессии: `tests/test_runtime_panel_regressions.py`, `tests/test_runtime_panel_frontend.cjs`. Дополнительный `tests/container_runtime_smoke.py` проверяет настоящий spawn runtime/worker после создания логовой БД в одноразовом контейнере. Прежний `tests/container_smoke.py` проверял supervisor только в stopped и не покрывал эту причину падения. Сбор свечей с реальной биржи после исправления **не проверялся**: все новые проверки выполнялись без сети и ключей; обработка данных и графиков покрыта offline-тестами.

### Загрузка графиков через туннель

Последующая read-only диагностика: рабочие bot/panel healthy; chart API возвращал HTTP 200 примерно за 20–40 мс. На сохранённых BTC/BNB snapshots один JSON занимает около 222 КБ. Старый frontend при отмене чтения HTTP 200 body (включая общий 10-секундный таймаут) сообщал «Некорректный ответ сервера», как при невалидном JSON. Это воспроизведено регрессионным тестом; без браузерного trace нельзя утверждать, какой именно сетевой сбой произошёл на скриншоте.

- Добавлен gzip с уровнем 5 и порогом 1000 байт. На прочитанных реальных snapshots: BTC около 222 → 50 КБ, BNB около 223 → 47 КБ (снижение на 77–79%). Длина истории и warm-up индикаторов не меняются; auth и `no-store` сохраняются. Измерена степень сжатия, не скорость внешнего туннеля.
- Клиент сохраняет `AbortError`, различает обрыв загрузки body и HTML вместо API JSON, не выводит raw body/ошибки транспорта.
- `App` сохраняет страницу Charts после первого посещения. Повторное открытие сразу показывает последний успешный снимок и выбранный источник/диапазон; актуальный snapshot загружается в фоне с доступным статусом. Скрытая вкладка отключает polling/WS subscriptions и отменяет запрос; поздние ответы игнорируются. Истечение auth удаляет сохранённый снимок.
- Эти оптимизации ещё не применены к рабочей панели; пересобран только panel image, bot/runtime не перезапускались. Рабочие конфигурация, данные и `.env` не менялись. Сетевые/торговые запросы для этой проверки не выполнялись; изучались существующие логи и snapshots.

### Воспроизводимые bounded probes

- `podman run --rm --network none -i localhost/servo-engine-bot:dev python - < tests/container_runtime_smoke.py` — настоящий read_only runtime/worker и SIGTERM, без mounts рабочих данных, сети и ключей; тот же тест проходит в panel image.
- `tests/probe_perpetual_ws.py bingx|mexc` — публичные проверки без `.env`.
- `tests/probe_mexc_spot_ws.py` — публичная protobuf-проверка без `.env`.
- `tests/probe_bingx_demo_read_only.py` — GET-only VST, env через `--env-file .env`.
- `tests/probe_grid_bingx_demo.py --confirm-vst-listen-only` — bounded private listenKey/WSS, без торговых мутаций.
- `tests/probe_grid_bingx_demo.py --confirm-vst-grid-market` — первоначальный bounded probe с постоянным ledger/command ID; сейчас entry отключён release-gate, старый UNKNOWN не обходится. Нельзя удалять журнал или менять ID ради нового POST.
- `tests/probe_bingx_order_validation.py --confirm-vst-validation-only` — архивный payload из read-only `/probes/native-market/probe.sqlite`; разрешены только VST GET времени/баланса и один `/order/test`. Mount `.cache/vst-probes:/probes:ro,z`; никаких entry/close/leverage или правок journal.
- `tests/probe_ai_read_only.py --confirm-get-only` — GET `/models` сохранённых AI-профилей, env через `--env-file .env`; никакого completion.

Первоначальные demo-артефакты сохранены в `.cache/vst-probes/native-market/`, включая `probe.sqlite` и отчёт `report-c1686cf2fef147d7b9981b0d82b61acc.json`; не публикуйте весь приватный ledger.

## Ограничения, которые нельзя скрывать

1. **Новые Perpetual GRID entries выключены**, включая native MARKET. GTC partial/resize lifecycle не доказан. Native BingX требует реальной активной пары exact-owned SL/TP, full close и cleanup; этой успешной VST-проверки пока нет. Capability не включается настройкой пользователя. Сверка сохранённого состояния остаётся доступной. Старые LIMIT не отменяются автоматически и могут исполниться без биржевого SL/TP. См. [GRID CONTRACTS](src/core/strategies/grid/CONTRACTS.md).
2. **MEXC не имеет API sandbox.** Demo нормализуется в production read_only, не симуляцию исполнения. Live MEXC mutations не проверялись. Актуальность используемого futures `/order/create` и всех protection readback-полей не подтверждена старым maintenance reference.
3. **MEXC Spot SL/TP только локальные.** Для LIVE нужен бюджет и явное принятие риска. Остановка процесса/сети оставляет лоты без биржевой защиты. Spot GRID работает последовательно с generic pending gate, не как сертифицированная параллельная сетка.
4. Ручные изменения общего счёта не атомарны с REST readback. Неоднозначность вызывает durable-паузу; безопасного UI-принятия изменённого оператором baseline пока нет. Нельзя удалять journal/ledger/registry для обхода.
5. Символьные trackers консервативно отвергают несколько собственных Hedge-позиций одного символа. Native GRID рассчитан на одну позицию/сторону: Inventory LONG; Hedged LONG либо SHORT, не одновременно. MARKET touch не гарантирует LIMIT-цену или отсутствие проскальзывания.
6. BingX budget 1 request/s — локальная консервативная политика, не официальный общий лимит. Limiter не знает о сторонних клиентах/IP-пользователях; гарантировать отсутствие 429 нельзя. SQLite volumes — локальные, не NFS.
7. JSONL ротируется, но автоматического удаления/импорта старой истории нет. Нужен контроль диска и политика архивации. Backup SQLite должен учитывать WAL.
8. DeepSeek/LM Studio completion проверен только fake transport-тестами. Для сетевой проверки DeepSeek нужен ключ; для LM Studio — доступный сервер и загруженная модель. Provider retries могут повторно тарифицироваться при потере ответа.

## Применение

Образы `localhost/servo-engine-bot:dev` и `localhost/servo-engine-panel:dev` пересобраны. Рабочий Compose-стек автоматически не пересоздавался и торговля не запускалась. Рабочие `.env`, позиции и история не редактировались; демо leverage изменялся только в bounded VST probe. Коммитов нет. Сохранённый execution остаётся read_only.

Исправления запуска и панели ещё **не применены** к работающим контейнерам: пересборка тега не заменяет уже запущенный image. На момент диагностики bot runtime — crashed, execution — read_only. Рабочие контейнеры в этой исправляющей итерации не останавливались/не пересоздавались; их config, data, история и журналы не редактировались.

Контролируемое применение требует согласования с оператором. Сохраните согласованный backup `config/` и `data/` с учётом SQLite WAL, затем адресно проверьте legacy managed orders. Для ngrok-панели учтите передачу URL через `scripts/start_panel.sh`: простое пересоздание panel без этого окружения может потерять актуальный адрес. Пересоздайте контейнеры по [CONTAINERS.md](CONTAINERS.md). Не запускайте старый и новый runtime одновременно с общим аккаунтом/volume. В рабочий `.env` добавляйте только нужные AI-ключи, не заменяйте его шаблоном.

## Источники

- [BingX docs-v3](https://bingx-api.github.io/docs-v3/), опубликованная [source map](https://bingx-api.github.io/docs-v3/static/js/app.b18e5015afe852e38cbf.js.map): Place Order, Test Order, Order Update.
- [MEXC Futures portal](https://www.mexc.com/api-docs/futures/); [старый contract_v1 reference](https://mexcdevelop.github.io/apidocs/contract_v1_en/) не доказывает поддержку current trading endpoints.
- [MEXC Spot V3](https://mexcdevelop.github.io/apidocs/spot_v3_en/), [официальная protobuf-схема](https://github.com/mexcdevelop/websocket-proto), закреплённый commit `0c9c4f35dd0fadc3a46a350e909a93379d81e811`; [MEXC_SPOT_PROTO.md](src/exchanges/ws/MEXC_SPOT_PROTO.md).
- [DeepSeek API](https://api-docs.deepseek.com/), [Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion), [List Models](https://api-docs.deepseek.com/api/list-models).
- [LM Studio OpenAI compatibility](https://lmstudio.ai/docs/developer/openai-compat).
