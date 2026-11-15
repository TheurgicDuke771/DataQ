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
#: Serial publishes timed, once per tick, before choosing whether to publish from threads.
PROBE_PUBLISHES = 8
#: The least network wait per publish (wall time less this thread's CPU time) worth
#: threads. Threads overlap the wait but not the CPU, which the GIL serialises and
#: contention inflates about 1.6 to 2 times (perf-baseline.md, "v1.3 — concurrent schedule
#: publish"), so they also need the wait to be at least the CPU time.
MIN_WAIT_S = 0.0005

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


@dataclass(frozen=True)
class _Outcome:
    item: _Queued
    task_id: str | None
    attempted: bool


class _Streak:
    """Consecutive publish failures, shared by the publishing threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self.down = threading.Event()

    def record(self, ok: bool) -> None:
        with self._lock:
            self._count = 0 if ok else self._count + 1
            if self._count >= BROKER_DOWN_AFTER:
                self.down.set()


def _publish_one(item: _Queued) -> _Outcome:
    """One publish; never raises, never touches the session. A failure is logged here,
    as it happens, from whichever thread made it."""
    try:
        return _Outcome(item, run_dispatch.dispatch_run(item.run_id), attempted=True)
    except Exception:
        log.exception(
            "run_dispatch_failed", run_id=str(item.run_id), schedule_id=str(item.schedule_id)
        )
        return _Outcome(item, None, attempted=True)


class _Publisher:
    """Publishes a tick's runs, serially or from ``PUBLISH_THREADS`` threads (#2117).

    The tick starts serially and times its publishes, splitting each into this thread's
    CPU time and the rest, the wait on the broker. The first success is not timed: it pays
    for the connection. After ``PROBE_PUBLISHES`` more, the tick goes threaded if the
    median wait is at least ``MIN_WAIT_S`` and at least the median CPU time. The decision
    and the thread pool last for the tick, so later batches start threaded at once.

    Concurrent ``send_task`` is safe because the app has no result backend (#2206): a
    publish shares nothing but kombu's producer pool, which hands each thread its own
    producer and connection. The serial start primes that pool and the app's lazy state on
    this thread. Each threaded publish runs in a copy of this thread's context, so the
    request id and the trace context still reach the message headers.
    """

    def __init__(self, *, threaded: bool | None = None) -> None:
        self.threaded = threaded
        self._pool: ThreadPoolExecutor | None = None
        self._successes = 0
        self._waits: list[float] = []
        self._cpus: list[float] = []

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None

    def _observe(self, wall_s: float, cpu_s: float) -> None:
        self._successes += 1
        if self._successes == 1:
            return
        self._waits.append(max(wall_s - cpu_s, 0.0))
        self._cpus.append(cpu_s)
        if len(self._waits) < PROBE_PUBLISHES:
            return
        wait_s = statistics.median(self._waits)
        cpu_s = statistics.median(self._cpus)
        self.threaded = wait_s >= max(MIN_WAIT_S, cpu_s)
        log.info(
            "schedule_publish_mode",
            threaded=self.threaded,
            median_wait_ms=round(wait_s * 1000, 3),
            median_cpu_ms=round(cpu_s * 1000, 3),
        )

    def publish(
        self, queued: list[_Queued]
    ) -> tuple[list[tuple[_Queued, str]], list[_Queued], bool]:
        """Publish a batch's runs. Returns (sent, failed, broker_down).

        Once ``BROKER_DOWN_AFTER`` publishes fail in a row (threaded: in the order they
        finish), no further one is started and the rest of the batch fails unattempted.
        """
        streak = _Streak()
        outcomes: list[_Outcome] = []
        index = 0
        while index < len(queued) and self.threaded is not True and not streak.down.is_set():
            wall, cpu = time.perf_counter(), time.thread_time()
            outcome = _publish_one(queued[index])
            index += 1
            outcomes.append(outcome)
            streak.record(outcome.task_id is not None)
            if outcome.task_id is not None and self.threaded is None:
                self._observe(time.perf_counter() - wall, time.thread_time() - cpu)

        rest = queued[index:]
        if rest and not streak.down.is_set():

            def attempt(item: _Queued) -> _Outcome:
                if streak.down.is_set():
                    return _Outcome(item, None, attempted=False)
                outcome = _publish_one(item)
                streak.record(outcome.task_id is not None)
                return outcome

            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=PUBLISH_THREADS, thread_name_prefix="schedule-publish"
                )
            futures = [
                self._pool.submit(contextvars.copy_context().run, attempt, item) for item in rest
            ]
            outcomes.extend(future.result() for future in futures)
        else:
            outcomes.extend(_Outcome(item, None, attempted=False) for item in rest)

        sent: list[tuple[_Queued, str]] = []
        failed: list[_Queued] = []
        for outcome in outcomes:
            if outcome.task_id is None:
                failed.append(outcome.item)
                continue
            sent.append((outcome.item, outcome.task_id))
            usage_telemetry.emit(usage_telemetry.UsageEvent.SUITE_RUN_TRIGGERED, trigger="schedule")
        broker_down = streak.down.is_set()
        if broker_down:
            log.error(
                "schedule_dispatch_broker_down",
                failed_without_attempt=sum(not o.attempted for o in outcomes),
            )
        return sent, failed, broker_down


def _dispatch_batch(
    session: Session,
    *,
    now: datetime,
    limit: int,
    fires: dict[tuple[str, str], datetime | None],
    publisher: _Publisher,
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

    sent, failed, broker_down = publisher.publish(queued)
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
    publisher = _Publisher()
    try:
        while True:
            outcomes = _dispatch_batch(
                session, now=now, limit=batch_size, fires=fires, publisher=publisher
            )
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
    finally:
        publisher.close()
    log.info("schedules_dispatch_completed", **summary)
    return summary
