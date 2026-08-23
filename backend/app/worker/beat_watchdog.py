"""Beat liveness watchdog — kill a worker that is alive but doing nothing (#904)."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from backend.app.core.logging import get_logger

log = get_logger(__name__)

# Redis key holding the epoch seconds of the last *executed* beat task.
BEAT_TICK_KEY = "dataq:beat:last_tick"

# Bounded (#854): a watchdog that can block forever on a half-open connection
# hangs the thread that exists to detect hangs.
REDIS_CONNECT_TIMEOUT_S = 2.0
REDIS_READ_TIMEOUT_S = 2.0

# Consecutive stale readings required before terminating (guard 5).
STALE_CONFIRMATIONS = 2

Verdict = Literal["ok", "stale", "unknown"]


class _TickStore(Protocol):
    """The two Redis calls this module needs (kept narrow so tests can fake it)."""

    def set(self, name: str, value: str) -> object: ...

    def get(self, name: str) -> object: ...


def build_store(redis_url: str) -> _TickStore:
    """A Redis client with bounded socket timeouts — the ONLY way to build this
    module's client; bare ``from_url`` defaults both timeouts to block-forever.
    """
    import redis

    client: _TickStore = redis.from_url(
        redis_url,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_S,
        socket_timeout=REDIS_READ_TIMEOUT_S,
    )
    return client


def _now() -> datetime:
    return datetime.now(UTC)


def record_beat_tick(store: _TickStore, *, now: datetime | None = None) -> None:
    """Stamp 'a scheduled task actually executed' — called BY the heartbeat task."""
    store.set(BEAT_TICK_KEY, str((now or _now()).timestamp()))


def read_beat_tick(store: _TickStore) -> float | None:
    """Epoch seconds of the last executed beat task, or None if unset/unreadable."""
    try:
        raw = store.get(BEAT_TICK_KEY)
    except Exception:
        log.warning("beat_watchdog_read_failed", exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return float(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (ValueError, AttributeError):
        log.warning("beat_watchdog_tick_malformed")
        return None


def active_task_count() -> int:
    """Tasks executing right now (guard 4) — in-process worker state, never
    ``control.inspect()`` (a broker round-trip could hang the watchdog). Outside
    a worker 'unknown' resolves to 0, never 'busy', which would disarm it.
    """
    try:
        from celery.worker import state as worker_state

        return len(worker_state.active_requests)
    except Exception:  # pragma: no cover - defensive: not running inside a worker
        return 0


def liveness_verdict(
    *,
    last_tick: float | None,
    now_ts: float,
    uptime_s: float,
    stale_after_s: float,
    grace_s: float,
) -> Verdict:
    """Liveness decision as a pure function (testable without threads or a broker)."""
    if uptime_s < grace_s:
        return "unknown"
    if last_tick is None:
        return "unknown"
    age = now_ts - last_tick
    if age < 0:
        return "unknown"
    return "stale" if age > stale_after_s else "ok"


def _terminate(reason: str, *, age_s: float) -> None:
    """Exit hard so the platform restarts us."""
    log.error(
        "beat_watchdog_terminating",
        reason=reason,
        seconds_since_last_beat_task=round(age_s, 1),
        remedy="process exits; the platform restarts it (ACA revision / compose restart)",
    )
    # Let the log line flush before the process disappears.
    time.sleep(1.0)
    os._exit(70)  # EX_SOFTWARE — distinguishable from a crash or an OOM kill


def watchdog_loop(
    store: _TickStore,
    *,
    stale_after_s: float,
    grace_s: float,
    interval_s: float,
    started_at: float,
    terminate: Callable[..., None] = _terminate,
    active_tasks: Callable[[], int] = active_task_count,
    iterations: int | None = None,
) -> None:
    """Poll the heartbeat and terminate once staleness is confirmed.
    ``iterations`` bounds the loop for tests; production passes None (forever).
    """
    seen_own_tick = False
    stale_streak = 0
    count = 0
    while iterations is None or count < iterations:
        count += 1
        last_tick = read_beat_tick(store)
        # Guard 3: only a tick AFTER this process started proves THIS incarnation consumes — the
        # stamp never expires, and a predecessor's key would die-restart-loop a worker forever.
        if last_tick is not None and last_tick >= started_at:
            seen_own_tick = True
        verdict = liveness_verdict(
            last_tick=last_tick,
            now_ts=time.time(),
            uptime_s=time.time() - started_at,
            stale_after_s=stale_after_s,
            grace_s=grace_s,
        )
        # Guard 4: a busy pool is a long run, not a wedge — killing it would
        # abort real work and strand its `runs` row for the #458 reaper.
        busy = active_tasks() > 0 if callable(active_tasks) else False
        stale_streak = (
            stale_streak + 1 if (verdict == "stale" and seen_own_tick and not busy) else 0
        )
        if stale_streak >= STALE_CONFIRMATIONS and callable(terminate) and last_tick is not None:
            terminate(
                "no beat task executed within the stale window",
                age_s=time.time() - last_tick,
            )
            return
        time.sleep(interval_s)


def start_watchdog(
    store: _TickStore, *, stale_after_s: float, interval_s: float
) -> threading.Thread:
    """Start the watchdog on a daemon thread (never blocks worker shutdown)."""
    started_at = time.time()
    thread = threading.Thread(
        target=watchdog_loop,
        args=(store,),
        kwargs={
            "stale_after_s": stale_after_s,
            # Grace = one full stale window: a cold start gets as long to produce
            # its first tick as a running worker gets to miss one.
            "grace_s": stale_after_s,
            "interval_s": interval_s,
            "started_at": started_at,
        },
        name="dataq-beat-watchdog",
        daemon=True,
    )
    thread.start()
    log.info("beat_watchdog_started", stale_after_s=stale_after_s, interval_s=interval_s)
    return thread
