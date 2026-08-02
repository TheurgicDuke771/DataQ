from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import ConfigDict, field_validator
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user, is_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import user_service

router = APIRouter(tags=["auth"])

#: `users.display_name` is `String(256)` (backend/app/db/models.py) — kept in
#: sync with the column, not re-derived from it, since a migration bumping the
#: column doesn't retroactively loosen this validator.
DISPLAY_NAME_MAX_LEN = 256


class MeResponse(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # NULL for an identity with no Azure AD object id — e.g. an email-OTP user
    # (ADR 0032 decision 6, #735). `response_model` validation is strict, so a
    # non-optional annotation here would turn every /me call by such a user into a
    # 500 the moment #734 starts provisioning them.
    aad_object_id: str | None
    email: str
    display_name: str | None
    last_seen_at: datetime | None
    # Whether this user may use the /admin endpoints — the frontend gates the
    # Admin nav item + route on it (server-side authz still enforces; this only
    # decides what to render). Not a User column: defaulted here so the passthrough
    # fields still load straight off the ORM object, then stamped in the handler.
    is_workspace_admin: bool = False


@router.get("/me", response_model=MeResponse, summary="Get the current user")
def me(current_user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    """Return the authenticated user's profile plus their workspace-admin flag.

    The identity the rest of the app keys off (resolved from the Azure AD token,
    or the dev-bypass user locally); the SPA reads `is_workspace_admin` to gate
    admin-only nav.
    """
    # model_validate keeps the passthrough fields automatic (a new User/MeResponse
    # column is picked up without editing this handler); only the computed flag is
    # stamped on.
    resp = MeResponse.model_validate(current_user)
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
    """Self-service profile update — currently just `display_name` (#1139).

    Auth is the same generic `get_current_user` seam as the GET handler, so it
    works identically for a cookie session, a PAT, or an Azure AD token — no
    mode-specific branching. Exists mainly for email-OTP users, whose row is
    JIT-provisioned with `display_name: NULL` (`otp_service.resolve_or_create_user`,
    ADR 0032) since the sign-in flow is credential-only; an AAD user's token
    already supplies a name (`_extract_claims`), but may still override it here —
    the row is the one place both identity paths converge.
    """
    updated = user_service.update_display_name(db, current_user, payload.display_name)
    resp = MeResponse.model_validate(updated)
    resp.is_workspace_admin = is_workspace_admin(updated)
    return resp
