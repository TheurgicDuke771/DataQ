from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import ConfigDict, field_validator
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.core.roles import is_workspace_admin, resolve_role
from backend.app.db.models import DEFAULT_WORKSPACE_ROLE, User
from backend.app.db.session import get_db
from backend.app.services import user_service

router = APIRouter(tags=["auth"])

#: `users.display_name` is `String(256)` (backend/app/db/models.py) — kept in sync with the column,
#: not re-derived from it.
DISPLAY_NAME_MAX_LEN = 256


class MeResponse(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # NULL for an identity with no Azure AD object id — e.g. an email-OTP user (ADR 0032 decision 6,
    # #735).
    aad_object_id: str | None
    email: str
    display_name: str | None
    last_seen_at: datetime | None
    # The caller's EFFECTIVE workspace role (ADR 0033) — `admin | member | viewer`.
    role: str = DEFAULT_WORKSPACE_ROLE
    # Whether this user may use the /admin endpoints — the frontend gates the Admin nav item + route
    # on it (server-side authz still enforces; this only decides what to render).
    is_workspace_admin: bool = False


@router.get("/me", response_model=MeResponse, summary="Get the current user")
def me(current_user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    """Return the authenticated user's profile plus their workspace-admin flag."""
    # model_validate keeps the passthrough fields automatic (a new User/MeResponse column is picked
    # up without editing this handler); only the computed flag is stamped on.
    resp = MeResponse.model_validate(current_user)
    resp.role = resolve_role(current_user)
    resp.is_workspace_admin = is_workspace_admin(current_user)
    return resp


class MeUpdate(ApiModel):
    """`PATCH /me` body — today, just the display name (#1139)."""

    display_name: str

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must not be empty or whitespace-only")
        if len(stripped) > DISPLAY_NAME_MAX_LEN:
            raise ValueError(f"display_name must be at most {DISPLAY_NAME_MAX_LEN} characters")
        return stripped


@router.patch("/me", response_model=MeResponse, summary="Update the current user's profile")
def update_me(
    payload: MeUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MeResponse:
    """Self-service profile update — currently just `display_name` (#1139)."""
    updated = user_service.update_display_name(db, current_user, payload.display_name)
    resp = MeResponse.model_validate(updated)
    resp.role = resolve_role(updated)
    resp.is_workspace_admin = is_workspace_admin(updated)
    return resp
