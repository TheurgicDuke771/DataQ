"""Assemble a redacted ``RunReport`` from a completed run's persisted rows.

This is the one place that reads the ORM and applies the seam's PII policy:
``sample_failures`` is passed through ``run_service.redact_sample_failures``
(counts-only; raw cell values masked) before it can reach a publisher. Everything
downstream of here works on the DTO, never the DB rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.alerting.base import CheckReport, RunReport
from backend.app.db.models import Check, Connection, Result, Run, Suite, worst_severity
from backend.app.services import run_service


def _target_label(suite: Suite | None) -> str:
    """A human-readable one-line target for the notification.

    Reads the datasource-shaped ``Suite.target`` (#215) directly rather than
    ``run_target.resolve_target`` (which can raise on a malformed target — a
    report must never fail to build). Flat-file targets show their ``path``;
    SQL targets show the dotted ``catalog.schema.table``.

    Mirrors the frontend ``summarizeTarget`` (``suiteTarget.ts``) precedence so
    a card labels a target the way the UI does — a new target field needs a
    matching edit here, there, and in ``run_target.resolve_target``.
    """
    target: dict[str, Any] = dict(suite.target) if suite and suite.target else {}
    path = target.get("path")
    if path:
        return str(path)
    parts = [target.get("catalog"), target.get("schema"), target.get("table")]
    dotted = ".".join(str(p) for p in parts if p)
    return dotted or "(no target)"


def build_run_report(session: Session, run: Run) -> RunReport:
    """Build the redacted, GX-agnostic report for a terminal ``run``.

    Joins each ``Result`` back to its ``Check`` (by id) for the check name +
    expectation; a result whose check was since deleted degrades to a placeholder
    name rather than failing the build. ``metric_value`` is widened ``Decimal`` →
    ``float`` for JSON-friendly transport.
    """
    suite = session.get(Suite, run.suite_id)
    connection = session.get(Connection, suite.connection_id) if suite is not None else None
    checks = {c.id: c for c in session.scalars(select(Check).where(Check.suite_id == run.suite_id))}
    results: list[Result] = run_service.list_results(session, run.id)

    counts: dict[str, int] = {}
    check_reports: list[CheckReport] = []
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        check = checks.get(result.check_id)
        check_reports.append(
            CheckReport(
                check_name=check.name if check is not None else "(deleted check)",
                expectation_type=check.expectation_type if check is not None else "",
                status=result.status,
                metric_value=(
                    float(result.metric_value) if result.metric_value is not None else None
                ),
                observed_value=result.observed_value,
                expected_value=result.expected_value,
                # Column-aware redaction (#415): the tested column's failing values
                # surface when non-PII; the suite policy + heuristics mask PII.
                sample_summary=run_service.redact_sample_failures(
                    result.sample_failures,
                    tested_column=(check.config.get("column") if check is not None else None),
                    policy=suite.column_policy if suite is not None else None,
                ),
            )
        )

    worst = worst_severity(r.status for r in results)
    return RunReport(
        run_id=run.id,
        suite_id=run.suite_id,
        suite_name=suite.name if suite is not None else "(deleted suite)",
        run_status=run.status,
        datasource_type=connection.type if connection is not None else "",
        target_label=_target_label(suite),
        worst_severity=worst,
        counts=counts,
        checks=check_reports,
        finished_at=run.finished_at,
    )
