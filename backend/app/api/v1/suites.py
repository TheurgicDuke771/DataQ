"""Suite CRUD endpoints.

Thin HTTP layer over `suite_service`: validates request shapes, wires the
current user + db session, and maps models onto responses. All business logic
(connection validation, persistence) lives in the service. `connection_id` is
set at create and immutable thereafter (re-pointing would orphan child checks).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.services import profile_service as profile
from backend.app.services import suite_io_service as suite_io
from backend.app.services import suite_service as svc
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["suites"])


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    connection_id: uuid.UUID


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)


class SuiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    connection_id: uuid.UUID
    created_by: uuid.UUID


@router.post(
    "/suites",
    response_model=SuiteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a suite",
)
def create_suite(
    payload: SuiteCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    suite = svc.create_suite(
        db,
        name=payload.name,
        description=payload.description,
        connection_id=payload.connection_id,
        created_by=current_user.id,
    )
    return SuiteRead.model_validate(suite)


@router.get("/suites", response_model=list[SuiteRead], summary="List suites")
def list_suites(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    connection_id: uuid.UUID | None = None,
) -> list[SuiteRead]:
    # Scoped to suites the user owns or has a share on.
    suites = svc.list_suites(db, user_id=current_user.id, connection_id=connection_id)
    return [SuiteRead.model_validate(s) for s in suites]


@router.get("/suites/{suite_id}", response_model=SuiteRead, summary="Get a suite")
def get_suite(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    suite = require_permission(db, suite_id, current_user.id, minimum="view")
    return SuiteRead.model_validate(suite)


@router.patch("/suites/{suite_id}", response_model=SuiteRead, summary="Update a suite")
def update_suite(
    suite_id: uuid.UUID,
    payload: SuiteUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    suite = svc.update_suite(db, suite_id, name=payload.name, description=payload.description)
    return SuiteRead.model_validate(suite)


@router.delete(
    "/suites/{suite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a suite",
)
def delete_suite(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    require_permission(db, suite_id, current_user.id, minimum="admin")
    svc.delete_suite(db, suite_id)


# ───────────────────────── export / import (portable documents) ─────


class CheckDocument(BaseModel):
    """One check inside a portable suite document — authoring fields only."""

    name: str = Field(min_length=1, max_length=256)
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class SuiteDocument(BaseModel):
    """Portable suite — connection-agnostic, no DB identity. Both the export
    response and the import payload (a round-trippable document)."""

    version: int = suite_io.EXPORT_VERSION
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    checks: list[CheckDocument] = Field(default_factory=list)


class SuiteImportRequest(BaseModel):
    connection_id: uuid.UUID
    document: SuiteDocument


@router.get(
    "/suites/{suite_id}/export",
    response_model=SuiteDocument,
    summary="Export a suite as a portable document",
)
def export_suite(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteDocument:
    suite = require_permission(db, suite_id, current_user.id, minimum="view")
    return SuiteDocument.model_validate(suite_io.export_suite(suite))


@router.post(
    "/suites/import",
    response_model=SuiteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Import a suite document onto a connection",
)
def import_suite(
    payload: SuiteImportRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    # Like create_suite: any authenticated user may import; the new suite is
    # owned by them. Thresholds/config round-trip exactly (Decimal in/out).
    doc = payload.document
    suite = suite_io.import_suite(
        db,
        version=doc.version,
        name=doc.name,
        description=doc.description,
        checks=[c.model_dump() for c in doc.checks],
        connection_id=payload.connection_id,
        created_by=current_user.id,
    )
    return SuiteRead.model_validate(suite)


# ───────────────────────── column profiler (no persistence) ─────────


class ColumnProfileRequest(BaseModel):
    table: str = Field(min_length=1, max_length=255, description="Table to profile")
    schema_: str | None = Field(default=None, alias="schema")
    columns: list[str] = Field(min_length=1, max_length=50)
    top_n: int = Field(default=10, ge=1, le=100, description="Most-frequent values per column")


class TopValue(BaseModel):
    value: Any | None
    count: int


class ColumnProfileRead(BaseModel):
    column: str
    null_count: int
    null_fraction: float
    distinct_count: int
    min_value: Any | None
    max_value: Any | None
    top_values: list[TopValue]


class TableProfileRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table: str
    schema_: str = Field(serialization_alias="schema")
    row_count: int
    columns: list[ColumnProfileRead]


@router.post(
    "/suites/{suite_id}/profile",
    response_model=TableProfileRead,
    summary="Profile columns of a table on the suite's connection (no persistence)",
)
def profile_columns(
    suite_id: uuid.UUID,
    payload: ColumnProfileRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> TableProfileRead:
    # sync def → threadpool; the warehouse connect + scans are blocking.
    # Authoring aid → 'edit', same as the dry-run. Connection FK is RESTRICT.
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None
    result = profile.profile_table(
        connection,
        table=payload.table,
        schema=payload.schema_,
        columns=payload.columns,
        top_n=payload.top_n,
        secret_store=secret_store,
    )
    return TableProfileRead(
        table=result.table,
        schema_=result.schema,
        row_count=result.row_count,
        columns=[
            ColumnProfileRead(
                column=c.column,
                null_count=c.null_count,
                null_fraction=c.null_fraction,
                distinct_count=c.distinct_count,
                min_value=c.min_value,
                max_value=c.max_value,
                top_values=[TopValue(value=t["value"], count=t["count"]) for t in c.top_values],
            )
            for c in result.columns
        ],
    )
