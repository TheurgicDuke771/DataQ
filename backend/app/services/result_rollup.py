"""`result_status_hourly` — the dashboard's result-status histogram, pre-counted (#2010).

The table is a summary of `results` and never a second source. It holds, per suite, UTC hour
and status, the number of results of the succeeded runs whose ``results_rolled_up_at`` is set:

- :func:`roll_up_run` adds a run's results as the run succeeds, in the run's own transaction.
- :func:`roll_up_pending` adds the runs that were missed: history from before the table
  existed, a run inserted already succeeded (seeds, imports), or a roll-up that failed.
- :func:`forget_checks` subtracts a check's results before the check is deleted, because
  `results.check_id` cascades. A suite's rows go with the suite (`ON DELETE CASCADE`).
- :func:`reconcile` recounts recent hours from `results` and repairs any bucket that differs,
  so drift from a path nobody planned for (a manual repair, a restore, a future purge) heals
  and is logged instead of staying silent.

Nothing else changes what the table summarises: results are written only while a run is
running, a succeeded run never leaves that status, and no code deletes a run on its own.

:func:`status_counts` is the reader. It is exact, not approximately fresh: a succeeded run
that is not rolled up yet is counted from `results`, and so are the two part-hours at a
window's edges, since a window starts at a moment and a bucket at the top of an hour.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, text, union_all
from sqlalchemy.orm import InstrumentedAttribute, Session

from backend.app.core.logging import get_logger
from backend.app.db.literals import sql_constant
from backend.app.db.models import Result, ResultStatusHourly, Run

log = get_logger(__name__)

#: The one run status whose results the table counts (`rollup.AGGREGATABLE_RUN_STATUSES`).
#: Written as a literal in every statement below (`sql_constant` in the ORM ones), so
#: `ix_runs_results_unrolled`'s predicate is provable under any driver.
_SUCCEEDED = "succeeded"


def unrolled_runs_statement(limit: int) -> Select[tuple[uuid.UUID]]:
    """Succeeded runs not yet added, oldest id first: ``ix_runs_results_unrolled``."""
    return (
        select(Run.id)
        .where(Run.status == sql_constant(_SUCCEEDED), Run.results_rolled_up_at.is_(None))
        .order_by(Run.id)
        .limit(limit)
    )


#: Runs the sweep adds per statement, and so per transaction.
SWEEP_BATCH = 2000

# Claim the runs (the marker is the claim: a run is added once, whoever gets there first),
# then add their results. One statement, so a failure leaves neither half behind. The rows
# are upserted in key order: two transactions touching the same buckets lock them alike.
# The data-modifying CTEs run to completion whether or not the final SELECT reads them.
_ADD_SQL = """
WITH {pick}claimed AS (
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
    _ADD_SQL.format(
        pick="", which="id = :run_id AND status = 'succeeded' AND results_rolled_up_at IS NULL"
    )
)
# The batch is picked once, in its own CTE. As an `IN (subquery)` the planner may rescan it
# per outer row, and each rescan skips the rows already claimed and takes more (#2753).
_ADD_PENDING = text(
    _ADD_SQL.format(
        pick="picked AS MATERIALIZED (SELECT id FROM runs WHERE status = 'succeeded' "
        "AND results_rolled_up_at IS NULL ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED), ",
        which="id IN (SELECT id FROM picked)",
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
        ids = session.scalars(unrolled_runs_statement(wanted)).all()
        added = sum(roll_up_run(session, run_id) for run_id in ids)
    session.commit()
    return added


#: Hours back from the current one that the daily :func:`reconcile` recounts.
RECONCILE_HOURS = 48

# Every bucket where the table and `results` differ: what it holds beside what it must hold
# (the results of the runs marked as added). One statement, so both sides are one snapshot.
_FIND_DRIFT = text("""
    WITH counted AS (
        SELECT run.suite_id, date_trunc('hour', r.created_at, 'UTC') AS hour, r.status,
               count(*) AS n
        FROM results r JOIN runs run ON run.id = r.run_id
        WHERE run.status = 'succeeded' AND run.results_rolled_up_at IS NOT NULL
          AND (CAST(:since AS timestamptz) IS NULL OR r.created_at >= :since)
        GROUP BY 1, 2, 3
    ), held AS (
        SELECT suite_id, hour, status, n FROM result_status_hourly
        WHERE CAST(:since AS timestamptz) IS NULL OR hour >= :since
    )
    SELECT coalesce(c.suite_id, h.suite_id) AS suite_id, coalesce(c.hour, h.hour) AS hour,
           coalesce(c.status, h.status) AS status
    FROM counted c FULL JOIN held h
      ON h.suite_id = c.suite_id AND h.hour = c.hour AND h.status = c.status
    WHERE coalesce(c.n, 0) <> coalesce(h.n, 0)
    ORDER BY 1, 2, 3
    """)
_LOCK_DRIFTED = text("""
    SELECT 1 FROM result_status_hourly h
    JOIN unnest(CAST(:suite_ids AS uuid[]), CAST(:hours AS timestamptz[]),
                CAST(:statuses AS text[])) AS k(suite_id, hour, status)
      ON h.suite_id = k.suite_id AND h.hour = k.hour AND h.status = k.status
    ORDER BY h.suite_id, h.hour, h.status
    FOR UPDATE OF h
    """)
# Recount just those buckets and move each by what it is off by, both sides read in one
# snapshot: a run that commits after it is in neither side, so adding the difference (never
# setting the count) keeps that run's results whichever commits first. The rows are locked
# beforehand, in key order, so this statement cannot deadlock with a writer.
_REPAIR = text("""
    WITH keys AS (
        SELECT * FROM unnest(CAST(:suite_ids AS uuid[]), CAST(:hours AS timestamptz[]),
                             CAST(:statuses AS text[])) AS k(suite_id, hour, status)
    ), counted AS (
        -- One range scan over the drifted hours and suites; `drift` keeps only the keys.
        SELECT run.suite_id, date_trunc('hour', r.created_at, 'UTC') AS hour, r.status,
               count(*) AS n
        FROM results r JOIN runs run ON run.id = r.run_id
        WHERE run.status = 'succeeded' AND run.results_rolled_up_at IS NOT NULL
          AND r.created_at >= (SELECT min(hour) FROM keys)
          AND r.created_at < (SELECT max(hour) FROM keys) + interval '1 hour'
          AND run.suite_id IN (SELECT suite_id FROM keys)
        GROUP BY 1, 2, 3
    ), held AS (
        SELECT h.suite_id, h.hour, h.status, h.n
        FROM result_status_hourly h
        JOIN keys k ON h.suite_id = k.suite_id AND h.hour = k.hour AND h.status = k.status
    ), drift AS (
        SELECT k.suite_id, k.hour, k.status, h.n IS NOT NULL AS held_row,
               coalesce(h.n, 0) AS held, coalesce(c.n, 0) AS counted
        FROM keys k
        LEFT JOIN counted c ON c.suite_id = k.suite_id AND c.hour = k.hour AND c.status = k.status
        LEFT JOIN held h ON h.suite_id = k.suite_id AND h.hour = k.hour AND h.status = k.status
        WHERE coalesce(c.n, 0) <> coalesce(h.n, 0)
    ), moved AS (
        UPDATE result_status_hourly AS h SET n = h.n + (d.counted - d.held)
        FROM drift d
        WHERE d.held_row AND h.suite_id = d.suite_id AND h.hour = d.hour AND h.status = d.status
        RETURNING 1
    ), added AS (
        INSERT INTO result_status_hourly (suite_id, hour, status, n)
        SELECT suite_id, hour, status, counted FROM drift WHERE NOT held_row ORDER BY 1, 2, 3
        ON CONFLICT (suite_id, hour, status) DO UPDATE SET n = result_status_hourly.n + EXCLUDED.n
        RETURNING 1
    )
    SELECT suite_id, hour, status, held, counted FROM drift ORDER BY 1, 2, 3
    """)


@dataclass(frozen=True)
class Drift:
    """A bucket the table held wrongly: ``held`` results where ``counted`` was right."""

    suite_id: uuid.UUID
    hour: datetime
    status: str
    held: int
    counted: int


def reconcile(
    session: Session, *, hours: int | None = RECONCILE_HOURS, now: datetime | None = None
) -> list[Drift]:
    """Recount the last ``hours`` hours (every hour when ``None``) from `results`, repair each
    bucket that differs, and commit. Returns the buckets repaired, with what each held.

    Read first, without locks: drift is rare, so a run of this normally changes nothing and
    blocks no writer. Only the drifted buckets are then locked, in the order every writer locks
    them, recounted and moved by the difference.
    """
    since = (
        None if hours is None else _hour_floor(now or datetime.now(UTC)) - timedelta(hours=hours)
    )
    found = session.execute(_FIND_DRIFT, {"since": since}).all()
    if not found:
        session.rollback()
        return []
    keys = {
        "suite_ids": [str(row.suite_id) for row in found],
        "hours": [row.hour for row in found],
        "statuses": [row.status for row in found],
    }
    session.execute(_LOCK_DRIFTED, keys)
    repaired = [
        Drift(row.suite_id, row.hour, row.status, int(row.held), int(row.counted))
        for row in session.execute(_REPAIR, keys)
    ]
    session.commit()
    return repaired


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
        .where(runs_visible, Run.status == sql_constant(_SUCCEEDED), created >= prev_since, *where)
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
    for status, n_current, n_prev in session.execute(stmt):
        counts[status] = counts.get(status, 0) + int(n_current)
        prev_counts[status] = prev_counts.get(status, 0) + int(n_prev)
    # A zero is no key: a status absent from a window is absent from its dict, and a bucket
    # emptied by a deleted check holds 0.
    return (
        {s: n for s, n in counts.items() if n > 0},
        {s: n for s, n in prev_counts.items() if n > 0},
    )
