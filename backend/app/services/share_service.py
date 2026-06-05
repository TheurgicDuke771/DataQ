"""Suite sharing — grant / list / update / revoke per-user permissions.

Managing shares requires `admin` on the suite (the owner always qualifies);
listing collaborators requires `view`. The target must be a real user and not
the suite's owner (the creator already has full, immutable access — a share row
for them would be meaningless). All authorization flows through
`suite_authz.require_permission`, so a caller without access gets a 404 (hidden)
and one with insufficient level gets a 403.

FastAPI-free: takes a `Session` + ids; the API layer passes `current_user.id` as
the actor.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Share, User
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)


class ShareNotFoundError(DataQError):
    status_code = 404
    code = "share_not_found"


class ShareConflictError(DataQError):
    status_code = 409
    code = "share_conflict"


class ShareTargetInvalidError(DataQError):
    status_code = 422
    code = "share_target_invalid"


def _get_share(session: Session, suite_id: uuid.UUID, user_id: uuid.UUID) -> Share:
    share = session.scalars(
        select(Share).where(Share.suite_id == suite_id, Share.user_id == user_id)
    ).first()
    if share is None:
        raise ShareNotFoundError(
            "no share for this user on this suite",
            detail={"suite_id": str(suite_id), "user_id": str(user_id)},
        )
    return share


def grant_share(
    session: Session,
    suite_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    target_user_id: uuid.UUID,
    permission: str,
) -> Share:
    """Grant `target_user_id` a permission on the suite. Actor needs `admin`."""
    suite = require_permission(session, suite_id, actor_id, minimum="admin")
    if target_user_id == suite.created_by:
        raise ShareTargetInvalidError(
            "cannot share a suite with its owner (already has full access)",
            detail={"suite_id": str(suite_id), "user_id": str(target_user_id)},
        )
    if session.get(User, target_user_id) is None:
        raise ShareTargetInvalidError(
            "target user does not exist", detail={"user_id": str(target_user_id)}
        )

    share = Share(suite_id=suite_id, user_id=target_user_id, permission=permission)
    session.add(share)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ShareConflictError(
            "user already has a share on this suite; use PATCH to change it",
            detail={"suite_id": str(suite_id), "user_id": str(target_user_id)},
        ) from exc
    session.refresh(share)
    log.info(
        "share_granted", suite_id=str(suite_id), user_id=str(target_user_id), permission=permission
    )
    return share


def list_shares(session: Session, suite_id: uuid.UUID, *, actor_id: uuid.UUID) -> list[Share]:
    """List a suite's shares. Actor needs `view` (collaborators can see who else has access)."""
    require_permission(session, suite_id, actor_id, minimum="view")
    return list(
        session.scalars(select(Share).where(Share.suite_id == suite_id).order_by(Share.created_at))
    )


def update_share(
    session: Session,
    suite_id: uuid.UUID,
    target_user_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    permission: str,
) -> Share:
    """Change a user's permission. Actor needs `admin`."""
    require_permission(session, suite_id, actor_id, minimum="admin")
    share = _get_share(session, suite_id, target_user_id)
    share.permission = permission
    session.commit()
    session.refresh(share)
    log.info(
        "share_updated", suite_id=str(suite_id), user_id=str(target_user_id), permission=permission
    )
    return share


def revoke_share(
    session: Session,
    suite_id: uuid.UUID,
    target_user_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
) -> None:
    """Revoke a user's share. Actor needs `admin`."""
    require_permission(session, suite_id, actor_id, minimum="admin")
    share = _get_share(session, suite_id, target_user_id)
    session.delete(share)
    session.commit()
    log.info("share_revoked", suite_id=str(suite_id), user_id=str(target_user_id))
