"""Connection CRUD + connectivity test, datasource-type-agnostic.

Drives the `connections` table and dispatches type-specific behaviour through
the `ConnectionAdapter` registry — so this layer never branches on
``connection.type``. Credentials are written through the `SecretStore`
(`set`) and only ever referenced by `Connection.secret_ref`; the plaintext
secret is never stored on the row or logged.

FastAPI-free by design (like `run_service`): takes a `Session` + `SecretStore`,
returns ORM models, raises `DataQError` subclasses. The API layer owns
request/response shapes and dependency wiring.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore, SecretWriteError
from backend.app.datasources.registry import (
    UnsupportedConnectionTypeError,
    get_connection_adapter,
)
from backend.app.db.models import ENVS, Check, Connection, ConnectionVersion, Suite
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
    # A comparison check references this connection as its source (ADR 0015):
    # the FK is ON DELETE RESTRICT, so surface a friendly 409 naming the
    # dependents instead of letting the raw FK violation 500.
    status_code = 409
    code = "connection_in_use"


def _extra_secrets(config: Mapping[str, Any], secret_store: SecretStore) -> dict[str, str]:
    """Resolve every *additional* credential a connection's config names, by convention.

    Some types need more than one credential (an Iceberg SQL catalog: the storage key
    AND the catalog DB password). Rather than smuggle the second into non-secret
    `config` — the #754/#826 bug — config holds only the SecretStore **key name**, in a
    field suffixed ``_secret_name``, and the caller (here) resolves it. `foo_secret_name`
    → the adapter receives ``foo_secret=<value>``.

    Generic on purpose: no branching on `connection.type`, so the seam keeps its "the
    caller resolves secrets, adapters never touch the store" invariant (ADR 0011) no
    matter how many credentials a future type needs. A named-but-missing secret is left
    out rather than raising, so `test()` surfaces it as a connectivity failure with the
    adapter's own message instead of a 500.
    """
    out: dict[str, str] = {}
    for key, value in config.items():
        if not key.endswith("_secret_name") or not isinstance(value, str) or not value:
            continue
        try:
            out[key.removesuffix("_name")] = secret_store.get(value)
        except SecretNotFoundError:
            log.warning("connection_extra_secret_missing", secret_field=key)
    return out


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


# DB index that enforces one orchestration-provider connection per (type, env)
# — see the connections migration (#72 / ADR 0004). Distinguished from the
# (name, env) unique constraint so each violation gets an accurate 409 message.
_ORCHESTRATOR_UNIQUE_INDEX = "uq_connections_orchestrator_type_env"


def _conflict_from_integrity_error(
    exc: IntegrityError, *, conn_type: str, env: str
) -> ConnectionConflictError:
    """Map a unique-violation to the right 409, by which constraint fired.

    Postgres surfaces the violated constraint/index name on the driver
    exception's ``diag``; use it to tell the orchestrator (type, env) singleton
    breach apart from a duplicate (name, env).
    """
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
    """Append an immutable snapshot of `conn`'s current non-secret state as its
    next version (a per-connection sequence starting at 1). The caller commits —
    this only adds the row, so the snapshot and the create/update it records
    commit atomically. The `(connection_id, version_no)` unique constraint is the
    backstop against a concurrent double-write computing the same number (rare
    under v1's single-tenant editing).

    The credential is **not** snapshotted (see `ConnectionVersion`); only the
    editable, non-secret fields. `conn.id` must be populated (flush first).
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
) -> Connection:
    """Validate, persist, and (if a secret is given) write its credential.

    The secret_ref is derived from the row's own id (``conn-<uuid>``) — unique
    and safe as a Key Vault secret name. The credential is written through the
    store; only the ref is persisted on the row.
    """
    _validated_config(conn_type, config)
    _validate_env(env)

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
            secret_ref = f"conn-{conn.id}"
            secret_store.set(secret_ref, secret)
            conn.secret_ref = secret_ref
        # v1 snapshot — atomic with the insert (same commit).
        record_connection_version(session, conn, actor_id=created_by)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _conflict_from_integrity_error(exc, conn_type=conn_type, env=env) from exc
    except SecretWriteError as exc:
        # Credential store (e.g. Key Vault) unreachable — an upstream-dependency
        # failure, not a client error. Roll the half-inserted row back and map to
        # 502 (like ConnectionTestFailedError), not a generic 500.
        session.rollback()
        log.warning("connection_secret_write_failed", type=conn_type, env=env)
        raise ConnectionSecretWriteError(
            "failed to store connection credential", detail={"type": conn_type, "env": env}
        ) from exc

    session.refresh(conn)
    log.info("connection_created", connection_id=str(conn.id), type=conn_type, env=env)
    return conn


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
) -> Connection:
    """Partial update of name / config / secret. Type and env are immutable.

    Records a new `ConnectionVersion` only when a snapshotted field (name/config)
    changed — a secret-only update (credential rotation) is not config history and
    records no version (mirrors `reauth_connection`).
    """
    conn = get_connection(session, connection_id)
    # Capture before commit: a unique violation rolls back and expires the
    # instance, so read the (immutable) type/env now for the conflict message.
    conn_type, conn_env = conn.type, conn.env

    if config is not None:
        _validated_config(conn.type, config)
        conn.config = config
    if name is not None:
        conn.name = name
    # Snapshot only a *real* name/config change. `is_modified` reports net changes,
    # so a no-op PATCH (fields re-sent at their current values) doesn't mint a
    # duplicate version (mirrors `check_service.update_check`). Captured **before**
    # the secret write so a credential rotation — which dirties `secret_ref` — is
    # not counted as config history (a secret-only update records no version).
    versioned_change = session.is_modified(conn)
    if secret is not None:
        secret_ref = conn.secret_ref or f"conn-{conn.id}"
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

    try:
        # Snapshot the post-update state, atomic with the update (same commit).
        # Inside the try: recording reads `MAX(version_no)`, which autoflushes the
        # pending name/config change — so a (name, env) collision can surface here
        # rather than at commit, and must map to the same conflict error.
        if versioned_change:
            record_connection_version(session, conn, actor_id=actor_id)
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
    """Re-point every targeted suite on `conn` at the asset its target now resolves to.

    A config change (account / database / workspace_url / container / bucket — every
    field the OpenLineage identity keys on) moves the asset identity, so a suite bound
    to `conn` would otherwise keep a **stale, confidently-wrong** `asset_id` that every
    later run stamps (worse than NULL for lineage/incidents — ADR 0034). Fail-soft:
    `resolve_and_upsert_asset` never raises; an unresolvable target leaves `asset_id`
    NULL and the update still succeeds.
    """
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
) -> None:
    """Rotate an existing connection's credential and verify it, in one step.

    The "fix an expired token" path. Unlike `update_connection` (which stores a
    secret but never checks it) and `test_connection` (which checks but can't
    rotate), re-auth writes the new credential **and** probes connectivity with
    it through the same adapter path as ``/test``.

    The credential is rotated *before* the probe, so a failed probe
    (`ConnectionTestFailedError`, 502) means the freshly supplied credential is
    itself bad — the old, expired one is already replaced. A store-write failure
    (`ConnectionSecretWriteError`, 502) happens before any row change, so the
    existing credential is left untouched.
    """
    conn = get_connection(session, connection_id)
    secret_ref = conn.secret_ref or f"conn-{conn.id}"
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
    """The 409 detail for suites still bound to the connection, or None when
    clear. Suite NAMES are grant-scoped (ADR 0027/0037) — the sample lists only
    suites the actor can view; the rest surface as a `restricted` count, never
    names (#927 review: naming a stranger's suites in a 409 would defeat the
    suite endpoint's 404-no-leak one request over)."""
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
    `_dependent_suites_detail`."""
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
            .order_by(Check.created_at)
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
    # Delete guard #1 (#753): suites still run against this connection. No
    # cascade is offered — deleting a connection must never silently take a
    # suite (and its checks/runs/results, #540) with it; the user deletes or
    # repoints the suites first, and the 409 counts them (naming only the ones
    # the actor's grants cover).
    suites_detail = _dependent_suites_detail(
        session, conn.id, actor_id=actor_id, actor_is_admin=actor_is_admin
    )
    if suites_detail:
        raise ConnectionInUseError(
            f"{suites_detail['total']} suite(s) run against this connection — "
            "delete or repoint them first",
            detail=suites_detail,
        )
    # Delete guard #2 (ADR 0015): comparison checks referencing this connection
    # as their source hold an ON DELETE RESTRICT FK. Bounded sample + true
    # total, `truncated`-flagged so a scripted remediation can't mistake the
    # sample for the full set.
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
    session.delete(conn)
    try:
        session.commit()
    except IntegrityError as exc:
        # TOCTOU backstop: a suite or comparison check created between the
        # pre-checks and this commit trips its FK — re-derive the SAME detail
        # shape the pre-checks raise (the dependents exist now, that's why the
        # FK fired) and 409, never a raw 500 (#753/#927 review). Any other
        # integrity failure is not this race; re-raise. (pipeline_runs no longer
        # reaches here — its FK cascades, migration a3b4c5d6e7f8.)
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
    # Best-effort remove the orphaned credential from the store (#372) — after the
    # row is gone, and fail-soft (delete never raises), so a store hiccup can't 500
    # a successful delete.
    if secret_ref:
        secret_store.delete(secret_ref)
    log.info("connection_deleted", connection_id=str(connection_id))


def test_connection(
    session: Session,
    connection_id: uuid.UUID,
    *,
    secret_store: SecretStore,
) -> None:
    """Resolve the connection's secret and probe live connectivity.

    Raises `ConnectionTestFailedError` (502) on missing credentials or any
    adapter-reported connectivity failure.
    """
    conn = get_connection(session, connection_id)
    adapter = get_connection_adapter(conn.type)

    if not conn.secret_ref:
        raise ConnectionTestFailedError(
            "connection has no stored credential to test with",
            detail={"connection_id": str(connection_id)},
        )
    try:
        secret = secret_store.get(conn.secret_ref)
    except SecretNotFoundError as exc:
        raise ConnectionTestFailedError(
            "credential could not be resolved", detail={"connection_id": str(connection_id)}
        ) from exc

    try:
        adapter.test(dict(conn.config), secret, **_extra_secrets(conn.config, secret_store))
    except Exception as exc:
        log.warning(
            "connection_test_failed",
            connection_id=str(connection_id),
            error_type=type(exc).__name__,
        )
        # Don't echo the adapter exception to the client — it can carry DSN /
        # credential fragments (it's also kept out of the logs above). The
        # original is preserved as __cause__ for server-side traceback only.
        raise ConnectionTestFailedError(
            "connection test failed", detail={"connection_id": str(connection_id)}
        ) from exc

    log.info("connection_test_succeeded", connection_id=str(connection_id))
