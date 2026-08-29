"""LLM invocation read surface (ADR 0042) — the polling target for worker-side
LLM calls. Creation happens on the feature endpoints (SQL-gen, suggestions),
not here. Rate-limited under the `llm` class by path prefix.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import get_current_user
from backend.app.core.errors import DataQError
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.services import llm_service, llm_sqlgen
from backend.app.services.custom_sql import SQL_QUERYABLE_TYPES
from backend.app.services.run_dispatch import dispatch_llm_invocation
from backend.app.services.suite_authz import require_permission

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


class LlmTargetNotSqlError(DataQError):
    status_code = 422
    code = "llm_target_not_sql"


class LlmDispatchFailedError(DataQError):
    status_code = 503
    code = "llm_dispatch_failed"


class SqlGenerationRequest(ApiRequestModel):
    suite_id: UUID
    description: str
    include_profile: bool = False


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
    stored, and the editor dry-runs it before save.
    """
    require_permission(db, payload.suite_id, current_user.id, minimum="edit")
    suite = db.get(Suite, payload.suite_id)
    assert suite is not None  # require_permission resolved it  # nosec B101
    connection = db.get(Connection, suite.connection_id)
    if connection is None or connection.type not in SQL_QUERYABLE_TYPES:
        raise LlmTargetNotSqlError(
            "custom SQL requires a SQL datasource",
            detail={"supported": sorted(SQL_QUERYABLE_TYPES)},
        )
    if not (payload.description or "").strip():
        raise LlmTargetNotSqlError("description is required", code="llm_description_required")
    invocation = llm_service.create_invocation(
        db,
        kind=llm_sqlgen.SQLGEN_KIND,
        requested_by=current_user,
        suite_id=suite.id,
        request={
            "description": payload.description[: llm_sqlgen.MAX_DESCRIPTION_CHARS],
            "include_profile": payload.include_profile,
        },
    )
    db.commit()
    try:
        dispatch_llm_invocation(invocation.id)
    except Exception as exc:
        invocation.status = "failed"
        invocation.error = "the task broker was unreachable — the generation was not started"
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
