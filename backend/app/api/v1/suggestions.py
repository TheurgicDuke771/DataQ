"""The automatic-coverage review queue (ADR 0047 §4): proposed rules a person accepts or rejects."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import MemberUser, get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import suggestion_service as svc

router = APIRouter(tags=["suggestions"])


class SuggestionRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite_id: uuid.UUID
    #: `profile` (from the table's own statistics) or `llm`.
    source: str
    status: str
    name: str
    expectation_type: str
    config: dict[str, Any]
    #: Why it was proposed, e.g. "No nulls in 12,480 rows."
    rationale: str | None
    #: The check created on accept; null while pending, when rejected, or once that check is gone.
    check_id: uuid.UUID | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime


@router.get(
    "/suites/{suite_id}/suggestions",
    response_model=list[SuggestionRead],
    summary="A suite's proposed rules (automatic coverage review queue)",
)
def list_suggestions(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[Literal["pending", "accepted", "rejected", "all"], Query()] = "pending",
) -> list[Any]:
    return svc.list_suggestions(
        db, suite_id, user_id=current_user.id, status=None if status == "all" else status
    )


@router.post(
    "/suggestions/{suggestion_id}/accept",
    response_model=SuggestionRead,
    summary="Accept a proposed rule: it becomes a check in the suite",
)
def accept_suggestion(
    suggestion_id: uuid.UUID,
    current_user: MemberUser,
    db: Annotated[Session, Depends(get_db)],
) -> Any:
    return svc.accept(db, suggestion_id, user_id=current_user.id)


@router.post(
    "/suggestions/{suggestion_id}/reject",
    response_model=SuggestionRead,
    summary="Reject a proposed rule; it is not proposed again",
)
def reject_suggestion(
    suggestion_id: uuid.UUID,
    current_user: MemberUser,
    db: Annotated[Session, Depends(get_db)],
) -> Any:
    return svc.reject(db, suggestion_id, user_id=current_user.id)
