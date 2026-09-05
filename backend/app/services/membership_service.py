"""In-app workspace membership — ADR 0043.

The table's own emptiness is the enforcement switch. While `workspace_members`
has no rows, `is_member` returns whatever the caller's own env allowlist decided,
so every door behaves exactly as it did before this module existed. Once the
table has a row, membership is `union(env allowlist, workspace_members)`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.identity import identity_log_fields, normalize_email
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    ADMIN_MEMBER_SOURCE,
    ADMIN_ROLE,
    AUTO_IMPORT_MEMBER_SOURCE,
    WORKSPACE_ROLES,
    User,
    WorkspaceMember,
)
from backend.app.services import audit_service

log = get_logger(__name__)

#: `audit_service` entity type for every event this module records.
AUDIT_ENTITY = "workspace_member"


class MembershipDeniedError(DataQError):
    """The credential is valid but its owner is not a member of this workspace."""

    def __init__(self) -> None:
        super().__init__(
            "This account is not a member of this DataQ workspace.",
            code="not_a_workspace_member",
            # 403, not 401: the credential IS valid, so re-authenticating would
            # loop the SPA forever. At /mcp the verifier turns this into a 401,
            # which ADR 0043 decision 4 records as a known asymmetry.
            status_code=403,
        )


class MemberNotFoundError(DataQError):
    status_code = 404
    code = "workspace_member_not_found"


class MembershipChangeRejectedError(DataQError):
    """A membership change the workspace's invariants forbid — never a silent no-op."""

    status_code = 409
    code = "membership_change_rejected"


# ── The predicate every door reads ────────────────────────────────────────────


def enforcement_active(db: Session, /) -> bool:
    """Whether any managed member exists — the switch itself (decision 3)."""
    return bool(db.execute(select(exists().select_from(WorkspaceMember))).scalar())


def _row_for(db: Session, normalized: str) -> WorkspaceMember | None:
    return db.execute(
        select(WorkspaceMember).where(func.lower(WorkspaceMember.email) == normalized)
    ).scalar_one_or_none()


def is_member(
    db: Session,
    email: str,
    *,
    env_allowed: bool,
    unmanaged_default: bool = True,
    settings: Settings | None = None,
) -> bool:
    """Whether `email` may hold or keep access to this workspace.

    `env_allowed` is an explicit env-allowlist entry, which stays grant-only
    (decision 7): it can admit, and can never remove somebody the table admits.
    Doors with no allowlist of their own pass False. It has no default, so a
    door cannot silently inherit somebody else's answer.

    `unmanaged_default` is what this door decides while the table is empty —
    its behaviour before this table existed. A door whose env allowlist is its
    whole rule (generic OIDC, OTP) passes its own verdict here; a door that has
    never had an app-side gate (Azure AD, sessions, PATs) leaves it True.
    """
    s = settings or get_settings()
    # Dev bypass is exempt, and the exemption is a mode predicate rather than a
    # comparison against DEV_BYPASS_EMAIL, which a caller on a real deployment
    # could supply (decision 5). Without it the local and eval stacks would be
    # unbootable the moment an admin wrote the first row. `dev_bypass_active`,
    # not `dev_bypass_allowed`: the latter stays true beside email OTP, where the
    # ladder picks OTP and the bypass identity is never minted.
    if s.dev_bypass_active:
        return True
    if env_allowed:
        return True
    if not enforcement_active(db):
        return unmanaged_default
    return _row_for(db, normalize_email(email)) is not None


def require_member(
    db: Session,
    email: str,
    *,
    door: str,
    env_allowed: bool = False,
    unmanaged_default: bool = True,
    settings: Settings | None = None,
) -> None:
    """`is_member`, raising at the door instead of returning False."""
    if is_member(
        db,
        email,
        env_allowed=env_allowed,
        unmanaged_default=unmanaged_default,
        settings=settings,
    ):
        return
    log.warning("auth_membership_denied", door=door, **identity_log_fields(email))
    raise MembershipDeniedError()


def initial_role_for(db: Session, email: str) -> str | None:
    """The pre-provisioned role for `email`, or None when it is not listed.

    Seeds a user row on the NEW-row branch only (decision 9) — the caller must
    never route it through an upsert's conflict branch, which would overwrite an
    in-app role change on every request.
    """
    row = _row_for(db, normalize_email(email))
    return row.initial_role if row is not None else None


# ── Admin CRUD ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MemberRow:
    id: uuid.UUID
    email: str
    initial_role: str
    source: str
    invited_by_email: str | None
    created_at: datetime
    #: The `users` row this address has signed in as, if any.
    user_id: uuid.UUID | None
    stored_role: str | None

    @property
    def status(self) -> str:
        return "active" if self.user_id is not None else "pending"


@dataclass(frozen=True)
class MembershipView:
    enforcement_active: bool
    #: Existing `users` rows the first managed add would auto-import (decision 8).
    unmanaged_user_count: int
    members: tuple[MemberRow, ...]


def _validate_email(email: str) -> str:
    normalized = normalize_email(email)
    local, at, domain = normalized.partition("@")
    if not at or not local or not domain or any(c.isspace() or c == "\x00" for c in normalized):
        raise MembershipChangeRejectedError(
            "that is not a usable email address",
            detail={"email": normalized[:64]},
        )
    if len(normalized) > 320:
        raise MembershipChangeRejectedError("email address is too long")
    return normalized


def _users_by_email(db: Session) -> dict[str, User]:
    return {normalize_email(u.email): u for u in db.scalars(select(User)).all()}


def list_members(db: Session) -> MembershipView:
    rows = db.scalars(select(WorkspaceMember).order_by(WorkspaceMember.created_at)).all()
    users = _users_by_email(db)
    inviters = {u.id: u.email for u in users.values()}
    members = tuple(
        MemberRow(
            id=row.id,
            email=row.email,
            initial_role=row.initial_role,
            source=row.source,
            invited_by_email=inviters.get(row.invited_by) if row.invited_by else None,
            created_at=row.created_at,
            user_id=(
                users[normalize_email(row.email)].id
                if normalize_email(row.email) in users
                else None
            ),
            stored_role=(
                users[normalize_email(row.email)].role
                if normalize_email(row.email) in users
                else None
            ),
        )
        for row in rows
    )
    listed = {normalize_email(row.email) for row in rows}
    return MembershipView(
        enforcement_active=bool(rows),
        unmanaged_user_count=sum(1 for email in users if email not in listed),
        members=members,
    )


def _auto_import(db: Session, *, exclude: str) -> int:
    """Admit every existing user row, provisionally, in the caller's transaction.

    Turning enforcement on can never evict a current user. These rows are marked
    `auto_import` because a `users` row proves somebody once signed in, not that
    they are still meant to be here — the Members page shows them for review.
    """
    count = 0
    seen: set[str] = {exclude}
    for user in db.scalars(select(User)).all():
        normalized = normalize_email(user.email)
        if normalized in seen:
            continue
        seen.add(normalized)
        db.add(
            WorkspaceMember(
                id=uuid.uuid4(),
                email=normalized,
                initial_role=user.role,
                source=AUTO_IMPORT_MEMBER_SOURCE,
                invited_by=None,
            )
        )
        count += 1
    return count


@dataclass(frozen=True)
class AddOutcome:
    member: MemberRow
    auto_imported_count: int


def add_member(db: Session, *, email: str, initial_role: str, actor: User) -> AddOutcome:
    """Admit `email`. The first managed add also turns enforcement on."""
    if initial_role not in WORKSPACE_ROLES:
        raise MembershipChangeRejectedError(
            f"unknown workspace role: {initial_role!r}",
            detail={"role": initial_role, "allowed": list(WORKSPACE_ROLES)},
        )
    normalized = _validate_email(email)
    if _row_for(db, normalized) is not None:
        raise MembershipChangeRejectedError(
            "that address is already a workspace member",
            detail={"email": normalized},
        )
    # Same transaction as the insert below, so the switch and the import commit
    # together or not at all — it is part of the first write, or it is a race.
    imported = 0 if enforcement_active(db) else _auto_import(db, exclude=normalized)
    row = WorkspaceMember(
        id=uuid.uuid4(),
        email=normalized,
        initial_role=initial_role,
        source=ADMIN_MEMBER_SOURCE,
        invited_by=actor.id,
    )
    db.add(row)
    audit_service.record(
        db,
        action="workspace_member.add",
        entity_type=AUDIT_ENTITY,
        entity_id=row.id,
        actor=actor,
        after={
            **(audit_service.snapshot(AUDIT_ENTITY, row) or {}),
            "auto_imported_count": imported,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # A concurrent first write imported the same address. Nothing partial
        # survives — the whole transaction went back.
        raise MembershipChangeRejectedError(
            "another membership change landed first; reload the members list and retry"
        ) from exc
    db.refresh(row)
    log.info(
        "workspace_member_added",
        member_id=str(row.id),
        source=row.source,
        auto_imported_count=imported,
        **identity_log_fields(normalized),
    )
    return AddOutcome(member=_reread(db, row.id), auto_imported_count=imported)


def _reread(db: Session, member_id: uuid.UUID) -> MemberRow:
    """One row through the SAME builder the list uses, so a response cannot
    carry a different computed shape than the table it lands in.
    """
    for row in list_members(db).members:
        if row.id == member_id:
            return row
    raise MemberNotFoundError("workspace member not found", detail={"member_id": str(member_id)})


def _locked_member(db: Session, member_id: uuid.UUID) -> WorkspaceMember:
    row = db.execute(
        select(WorkspaceMember).where(WorkspaceMember.id == member_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise MemberNotFoundError(
            "workspace member not found", detail={"member_id": str(member_id)}
        )
    return row


def remove_member(
    db: Session, member_id: uuid.UUID, *, actor: User, confirm_self: bool = False
) -> None:
    """Withdraw a membership. Bites on the removed user's next request."""
    # Unlocked read first, for existence and the self-check. The locks below are
    # then taken in one fixed order — admin users, admin memberships by id, the
    # target — so two concurrent removals queue instead of deadlocking.
    row = db.get(WorkspaceMember, member_id)
    if row is None:
        raise MemberNotFoundError(
            "workspace member not found", detail={"member_id": str(member_id)}
        )
    normalized = normalize_email(row.email)

    if normalized == normalize_email(actor.email) and not confirm_self:
        raise MembershipChangeRejectedError(
            "removing your own membership signs you out of this workspace — "
            "resend with confirm_self=true if you mean it",
            detail={"member_id": str(member_id)},
        )

    # Everything below decides from LOCKED state. Stored-role admins only: an
    # allowlist-resolved admin can vanish with the next deploy, so it cannot
    # satisfy the invariant it is the recovery path for.
    admin_emails = set(
        db.scalars(
            select(func.lower(User.email))
            .where(User.role == ADMIN_ROLE)
            .order_by(User.id)
            .with_for_update()
        ).all()
    )
    # Locking the admins' MEMBERSHIP rows is what makes the guard hold. Removing a
    # membership changes no `users` row, so two concurrent removals that locked
    # only `users` would both see two admins and both succeed, leaving none.
    admin_member_ids = (
        set(
            db.scalars(
                select(WorkspaceMember.id)
                .where(func.lower(WorkspaceMember.email).in_(admin_emails))
                .order_by(WorkspaceMember.id)
                .with_for_update()
            ).all()
        )
        if admin_emails
        else set()
    )
    row = _locked_member(db, member_id)
    if row.id in admin_member_ids and admin_member_ids <= {row.id}:
        raise MembershipChangeRejectedError(
            "this is the last admin in the workspace — promote another admin first",
            detail={"member_id": str(member_id)},
        )

    before = audit_service.snapshot(AUDIT_ENTITY, row)
    db.delete(row)
    audit_service.record(
        db,
        action="workspace_member.remove",
        entity_type=AUDIT_ENTITY,
        entity_id=member_id,
        actor=actor,
        before=before,
    )
    db.commit()
    log.info(
        "workspace_member_removed", member_id=str(member_id), **identity_log_fields(normalized)
    )


def confirm_member(db: Session, member_id: uuid.UUID, *, actor: User) -> MemberRow:
    """Clear the provisional flag on an auto-imported row (decision 8)."""
    row = _locked_member(db, member_id)
    if row.source == ADMIN_MEMBER_SOURCE:
        # Idempotent: a UI that re-submits an already-confirmed row should not
        # surface a failure.
        return _reread(db, member_id)
    before = audit_service.snapshot(AUDIT_ENTITY, row)
    row.source = ADMIN_MEMBER_SOURCE
    row.invited_by = actor.id
    audit_service.record(
        db,
        action="workspace_member.confirm",
        entity_type=AUDIT_ENTITY,
        entity_id=member_id,
        actor=actor,
        before=before,
        after=audit_service.snapshot(AUDIT_ENTITY, row),
    )
    db.commit()
    log.info("workspace_member_confirmed", member_id=str(member_id))
    return _reread(db, member_id)
