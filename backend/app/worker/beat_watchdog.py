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
manual remedy that has worked all three times. "Alive but idle" stops being a
state this system can sit in indefinitely.

**Why not a liveness probe.** Azure Container Apps probes are HTTP/TCP, and the
worker serves neither; adding an HTTP server to a Celery worker to answer one
question is more moving parts than the question deserves. Dying is portable —
it works identically under compose, locally, and anywhere else this runs.

Three guards keep it from becoming a crash loop, because a watchdog that
restarts a container every 30 seconds is worse than the bug it is watching:

1. **Grace period from boot** — a cold start (migrations, slow broker) must not
   look like a wedge.
2. **Only ever fires on a STALE-but-READABLE heartbeat.** If Redis cannot be
   read, the verdict is ``unknown`` and nothing is killed: restarting cannot fix
   a broker outage, and a crash loop would bury the actual cause (the #852
   lesson — noise that hides the signal is worse than no signal).
3. **Never fires before the first heartbeat is seen**, so a deploy that starts
   the worker before the beat has ticked once doesn't self-immolate.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Literal, Protocol

from backend.app.core.logging import get_logger

log = get_logger(__name__)

# Redis key holding the epoch seconds of the last *executed* beat task.
BEAT_TICK_KEY = "dataq:beat:last_tick"

Verdict = Literal["ok", "stale", "unknown"]


class _TickStore(Protocol):
    """The two Redis calls this module needs (kept narrow so tests can fake it)."""

    def set(self, name: str, value: str) -> object: ...

    def get(self, name: str) -> object: ...


def _now() -> datetime:
    return datetime.now(UTC)


def record_beat_tick(store: _TickStore, *, now: datetime | None = None) -> None:
    """Stamp 'a scheduled task actually executed' — called BY the heartbeat task.

    Deliberately no expiry: an absent key is indistinguishable from a key that
    expired while the worker was wedged, and the watchdog needs to tell 'never
    ticked' (grace) from 'ticked long ago' (stale)."""
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

    - ``unknown`` — still inside the boot grace period, or no heartbeat has ever
      been observed, or the store could not be read. Never kill on these: the
      first two are normal startup, and the third cannot be fixed by restarting.
    - ``stale`` — a heartbeat exists and is older than ``stale_after_s``. This is
      the outage signature: the process is up, the schedule is armed, and nothing
      has run for many multiples of the tick interval.
    - ``ok`` — a task executed recently.
    """
    if uptime_s < grace_s:
        return "unknown"
    if last_tick is None:
        return "unknown"
    return "stale" if (now_ts - last_tick) > stale_after_s else "ok"


def _terminate(reason: str, *, age_s: float) -> None:
    """Exit hard so the platform restarts us.

    ``os._exit`` rather than ``sys.exit``/``SIGTERM``: the worker is by
    definition not processing anything, and a graceful shutdown path can itself
    block on the very pool that is wedged — which would leave the container
    alive and idle, i.e. exactly where we started."""
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
    terminate: object = _terminate,
    iterations: int | None = None,
) -> None:
    """Poll the heartbeat and terminate when it goes stale.

    ``iterations`` bounds the loop for tests; production passes None (forever).
    """
    seen_tick = False
    count = 0
    while iterations is None or count < iterations:
        count += 1
        last_tick = read_beat_tick(store)
        if last_tick is not None:
            seen_tick = True
        verdict = liveness_verdict(
            last_tick=last_tick,
            now_ts=time.time(),
            uptime_s=time.time() - started_at,
            stale_after_s=stale_after_s,
            grace_s=grace_s,
        )
        # `seen_tick` is the third guard: only kill a worker that demonstrably
        # WAS executing tasks and then stopped. A worker that has never ticked
        # since boot may simply be starting behind a slow broker.
        if verdict == "stale" and seen_tick and callable(terminate):
            terminate("no beat task executed within the stale window", age_s=time.time() - last_tick)  # type: ignore[operator]
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
