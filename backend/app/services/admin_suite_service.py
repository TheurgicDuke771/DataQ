"""Workspace-admin suite operations — revoke any grant, transfer ownership,
delete any suite (ADR 0027 grants + ADR 0033 roles).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.roles import VIEWER_ROLE, resolve_role
from backend.app.db.models import Share, Suite, User
from backend.app.services import audit_service, suite_service
from backend.app.services.admin_service import UserNotFoundError
from backend.app.services.share_service import ShareNotFoundError
from backend.app.services.suite_authz import cap_for_viewer
from backend.app.services.suite_service import SuiteNotFoundError

log = get_logger(__name__)


@dataclass(frozen=True)
class TransferResult:
    """What a transfer did — the new state plus what the previous owner keeps."""

    suite: Suite
    previous_owner_id: uuid.UUID | None
    #: The level the previous owner keeps, `None` when they keep nothing.
    previous_owner_permission: str | None


class SuiteTransferRejectedError(DataQError):
    status_code = 422
    code = "suite_transfer_rejected"


class SuiteTransferNoOpError(DataQError):
    status_code = 409
    code = "suite_transfer_noop"


def revoke_grant(
    session: Session, suite_id: uuid.UUID, grant_id: uuid.UUID, *, actor: User
) -> None:
    """Revoke any per-suite share as a workspace admin, share owner or not."""
    suite_service.get_suite(session, suite_id)
    share = session.execute(
        select(Share).where(Share.id == grant_id, Share.suite_id == suite_id).with_for_update()
    ).scalar_one_or_none()
    if share is None:
        raise ShareNotFoundError(
            "no such access grant on this suite",
            detail={"suite_id": str(suite_id), "grant_id": str(grant_id)},
        )
    target_user_id = share.user_id
    # The row is about to stop existing, and this payload is the only surviving record.
    before = audit_service.snapshot("share", share)
    session.delete(share)
    audit_service.record(
        session,
        action="suite_access.revoke",
        entity_type="share",
        entity_id=grant_id,
        actor=actor,
        before=before,
        after={"revoked": True, "admin_override": True},
    )
    session.commit()
    log.info(
        "admin_share_revoked",
        suite_id=str(suite_id),
        grant_id=str(grant_id),
        target_user_id=str(target_user_id),
        actor_id=str(actor.id),
    )


def transfer_ownership(
    session: Session,
    suite_id: uuid.UUID,
    *,
    new_owner_user_id: uuid.UUID,
    actor: User,
    keep_previous_owner_access: bool = True,
) -> TransferResult:
    """Hand a suite to another user."""
    # Everything below decides from LOCKED state: two concurrent transfers must not
    # each read the same previous owner and write two different ones.
    suite = session.execute(
        select(Suite).where(Suite.id == suite_id).with_for_update()
    ).scalar_one_or_none()
    if suite is None:
        raise SuiteNotFoundError("suite not found", detail={"suite_id": str(suite_id)})
    target = session.execute(
        select(User).where(User.id == new_owner_user_id).with_for_update()
    ).scalar_one_or_none()
    if target is None:
        raise UserNotFoundError("user not found", detail={"user_id": str(new_owner_user_id)})
    previous_owner_id = suite.created_by
    if previous_owner_id == new_owner_user_id:
        raise SuiteTransferNoOpError(
            "this user already owns the suite",
            detail={"suite_id": str(suite_id), "user_id": str(new_owner_user_id)},
        )
    target_role = resolve_role(target)
    if target_role == VIEWER_ROLE:
        raise SuiteTransferRejectedError(
            "a workspace viewer cannot own a suite — viewers are read-only; "
            "change their workspace role to member first",
            detail={"user_id": str(new_owner_user_id), "role": target_role},
        )

    # An owner outranks any grant, and `grant_share` refuses to share a suite with its
    # owner — so the new owner's own share row would render as a second, weaker grant.
    session.execute(
        sql_delete(Share).where(Share.suite_id == suite_id, Share.user_id == new_owner_user_id)
    )
    suite.created_by = new_owner_user_id

    kept: str | None = None
    if previous_owner_id is not None:
        existing = session.scalars(
            select(Share).where(Share.suite_id == suite_id, Share.user_id == previous_owner_id)
        ).first()
        if keep_previous_owner_access:
            previous_owner = session.get(User, previous_owner_id, with_for_update=True)
            role = resolve_role(previous_owner) if previous_owner is not None else VIEWER_ROLE
            if existing is None:
                kept = cap_for_viewer("edit", role)
                session.add(Share(suite_id=suite_id, user_id=previous_owner_id, permission=kept))
            else:
                # A stale `edit` on a since-demoted Viewer must not be reported as kept.
                kept = cap_for_viewer(existing.permission, role) or existing.permission
                existing.permission = kept
        elif existing is not None:
            session.delete(existing)

    audit_service.record(
        session,
        action="suite.transfer",
        entity_type="suite",
        entity_id=suite_id,
        actor=actor,
        before={
            "id": str(suite_id),
            "owner_id": str(previous_owner_id) if previous_owner_id else None,
        },
        after={
            "id": str(suite_id),
            "owner_id": str(new_owner_user_id),
            "previous_owner_permission": kept,
            "admin_override": True,
        },
    )
    session.commit()
    session.refresh(suite)
    log.info(
        "suite_ownership_transferred",
        suite_id=str(suite_id),
        previous_owner_id=str(previous_owner_id) if previous_owner_id else None,
        new_owner_id=str(new_owner_user_id),
        previous_owner_permission=kept,
        actor_id=str(actor.id),
    )
    return TransferResult(
        suite=suite, previous_owner_id=previous_owner_id, previous_owner_permission=kept
    )


def delete_any_suite(session: Session, suite_id: uuid.UUID, *, actor: User) -> dict[str, int]:
    """Delete any suite as a workspace admin, recording what the cascade destroyed."""
    impact = suite_service.deletion_impact(session, suite_id)
    suite_service.delete_suite(
        session,
        suite_id,
        actor_id=actor.id,
        audit_extra={"deleted": True, "admin_override": True, "impact": impact},
    )
    return impact
