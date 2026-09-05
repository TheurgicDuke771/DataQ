"""The offboarding pass — one guided admin action for a departing user (#1699).

Its own module beside `admin_members.py` and `admin_suites.py`: this composes
their primitives, it does not extend either axis.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import require_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import offboarding_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


class OwnedSuiteRead(ApiModel):
    id: UUID
    name: str
    check_count: int
    run_count: int
    result_count: int


class OffboardPreviewRead(ApiModel):
    user_id: UUID
    email: str
    display_name: str | None
    role: str
    #: True when the admin previewing this is the user being offboarded.
    is_self: bool
    #: The pass is refused while this is true — nothing below it would run.
    is_last_admin: bool
    #: `member` is the only state in which membership can be withdrawn here.
    membership_state: Literal["member", "not_a_member", "env_listed"]
    membership_id: UUID | None
    #: Why it cannot be, naming the env var when an allowlist is the reason.
    membership_note: str | None
    owned_suites: list[OwnedSuiteRead]
    #: Unrevoked, unexpired only — a PAT that already lapsed is not a live credential.
    open_api_key_count: int
    live_session_count: int


class OffboardRequest(ApiRequestModel):
    """`POST /admin/offboarding/{user_id}` body."""

    #: Required when the user owns any suite; ignored when they own none. A
    #: workspace viewer cannot inherit (422) — change their role first.
    new_owner_user_id: UUID | None = None
    #: The departing user keeps nothing by default — the opposite of the standalone
    #: transfer endpoint, because here they are leaving.
    keep_previous_owner_access: bool = False
    #: The user's own email address, typed by the admin. A mismatch is a 422.
    confirm_email: str = Field(min_length=3, max_length=320)


class OffboardReceiptRead(ApiModel):
    user_id: UUID
    email: str
    new_owner_user_id: UUID | None
    transferred_suite_ids: list[UUID]
    api_keys_revoked: int
    sessions_revoked: int
    membership_removed: bool
    #: Every step that did not run, with its reason — an empty list means all ran.
    skipped: list[dict[str, str]]


@router.get(
    "/offboarding/{user_id}/preview",
    response_model=OffboardPreviewRead,
    summary="What offboarding this user would do (admin)",
)
def preview_offboarding(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> OffboardPreviewRead:
    """Read-only. Nothing here is reserved or locked — a suite can change owner
    between this call and the pass, so treat the counts as of now.
    """
    view = svc.preview(db, user_id, actor=current_user)
    return OffboardPreviewRead(
        user_id=view.user_id,
        email=view.email,
        display_name=view.display_name,
        role=view.role,
        is_self=view.is_self,
        is_last_admin=view.is_last_admin,
        membership_state=view.membership_state,
        membership_id=view.membership_id,
        membership_note=view.membership_note,
        owned_suites=[
            OwnedSuiteRead(
                id=suite.id,
                name=suite.name,
                check_count=suite.check_count,
                run_count=suite.run_count,
                result_count=suite.result_count,
            )
            for suite in view.owned_suites
        ],
        open_api_key_count=view.open_api_key_count,
        live_session_count=view.live_session_count,
    )


@router.post(
    "/offboarding/{user_id}",
    response_model=OffboardReceiptRead,
    summary="Offboard a user in one audited pass (admin)",
)
def offboard_user(
    user_id: UUID,
    payload: OffboardRequest,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> OffboardReceiptRead:
    """Transfer every suite this user owns, revoke every PAT and browser session
    they hold, then withdraw their workspace membership — in one transaction.

    Their authored history stays: runs, results and checks keep pointing at them,
    and the user row itself is not deleted (erasure is the data-subject-rights
    endpoint, which is a different act). A step that cannot run is reported in
    `skipped` with its reason rather than silently passing.
    """
    receipt = svc.offboard(
        db,
        user_id,
        new_owner_user_id=payload.new_owner_user_id,
        confirm_email=payload.confirm_email,
        actor=current_user,
        keep_previous_owner_access=payload.keep_previous_owner_access,
    )
    return OffboardReceiptRead(
        user_id=receipt.user_id,
        email=receipt.email,
        new_owner_user_id=receipt.new_owner_user_id,
        transferred_suite_ids=receipt.transferred_suite_ids,
        api_keys_revoked=receipt.api_keys_revoked,
        sessions_revoked=receipt.sessions_revoked,
        membership_removed=receipt.membership_removed,
        skipped=receipt.skipped,
    )
