"""Suite share endpoints — manage who can access a suite and at what level.

Nested under the suite. Managing shares requires `admin` on the suite (owner
qualifies); listing requires `view`. Authorization + the 404-hide / 403 split
live in `suite_authz`; this layer just wires the current user as the actor.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.db.models import Share, User
from backend.app.db.session import get_db
from backend.app.services import share_service as svc

router = APIRouter(tags=["shares"])

# Grantable share levels. NOT 'owner' (the immutable creator, not a share) and
# NOT 'admin' — admin is the workspace-admin, implicit on every suite, never
# granted to a normal user (ADR 0027 / #482).
SharePermission = Literal["view", "edit"]


class ShareCreate(ApiModel):
    user_id: uuid.UUID
    permission: SharePermission


class ShareUpdate(ApiModel):
    permission: SharePermission


class ShareRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    suite_id: uuid.UUID
    user_id: uuid.UUID
    permission: str
    # The grantee's directory identity, joined from `Share.user` so the sharing
    # UI can name collaborators without a second lookup per row.
    email: str
    display_name: str | None

    @classmethod
    def from_share(cls, share: Share) -> ShareRead:
        return cls(
            suite_id=share.suite_id,
            user_id=share.user_id,
            permission=share.permission,
            email=share.user.email,
            display_name=share.user.display_name,
        )


@router.post(
    "/suites/{suite_id}/shares",
    response_model=ShareRead,
    status_code=status.HTTP_201_CREATED,
    summary="Share a suite with a user",
)
def grant_share(
    suite_id: uuid.UUID,
    payload: ShareCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ShareRead:
    share = svc.grant_share(
        db,
        suite_id,
        actor_id=current_user.id,
        target_user_id=payload.user_id,
        permission=payload.permission,
    )
    return ShareRead.from_share(share)


@router.get(
    "/suites/{suite_id}/shares",
    response_model=list[ShareRead],
    summary="List a suite's shares",
)
def list_shares(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ShareRead]:
    return [
        ShareRead.from_share(s) for s in svc.list_shares(db, suite_id, actor_id=current_user.id)
    ]


@router.patch(
    "/suites/{suite_id}/shares/{user_id}",
    response_model=ShareRead,
    summary="Change a user's permission on a suite",
)
def update_share(
    suite_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: ShareUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ShareRead:
    share = svc.update_share(
        db, suite_id, user_id, actor_id=current_user.id, permission=payload.permission
    )
    return ShareRead.from_share(share)


@router.delete(
    "/suites/{suite_id}/shares/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a user's share",
)
def revoke_share(
    suite_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    svc.revoke_share(db, suite_id, user_id, actor_id=current_user.id)
