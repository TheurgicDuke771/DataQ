"""Execute a suite's checks via a datasource adapter and persist the results.

This is the GX-agnostic core of a run: it drives the `Run` lifecycle, calls a
`CheckRunner` (injected — Snowflake in production, a fake in tests), and maps the
returned `SuiteOutcome` onto `Result` rows. GX/Snowflake specifics live behind
the adapter; this layer only knows the DTOs in ``datasources.base``.

Run.status describes *execution*, not data quality: a run that completes is
``succeeded`` even when checks fail (the failures live in ``Result.status`` /
``SuiteOutcome.success``). ``failed`` means the run could not execute — the
adapter raised (e.g. could not reach the warehouse).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, null, select, update
from sqlalchemy.orm import Session

from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.datasources.base import CheckOutcome, CheckRunner, CheckSpec
from backend.app.db.models import RESULT_STATUSES, Check, Result, Run
from backend.app.services import suite_service
from backend.app.services.severity import resolve_status

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _build_result(run_id: uuid.UUID, check: Check, outcome: CheckOutcome) -> Result:
    """Map a check + its GX outcome to a `Result`, deriving the severity tier.

    The unexpected-percent badness scalar is extracted once and used both to band
    the tier (ADR 0005 / 0016) and to persist as the durable, SQL-aggregatable
    `metric_value` (ADR 0012). `duration_ms` stays NULL in v1 — per-check timing
    isn't separable from GX's single suite-level `validate()` (reserved seam).

    A check the runner could not *evaluate* (`outcome.errored` — e.g. it raised
    referencing a missing column) is an operational ``error`` result (#122), not a
    data failure: no severity tier, no `metric_value`. It's orthogonal to the
    health score (ADR 0005 weights only the four tiers), so it must never be
    banded as `fail`. The error message lands in `observed_value` for debugging —
    GX exception messages are schema-level (no row data), so they don't go through
    the `sample_failures` retention/PII path.
    """
    status, metric = resolve_status(
        outcome,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
    )
    if outcome.errored:
        # An errored check has no observed metric and no failing-row sample; surface
        # the (schema-level, row-data-free) GX message for debugging instead.
        observed = {"error": outcome.error_message} if outcome.error_message else None
        sample = None
    else:
        observed = sanitize_json(outcome.observed_value)
        sample = sanitize_json(outcome.sample_failures)
    return Result(
        run_id=run_id,
        check_id=check.id,
        status=status,
        metric_value=metric,
        observed_value=observed,
        expected_value=sanitize_json(outcome.expected_value),
        sample_failures=sample,
    )


_EXPECTATION_KIND = "expectation"


def _specs_for_checks(checks: list[Check]) -> list[CheckSpec]:
    """Dispatch checks by `check.kind` to their runner input (ADR 0012).

    v1 implements only the `expectation` kind (the GX `CheckRunner`). The other
    reserved kinds (`freshness`/`volume`/`schema_drift`/`anomaly`/`comparison`)
    are constraint-valid but have no runner yet, so a run containing one raises
    `NotImplementedError` rather than silently feeding it to GX as an
    expectation. This dispatch composes with the connection-type `CheckRunner`
    selection (Week 5, ADR 0011): `kind` chooses the *monitor*, `connection.type`
    chooses the *adapter*.
    """
    unsupported = sorted({c.kind for c in checks if c.kind != _EXPECTATION_KIND})
    if unsupported:
        raise NotImplementedError(
            f"no run path for check kind(s) {', '.join(unsupported)}; "
            f"only {_EXPECTATION_KIND!r} is implemented in v1"
        )
    return [CheckSpec(expectation_type=c.expectation_type, kwargs=dict(c.config)) for c in checks]


def _cancelled_mid_run(session: Session, run: Run) -> bool:
    """Did a cancel commit (from the API session) while this run was executing?

    ``refresh`` issues a fresh SELECT, so under READ COMMITTED it sees the API
    session's committed ``cancelled`` even though this (worker) session set the
    run ``running`` earlier. Note: with ``autoflush=False`` (db/session.py) the
    refresh does NOT flush the caller's pending result rows — they stay staged for
    the caller to either ``commit`` (not cancelled) or ``rollback`` (cancelled).
    """
    session.refresh(run)
    return run.status == "cancelled"


def execute_run(
    session: Session,
    *,
    run: Run,
    checks: list[Check],
    runner: CheckRunner,
    table: str,
    schema: str | None = None,
) -> Run:
    """Run ``checks`` against ``table`` via ``runner`` and persist the outcome.

    ``run`` must already be persisted (it carries the id the results link to).
    Returns the same `Run`, updated to ``succeeded`` or ``failed``.
    """
    run.status = "running"
    run.started_at = _now()
    session.commit()
    log.info(
        "run_started",
        run_id=str(run.id),
        suite_id=str(run.suite_id),
        n_checks=len(checks),
        table=table,
    )

    # Everything from here — dispatching by kind, running the adapter, building
    # rows, and persisting them — is guarded so any failure drives the run to a
    # terminal 'failed' state. Without this, a DB error during add_all/commit (or
    # an unrunnable check kind) would leave the run stuck in 'running' forever.
    # rollback() discards any partial result inserts before we record the failure.
    try:
        specs = _specs_for_checks(checks)
        outcome = runner.run_checks(table=table, schema=schema, checks=specs)
        rows = [
            _build_result(run.id, check, check_outcome)
            for check, check_outcome in zip(checks, outcome.checks, strict=True)
        ]
        session.add_all(rows)
        # Cooperative cancellation: if a cancel committed (from the API session)
        # while GX ran, don't overwrite it with a terminal success — drop the now-
        # moot (still-pending, unflushed) results and leave the run 'cancelled'.
        if _cancelled_mid_run(session, run):
            session.rollback()
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        run.status = "succeeded"
        run.finished_at = _now()
        session.commit()
    except Exception:
        session.rollback()
        # Same cooperative check on the failure path: a run the user cancelled
        # mid-flight that *also* errored stays 'cancelled', not masked as 'failed'.
        if _cancelled_mid_run(session, run):
            log.info("run_cancelled_during_execution", run_id=str(run.id))
            return run
        run.status = "failed"
        run.finished_at = _now()
        session.commit()
        log.exception("run_failed", run_id=str(run.id), table=table)
        return run

    log.info(
        "run_completed",
        run_id=str(run.id),
        suite_success=outcome.success,
        n_results=len(rows),
    )
    return run


def skip_run(session: Session, *, run: Run, checks: list[Check], reason: str) -> Run:
    """Record a run that had nothing to evaluate — every check `skip`ped (#122).

    Used when the adapter is never invoked because there's no data to validate
    (e.g. the target batch hasn't landed yet). The run still **succeeds** — it
    executed end to end, it just found nothing to check — and each check gets a
    ``skip`` Result carrying the ``reason`` (operational, not a severity tier, so
    it's excluded from the health-score N per ADR 0005). Distinct from ``failed``,
    which means the run could not execute.
    """
    run.status = "running"
    run.started_at = _now()
    session.commit()
    rows = [
        Result(run_id=run.id, check_id=check.id, status="skip", observed_value={"reason": reason})
        for check in checks
    ]
    session.add_all(rows)
    run.status = "succeeded"
    run.finished_at = _now()
    session.commit()
    log.info("run_skipped", run_id=str(run.id), reason=reason, n_checks=len(checks))
    return run


# ── read model (PR-C0b: the runs/results surface) ────────────────────────────
# Reads are scoped to suites the user can access — owned (`created_by`) or shared
# (`shares`), the same visibility `suite_service.list_suites` enforces. The API
# layer additionally calls `require_permission` for single-suite / single-run
# lookups (404 hides existence); this subquery is the defence-in-depth filter so
# a list query can never leak a run from a suite the caller can't see.


def list_runs(
    session: Session,
    *,
    user_id: uuid.UUID,
    suite_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Run]:
    """Runs for suites the user can access, newest first (`created_at` desc).

    Optionally narrowed to one ``suite_id`` and/or a ``status``. The accessible
    subquery is always applied, so passing a ``suite_id`` the user can't see
    yields an empty list (the API layer 404s that case up front via
    `require_permission`, but the filter keeps the service safe on its own).
    """
    accessible = suite_service.accessible_suite_ids(user_id)
    stmt = (
        select(Run).where(Run.suite_id.in_(accessible)).order_by(Run.created_at.desc()).limit(limit)
    )
    if suite_id is not None:
        stmt = stmt.where(Run.suite_id == suite_id)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    return list(session.scalars(stmt))


def get_run(session: Session, run_id: uuid.UUID) -> Run | None:
    """Fetch a run by id (no authz — the API layer gates on the run's suite)."""
    return session.get(Run, run_id)


_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def cancel_run(session: Session, run: Run) -> bool:
    """Transition a non-terminal run to ``cancelled``; return whether it changed.

    Returns ``False`` if the run is already terminal (succeeded/failed/cancelled)
    — the API surfaces that as 409. Sets ``finished_at``; ``started_at`` is left
    as-is (NULL if the run was still queued). This is the DB half; the API layer
    also best-effort revokes the Celery task, and the worker honours the
    ``cancelled`` status cooperatively (start-check + in-flight guard).
    """
    if run.status in _TERMINAL_STATUSES:
        return False
    run.status = "cancelled"
    run.finished_at = _now()
    session.commit()
    log.info("run_cancelled", run_id=str(run.id))
    return True


def list_results(session: Session, run_id: uuid.UUID) -> list[Result]:
    """The result rows for a run, in stable check order (`created_at`)."""
    return list(
        session.scalars(select(Result).where(Result.run_id == run_id).order_by(Result.created_at))
    )


# ── run progress (A1: the poll surface for the live-progress UI) ──────────────


@dataclass(frozen=True)
class CheckProgress:
    """One check's progress within a run. ``status`` is ``None`` when the check has
    no result row — *pending* while the run is active, or *not recorded* for a
    terminal run (a ``failed`` run rolls back and writes no results, so consumers
    must read this together with the run's lifecycle ``status``, not in isolation)."""

    check_id: uuid.UUID
    name: str
    status: str | None


@dataclass(frozen=True)
class RunProgress:
    """A run's live progress: lifecycle status + per-check resolution + a status
    histogram, the compact shape the live-progress UI polls."""

    run: Run
    total_checks: int
    completed_checks: int
    counts: dict[str, int]
    checks: list[CheckProgress]


def get_run_progress(session: Session, run: Run) -> RunProgress:
    """Assemble a run's progress from the suite's checks + the run's results.

    DB-driven (not Celery task state): the worker writes the ``run.status``
    lifecycle (queued → running → succeeded/failed/cancelled) and the per-check
    ``Result`` rows, so the DB is the source of truth and this composes with the
    same suite-scoped authz the rest of the read API uses.

    Each suite check maps to its result's status, or ``None`` while pending.
    Note: because GX validates a suite in one atomic batch, all result rows land
    together at completion — so mid-run a check reads ``pending`` and the
    histogram fills at the terminal transition (this endpoint reports lifecycle +
    final per-check resolution, not sub-GX incremental progress). Checks are taken
    from the *current* suite definition; a result is matched to its check by id.
    """
    checks = list(
        session.scalars(
            select(Check).where(Check.suite_id == run.suite_id).order_by(Check.created_at)
        )
    )
    # One result per (run_id, check_id) in v1 (each run writes one row per check);
    # keyed by check_id to join against the suite's current checks.
    results = {r.check_id: r for r in list_results(session, run.id)}
    counts: dict[str, int] = dict.fromkeys(RESULT_STATUSES, 0)
    per_check: list[CheckProgress] = []
    completed = 0
    for check in checks:
        result = results.get(check.id)
        status = result.status if result is not None else None
        per_check.append(CheckProgress(check_id=check.id, name=check.name, status=status))
        if status is not None:
            completed += 1
            counts[status] = counts.get(status, 0) + 1
    return RunProgress(
        run=run,
        total_checks=len(checks),
        completed_checks=completed,
        counts=counts,
        checks=per_check,
    )


# ── retention sweep (configurable PII purge of old result samples) ────────────


def purge_expired_sample_failures(
    session: Session, *, retention_days: int, now: datetime | None = None
) -> int:
    """Scrub `sample_failures` from results older than ``retention_days``.

    ``sample_failures`` is the only result column that can carry real (possibly
    PII-bearing) data rows; after the retention window we null it out (to a true
    SQL NULL) and stamp ``sample_failures_purged_at`` so the purge is auditable.
    The result row itself — and crucially ``metric_value`` — is **kept**, so
    dashboard trends / anomaly baselines survive the purge (ADR 0012); this is a
    PII-minimisation sweep, not a run-history delete. Returns the rows scrubbed.

    Only rows that actually hold a sample *object* are touched: the JSONB column
    stores Python ``None`` as JSON ``'null'`` (``none_as_null`` defaults False),
    and passing/errored checks write that — so ``IS NOT NULL`` would over-match
    millions of empty rows. ``jsonb_typeof`` excludes both SQL NULL (→ NULL) and
    JSON ``'null'`` (→ ``'null'``), leaving only real ``object``/``array``
    samples. Naturally idempotent (a scrubbed row is SQL NULL → typeof NULL →
    excluded); the ``purged_at IS NULL`` guard makes that intent explicit.

    ``retention_days <= 0`` disables the sweep (returns 0 without touching the DB)
    — a clean off-switch rather than purging everything. The cutoff is anchored on
    ``Result.created_at`` (when the result landed ≈ when the run completed).
    """
    if retention_days <= 0:
        return 0
    moment = now or _now()
    cutoff = moment - timedelta(days=retention_days)
    sample_typeof = func.jsonb_typeof(Result.sample_failures)
    # session.execute(<DML>) returns a CursorResult; the typed overload widens it
    # to Result (no rowcount), so cast to read the affected-row count.
    purge_result = cast(
        CursorResult[Any],
        session.execute(
            update(Result)
            .where(
                Result.created_at < cutoff,
                Result.sample_failures_purged_at.is_(None),
                sample_typeof.isnot(None),
                sample_typeof != "null",
            )
            .values(sample_failures=null(), sample_failures_purged_at=moment)
            # Fire-and-forget bulk DML on a fresh, short-lived worker session with
            # no loaded Result identities — skip the ORM identity-map sync, which
            # under the default 'auto'/'fetch' would emit an extra SELECT of every
            # matching PK before the UPDATE (the WHERE uses jsonb_typeof, so the
            # in-Python 'evaluate' strategy can't apply).
            .execution_options(synchronize_session=False)
        ),
    )
    session.commit()
    purged = purge_result.rowcount
    log.info(
        "sample_failures_purged",
        purged=purged,
        retention_days=retention_days,
        cutoff=cutoff.isoformat(),
    )
    return purged
