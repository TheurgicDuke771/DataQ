"""Connection CRUD + connectivity test, datasource-type-agnostic."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secret_names import connection_secret_ref
from backend.app.core.secrets import SecretNotFoundError, SecretStore, SecretWriteError
from backend.app.datasources.registry import (
    UnsupportedConnectionTypeError,
    credential_expiry,
    destination_fields,
    get_connection_adapter,
)
from backend.app.db.models import (
    CHECK_ORDER,
    ENVS,
    Check,
    Connection,
    ConnectionVersion,
    Run,
    Suite,
)
from backend.app.services import audit_service
from backend.app.services.asset_service import resolve_and_upsert_asset
from backend.app.services.suite_service import accessible_suite_ids

log = get_logger(__name__)


class ConnectionNotFoundError(DataQError):
    status_code = 404
    code = "connection_not_found"


class ConnectionConfigInvalidError(DataQError):
    status_code = 422
    code = "connection_config_invalid"


class ConnectionConflictError(DataQError):
    status_code = 409
    code = "connection_conflict"


class ConnectionTestFailedError(DataQError):
    status_code = 502
    code = "connection_test_failed"


class ConnectionSecretWriteError(DataQError):
    status_code = 502
    code = "connection_secret_write_failed"


class ConnectionInUseError(DataQError):
    # A comparison check references this connection as its source (ADR 0015): the FK is ON DELETE
    # RESTRICT.
    status_code = 409
    code = "connection_in_use"


def _extra_secrets(config: Mapping[str, Any], secret_store: SecretStore) -> dict[str, str]:
    """Resolve every *additional* credential a connection's config names, by convention."""
    out: dict[str, str] = {}
    for key, value in config.items():
        if not key.endswith("_secret_name") or not isinstance(value, str) or not value:
            continue
        try:
            out[key.removesuffix("_name")] = secret_store.get(value)
        except SecretNotFoundError:
            log.warning("connection_extra_secret_missing", secret_field=key)
    return out


class ForeignSecretReferenceError(DataQError):
    """A caller-supplied `*_secret_name` naming a secret it does not own (#1118)."""

    status_code = 422
    code = "foreign_secret_reference"


def _reject_foreign_secret_names(
    config: Mapping[str, Any], *, stored: Mapping[str, Any] | None
) -> None:
    """Reject a `*_secret_name` a caller invented — closes #1118."""
    for key, value in config.items():
        if not key.endswith("_secret_name") or not isinstance(value, str):
            continue
        # An EMPTY string is checked too, not skipped as "absent".
        if stored is None or stored.get(key) != value:
            raise ForeignSecretReferenceError(
                f"'{key}' is set by the server and cannot be supplied or changed by a "
                "client; send the credential itself (e.g. 'catalog_secret') instead",
                detail={"field": key},
            )


class CredentialRedirectError(DataQError):
    """A config change that moves a credential's destination without re-supplying
    the credential (#1401).
    """

    status_code = 422
    code = "credential_redirect"


def _reject_uncredentialed_redirect(
    conn_type: str,
    *,
    stored: Mapping[str, Any],
    incoming: Mapping[str, Any],
    has_stored_secret: bool,
    supplied_secret: str | None,
    supplied_extra_secrets: Mapping[str, str | None],
) -> None:
    """Refuse to point a STORED credential at a new host — closes #1401."""
    moved: set[str] = set()
    missing: list[str] = []
    for slot, fields in sorted(destination_fields(conn_type).items()):
        slot_moved = [f for f in fields if stored.get(f) != incoming.get(f)]
        if not slot_moved:
            continue
        if slot == "secret":
            if not has_stored_secret or supplied_secret is not None:
                continue
            missing.append("secret")
        # An extra credential is "stored" iff config carries its ref, by the same `*_secret_name`
        # suffix convention `_extra_secrets` resolves by.
        elif stored.get(f"{slot}_secret_name") and supplied_extra_secrets.get(slot) is None:
            missing.append(f"{slot}_secret")
        else:
            continue
        # Only the fields belonging to a slot that actually WENT unsatisfied — a message naming a
        # field the caller already covered sends them looking in the wrong place.
        moved.update(slot_moved)

    if missing:
        raise CredentialRedirectError(
            f"changing {', '.join(repr(f) for f in sorted(moved))} moves where this "
            f"connection's credentials are sent, so {', '.join(repr(m) for m in missing)} "
            "must be re-supplied in the same request",
            detail={"fields": sorted(moved), "required": missing},
        )


def _validate_extra_secret_supported(conn_type: str, config: Mapping[str, Any], field: str) -> None:
    """Reject a `<field>_secret` a connection TYPE's config model can't receive."""
    adapter = get_connection_adapter(conn_type)
    try:
        adapter.validate_config({**config, f"{field}_secret_name": "probe"})
    except ValidationError as exc:
        raise ConnectionConfigInvalidError(
            f"{conn_type!r} connections do not accept a {field!r} credential",
            detail={"errors": exc.errors()},
        ) from exc


def _write_extra_secret(
    conn: Connection, value: str, secret_store: SecretStore, *, field: str
) -> None:
    """Write a connection's SECOND credential through the store and point
    `config.<field>_secret_name` at it — the create/update-time counterpart to
    `_extra_secrets` resolving it back out.
    """
    name_key = f"{field}_secret_name"
    ref = (conn.config or {}).get(name_key) or connection_secret_ref(
        connection_id=conn.id, env=conn.env, name=conn.name, conn_type=conn.type, kind=field
    )
    secret_store.set(ref, value)
    conn.config = {**conn.config, name_key: ref}


def _carry_over_secret_name_keys(
    old_config: Mapping[str, Any], new_config: dict[str, Any]
) -> dict[str, Any]:
    """Preserve every `*_secret_name` key `old_config` has that `new_config` omits."""
    carried = {
        key: value
        for key, value in old_config.items()
        if key.endswith("_secret_name") and key not in new_config
    }
    return {**new_config, **carried} if carried else new_config


def _validated_config(conn_type: str, config: dict[str, Any]) -> None:
    """Reject an unknown type or a config that fails its adapter's schema."""
    try:
        adapter = get_connection_adapter(conn_type)
    except UnsupportedConnectionTypeError as exc:
        raise ConnectionConfigInvalidError(str(exc), detail={"type": conn_type}) from exc
    try:
        adapter.validate_config(config)
    except ValidationError as exc:
        raise ConnectionConfigInvalidError(
            f"Invalid config for {conn_type!r} connection",
            detail={"errors": exc.errors()},
        ) from exc


def _validate_env(env: str) -> None:
    """Reject an env outside the allowed set before it hits the DB CHECK."""
    if env not in ENVS:
        raise ConnectionConfigInvalidError(f"invalid env {env!r}", detail={"allowed": list(ENVS)})


# DB index that enforces one orchestration-provider connection per (type, env) — see the connections
# migration (#72 / ADR 0004).
_ORCHESTRATOR_UNIQUE_INDEX = "uq_connections_orchestrator_type_env"


def _conflict_from_integrity_error(
    exc: IntegrityError, *, conn_type: str, env: str
) -> ConnectionConflictError:
    """Map a unique-violation to the right 409, by which constraint fired."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    if constraint_name == _ORCHESTRATOR_UNIQUE_INDEX:
        return ConnectionConflictError(
            f"an orchestration connection of type {conn_type!r} already exists in env {env!r}",
            detail={"type": conn_type, "env": env},
        )
    return ConnectionConflictError(
        "a connection with this name already exists in this env",
        detail={"type": conn_type, "env": env},
    )


def record_connection_version(
    session: Session, conn: Connection, *, actor_id: uuid.UUID | None
) -> ConnectionVersion:
    """Append an immutable snapshot of `conn`'s current non-secret state as its next version (a
    per-connection sequence starting at 1). The caller commits — this only adds the row, so the
    snapshot and the create/update it records commit atomically.
    """
    # MAX over no rows is NULL → None; `or 0` makes the first version 1.
    current_max = session.scalar(
        select(func.max(ConnectionVersion.version_no)).where(
            ConnectionVersion.connection_id == conn.id
        )
    )
    next_no = (current_max or 0) + 1
    version = ConnectionVersion(
        connection_id=conn.id,
        version_no=next_no,
        name=conn.name,
        type=conn.type,
        env=conn.env,
        config=conn.config,
        changed_by=actor_id,
    )
    session.add(version)
    return version


# How far ahead the *backend* calls a credential "expiring soon" — used only for the operator-facing
# log line the sweep emits.
CREDENTIAL_EXPIRY_WARN_DAYS = 14


def _refresh_credential_expiry(conn: Connection, secret: str) -> None:
    """Recompute `credential_expires_at` from the credential just written."""
    conn.credential_expires_at = credential_expiry(conn.type, conn.config, secret)
    # Stamped whatever the outcome, INCLUDING when the expiry is None (#1024) — "we looked and this
    # credential has no readable lifetime" is a different fact from "we have never looked".
    conn.credential_expiry_checked_at = datetime.now(UTC)


def refresh_credential_expiry(session: Session, *, secret_store: SecretStore) -> int:
    """Re-read every stored credential's expiry; returns how many rows changed."""
    changed = 0
    conn_ids = list(session.scalars(select(Connection.id).where(Connection.secret_ref.isnot(None))))
    for conn_id in conn_ids:
        try:
            if _refresh_one_credential_expiry(session, conn_id, secret_store):
                changed += 1
        except Exception:
            # No exception text: a secret-store error can quote the secret name and,
            # in some SDKs, the value (#536). The connection id is enough to act on.
            session.rollback()
            log.warning("credential_expiry_refresh_skipped", connection_id=str(conn_id))
    log.info("credential_expiry_refreshed", changed=changed, scanned=len(conn_ids))
    return changed


def _refresh_one_credential_expiry(
    session: Session, conn_id: uuid.UUID, secret_store: SecretStore
) -> bool:
    """Re-read one connection's credential expiry and commit it. True if it moved."""
    conn = session.get(Connection, conn_id)
    if conn is None or conn.secret_ref is None:
        return False
    secret = secret_store.get(conn.secret_ref)
    previous = conn.credential_expires_at
    _refresh_credential_expiry(conn, secret)
    moved = conn.credential_expires_at != previous
    session.commit()
    _log_if_expiring(conn)
    return moved


def _log_if_expiring(conn: Connection) -> None:
    """Emit an operator-facing line for a credential at or near its end."""
    expires_at = conn.credential_expires_at
    if expires_at is None:
        return
    days_left = (expires_at - datetime.now(UTC)).total_seconds() / 86400
    if days_left <= CREDENTIAL_EXPIRY_WARN_DAYS:
        log.warning(
            "credential_expiring",
            connection_id=str(conn.id),
            type=conn.type,
            days_left=days_left,
        )


def create_connection(
    session: Session,
    *,
    name: str,
    conn_type: str,
    env: str,
    config: dict[str, Any],
    secret: str | None,
    created_by: uuid.UUID,
    secret_store: SecretStore,
    catalog_secret: str | None = None,
) -> Connection:
    """Validate, persist, and (if given) write the credential(s)."""
    _validated_config(conn_type, config)
    _validate_env(env)
    # #1118: there is no stored row yet, so ANY `*_secret_name` in the payload names someone else's
    # secret.
    _reject_foreign_secret_names(config, stored=None)
    if catalog_secret is not None:
        _validate_extra_secret_supported(conn_type, config, "catalog")

    conn = Connection(
        name=name,
        type=conn_type,
        env=env,
        config=config,
        secret_ref=None,
        created_by=created_by,
    )
    session.add(conn)
    try:
        session.flush()  # assign conn.id + surface the (name, env) unique violation
        if secret is not None:
            secret_ref = connection_secret_ref(
                connection_id=conn.id, env=conn.env, name=conn.name, conn_type=conn.type
            )
            secret_store.set(secret_ref, secret)
            conn.secret_ref = secret_ref
            # Read the credential's own expiry while it is in hand (#838) — the
            # sweep would otherwise leave a brand-new connection unknown for a day.
            _refresh_credential_expiry(conn, secret)
        if catalog_secret is not None:
            _write_extra_secret(conn, catalog_secret, secret_store, field="catalog")
        # v1 snapshot — atomic with the insert (same commit).
        record_connection_version(session, conn, actor_id=created_by)
        # And the audit event, also same-commit (ADR 0041 §2.1).
        audit_service.record_entity_change(
            session,
            action="connection.create",
            entity_type="connection",
            entity=conn,
            actor=created_by,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict_from_integrity_error(exc, conn_type=conn_type, env=env) from exc
    except SecretWriteError as exc:
        # Credential store (e.g. Key Vault) unreachable — an upstream-dependency failure, not a
        # client error. Roll the half-inserted row back and map to 502 (like
        # ConnectionTestFailedError), not a generic 500.
        session.rollback()
        log.warning("connection_secret_write_failed", type=conn_type, env=env)
        raise ConnectionSecretWriteError(
            "failed to store connection credential", detail={"type": conn_type, "env": env}
        ) from exc

    session.refresh(conn)
    log.info("connection_created", connection_id=str(conn.id), type=conn_type, env=env)
    return conn


@dataclass(frozen=True)
class DatasourceHealth:
    """Run-derived health for a DATASOURCE connection (#954)."""

    last_run_at: datetime | None = None
    consecutive_failures: int = 0
    reason: str | None = None


# How far back the consecutive-failure streak is counted.
_HEALTH_RUN_WINDOW = 20


def datasource_health(
    session: Session, connection_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, DatasourceHealth]:
    """Run-derived health per connection, in ONE query for the whole set."""
    if not connection_ids:
        return {}
    # LATERAL top-N per suite, not a window over everything (#999).
    recent = (
        select(
            Run.suite_id.label("suite_id"),
            Run.status.label("status"),
            Run.failure_reason.label("failure_reason"),
            Run.created_at.label("created_at"),
        )
        .where(Run.suite_id == Suite.id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(_HEALTH_RUN_WINDOW)
        .lateral("recent_runs")
    )
    rows = session.execute(
        select(
            Suite.connection_id.label("connection_id"),
            recent.c.suite_id,
            recent.c.status,
            recent.c.failure_reason,
            recent.c.created_at,
        )
        .select_from(Suite)
        .join(recent, true())
        .where(Suite.connection_id.in_(connection_ids))
        .order_by(Suite.connection_id, recent.c.suite_id, recent.c.created_at.desc())
    ).all()

    by_suite: dict[tuple[uuid.UUID, uuid.UUID], list[Any]] = defaultdict(list)
    for row in rows:
        by_suite[(row.connection_id, row.suite_id)].append(row)

    per_connection: dict[uuid.UUID, list[tuple[int, str | None, Any]]] = defaultdict(list)
    for (conn_id, _suite_id), runs in by_suite.items():
        streak, reason = _leading_failure_streak(runs)
        per_connection[conn_id].append((streak, reason, runs[0].created_at))

    health: dict[uuid.UUID, DatasourceHealth] = {}
    for conn_id, suites in per_connection.items():
        last_run_at = max(created for _s, _r, created in suites)
        # Degraded only when EVERY suite that has run is failing — a single succeeding suite proves
        # the connection itself is reachable.
        if any(streak == 0 for streak, _r, _c in suites):
            health[conn_id] = DatasourceHealth(last_run_at=last_run_at)
            continue
        # The strongest claim true of all of them, not the loudest one.
        streak = min(streak for streak, _r, _c in suites)
        reason = next(
            (r for _s, r, _c in sorted(suites, key=lambda t: t[2], reverse=True) if r), None
        )
        health[conn_id] = DatasourceHealth(
            last_run_at=last_run_at, consecutive_failures=streak, reason=reason
        )
    return health


def _leading_failure_streak(runs: Sequence[Any]) -> tuple[int, str | None]:
    """Leading failures in newest-first order, plus the newest failure's reason."""
    streak = 0
    reason: str | None = None
    for run in runs:
        if run.status == "succeeded":
            break
        if run.status != "failed":
            continue
        streak += 1
        reason = reason or run.failure_reason
    return streak, reason


def list_connections(
    session: Session,
    *,
    conn_type: str | None = None,
    env: str | None = None,
) -> list[Connection]:
    stmt = select(Connection).order_by(Connection.created_at.desc())
    if conn_type is not None:
        stmt = stmt.where(Connection.type == conn_type)
    if env is not None:
        stmt = stmt.where(Connection.env == env)
    return list(session.scalars(stmt))


def get_connection(session: Session, connection_id: uuid.UUID) -> Connection:
    conn = session.get(Connection, connection_id)
    if conn is None:
        raise ConnectionNotFoundError(
            "connection not found", detail={"connection_id": str(connection_id)}
        )
    return conn


def update_connection(
    session: Session,
    connection_id: uuid.UUID,
    *,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    secret: str | None = None,
    secret_store: SecretStore,
    actor_id: uuid.UUID | None = None,
    catalog_secret: str | None = None,
) -> Connection:
    """Partial update of name / config / secret(s). Type and env are immutable."""
    conn = get_connection(session, connection_id)
    # Capture before commit: a unique violation rolls back and expires the
    # instance, so read the (immutable) type/env now for the conflict message.
    conn_type, conn_env = conn.type, conn.env
    # Same reasoning for the audit payload, and it must be taken before any field below is mutated —
    # a snapshot read afterwards would record the new state as the old one.
    audit_before = audit_service.snapshot("connection", conn)

    if config is not None:
        _validated_config(conn.type, config)
        # #1118: a client may echo back the `*_secret_name` it read off this connection (the GET →
        # edit one field → PATCH the whole config flow), but may not introduce or repoint one.
        _reject_foreign_secret_names(config, stored=conn.config or {})
        stored_config = conn.config or {}
        # Compare against the MERGED config, not the raw payload: a PATCH that omits
        # `catalog_secret_name` has it carried over.
        merged_config = _carry_over_secret_name_keys(stored_config, config)
        _reject_uncredentialed_redirect(
            conn.type,
            stored=stored_config,
            incoming=merged_config,
            has_stored_secret=conn.secret_ref is not None,
            supplied_secret=secret,
            supplied_extra_secrets={"catalog": catalog_secret},
        )
        was_syncing = bool(stored_config.get("inventory_sync"))
        conn.config = merged_config
        if was_syncing and not bool((conn.config or {}).get("inventory_sync")):
            # Turning the ADR 0040 toggle OFF ends the sync, so the outcome state describes
            # something that no longer happens (#1104).
            conn.inventory_sync_last_attempted_at = None
            conn.inventory_sync_last_error = None
            conn.inventory_sync_failing_since = None
    if catalog_secret is not None:
        # After the `config is not None` branch above.
        _validate_extra_secret_supported(conn.type, conn.config, "catalog")
    if name is not None:
        conn.name = name
    # Snapshot only a *real* name/config change.
    versioned_change = session.is_modified(conn)
    # Catalog secret FIRST, primary secret LAST — deliberately, not incidentally.
    if catalog_secret is not None:
        try:
            _write_extra_secret(conn, catalog_secret, secret_store, field="catalog")
        except SecretWriteError as exc:
            session.rollback()
            log.warning("connection_catalog_secret_write_failed", connection_id=str(connection_id))
            raise ConnectionSecretWriteError(
                "failed to store connection credential",
                detail={"connection_id": str(connection_id)},
            ) from exc

    if secret is not None:
        # `or` — not a recompute.
        secret_ref = conn.secret_ref or connection_secret_ref(
            connection_id=conn.id, env=conn.env, name=conn.name, conn_type=conn.type
        )
        try:
            secret_store.set(secret_ref, secret)
        except SecretWriteError as exc:
            session.rollback()
            log.warning("connection_secret_write_failed", connection_id=str(connection_id))
            raise ConnectionSecretWriteError(
                "failed to store connection credential",
                detail={"connection_id": str(connection_id)},
            ) from exc
        conn.secret_ref = secret_ref
        # The rotated credential has its own lifetime — including "none", which
        # must clear the previous date rather than leave a stale warning (#838).
        _refresh_credential_expiry(conn, secret)

    try:
        # Snapshot the post-update state, atomic with the update (same commit).
        if versioned_change:
            record_connection_version(session, conn, actor_id=actor_id)
        # Audited on EVERY update, including a secret-only rotation that records no version.
        audit_service.record_entity_change(
            session,
            action="connection.update",
            entity_type="connection",
            entity=conn,
            actor=actor_id,
            before=audit_before,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict_from_integrity_error(exc, conn_type=conn_type, env=conn_env) from exc
    session.refresh(conn)
    if config is not None:
        _reresolve_suite_assets(session, conn)
    log.info("connection_updated", connection_id=str(conn.id))
    return conn


def _reresolve_suite_assets(session: Session, conn: Connection) -> None:
    """Re-point every targeted suite on `conn` at the asset its target now resolves to."""
    suites = list(
        session.scalars(
            select(Suite).where(Suite.connection_id == conn.id, Suite.target.isnot(None))
        )
    )
    if not suites:
        return
    for suite in suites:
        suite.asset_id = resolve_and_upsert_asset(session, conn, suite.target)
    session.commit()
    log.info(
        "connection_suite_assets_reresolved",
        connection_id=str(conn.id),
        count=len(suites),
    )


def reauth_connection(
    session: Session,
    connection_id: uuid.UUID,
    *,
    secret: str,
    secret_store: SecretStore,
    actor_id: uuid.UUID | None = None,
) -> None:
    """Rotate an existing connection's credential and verify it, in one step."""
    conn = get_connection(session, connection_id)
    before = audit_service.snapshot("connection", conn)
    secret_ref = conn.secret_ref or connection_secret_ref(
        connection_id=conn.id, env=conn.env, name=conn.name, conn_type=conn.type
    )
    try:
        secret_store.set(secret_ref, secret)
    except SecretWriteError as exc:
        session.rollback()
        log.warning("connection_reauth_secret_write_failed", connection_id=str(connection_id))
        raise ConnectionSecretWriteError(
            "failed to store connection credential",
            detail={"connection_id": str(connection_id)},
        ) from exc
    conn.secret_ref = secret_ref
    # The "fix an expired token" path is exactly where the new expiry matters most:
    # the badge that prompted the rotation must clear on the same request (#838).
    _refresh_credential_expiry(conn, secret)
    # The event ADR 0020 shipped as a known hole: a credential rotation left no trace of any kind.
    audit_service.record_entity_change(
        session,
        action="connection.reauth",
        entity_type="connection",
        entity=conn,
        actor=actor_id,
        before=before,
    )
    session.commit()

    # Verify the freshly-rotated credential through the same probe as /test;
    # raises ConnectionTestFailedError (502) if the new credential doesn't work.
    test_connection(session, connection_id, secret_store=secret_store)
    log.info("connection_reauthed", connection_id=str(connection_id))


def list_connection_versions(session: Session, connection_id: uuid.UUID) -> list[ConnectionVersion]:
    """A connection's version history, newest first. 404 if the connection is
    missing. Eager-loads each version's author (only query that needs it) so the
    API can name the editor without an N+1.
    """
    get_connection(session, connection_id)  # 404 guard
    return list(
        session.scalars(
            select(ConnectionVersion)
            .where(ConnectionVersion.connection_id == connection_id)
            .options(selectinload(ConnectionVersion.author))
            .order_by(ConnectionVersion.version_no.desc())
        )
    )


def _dependent_suites_detail(
    session: Session,
    connection_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    actor_is_admin: bool,
) -> dict[str, Any] | None:
    """The 409 detail for suites still bound to the connection, or None when clear. Suite NAMES are
    grant-scoped (ADR 0027/0037) — the sample lists only suites the actor can view; the rest
    surface as a `restricted` count, never names (#927 review: naming a stranger's suites in a
    409 would defeat the suite endpoint's 404-no-leak one request over).
    """
    total = session.scalar(
        select(func.count()).select_from(Suite).where(Suite.connection_id == connection_id)
    )
    if not total:
        return None
    viewable = accessible_suite_ids(actor_id, include_all=actor_is_admin)
    sample = list(
        session.execute(
            select(Suite.name, Suite.id)
            .where(Suite.connection_id == connection_id, Suite.id.in_(viewable))
            .order_by(Suite.created_at)
            .limit(10)
        )
    )
    viewable_total = session.scalar(
        select(func.count())
        .select_from(Suite)
        .where(Suite.connection_id == connection_id, Suite.id.in_(viewable))
    )
    return {
        "connection_id": str(connection_id),
        "total": total,
        "restricted": total - (viewable_total or 0),
        "truncated": (viewable_total or 0) > len(sample),
        "suites": [{"name": name, "id": str(sid)} for name, sid in sample],
    }


def _dependent_source_checks_detail(
    session: Session,
    connection_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    actor_is_admin: bool,
) -> dict[str, Any] | None:
    """The 409 detail for comparison checks sourcing this connection (ADR 0015),
    or None when clear. Check names ride their suite's grant — same gating as
    `_dependent_suites_detail`.
    """
    total = session.scalar(
        select(func.count()).select_from(Check).where(Check.source_connection_id == connection_id)
    )
    if not total:
        return None
    viewable = accessible_suite_ids(actor_id, include_all=actor_is_admin)
    sample = list(
        session.execute(
            select(Check.name, Check.suite_id)
            .where(Check.source_connection_id == connection_id, Check.suite_id.in_(viewable))
            # Shared key (#318 G5): a same-transaction batch of checks ties on `created_at`, so an
            # untie-broken LIMIT 10 could return a different sample each call.
            .order_by(*CHECK_ORDER)
            .limit(10)
        )
    )
    viewable_total = session.scalar(
        select(func.count())
        .select_from(Check)
        .where(Check.source_connection_id == connection_id, Check.suite_id.in_(viewable))
    )
    return {
        "connection_id": str(connection_id),
        "total": total,
        "restricted": total - (viewable_total or 0),
        "truncated": (viewable_total or 0) > len(sample),
        "checks": [{"name": name, "suite_id": str(sid)} for name, sid in sample],
    }


def delete_connection(
    session: Session,
    connection_id: uuid.UUID,
    *,
    secret_store: SecretStore,
    actor_id: uuid.UUID,
    actor_is_admin: bool = False,
) -> None:
    conn = get_connection(session, connection_id)
    # Delete guard #1 (#753): suites still run against this connection.
    suites_detail = _dependent_suites_detail(
        session, conn.id, actor_id=actor_id, actor_is_admin=actor_is_admin
    )
    if suites_detail:
        raise ConnectionInUseError(
            f"{suites_detail['total']} suite(s) run against this connection — "
            "delete or repoint them first",
            detail=suites_detail,
        )
    # Delete guard #2 (ADR 0015): comparison checks referencing this connection as their source hold
    # an ON DELETE RESTRICT FK.
    checks_detail = _dependent_source_checks_detail(
        session, conn.id, actor_id=actor_id, actor_is_admin=actor_is_admin
    )
    if checks_detail:
        raise ConnectionInUseError(
            f"this connection is the comparison source of {checks_detail['total']} "
            "check(s) — repoint or delete them first",
            detail=checks_detail,
        )
    secret_ref = conn.secret_ref
    # Every SECOND credential's ref too (#1181) — `*_secret_name` by convention, same as
    # `_extra_secrets`/`_write_extra_secret` — captured before the row goes away.
    extra_secret_refs = [
        v for k, v in (conn.config or {}).items() if k.endswith("_secret_name") and v
    ]
    # Snapshot BEFORE the delete.
    audit_before = audit_service.snapshot("connection", conn)
    session.delete(conn)
    try:
        audit_service.record_entity_change(
            session,
            action="connection.delete",
            entity_type="connection",
            entity=None,
            actor=actor_id,
            before=audit_before,
        )
        session.commit()
    except IntegrityError as exc:
        # TOCTOU backstop: a suite or comparison check created between the pre-checks and this
        # commit trips its FK.
        session.rollback()
        cause = str(exc.orig)
        if "fk_suites_connection_id_connections" in cause:
            raise ConnectionInUseError(
                "a suite was bound to this connection while the delete was in "
                "flight — delete or repoint it first",
                detail=_dependent_suites_detail(
                    session, connection_id, actor_id=actor_id, actor_is_admin=actor_is_admin
                )
                or {"connection_id": str(connection_id)},
            ) from exc
        if "fk_checks_source_connection_id_connections" not in cause:
            raise
        raise ConnectionInUseError(
            "this connection became the comparison source of a check while the "
            "delete was in flight — repoint or delete that check first",
            detail=_dependent_source_checks_detail(
                session, connection_id, actor_id=actor_id, actor_is_admin=actor_is_admin
            )
            or {"connection_id": str(connection_id)},
        ) from exc
    # Best-effort remove the orphaned credential(s) from the store (#372, #1181) — after the row is
    # gone, and fail-soft (delete never raises), so a store hiccup can't 500 a successful delete.
    if secret_ref:
        secret_store.delete(secret_ref)
    for ref in extra_secret_refs:
        secret_store.delete(ref)
    log.info("connection_deleted", connection_id=str(connection_id))


def test_connection(
    session: Session,
    connection_id: uuid.UUID,
    *,
    secret_store: SecretStore,
) -> None:
    """Resolve the connection's secret and probe live connectivity."""
    conn = get_connection(session, connection_id)
    adapter = get_connection_adapter(conn.type)
    secret_optional = getattr(adapter, "secret_optional", False)

    secret: str | None = None
    if conn.secret_ref:
        try:
            secret = secret_store.get(conn.secret_ref)
        except SecretNotFoundError as exc:
            raise ConnectionTestFailedError(
                "credential could not be resolved", detail={"connection_id": str(connection_id)}
            ) from exc
    elif not secret_optional:
        raise ConnectionTestFailedError(
            "connection has no stored credential to test with",
            detail={"connection_id": str(connection_id)},
        )

    try:
        adapter.test(dict(conn.config), secret, **_extra_secrets(conn.config, secret_store))
    except Exception as exc:
        log.warning(
            "connection_test_failed",
            connection_id=str(connection_id),
            error_type=type(exc).__name__,
        )
        # Don't echo the adapter exception to the client — it can carry DSN / credential fragments
        # (it's also kept out of the logs above).
        raise ConnectionTestFailedError(
            "connection test failed", detail={"connection_id": str(connection_id)}
        ) from exc

    log.info("connection_test_succeeded", connection_id=str(connection_id))


def test_draft_connection(
    conn_type: str,
    *,
    env: str | None,
    config: dict[str, Any],
    secret: str | None,
    secret_store: SecretStore,
    catalog_secret: str | None = None,
) -> None:
    """Probe connectivity for an UNSAVED draft (#351) — no `connections` row, no
    `SecretStore` write, ever.
    """
    _validated_config(conn_type, config)
    if env is not None:
        _validate_env(env)
    # #1118 — a draft owns no secret refs, so every `*_secret_name` is foreign.
    _reject_foreign_secret_names(config, stored=None)
    if catalog_secret is not None:
        # Same guard `create_connection`/`update_connection` run — a draft for a type whose config
        # model has no `catalog_secret_name` field must 422 the same way a real create would.
        _validate_extra_secret_supported(conn_type, config, "catalog")
    adapter = get_connection_adapter(conn_type)
    secret_optional = getattr(adapter, "secret_optional", False)

    if not secret and not secret_optional:
        raise ConnectionTestFailedError(
            "a credential is required to test this connection", detail={"type": conn_type}
        )
    # Normalize a blank string to None — the wire payload can hand in "" where `create_connection`'s
    # own contract only ever sees `str | None`; both mean "no credential".
    secret = secret or None

    # No `_extra_secrets(config, ...)` here — the guard above rejected every `*_secret_name`, so
    # there is nothing to resolve and nothing this path can be made to read out of the store
    # (#1118).
    extra_secrets: dict[str, str] = {}
    if catalog_secret:
        extra_secrets["catalog_secret"] = catalog_secret

    try:
        adapter.test(dict(config), secret, **extra_secrets)
    except Exception as exc:
        log.warning(
            "connection_draft_test_failed",
            type=conn_type,
            error_type=type(exc).__name__,
        )
        # Same rationale as `test_connection`: never echo the adapter exception to the client
        # (DSN/credential fragments), original kept as __cause__ for the server-side traceback only.
        raise ConnectionTestFailedError(
            "connection test failed", detail={"type": conn_type}
        ) from exc

    log.info("connection_draft_test_succeeded", type=conn_type)
