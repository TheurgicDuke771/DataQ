"""The automatic-coverage review queue (ADR 0047 §4): proposed rules a person accepts or rejects."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import MemberUser, get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import suggestion_service as svc
from backend.app.services.run_service import RedactionReason

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
    redacted_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Columns whose stored `config.value_set` is withheld (each value `<redacted>`) because "
            "the column is classified as sensitive now, by its warehouse tag, the suite's policy "
            "or the classifier. Such a suggestion cannot be accepted (409). For an AI-proposed "
            "rule the name and rationale are replaced too, as the model's wording may quote values."
        ),
    )
    redaction_reasons: dict[str, RedactionReason] = Field(
        default_factory=dict,
        description="Why each entry of `redacted_columns` is withheld, keyed by column.",
    )


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
    return svc.present(
        db,
        svc.list_suggestions(
            db, suite_id, user_id=current_user.id, status=None if status == "all" else status
        ),
    )


@router.post(
    "/suggestions/{suggestion_id}/accept",
    response_model=SuggestionRead,
    summary="Accept a proposed rule: it becomes a check in the suite",
    description=(
        "Refused with 409 `suggestion_values_sensitive` when the rule's value set is over a column "
        "classified as sensitive since it was proposed; reject it instead."
    ),
)
def accept_suggestion(
    suggestion_id: uuid.UUID,
    current_user: MemberUser,
    db: Annotated[Session, Depends(get_db)],
) -> Any:
    return svc.present(db, [svc.accept(db, suggestion_id, user_id=current_user.id)])[0]


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
    return svc.present(db, [svc.reject(db, suggestion_id, user_id=current_user.id)])[0]
