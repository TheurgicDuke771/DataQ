"""Offboarding — transfer a departing user's suites, revoke their credentials and
withdraw their membership in one audited pass (#1699).

Every step already exists as its own admin primitive. What this module adds is
that they run together or not at all: a half-finished offboarding (PATs revoked,
suites still owned by somebody who can no longer sign in) is worse than none.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.identity import normalize_email
from backend.app.core.logging import get_logger
from backend.app.db.models import ADMIN_ROLE, ApiKey, Suite, User, UserSession, WorkspaceMember
from backend.app.services import admin_suite_service, audit_service, membership_service
from backend.app.services.admin_service import UserNotFoundError
from backend.app.services.suite_service import deletion_impact

log = get_logger(__name__)

AUDIT_ACTION = "user.offboard"

#: Membership states the preview and the receipt share.
MEMBER = "member"
NOT_A_MEMBER = "not_a_member"
ENV_LISTED = "env_listed"


class OffboardRejectedError(DataQError):
    status_code = 422
    code = "offboard_rejected"


class OffboardBlockedError(DataQError):
    """An invariant of the workspace forbids the pass — never a partial run."""

    status_code = 409
    code = "offboard_blocked"


@dataclass(frozen=True)
class OwnedSuite:
    id: uuid.UUID
    name: str
    check_count: int
    run_count: int
    result_count: int


@dataclass(frozen=True)
class OffboardPreview:
    user_id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    #: True when the admin running this is the user being offboarded.
    is_self: bool
    #: Refused up front — the workspace would be left with no stored-role admin.
    is_last_admin: bool
    membership_state: str
    membership_id: uuid.UUID | None
    #: Why membership cannot be withdrawn here, naming the env var when that is why.
    membership_note: str | None
    owned_suites: tuple[OwnedSuite, ...]
    open_api_key_count: int
    live_session_count: int


@dataclass
class OffboardReceipt:
    user_id: uuid.UUID
    email: str
    new_owner_user_id: uuid.UUID | None
    transferred_suite_ids: list[uuid.UUID] = field(default_factory=list)
    api_keys_revoked: int = 0
    sessions_revoked: int = 0
    membership_removed: bool = False
    #: `[{"step": ..., "reason": ...}]` — every step that did not run, and why.
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "email": self.email,
            "new_owner_user_id": str(self.new_owner_user_id) if self.new_owner_user_id else None,
            "transferred_suite_ids": [str(sid) for sid in self.transferred_suite_ids],
            "transferred_suite_count": len(self.transferred_suite_ids),
            "api_keys_revoked": self.api_keys_revoked,
            "sessions_revoked": self.sessions_revoked,
            "membership_removed": self.membership_removed,
            "skipped": self.skipped,
        }


# ── Reads ─────────────────────────────────────────────────────────────────────

#: Settings property → the env var an operator would edit. The preview names the
#: variable, not the attribute.
_EMAIL_ALLOWLISTS = {
    "oidc_allowed_email_set": "OIDC_ALLOWED_EMAILS",
    "auth_otp_allowed_email_set": "AUTH_OTP_ALLOWED_EMAILS",
}
_DOMAIN_ALLOWLISTS = {
    "oidc_allowed_domain_set": "OIDC_ALLOWED_DOMAINS",
    "auth_otp_allowed_domain_set": "AUTH_OTP_ALLOWED_DOMAINS",
}


def env_allowlists_naming(email: str, settings: Settings | None = None) -> list[str]:
    """The env vars that would keep `email` signing in after the row is gone.

    Membership is `union(env allowlist, workspace_members)` (ADR 0043 decision 7),
    so withdrawing the row is not a removal while any of these still list them.
    """
    s = settings or get_settings()
    normalized = normalize_email(email)
    _, _, domain = normalized.partition("@")
    naming = [var for attr, var in _EMAIL_ALLOWLISTS.items() if normalized in getattr(s, attr)]
    naming += [
        var for attr, var in _DOMAIN_ALLOWLISTS.items() if domain and domain in getattr(s, attr)
    ]
    if normalized in s.workspace_admin_email_set:
        naming.append("WORKSPACE_ADMIN_EMAILS")
    return sorted(set(naming))


def _load_user(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> User:
    stmt = select(User).where(User.id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    user = db.execute(stmt).scalar_one_or_none()
    if user is None:
        raise UserNotFoundError("user not found", detail={"user_id": str(user_id)})
    return user


def _is_last_admin(db: Session, user: User, *, lock: bool = False) -> bool:
    """Stored-role admins only — an allowlist-resolved admin can vanish with the
    next deploy, so it cannot satisfy the invariant it is the recovery path for.
    """
    if user.role != ADMIN_ROLE:
        return False
    stmt = select(User.id).where(User.role == ADMIN_ROLE).order_by(User.id)
    if lock:
        stmt = stmt.with_for_update()
    return set(db.scalars(stmt).all()) <= {user.id}


def _owned_suites(db: Session, user_id: uuid.UUID) -> list[Suite]:
    return list(
        db.scalars(
            select(Suite).where(Suite.created_by == user_id).order_by(Suite.created_at, Suite.id)
        ).all()
    )


def _open_api_keys(db: Session, user_id: uuid.UUID) -> list[ApiKey]:
    now = datetime.now(UTC)
    return list(
        db.scalars(
            select(ApiKey)
            .where(
                ApiKey.user_id == user_id,
                ApiKey.revoked_at.is_(None),
                ApiKey.expires_at > now,
            )
            .order_by(ApiKey.created_at, ApiKey.id)
        ).all()
    )


def _live_sessions(db: Session, user_id: uuid.UUID) -> list[UserSession]:
    now = datetime.now(UTC)
    return list(
        db.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.id)
        ).all()
    )


def _membership(
    db: Session, email: str, settings: Settings | None = None
) -> tuple[str, uuid.UUID | None, str | None]:
    """(state, membership_id, note) — what step (d) will be able to do."""
    naming = env_allowlists_naming(email, settings)
    row = db.execute(
        select(WorkspaceMember).where(func.lower(WorkspaceMember.email) == normalize_email(email))
    ).scalar_one_or_none()
    if naming:
        return (
            ENV_LISTED,
            row.id if row is not None else None,
            "this address is listed in "
            + " and ".join(naming)
            + " — an env allowlist admits on its own, so removing the row here would "
            "not withdraw access; remove it there instead",
        )
    if row is None:
        return (
            NOT_A_MEMBER,
            None,
            "no workspace membership row — this workspace is not enforcing membership "
            "for this address, so there is nothing to withdraw",
        )
    return MEMBER, row.id, None


def preview(db: Session, user_id: uuid.UUID, *, actor: User) -> OffboardPreview:
    """What the pass would do, before anything is written."""
    user = _load_user(db, user_id)
    state, member_id, note = _membership(db, user.email)
    return OffboardPreview(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_self=user.id == actor.id,
        is_last_admin=_is_last_admin(db, user),
        membership_state=state,
        membership_id=member_id,
        membership_note=note,
        owned_suites=tuple(_owned_suite_rows(db, user_id)),
        open_api_key_count=len(_open_api_keys(db, user_id)),
        live_session_count=len(_live_sessions(db, user_id)),
    )


def _owned_suite_rows(db: Session, user_id: uuid.UUID) -> list[OwnedSuite]:
    rows = []
    for suite in _owned_suites(db, user_id):
        impact = deletion_impact(db, suite.id)
        rows.append(
            OwnedSuite(
                id=suite.id,
                name=suite.name,
                check_count=impact["checks"],
                run_count=impact["runs"],
                result_count=impact["results"],
            )
        )
    return rows


# ── The pass ──────────────────────────────────────────────────────────────────


def offboard(
    db: Session,
    user_id: uuid.UUID,
    *,
    new_owner_user_id: uuid.UUID | None,
    confirm_email: str,
    actor: User,
    keep_previous_owner_access: bool = False,
) -> OffboardReceipt:
    """Run the whole pass, or none of it.

    The primitives composed below each end in `commit()`, which is right when one
    of them is the whole request and wrong here. `inner` runs on the caller's own
    connection with `join_transaction_mode="create_savepoint"`, so those commits
    release savepoints inside ONE transaction that only `db` can end — a failure
    at any step takes every earlier step back with it.
    """
    inner = Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    try:
        receipt = _run(
            inner,
            user_id,
            new_owner_user_id=new_owner_user_id,
            confirm_email=confirm_email,
            actor=actor,
            keep_previous_owner_access=keep_previous_owner_access,
        )
    except BaseException:
        # `inner` releases its savepoint state first, and the `finally` makes the
        # outer rollback run even if that close is what failed.
        try:
            inner.close()
        finally:
            db.rollback()
        raise
    inner.close()
    db.commit()
    db.expire_all()
    log.info(
        "user_offboarded",
        user_id=str(user_id),
        actor_id=str(actor.id),
        transferred_suite_count=len(receipt.transferred_suite_ids),
        api_keys_revoked=receipt.api_keys_revoked,
        sessions_revoked=receipt.sessions_revoked,
        membership_removed=receipt.membership_removed,
        skipped_steps=[step["step"] for step in receipt.skipped],
    )
    return receipt


def _run(
    db: Session,
    user_id: uuid.UUID,
    *,
    new_owner_user_id: uuid.UUID | None,
    confirm_email: str,
    actor: User,
    keep_previous_owner_access: bool,
) -> OffboardReceipt:
    # (a) Guards, from locked state.
    user = _load_user(db, user_id, lock=True)
    if _is_last_admin(db, user, lock=True):
        raise OffboardBlockedError(
            "this is the last admin in the workspace — promote another admin first",
            detail={"user_id": str(user_id)},
        )
    if normalize_email(confirm_email) != normalize_email(user.email):
        raise OffboardRejectedError(
            "the typed confirmation does not match this user's email address",
            detail={"user_id": str(user_id)},
        )
    if new_owner_user_id == user_id:
        raise OffboardRejectedError(
            "the departing user cannot inherit their own suites",
            detail={"user_id": str(user_id)},
        )

    receipt = OffboardReceipt(
        user_id=user.id, email=user.email, new_owner_user_id=new_owner_user_id
    )

    # (b) Suites first: a failure here must not already have cost the user their
    # credentials.
    suites = _owned_suites(db, user_id)
    if suites and new_owner_user_id is None:
        raise OffboardRejectedError(
            f"this user owns {len(suites)} suite(s) — choose who inherits them",
            detail={"owned_suite_count": len(suites)},
        )
    for suite in suites:
        assert new_owner_user_id is not None
        admin_suite_service.transfer_ownership(
            db,
            suite.id,
            new_owner_user_id=new_owner_user_id,
            actor=actor,
            keep_previous_owner_access=keep_previous_owner_access,
        )
        receipt.transferred_suite_ids.append(suite.id)
    if not suites:
        receipt.skipped.append({"step": "transfer_suites", "reason": "this user owns no suites"})

    # (c) Credentials. Written here rather than through `api_key_service.revoke_key`,
    # which attributes the revoke to the key's OWNER — on an offboarding the actor is
    # the admin, and a trail saying the departing user revoked their own key is worse
    # than no trail.
    now = datetime.now(UTC)
    for key in _open_api_keys(db, user_id):
        before = audit_service.snapshot("api_key", key)
        key.revoked_at = now
        audit_service.record_entity_change(
            db,
            action="api_key.revoke",
            entity_type="api_key",
            entity=key,
            actor=actor,
            before=before,
        )
        receipt.api_keys_revoked += 1
    for row in _live_sessions(db, user_id):
        row.revoked_at = now
        receipt.sessions_revoked += 1
    db.flush()

    # (d) Membership last: while it stands, an admin can still see the user in the
    # list and undo the steps above.
    state, member_id, note = _membership(db, user.email)
    if state == MEMBER and member_id is not None:
        membership_service.remove_member(
            db, member_id, actor=actor, confirm_self=user.id == actor.id
        )
        receipt.membership_removed = True
    else:
        receipt.skipped.append({"step": "remove_membership", "reason": note or state})

    # (e) `created_by` history is deliberately untouched: the runs, results and
    # checks this user authored survive them leaving.
    audit_service.record(
        db,
        action=AUDIT_ACTION,
        entity_type="user",
        entity_id=user.id,
        actor=actor,
        before=audit_service.snapshot("user", user),
        after=receipt.as_payload(),
    )
    db.commit()
    return receipt
