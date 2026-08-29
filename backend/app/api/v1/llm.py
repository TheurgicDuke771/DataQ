"""LLM invocation read surface (ADR 0042) — the polling target for worker-side
LLM calls. Creation happens on the feature endpoints (SQL-gen, suggestions),
not here. Rate-limited under the `llm` class by path prefix.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import get_current_user
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import Connection, LlmInvocation, User
from backend.app.db.session import get_db
from backend.app.services import (
    llm_kinds,  # noqa: F401 — registers every kind in this process
    llm_service,
    llm_sqlgen,
)
from backend.app.services.run_dispatch import dispatch_llm_invocation
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])


class LlmInvocationRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    status: str
    suite_id: UUID | None
    response: dict[str, Any] | None
    error: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int | None
    created_at: datetime
    finished_at: datetime | None


class LlmInvocationNotFoundError(DataQError):
    status_code = 404
    code = "llm_invocation_not_found"


class LlmDispatchFailedError(DataQError):
    status_code = 503
    code = "llm_dispatch_failed"


class SqlGenerationRequest(ApiRequestModel):
    suite_id: UUID
    #: Refused over-length, never silently truncated — a clipped rule generates
    #: SQL that quietly omits conditions the user wrote.
    description: str = Field(min_length=1, max_length=llm_sqlgen.MAX_DESCRIPTION_CHARS)
    include_profile: bool = False

    @field_validator("description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value


class LlmInvocationQueued(ApiModel):
    invocation_id: UUID
    status: str = "pending"


@router.post(
    "/sql_generation",
    response_model=LlmInvocationQueued,
    status_code=202,
    summary="Generate a custom-SQL check from a natural-language rule (suite edit)",
)
def generate_sql(
    payload: SqlGenerationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LlmInvocationQueued:
    """Queues a worker-side generation; poll `GET /llm/invocations/{id}`. The
    model's SQL passes the same ADR 0019 validator a human's would before it is
    ever stored; add the result to a check like any custom SQL (dry-run in the
    editor before save applies there unchanged).
    """
    suite = require_permission(db, payload.suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    llm_sqlgen.check_generation_preconditions(suite, connection)
    invocation = llm_service.create_invocation(
        db,
        kind=llm_sqlgen.SQLGEN_KIND,
        requested_by=current_user,
        suite_id=suite.id,
        request={
            "description": payload.description,
            "include_profile": payload.include_profile,
        },
    )
    db.commit()
    try:
        dispatch_llm_invocation(invocation.id)
    except Exception as exc:
        log.exception("llm_dispatch_failed", invocation_id=str(invocation.id))
        # Conditional, like the worker's claim: send_task can raise after the
        # message was effectively published, and a claimed row must not be
        # clobbered back to failed.
        db.rollback()
        db.execute(
            update(LlmInvocation)
            .where(LlmInvocation.id == invocation.id, LlmInvocation.status == "pending")
            .values(
                status="failed",
                error="the task broker was unreachable — the generation was not started",
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
        raise LlmDispatchFailedError("could not dispatch the generation to the worker") from exc
    return LlmInvocationQueued(invocation_id=invocation.id)


@router.get(
    "/invocations/{invocation_id}",
    response_model=LlmInvocationRead,
    summary="One LLM invocation — requester or workspace admin only",
)
def get_invocation(
    invocation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LlmInvocationRead:
    invocation = llm_service.get_visible_invocation(
        db, invocation_id, user=current_user, is_admin=is_workspace_admin(current_user)
    )
    if invocation is None:  # 404-no-leak: another user's invocation reads as absent
        raise LlmInvocationNotFoundError("LLM invocation not found")
    return LlmInvocationRead.model_validate(invocation)
