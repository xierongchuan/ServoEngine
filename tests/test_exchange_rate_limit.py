"""SQLite limiter: multiprocessing, durable cooldown и отсутствие реальных HTTP."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import multiprocessing
from pathlib import Path
import pickle
import sqlite3
import time

import pytest
import requests

from src.exchanges.errors import ExchangeAPIError, ExchangeStateUnavailableError, UnknownOrderStateError
from src.exchanges.rate_limit import ExchangeRateLimiter, acquire_rate_limits, get_exchange_rate_limiters
from src.exchanges import rate_limit
from src.exchanges.impl.mexc_transport import MEXCFuturesTransport, MEXCSpotTransport
from test_mexc import config, Response, Session


def _worker_acquire(limiter, barrier, queue):
    try:
        barrier.wait(timeout=10)
        limiter.acquire()
        queue.put(("ok", time.monotonic()))
    except Exception as exc:
        queue.put(("error", type(exc).__name__))


def _worker_cooldown(limiter, queue):
    try:
        limiter.cooldown(120)
        queue.put("ok")
    except Exception as exc:
        queue.put(type(exc).__name__)


def _worker_initialize(path, barrier, queue):
    try:
        barrier.wait(timeout=10)
        limiter = ExchangeRateLimiter("test", capacity=10, period=1, data_dir=path, max_wait=3)
        limiter.acquire()
        queue.put("ok")
    except Exception as exc:
        queue.put(type(exc).__name__)


def _finish(processes):
    try:
        for process in processes:
            process.join(timeout=10)
            assert not process.is_alive(), "Дочерний процесс limiter завис"
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


def _event_weight(limiter):
    with sqlite3.connect(limiter.db_path) as connection:
        return connection.execute("SELECT COALESCE(SUM(weight), 0) FROM events WHERE key=?", (limiter._key,)).fetchone()[0]


@pytest.mark.parametrize("method", ["spawn", "fork"])
def test_multiprocessing_respects_single_shared_sliding_window(tmp_path, method):
    ctx = multiprocessing.get_context(method)
    limiter = ExchangeRateLimiter("test", capacity=2, period=0.2, max_wait=3, data_dir=tmp_path)
    queue, barrier = ctx.Queue(), ctx.Barrier(6)
    processes = [ctx.Process(target=_worker_acquire, args=(limiter, barrier, queue)) for _ in range(6)]
    for process in processes:
        process.start()
    _finish(processes)
    results = [queue.get(timeout=2) for _ in processes]
    queue.close()
    assert all(status == "ok" for status, _ in results)
    times = sorted(value for _, value in results)
    assert all(times[i + 2] - times[i] >= 0.175 for i in range(4))


def test_concurrent_first_initialization_does_not_reset_budgets(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue, barrier = ctx.Queue(), ctx.Barrier(4)
    processes = [ctx.Process(target=_worker_initialize, args=(str(tmp_path), barrier, queue)) for _ in range(4)]
    for process in processes:
        process.start()
    _finish(processes)
    assert [queue.get(timeout=2) for _ in processes] == ["ok"] * 4
    queue.close()
    limiter = ExchangeRateLimiter("test", capacity=10, period=1, data_dir=tmp_path)
    assert _event_weight(limiter) == 4


def test_fork_after_parent_acquire_preserves_budget_and_cooldown(tmp_path):
    limiter = ExchangeRateLimiter("test", capacity=1, period=120, max_wait=0.01, data_dir=tmp_path)
    limiter.acquire()
    ctx = multiprocessing.get_context("fork")
    queue, barrier = ctx.Queue(), ctx.Barrier(1)
    process = ctx.Process(target=_worker_acquire, args=(limiter, barrier, queue))
    process.start()
    _finish([process])
    assert queue.get(timeout=2) == ("error", "ExchangeStateUnavailableError")
    queue.close()


def test_cooldown_120_is_durable_shared_between_processes_and_buckets(tmp_path):
    parent = ExchangeRateLimiter("mexc", "public", data_dir=tmp_path, max_wait=0.05)
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_worker_cooldown, args=(parent, queue))
    process.start()
    _finish([process])
    assert queue.get(timeout=2) == "ok"
    queue.close()
    recreated = ExchangeRateLimiter("mexc", "private", data_dir=tmp_path, max_wait=0.05, account_id="another-account")
    started = time.monotonic()
    with pytest.raises(ExchangeStateUnavailableError):
        recreated.acquire()
    assert time.monotonic() - started < 0.3
    with sqlite3.connect(parent.db_path) as connection:
        until = connection.execute("SELECT until FROM cooldowns WHERE key=?", (parent._cooldown_key,)).fetchone()[0]
        logical = connection.execute("SELECT logical FROM clock_state WHERE id=1").fetchone()[0]
    assert (until - logical) / 1e9 > 118


def test_short_cooldown_never_reduces_persisted_long_cooldown(tmp_path, monkeypatch):
    tick = [1_000_000_000]
    monkeypatch.setattr(rate_limit, "_clock_sample", lambda: ("boot-a", tick[0]))
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, max_wait=0, data_dir=tmp_path)
    limiter.cooldown(120)
    tick[0] += 1_000_000_000
    limiter.cooldown(1)
    with sqlite3.connect(limiter.db_path) as connection:
        assert connection.execute("SELECT until FROM cooldowns").fetchone()[0] == 120_000_000_000


def test_atomic_reservation_does_not_spend_ip_if_private_budget_is_full(tmp_path):
    public = ExchangeRateLimiter("test", "public", 10, 120, data_dir=tmp_path, max_wait=0)
    private = ExchangeRateLimiter("test", "private", 1, 120, data_dir=tmp_path, max_wait=0)
    private.acquire()
    with pytest.raises(ExchangeStateUnavailableError):
        acquire_rate_limits([(public, 5), (private, 1)])
    assert _event_weight(public) == 0 and _event_weight(private) == 1


def test_weighted_window_waits_until_enough_weight_expires(tmp_path, monkeypatch):
    tick = [1_000_000_000]
    monkeypatch.setattr(rate_limit, "_clock_sample", lambda: ("boot-a", tick[0]))
    limiter = ExchangeRateLimiter("test", capacity=5, period=10, data_dir=tmp_path, max_wait=0)
    limiter.acquire(3)
    tick[0] += 1_000_000_000
    limiter.acquire(2)
    tick[0] += 9_000_000_000
    limiter.acquire(3)
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire(1)
    tick[0] += 1_000_000_000
    limiter.acquire(2)
    assert _event_weight(limiter) == 5


def test_duplicate_objects_for_one_bucket_are_not_double_charged(tmp_path):
    one = ExchangeRateLimiter("test", capacity=10, period=1, data_dir=tmp_path)
    two = ExchangeRateLimiter("test", capacity=10, period=1, data_dir=tmp_path)
    acquire_rate_limits([(one, 2), (two, 3)])
    assert _event_weight(one) == 3


def test_conflicting_bucket_config_is_failclosed(tmp_path):
    ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path)
    with pytest.raises(ExchangeStateUnavailableError):
        ExchangeRateLimiter("test", capacity=2, period=1, data_dir=tmp_path)


def test_sqlite_lock_has_bounded_wait_not_busy_spin(tmp_path, monkeypatch):
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, max_wait=0.15, data_dir=tmp_path)
    sleeps, original_sleep = [], rate_limit._sleep
    def observe(seconds):
        sleeps.append(seconds)
        original_sleep(seconds)
    monkeypatch.setattr(rate_limit, "_sleep", observe)
    with sqlite3.connect(limiter.db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(ExchangeStateUnavailableError):
            limiter.acquire()
        elapsed = time.monotonic() - started
        assert elapsed < 0.4 and len(sleeps) < 8
    assert all(delay > 0 for delay in sleeps)


def test_quota_wait_uses_sleep_and_max_wait_is_observed(tmp_path, monkeypatch):
    limiter = ExchangeRateLimiter("test", capacity=1, period=0.08, max_wait=0.3, data_dir=tmp_path)
    sleeps, original_sleep = [], rate_limit._sleep
    monkeypatch.setattr(rate_limit, "_sleep", lambda seconds: (sleeps.append(seconds), original_sleep(seconds)))
    limiter.acquire()
    started = time.monotonic()
    limiter.acquire()
    assert 0.06 <= time.monotonic() - started < 0.3
    assert 1 <= len(sleeps) <= 3 and min(sleeps) > 0


def test_wall_clock_jump_cannot_expire_quota_or_cooldown(tmp_path, monkeypatch):
    tick = [1_000_000_000]
    monkeypatch.setattr(rate_limit, "_clock_sample", lambda: ("boot-a", tick[0]))
    limiter = ExchangeRateLimiter("test", capacity=1, period=10, max_wait=0, data_dir=tmp_path)
    limiter.acquire()
    limiter.cooldown(120)
    monkeypatch.setattr(time, "time", lambda: 9_999_999_999)
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire()
    monkeypatch.setattr(time, "time", lambda: 1)
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire()
    tick[0] += 120_000_000_000
    limiter.acquire()


def test_monotonic_rollback_does_not_mint_credits(tmp_path, monkeypatch):
    tick = [10_000_000_000]
    monkeypatch.setattr(rate_limit, "_clock_sample", lambda: ("boot-a", tick[0]))
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, max_wait=0, data_dir=tmp_path)
    limiter.acquire()
    tick[0] = 1_000_000_000
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire()
    tick[0] = 10_500_000_000
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire()
    tick[0] = 11_000_000_000
    limiter.acquire()


def test_new_boot_conservatively_preserves_full_remaining_cooldown(tmp_path, monkeypatch):
    state = ["boot-a", 10_000_000_000]
    monkeypatch.setattr(rate_limit, "_clock_sample", lambda: tuple(state))
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, max_wait=0, data_dir=tmp_path)
    limiter.cooldown(120)
    state[:] = ["boot-b", 1_000_000_000]
    recreated = ExchangeRateLimiter("test", capacity=1, period=1, max_wait=0, data_dir=tmp_path)
    with pytest.raises(ExchangeStateUnavailableError):
        recreated.acquire()
    state[1] += 119_000_000_000
    with pytest.raises(ExchangeStateUnavailableError):
        recreated.acquire()
    state[1] += 1_000_000_000
    recreated.acquire()


@pytest.mark.parametrize("operation", ["acquire", "cooldown"])
def test_deleted_database_fails_closed_and_is_not_recreated(tmp_path, operation):
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path)
    Path(limiter.db_path).unlink()
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire() if operation == "acquire" else limiter.cooldown(120)
    with pytest.raises(ExchangeStateUnavailableError):
        ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path)
    assert not Path(limiter.db_path).exists()


def test_deleted_parent_directory_and_invalid_path_failclosed(tmp_path):
    with pytest.raises(ExchangeStateUnavailableError):
        ExchangeRateLimiter("test", capacity=1, period=1, db_path=tmp_path / "missing" / "limits.sqlite3")
    blocker = tmp_path / "file"
    blocker.write_text("не директория", encoding="utf-8")
    with pytest.raises(ExchangeStateUnavailableError):
        ExchangeRateLimiter("test", capacity=1, period=1, data_dir=blocker)


def test_damaged_schema_and_guard_failclosed(tmp_path):
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path)
    with sqlite3.connect(limiter.db_path) as connection:
        connection.execute("DROP TABLE clock_state")
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire()
    with pytest.raises(ExchangeStateUnavailableError):
        ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path)
    Path(limiter._guard_path).write_bytes(b"\xff\xff")
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.cooldown(120)


def test_account_and_ip_identifiers_are_hashed_not_stored_or_pickled(tmp_path):
    account = "private-stable-account-identifier"
    ip = "private-egress-label"
    limiter = ExchangeRateLimiter("mexc", "private", data_dir=tmp_path, account_id=account, ip_id=ip)
    limiter.acquire()
    limiter.cooldown(120)
    blob = pickle.dumps(limiter)
    for path in tmp_path.iterdir():
        if path.is_file():
            blob += path.read_bytes()
    assert account.encode() not in blob and ip.encode() not in blob
    assert hashlib.sha256(account.encode()).hexdigest().encode() not in blob


def test_uid_is_shared_across_egress_but_ip_budget_is_not_split_by_uid(tmp_path):
    a = ExchangeRateLimiter("test", "public", 1, 120, data_dir=tmp_path, account_id="uid-a", max_wait=0)
    b = ExchangeRateLimiter("test", "public", 1, 120, data_dir=tmp_path, account_id="uid-b", max_wait=0)
    a.acquire()
    with pytest.raises(ExchangeStateUnavailableError):
        b.acquire()
    private_a = ExchangeRateLimiter("test", "private", 1, 120, data_dir=tmp_path, account_id="uid-a", ip_id="egress-a", max_wait=0)
    private_b = ExchangeRateLimiter("test", "private", 1, 120, data_dir=tmp_path, account_id="uid-a", ip_id="egress-b", max_wait=0)
    private_a.acquire()
    with pytest.raises(ExchangeStateUnavailableError):
        private_b.acquire()


def test_threads_share_same_budget_without_inherited_connections(tmp_path):
    limiter = ExchangeRateLimiter("test", capacity=1, period=120, max_wait=0, data_dir=tmp_path)
    def attempt(_):
        try:
            limiter.acquire()
            return True
        except ExchangeStateUnavailableError:
            return False
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(attempt, range(4))) == 1


@pytest.mark.parametrize("kwargs", [dict(capacity=0), dict(capacity=True), dict(capacity=1.5), dict(period=0), dict(period=float("nan")), dict(max_wait=-1), dict(max_wait=float("inf"))])
def test_invalid_settings_are_rejected_without_db(tmp_path, kwargs):
    options = dict(capacity=1, period=1, data_dir=tmp_path)
    options.update(kwargs)
    with pytest.raises(ValueError):
        ExchangeRateLimiter("test", **options)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("weight", [0, -1, True, 1.5, 2])
def test_invalid_or_impossible_weight_is_rejected(tmp_path, weight):
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path)
    with pytest.raises(ValueError):
        limiter.acquire(weight)
    assert _event_weight(limiter) == 0


def test_factory_mexc_and_bingx_budgets_are_explicit(tmp_path):
    mexc = get_exchange_rate_limiters("mexc", data_dir=tmp_path)
    assert (mexc["public"].capacity, mexc["public"].period) == (300, 10)
    assert (mexc["private"].capacity, mexc["private"].period) == (500, 10)
    assert (mexc["orders"].capacity, mexc["orders"].period) == (12, 1)
    assert (mexc["/api/v1/*"].capacity, mexc["/api/v1/*"].period) == (20, 2)
    assert mexc["/api/v1/contract/kline/*"] is mexc["/api/v1/private/order/list/open_orders"]
    assert (mexc["/api/v1/private/stoporder/place"].capacity, mexc["/api/v1/private/stoporder/place"].period) == (5, 2)
    bingx = get_exchange_rate_limiters("bingx", data_dir=tmp_path)
    assert bingx["public"] is bingx["private"] is bingx["orders"]
    assert bingx["public"].capacity == 1 and bingx["public"].period == 1
    assert set(bingx) == {"public", "private", "orders", "bingx"}
    assert bingx["bingx"] is bingx["public"]


def test_env_data_dir_and_max_wait_are_observed_without_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SERVO_RATE_LIMIT_MAX_WAIT", "0.1")
    limiter = ExchangeRateLimiter("mexc")
    assert Path(limiter.db_path).parent == tmp_path and limiter.max_wait == 0.1


def test_mexc_default_auto_integration_and_signing_after_acquire(tmp_path, monkeypatch):
    transport = MEXCSpotTransport(config(rate_limit_data_dir=str(tmp_path)), Session([Response({"balances": []})]))
    transport._time_synced = True
    monkeypatch.setattr(transport, "_timestamp", lambda: 123)
    def acquire(method, endpoint, private):
        monkeypatch.setattr(transport, "_timestamp", lambda: 456)
    monkeypatch.setattr(transport, "_acquire", acquire)
    transport.request("GET", "/api/v3/account", private=True)
    assert dict(transport.session.calls[0][2]["params"])["timestamp"] == 456
    assert all(isinstance(transport.limiters[key], ExchangeRateLimiter) for key in ("public", "private", "orders"))


def test_mexc_atomic_budget_weights_and_futures_stop_budget(tmp_path):
    limiters = get_exchange_rate_limiters("mexc", data_dir=tmp_path, max_wait=0)
    transport = MEXCSpotTransport(config(), Session([Response({})]), rate_limiters=limiters)
    transport._time_synced = True
    transport.request("GET", "/api/v3/account", private=True)
    assert _event_weight(limiters["public"]) == _event_weight(limiters["private"]) == 10
    assert _event_weight(limiters["orders"]) == 0
    futures = MEXCFuturesTransport(config(), Session([Response({"success": True, "data": 88})]), limiters=limiters)
    futures._time_synced = True
    futures.request("POST", "/api/v1/private/stoporder/place", {}, private=True)
    assert _event_weight(limiters["orders"]) == _event_weight(limiters["/api/v1/*"]) == _event_weight(limiters["/api/v1/private/stoporder/place"]) == 1


def test_retry_after_120_blocks_new_transport_before_any_http(tmp_path):
    limiters = get_exchange_rate_limiters("mexc", data_dir=tmp_path, max_wait=0.05)
    session = Session([Response({"code": 429}, status=429, headers={"Retry-After": "120"})])
    transport = MEXCSpotTransport(config(), session, rate_limiters=limiters)
    transport._time_synced = True
    with pytest.raises(ExchangeAPIError):
        transport.request("POST", "/api/v3/order", {}, private=True, max_attempts=99)
    assert len(session.calls) == 1
    new_session = Session([])
    new_transport = MEXCFuturesTransport(config(), new_session, rate_limiters=get_exchange_rate_limiters("mexc", data_dir=tmp_path, max_wait=0.05))
    with pytest.raises(ExchangeStateUnavailableError):
        new_transport.request("GET", "/api/v1/contract/ping")
    assert new_session.calls == []
    with pytest.raises(ExchangeStateUnavailableError):
        transport.request("GET", "/api/v3/time")
    assert len(session.calls) == 1


def test_get_429_does_not_wait_120_or_send_again(tmp_path):
    session = Session([Response({}, status=429, headers={"Retry-After": "120"})])
    transport = MEXCSpotTransport(config(rate_limit_data_dir=str(tmp_path), rate_limit_max_wait=0.05), session)
    started = time.monotonic()
    with pytest.raises(ExchangeStateUnavailableError):
        transport.request("GET", "/api/v3/time")
    assert time.monotonic() - started < 0.3 and len(session.calls) == 1


@pytest.mark.parametrize("failure", [requests.exceptions.Timeout(), Response({}, status=503)])
def test_mutation_remains_one_send_unknown_with_real_shared_limiter(tmp_path, failure):
    session = Session([failure])
    transport = MEXCFuturesTransport(config(rate_limit_data_dir=str(tmp_path)), session)
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError):
        transport.request("POST", "/api/v1/private/order/create", {}, max_attempts=99)
    assert len(session.calls) == 1 and _event_weight(transport.limiters["orders"]) == 1


def test_limiter_filesystem_failure_blocks_mutation_before_http(tmp_path):
    session = Session([])
    transport = MEXCFuturesTransport(config(rate_limit_data_dir=str(tmp_path)), session)
    transport._time_synced = True
    Path(transport.limiters["public"].db_path).unlink()
    with pytest.raises(ExchangeStateUnavailableError):
        transport.request("POST", "/api/v1/private/order/create", {})
    assert session.calls == []


def test_empty_injection_cannot_disable_default_budgets(tmp_path):
    transport = MEXCSpotTransport(config(rate_limit_data_dir=str(tmp_path)), Session([]), limiters={})
    assert all(isinstance(transport.limiters[key], ExchangeRateLimiter) for key in ("public", "private", "orders"))


def test_public_reader_alias_reserves_same_futures_bucket(tmp_path):
    budgets = get_exchange_rate_limiters("mexc", data_dir=tmp_path, max_wait=0)
    for _ in range(20):
        budgets["mexc"].acquire()
    assert _event_weight(budgets["public"]) == 20
    assert _event_weight(budgets["/api/v1/*"]) == 20
    session = Session([])
    transport = MEXCFuturesTransport(config(), session, rate_limiters=budgets)
    with pytest.raises(ExchangeStateUnavailableError):
        transport.request("GET", "/api/v1/contract/kline/BTC_USDT")
    assert session.calls == []


def test_duplicate_bucket_objects_observe_strictest_max_wait(tmp_path):
    immediate = ExchangeRateLimiter("test", capacity=1, period=120, data_dir=tmp_path, max_wait=0)
    patient = ExchangeRateLimiter("test", capacity=1, period=120, data_dir=tmp_path, max_wait=5)
    immediate.acquire()
    started = time.monotonic()
    with pytest.raises(ExchangeStateUnavailableError):
        acquire_rate_limits([(immediate, 1), (patient, 1)])
    assert time.monotonic() - started < 0.2


def test_same_bucket_in_different_databases_cannot_hide_in_dedup(tmp_path):
    one = ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path / "a")
    two = ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path / "b")
    with pytest.raises(ExchangeStateUnavailableError):
        acquire_rate_limits([(one, 1), (two, 1)])
    assert _event_weight(one) == _event_weight(two) == 0


def test_corrupted_cooldown_fails_closed(tmp_path):
    limiter = ExchangeRateLimiter("test", capacity=1, period=1, data_dir=tmp_path)
    with sqlite3.connect(limiter.db_path) as connection:
        connection.execute("INSERT INTO cooldowns VALUES (?, ?)", (limiter._cooldown_key, "не число"))
    with pytest.raises(ExchangeStateUnavailableError):
        limiter.acquire()


def test_unavailable_boot_clock_fails_before_http(tmp_path, monkeypatch):
    transport = MEXCSpotTransport(config(rate_limit_data_dir=str(tmp_path)), Session([]))
    def broken_clock():
        raise OSError("boot clock недоступен")
    monkeypatch.setattr(rate_limit, "_clock_sample", broken_clock)
    with pytest.raises(ExchangeStateUnavailableError):
        transport.request("GET", "/api/v3/time")
    assert transport.session.calls == []


def test_retry_after_503_mutation_remains_unknown_and_shared(tmp_path):
    session = Session([Response({}, status=503, headers={"Retry-After": "120"})])
    budgets = get_exchange_rate_limiters("mexc", data_dir=tmp_path, max_wait=0.05)
    transport = MEXCFuturesTransport(config(), session, rate_limiters=budgets)
    transport._time_synced = True
    with pytest.raises(UnknownOrderStateError):
        transport.request("POST", "/api/v1/private/order/create", {}, max_attempts=99)
    assert len(session.calls) == 1
    with pytest.raises(ExchangeStateUnavailableError):
        ExchangeRateLimiter("mexc", data_dir=tmp_path, max_wait=0.05).acquire()


def test_injected_adapter_requires_cooldown_not_only_acquire():
    class IncompleteAdapter:
        def acquire(self, weight=1):
            pass
    with pytest.raises(ValueError):
        MEXCSpotTransport(config(), Session([]), limiters={"public": IncompleteAdapter()})


def test_spot_depth_doc_weight_is_three(tmp_path):
    transport = MEXCSpotTransport(config(rate_limit_data_dir=str(tmp_path)), Session([Response({})]))
    transport.request("GET", "/api/v3/depth", {"symbol": "BTCUSDT"})
    assert _event_weight(transport.limiters["public"]) == 3
