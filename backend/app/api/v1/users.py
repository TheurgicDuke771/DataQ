"""User directory endpoints — search users to grant suite access to.

The sharing UI keys shares on a raw `user_id`, but a human picks a collaborator
by email/name; this endpoint is the type-ahead behind that picker. Single
tenant, so any authenticated user may search the directory. Only a minimal
public summary (id / email / display_name) is exposed — never AAD object ids or
timestamps.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.core.roles import resolve_role
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import user_service as svc

router = APIRouter(tags=["users"])


class UserSummary(ApiModel):
    """The public sliver of a user safe to expose in the directory picker."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    #: The user's EFFECTIVE workspace role (ADR 0033) — the effective one, not
    #: the stored column, because that is what `share_service` checks when it
    #: rejects an `edit` grant to a Viewer. A break-glass allowlist admin whose
    #: row still reads `member` can hold `edit`, and a picker reading the stored
    #: value would wrongly refuse them.
    #:
    #: Exposing it here is a deliberate, bounded widening: this directory already
    #: returns every authenticated caller an email and a display name, and a
    #: coarse role is workspace metadata of the same kind — not a credential, not
    #: a per-suite grant. It exists so the share picker can mirror the backend's
    #: rule instead of offering a level the server will refuse.
    role: str


@router.get(
    "/users/search",
    response_model=list[UserSummary],
    summary="Search the user directory by email or display name",
)
def search_users(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str, Query(description="Email or name substring (min 2 chars).")] = "",
    limit: Annotated[int, Query(ge=1, le=svc.MAX_LIMIT)] = svc.DEFAULT_LIMIT,
) -> list[UserSummary]:
    users = svc.search_users(db, q, limit=limit)
    return [
        UserSummary(id=u.id, email=u.email, display_name=u.display_name, role=resolve_role(u))
        for u in users
    ]
