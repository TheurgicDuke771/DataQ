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
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import orchestration_service
from backend.app.services import run_service as svc
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["runs"])

_LIST_LIMIT_DEFAULT = 50
_LIST_LIMIT_MAX = 200


class RunRead(BaseModel):
    """A DQ suite run (execution lifecycle; `status` is execution, not pass/fail)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite_id: uuid.UUID
    status: str  # queued | running | succeeded | failed | cancelled
    triggered_by: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ResultRead(BaseModel):
    """One check's result within a run. `metric_value` is the SQL-aggregatable
    badness scalar (ADR 0012); `sample_failures` is sanitised at write time and
    only reaches a caller who cleared the suite-view gate."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    check_id: uuid.UUID
    status: str  # pass | warn | fail | critical | skip | error
    metric_value: float | None
    duration_ms: int | None
    observed_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None
    sample_failures: dict[str, Any] | None


class RunDetailRead(RunRead):
    results: list[ResultRead]


class PipelineRunRead(BaseModel):
    """A monitored orchestrator pipeline/DAG run (`pipeline_runs` ≠ `runs`)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str  # adf | airflow
    connection_id: uuid.UUID
    provider_run_id: str
    pipeline_or_dag_id: str
    env: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    failure_reason: str | None
    created_at: datetime


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
    # the service scopes to every suite the caller can access.
    if suite_id is not None:
        require_permission(db, suite_id, current_user.id, minimum="view")
    runs = svc.list_runs(
        db, user_id=current_user.id, suite_id=suite_id, status=run_status, limit=limit
    )
    return [RunRead.model_validate(r) for r in runs]


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
    require_permission(db, run.suite_id, current_user.id, minimum="view")
    results = svc.list_results(db, run_id)
    # Build from the validated RunRead — `Run` has no `results` relationship to
    # validate from, and results are fetched + redaction-gated separately.
    return RunDetailRead(
        **RunRead.model_validate(run).model_dump(),
        results=[ResultRead.model_validate(r) for r in results],
    )


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
