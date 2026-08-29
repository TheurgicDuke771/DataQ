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

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.core.errors import DataQError
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import llm_service

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
