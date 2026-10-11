"""Ограниченный private WS-сеанс: только доказательства связи attached защиты, без торговли."""

import copy
import gzip
import io
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import quote

import websocket

from src.exchanges.errors import ExchangeStateUnavailableError


@dataclass(frozen=True)
class BingXListenKey:
    """Токен существует только в памяти; журнал получает лишь имя подтверждённой схемы."""
    token: str = field(repr=False)
    schema: str

    @classmethod
    def from_response(cls, response):
        if not isinstance(response, dict):
            raise ExchangeStateUnavailableError("GRID: listenKey response должен быть объектом")
        top = "listenKey" in response
        nested = isinstance(response.get("data"), dict) and "listenKey" in response["data"]
        if top == nested or "code" not in response and set(response) != {"listenKey"}:
            raise ExchangeStateUnavailableError("GRID: listenKey response имеет неизвестную/неоднозначную схему")
        if "code" in response and (type(response["code"]) is not int or response["code"] != 0):
            raise ExchangeStateUnavailableError("GRID: listenKey response не подтверждён успешным code")
        token = response["listenKey"] if top else response["data"]["listenKey"]
        if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) <= 32 for c in token):
            raise ExchangeStateUnavailableError("GRID: listenKey отсутствует или имеет неверный тип/формат")
        return cls(token=token, schema="top_level" if top else "data")


class BingXAttachmentStream:
    def __init__(self, client, timeout):
        self.timeout = min(30.0, max(1.0, float(timeout)))
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.condition = threading.Condition()
        self.events = deque(maxlen=256)
        self.failed = False
        self.socket = None
        self.thread = None
        client._allow_mutation()
        self.listen_receipt = client.create_grid_listen_key()
        if not isinstance(self.listen_receipt, BingXListenKey):
            raise ExchangeStateUnavailableError("GRID: typed listenKey receipt не получен")
        token = self.listen_receipt.token
        host = "vst-open-api-ws.bingx.com" if client._settings()["execution_mode"] == "demo" else "open-api-swap.bingx.com"
        try:
            # Redirect запрещён, токен не попадает в journal/логи/текст исключения.
            self.socket = websocket.create_connection(f"wss://{host}/swap-market?listenKey={quote(token, safe='')}",
                                                      timeout=self.timeout, redirect_limit=0)
            self.socket.settimeout(1)
            self.thread = threading.Thread(target=self._receive, daemon=True, name="grid-attached-ws")
            self.thread.start()
            # Private stream не требует subscribe и не документирует startup ACK.
            # Успешный WSS handshake с REST listenKey — готовность транспорта, не доказательство защиты.
            if not self.ready.wait(self.timeout) or self.failed:
                raise ExchangeStateUnavailableError("GRID: private WS не подтвердил готовность до entry")
        except Exception:
            self.close()
            raise ExchangeStateUnavailableError("GRID: private WS недоступен; entry не отправлен") from None

    def _receive(self):
        try:
            self.ready.set()
            while not self.stopped.is_set():
                try:
                    message = self.socket.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not message:
                    raise ValueError("WS закрыт")
                if len(message) > 1_000_000:
                    raise ValueError("Слишком большой WS frame")
                if isinstance(message, bytes):
                    if message[:2] == b"\x1f\x8b":
                        with gzip.GzipFile(fileobj=io.BytesIO(message)) as compressed:
                            message = compressed.read(1_000_001)
                    message = message.decode()
                if len(message) > 1_000_000:
                    raise ValueError("Слишком большой WS frame")
                if message == "Ping":
                    self.socket.send("Pong")
                    continue
                event = json.loads(message)
                if not isinstance(event, dict) or event.get("code", 0) != 0:
                    raise ValueError("Ошибка private WS")
                if "e" in event or event.get("code") == 0:
                    self.ready.set()
                order = event.get("o")
                if event.get("e") in {"TRADE_UPDATE", "ORDER_TRADE_UPDATE"} and isinstance(order, dict) and order.get("li"):
                    with self.condition:
                        if len(self.events) == self.events.maxlen:
                            raise ValueError("Переполнение доказательств WS")
                        self.events.append(copy.deepcopy(order))
                        self.condition.notify_all()
        except Exception:
            if not self.stopped.is_set():
                self.failed = True
                self.ready.set()
                with self.condition:
                    self.condition.notify_all()

    def collect(self, parent_id):
        deadline = time.monotonic() + self.timeout
        with self.condition:
            while True:
                linked = [copy.deepcopy(e) for e in self.events if str(e.get("li")) == str(parent_id)]
                kinds = {e.get("o") for e in linked}
                if self.failed:
                    return []
                if {"STOP_MARKET", "TAKE_PROFIT_MARKET"}.issubset(kinds):
                    return linked
                if time.monotonic() >= deadline:
                    return linked
                self.condition.wait(max(0, deadline - time.monotonic()))

    def close(self):
        self.stopped.set()
        if self.socket is not None:
            self.socket.close()
        if self.thread is not None:
            self.thread.join(timeout=2)
