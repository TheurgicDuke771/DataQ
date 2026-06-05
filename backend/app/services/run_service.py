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

from sqlalchemy.orm import Session

from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.datasources.base import CheckOutcome, CheckRunner, CheckSpec
from backend.app.db.models import Check, Result, Run
from backend.app.services.severity import derive_status, extract_metric

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _build_result(run_id: uuid.UUID, check: Check, outcome: CheckOutcome) -> Result:
    """Map a check + its GX outcome to a `Result`, deriving the severity tier.

    The unexpected-percent badness scalar is extracted once and used both to band
    the tier (ADR 0005 / 0016) and to persist as the durable, SQL-aggregatable
    `metric_value` (ADR 0012). `duration_ms` stays NULL in v1 — per-check timing
    isn't separable from GX's single suite-level `validate()` (reserved seam).
    """
    metric = extract_metric(outcome)
    status = derive_status(
        success=outcome.success,
        metric_value=metric,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
    )
    return Result(
        run_id=run_id,
        check_id=check.id,
        status=status,
        metric_value=metric,
        observed_value=sanitize_json(outcome.observed_value),
        expected_value=sanitize_json(outcome.expected_value),
        sample_failures=sanitize_json(outcome.sample_failures),
    )


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

    specs = [CheckSpec(expectation_type=c.expectation_type, kwargs=dict(c.config)) for c in checks]

    # Everything from here — running the adapter, building rows, and persisting
    # them — is guarded so any failure drives the run to a terminal 'failed'
    # state. Without this, a DB error during add_all/commit would leave the run
    # stuck in 'running' forever. rollback() discards any partial result inserts
    # before we record the failure.
    try:
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
