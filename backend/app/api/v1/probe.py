"""Week 1 exit-gate probe endpoint.

POST seeds the probe fixtures, creates a queued Run, and dispatches the
``run_suite`` Celery task (GX → Snowflake DEV). GET reads a run back with its
results. This is a thin demonstrator of the full async path — not the general
run API, which arrives with suite/check CRUD in Weeks 3–5.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user
from backend.app.core.config import get_settings
from backend.app.db.models import Result, Run, User
from backend.app.db.session import get_db
from backend.app.services.probe import ensure_probe_fixtures
from backend.app.worker.tasks import run_suite

router = APIRouter(tags=["probe"])


class ProbeRunResponse(BaseModel):
    run_id: uuid.UUID
    status: str


class CheckResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    check_id: uuid.UUID
    status: str
    observed_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None


class RunStatusResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    results: list[CheckResultResponse]


@router.post(
    "/_probe/snowflake-suite",
    response_model=ProbeRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger the Snowflake probe suite",
    description=(
        "Seeds the dev Snowflake connection + canned suite, queues a run, and "
        "dispatches it to the Celery worker. Returns the run id to poll."
    ),
)
def trigger_snowflake_probe(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProbeRunResponse:
    settings = get_settings()
    _, suite, _ = ensure_probe_fixtures(db, user=current_user, settings=settings)

    run = Run(suite_id=suite.id, status="queued", triggered_by=f"probe:{current_user.id}")
    db.add(run)
    db.commit()
    db.refresh(run)

    table = settings.probe_snowflake_table or "UNSET"
    run_suite.delay(str(run.id), table)
    return ProbeRunResponse(run_id=run.id, status=run.status)


@router.get(
    "/_probe/runs/{run_id}",
    response_model=RunStatusResponse,
    summary="Read a probe run and its results",
)
def get_probe_run(
    run_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RunStatusResponse:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    results = list(db.scalars(select(Result).where(Result.run_id == run_id)))
    return RunStatusResponse(
        run_id=run.id,
        status=run.status,
        results=[CheckResultResponse.model_validate(r) for r in results],
    )
