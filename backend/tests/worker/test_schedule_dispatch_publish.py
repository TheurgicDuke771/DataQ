"""Concurrent publish of a batch's scheduled runs (#2117).

The unit tests stub the publish. The last test publishes for real, from the pool, against a
local Redis, because a deadlock in concurrent `send_task` (#2111) only showed against a broker.
"""

import gc
import threading
import time
import uuid
from typing import Any

import pytest
from structlog.testing import capture_logs

from backend.app.core.logging import request_id_var
from backend.app.services import run_dispatch, schedule_dispatch
from backend.app.services.schedule_dispatch import _Queued
from backend.app.worker.celery_app import celery_app, create_celery_app


def _queued(count: int) -> list[_Queued]:
    return [_Queued(run_id=uuid.uuid4(), schedule_id=uuid.uuid4()) for _ in range(count)]


def test_a_slow_broker_batch_is_published_from_several_threads_in_its_own_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = threading.Lock()
    in_flight = 0
    peak = 0
    threads: set[str] = set()

    def _slow(run_id: uuid.UUID) -> str:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            threads.add(threading.current_thread().name)
        time.sleep(0.02)
        with lock:
            in_flight -= 1
        return f"task-{run_id}"

    monkeypatch.setattr(run_dispatch, "dispatch_run", _slow)
    queued = _queued(40)

    sent, failed, broker_down = schedule_dispatch._publish(queued)

    assert [item for item, _ in sent] == queued
    assert all(task_id == f"task-{item.run_id}" for item, task_id in sent)
    assert failed == [] and broker_down is False
    assert 1 < peak <= schedule_dispatch.PUBLISH_THREADS
    assert any(name.startswith("schedule-publish") for name in threads)


def test_a_fast_broker_batch_stays_on_the_calling_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below the threshold a publish is CPU-bound, and threads would only contend for the GIL."""
    threads: set[str] = set()

    def _instant(run_id: uuid.UUID) -> str:
        threads.add(threading.current_thread().name)
        return str(run_id)

    monkeypatch.setattr(run_dispatch, "dispatch_run", _instant)

    with capture_logs() as logs:
        sent, _, _ = schedule_dispatch._publish(_queued(40))

    assert len(sent) == 40
    assert threads == {threading.current_thread().name}
    (mode,) = [e for e in logs if e["event"] == "schedule_publish_mode"]
    assert mode["concurrent"] is False


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
    monkeypatch.setattr(schedule_dispatch, "CONCURRENT_ABOVE_S", 0.0)
    reset = request_id_var.set("req-2117")
    try:
        schedule_dispatch._publish(_queued(20))
    finally:
        request_id_var.reset(reset)

    assert [rid for rid, _ in seen] == ["req-2117"] * 20
    assert any(name.startswith("schedule-publish") for _, name in seen)


def test_a_broker_lost_mid_batch_stops_new_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    lock = threading.Lock()

    up_for = schedule_dispatch.PROBE_PUBLISHES + 4  # past the probe, into the threads

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
    monkeypatch.setattr(schedule_dispatch, "CONCURRENT_ABOVE_S", 0.0)
    queued = _queued(100)

    with capture_logs() as logs:
        sent, failed, broker_down = schedule_dispatch._publish(queued)

    assert broker_down is True
    assert len(sent) == up_for and len(failed) == len(queued) - up_for
    # What succeeded, the failures that end it, and those already in flight: no more.
    bound = up_for + schedule_dispatch.BROKER_DOWN_AFTER + schedule_dispatch.PUBLISH_THREADS
    assert attempts <= bound < len(queued)
    (down,) = [e for e in logs if e["event"] == "schedule_dispatch_broker_down"]
    assert down["failed_without_attempt"] == len(queued) - attempts
    errors = [e for e in logs if e["event"] == "run_dispatch_failed"]
    assert len(errors) == attempts - up_for
    assert all(isinstance(e["exc_info"], RuntimeError) for e in errors)


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

    sent, failed, broker_down = schedule_dispatch._publish(_queued(30))

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
    publish; every burst must finish and every message must arrive."""
    app = create_celery_app()
    app.conf.broker_url = _TEST_BROKER
    monkeypatch.setattr(run_dispatch, "celery_app", app)
    # Loopback Redis is fast enough to stay serial: force the threads this test is about.
    monkeypatch.setattr(schedule_dispatch, "CONCURRENT_ABOVE_S", 0.0)
    client = _redis()
    client.delete("celery")
    bursts, per_burst = 10, 200
    old_threshold = gc.get_threshold()
    gc.set_threshold(10, 1, 1)
    try:
        for _ in range(bursts):
            outcome: list[Any] = []
            runner = threading.Thread(
                target=lambda out=outcome: out.append(
                    schedule_dispatch._publish(_queued(per_burst))
                ),
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
        client.delete("celery")
        app.close()
