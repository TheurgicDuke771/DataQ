"""Data-subject-rights machinery (GDPR Art 15/17/20, CCPA/CPRA) — G2, #432.

DataQ has no people-table: personal data appears only as an INCIDENTAL residual in
`results.sample_failures` / `results.observed_value` — and in the incident evidence
snapshot (`incidents.evidence.failing_result.observed_value`, #1795), a third
persisted copy of the same shape — captured from the customer's own warehouse rows
(docs/site/security.md "Roles & deployment model"). A "subject" is therefore
identified by a **(column, value)** pair — the same key the customer's own warehouse
uses to identify that row — not a DataQ user id.

Matching is workspace-wide (an admin-only capability, ADR 0033) and spans every
suite, since the same subject's data can turn up in results from any suite.

**Erasure is surgical, not a blunt column-null.** Unlike the retention sweep
(#1253/#1267), which nulls a whole `sample_failures`/`observed_value` field once its
age crosses a clock, an on-demand subject erasure only removes the matching
row/cell — the rest of a result's captured sample (other rows, other subjects,
whatever made the check fail) survives. A GDPR erasure right does not license
destroying data belonging to unrelated rows, and the operator still needs that data
to debug why the check failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.db.models import Check, Incident, Result, Run, Suite
from backend.app.services.run_service import historical_check_context

# Mirrors run_service._FAILING_ROW_LIST_KEYS / _COMPARISON_SAMPLE_KEYS — the complete
# set of list-bearing keys `sample_failures` can carry dict-shaped rows under.
_FAILING_ROW_LIST_KEYS = ("unexpected_index_list", "partial_unexpected_list")
_COMPARISON_SAMPLE_KEYS = ("mismatched", "additional_in_source", "additional_in_target")
_ROW_LIST_KEYS = (*_FAILING_ROW_LIST_KEYS, *_COMPARISON_SAMPLE_KEYS)


def _sample_matches(sample: dict[str, Any] | None, *, column: str, value: str) -> bool:
    if not sample:
        return False
    for key in _ROW_LIST_KEYS:
        rows = sample.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and column in row and str(row.get(column)) == value:
                return True
    return False


def _scrub_sample(
    sample: dict[str, Any], *, column: str, value: str
) -> tuple[dict[str, Any], bool]:
    """Drop only the rows matching `(column, value)` from every list-bearing key.
    Everything else in the blob — other rows, other keys — is untouched.
    """
    changed = False
    out = dict(sample)
    for key in _ROW_LIST_KEYS:
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        kept = [
            row
            for row in rows
            if not (isinstance(row, dict) and column in row and str(row.get(column)) == value)
        ]
        if len(kept) != len(rows):
            changed = True
            out[key] = kept
    return out, changed


def _observed_matches(
    observed: dict[str, Any] | None, *, column: str, value: str, tested_column: str | None
) -> bool:
    if not observed:
        return False
    if "unparsed_value" in observed:
        return observed.get("column") == column and str(observed.get("unparsed_value")) == value
    inner = observed.get("observed_value")
    if isinstance(inner, list) and tested_column == column:
        return any(str(v) == value for v in inner)
    return False


def _scrub_observed(
    observed: dict[str, Any], *, column: str, value: str, tested_column: str | None
) -> tuple[dict[str, Any], bool]:
    """Scrub only the matching cell/value, keeping the shape's other fields —
    `unparsed_value`'s `column` name, a list-shaped case's other distinct values.
    """
    if "unparsed_value" in observed:
        if observed.get("column") == column and str(observed.get("unparsed_value")) == value:
            return {**observed, "unparsed_value": None}, True
        return observed, False
    inner = observed.get("observed_value")
    if isinstance(inner, list) and tested_column == column:
        kept = [v for v in inner if str(v) != value]
        if len(kept) != len(inner):
            return {**observed, "observed_value": kept}, True
    return observed, False


@dataclass
class MatchedResult:
    result_id: UUID
    run_id: UUID
    suite_id: UUID
    suite_name: str
    check_id: UUID
    check_name: str
    created_at: datetime
    #: Which of the two JSONB columns carried a hit — a result can match in both.
    matched_in: tuple[str, ...]
    sample_failures: dict[str, Any] | None
    observed_value: dict[str, Any] | None
    #: Carried along so a subsequent erase doesn't need to re-fetch the check.
    tested_column: str | None


def _candidate_query() -> Any:
    """Every result whose captured sample/observed data could possibly carry the
    subject's row. Workspace-wide (an admin-only capability), and a full scan of
    the JSONB columns rather than an indexed lookup — there is no way to index an
    arbitrary column-name/value match inside a schemaless blob, and this is an
    operator-triggered, low-frequency action, not a hot path.
    """
    return (
        select(Result, Run, Suite, Check)
        .join(Run, Result.run_id == Run.id)
        .join(Suite, Run.suite_id == Suite.id)
        .join(Check, Result.check_id == Check.id)
        .where(or_(Result.sample_failures.isnot(None), Result.observed_value.isnot(None)))
    )


def find_matching_results(session: Session, *, column: str, value: str) -> list[MatchedResult]:
    """Workspace-wide scan for results whose captured sample/observed data names
    `column` = `value` for a subject's warehouse row. Read-only — the access/export
    half of the subject-rights machinery (GDPR Art 15/20).
    """
    rows = session.execute(_candidate_query()).all()
    # The tested column as of WHEN each result was written (#1489's fix, reused
    # here): the check's config can change after the fact, and matching on its
    # CURRENT column would silently miss a subject whose data was captured under
    # an earlier column name.
    context = historical_check_context(
        session,
        [result for result, _run, _suite, _check in rows],
        {check.id: check for _result, _run, _suite, check in rows},
    )
    matched: list[MatchedResult] = []
    for result, run, suite, check in rows:
        tested_column, _expectation_type = context.get(result.id, (None, None))
        hit_sample = _sample_matches(result.sample_failures, column=column, value=value)
        hit_observed = _observed_matches(
            result.observed_value, column=column, value=value, tested_column=tested_column
        )
        if not (hit_sample or hit_observed):
            continue
        kinds = tuple(
            k
            for k, hit in (("sample_failures", hit_sample), ("observed_value", hit_observed))
            if hit
        )
        matched.append(
            MatchedResult(
                result_id=result.id,
                run_id=run.id,
                suite_id=suite.id,
                suite_name=suite.name,
                check_id=check.id,
                check_name=check.name,
                created_at=result.created_at,
                matched_in=kinds,
                sample_failures=result.sample_failures,
                observed_value=result.observed_value,
                tested_column=tested_column,
            )
        )
    return matched


@dataclass
class MatchedIncident:
    incident_id: UUID
    suite_id: UUID
    suite_name: str
    check_id: UUID
    check_name: str
    status: str
    created_at: datetime
    #: The stored `failing_result.observed_value` snapshot — the only field of the
    #: evidence card that can carry a warehouse cell (`incident_evidence` strips the
    #: sample lists before storing it).
    observed_value: dict[str, Any] | None
    tested_column: str | None


def _incident_observed(incident: Incident) -> dict[str, Any] | None:
    evidence = incident.evidence
    failing = evidence.get("failing_result") if isinstance(evidence, dict) else None
    observed = failing.get("observed_value") if isinstance(failing, dict) else None
    return observed if isinstance(observed, dict) else None


def find_matching_incidents(session: Session, *, column: str, value: str) -> list[MatchedIncident]:
    """Workspace-wide scan of `incidents.evidence` for the subject's `(column, value)`
    (#1795). The snapshot is written once per occurrence and is NOT on the retention
    clock, so a value already purged from `results` can still be here.

    The tested column is the check's CURRENT `config.column`: no `Result` row is
    retained on the incident, so the as-of resolution `historical_check_context`
    gives results is not available (#1809 tracks an as-of variant).
    """
    rows = session.execute(
        select(Incident, Suite, Check)
        .join(Suite, Incident.suite_id == Suite.id)
        .join(Check, Incident.check_id == Check.id)
        .where(Incident.evidence.is_not(None))
    ).all()
    matched: list[MatchedIncident] = []
    for incident, suite, check in rows:
        observed = _incident_observed(incident)
        tested_column = check.config.get("column") if check.config else None
        if not _observed_matches(observed, column=column, value=value, tested_column=tested_column):
            continue
        matched.append(
            MatchedIncident(
                incident_id=incident.id,
                suite_id=suite.id,
                suite_name=suite.name,
                check_id=check.id,
                check_name=check.name,
                status=incident.status,
                created_at=incident.created_at,
                observed_value=observed,
                tested_column=tested_column,
            )
        )
    return matched


@dataclass
class ErasureSummary:
    #: Every result that matched the (column, value) pair, whether or not
    #: scrubbing actually changed it.
    matched_result_ids: list[UUID]
    erased_count: int
    #: Incident evidence snapshots (#1795) — reported separately so the response can
    #: say WHERE the subject's data was, not just how much.
    matched_incident_ids: list[UUID] = field(default_factory=list)
    erased_incident_count: int = 0


def erase_matching_results(session: Session, *, column: str, value: str) -> ErasureSummary:
    """On-demand erasure (GDPR Art 17 / CCPA delete): removes the matching
    row/cell from `sample_failures` and `observed_value` on every matched result,
    leaving the rest of each result's captured sample intact — and the same cell
    from every matched incident's stored evidence snapshot (#1795), rewritten in
    place the way `incident_service.redact_stale_evidence` does. Caller is
    responsible for the audit_events row and the commit (mirrors how other admin
    mutations in `admin_service` compose with the router).
    """
    matched = find_matching_results(session, column=column, value=value)
    erased_ids: list[UUID] = []
    for m in matched:
        result = session.get(Result, m.result_id)
        assert result is not None  # loaded in the same transaction, moments ago
        changed = False
        if result.sample_failures:
            scrubbed, sample_changed = _scrub_sample(
                result.sample_failures, column=column, value=value
            )
            if sample_changed:
                result.sample_failures = scrubbed
                changed = True
        if result.observed_value:
            scrubbed_obs, obs_changed = _scrub_observed(
                result.observed_value, column=column, value=value, tested_column=m.tested_column
            )
            if obs_changed:
                result.observed_value = scrubbed_obs
                changed = True
        if changed:
            erased_ids.append(m.result_id)
    matched_incidents = find_matching_incidents(session, column=column, value=value)
    erased_incidents = 0
    for mi in matched_incidents:
        incident = session.get(Incident, mi.incident_id)
        assert incident is not None and mi.observed_value is not None
        scrubbed_obs, obs_changed = _scrub_observed(
            mi.observed_value, column=column, value=value, tested_column=mi.tested_column
        )
        if not obs_changed or not isinstance(incident.evidence, dict):
            continue
        failing = incident.evidence.get("failing_result")
        if not isinstance(failing, dict):
            continue
        incident.evidence = {
            **incident.evidence,
            "failing_result": {**failing, "observed_value": scrubbed_obs},
        }
        erased_incidents += 1
    return ErasureSummary(
        matched_result_ids=[m.result_id for m in matched],
        erased_count=len(erased_ids),
        matched_incident_ids=[mi.incident_id for mi in matched_incidents],
        erased_incident_count=erased_incidents,
    )
