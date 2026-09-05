"""In-app workspace membership — ADR 0043.

The table's emptiness is the switch: with no rows `is_member` returns the
deployment's env-allowlist verdict, which is what every door did before this
module existed; with rows it is `union(env allowlists, the table)`.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, func, inspect, select
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.identity import allowlisted, identity_log_fields, normalize_email
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

#: Synthetic `source` for a row that exists only in the environment.
ENV_MEMBER_SOURCE = "env"

#: Namespace for the stable synthetic id of an env-listed row.
_ENV_ROW_NAMESPACE = uuid.UUID("6f9f5d2e-9a4c-4e5a-9a1f-0043004d4249")

#: Shared by the switch-on write (exclusive) and every sign-in that may create a
#: user row (shared), so the import cannot miss a concurrent first sign-in.
SIGNIN_LOCK_KEY = 4300431


class MembershipDeniedError(DataQError):
    """The credential is valid but its owner is not a member of this workspace."""

    def __init__(self) -> None:
        super().__init__(
            "This account is not a member of this DataQ workspace.",
            code="not_a_workspace_member",
            # 403, not 401: the credential IS valid, so re-authenticating would
            # loop the SPA forever. /mcp turns this into a 401 (decision 4).
            status_code=403,
        )


class MemberNotFoundError(DataQError):
    status_code = 404
    code = "workspace_member_not_found"


class MembershipChangeRejectedError(DataQError):
    """A membership change the workspace's invariants forbid — never a silent no-op."""

    status_code = 409
    code = "membership_change_rejected"


# ── The env half of the rule ──────────────────────────────────────────────────


class EnvVerdict(enum.Enum):
    """What this deployment's env allowlists say, before the table is consulted."""

    GRANTED = "granted"
    DENIED = "denied"
    NO_ALLOWLIST = "no_allowlist"


def _access_allowlists(s: Settings) -> list[tuple[frozenset[str], frozenset[str]]]:
    """The allowlists that gate a door on THIS deployment.

    An unconfigured mode contributes nothing: reading an unrelated mode's env var
    would deny an entire Azure-AD tenant, which has no app-side allowlist at all.
    """
    gates = []
    if s.generic_oidc_configured and s.oidc_allowlist_configured:
        gates.append((s.oidc_allowed_email_set, s.oidc_allowed_domain_set))
    if s.otp_auth_configured:
        gates.append((s.auth_otp_allowed_email_set, s.auth_otp_allowed_domain_set))
    return gates


def env_verdict(email: str, settings: Settings | None = None) -> EnvVerdict:
    """The env allowlists' verdict on `email`. `WORKSPACE_ADMIN_EMAILS` grants
    only (ADR 0033 decision 6): the way back into a locked-out workspace.
    """
    s = settings or get_settings()
    gates = _access_allowlists(s)
    if s.is_admin_email(email) or any(allowlisted(email, *gate) for gate in gates):
        return EnvVerdict.GRANTED
    return EnvVerdict.DENIED if gates else EnvVerdict.NO_ALLOWLIST


def env_listed_addresses(settings: Settings | None = None) -> tuple[str, ...]:
    """Every address an env var names outright — a domain entry names nobody."""
    s = settings or get_settings()
    addresses = set(s.workspace_admin_email_set)
    for emails, _ in _access_allowlists(s):
        addresses |= emails
    return tuple(sorted(addresses))


def env_allowed_domains(settings: Settings | None = None) -> tuple[str, ...]:
    """Domains an env var admits wholesale — un-enumerable, so shown as themselves."""
    s = settings or get_settings()
    domains: set[str] = set()
    for _, gate_domains in _access_allowlists(s):
        domains |= gate_domains
    return tuple(sorted(domains))


# ── The predicate every door reads ────────────────────────────────────────────


def _table_missing(db: Session, exc: ProgrammingError) -> bool:
    """Whether the failure was `workspace_members` not existing yet."""
    db.rollback()
    bind = db.get_bind()
    try:
        return not inspect(bind).has_table(WorkspaceMember.__tablename__)
    except Exception:  # pragma: no cover — the catalog read itself failed
        return False


def enforcement_active(db: Session, /) -> bool:
    """Whether any managed member exists — the switch itself (decision 3).

    A missing table reads as "not enforced" — what an empty one means — so an
    image rolled ahead of its migration does not 500 every request.
    """
    try:
        return bool(db.execute(select(exists().select_from(WorkspaceMember))).scalar())
    except ProgrammingError as exc:
        if not _table_missing(db, exc):
            raise
        log.warning("membership_table_missing", effect="membership is not enforced")
        return False


def _row_for(db: Session, normalized: str) -> WorkspaceMember | None:
    return db.execute(
        select(WorkspaceMember).where(func.lower(WorkspaceMember.email) == normalized)
    ).scalar_one_or_none()


def _decide(db: Session, email: str, s: Settings) -> tuple[bool, WorkspaceMember | None]:
    """`(admitted, the row that admitted them)`. The row is handed back so a
    caller needing `initial_role` does not repeat the lookup.
    """
    if s.dev_bypass_active:
        return True, None
    verdict = env_verdict(email, s)
    try:
        row = _row_for(db, normalize_email(email))
    except ProgrammingError as exc:
        if not _table_missing(db, exc):
            raise
        log.warning("membership_table_missing", effect="membership is not enforced")
        return verdict is not EnvVerdict.DENIED, None
    if row is not None or verdict is EnvVerdict.GRANTED:
        return True, row
    if enforcement_active(db):
        return False, None
    return verdict is not EnvVerdict.DENIED, None


def is_member(db: Session, email: str, *, settings: Settings | None = None) -> bool:
    """Whether `email` may hold or keep access. The env grant is derived here,
    not passed in: a door cannot be handed a different answer than its siblings.
    """
    return _decide(db, email, settings or get_settings())[0]


def require_member(
    db: Session, email: str, *, door: str, settings: Settings | None = None
) -> WorkspaceMember | None:
    """`is_member`, raising at the door instead of returning False."""
    allowed, row = _decide(db, email, settings or get_settings())
    if allowed:
        return row
    log.warning("auth_membership_denied", door=door, **identity_log_fields(email))
    raise MembershipDeniedError()


def initial_role_for(db: Session, email: str) -> str | None:
    """The pre-provisioned role for `email`, or None. New-row branch only
    (decision 9): a conflict branch would overwrite an in-app role change.
    """
    row = _row_for(db, normalize_email(email))
    return row.initial_role if row is not None else None


def _advisory_lock(db: Session, *, shared: bool) -> None:
    if db.get_bind().dialect.name != "postgresql":  # pragma: no cover — Postgres in CI
        return
    fn = func.pg_advisory_xact_lock_shared if shared else func.pg_advisory_xact_lock
    db.execute(select(fn(SIGNIN_LOCK_KEY)))


def lock_signin(db: Session) -> None:
    """Hold off a switch-on until this sign-in's user row has committed, so the
    import cannot miss it and leave that person neither imported nor a member.
    """
    _advisory_lock(db, shared=True)


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
    #: An env var names this address, so removing the table row does not revoke it.
    env_listed: bool

    @property
    def status(self) -> str:
        return "active" if self.user_id is not None else "pending"

    @property
    def removable(self) -> bool:
        return self.source != ENV_MEMBER_SOURCE


@dataclass(frozen=True)
class EnforcementStatus:
    #: The table has at least one row.
    enforcement_active: bool
    #: Whether any door is actually gated right now, and why not when it is not.
    enforced: bool
    enforced_reason: str | None


@dataclass(frozen=True)
class MembershipView:
    status: EnforcementStatus
    #: Existing `users` rows the first managed add would auto-import (decision 8).
    unmanaged_user_count: int
    #: Domains an env var admits wholesale — no row can represent them.
    env_allowed_domains: tuple[str, ...]
    members: tuple[MemberRow, ...]


def enforcement_status(db: Session, settings: Settings | None = None) -> EnforcementStatus:
    s = settings or get_settings()
    active = enforcement_active(db)
    if s.dev_bypass_active:
        return EnforcementStatus(active, False, "developer bypass is active on this deployment")
    if not active:
        return EnforcementStatus(False, False, "no members have been added yet")
    return EnforcementStatus(True, True, None)


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


def _env_row_id(normalized: str) -> uuid.UUID:
    return uuid.uuid5(_ENV_ROW_NAMESPACE, normalized)


def _member_row(
    row: WorkspaceMember,
    users: dict[str, User],
    inviters: dict[uuid.UUID, str],
    env_addresses: frozenset[str],
) -> MemberRow:
    normalized = normalize_email(row.email)
    user = users.get(normalized)
    return MemberRow(
        id=row.id,
        email=row.email,
        initial_role=row.initial_role,
        source=row.source,
        invited_by_email=inviters.get(row.invited_by) if row.invited_by else None,
        created_at=row.created_at,
        user_id=user.id if user else None,
        stored_role=user.role if user else None,
        env_listed=normalized in env_addresses,
    )


def _env_member_row(normalized: str, users: dict[str, User], now: datetime) -> MemberRow:
    user = users.get(normalized)
    return MemberRow(
        id=_env_row_id(normalized),
        email=normalized,
        initial_role=user.role if user else "",
        source=ENV_MEMBER_SOURCE,
        invited_by_email=None,
        created_at=now,
        user_id=user.id if user else None,
        stored_role=user.role if user else None,
        env_listed=True,
    )


def list_members(db: Session, settings: Settings | None = None) -> MembershipView:
    """Every admitted address, table-managed and env-listed alike: omitting an
    env-listed one reads as "not admitted", and removing the managed row of
    somebody the environment also names would look like a completed removal.
    """
    s = settings or get_settings()
    rows = db.scalars(select(WorkspaceMember).order_by(WorkspaceMember.created_at)).all()
    users = _users_by_email(db)
    inviters = {u.id: u.email for u in users.values()}
    env_addresses = frozenset(env_listed_addresses(s))
    members = [_member_row(row, users, inviters, env_addresses) for row in rows]
    listed = {normalize_email(row.email) for row in rows}
    now = datetime.now(tz=None).astimezone()
    members.extend(
        _env_member_row(address, users, now) for address in sorted(env_addresses - listed)
    )
    return MembershipView(
        status=enforcement_status(db, s),
        unmanaged_user_count=sum(1 for email in users if email not in listed),
        env_allowed_domains=env_allowed_domains(s),
        members=tuple(members),
    )


def _auto_import(db: Session, *, exclude: str) -> int:
    """Admit every existing user row, provisionally, in the caller's transaction.

    Enforcement can never evict a current user, but a `users` row proves somebody
    once signed in, not that they still belong — hence `auto_import`.
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
    status: EnforcementStatus


def add_member(
    db: Session,
    *,
    email: str,
    initial_role: str,
    actor: User,
    settings: Settings | None = None,
) -> AddOutcome:
    """Admit `email`. The first managed add also turns enforcement on."""
    s = settings or get_settings()
    if initial_role not in WORKSPACE_ROLES:
        raise MembershipChangeRejectedError(
            f"unknown workspace role: {initial_role!r}",
            detail={"role": initial_role, "allowed": list(WORKSPACE_ROLES)},
        )
    normalized = _validate_email(email)
    # Exclusive: the import below must not read `users` just before a sign-in
    # commits one.
    _advisory_lock(db, shared=False)
    if _row_for(db, normalized) is not None:
        raise MembershipChangeRejectedError(
            "that address is already a workspace member",
            detail={"email": normalized},
        )
    # Same transaction as the insert: switch and import commit together, or not.
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
    return AddOutcome(
        member=_reread(db, row.id, s),
        auto_imported_count=imported,
        status=enforcement_status(db, s),
    )


def _reread(db: Session, member_id: uuid.UUID, settings: Settings | None = None) -> MemberRow:
    """One row through the SAME builder the list uses, so a response cannot
    disagree with the table it lands in.
    """
    row = db.get(WorkspaceMember, member_id)
    if row is None:
        raise MemberNotFoundError(
            "workspace member not found", detail={"member_id": str(member_id)}
        )
    s = settings or get_settings()
    users = _users_by_email(db)
    inviters = {u.id: u.email for u in users.values()}
    return _member_row(row, users, inviters, frozenset(env_listed_addresses(s)))


def _locked_member(db: Session, member_id: uuid.UUID) -> WorkspaceMember:
    row = db.execute(
        select(WorkspaceMember).where(WorkspaceMember.id == member_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise MemberNotFoundError(
            "workspace member not found", detail={"member_id": str(member_id)}
        )
    return row


def _refuse_env_removal(db: Session, member_id: uuid.UUID, s: Settings) -> None:
    """No row to delete; name the variable instead of 404ing."""
    for address in env_listed_addresses(s):
        if _env_row_id(address) == member_id:
            raise MembershipChangeRejectedError(
                "this address is admitted by the environment, not by the members "
                "table — remove it from AUTH_OTP_ALLOWED_EMAILS / OIDC_ALLOWED_EMAILS "
                "/ WORKSPACE_ADMIN_EMAILS and restart",
                detail={"email": address},
            )


def remove_member(
    db: Session,
    member_id: uuid.UUID,
    *,
    actor: User,
    confirm_self: bool = False,
    settings: Settings | None = None,
) -> None:
    """Withdraw a membership. Bites on that person's next request."""
    s = settings or get_settings()
    row = db.get(WorkspaceMember, member_id)
    if row is None:
        _refuse_env_removal(db, member_id, s)
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

    # Local import: `admin_service` reaches `core.auth`, which imports this module.
    from backend.app.services.admin_service import RoleChangeRejectedError, assert_admin_remains

    # Only an admin's membership can breach the invariant; only it pays the locks.
    if _stored_role(db, normalized) == ADMIN_ROLE:
        try:
            assert_admin_remains(db, exclude_member_id=member_id)
        except RoleChangeRejectedError as exc:
            raise MembershipChangeRejectedError(
                "this is the last admin in the workspace — promote another admin first",
                detail={"member_id": str(member_id)},
            ) from exc
    row = _locked_member(db, member_id)

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


def _stored_role(db: Session, normalized: str) -> str | None:
    return db.scalars(select(User.role).where(func.lower(User.email) == normalized)).first()


def confirm_member(db: Session, member_id: uuid.UUID, *, actor: User) -> MemberRow:
    """Clear the provisional flag on an auto-imported row (decision 8)."""
    row = _locked_member(db, member_id)
    if row.source == ADMIN_MEMBER_SOURCE:
        # Idempotent: a re-submitted confirm must not surface a failure.
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
