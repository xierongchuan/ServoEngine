# Контракты интеграции GRID

## Реализованная, но отключённая альтернатива: локальный touch → native-protected MARKET

**BingX perpetual получил новый entry-путь**, не снимающий запрет GTC LIMIT. Он отключён `BINGX_NATIVE_MARKET_VERIFIED=False` до успешной проверки active SL/TP/full close/cleanup на VST; этот release-gate не является настройкой пользователя. Модель изменена: публичные уровни — локальные durable triggers, а не resting биржевые LIMIT. Touch открывает один MARKET с обеими `stopLoss`/`takeProfit` **в исходном POST**. Worker по умолчанию выбирает `entry_execution="native_market"` для BingX perpetual; прямой `GridExecutor` сохраняет default `gtc_limit` для совместимости. Явный `gtc_limit` остаётся failclosed. Рабочий конфиг не изменяется.

Выбрана только биржевая защита. Local opt-in, post-fill `set_sl_tp()` и raw fallback не используются. Остановка worker до touch оставляет символ flat; после успешного входа SL/TP уже существуют на бирже и не требуют работающего local stop.

1. Native entry поддерживает Hedge/One-way в разрешённом raw isolated-контракте. До каждого исходящего entry проверяются полностью flat символ, отсутствие **любых** открытых заявок и положительного durable ownership аккаунта, instrument rules, margin и gross cap. `inventory` открывает только LONG; `hedged` допускает LONG или SHORT, **но не одновременно**. Parallelism = 1.
2. Уровни закрепляются при arm и сохраняются вместе с предыдущей public ценой. Worker не центрирует их заново на каждом tick. До пересечения нет торгового POST; при gap выбирается первый пересечённый уровень. Один touch поглощает весь arm. После закрытия и cleanup новый arm привязывается к новой цене.
3. `emergency_stop_loss_pct` в native-модели задаёт расстояние **биржевого attached SL** от свежего last/mark reference; TP — `grid_spacing_pct × spacing_mult`. Это не local emergency. Raw ещё раз проверяет направление/округление по актуальной reference перед POST. Проскальзывание возможно: MARKET не обещает LIMIT-цену уровня.
4. До entry открывается bounded private WSS с REST listenKey. Подписки/startup ACK документация не требует; успешный handshake означает только готовность транспорта. `TRADE_UPDATE.o.li` доказывает exact parent entry; сохраняются IDs и параметры обеих conditional заявок. Ни matching по цене, ни `ti`, ни присвоение ручных планов не допускаются.
5. Успех требует **полного FILLED MARKET**, durable исходного wire с обеими защитами, двух разных exact-ID `li` связей, активного REST readback обеих SL/TP с полным объёмом/side/reference и единственной совпадающей позиции. В One-way требуется `reduceOnly=true` либо `closePosition=true` на защите, но quantity всё равно обязателен. Чужая pending заявка после fill также блокирует подтверждение.
6. Дополнительный entry, partial reduce, SLTP resize/replacement и leverage в защищённую позицию запрещены. Multi-level resize обеих сторон не заявляется: безопасная альтернатива исключает сам такой переход.
7. Сверка продолжается и после terminal entry. Trigger-close учитывается только через exact saved conditional ID → exact `triggerOrderId` MARKET child с full fill и fresh flat. Исчезновение позиции само по себе, partial child или другой parent не обнуляют ownership.
8. Добровольный `close_position(..., percentage=1)` разрешён после свежей проверки активного native lifecycle и exact owned size/ID. Защита сохраняется до close fill. Close receipt, нулевой owned и связь parent записываются **атомарно**. Остаточные conditional заявки отменяются только по сохранённым exact IDs и после fresh flat. Пока cleanup не подтверждён, новый entry запрещён.
9. Entry failure и worker failure используют existing `failure_policy`: `pause` либо один safety full close, только если полный entry и точный own размер доказаны. При manual size/side/ID drift или UNKNOWN entry/close закрытие не угадывается. При потере `li` после full fill policy `close` может подтвердить flat, но неизвестные orphan IDs нельзя отменять подбором: символ остаётся paused.
10. PREPARED/UNKNOWN не переотправляются. Restart читает exact order/client IDs; pending native close/cleanup восстанавливаются до entry-сверки. UNKNOWN DELETE проверяется только GET; ещё активный план оставляет паузу, повтор DELETE запрещён. PREPARED без wire отвергается как not-sent. Crash до сохранения WS links не позволяет угадать их из REST.

`get_grid_entry_capabilities()` разделяет `entry_allowed=False` / `max_safe_entry_parallelism=0` для GTC и `native_market_entry_allowed` / `native_market_parallelism=1`. В текущем release `native_market_entry_allowed=False` и `native_market_parallelism=0`: fake lifecycle и успешный `/order/test` не разрешают реальный runtime-вход. После доказанной верификации проектная модель ограничивает parallelism единицей. Native entry идёт исключительно через `place_native_grid_entry()` и durable coordinator.

**Границы:** не обещаются абсолютные 0-race гарантии, гарантированная MARKET-цена, отсутствие slippage или невозможность исключительного partial MARKET fill. Partial/неполный readback не считается успехом и не открывает следующий уровень. Атомарное partial GTC покрытие не доказано и не разрешается ни в demo, ни в live. Реальная доставка `li` для активных attached conditional заявок и варианты представления trigger ордеров на VST ещё не проверены; отсутствие доказательств даёт policy close/pause, а не ложное ACTIVE.

**Legacy не мигрирует автоматически.** Обновление не отменяет старые LIMIT и не добавляет защиту старым позициям. Переход старого durable GRID с командной историей в native-модель требует остановки/сверки оператором. Spot local protection не разрешает perpetual entry; AI/config/frontend и рабочие данные не изменяются.

## Исследованный официальный API-контракт (2026-10-11)

### BingX

- [Актуальная документация docs-v3](https://bingx-api.github.io/docs-v3/), [официальный commit](https://github.com/BingX-API/docs-v3/commit/0cc3b7c5f0e67cada60bbe2f8043d04182b835ac) от 2026-10-09. SPA-документы исследованы через [опубликованную source map](https://bingx-api.github.io/docs-v3/static/js/app.b18e5015afe852e38cbf.js.map): разделы Swap / Trades Endpoints / Place Order, Query Order Details, Cancel Order и WebSocket Account Data / Order update push.
- `POST /openApi/swap/v2/trade/order` принимает JSON-string `stopLoss` и `takeProfit`; опубликован пример MARKET с attached `takeProfit`. Native-путь отправляет обе защиты в том же запросе: type, stopPrice, quantity, workingType. REST echo этих полей не доказывает activation/coverage каждой частичной сделки.
- Обновление **2026-10-09** добавило в WS `TRADE_UPDATE` поле `li`: «Parent order ID associated with the take-profit or stop-loss order; returned only when a related parent order exists». `ti` — related conditional order ID, **не parent ID**.
- Документы не предоставили достаточного контракта атомарной защиты каждого partial fill и durable REST восстановления parent-child связи после пропущенного WS-события. WS `li` не заменяется совпадением symbol/цены/объёма; Для native MARKET добавлен отдельный bounded private stream внутри GRID scope; общие WS providers не изменялись.
- Нельзя предполагать защиту «всей позиции» без quantity: changelog 2026-08-24 прямо требует quantity и stopPrice при `closePosition=true`, запрещает reduceOnly в Hedge. Это не доказательство безопасного resize многослойной GRID.

### MEXC

- [Текущий futures portal](https://www.mexc.com/api-docs/futures/trading-and-orders) не удалось получить через fetch. Использован доступный [официальный contract_v1 reference](https://mexcdevelop.github.io/apidocs/contract_v1_en/); его наличие **не подтверждает актуальность торговых endpoint**.
- Reference описывает `/api/v1/private/order/submit` с `stopLossPrice`, `takeProfitPrice`, `positionMode`, но раздел отмечен **Under maintenance**. Текущий raw-клиент использует `/api/v1/private/order/create`; reference не доказывает доступность или атомарную семантику этого маршрута.
- В `GET /api/v1/private/stoporder/list/orders` документированы `id` — ID плана, `orderId` — exact родительский LIMIT ID (0 для position-based), `positionId`, `positionType`, `vol`, цены SL/TP, `state`, `isFinished`, `triggerSide`, `realityVol`, `placeOrderId` — ID ордера после успешной delegation. Это позволяет адресную read-only сверку сохранённого состояния, но не доказывает защиту во время переходов partial fill.
- `state`: 1 untriggered, 2 cancelled, 3 executed, 4 invalid, 5 execution failed. `triggerSide`: 1 TP, 2 SL. Сам executed план не доказывает close fill: требуется чтение exact child.
- Используемые существующим raw coverage-контрактом дополнительные поля reference/типа/объёма (`lossTrend`, `profitTrend`, `volType`, `profitLossVolType`, типы/reverse и `priceProtect`) не перечислены в найденной старой таблице stoporder response. Их отсутствие не дополняется defaults; неполный readback блокируется. Проверка таких полей в офлайн fixtures **не доказывает их поддержку актуальной биржей** и не разрешает entry.
- В reference нет достаточной гарантии атомарного partial покрытия, resize обеих защит или сохранения защиты исполненной части при cancellation race. История stoporder без временных параметров по умолчанию ограничена последними 7 днями; исчезновение плана не означает безопасный cleanup.

**Вывод для LIMIT:** недостаточно документальных гарантий для разрешения даже single-level GTC; его parallelism = **0**. Native MARKET BingX — отдельный ограниченный путь без pending GTC/resize. MEXC native submission пока не поддерживается. Реальных сделок не было. Сетевой VST probe отправил один attached MARKET POST: отказ `109400`, fill отсутствует, final flat/open orders=0. Старый UNKNOWN не снят. Private listenKey/WSS handshake подтверждён; отдельный `/order/test` с архивным payload вернул code=0, но active pair/full close/cleanup это не доказывает. Правила BTC VST/production совпали; первоначальный msg не сохранён, причина отказа не установлена.

## Совместимость: legacy saved-attached LIMIT lifecycle

Attached-режим определяется только сохранённым `grid_entry` с `request.attached_protection="exchange"` и обеими SL/TP. Legacy records не мигрируют в него автоматически. Офлайн-тесты явно создают сохранённые intents/wires; production guard не отключают.

1. До сверки защиты требуется durable **исходный entry POST**: exact endpoint/client ID, symbol, opening side, LIMIT, quantity, price, position mode и reference; для MEXC также isolated/openType и плечо. Echo без wire не присваивает управление защитой.
2. MEXC ищет только `plan.orderId == entry.order_id`, допускает один комбинированный план на parent. Сохранённый plan ID нельзя заменить; дубли, другой position ID/side, изменённые SL/TP/reference/coverage, регрессия version/времени — UNKNOWN. Position-based планы с `orderId=0` и похожие ручные планы не присваиваются.
3. Pending без fill возвращает `confirmed=False`. Partial/full readback сверяется с точным объёмом parent, а не всей агрегированной позиции. Несколько сохранённых LONG/SHORT parents могут сверяться вместе, их остатки суммируются по exact position ID. Это чтение изменения объёма, **не атомарный биржевой resize**. Смешанный legacy/attached aggregate не считается подтверждённым.
4. `attachment.confirmed=True` означает соответствие полного **текущего snapshot** проверкам coverage. Это не гарантия отсутствия прошлых/будущих незащищённых интервалов и не разрешение POST. `verify_protection()` требует сумму parent coverage, равную exact owned quantity, и выполняет повторную GET-сверку.
5. Внешняя отмена MEXC entry без fill требует terminal cancelled плана; активный или пропавший план не считается cleanup. После внешней отмены partial entry его исполненная часть должна сохранять полный active SL/TP; terminal partial остаётся safety-паузой.
6. MEXC trigger учитывается только после terminal parent и чтения exact `placeOrderId`: совпадающие symbol/position ID, MARKET closing side, delegated quantity и полный FILLED child. Owned уменьшается только на доказанный child fill; повторная сверка/restart идемпотентны. Смена terminal child ID/snapshot запрещена. Trigger при pending parent или partial/pending child блокируется: дальнейшее покрытие не доказано.
7. Legacy BingX LIMIT поддерживает только pending echo-сверку. Любой fill, terminal entry или отсутствие/изменение echo — UNKNOWN, потому durable active parent-child связь/cleanup не доказаны. При подтверждённом entry fill и совпадающем aggregate ownership сохраняется, даже если защита не доказана.
8. Fills, ownership, receipt и пауза `GRID_ATTACHED_PROTECTION_UNKNOWN` при ошибке attachment фиксируются одной journal-транзакцией. Ошибка readback не обнуляет ранее доказанный owned объём. Ошибки aggregate size/side/ID, account mode и lookup дают failclosed. Чужая durable pause не заменяется и не снимается успешной attached-сверкой.
9. PREPARED/UNKNOWN восстанавливаются exact-ID GET, без повторного entry POST. Без wire PREPARED считается not-sent, а не поводом отправить заявку. Проверка продолжается и для terminal entries. Native receipt после доказанного full close и terminal cleanup архивируется логически и больше не сверяется против новой позиции; legacy history retention не обходится.
10. Pending/owned legacy attached exposure запрещает entry/reduce/close/protection/leverage мутации **для всего символа аккаунта**, включая другого owner. Исключения native — доказанный full close и flat exact-ID cleanup, описанные выше. Attached cancellation также запрещена до I/O/PREPARED: гарантии сохранения SL/TP при concurrent fill нет. Ни post-fill `set_sl_tp`, ни local opt-in, ни legacy raw fallback не используются. HTTP redirect prohibition raw-клиентов сохранён.

### Матрица legacy LIMIT capabilities (не native MARKET)

| Capability | BingX | MEXC |
| --- | --- | --- |
| Новый attached GRID LIMIT, включая single-level | Запрещён | Запрещён |
| Read-only pending исходного attached entry | Echo, не active proof | Exact parent plan либо ожидание появления, не active proof |
| Read-only partial/full snapshot coverage | UNKNOWN | Условно: только полный строгий readback |
| Несколько сохранённых parents / LONG + SHORT | Fill UNKNOWN | Read-only aggregate сверка в Hedge; не разрешение новых уровней |
| Внешний cancel readback / cleanup | UNKNOWN | Строгий terminal/active readback в зависимости от fill |
| Exact protection-trigger close attribution | UNKNOWN | Terminal parent + полностью FILLED exact MARKET child |
| Attached entry cancel / close / SLTP resize POST | Запрещены | Запрещены |
| Atomic partial/resize/cancel-preservation гарантия | Не доказана | Не доказана |
| UNKNOWN / crash / restart | GET-only, без resend | GET-only, без resend |

## Runtime и идентичность

- Worker вызывается как `run_grid_worker(symbol, grid_config, runtime_config, ws_cache=..., ws_ready=...)`.
- Resolved config содержит `execution`, `STRATEGY_INSTANCE_ID`, `EXECUTION_ACCOUNT_ID`, плечо, лимиты и `DATA_DIR`. Namespace аккаунта — стабильный несекретный label, например `default:bingx:demo`, не API-ключ.
- Worker устанавливает log context и подключает Manager WS-proxies до создания клиента. Он не управляет lifecycle владельца данных.
- Factory разделяет clients по owner/account/settings; для Perpetual подключает общий durable coordinator, для MEXC Spot — owned-only адаптер и supervisor локальной защиты.
- `read_only` вычисляет public preview без authenticated prerequisites, торговых заявок и mutable GRID-реестра.
- Свечи читаются по точному свежему `StreamKey`. Защитная reference — настоящий last/mark, не mid или OHLC close. При отсутствии mark в BingX ticker используется public premiumIndex.
- Изменение scope или grid/position mode при pending/owned состоянии блокируется. Не удаляйте реестр ради обхода проверки.

## Сверка и управление существующим состоянием

- `reconcile_grid(symbol)` адресно читает всю управляемую сетку и атомарно обновляет aggregate ownership. ACK не доказывает fill.
- Journal хранит стабильные `command_id` и `client_order_id`. UNKNOWN не переотправляется, в том числе после рестарта. Отмена подтверждается терминальным exact-ID readback.
- `is_grid_symbol_blocked()` допускает собственные подтверждённые ACK LIMIT, но не обходит PREPARED/UNKNOWN/PARTIAL, чужие pending и protection-блокировки.
- Inventory SELL уменьшает только доказанный собственный LONG: `place_reduce_order(..., position_id=..., reduce_only=True)`. Hedge использует LONG, One-way — BOTH. Entry SELL не подменяет reduce API.
- Hedged BUY LONG и SELL SHORT — независимые открывающие направления. Это контракт сохранённого состояния, не разрешение новых входов.
- Emergency сначала подтверждает отмену managed LIMIT, затем закрывает только собственные position IDs/объёмы. Неопределённый close не повторяется; dust остаётся в реестре.
- Изменение защищённого объёма требует сверки защиты; `CLOSE_PROTECTION_PENDING` не обходится.
- `stable_client_id` содержит 30 символов (`se` + 28 hex), укладывается в лимит MEXC 32. Сохранённые IDs не пересчитываются при рестарте.

## Spot

- GRID использует `SpotTradingAdapter.submit_order(...)`: BUY LONG с локальным SL, SELL собственного LONG-лота, плечо 1.
- Размер лота подтверждается durable fills с учётом base-комиссии. Перед SELL проверяется `get_sellable_quantity()`. Ручные монеты не становятся inventory.
- Общий `quote_budget_usdt` принадлежит аккаунту, а не выдаётся целиком каждому worker. Quote-pool атомарно учитывает резервы других владельцев.
- LIVE Spot требует явного `allow_local_protection` и живого supervisor. Биржевого SL/TP нет; остановка процесса/сети оставляет лоты без защиты.
- Несколько LIMIT поддерживаются адаптером, но GRID соблюдает generic `is_symbol_blocked()` и исполняет уровни последовательно. Параллельная Spot-сетка не должна обходить этот gate без отдельного domain-specific контракта.

## Лимиты и durable-реестр

`inventory_limit` измеряется в base quantity. Gross учитывает LONG + SHORT + remaining pending entries; не net и не уменьшенный плечом nominal. Закрывающая заявка не освобождает лимит до fill. Чужие заявки учитываются консервативно, неизвестный объём блокирует синхронизацию.

Реестр: `DATA_DIR/grid/<get_filename(symbol)>_<scope SHA256>.json`. Используются atomic replace, fsync и writer flock. Сохраняются identity, mode/leverage signature, команды, partial fills и собственные позиции. Повреждение, второй writer, UNKNOWN или неопределённая отмена не превращаются в пустое состояние.

## Проверки

`tests/test_grid_native_market.py` проверяет native lifecycle только с fake HTTP/WS и scoped release-gate эмуляцией: это не подтверждение биржевой capability. Включены регрессии явного отказа entry после ACK listenKey и crash-recovery REJECTED wire без повторного POST.

`tests/test_grid_perpetual_fail_closed.py` проверяет production-запрет обоих entry-путей до I/O/PREPARED, BUY/SELL, обе биржи, режимы позиций, отсутствие raw fallback, legacy restart/fill/cancel и работающие owned reduce/emergency.

Прежние тесты очереди явно обозначают эмуляцию legacy-версии до запрета. Это не production opt-in. В `test_position_contracts.py` cancellation/close проверяются через preload доказанного legacy-состояния, без отключения действующего guard.

`tests/test_grid_attached_protection.py` проверяет production guard без opt-in, полную wire-идентичность, pending/partial/full и restart LONG/SHORT в Hedge/One-way, multi-parent coverage/trigger, exact IDs, manual drift, missing/неполный readback, cancellation ограничения, UNKNOWN без resend, atomic failure commit и сохранение чужой паузы. HTTP/transport полностью fake, любые биржевые мутации в этом файле запрещены assertions.

Все Python-запуски выполняются только в Podman; проверочная команда офлайн, без live tests и установки dependencies:

```sh
podman run --rm --network=none --user 0:0 \
  -v /home/temur/Projects/ServoEngine:/app:ro,Z -w /app \
  --entrypoint python -e PYTHONDONTWRITEBYTECODE=1 -e SERVO_DATA_DIR=/tmp/servo-data \
  localhost/servo-clock-tests:dev -m pytest \
  tests/test_grid_attached_protection.py tests/test_grid_perpetual_fail_closed.py \
  tests/test_execution_grid_regressions.py tests/test_execution_grid_integration_regressions.py \
  tests/test_grid_execution_safety.py tests/test_position_contracts.py \
  tests/test_bingx_safety_regressions.py tests/test_bingx_redirect_safety.py \
  tests/test_mexc_regressions.py tests/test_mexc_reduce_orders.py \
  tests/test_execution_core_regressions.py tests/test_execution_journal_regressions.py \
  -q -p no:cacheprovider
```

Дополнительно запускаются `tests/test_grid_native_market.py`, `tests/test_grid_native_stream.py`, `tests/test_grid_demo_probe_guard.py`. Native проверки используют настоящие coordinator/raw методы и fake HTTP/private WS: original POST SLTP, full fill, обе стороны/position modes, anchored touch/restart, worker без local emergency, one-entry/gross cap, foreign pending, manual drift, exact trigger child, full close crash recovery, UNKNOWN cleanup без resend и policy close/pause. Stream проверки покрывают fixed hosts/no redirects, gzip/Ping, malformed/oversized frames и overflow.

Результат расширенной команды на 2026-10-11: **750 passed**, включая **66 native/stream/probe-guard сценариев** и **90 saved-attached сценариев**. Всё в Podman `--network=none`; live tests не запускались. Офлайн-тесты доказывают поведение реализации на fixtures, не торговые гарантии биржи.

## Отдельный VST-only opt-in probe (не запускался)

`tests/probe_grid_bingx_demo.py` не является live pytest и без `--confirm-vst-grid-market` не создаёт клиент. Private REST/все mutations разрешены только на `https://open-api-vst.bingx.com`; WS — только `wss://vst-open-api-ws.bingx.com`, redirects запрещены. Production host разрешён лишь для unsigned public GET. Фиксированный `BTC-USDT`, Hedge/isolated, fresh flat/no orders, минимальный instrument объём, demo notional ≤100 USDT, leverage 1. Максимум один attached entry, один exact owned full close, два exact-ID DELETE, один listenKey и одно изменение leverage; budget 120 секунд/150 HTTP, новое entry не отправляется, если осталось меньше 60 секунд.

Пример **только для явного opt-in пользователя**, не часть офлайн validation:

```sh
podman run --rm --user 0:0 --env-file .env \
  -v /home/temur/Projects/ServoEngine:/app:ro,Z -w /app \
  --entrypoint python -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app \
  -e SERVO_DATA_DIR=/tmp/grid-vst-probe \
  localhost/servo-clock-tests:dev tests/probe_grid_bingx_demo.py --confirm-vst-grid-market
```

Probe не меняет `.env`/рабочий конфиг/данные. Отдельный journal сохраняется в `/tmp` контейнера. При UNKNOWN никаких blind retries/усыновления orphan IDs: итог выводит flat/open-orders/blocked без ключей и сетевых URL. При failure/оставшемся объёме или планах требуется адресная проверка VST оператором; повторять probe ради cleanup нельзя. Для сохранения journal после `--rm` заранее подключите отдельный временный каталог вместо удаления evidence вместе с контейнером.
