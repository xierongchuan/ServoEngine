"""Общий owner runtime: WS callbacks без REST, reconciliation и publisher отдельно."""

from __future__ import annotations

import copy
import logging
import os
import queue
import random
import threading
import time

import websocket

from .base import WebSocketConfig, WebSocketProvider, WebSocketState
from ..market_data import StreamKey, StreamStore, candle_gaps, closed_candles, interval_start, next_start
from ..market_data_storage import publish_market_snapshot
from ..public_market_rest import PublicRESTFetcher

logger = logging.getLogger(__name__)


class ManagedMarketDataProvider(WebSocketProvider):
    EXCHANGE = ""
    MARKET_TYPE = "perpetual"
    WS_URL = None
    INTERVALS = {}
    CACHE_SIZE = 600

    def __init__(self, *, store=None, settings=None, rest_fetcher=None, rate_limiters=None,
                 ws_factory=None, publisher=None, clock=time.time, monotonic=time.monotonic):
        super().__init__(WebSocketConfig(ws_url=self.WS_URL or ""))
        self.store = store if store is not None else StreamStore(settings=settings, clock=clock)
        self.settings = self.store.settings
        self.clock, self.monotonic = clock, monotonic
        self.rest_fetcher = rest_fetcher or PublicRESTFetcher(settings=self.settings, rate_limiters=rate_limiters)
        self.ws_factory = ws_factory or websocket.WebSocketApp
        data_dir = self.settings.get("data_dir", os.getenv("SERVO_DATA_DIR", "data"))
        self._custom_publisher = publisher
        self.publisher = publisher or (lambda snapshot: publish_market_snapshot(data_dir, snapshot))
        self._owner_pid = os.getpid()
        self._streams = set()
        self._epochs = {}
        self._revision = 0
        self._jobs = queue.Queue()
        self._pending = {}
        self._last_reconcile = {}
        self._ws_errors = {}
        self._dirty = set()
        self._retired = {}
        self._publish_event = threading.Event()
        self._stop_event = threading.Event()
        self._changed_event = threading.Event()
        self._threads = []
        self._ws = None
        self._connected = False
        self._generation = 0
        self._acked = set()
        self._ack_deadlines = {}
        self._requests = {}
        self._opened_at = 0.0
        self._last_transport_message = 0.0
        self._last_ping = 0.0
        self._pending_ping = None

    def _assert_owner(self):
        if os.getpid() != self._owner_pid:
            raise RuntimeError("WS runtime нельзя запускать/менять из worker; передавайте только Manager proxies")

    @property
    def streams(self):
        with self._lock:
            return sorted(self._streams)

    def start(self, symbols=None, interval="5m", *, streams=None):
        self._assert_owner()
        desired = streams if streams is not None else [StreamKey(self.EXCHANGE, self.MARKET_TYPE, symbol, interval) for symbol in symbols or []]
        if not self._running and any(thread.is_alive() for thread in self._threads):
            raise RuntimeError("Прежний WS runtime ещё останавливается; повторите start после завершения потоков")
        self.sync_subscriptions(desired)
        if self._running:
            return
        with self._lock:
            self._stop_event.clear()
            self._revision += 1
            self._pending.clear()
            self._jobs = queue.Queue()
            self._last_reconcile.clear()
            self._ws_errors.clear()
            self._ws = None
            for key in self._streams:
                self._epochs[key] = self._revision
                self.store.mark(key, status="recovering", error=None, ws_error=None, connected=False, subscribed=False)
                self.request_reconcile(key)
                self._emit(key)
        self._running = True
        targets = [self._reconcile_loop, self._watchdog_loop, self._publisher_loop]
        if self.WS_URL:
            targets.append(self._network_loop)
        self._threads = [threading.Thread(target=target, name=f"{self.EXCHANGE}-{target.__name__}", daemon=True) for target in targets]
        for thread in self._threads:
            thread.start()

    def refresh_settings(self, *, rate_limiters=None):
        self._assert_owner()
        with self._lock:
            self.settings = self.store.settings
            if isinstance(self.rest_fetcher, PublicRESTFetcher):
                limiters = self.rest_fetcher.rate_limiters if rate_limiters is None else rate_limiters
                self.rest_fetcher = PublicRESTFetcher(settings=self.settings, rate_limiters=limiters,
                                                     request=self.rest_fetcher.request, sleep=self.rest_fetcher.sleep)
            if self._custom_publisher is None:
                data_dir = self.settings.get("data_dir", os.getenv("SERVO_DATA_DIR", "data"))
                self.publisher = lambda snapshot: publish_market_snapshot(data_dir, snapshot)
            for key in self._streams:
                self.request_reconcile(key)
                self._emit(key)

    def sync_subscriptions(self, streams):
        self._assert_owner()
        desired = {StreamKey.from_value(key) for key in streams}
        for key in desired:
            if (key.exchange, key.market_type) != (self.EXCHANGE, self.MARKET_TYPE):
                raise ValueError(f"Поток {key.id} относится к другому provider")
            if self.WS_URL and key.interval not in self.INTERVALS:
                raise ValueError(f"WS не поддерживает таймфрейм {key.id}")
        with self._lock:
            added, removed = desired - self._streams, self._streams - desired
            if not added and not removed:
                return
            self._revision += 1
            ws = self._ws if self._connected else None
            for key in removed:
                self._acked.discard(key)
                self._ack_deadlines.pop(key, None)
                self._epochs.pop(key, None)
                self._pending.pop(key, None)
                self._last_reconcile.pop(key, None)
                self._ws_errors.pop(key, None)
                self._retired[key.id] = self.store.mark(key, status="stopped", connected=False, subscribed=False)
                self.store.remove(key)
                self._dirty.discard(key)
                self._publish_event.set()
                if ws:
                    try:
                        self._send_subscription(ws, key, subscribe=False)
                    except Exception as exc:
                        logger.warning("⚠️ Не удалось отписать %s: %s", key.id, exc)
                        ws.close()
            self._streams = desired
            self._symbols = sorted({key.symbol for key in desired})
            for key in added:
                self._epochs[key] = self._revision
                self.store.ensure(key)
                self.store.mark(key, transport="ws" if self.WS_URL else "rest", status="recovering")
                self._emit(key)
                self.request_reconcile(key)
                if ws:
                    self._send_subscription(ws, key)
            self._changed_event.set()

    def subscribe(self, symbols, interval=None):
        if interval is None:
            intervals = {key.interval for key in self.streams}
            if len(intervals) > 1:
                raise ValueError("При нескольких TF нужно явно передать interval подписки")
            interval = next(iter(intervals), self._interval)
        self.sync_subscriptions(set(self.streams) | {StreamKey(self.EXCHANGE, self.MARKET_TYPE, s, interval) for s in symbols})

    def unsubscribe(self, symbols):
        from ..market_data import canonical_symbol
        symbols = {canonical_symbol(s) for s in symbols}
        self.sync_subscriptions([key for key in self.streams if key.symbol not in symbols])

    def get_klines(self, symbol, limit=288, interval=None, *, closed_only=False):
        from ..market_data import select_stream, evaluate_snapshot
        value = select_stream(self.store.cache, symbol, interval, exchange=self.EXCHANGE, market_type=self.MARKET_TYPE)
        if not value or not evaluate_snapshot(value, now=self.clock())["ready"]:
            return []
        rows = closed_candles(value["candles"], value["stream"]["interval"], now=self.clock()) if closed_only else value["candles"]
        return copy.deepcopy(rows[-limit:]) if limit > 0 and len(rows) >= limit and not candle_gaps(rows[-limit:], value["stream"]["interval"]) else []

    def is_ready(self, symbol, interval=None):
        from ..market_data import select_stream, evaluate_snapshot
        value = select_stream(self.store.cache, symbol, interval, exchange=self.EXCHANGE, market_type=self.MARKET_TYPE)
        return bool(value and evaluate_snapshot(value, now=self.clock())["ready"])

    def wait_ready(self, timeout=30):
        deadline = self.monotonic() + timeout
        while self.monotonic() < deadline and not self._stop_event.is_set():
            if self.streams and all(self.store.snapshot(key)["ready"] for key in self.streams):
                return True
            self._stop_event.wait(0.05)
        return False

    def stop(self):
        self._assert_owner()
        self._running = False
        self._stop_event.set()
        self._changed_event.set()
        self._publish_event.set()
        with self._lock:
            self._connected = False
            self._revision += 1
            self._pending.clear()
            ws = self._ws
            for key in self._streams:
                self._epochs[key] = self._revision
                self.store.mark(key, status="stopped", connected=False, subscribed=False)
                self._emit(key)
        if ws:
            ws.close()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=self.settings["rest_timeout_seconds"] + 1)
        self._flush_publish()
        self._set_state(WebSocketState.DISCONNECTED)

    def request_reconcile(self, key, *, start_ms=None, end_ms=None):
        self._assert_owner()
        key = StreamKey.from_value(key)
        with self._lock:
            if key not in self._streams:
                return False
            if key in self._pending:
                old_start, old_end, epoch = self._pending[key]
                start_ms = None if old_start is None or start_ms is None else min(old_start, start_ms)
                end_ms = None if old_end is None or end_ms is None else max(old_end, end_ms)
                self._pending[key] = (start_ms, end_ms, epoch)
            else:
                self._pending[key] = (start_ms, end_ms, self._epochs[key])
                self._jobs.put(key)
            self._last_reconcile[key] = self.monotonic()
            return True

    def reconcile_once(self, key, *, start_ms=None, end_ms=None, epoch=None):
        """Синхронный шаг для background worker и offline-тестов, не для WS callback."""
        key = StreamKey.from_value(key)
        with self._lock:
            if key not in self._streams or self._stop_event.is_set():
                return
            epoch = self._epochs[key] if epoch is None else epoch
            snapshot = self.store.snapshot(key)
        started = self.clock()
        end_ms = int(started * 1000) + 1 if end_ms is None else end_ms
        if start_ms is None:
            if len(snapshot["candles"]) < self.settings["history_limit"]:
                start_ms = next_start(interval_start(end_ms - 1, key.interval), key.interval, -self.settings["history_limit"] + 1)
            else:
                candidates = [gap["start_ms"] for gap in snapshot["gaps"]]
                # Предыдущая свеча ещё могла не получить final update.
                candidates.append(snapshot["candles"][-2 if len(snapshot["candles"]) > 1 else -1]["timestamp"])
                start_ms = min(candidates)
        try:
            rows = self.rest_fetcher(key, start_ms=start_ms, end_ms=end_ms, limit=self.settings["history_limit"])
            if not rows:
                raise ValueError("Публичный REST вернул пустую историю")
            with self._lock:
                if self._stop_event.is_set() or self._epochs.get(key) != epoch:
                    return
                status = "live" if self._connected and key in self._acked and key not in self._ws_errors else "rest"
                self.store.merge(key, rows, source="rest", received_at=started, status=status)
                self._emit(key)
        except Exception as exc:
            logger.warning("⚠️ Не удалось восстановить %s: %s", key.id, exc)
            with self._lock:
                if not self._stop_event.is_set() and self._epochs.get(key) == epoch:
                    self.store.mark(key, status="error", error=str(exc))
                    self._emit(key)

    def _reconcile_loop(self):
        while not self._stop_event.is_set():
            try:
                key = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                job = self._pending.pop(key, None)
            if job:
                self.reconcile_once(key, start_ms=job[0], end_ms=job[1], epoch=job[2])

    def _emit(self, key):
        with self._lock:
            self._dirty.add(key)
            self._publish_event.set()

    def _flush_publish(self):
        with self._lock:
            keys = list(self._dirty)
            self._dirty.clear()
            snapshots = list(self._retired.values())
            self._retired.clear()
            snapshots.extend(self.store.snapshot(key) for key in keys if key in self._streams)
        for snapshot in snapshots:
            try:
                self.publisher(snapshot)
            except Exception as exc:
                logger.error("❌ Не удалось атомарно опубликовать %s: %s", snapshot["stream"], exc)
                key = StreamKey.from_value(snapshot["stream"])
                with self._lock:
                    if key in self._streams:
                        self._dirty.add(key)
                    else:
                        self._retired.setdefault(key.id, snapshot)

    def _publisher_loop(self):
        while not self._stop_event.is_set():
            self._publish_event.wait(0.5)
            self._publish_event.clear()
            self._flush_publish()

    def _network_loop(self):
        delay = self.settings["reconnect_delay_seconds"]
        while not self._stop_event.is_set():
            if not self.streams:
                self._changed_event.wait(0.5)
                self._changed_event.clear()
                continue
            self._set_state(WebSocketState.CONNECTING)
            self._opened_at = None
            try:
                ws = self.ws_factory(self.WS_URL, on_open=self._on_open, on_message=self._on_message,
                                     on_error=self._on_error, on_close=self._on_close, on_pong=self._on_pong)
                with self._lock:
                    self._ws = ws
                ws.run_forever()
            except Exception as exc:
                logger.warning("⚠️ WS %s: ошибка соединения: %s", self.EXCHANGE, exc)
            finally:
                self._connection_lost()
            if self._stop_event.is_set():
                return
            stable = self._opened_at is not None and self.monotonic() - self._opened_at >= self.settings["heartbeat_timeout_seconds"]
            delay = self.settings["reconnect_delay_seconds"] if stable else delay
            self._set_state(WebSocketState.RECONNECTING)
            # Задержка применяется и после штатного возврата run_forever, не только exception.
            self._stop_event.wait(delay * random.uniform(0.8, 1.2))
            delay = min(delay * 2, self.settings["max_reconnect_delay_seconds"])

    def _connection_lost(self):
        with self._lock:
            self._connected = False
            self._acked.clear()
            self._ack_deadlines.clear()
            self._requests.clear()
            self._pending_ping = None
            if self._stop_event.is_set():
                return
            for key in self._streams:
                self.store.mark(key, status="recovering", connected=False, subscribed=False)
                self._emit(key)
                self.request_reconcile(key)

    def _on_open(self, ws):
        with self._lock:
            if ws is not self._ws or self._stop_event.is_set():
                return
            self._generation += 1
            self._connected = True
            self._opened_at = self.monotonic()
            self._last_transport_message = self._opened_at
            self._last_ping = self._opened_at
            self._pending_ping = None
            self._acked.clear()
            self._ack_deadlines.clear()
            self._requests.clear()
            for key in self._streams:
                self.store.mark(key, status="recovering", connected=True, subscribed=False)
                self._send_subscription(ws, key)
                self.request_reconcile(key)
                self._emit(key)
        self._set_state(WebSocketState.CONNECTED)
        logger.info("✅ WS %s подключён: поколение %s, потоков %s", self.EXCHANGE, self._generation, len(self._streams))

    def _send_subscription(self, ws, key, subscribe=True):
        request_id = f"{self._generation}:{key.id}"
        if subscribe:
            self._requests[request_id] = key
            self._ack_deadlines[key] = self.monotonic() + self.settings["subscription_timeout_seconds"]
        self.send_subscription(ws, key, request_id, subscribe)

    def acknowledge(self, key):
        with self._lock:
            if key in self._streams:
                self._acked.add(key)
                self._ack_deadlines.pop(key, None)
                self.store.mark(key, subscribed=True)
                self._emit(key)

    def acknowledge_request(self, request_id, *, error=None):
        with self._lock:
            key = self._requests.get(str(request_id))
            if key is None:
                return
            if error:
                self.store.mark(key, status="error", error=str(error))
                self._emit(key)
            else:
                self.acknowledge(key)

    def received_pong(self):
        with self._lock:
            self._pending_ping = None

    def _on_pong(self, ws, message):
        with self._lock:
            if ws is self._ws and not self._stop_event.is_set():
                self._last_transport_message = self.monotonic()
                self.received_pong()

    def accept_candle(self, ws, key, row):
        with self._lock:
            if ws is not self._ws or key not in self._streams or self._stop_event.is_set():
                return
            try:
                # Первый валидный kline — также неявное подтверждение подписки.
                before = self.store.snapshot(key)
                previous = before["candles"]
                fault = self._ws_errors.get(key)
                # Пока WS неисправен, только принятый свежий tail возвращает статус live.
                # Старый/replayed валидный кадр не доказывает восстановление потока.
                status = "rest" if fault and before["status"] == "rest" else None
                snapshot = self.store.merge(key, [row], source="ws", status=status)
                if (fault and snapshot["last_valid_update"] > (before.get("last_valid_update") or 0)
                        and snapshot["candles"][-1]["source"] == "ws"):
                    self._ws_errors.pop(key)
                    snapshot = self.store.mark(key, ws_error=None,
                                               status="live" if snapshot["status"] == "rest" else snapshot["status"])
                    logger.info("✅ WS-поток %s восстановлен после %s отклонённых свечей", key.id, fault["rejected"])
                self.acknowledge(key)
                if snapshot["gaps"]:
                    self.request_reconcile(key, start_ms=snapshot["gaps"][0]["start_ms"])
                elif previous and snapshot["candles"][-1]["timestamp"] > previous[-1]["timestamp"]:
                    # Смена периода не доказывает final OHLCV предыдущей свечи.
                    self.request_reconcile(key, start_ms=previous[-1]["timestamp"])
                self._emit(key)
                self._notify_kline(key.id, row)
            except (TypeError, ValueError, OverflowError) as exc:
                fault = self._ws_errors.get(key)
                if fault:
                    # Кадр уже отвергнут строгим валидатором. Не сбиваем здоровый REST
                    # и не добавляем запрос на каждый тик; watchdog продолжает сверку.
                    fault["rejected"] += 1
                    return
                self._ws_errors[key] = {"rejected": 1}
                logger.warning("⚠️ Отклонена некорректная свеча %s: %s; восстанавливаем через REST", key.id, exc)
                self.store.mark(key, status="error", error=str(exc), ws_error=str(exc))
                self.request_reconcile(key)
                self._emit(key)

    def _on_message(self, ws, message):
        with self._lock:
            if ws is not self._ws or self._stop_event.is_set():
                return
            self._last_transport_message = self.monotonic()
        try:
            with self._lock:
                if ws is not self._ws or self._stop_event.is_set():
                    return
                self.handle_message(ws, message)
        except Exception as exc:
            logger.warning("⚠️ Отклонено WS сообщение %s: %s", self.EXCHANGE, exc)
            with self._lock:
                if ws is self._ws and not self._stop_event.is_set():
                    for key in self._streams:
                        self.store.mark(key, status="error", error=str(exc))
                        self.request_reconcile(key)
                        self._emit(key)

    def _on_error(self, ws, exc):
        if ws is self._ws:
            logger.warning("⚠️ WS %s: %s", self.EXCHANGE, exc)
            self._notify_error(str(exc))

    def _on_close(self, ws, status, message):
        if ws is self._ws:
            logger.warning("⚠️ WS %s закрыт: %s %s", self.EXCHANGE, status, message)
            self._connection_lost()

    def watchdog_once(self):
        now = self.monotonic()
        close_ws = None
        with self._lock:
            for key in self._streams:
                snapshot = self.store.snapshot(key)
                if bool(self.store.ready.get(key.id)) != snapshot["ready"]:
                    self.store.mark(key)
                    self._emit(key)
                # Период reconciliation — верхняя граница; REST fallback не должен
                # быть stale половину цикла при reconcile_interval > max_age.
                due = min(self.settings["reconcile_interval_seconds"], self.settings["max_age_seconds"] / 2)
                if now - self._last_reconcile.get(key, -float("inf")) >= due:
                    self.request_reconcile(key)
            ws = self._ws
            if not self._connected or ws is None:
                return
            if any(now >= deadline for deadline in self._ack_deadlines.values()):
                logger.warning("⚠️ WS %s: истёк timeout подтверждения подписки", self.EXCHANGE)
                close_ws = ws
            elif self._pending_ping is not None and now - self._pending_ping >= self.settings["heartbeat_timeout_seconds"]:
                logger.warning("⚠️ WS %s: не получен heartbeat ACK", self.EXCHANGE)
                close_ws = ws
            elif now - self._last_transport_message >= self.settings["heartbeat_timeout_seconds"]:
                close_ws = ws
            elif self._pending_ping is None and now - self._last_ping >= self.settings["heartbeat_interval_seconds"]:
                try:
                    self.send_ping(ws)
                    self._last_ping = now
                    self._pending_ping = now
                except Exception as exc:
                    logger.warning("⚠️ WS %s: heartbeat не отправлен: %s", self.EXCHANGE, exc)
                    close_ws = ws
        if close_ws:
            close_ws.close()
            self._connection_lost()

    def _watchdog_loop(self):
        while not self._stop_event.wait(0.25):
            self.watchdog_once()

    def send_subscription(self, ws, key, request_id, subscribe):
        raise NotImplementedError

    def send_ping(self, ws):
        raise NotImplementedError

    def handle_message(self, ws, message):
        raise NotImplementedError


class RESTPollingProvider(ManagedMarketDataProvider):
    """Явный public REST fallback: protobuf WS не заявляется как реализованный."""
    def __init__(self, exchange, market_type, **kwargs):
        self.EXCHANGE, self.MARKET_TYPE = exchange, market_type
        super().__init__(**kwargs)
