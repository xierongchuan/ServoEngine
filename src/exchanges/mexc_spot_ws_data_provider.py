"""Публичный MEXC Spot protobuf kline WS; общий reducer и фоновый REST backfill."""

import json

from src.exchanges.market_data import StreamKey
from src.exchanges.ws.mexc_spot_proto import (
    MAX_MESSAGE_BYTES, MEXC_SPOT_WS_INTERVALS, channel_for, decode_spot_kline,
)
from src.exchanges.ws.runtime import ManagedMarketDataProvider


class MEXCSpotWebSocketDataProvider(ManagedMarketDataProvider):
    EXCHANGE = "mexc"
    MARKET_TYPE = "spot"
    WS_URL = "wss://wbs-api.mexc.com/ws"
    INTERVALS = MEXC_SPOT_WS_INTERVALS
    MAX_SUBSCRIPTIONS = 30

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wire_sequence = 0
        self._wire_requests = {}
        self._ping_id = None

    def sync_subscriptions(self, streams):
        desired = {StreamKey.from_value(key) for key in streams}
        if len(desired) > self.MAX_SUBSCRIPTIONS:
            raise ValueError("MEXC Spot допускает 30 каналов на WS; используйте factory shards")
        with self._lock:
            super().sync_subscriptions(desired)
            self._wire_requests = {identifier: value for identifier, value in self._wire_requests.items()
                                   if value[0] in desired}

    def _next_wire_id(self):
        # ID не переиспользуется между reconnect и unsubscribe/readd.
        self._wire_sequence += 1
        if self._wire_sequence >= (1 << 32):
            raise RuntimeError("Исчерпаны unsigned ID MEXC Spot; пересоздайте provider")
        return self._wire_sequence

    def _on_open(self, ws):
        with self._lock:
            if ws is self._ws and not self._stop_event.is_set():
                self._wire_requests.clear()
                self._ping_id = None
            super()._on_open(ws)

    def send_subscription(self, ws, key, request_id, subscribe):
        identifier = self._next_wire_id()
        if subscribe:
            self._wire_requests[identifier] = (key, self._epochs[key], self._generation)
        ws.send(json.dumps({"method": "SUBSCRIPTION" if subscribe else "UNSUBSCRIPTION",
                            "params": [channel_for(key)], "id": identifier}))

    def send_ping(self, ws):
        # Документированный и проверенный wire PONG имеет id=0: сервер не отражает ID PING.
        # ACK относится только к pending heartbeat текущего socket; старое поколение отсекает runtime.
        self._ping_id = 0
        ws.send(json.dumps({"method": "PING"}))

    def _on_pong(self, ws, message):
        # RFC PONG не подтверждает application-level JSON PING.
        with self._lock:
            if ws is self._ws and not self._stop_event.is_set():
                self._last_transport_message = self.monotonic()

    def handle_message(self, ws, message):
        if isinstance(message, bytes):
            decoded = decode_spot_kline(message)
            if decoded is not None:
                self.accept_candle(ws, *decoded)
            return
        if not isinstance(message, str) or len(message) > MAX_MESSAGE_BYTES:
            raise ValueError("Некорректный MEXC Spot control frame")
        payload = json.loads(message)
        if not isinstance(payload, dict):
            raise ValueError("MEXC Spot control frame должен быть объектом")
        # RFC ping обрабатывает websocket-client. Серверный JSON PING не является ACK нашего PING.
        if payload.get("method") == "PING":
            ws.send(json.dumps({"method": "PONG", **({"id": payload["id"]} if "id" in payload else {})}))
            return
        identifier = payload.get("id")
        if not isinstance(identifier, int) or isinstance(identifier, bool):
            return
        code, message = payload.get("code"), payload.get("msg")
        if not isinstance(code, int) or isinstance(code, bool):
            return
        if message == "PONG":
            if code == 0 and self._pending_ping is not None and identifier == self._ping_id:
                self.received_pong()
                self._ping_id = None
            return
        pending = self._wire_requests.get(identifier)
        if pending is None:
            return
        key, epoch, generation = pending
        if key not in self._streams or epoch != self._epochs[key] or generation != self._generation:
            self._wire_requests.pop(identifier, None)
            return
        if code == 0:
            # Успешный ACK подтверждает только запрошенный канал, никогда весь набор подписок.
            if message != channel_for(key):
                return
            self.acknowledge(key)
        else:
            self._acked.discard(key)
            self.store.mark(key, status="error", error=f"MEXC Spot подписка отклонена: {code} {message}", subscribed=False)
            self.request_reconcile(key)
            self._emit(key)
        self._wire_requests.pop(identifier, None)
