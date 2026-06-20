"""Connection CRUD + connectivity-test endpoints.

Thin HTTP layer over `connection_service`: validates request shapes, wires the
current user + db session + secret store, and maps models onto responses. All
business logic (validation dispatch, secret write-through, connectivity probe)
lives in the service. Responses never carry secret material — only `has_secret`.

The `/test` route is a sync ``def`` so FastAPI runs it in a worker thread; the
Snowflake connect is blocking and must not stall the event loop.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.services import connection_service as svc

router = APIRouter(tags=["connections"])


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str
    env: str
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, description="Credential; write-only, never returned")


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: dict[str, Any] | None = None
    secret: str | None = Field(default=None, description="Rotate the credential; write-only")


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    env: str
    config: dict[str, Any]
    has_secret: bool
    created_by: uuid.UUID

    @classmethod
    def from_model(cls, conn: Connection) -> ConnectionRead:
        return cls(
            id=conn.id,
            name=conn.name,
            type=conn.type,
            env=conn.env,
            config=dict(conn.config),
            has_secret=conn.secret_ref is not None,
            created_by=conn.created_by,
        )


class ConnectionReauth(BaseModel):
    secret: str = Field(min_length=1, description="New credential; write-only, never returned")


class ConnectionTestResult(BaseModel):
    ok: bool


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
    )
    return ConnectionRead.from_model(conn)


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
    return [ConnectionRead.from_model(c) for c in conns]


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
    return ConnectionRead.from_model(svc.get_connection(db, connection_id))


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
) -> None:
    svc.delete_connection(db, connection_id)


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


class ConnectionVersionRead(BaseModel):
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
