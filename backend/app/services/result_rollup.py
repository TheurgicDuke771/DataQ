"""`result_status_hourly` — the dashboard's result-status histogram, pre-counted (#2010).

The table is a summary of `results` and never a second source. It holds, per suite, UTC hour
and status, the number of results of the succeeded runs whose ``results_rolled_up_at`` is set:

- :func:`roll_up_run` adds a run's results as the run succeeds, in the run's own transaction.
- :func:`roll_up_pending` adds the runs that were missed: history from before the table
  existed, a run inserted already succeeded (seeds, imports), or a roll-up that failed.
- :func:`forget_checks` subtracts a check's results before the check is deleted, because
  `results.check_id` cascades. A suite's rows go with the suite (`ON DELETE CASCADE`).

Nothing else changes what the table summarises: results are written only while a run is
running, a succeeded run never leaves that status, and no code deletes a run on its own.

:func:`status_counts` is the reader. It is exact, not approximately fresh: a succeeded run
that is not rolled up yet is counted from `results`, and so are the two part-hours at a
window's edges, since a window starts at a moment and a bucket at the top of an hour.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, and_, func, or_, select, text, union_all
from sqlalchemy.orm import InstrumentedAttribute, Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Result, ResultStatusHourly, Run

log = get_logger(__name__)

#: The one run status whose results the table counts (`rollup.AGGREGATABLE_RUN_STATUSES`).
#: A literal in the SQL below, so `ix_runs_results_unrolled`'s predicate is provable.
_SUCCEEDED = "succeeded"
#: Runs the sweep adds per statement, and so per transaction.
SWEEP_BATCH = 2000

# Claim the runs (the marker is the claim: a run is added once, whoever gets there first),
# then add their results. One statement, so a failure leaves neither half behind. The rows
# are upserted in key order: two transactions touching the same buckets lock them alike.
# The data-modifying CTEs run to completion whether or not the final SELECT reads them.
_ADD_SQL = """
WITH claimed AS (
    UPDATE runs SET results_rolled_up_at = now()
    WHERE {which}
    RETURNING id, suite_id
), added AS (
    INSERT INTO result_status_hourly (suite_id, hour, status, n)
    SELECT c.suite_id, date_trunc('hour', r.created_at, 'UTC'), r.status, count(*)
    FROM results r JOIN claimed c ON c.id = r.run_id
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3
    ON CONFLICT (suite_id, hour, status) DO UPDATE SET n = result_status_hourly.n + EXCLUDED.n
    RETURNING 1
)
SELECT (SELECT count(*) FROM claimed) AS runs
"""
_ADD_ONE = text(
    _ADD_SQL.format(which="id = :run_id AND status = 'succeeded' AND results_rolled_up_at IS NULL")
)
_ADD_PENDING = text(
    _ADD_SQL.format(
        which="id IN (SELECT id FROM runs WHERE status = 'succeeded' "
        "AND results_rolled_up_at IS NULL ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED)"
    )
)
#: Above this many un-added runs a read counts from `results` alone (see `status_counts`).
PENDING_MAX = 200
_PENDING_OVER = text(
    "SELECT count(*) FROM (SELECT 1 FROM runs "
    "WHERE status = 'succeeded' AND results_rolled_up_at IS NULL LIMIT :limit) AS pending"
)
_PENDING = text(
    "SELECT count(*) FROM runs WHERE status = 'succeeded' AND results_rolled_up_at IS NULL"
)
# A check's delete cascades to its results, so they are taken out of the table first. Only
# the results of runs already added: a run not added yet has nothing to take back.
#
# Locks first, in the order every writer takes them (runs by id, then buckets by key), so two
# of these cannot deadlock with each other or with a sweep batch. Locking the runs also makes
# the marker read below final: a sweep that has claimed one of them has not committed, and
# this waits for it, then sees its marker and subtracts what it added.
_LOCK_RUNS = text("""
    SELECT id FROM runs
    WHERE status = 'succeeded'
      AND id IN (SELECT run_id FROM results WHERE check_id = ANY (CAST(:check_ids AS uuid[])))
    ORDER BY id
    FOR UPDATE
    """)
# What each of the next two statements takes out: the results of the checks, by bucket.
_LOCK_BUCKETS = text("""
    SELECT 1 FROM result_status_hourly h
    JOIN (
        SELECT run.suite_id, date_trunc('hour', r.created_at, 'UTC') AS hour, r.status
        FROM results r JOIN runs run ON run.id = r.run_id
        WHERE r.check_id = ANY (CAST(:check_ids AS uuid[]))
          AND run.results_rolled_up_at IS NOT NULL
        GROUP BY 1, 2, 3
    ) AS gone ON h.suite_id = gone.suite_id AND h.hour = gone.hour AND h.status = gone.status
    ORDER BY h.suite_id, h.hour, h.status
    FOR UPDATE OF h
    """)
_FORGET_CHECKS = text("""
    UPDATE result_status_hourly AS h
    SET n = h.n - gone.n
    FROM (
        SELECT run.suite_id, date_trunc('hour', r.created_at, 'UTC') AS hour, r.status,
               count(*) AS n
        FROM results r JOIN runs run ON run.id = r.run_id
        WHERE r.check_id = ANY (CAST(:check_ids AS uuid[]))
          AND run.results_rolled_up_at IS NOT NULL
        GROUP BY 1, 2, 3
    ) AS gone
    WHERE h.suite_id = gone.suite_id AND h.hour = gone.hour AND h.status = gone.status
    """)


def roll_up_run(session: Session, run_id: uuid.UUID) -> bool:
    """Add a just-succeeded run's results to the table, in the caller's transaction.

    Inside a savepoint, and never raises: a failure here must not fail the run. The run then
    stays un-added, readers go on counting it from `results`, and the sweep adds it later.
    Returns whether the add ran without error.
    """
    try:
        with session.begin_nested():
            session.execute(_ADD_ONE, {"run_id": run_id})
    except Exception:
        log.exception("result_rollup_failed", run_id=str(run_id))
        return False
    return True


def roll_up_pending(session: Session, *, limit: int | None = None) -> int:
    """Add up to ``limit`` (default ``SWEEP_BATCH``) succeeded runs not added yet, oldest id
    first; commits. Returns how many were added: 0 means nothing is left to claim.

    One statement for the batch. If it fails, the runs are added one at a time instead, each
    in its own savepoint, so one run that cannot be added (and is logged) does not hold the
    rest of the backlog back.
    """
    wanted = limit or SWEEP_BATCH
    try:
        with session.begin_nested():
            added = int(session.execute(_ADD_PENDING, {"limit": wanted}).scalar_one())
    except Exception:
        log.exception("result_rollup_batch_failed", limit=wanted)
        ids = session.scalars(
            select(Run.id)
            .where(Run.status == _SUCCEEDED, Run.results_rolled_up_at.is_(None))
            .order_by(Run.id)
            .limit(wanted)
        ).all()
        added = sum(roll_up_run(session, run_id) for run_id in ids)
    session.commit()
    return added


def pending_count(session: Session) -> int:
    """Succeeded runs not in the table yet."""
    count = session.scalar(_PENDING) or 0
    session.rollback()
    return int(count)


def forget_checks(session: Session, check_ids: Sequence[uuid.UUID]) -> None:
    """Take these checks' results out of the table. Call BEFORE deleting the checks, in the
    same transaction: the delete cascades to the results this reads."""
    if not check_ids:
        return
    params = {"check_ids": [str(i) for i in check_ids]}
    session.execute(_LOCK_RUNS, params)
    session.execute(_LOCK_BUCKETS, params)
    session.execute(_FORGET_CHECKS, params)


def _hour_floor(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _hour_ceil(moment: datetime) -> datetime:
    floor = _hour_floor(moment)
    return floor if floor == moment else floor + timedelta(hours=1)


def _from_results(
    runs_visible: ColumnElement[bool],
    since: datetime,
    prev_since: datetime,
    *where: ColumnElement[bool],
) -> Any:
    """(status, results in the current window, results in the previous one) from `results`."""
    created = Result.created_at
    return (
        select(
            Result.status,
            func.count().filter(created >= since),
            func.count().filter(created < since),
        )
        .join(Run, Result.run_id == Run.id)
        .where(runs_visible, Run.status == _SUCCEEDED, created >= prev_since, *where)
        .group_by(Result.status)
    )


def status_counts(
    session: Session,
    *,
    visible: Callable[[InstrumentedAttribute[uuid.UUID]], ColumnElement[bool]],
    since: datetime,
    prev_since: datetime,
) -> tuple[dict[str, int], dict[str, int]]:
    """Result-status histograms over succeeded runs of visible suites, for the window
    ``[since, ∞)`` and the one before it, ``[prev_since, since)``.

    ``visible`` turns a suite-id column into the caller's suite-visibility predicate. It is
    applied to ``Run.suite_id`` and to ``ResultStatusHourly.suite_id``, so both halves of the
    count are over the same suites. A status absent from a window is absent from its dict.

    Two statements. The first asks whether more than ``PENDING_MAX`` succeeded runs are not in
    the table yet (history still being added after an upgrade): then the table would save
    nothing, and everything is counted from `results`, as before the table. Otherwise the
    second counts, in one snapshot: the whole hours from the table; the part-hours at
    ``prev_since`` and ``since`` from `results`; and every succeeded run not in the table, from
    `results`, whichever run that is at the moment of the read.
    """
    backlog = session.scalar(_PENDING_OVER, {"limit": PENDING_MAX + 1}) or 0
    if backlog > PENDING_MAX:
        stmt = _from_results(visible(Run.suite_id), since, prev_since)
    else:
        edge_low = (prev_since, _hour_ceil(prev_since))
        edge_mid = (_hour_floor(since), _hour_ceil(since))
        hour = ResultStatusHourly.hour
        created = Result.created_at
        in_edges = or_(
            and_(created >= edge_low[0], created < edge_low[1]),
            and_(created >= edge_mid[0], created < edge_mid[1]),
        )
        runs_visible = visible(Run.suite_id)
        stmt = union_all(
            select(
                ResultStatusHourly.status,
                func.coalesce(func.sum(ResultStatusHourly.n).filter(hour >= edge_mid[1]), 0),
                func.coalesce(func.sum(ResultStatusHourly.n).filter(hour < edge_mid[0]), 0),
            )
            .where(visible(ResultStatusHourly.suite_id), hour >= edge_low[1])
            .group_by(ResultStatusHourly.status),
            # The part-hours: every succeeded run, in the table or not.
            _from_results(runs_visible, since, prev_since, in_edges),
            # The whole hours of the runs the table does not hold.
            _from_results(
                runs_visible, since, prev_since, Run.results_rolled_up_at.is_(None), ~in_edges
            ),
        )

    counts: dict[str, int] = {}
    prev_counts: dict[str, int] = {}
    for status, n_current, n_prev in cast("CursorResult[Any]", session.execute(stmt)):
        counts[status] = counts.get(status, 0) + int(n_current)
        prev_counts[status] = prev_counts.get(status, 0) + int(n_prev)
    # A zero is no key: a status absent from a window is absent from its dict, and a bucket
    # emptied by a deleted check holds 0.
    return (
        {s: n for s, n in counts.items() if n > 0},
        {s: n for s, n in prev_counts.items() if n > 0},
    )
