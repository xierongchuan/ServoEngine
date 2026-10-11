"""Ограниченный decoder публичной MEXC Spot kline protobuf-схемы, без private API.

Источник: mexcdevelop/websocket-proto, commit 0c9c4f35dd0fadc3a46a350e909a93379d81e811.
См. MEXC_SPOT_PROTO.md: номера полей взяты из двух официальных .proto.
"""

from src.exchanges.market_data import StreamKey, next_start

MEXC_SPOT_WS_INTERVALS = {
    "1m": "Min1", "5m": "Min5", "15m": "Min15", "30m": "Min30", "1h": "Min60",
    "4h": "Hour4", "8h": "Hour8", "1d": "Day1", "1w": "Week1", "1M": "Month1",
}
CHANNEL_PREFIX = "spot@public.kline.v3.api.pb@"
MAX_MESSAGE_BYTES = 65_536
MAX_FIELDS = 128
# PushDataV3ApiWrapper: channel=1, symbol=3, createTime=5, sendTime=6, publicSpotKline=308.
_WRAPPER_TYPES = {1: 2, 3: 2, 4: 2, 5: 0, 6: 0, 308: 2}
# PublicSpotKlineV3Api: interval=1, windowStart=2, OHLCV=3..7, amount=8, windowEnd=9.
_KLINE_TYPES = {1: 2, 2: 0, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2, 9: 0}


def channel_for(key):
    key = StreamKey.from_value(key)
    if (key.exchange, key.market_type) != ("mexc", "spot") or key.interval not in MEXC_SPOT_WS_INTERVALS:
        raise ValueError(f"Неподдерживаемый Spot WS поток: {key.id}")
    return f"{CHANNEL_PREFIX}{key.symbol}@{MEXC_SPOT_WS_INTERVALS[key.interval]}"


def key_for_channel(channel):
    if not isinstance(channel, str) or not channel.startswith(CHANNEL_PREFIX):
        raise ValueError("Это не публичный MEXC Spot kline канал")
    parts = channel[len(CHANNEL_PREFIX):].split("@")
    if len(parts) != 2:
        raise ValueError("Некорректный MEXC Spot канал")
    key = StreamKey("mexc", "spot", *parts)
    if channel_for(key) != channel:
        raise ValueError("Символ/интервал MEXC Spot канала не соответствует протоколу")
    return key


def _varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Обрезанный protobuf varint")
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise ValueError("Protobuf varint превышает uint64")
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Слишком длинный protobuf varint")


def _fields(data, types):
    result, offset, count, body_field = {}, 0, 0, None
    while offset < len(data):
        count += 1
        if count > MAX_FIELDS:
            raise ValueError("Слишком много protobuf полей")
        tag, offset = _varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise ValueError("Некорректный номер protobuf поля")
        if number in types and (types[number] != wire or number in result):
            raise ValueError("Повторное поле или неверный protobuf wire type")
        if types is _WRAPPER_TYPES and 301 <= number <= 315:
            if body_field is not None or wire != 2:
                raise ValueError("Нарушен protobuf oneof body")
            body_field = number
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire in {1, 2, 5}:
            if wire == 2:
                size, offset = _varint(data, offset)
            else:
                size = 8 if wire == 1 else 4
            end = offset + size
            if end > len(data):
                raise ValueError("Обрезанное protobuf поле")
            value, offset = data[offset:end], end
        else:
            # Groups не используются proto3; неизвестные обычные поля безопасно пропускаем.
            raise ValueError("Неподдерживаемый protobuf wire type")
        if number in types:
            result[number] = value
    return result


def _text(fields, number, required=True):
    value = fields.get(number)
    if value is None:
        if required:
            raise ValueError(f"Отсутствует protobuf поле {number}")
        return None
    if not isinstance(value, bytes) or not 0 < len(value) <= 256:
        raise ValueError("Некорректная длина protobuf строки")
    return value.decode("utf-8", errors="strict")


def _positive_int64(fields, number):
    value = fields.get(number)
    if not isinstance(value, int) or not 0 < value < (1 << 63):
        raise ValueError(f"Некорректное положительное int64 поле {number}")
    return value


def decode_spot_kline(message):
    """Возвращает (StreamKey, raw OHLCV) либо None для другого публичного канала."""
    if not isinstance(message, bytes) or not 0 < len(message) <= MAX_MESSAGE_BYTES:
        raise ValueError("Некорректная длина бинарного MEXC Spot сообщения")
    wrapper = _fields(message, _WRAPPER_TYPES)
    channel = _text(wrapper, 1)
    if not channel.startswith(CHANNEL_PREFIX):
        return None
    key = key_for_channel(channel)
    symbol = _text(wrapper, 3, required=False)
    if symbol is not None and symbol != key.symbol:
        raise ValueError("Protobuf symbol не совпадает с каналом")
    body = wrapper.get(308)
    if not isinstance(body, bytes) or not 0 < len(body) <= 4096:
        raise ValueError("Отсутствует publicSpotKline=308 или превышен размер")
    kline = _fields(body, _KLINE_TYPES)
    if _text(kline, 1) != MEXC_SPOT_WS_INTERVALS[key.interval]:
        raise ValueError("Protobuf interval не совпадает с каналом")
    start = _positive_int64(kline, 2) * 1000
    end = _positive_int64(kline, 9) * 1000
    if end != next_start(start, key.interval):
        raise ValueError("Окно protobuf свечи не совпадает с её таймфреймом")
    # Время генерации предпочтительнее sendTime: задержанная доставка не освежает replay.
    event = _positive_int64(wrapper, 5 if 5 in wrapper else 6)
    row = {"timestamp": start, "symbol": key.symbol, "interval": key.interval,
           "open": _text(kline, 3), "close": _text(kline, 4), "high": _text(kline, 5),
           "low": _text(kline, 6), "volume": _text(kline, 7), "event_time_ms": event}
    # windowEnd — граница периода, не final flag; закрытие подтверждает REST reconciliation.
    return key, row
