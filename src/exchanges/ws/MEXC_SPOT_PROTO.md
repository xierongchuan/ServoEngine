# MEXC Spot public kline WS

Источники (проверены 2026-10-11):

- [Официальные Websocket Market Streams](https://mexcdevelop.github.io/apidocs/spot_v3_en/#websocket-market-streams).
- [Актуальные docs](https://www.mexc.com/api-docs/spot-v3/websocket-market-streams) в этой среде недоступны (connection reset); используем опубликованные MEXC docs и актуальный официальный repository.
- [PublicSpotKlineV3Api.proto](https://github.com/mexcdevelop/websocket-proto/blob/0c9c4f35dd0fadc3a46a350e909a93379d81e811/PublicSpotKlineV3Api.proto).
- [PushDataV3ApiWrapper.proto](https://github.com/mexcdevelop/websocket-proto/blob/0c9c4f35dd0fadc3a46a350e909a93379d81e811/PushDataV3ApiWrapper.proto).

Новая runtime-зависимость не нужна: bounded decoder читает только известные поля,
неизвестные обычные wire fields пропускает; groups, переполнение, обрезанные поля,
повторные известные поля и несколько oneof body отклоняет. Максимум 64 KiB на frame,
128 полей, 4 KiB на kline body, 256 bytes на строку. Private сообщения не интерпретируются.

## Wire contract

Публичный TLS endpoint: `wss://wbs-api.mexc.com/ws` (без listenKey/credentials).

```json
{"method":"SUBSCRIPTION","params":["spot@public.kline.v3.api.pb@BTCUSDT@Min1"],"id":1}
```

Отписка: `UNSUBSCRIPTION`, тот же полный канал. Успешный control ACK:
`{"id":1,"code":0,"msg":"spot@public.kline.v3.api.pb@BTCUSDT@Min1"}`.
`id` — unsigned integer, локально уникальный между поколениями и readd.
`{"method":"PING"}` → `{"id":0,"code":0,"msg":"PONG"}`.
Сервер не отражает переданный ID PING (проверено публичным wire probe). Ответ
принимается только при pending heartbeat текущего socket; прежнее поколение
отсекается до parser. Серверный PING, RFC PONG, данные свечи и ACK подписки
не подтверждают клиентский JSON heartbeat.

Максимум 30 каналов на соединение; factory сохраняет стабильные shards при изменении
набора. Несколько TF одного символа допускаются в одном Spot socket.

### PushDataV3ApiWrapper

| Поле | Номер | Тип |
| --- | --- | --- |
| channel | 1 | string |
| symbol | 3 | optional string |
| symbolId | 4 | optional string |
| createTime | 5 | optional int64, ms |
| sendTime | 6 | optional int64, ms |
| publicSpotKline | 308 | oneof message |

### PublicSpotKlineV3Api

| Поле | Номер | Тип |
| --- | --- | --- |
| interval | 1 | string |
| windowStart | 2 | int64, seconds |
| openingPrice | 3 | string |
| closingPrice | 4 | string |
| highestPrice | 5 | string |
| lowestPrice | 6 | string |
| volume | 7 | string, base asset |
| amount | 8 | string, quote asset (не OHLCV volume) |
| windowEnd | 9 | int64, seconds |

Свежесть/replay использует createTime, либо sendTime при отсутствии createTime.
Время получения не подставляется вместо отсутствующего event timestamp.
`windowEnd` — конец периода, **не final flag**. REST backfill подтверждает закрытие;
WS обновляет live-ряд. Канал, symbol и body.interval должны совпадать.

WS интервалы: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1, Week1, Month1.
Spot REST использует 1m/5m/15m/30m/60m/4h/1d/1W/1M. Для Hour8 backfill агрегирует
ровно две документированные 4h свечи. Неполный исторический bucket не публикуется как
закрытый; текущий период допускает только уже начавшиеся составляющие. Пропуски не заполняются.

Reconnect/backoff, REST range repair, поколение socket, stale watchdog и atomic
publication наследуются от общего ManagedMarketDataProvider/StreamStore.
