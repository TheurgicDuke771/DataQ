"""Workspace-admin suite writes — revoke any grant, transfer ownership, delete any
suite (ADR 0027 grants + ADR 0033 roles). Read endpoints live in `admin.py`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import require_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import admin_suite_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


@router.delete(
    "/suites/{suite_id}/access/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke any per-suite access grant (admin)",
)
def revoke_grant(
    suite_id: UUID,
    grant_id: UUID,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Revoke a share on any suite — the suite's own panel is owner/admin-gated,
    this one is workspace-admin-gated and needs no grant on the suite itself.
    """
    svc.revoke_grant(db, suite_id, grant_id, actor=current_user)


class SuiteTransfer(ApiRequestModel):
    """`POST /admin/suites/{id}/transfer` body."""

    new_owner_user_id: UUID
    #: The previous owner keeps an `edit` grant by default (a `view` one when they
    #: are a workspace Viewer, who cannot hold `edit` — ADR 0033).
    keep_previous_owner_access: bool = True


class SuiteTransferResult(ApiModel):
    suite_id: UUID
    previous_owner_id: UUID | None
    new_owner_id: UUID
    #: The level the previous owner keeps, or `null` when they keep nothing —
    #: including the case where there was no previous owner to keep anything.
    previous_owner_permission: str | None


@router.post(
    "/suites/{suite_id}/transfer",
    response_model=SuiteTransferResult,
    summary="Transfer suite ownership (admin)",
)
def transfer_suite(
    suite_id: UUID,
    payload: SuiteTransfer,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteTransferResult:
    """Hand a suite to another user — the offboarding primitive."""
    result = svc.transfer_ownership(
        db,
        suite_id,
        new_owner_user_id=payload.new_owner_user_id,
        actor=current_user,
        keep_previous_owner_access=payload.keep_previous_owner_access,
    )
    return SuiteTransferResult(
        suite_id=result.suite.id,
        previous_owner_id=result.previous_owner_id,
        new_owner_id=payload.new_owner_user_id,
        previous_owner_permission=result.previous_owner_permission,
    )


@router.delete(
    "/suites/{suite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete any suite (admin)",
)
def delete_suite(
    suite_id: UUID,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Delete any suite in the workspace. The cascade is the same one the owner's
    own delete runs; the audit event records what it destroyed.
    """
    svc.delete_any_suite(db, suite_id, actor=current_user)
