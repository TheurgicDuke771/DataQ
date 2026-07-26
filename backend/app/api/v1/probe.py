"""Week 1 exit-gate probe endpoint.

Seeds the probe fixtures, creates a queued Run, and dispatches the ``run_suite``
Celery task (GX → Snowflake DEV). Kept because it does in one call what the real
API needs an already-seeded suite for.

**The companion `GET /_probe/runs/{run_id}` was removed (#1039).** It read `runs`
and `results` with **no suite-ownership check** — only `get_current_user` — so any
authenticated user holding a run id could read any run's results, bypassing
ADR 0027. A UUID is not a capability token, and run ids are treated as non-secret
everywhere else in the product.

It was also a second, weaker path to rows the real API already serves, which is
how it came to be missed by #989's redaction sweep — a forgotten sibling is
exactly what per-sink redaction fails to reach. Two bugs on one route in one PR
was the argument for deleting rather than gating it.

Read runs through `GET /api/v1/runs/{id}` and `/runs/{id}/results`, which enforce
suite authz and redact.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.core.config import get_settings
from backend.app.db.models import User
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
    current_user: Annotated[User, Depends(get_current_user)],
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
