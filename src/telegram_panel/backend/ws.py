import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket
from src.utils.log_context import SecretRedactor

logger = logging.getLogger("panel.ws")


class ConnectionManager:
    def __init__(self, queue_size=64, send_timeout=2.0, max_connections=128) -> None:
        self._connections: set[WebSocket] = set()
        self._queues: dict[WebSocket, asyncio.Queue] = {}
        self._senders: dict[WebSocket, asyncio.Task] = {}
        self._closers: set[asyncio.Task] = set()
        self.queue_size = queue_size
        self.send_timeout = send_timeout
        self.max_connections = max_connections

    async def connect(self, websocket: WebSocket) -> None:
        # Авторизацию до этого вызова выполняет websocket endpoint.
        if websocket in self._connections:
            return
        if len(self._connections) + len(self._closers) >= self.max_connections:
            await asyncio.wait_for(websocket.close(code=1013), self.send_timeout)
            return
        await websocket.accept()
        self._connections.add(websocket)
        queue = asyncio.Queue(maxsize=self.queue_size)
        self._queues[websocket] = queue
        self._senders[websocket] = asyncio.create_task(self._send(websocket, queue))
        logger.info("WebSocket подключён (%d всего)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        existed = websocket in self._connections
        self._connections.discard(websocket)
        self._queues.pop(websocket, None)
        task = self._senders.pop(websocket, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        if existed:
            # Sender может быть отменён ДО первого запуска: его finally не выполнится.
            closer = asyncio.create_task(self._close(websocket))
            self._closers.add(closer)
            closer.add_done_callback(self._closers.discard)
            logger.info("WebSocket отключён (%d всего)", len(self._connections))

    async def _close(self, websocket):
        try:
            await asyncio.wait_for(websocket.close(code=1013), self.send_timeout)
        except (Exception, asyncio.CancelledError):
            pass

    async def _send(self, websocket, queue):
        try:
            while websocket in self._connections:
                message = await queue.get()
                await asyncio.wait_for(websocket.send_text(message), self.send_timeout)
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            self.disconnect(websocket)

    async def shutdown(self) -> None:
        senders = tuple(self._senders.values())
        for websocket in tuple(self._connections):
            self.disconnect(websocket)
        await asyncio.gather(*senders, *tuple(self._closers), return_exceptions=True)

    async def broadcast(self, event_type: str, data: Any = None) -> None:
        if not self._connections:
            return
        message = json.dumps(SecretRedactor().clean({"type": event_type, "data": data}), ensure_ascii=False)
        if len(message.encode("utf-8")) > 65536:
            logger.warning("WebSocket событие отклонено: превышен лимит размера")
            return
        # Никаких await в обходе; каждый клиент имеет собственный bounded sender.
        for websocket in tuple(self._connections):
            queue = self._queues.get(websocket)
            if queue is None:
                continue
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Переполнение требует reconnect/catch-up, а не тихой потери событий.
                self.disconnect(websocket)


manager = ConnectionManager()
