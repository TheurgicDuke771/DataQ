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
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.datasources.registry import (
    UnsupportedConnectionTypeError,
    get_connection_adapter,
)
from backend.app.db.models import ENVS, Connection

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
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConnectionConflictError(
            "a connection with this name already exists in this env",
            detail={"name": name, "env": env},
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
) -> Connection:
    """Partial update of name / config / secret. Type and env are immutable."""
    conn = get_connection(session, connection_id)

    if config is not None:
        _validated_config(conn.type, config)
        conn.config = config
    if name is not None:
        conn.name = name
    if secret is not None:
        secret_ref = conn.secret_ref or f"conn-{conn.id}"
        secret_store.set(secret_ref, secret)
        conn.secret_ref = secret_ref

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConnectionConflictError(
            "a connection with this name already exists in this env",
            detail={"connection_id": str(connection_id)},
        ) from exc
    session.refresh(conn)
    log.info("connection_updated", connection_id=str(conn.id))
    return conn


def delete_connection(session: Session, connection_id: uuid.UUID) -> None:
    conn = get_connection(session, connection_id)
    session.delete(conn)
    session.commit()
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
        adapter.test(dict(conn.config), secret)
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
