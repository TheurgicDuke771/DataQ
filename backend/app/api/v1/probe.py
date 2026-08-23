"""Week 1 exit-gate probe endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import AdminUser
from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.services import run_dispatch
from backend.app.services.probe import ensure_probe_fixtures

router = APIRouter(tags=["probe"])


class ProbeRunResponse(ApiModel):
    run_id: uuid.UUID
    status: str


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
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> ProbeRunResponse:
    settings = get_settings()
    _, suite, _ = ensure_probe_fixtures(db, user=current_user, settings=settings)

    run = run_dispatch.new_queued_run(suite, triggered_by=f"probe:{current_user.id}")
    db.add(run)
    db.commit()
    db.refresh(run)

    # Shared create-adjacent dispatch+broker-failure block (#227): on failure the
    # run is marked terminal-`failed` (never left stuck `queued`); we surface 503.
    if not run_dispatch.dispatch_or_fail(db, run):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="failed to dispatch run",
        )
    return ProbeRunResponse(run_id=run.id, status=run.status)
