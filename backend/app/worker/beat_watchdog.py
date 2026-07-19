"""Beat liveness watchdog — kill a worker that is alive but doing nothing (#904).

Three times now (2026-07-18 00:12Z, the #905 six-hour outage, 2026-07-19 04:55Z)
a post-deploy roll has left the worker in the same state: the container reports
Healthy, Celery logs ``ready`` and ``beat: Starting...``, no exception is raised
— and **not one scheduled task ever executes**. Orchestration polling, scheduled
runs, gap recovery, the reaper and the purge all stop silently. Every single
time, the only thing that told the truth was the DATABASE (``last_polled_at``
frozen), and every single time the fix was a human noticing and restarting the
revision.

This module makes the system notice instead.

**What is measured.** A periodic task writes a timestamp on *execution*
(``record_beat_tick``). That is deliberate: it proves the whole loop — beat
scheduled the task AND a worker consumed it. The failure mode being defended
against is precisely "beat keeps queueing, nothing consumes", so a heartbeat
written by the *scheduler* would have reported healthy right through the
outage.

**What is done about it.** A daemon thread compares that timestamp against the
clock and, when it goes stale, logs loudly and terminates the process — the
platform (ACA / compose ``restart:``) then restarts it, which is exactly the
manual remedy that has worked all three times.

**Why not a liveness probe.** Azure Container Apps probes are HTTP/TCP, and the
worker serves neither; adding an HTTP server to a Celery worker to answer one
question is more moving parts than the question deserves. Dying is portable.

A watchdog that restarts a container every 30 seconds is worse than the bug it
watches, and one that hangs is worse than none at all. Five guards, each earned
by a concrete failure mode found in review:

1. **Boot grace** — a cold start (migrations, slow broker) must not look like a
   wedge.
2. **Never kill on an unreadable store.** Restarting cannot fix a broker
   outage, and a crash loop would bury the actual cause (#852: noise that hides
   the signal is worse than no signal).
3. **Never kill unless THIS incarnation saw a heartbeat.** The stamp has no
   expiry, so a key left by a *previous* worker must not arm the watchdog — the
   tick has to be newer than this process's start, or a worker that never
   consumed anything would kill itself on its predecessor's key and loop
   forever.
4. **Never kill while tasks are actually running.** A stale beat with a busy
   pool is a long GX run occupying the slots, not a wedge; hard-exiting there
   would abort real work and strand its ``runs`` row.
5. **Require consecutive confirmations.** One stale reading can be a clock step
   (NTP); a wedge is still there a minute later.

Every Redis call this module makes is **bounded by a socket timeout** — an
untimed read is exactly the forever-wait #854 taught, and it would hang the one
thread whose job is to notice hangs.

**Assumed topology: one worker per Redis.** Production runs ``max_replicas = 1``,
so the process that writes the stamp is the process that reads it. Point a
second worker with a *skewed clock* at the same Redis — a host-run worker beside
the compose one, which logs a 7-hour drift in practice — and the two disagree
about what "recent" means. The guards contain the blast radius (a future-dated
stamp reads as ``unknown``, and a confirmation streak absorbs one-off jumps),
but the honest statement is that this is a single-writer signal, not a
distributed one. Sharing a Redis across environments is already unsupported for
the rate limiter's counters (ADR 0035); the same applies here.
"""

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

# Bounded like every other Redis path in the app (`core/rate_limit.py`): a
# watchdog that can block forever on a half-open connection is the #854 failure
# reintroduced on the thread that exists to detect it.
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
    """A Redis client with bounded socket timeouts — the ONLY way this module's
    client should be built. ``from_url`` defaults both timeouts to None, i.e.
    block forever, which on the watchdog thread means it silently stops
    watching."""
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
    """Stamp 'a scheduled task actually executed' — called BY the heartbeat task.

    Deliberately no expiry: an absent key is indistinguishable from one that
    expired *because* the worker was wedged. Guard 3 (the tick must be newer
    than this process's start) is what stops a stale key arming the watchdog,
    not a TTL."""
    store.set(BEAT_TICK_KEY, str((now or _now()).timestamp()))


def read_beat_tick(store: _TickStore) -> float | None:
    """Epoch seconds of the last executed beat task, or None if unset/unreadable.

    Never raises: a broker hiccup must yield ``unknown``, not an exception on the
    watchdog thread (which would silently kill the watchdog itself — the very
    class of silent death this module exists to end)."""
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
    """How many tasks this worker is executing right now (guard 4).

    Read from Celery's in-process worker state — local and non-blocking, unlike
    ``control.inspect()``, which round-trips the broker and could hang the
    watchdog. The import is local and defensive: outside a worker (tests, the
    API) the module may be absent, and 'unknown' must resolve to 0, never to
    'busy' — a permanently-busy reading would disarm the watchdog entirely."""
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
    """Decide whether this worker is executing scheduled work — a pure function,
    so the decision is testable without threads, clocks, or a broker.

    - ``unknown`` — inside the boot grace period, no heartbeat readable, or a
      negative age (the clock stepped backwards under us; never act on it).
    - ``stale`` — a heartbeat exists and is older than ``stale_after_s``.
    - ``ok`` — a task executed recently.
    """
    if uptime_s < grace_s:
        return "unknown"
    if last_tick is None:
        return "unknown"
    age = now_ts - last_tick
    if age < 0:
        # Clock stepped backwards (NTP correction): the reading is meaningless,
        # and acting on it would kill a healthy worker.
        return "unknown"
    return "stale" if age > stale_after_s else "ok"


def _terminate(reason: str, *, age_s: float) -> None:
    """Exit hard so the platform restarts us.

    ``os._exit`` rather than ``sys.exit``/``SIGTERM``: a graceful shutdown can
    itself block on the very pool that is wedged, leaving the container alive
    and idle — exactly where we started. Guard 4 has already established that no
    task is running, so nothing in flight is aborted by exiting here."""
    log.error(
        "beat_watchdog_terminating",
        reason=reason,
        seconds_since_last_beat_task=round(age_s, 1),
        remedy="process exits; the platform restarts it (ACA revision / compose restart)",
    )
    # Give the log line a moment to flush through the export handler before the
    # process disappears — an unexplained restart is its own mystery.
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
        # Guard 3: only a tick produced AFTER this process started proves that
        # THIS incarnation is consuming. The stamp never expires, so a
        # predecessor's key would otherwise arm the watchdog against a worker
        # that has never consumed anything — which would then die, restart, and
        # loop forever on an ever-staler key.
        if last_tick is not None and last_tick >= started_at:
            seen_own_tick = True
        verdict = liveness_verdict(
            last_tick=last_tick,
            now_ts=time.time(),
            uptime_s=time.time() - started_at,
            stale_after_s=stale_after_s,
            grace_s=grace_s,
        )
        # Guard 4: a busy pool is not a wedge. A 30-minute comparison run can
        # legitimately outlast the stale window; killing it would abort real
        # work and strand its `runs` row for the #458 reaper.
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
            # Grace = one full stale window: a cold start gets at least as long
            # to produce its first tick as a running worker gets to miss one.
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
