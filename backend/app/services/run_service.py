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
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.datasources.base import CheckOutcome, CheckRunner, CheckSpec
from backend.app.db.models import Check, Result, Run
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
        run.status = "succeeded"
        run.finished_at = _now()
        session.commit()
    except Exception:
        session.rollback()
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


def list_results(session: Session, run_id: uuid.UUID) -> list[Result]:
    """The result rows for a run, in stable check order (`created_at`)."""
    return list(
        session.scalars(select(Result).where(Result.run_id == run_id).order_by(Result.created_at))
    )
