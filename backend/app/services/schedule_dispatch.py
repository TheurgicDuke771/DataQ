"""Batched scheduled-run dispatch (A7, #1999).

Each batch is one transaction: claim up to ``batch_size`` due schedules under
``FOR UPDATE SKIP LOCKED``, advance them and insert their queued runs, commit —
then publish. The claim, advance and insert commit together, so a schedule
another dispatcher holds is skipped and a committed advance moves it out of the
due set: exactly-once holds per batch as it did per row. Publishing only after
the commit means a rolled-back batch never reaches the broker.
"""

from __future__ import annotations

import contextvars
import statistics
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

from backend.app.core import usage_telemetry
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Connection, Run, Schedule, Suite
from backend.app.services import cron, run_dispatch, run_target

log = get_logger(__name__)

#: Schedules claimed (and row-locked) per transaction.
BATCH_SIZE = 500
#: Stop claiming once a tick has run this long — under the 60 s beat interval, so a
#: tick that cannot drain its due set reports the residual instead of overlapping.
BUDGET_S = 45.0
#: Consecutive publish failures that mean the broker is down: each failure costs a full
#: publish-retry cycle, so the rest of the batch is failed without trying and the tick ends.
BROKER_DOWN_AFTER = 3
#: Threads publishing a batch's runs at once (#2117). At most the broker pool's size
#: (Celery's ``broker_pool_limit``, 10), so no thread waits for a producer.
PUBLISH_THREADS = 8
#: Serial publishes timed before choosing whether to publish the rest from threads.
PROBE_PUBLISHES = 8
#: The median serial publish from which the rest go from threads. Measured (perf-baseline.md,
#: "v1.3 — concurrent schedule publish"): a serial publish costs about 0.5 ms plus the
#: round-trip; threaded, about 0.9 to 1.1 ms whatever the round-trip. Set above that floor,
#: so a batch only goes threaded where it is clearly faster.
CONCURRENT_ABOVE_S = 0.0015

_ADVANCE_SQL = text("""
    UPDATE schedules AS s
    SET last_run_at = :now,
        next_run_at = COALESCE(u.next_run_at, s.next_run_at),
        enabled = u.next_run_at IS NOT NULL,
        updated_at = now()
    FROM unnest(CAST(:ids AS uuid[]), CAST(:next_run_ats AS timestamptz[]))
         AS u(id, next_run_at)
    WHERE s.id = u.id
    """)

_TASK_IDS_SQL = text("""
    UPDATE runs AS r
    SET celery_task_id = u.task_id
    FROM unnest(CAST(:ids AS uuid[]), CAST(:task_ids AS text[])) AS u(id, task_id)
    WHERE r.id = u.id
    """)


@dataclass(frozen=True)
class _Queued:
    run_id: uuid.UUID
    schedule_id: uuid.UUID


def _next_fire(
    cache: dict[tuple[str, str], datetime | None], expression: str, timezone: str, now: datetime
) -> datetime | None:
    """``cron.next_fire(after=now)`` memoised per (cron, timezone) — ``now`` is fixed
    for the tick, so every schedule sharing both shares the answer. ``None`` = never.
    """
    key = (expression, timezone)
    if key not in cache:
        try:
            cache[key] = cron.next_fire(expression, timezone, after=now)
        except DataQError:
            cache[key] = None
    return cache[key]


def _publish_one(run_id: uuid.UUID) -> tuple[str | None, BaseException | None]:
    """One publish, as a pool thread runs it: never raises, never touches the session."""
    try:
        return run_dispatch.dispatch_run(run_id), None
    except Exception as exc:
        return None, exc


def _publish(
    queued: list[_Queued],
) -> tuple[list[tuple[_Queued, str]], list[_Queued], bool]:
    """Publish a batch's runs. Returns (sent, failed, broker_down).

    Serially at first, timing each publish. Once ``PROBE_PUBLISHES`` have succeeded after
    the first, which pays for the connection, a median at or above ``CONCURRENT_ABOVE_S``
    means the broker round-trip dominates, and the rest go from ``PUBLISH_THREADS``
    threads, which overlap the waits (#2117). A faster broker stays serial: building a
    message is CPU work under the GIL, so threads only add contention there.

    Concurrent ``send_task`` is safe because the app has no result backend (#2206), so a
    publish shares nothing but kombu's producer pool, which hands each thread its own
    producer and connection. The serial start primes that pool and the app's lazy state
    on this thread. Each threaded publish runs in a copy of this thread's context, so the
    request id and the trace context still reach the message headers. Once
    ``BROKER_DOWN_AFTER`` publishes fail in a row, in the order they finish, no further one
    is started and the rest of the batch fails without an attempt.
    """
    outcomes: list[tuple[_Queued, str | None, BaseException | None]] = []
    timings: list[float] = []
    succeeded = 0
    streak = 0
    index = 0
    concurrent = False
    while index < len(queued) and streak < BROKER_DOWN_AFTER:
        item = queued[index]
        index += 1
        started = time.perf_counter()
        task_id, exc = _publish_one(item.run_id)
        outcomes.append((item, task_id, exc))
        if exc is not None:
            streak += 1
            continue
        streak = 0
        succeeded += 1
        if succeeded > 1 and len(timings) < PROBE_PUBLISHES:
            timings.append(time.perf_counter() - started)
            if len(timings) == PROBE_PUBLISHES:
                median_s = statistics.median(timings)
                concurrent = median_s >= CONCURRENT_ABOVE_S
                log.info(
                    "schedule_publish_mode",
                    concurrent=concurrent,
                    median_publish_ms=round(median_s * 1000, 3),
                )
                if concurrent:
                    break

    rest = queued[index:]
    broker_down = streak >= BROKER_DOWN_AFTER
    if concurrent and rest:
        lock = threading.Lock()
        stop = threading.Event()
        run_streak = 0

        def attempt(item: _Queued) -> tuple[_Queued, str | None, BaseException | None]:
            nonlocal run_streak
            if stop.is_set():
                return item, None, None
            task_id, exc = _publish_one(item.run_id)
            with lock:
                run_streak = 0 if exc is None else run_streak + 1
                if run_streak >= BROKER_DOWN_AFTER:
                    stop.set()
            return item, task_id, exc

        with ThreadPoolExecutor(
            max_workers=min(PUBLISH_THREADS, len(rest)), thread_name_prefix="schedule-publish"
        ) as pool:
            futures = [pool.submit(contextvars.copy_context().run, attempt, item) for item in rest]
            outcomes.extend(future.result() for future in futures)
        broker_down = stop.is_set()
    else:
        outcomes.extend((item, None, None) for item in rest)

    sent: list[tuple[_Queued, str]] = []
    failed: list[_Queued] = []
    skipped = 0
    for item, task_id, exc in outcomes:
        if task_id is not None:
            sent.append((item, task_id))
            usage_telemetry.emit(usage_telemetry.UsageEvent.SUITE_RUN_TRIGGERED, trigger="schedule")
            continue
        failed.append(item)
        if exc is None:
            skipped += 1
        else:
            log.error(
                "run_dispatch_failed",
                run_id=str(item.run_id),
                schedule_id=str(item.schedule_id),
                exc_info=exc,
            )
    if broker_down:
        log.error("schedule_dispatch_broker_down", failed_without_attempt=skipped)
    return sent, failed, broker_down


def _dispatch_batch(
    session: Session,
    *,
    now: datetime,
    limit: int,
    fires: dict[tuple[str, str], datetime | None],
) -> dict[str, int]:
    """Claim, advance and fire one batch. Returns outcome counts (empty = nothing due)."""
    claimed = session.execute(
        select(Schedule.id, Schedule.suite_id, Schedule.cron, Schedule.timezone)
        .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
        .order_by(Schedule.next_run_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    if not claimed:
        session.rollback()
        return {}

    suites = {
        row.id: row
        for row in session.execute(
            select(Suite.id, Suite.target, Suite.asset_id, Connection.type)
            .join(Connection, Connection.id == Suite.connection_id)
            .where(Suite.id.in_({c.suite_id for c in claimed}))
        )
    }
    runnable: dict[uuid.UUID, bool] = {}
    outcomes = {"dispatched": 0, "skipped_target": 0, "dispatch_failed": 0, "disabled": 0}
    next_run_ats: list[datetime | None] = []
    run_rows: list[dict[str, Any]] = []
    queued: list[_Queued] = []

    for schedule_id, suite_id, expression, timezone in claimed:
        nxt = _next_fire(fires, expression, timezone, now)
        next_run_ats.append(nxt)
        if nxt is None:
            outcomes["disabled"] += 1
            log.error(
                "schedule_disabled_invalid_cron",
                schedule_id=str(schedule_id),
                cron=expression,
                timezone=timezone,
            )
            continue
        # A schedule cascade-deletes with its suite, and the claim's row lock blocks that.
        suite = suites[suite_id]
        if suite_id not in runnable:
            try:
                run_target.resolve_target(suite.type, suite.target)
                runnable[suite_id] = True
            except DataQError:
                runnable[suite_id] = False
        if not runnable[suite_id]:
            outcomes["skipped_target"] += 1
            log.warning(
                "schedule_skipped_invalid_target",
                schedule_id=str(schedule_id),
                suite_id=str(suite_id),
            )
            continue
        run_id = uuid.uuid4()
        run_rows.append(
            {
                "id": run_id,
                "suite_id": suite_id,
                "asset_id": suite.asset_id,
                "status": "queued",
                "triggered_by": f"schedule:{schedule_id}",
            }
        )
        queued.append(_Queued(run_id=run_id, schedule_id=schedule_id))

    session.execute(
        _ADVANCE_SQL,
        {
            "now": now,
            "ids": [str(c.id) for c in claimed],
            "next_run_ats": next_run_ats,
        },
    )
    if run_rows:
        session.execute(insert(Run), run_rows)
    session.commit()

    sent, failed, broker_down = _publish(queued)
    for item, _task_id in sent:
        log.info("schedule_fired", schedule_id=str(item.schedule_id), run_id=str(item.run_id))
    if sent:
        session.execute(
            _TASK_IDS_SQL,
            {
                "ids": [str(item.run_id) for item, _ in sent],
                "task_ids": [task_id for _, task_id in sent],
            },
        )
    if failed:
        session.execute(
            update(Run)
            .where(Run.id.in_([item.run_id for item in failed]), Run.status == "queued")
            .values(**run_dispatch.dispatch_failed_values(at=datetime.now(UTC)))
        )
    if sent or failed:
        session.commit()
    outcomes["dispatched"] = len(sent)
    outcomes["dispatch_failed"] = len(failed)
    outcomes["claimed"] = len(claimed)
    outcomes["broker_down"] = int(broker_down)
    return outcomes


def dispatch_due_schedules(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = BATCH_SIZE,
    budget_s: float = BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, int]:
    """Fire every enabled schedule whose ``next_run_at`` has passed (A7), in batches.

    NO-BACKFILL: a fire advances ``next_run_at`` to the next occurrence strictly after
    ``now`` in the schedule's own timezone, so a missed window fires once.
    """
    now = now or datetime.now(UTC)
    summary = {
        "due": 0,
        "dispatched": 0,
        "skipped_target": 0,
        "dispatch_failed": 0,
        "disabled": 0,
        "residual": 0,
    }
    fires: dict[tuple[str, str], datetime | None] = {}
    started = clock()
    while True:
        outcomes = _dispatch_batch(session, now=now, limit=batch_size, fires=fires)
        claimed = outcomes.pop("claimed", 0)
        broker_down = outcomes.pop("broker_down", 0)
        summary["due"] += claimed
        for key, count in outcomes.items():
            summary[key] += count
        # A short batch means everything still due is locked by another dispatcher.
        if claimed < batch_size and not broker_down:
            break
        # A down broker would fail every further batch too: leave them due for the next tick.
        if broker_down or clock() - started >= budget_s:
            summary["residual"] = (
                session.scalar(
                    select(func.count())
                    .select_from(Schedule)
                    .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
                )
                or 0
            )
            session.rollback()
            log.warning(
                (
                    "schedules_dispatch_stopped_broker_down"
                    if broker_down
                    else "schedules_dispatch_budget_exhausted"
                ),
                residual=summary["residual"],
                dispatched=summary["dispatched"],
                elapsed_s=round(clock() - started, 3),
                budget_s=budget_s,
            )
            break
    log.info("schedules_dispatch_completed", **summary)
    return summary
