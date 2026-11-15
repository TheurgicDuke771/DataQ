"""Concurrent publish of a tick's scheduled runs (#2117).

The unit tests stub the publish. The last test publishes for real, from the pool, against a
local Redis, because a deadlock in concurrent `send_task` (#2111) only showed against a broker.
"""

import gc
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from structlog.testing import capture_logs

from backend.app.core.logging import request_id_var
from backend.app.services import run_dispatch, schedule_dispatch
from backend.app.services.schedule_dispatch import _Publisher, _Queued
from backend.app.worker.celery_app import celery_app, create_celery_app

Published = tuple[list[tuple[_Queued, str]], list[_Queued], bool]


def _queued(count: int) -> list[_Queued]:
    return [_Queued(run_id=uuid.uuid4(), schedule_id=uuid.uuid4()) for _ in range(count)]


def _publish(queued: list[_Queued], *, threaded: bool | None = None) -> Published:
    publisher = _Publisher(threaded=threaded)
    try:
        return publisher.publish(queued)
    finally:
        publisher.close()


def _tracking(delay: Callable[[], None]) -> tuple[Callable[[uuid.UUID], str], dict[str, Any]]:
    """A publish stub that records the peak number in flight and the threads used."""
    lock = threading.Lock()
    seen: dict[str, Any] = {"in_flight": 0, "peak": 0, "threads": set()}

    def _stub(run_id: uuid.UUID) -> str:
        with lock:
            seen["in_flight"] += 1
            seen["peak"] = max(seen["peak"], seen["in_flight"])
            seen["threads"].add(threading.current_thread().name)
        delay()
        with lock:
            seen["in_flight"] -= 1
        return f"task-{run_id}"

    return _stub, seen


def _burn(seconds: float) -> None:
    """Hold the CPU, as building a message does: no wait on anything."""
    end = time.thread_time() + seconds
    while time.thread_time() < end:
        pass


def test_a_slow_broker_batch_is_published_from_several_threads_in_its_own_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub, seen = _tracking(lambda: time.sleep(0.02))
    monkeypatch.setattr(run_dispatch, "dispatch_run", stub)
    queued = _queued(40)

    with capture_logs() as logs:
        sent, failed, broker_down = _publish(queued)

    assert [item for item, _ in sent] == queued
    assert all(task_id == f"task-{item.run_id}" for item, task_id in sent)
    assert failed == [] and broker_down is False
    assert 1 < seen["peak"] <= schedule_dispatch.PUBLISH_THREADS
    assert any(name.startswith("schedule-publish") for name in seen["threads"])
    (mode,) = [e for e in logs if e["event"] == "schedule_publish_mode"]
    assert mode["threaded"] is True


def test_a_fast_broker_batch_stays_on_the_calling_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub, seen = _tracking(lambda: None)
    monkeypatch.setattr(run_dispatch, "dispatch_run", stub)

    with capture_logs() as logs:
        sent, _, _ = _publish(_queued(40))

    assert len(sent) == 40
    assert seen["threads"] == {threading.current_thread().name}
    (mode,) = [e for e in logs if e["event"] == "schedule_publish_mode"]
    assert mode["threaded"] is False


def test_a_cpu_bound_publish_stays_serial_however_long_it_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow publish is not a slow broker: threads overlap a wait, not CPU the GIL serialises."""
    stub, seen = _tracking(lambda: _burn(0.003))
    monkeypatch.setattr(run_dispatch, "dispatch_run", stub)

    with capture_logs() as logs:
        _publish(_queued(20))

    assert seen["threads"] == {threading.current_thread().name}
    (mode,) = [e for e in logs if e["event"] == "schedule_publish_mode"]
    assert mode["threaded"] is False and mode["median_cpu_ms"] >= 2.5


def test_the_decision_lasts_for_the_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured once: the tick's later batches start threaded, on the same pool."""
    stub, seen = _tracking(lambda: time.sleep(0.01))
    monkeypatch.setattr(run_dispatch, "dispatch_run", stub)
    publisher = _Publisher()
    try:
        publisher.publish(_queued(20))
        assert publisher.threaded is True
        pool = publisher._pool
        seen["threads"].clear()
        with capture_logs() as logs:
            sent, _, _ = publisher.publish(_queued(8))
        assert len(sent) == 8
        assert all(name.startswith("schedule-publish") for name in seen["threads"])
        assert publisher._pool is pool
        assert not [e for e in logs if e["event"] == "schedule_publish_mode"]
    finally:
        publisher.close()
    assert publisher._pool is None


def test_every_publish_carries_the_dispatching_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request id reaches the message headers from a context variable, which a pool
    thread does not inherit unless the publish runs in a copy of the caller's context."""
    seen: list[tuple[str | None, str]] = []

    def _record(run_id: uuid.UUID) -> str:
        seen.append((request_id_var.get(), threading.current_thread().name))
        return str(run_id)

    monkeypatch.setattr(run_dispatch, "dispatch_run", _record)
    reset = request_id_var.set("req-2117")
    try:
        _publish(_queued(20), threaded=True)
    finally:
        request_id_var.reset(reset)

    assert [rid for rid, _ in seen] == ["req-2117"] * 20
    assert all(name.startswith("schedule-publish") for _, name in seen)


def test_a_broker_lost_mid_batch_stops_new_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    lock = threading.Lock()
    up_for = 12

    def _lost_mid_batch(run_id: uuid.UUID) -> str:
        nonlocal attempts
        with lock:
            attempts += 1
            up = attempts <= up_for
        if up:
            return str(run_id)
        time.sleep(0.01)
        raise RuntimeError("broker down")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _lost_mid_batch)
    queued = _queued(100)

    with capture_logs() as logs:
        sent, failed, broker_down = _publish(queued, threaded=True)

    assert broker_down is True
    assert len(sent) == up_for and len(failed) == len(queued) - up_for
    # What succeeded, the failures that end it, and those already in flight: no more.
    bound = up_for + schedule_dispatch.BROKER_DOWN_AFTER + schedule_dispatch.PUBLISH_THREADS
    assert attempts <= bound < len(queued)
    (down,) = [e for e in logs if e["event"] == "schedule_dispatch_broker_down"]
    assert down["failed_without_attempt"] == len(queued) - attempts
    errors = [e for e in logs if e["event"] == "run_dispatch_failed"]
    assert len(errors) == attempts - up_for


def test_a_failure_is_logged_when_it_happens(monkeypatch: pytest.MonkeyPatch) -> None:
    """From the thread that made it, not after the batch: the log's timestamp is the failure's."""
    logged_by: list[str] = []

    def _fail(run_id: uuid.UUID) -> str:
        raise RuntimeError("one bad publish")

    def _log_exception(event: str, **_kw: Any) -> None:
        logged_by.append(threading.current_thread().name)

    monkeypatch.setattr(run_dispatch, "dispatch_run", _fail)
    monkeypatch.setattr(schedule_dispatch.log, "exception", _log_exception)

    _publish(_queued(5), threaded=True)

    assert logged_by and all(name.startswith("schedule-publish") for name in logged_by)


def test_a_success_resets_the_failure_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolated failures are not a down broker: only consecutive ones stop the batch."""
    lock = threading.Lock()
    calls = 0

    def _every_other(run_id: uuid.UUID) -> str:
        nonlocal calls
        with lock:
            calls += 1
            fail = calls % 2 == 0
        if fail:
            raise RuntimeError("one bad publish")
        return str(run_id)

    monkeypatch.setattr(run_dispatch, "dispatch_run", _every_other)

    sent, failed, broker_down = _publish(_queued(30), threaded=False)

    assert broker_down is False
    assert len(sent) == 15 and len(failed) == 15


def test_the_publish_threads_fit_in_the_broker_pool() -> None:
    """A thread past the pool's limit would block waiting for a producer to come back."""
    assert schedule_dispatch.PUBLISH_THREADS <= celery_app.conf.broker_pool_limit


_TEST_BROKER = "redis://localhost:6379/15"


def _redis() -> Any:
    import redis

    return redis.from_url(_TEST_BROKER, socket_connect_timeout=1, socket_timeout=5)


def _redis_up() -> bool:
    try:
        return bool(_redis().ping())
    except Exception:
        return False


@pytest.mark.real_dispatch
@pytest.mark.skipif(not _redis_up(), reason="redis not reachable (docker compose up -d redis)")
def test_concurrent_publishes_to_real_redis_neither_hang_nor_drop_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #2111 deadlock was a garbage collection inside a publish re-entering a lock it held,
    made likely by threads. A low GC threshold makes a collection happen inside nearly every
    publish; every burst must finish and every message must arrive. The OpenTelemetry Celery
    instrumentation every worker child turns on runs too: its publish hooks store spans on the
    shared task object, from each publishing thread."""
    from opentelemetry.instrumentation.celery import CeleryInstrumentor

    app = create_celery_app()
    app.conf.broker_url = _TEST_BROKER
    monkeypatch.setattr(run_dispatch, "celery_app", app)
    client = _redis()
    client.delete("celery")
    bursts, per_burst = 10, 200
    old_threshold = gc.get_threshold()
    instrumentor = CeleryInstrumentor()
    instrumentor.instrument()
    gc.set_threshold(10, 1, 1)
    try:
        for _ in range(bursts):
            outcome: list[Published] = []
            runner = threading.Thread(
                # Loopback Redis is fast enough to stay serial: force the threads under test.
                target=lambda out=outcome: out.append(_publish(_queued(per_burst), threaded=True)),
                daemon=True,
            )
            runner.start()
            runner.join(timeout=60)
            assert not runner.is_alive(), "a concurrent publish burst hung"
            sent, failed, broker_down = outcome[0]
            assert len(sent) == per_burst and failed == [] and broker_down is False
        assert client.llen("celery") == bursts * per_burst
    finally:
        gc.set_threshold(*old_threshold)
        instrumentor.uninstrument()
        client.delete("celery")
        app.close()
