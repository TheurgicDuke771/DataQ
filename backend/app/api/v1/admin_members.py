"""Workspace-membership admin endpoints — ADR 0043 decision 2.

A separate module from `admin.py` so the membership write axis has its own file;
it mounts under the same `/admin` prefix and the same workspace-admin gate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import require_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import membership_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


class MemberRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    #: Seeds the user row at first sign-in; the Members role editor is authoritative after that.
    initial_role: str
    #: `auto_import` rows were admitted by the lockout guard and await review.
    source: Literal["admin", "auto_import"]
    invited_by_email: str | None
    created_at: datetime
    #: Set once this address has signed in at least once.
    user_id: UUID | None
    stored_role: str | None
    #: `pending` means admitted but never signed in — not a failure state.
    status: Literal["active", "pending"]


class MembershipRead(ApiModel):
    #: False while the table is empty, when every door behaves exactly as it did
    #: before membership existed.
    enforcement_active: bool
    #: Existing users the FIRST add would auto-import — the number the switch-on
    #: warning states rather than leaving to be discovered.
    unmanaged_user_count: int
    members: list[MemberRead]


class MemberCreate(ApiRequestModel):
    email: str = Field(min_length=3, max_length=320)
    initial_role: Literal["admin", "member", "viewer"] = "member"


class MemberAddedRead(ApiModel):
    member: MemberRead
    auto_imported_count: int
    enforcement_active: bool


def _read(row: svc.MemberRow) -> MemberRead:
    return MemberRead(
        id=row.id,
        email=row.email,
        initial_role=row.initial_role,
        source=row.source,
        invited_by_email=row.invited_by_email,
        created_at=row.created_at,
        user_id=row.user_id,
        stored_role=row.stored_role,
        status=row.status,
    )


@router.get("/members", response_model=MembershipRead, summary="Workspace members (admin)")
def list_members(db: Annotated[Session, Depends(get_db)]) -> MembershipRead:
    """Who is admitted to this workspace, and whether membership is being enforced.

    An empty list means enforcement is OFF, not that nobody has access: who may
    sign in is then decided entirely by the deployment's env allowlists.
    """
    view = svc.list_members(db)
    return MembershipRead(
        enforcement_active=view.enforcement_active,
        unmanaged_user_count=view.unmanaged_user_count,
        members=[_read(row) for row in view.members],
    )


@router.post(
    "/members",
    response_model=MemberAddedRead,
    status_code=status.HTTP_201_CREATED,
    summary="Admit an address to the workspace (admin)",
)
def add_member(
    payload: MemberCreate,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> MemberAddedRead:
    """Admit `email`. Their identity provider still has to let them authenticate —
    this grants admission, it does not create an account anywhere.

    The FIRST add turns enforcement on and imports every existing user as a
    provisional member in the same transaction, so nobody is evicted by the switch.
    """
    outcome = svc.add_member(
        db, email=payload.email, initial_role=payload.initial_role, actor=current_user
    )
    return MemberAddedRead(
        member=_read(outcome.member),
        auto_imported_count=outcome.auto_imported_count,
        enforcement_active=True,
    )


@router.delete(
    "/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a membership (admin)",
)
def remove_member(
    member_id: UUID,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
    confirm_self: Annotated[
        bool, Query(description="Required to remove your own membership.")
    ] = False,
) -> Response:
    """Withdraw a membership. It bites on that person's next request, whatever
    credential they hold — their browser session and every PAT they own stop
    resolving. Refused for the last stored-role admin.
    """
    svc.remove_member(db, member_id, actor=current_user, confirm_self=confirm_self)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/members/{member_id}/confirm",
    response_model=MemberRead,
    summary="Confirm an auto-imported member (admin)",
)
def confirm_member(
    member_id: UUID,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> MemberRead:
    """Clear the provisional flag on a row the switch-on import admitted.

    Confirming changes nothing about their access — they already have it. It
    records that an admin looked at the row and meant to keep it.
    """
    return _read(svc.confirm_member(db, member_id, actor=current_user))
