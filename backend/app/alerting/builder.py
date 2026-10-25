"""Assemble a redacted ``RunReport`` from a completed run's persisted rows."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    HEALTH_FAILING,
    CheckReport,
    ConnectionHealthReport,
    HealthState,
    IncidentCard,
    RunReport,
)
from backend.app.core.config import get_settings
from backend.app.db.models import (
    FAILING_TIERS,
    Asset,
    Check,
    Connection,
    Result,
    Run,
    Suite,
    User,
    worst_severity,
)
from backend.app.services import (
    incident_service,
    llm_rca,
    privacy_settings_service,
    run_service,
)
from backend.app.services.column_tags import effective_column_tags


def _run_url(run_id: uuid.UUID) -> str | None:
    """Deep link to the run-detail page (``/results/<id>``, App.tsx), or ``None``
    when no public base URL is configured — the alert then omits the link rather
    than emitting a broken relative one.
    """
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/results/{run_id}" if base else None


def build_connection_health_report(
    connection: Connection, *, state: HealthState
) -> ConnectionHealthReport:
    """A :class:`ConnectionHealthReport` from a connection's persisted poll health (#837)."""
    base = get_settings().public_base_url.rstrip("/")
    return ConnectionHealthReport(
        connection_id=connection.id,
        connection_name=connection.name,
        connection_type=connection.type,
        state=state,
        consecutive_failures=connection.consecutive_poll_failures or 0,
        # A failing edge must never render a card that says "failing" and silently omits WHY
        # (health_facts drops empty values) — an unset reason degrades to a visible "unknown".
        reason=(
            (connection.last_poll_error or "unknown — test the connection")
            if state == HEALTH_FAILING
            else None
        ),
        last_polled_at=connection.last_polled_at,
        connection_url=f"{base}/connections" if base else None,
    )


def _resolve_owner(session: Session, asset: Asset | None, suite: Suite | None) -> User | None:
    """The recipient an incident/alert's 'Owner' attribution routes to (#1515):
    the asset's own owner when one is set, else the suite's creator — the asset
    owner is who is actually responsible for the data, and a suite is only ever
    one of possibly several ways it gets monitored.
    """
    if asset is not None and asset.owner_user_id is not None:
        return session.get(User, asset.owner_user_id)
    # An automatic-coverage suite has no human creator (#2293).
    if suite is None or suite.created_by is None:
        return None
    return session.get(User, suite.created_by)


def _target_label(suite: Suite | None) -> str:
    """A human-readable one-line target for the notification."""
    target: dict[str, Any] = dict(suite.target) if suite and suite.target else {}
    path = target.get("path")
    if path:
        return str(path)
    # Empty/whitespace-only namespace folds to absent, mirroring
    # `run_target.resolve_target`'s `_str_or_none` — not a real namespace.
    namespace = target.get("namespace")
    namespace = namespace if isinstance(namespace, str) and namespace.strip() else None
    parts = [target.get("catalog"), namespace, target.get("schema"), target.get("table")]
    dotted = ".".join(str(p) for p in parts if p)
    return dotted or "(no target)"


def build_run_report(session: Session, run: Run) -> RunReport:
    """Build the redacted, GX-agnostic report for a terminal ``run``."""
    suite = session.get(Suite, run.suite_id)
    connection = session.get(Connection, suite.connection_id) if suite is not None else None
    asset = run_service.resolve_asset(session, suite, run)
    owner = _resolve_owner(session, asset, suite)
    checks = {c.id: c for c in session.scalars(select(Check).where(Check.suite_id == run.suite_id))}
    # The warehouse's own column classifications (G3, #433) — the same governance floor the REST and
    # MCP read paths apply.
    tags = effective_column_tags(session, asset)
    results: list[Result] = run_service.list_results(session, run.id)
    # The check as of THIS result (#1489/#1880/#1613) — the card's label, the redactors'
    # column + type and `zero_sample_suppressed`'s kind/engine all read the same snapshot
    # REST/MCP use, so a check re-pointed between the run and a late delivery never
    # relabels the result or redacts it against the wrong column. One `check_versions` read.
    as_of = run_service.historical_check_snapshots(session, results, checks)
    # Resolved once per report (#1887), not per result.
    zero_sample_mode = privacy_settings_service.zero_sample_mode(session)

    counts: dict[str, int] = {}
    check_reports: list[CheckReport] = []
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        check = checks.get(result.check_id)
        snap = as_of[result.id]
        # Column-aware redaction (#415): the tested column (as of the result, #1613) surfaces when
        # non-PII; the suite policy + heuristics mask PII. The tracker names what was masked and
        # why (#2255), the same pair REST and MCP return.
        redacted = run_service.redact_sample_failures_with_state(
            result.sample_failures,
            tested_column=snap.column,
            policy=suite.column_policy if suite is not None else None,
            tags=tags,
        )
        check_reports.append(
            CheckReport(
                check_id=result.check_id,
                check_name=check.name if check is not None else "(deleted check)",
                expectation_type=snap.expectation_type or "",
                status=result.status,
                metric_value=(
                    float(result.metric_value) if result.metric_value is not None else None
                ),
                observed_value=run_service.redact_observed_value(
                    result.observed_value,
                    tested_column=snap.column,
                    expectation_type=snap.expectation_type,
                    policy=suite.column_policy if suite is not None else None,
                    tags=tags,
                ),
                expected_value=result.expected_value,
                sample_summary=redacted.sample,
                redacted_columns=tuple(redacted.redacted_columns),
                redaction_reasons=redacted.redaction_reasons,
                sample_suppressed=run_service.zero_sample_suppressed(
                    status=result.status,
                    check_kind=snap.kind,
                    engine=snap.engine,
                    zero_sample_mode=zero_sample_mode,
                ),
            )
        )

    worst = worst_severity(r.status for r in results)
    incidents = _incident_cards(session, run, results, checks)
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
        env=connection.env if connection is not None else None,
        started_at=run.started_at,
        triggered_by=run.triggered_by,
        run_url=_run_url(run.id),
        owner=(owner.display_name or owner.email) if owner is not None else None,
        incidents=incidents,
    )


def _incident_cards(
    session: Session,
    run: Run,
    results: list[Result],
    checks: dict[uuid.UUID, Check],
) -> list[IncidentCard]:
    """The active incidents this run's *breaching* checks reference (ADR 0034 #761)."""
    active = incident_service.active_incidents_for_run(session, run)
    if not active:
        return []
    cards: list[IncidentCard] = []
    for result in results:
        if result.status not in FAILING_TIERS:
            continue
        incident = active.get(result.check_id)
        if incident is None:
            continue
        check = checks.get(result.check_id)
        cards.append(
            IncidentCard(
                incident_id=incident.id,
                check_id=result.check_id,
                check_name=check.name if check is not None else "(deleted check)",
                status=result.status,
                occurrence_count=incident.occurrence_count,
                is_new=incident.created_at == incident.last_seen_at,
                evidence=incident_service.evidence_for_alert(incident, session),
                narrative=llm_rca.latest_narrative_for_alert(session, incident),
            )
        )
    return cards
