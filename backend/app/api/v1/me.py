from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from backend.app.core.auth import get_current_user, is_workspace_admin
from backend.app.db.models import User

router = APIRouter(tags=["auth"])


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    aad_object_id: str
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
