"""Run / result / pipeline-run read API — the results surface (PR-C0b).

Read-only views over `runs`, `results`, and `pipeline_runs` for the in-app
results page. The DQ-run reads (`/runs`, `/runs/{id}`) are **suite-scoped**: the
list filters to suites the caller can access and the detail gates on
`require_permission(view)`, so per-suite sharing + existence-hiding hold exactly
as for suites/checks (this is why reading Postgres directly — e.g. a Grafana
panel — is rejected as the primary surface: it bypasses this authz + the sample
redaction below). `/pipeline_runs` is orchestration monitoring, not suite-scoped,
so it is gated on authentication only.

Manual run *triggering* lives on `POST /suites/{id}/run` in `suites.py` (the
suite-scoped, edit-gated write); this module owns the reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user, is_workspace_admin
from backend.app.db.models import Check, User
from backend.app.db.session import get_db
from backend.app.services import orchestration_service, run_dispatch
from backend.app.services import run_service as svc
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["runs"])

_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 200


class RunRead(ApiModel):
    """A DQ suite run (execution lifecycle; `status` is execution, not pass/fail)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite_id: uuid.UUID
    # The asset resolved from the suite's target, stamped at dispatch (ADR 0034,
    # #760) — run history records the asset it actually ran against. NULL for
    # older rows / a targetless suite. Deferred to #760 by the #764 review.
    asset_id: uuid.UUID | None = None
    status: str  # queued | running | succeeded | failed | cancelled
    triggered_by: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    # Data-quality outcome — distinct from `status` (execution lifecycle): a run is
    # `succeeded` even when checks fail. Lets the runs list flag failing checks
    # without a drill-in. Grafted from `check_outcome_counts` on BOTH the list and
    # the detail endpoint (a bare `RunRead.model_validate(run)` leaves these at the
    # 0/0/None defaults — the ORM `Run` has no such columns — so the graft is what
    # populates them; #571).
    #
    # `checks_total` counts **evaluated** checks (pass + warn/fail/critical),
    # excluding operational skip/error (#122) — it is the data-quality-outcome
    # denominator, deliberately NOT the suite's check count. It therefore differs
    # from `GET /runs/{id}/progress`'s `total_checks`, which is the suite's *defined*
    # check count: a run that fails before any check executes (bad-credential
    # connection) evaluated nothing, so `checks_total == 0` (rendered `—`, not a
    # misleading `0/N`) while progress still reports the suite size. Both are
    # truthful about different things (#571).
    checks_total: int = 0
    checks_passed: int = 0
    worst_severity: str | None = None  # warn | fail | critical | None (all passed)
    # A redaction-safe reason for a `failed` run (#605) — a fixed classified
    # message (never raw adapter text). NULL for non-failed runs and older rows.
    failure_reason: str | None = None


class ResultRead(ApiModel):
    """One check's result within a run. `metric_value` is the SQL-aggregatable
    badness scalar (ADR 0012); `observed_value`/`expected_value` are GX summary
    values (same fields the dry-run / probe already surface).

    `sample_failures` is the raw GX failing-row sample — it can carry real data,
    so it is **redacted at the boundary** before it leaves DataQ (the numeric
    counts are kept; the offending cell values are masked). See
    `run_service.redact_sample_failures` for the policy and `_result_read` below
    for why it is never auto-populated from the ORM object (#226)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    check_id: uuid.UUID
    status: str  # pass | warn | fail | critical | skip | error
    metric_value: float | None
    duration_ms: int | None
    observed_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None
    sample_failures: dict[str, Any] | None  # redacted (counts kept, values masked)


class RunDetailRead(RunRead):
    results: list[ResultRead]


class CheckProgressRead(ApiModel):
    """One check's progress; `status` is null while the check is still pending."""

    check_id: uuid.UUID
    name: str
    status: str | None  # null = pending | pass | warn | fail | critical | skip | error


class RunProgressRead(ApiModel):
    """Compact live-progress view for polling: run lifecycle + per-check
    resolution + a status histogram. Lighter than the full run+results detail."""

    run_id: uuid.UUID
    suite_id: uuid.UUID
    status: str  # queued | running | succeeded | failed | cancelled
    total_checks: int
    completed_checks: int
    counts: dict[str, int]  # histogram over result statuses (all keys present)
    checks: list[CheckProgressRead]
    started_at: datetime | None
    finished_at: datetime | None


class PipelineRunRead(ApiModel):
    """A monitored orchestrator pipeline/DAG run (`pipeline_runs` ≠ `runs`)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str  # one of ORCHESTRATION_PROVIDERS (db/models.py — adf | airflow | dbt)
    connection_id: uuid.UUID
    provider_run_id: str
    pipeline_or_dag_id: str
    env: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    failure_reason: str | None
    created_at: datetime


_OUTCOME_FIELDS = ("checks_total", "checks_passed", "worst_severity")


def _outcome_update(outcome: tuple[int, int, str | None] | None) -> dict[str, object]:
    """Map a `check_outcome_counts` tuple (or None for a run with no results) onto
    the RunRead outcome fields — shared by the list and detail endpoints so both
    read models graft identically (#571)."""
    return dict(zip(_OUTCOME_FIELDS, outcome or (0, 0, None), strict=True))


@router.get("/runs", response_model=list[RunRead], summary="List runs")
def list_runs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    suite_id: uuid.UUID | None = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    limit: int = Query(default=_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
) -> list[RunRead]:
    # When a suite is named, gate on it up front so an inaccessible/unknown suite
    # 404s (existence hidden) rather than silently returning []. With no suite,
    # the service scopes to every suite the caller can access — or all suites for
    # a workspace-admin (ADR 0027). require_permission already grants a
    # workspace-admin `view` on a named suite, so the per-suite gate is consistent.
    if suite_id is not None:
        require_permission(db, suite_id, current_user.id, minimum="view")
    runs = svc.list_runs(
        db,
        user_id=current_user.id,
        suite_id=suite_id,
        status=run_status,
        limit=limit,
        include_all=is_workspace_admin(current_user),
    )
    # Graft each run's data-quality outcome (total/passed/worst-severity) in one
    # grouped query, so the list can flag failing checks behind a `succeeded` run.
    outcomes = svc.check_outcome_counts(db, [r.id for r in runs])
    return [
        RunRead.model_validate(r).model_copy(update=_outcome_update(outcomes.get(r.id)))
        for r in runs
    ]


def _result_read(
    result: Any,
    *,
    tested_column: str | None = None,
    policy: dict[str, Any] | None = None,
) -> ResultRead:
    """Map a `Result` ORM row to `ResultRead`, redacting `sample_failures`.

    Built field-by-field rather than via `model_validate(from_attributes)` so the
    raw, PII-bearing `sample_failures` can never be auto-copied onto the wire —
    redaction is the only path it can take out of here (#226). ``tested_column`` +
    the suite ``policy`` drive column-aware redaction (#415): a non-PII tested
    column's failing values surface; PII stays masked."""
    return ResultRead(
        id=result.id,
        check_id=result.check_id,
        status=result.status,
        metric_value=result.metric_value,
        duration_ms=result.duration_ms,
        observed_value=result.observed_value,
        expected_value=result.expected_value,
        sample_failures=svc.redact_sample_failures(
            result.sample_failures, tested_column=tested_column, policy=policy
        ),
    )


@router.get("/runs/{run_id}", response_model=RunDetailRead, summary="Get a run with its results")
def get_run(
    run_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RunDetailRead:
    run = svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    # Gate on the run's suite: a caller who can't see the suite can't see its
    # runs (404 hides the run id too, matching the suite existence-hiding rule).
    suite = require_permission(db, run.suite_id, current_user.id, minimum="view")
    results = svc.list_results(db, run_id)
    # Map check_id → tested column so each result's sample is redacted column-aware
    # against the suite's policy (#415): a non-PII tested column's values surface.
    checks = {c.id: c for c in db.scalars(select(Check).where(Check.suite_id == run.suite_id))}
    policy = suite.column_policy
    # `Run` has no `results` relationship to validate a RunDetailRead from
    # directly, so validate the run fields (as RunRead), graft the data-quality
    # outcome (#571 — else checks_total/passed stay at the 0/0 default here), and
    # attach the separately-fetched, redaction-gated results.
    outcome = svc.check_outcome_counts(db, [run.id]).get(run.id)
    return RunDetailRead(
        **RunRead.model_validate(run).model_copy(update=_outcome_update(outcome)).model_dump(),
        results=[
            _result_read(
                r,
                tested_column=(
                    checks[r.check_id].config.get("column") if r.check_id in checks else None
                ),
                policy=policy,
            )
            for r in results
        ],
    )


@router.get(
    "/runs/{run_id}/progress",
    response_model=RunProgressRead,
    summary="Poll a run's live progress (lifecycle + per-check status)",
)
def get_run_progress(
    run_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RunProgressRead:
    run = svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    # Same suite-scoped gate as the run detail: can't see the suite → 404 the run.
    require_permission(db, run.suite_id, current_user.id, minimum="view")
    progress = svc.get_run_progress(db, run)
    return RunProgressRead(
        run_id=run.id,
        suite_id=run.suite_id,
        status=run.status,
        total_checks=progress.total_checks,
        completed_checks=progress.completed_checks,
        counts=progress.counts,
        checks=[
            CheckProgressRead(check_id=c.check_id, name=c.name, status=c.status)
            for c in progress.checks
        ],
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunRead,
    summary="Cancel a queued or running run",
)
def cancel_run(
    run_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RunRead:
    """Cancel a non-terminal run. `edit`-gated (same capability as triggering).

    Marks the run `cancelled` and best-effort revokes its Celery task (dropping it
    if still queued). An already-finished run (succeeded/failed/cancelled) → 409.
    An in-flight run is stopped cooperatively by the worker (it won't overwrite a
    `cancelled` status with results), so cancel may race a fast run to completion.
    """
    run = svc.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    # Cancel is a control action on the suite's runs → edit (404 hides the run
    # for a caller who can't see the suite, matching the read endpoints).
    require_permission(db, run.suite_id, current_user.id, minimum="edit")
    if not svc.cancel_run(db, run):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is already finished")
    run_dispatch.revoke_run(run.celery_task_id)
    return RunRead.model_validate(run)


@router.get(
    "/pipeline_runs",
    response_model=list[PipelineRunRead],
    summary="List monitored orchestrator pipeline/DAG runs",
)
def list_pipeline_runs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    provider: str | None = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    limit: int = Query(default=_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
) -> list[PipelineRunRead]:
    pipeline_runs = orchestration_service.list_pipeline_runs(
        db, provider=provider, status=run_status, limit=limit
    )
    return [PipelineRunRead.model_validate(p) for p in pipeline_runs]


@router.get(
    "/orchestration/pipelines",
    response_model=list[PipelineRunRead],
    summary="List monitored pipelines with their latest run status",
)
def list_pipelines(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    provider: str | None = None,
    env: str | None = None,
    limit: int = Query(default=_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
) -> list[PipelineRunRead]:
    """The pipeline status view: one row per monitored pipeline (provider /
    pipeline-or-dag / env), carrying its most-recent run, most-recently-active
    first. Auth-only gated (orchestration monitoring, not suite-scoped); the
    per-run feed is `/pipeline_runs`.
    """
    pipelines = orchestration_service.list_pipelines(db, provider=provider, env=env, limit=limit)
    return [PipelineRunRead.model_validate(p) for p in pipelines]
