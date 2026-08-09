"""Connection CRUD + connectivity-test endpoints.

Thin HTTP layer over `connection_service`: validates request shapes, wires the
current user + db session + secret store, and maps models onto responses. All
business logic (validation dispatch, secret write-through, connectivity probe)
lives in the service. Responses never carry secret material — only `has_secret`.

Both `/test` routes (the saved-connection `/connections/{id}/test` and the
draft `/connections/test`, #351) are sync ``def`` so FastAPI runs them in a
worker thread; the datasource connect is blocking and must not stall the
event loop.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user, is_workspace_admin
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.core.uri_credentials import redact_config_uris
from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.services import connection_service as svc

router = APIRouter(tags=["connections"])


class ConnectionCreate(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    type: str
    env: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, description="Credential; write-only, never returned")
    catalog_secret: str | None = Field(
        default=None,
        description=(
            "Second credential a connection type may need (currently the Iceberg "
            "SQL-catalog DB password, #1181); write-only, never returned"
        ),
    )


class ConnectionUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: dict[str, Any] | None = None
    secret: str | None = Field(default=None, description="Rotate the credential; write-only")
    catalog_secret: str | None = Field(
        default=None, description="Rotate the second (catalog) credential; write-only"
    )


class ConnectionRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    env: str
    config: dict[str, Any]
    has_secret: bool
    created_by: uuid.UUID

    # Poll health (#828) — orchestration connections only; NULL/0 elsewhere. A failing
    # poll used to live purely in the logs, so a dead integration looked identical to a
    # healthy-but-quiet one. `last_poll_error` is a CLASSIFIED reason, never raw
    # exception text (which can carry a SAS/DSN/token).
    last_polled_at: datetime | None = None
    last_poll_error: str | None = None
    consecutive_poll_failures: int = 0

    # Run-derived health (#954) — DATASOURCE connections. Nothing polls a
    # datasource, so a dead credential used to be invisible until a run failed,
    # and then it showed on the RUN, not here: two prod Snowflake connections sat
    # dead for weeks and diagnosing them meant reading worker logs. Derived from
    # `runs`, never stored, so it cannot drift from the runs it describes.
    # `last_run_error` is `runs.failure_reason`, already classified at the point of
    # failure (#605) — never raw driver text.
    last_run_at: datetime | None = None
    last_run_error: str | None = None
    consecutive_run_failures: int = 0

    # Credential expiry (#838) — when the credential itself states one (a SAS prints
    # `se=`). NULL means **unknown** (this credential type has no readable lifetime,
    # or it has not been read yet), never "does not expire", so a client must render
    # NULL as silence rather than reassurance. A date, never credential material.
    credential_expires_at: datetime | None = None
    # When the expiry was last read (#1024). NULL here means we have never looked,
    # which the client must render as silence rather than as "nothing expires
    # soon" — the two were indistinguishable before this field existed.
    credential_expiry_checked_at: datetime | None = None

    # Inventory-sync outcome (#1104) — opted-in `snowflake`/`unity_catalog`
    # connections only (config.inventory_sync, ADR 0040); NULL/never-attempted on
    # every other connection. A sync whose principal can't read the enumeration
    # query used to fail every tick invisibly: toggle on, connection test green
    # (the `SELECT 1` probe never exercises this query), zero assets ever appear,
    # no surface said why. `inventory_sync_last_error` is a CLASSIFIED reason
    # (never raw exception text); NULL means the last attempt succeeded.
    # `inventory_sync_failing_since` is NULL whenever the connection is currently
    # healthy (last attempt succeeded, or it has never been attempted).
    inventory_sync_last_attempted_at: datetime | None = None
    inventory_sync_last_error: str | None = None
    inventory_sync_failing_since: datetime | None = None

    @classmethod
    def from_model(
        cls, conn: Connection, health: svc.DatasourceHealth | None = None
    ) -> ConnectionRead:
        health = health or svc.DatasourceHealth()
        return cls(
            id=conn.id,
            name=conn.name,
            type=conn.type,
            env=conn.env,
            # `config` is non-secret by contract, but a URI-shaped field can smuggle a
            # credential through it (#754) — scrub any URI password on the way out, so
            # a row written before the config-level guard existed can't still leak.
            config=redact_config_uris(conn.config),
            has_secret=conn.secret_ref is not None,
            created_by=conn.created_by,
            last_polled_at=conn.last_polled_at,
            last_poll_error=conn.last_poll_error,
            consecutive_poll_failures=conn.consecutive_poll_failures or 0,
            last_run_at=health.last_run_at,
            last_run_error=health.reason,
            consecutive_run_failures=health.consecutive_failures,
            credential_expires_at=conn.credential_expires_at,
            credential_expiry_checked_at=conn.credential_expiry_checked_at,
            inventory_sync_last_attempted_at=conn.inventory_sync_last_attempted_at,
            inventory_sync_last_error=conn.inventory_sync_last_error,
            inventory_sync_failing_since=conn.inventory_sync_failing_since,
        )


class ConnectionReauth(ApiModel):
    secret: str = Field(min_length=1, description="New credential; write-only, never returned")


class ConnectionTestResult(ApiModel):
    ok: bool


class ConnectionDraftTest(ApiModel):
    """The payload for `/connections/test` — everything `ConnectionCreate` needs
    to probe connectivity, minus `name` (a draft has no row and needs none).
    `env` is optional: it plays no role in the probe itself (only in the
    orchestrator-singleton uniqueness check a real create enforces), so a caller
    that hasn't picked one yet still gets a full connectivity check.
    """

    type: str
    env: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, description="Credential to test; write-only")
    catalog_secret: str | None = Field(
        default=None, description="Second (catalog) credential to test; write-only"
    )


@router.post(
    "/connections",
    response_model=ConnectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a connection",
)
def create_connection(
    payload: ConnectionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ConnectionRead:
    conn = svc.create_connection(
        db,
        name=payload.name,
        conn_type=payload.type,
        env=payload.env,
        config=payload.config,
        secret=payload.secret,
        created_by=current_user.id,
        secret_store=secret_store,
        catalog_secret=payload.catalog_secret,
    )
    return ConnectionRead.from_model(conn)


@router.post(
    "/connections/test",
    response_model=ConnectionTestResult,
    summary="Test live connectivity for an unsaved draft connection",
)
def test_draft_connection(
    payload: ConnectionDraftTest,
    current_user: Annotated[User, Depends(get_current_user)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ConnectionTestResult:
    """Probe the config/secret the user just typed — before Create is pressed.

    Registered ahead of `/connections/{connection_id}/test` in file order, but
    the two never actually collide: `/connections/test` is two path segments
    (`connections`, `test`) and `/connections/{connection_id}/test` is three
    (`connections`, `{connection_id}`, `test`), so Starlette can't route a
    `/connections/test` request to the parameterized handler regardless of
    registration order — `test_both_test_routes_resolve` pins this down.
    Nothing is persisted: no `connections` row, no `SecretStore` write. Same
    auth gate as `create_connection` — a credential-carrying probe must not be
    more permissive than the endpoint that actually stores one. Sync `def` like
    the saved-connection `/test`: the datasource connect is blocking.
    """
    svc.test_draft_connection(
        payload.type,
        env=payload.env,
        config=payload.config,
        secret=payload.secret,
        secret_store=secret_store,
        catalog_secret=payload.catalog_secret,
    )
    return ConnectionTestResult(ok=True)


@router.get(
    "/connections",
    response_model=list[ConnectionRead],
    summary="List connections",
)
def list_connections(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    type: str | None = None,
    env: str | None = None,
) -> list[ConnectionRead]:
    conns = svc.list_connections(db, conn_type=type, env=env)
    # One batched query for the whole list, not one per connection — the N+1 that
    # #947 just removed from the MCP surface.
    health = svc.datasource_health(db, [c.id for c in conns])
    return [ConnectionRead.from_model(c, health.get(c.id)) for c in conns]


@router.get(
    "/connections/{connection_id}",
    response_model=ConnectionRead,
    summary="Get a connection",
)
def get_connection(
    connection_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ConnectionRead:
    conn = svc.get_connection(db, connection_id)
    return ConnectionRead.from_model(conn, svc.datasource_health(db, [conn.id]).get(conn.id))


@router.patch(
    "/connections/{connection_id}",
    response_model=ConnectionRead,
    summary="Update a connection",
)
def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ConnectionRead:
    conn = svc.update_connection(
        db,
        connection_id,
        name=payload.name,
        config=payload.config,
        secret=payload.secret,
        secret_store=secret_store,
        actor_id=current_user.id,
        catalog_secret=payload.catalog_secret,
    )
    return ConnectionRead.from_model(conn)


@router.delete(
    "/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a connection",
)
def delete_connection(
    connection_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> None:
    # Actor identity gates which dependent-suite NAMES a 409 may echo back
    # (ADR 0027 grants; #927 review) — admins see all names.
    svc.delete_connection(
        db,
        connection_id,
        secret_store=secret_store,
        actor_id=current_user.id,
        actor_is_admin=is_workspace_admin(current_user),
    )


@router.post(
    "/connections/{connection_id}/test",
    response_model=ConnectionTestResult,
    summary="Test live connectivity for a connection",
)
def test_connection(
    connection_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ConnectionTestResult:
    # sync def → runs in a threadpool; the datasource connect is blocking.
    svc.test_connection(db, connection_id, secret_store=secret_store)
    return ConnectionTestResult(ok=True)


@router.post(
    "/connections/{connection_id}/reauth",
    response_model=ConnectionTestResult,
    summary="Rotate a connection's credential and verify it",
)
def reauth_connection(
    connection_id: uuid.UUID,
    payload: ConnectionReauth,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ConnectionTestResult:
    # sync def → threadpool; the verify probe is blocking, like /test. Rotates
    # the credential then probes it; a bad new credential surfaces as 502.
    svc.reauth_connection(db, connection_id, secret=payload.secret, secret_store=secret_store)
    return ConnectionTestResult(ok=True)


# ───────────────────────── version history ─────────────────────────


class ConnectionVersionRead(ApiModel):
    """One snapshot in a connection's history. `changed_by_name` (the author's
    display name or email, NULL for a system actor / removed user) comes from the
    model property, resolved server-side so the client needn't join users. No
    credential is present — only the editable, non-secret fields are versioned.
    """

    model_config = ConfigDict(from_attributes=True)

    version_no: int
    name: str
    type: str
    env: str
    config: dict[str, Any]
    changed_by: uuid.UUID | None
    changed_by_name: str | None
    created_at: datetime

    @field_validator("config")
    @classmethod
    def _scrub_uri_credentials(cls, v: dict[str, Any]) -> dict[str, Any]:
        # A snapshot taken before the config-level guard existed can still carry a
        # credential in a URI-shaped field (#754) — never hand it back out.
        return redact_config_uris(v)


@router.get(
    "/connections/{connection_id}/versions",
    response_model=list[ConnectionVersionRead],
    summary="List a connection's version history (newest first)",
)
def list_connection_versions(
    connection_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ConnectionVersionRead]:
    return [
        ConnectionVersionRead.model_validate(v)
        for v in svc.list_connection_versions(db, connection_id)
    ]
