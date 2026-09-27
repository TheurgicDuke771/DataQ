"""Batched scheduled-run dispatch (A7, #1999).

Each batch is one transaction: claim up to ``batch_size`` due schedules under
``FOR UPDATE SKIP LOCKED``, advance them and insert their queued runs, commit —
then publish. The claim, advance and insert commit together, so a schedule
another dispatcher holds is skipped and a committed advance moves it out of the
due set: exactly-once holds per batch as it did per row. Publishing only after
the commit means a rolled-back batch never reaches the broker.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.orm import Session

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


def _publish(queued: list[_Queued]) -> tuple[list[tuple[_Queued, str]], list[_Queued]]:
    """Publish serially: `send_task` is not safe to call concurrently — the Redis result
    backend shares one non-reentrant PubSub lock across calls.
    """
    sent: list[tuple[_Queued, str]] = []
    failed: list[_Queued] = []
    for item in queued:
        try:
            sent.append((item, run_dispatch.dispatch_run(item.run_id)))
        except Exception:
            log.exception(
                "run_dispatch_failed", run_id=str(item.run_id), schedule_id=str(item.schedule_id)
            )
            failed.append(item)
    return sent, failed


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

    sent, failed = _publish(queued)
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
        summary["due"] += claimed
        for key, count in outcomes.items():
            summary[key] += count
        # A short batch means everything still due is locked by another dispatcher.
        if claimed < batch_size:
            break
        if clock() - started >= budget_s:
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
                "schedules_dispatch_budget_exhausted",
                residual=summary["residual"],
                dispatched=summary["dispatched"],
                elapsed_s=round(clock() - started, 3),
                budget_s=budget_s,
            )
            break
    log.info("schedules_dispatch_completed", **summary)
    return summary
