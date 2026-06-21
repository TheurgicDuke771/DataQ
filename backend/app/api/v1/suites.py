"""Suite CRUD endpoints.

Thin HTTP layer over `suite_service`: validates request shapes, wires the
current user + db session, and maps models onto responses. All business logic
(connection validation, persistence) lives in the service. `connection_id` is
set at create and immutable thereafter (re-pointing would orphan child checks).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1.runs import RunRead
from backend.app.core.auth import get_current_user
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import Connection, Run, Suite, User
from backend.app.db.session import get_db
from backend.app.services import profile_service as profile
from backend.app.services import run_dispatch, run_target
from backend.app.services import suite_io_service as suite_io
from backend.app.services import suite_service as svc
from backend.app.services.suite_authz import (
    OWNER,
    effective_permission,
    effective_permissions,
    require_permission,
)

router = APIRouter(tags=["suites"])


class SuiteTarget(BaseModel):
    """Datasource-shaped run target (#215) — which table / flat-file path / Unity
    Catalog name the suite's checks run against. Same shape as the column-profiler
    request; `run_target.resolve_target` validates the right fields per connection
    type (`table` for SQL, `path` for flat files, `catalog` for Unity Catalog).

    A flat-file target can instead select a **batch** of files: `pattern` (a regex
    whose first capture group is the batch key) + `strategy` (`latest`/`specific`,
    with `batch` for `specific`) + optional `prefix` (A4). The exact field
    combination is validated by `run_target.resolve_target` per connection type, so
    those rules live in one place — this model only declares the storable keys."""

    model_config = ConfigDict(populate_by_name=True)

    table: str | None = Field(default=None, max_length=255)
    schema_: str | None = Field(default=None, alias="schema", max_length=255)
    catalog: str | None = Field(default=None, max_length=255)
    path: str | None = Field(default=None, max_length=1024)
    file_format: Literal["csv", "parquet"] | None = None
    # Flat-file batch selection (A4); validated in run_target, not here.
    pattern: str | None = Field(default=None, max_length=1024)
    strategy: Literal["latest", "specific"] | None = None
    batch: str | None = Field(default=None, max_length=255)
    prefix: str | None = Field(default=None, max_length=1024)

    def to_storage(self) -> dict[str, Any]:
        """JSONB dict with the canonical `schema` key (not the `schema_` alias)."""
        return self.model_dump(by_alias=True, exclude_none=True)


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    connection_id: uuid.UUID
    target: SuiteTarget | None = None


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    target: SuiteTarget | None = None


class SuiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    connection_id: uuid.UUID
    target: dict[str, Any] | None
    created_by: uuid.UUID
    # The caller's effective level on this suite (`owner`/`admin`/`edit`/`view`)
    # so the UI can gate per-suite actions — manage shares, delete — without a
    # second round-trip or guessing from `created_by`. Always set on an
    # accessible read (the read is already permission-gated).
    my_permission: str | None = None

    @classmethod
    def of(cls, suite: Suite, my_permission: str | None) -> SuiteRead:
        read = cls.model_validate(suite)
        read.my_permission = my_permission
        return read


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
        target=payload.target.to_storage() if payload.target is not None else None,
    )
    # The creator is, by definition, the owner.
    return SuiteRead.of(suite, OWNER)


@router.get("/suites", response_model=list[SuiteRead], summary="List suites")
def list_suites(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    connection_id: uuid.UUID | None = None,
) -> list[SuiteRead]:
    # Scoped to suites the user owns or has a share on.
    suites = svc.list_suites(db, user_id=current_user.id, connection_id=connection_id)
    levels = effective_permissions(db, suites, current_user.id)
    return [SuiteRead.of(s, levels[s.id]) for s in suites]


@router.get("/suites/{suite_id}", response_model=SuiteRead, summary="Get a suite")
def get_suite(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    suite = require_permission(db, suite_id, current_user.id, minimum="view")
    return SuiteRead.of(suite, effective_permission(db, suite, current_user.id))


@router.patch("/suites/{suite_id}", response_model=SuiteRead, summary="Update a suite")
def update_suite(
    suite_id: uuid.UUID,
    payload: SuiteUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    suite = svc.update_suite(
        db,
        suite_id,
        name=payload.name,
        description=payload.description,
        target=payload.target.to_storage() if payload.target is not None else None,
    )
    return SuiteRead.of(suite, effective_permission(db, suite, current_user.id))


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


# ───────────────────────── manual run trigger ──────────────────────


@router.post(
    "/suites/{suite_id}/run",
    response_model=RunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a run of the suite",
)
def trigger_suite_run(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RunRead:
    """Queue a run of the suite and dispatch it to the worker.

    `edit` gates this (the capability ladder grants 'trigger runs' at edit). The
    suite's target (#215) is resolved up front so a targetless/misconfigured
    suite fails fast with a 422 instead of a queued→failed run the caller has to
    poll to discover. A broker outage marks the run `failed` (never left stuck
    `queued`) and surfaces 503 — the same contract as the probe endpoint.
    """
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None  # FK is RESTRICT; a suite always has its connection
    # Raises SuiteTargetInvalidError (422) for a targetless / wrong-datasource target.
    run_target.resolve_target(connection.type, suite.target)

    run = Run(suite_id=suite.id, status="queued", triggered_by=f"manual:{current_user.id}")
    db.add(run)
    db.commit()
    db.refresh(run)

    # Shared create-adjacent dispatch+broker-failure block (#227): on a broker
    # outage the run is marked terminal-`failed` (never left stuck `queued`) and we
    # surface 503 — the same contract as the probe endpoint.
    if not run_dispatch.dispatch_or_fail(db, run):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="failed to dispatch run",
        )
    return RunRead.model_validate(run)


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
    columns: list[str] = Field(min_length=1, max_length=50)
    top_n: int = Field(default=10, ge=1, le=100, description="Most-frequent values per column")
    # SQL datasources: the target is a table (+ schema; Unity Catalog also catalog).
    table: str | None = Field(default=None, max_length=255, description="SQL table to profile")
    schema_: str | None = Field(default=None, alias="schema")
    catalog: str | None = Field(default=None, max_length=255, description="Unity Catalog catalog")
    # Flat-file datasources (ADLS Gen2 / S3): the target is a file path.
    path: str | None = Field(default=None, max_length=1024, description="Flat-file path to profile")
    file_format: Literal["csv", "parquet"] | None = None


class TopValue(BaseModel):
    value: Any | None
    count: int


class ColumnProfileRead(BaseModel):
    column: str
    null_count: int
    null_fraction: float
    distinct_count: int | None
    min_value: Any | None
    max_value: Any | None
    top_values: list[TopValue]


class ProfileRead(BaseModel):
    """Profile result. Identity fields are type-specific: SQL datasources fill
    `table` / `schema` (+ `catalog` for Unity Catalog), flat-file datasources fill
    `path` / `file_format`."""

    model_config = ConfigDict(populate_by_name=True)

    row_count: int
    columns: list[ColumnProfileRead]
    table: str | None = None
    schema_: str | None = Field(default=None, serialization_alias="schema")
    catalog: str | None = None
    path: str | None = None
    file_format: str | None = None


@router.post(
    "/suites/{suite_id}/profile",
    response_model=ProfileRead,
    summary="Profile columns of a table/file on the suite's connection (no persistence)",
)
def profile_columns(
    suite_id: uuid.UUID,
    payload: ColumnProfileRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ProfileRead:
    # sync def → threadpool; the datasource connect + scans/downloads are blocking.
    # Authoring aid → 'edit', same as the dry-run. Connection FK is RESTRICT.
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None
    result = profile.profile_connection(
        connection,
        columns=payload.columns,
        top_n=payload.top_n,
        table=payload.table,
        schema=payload.schema_,
        catalog=payload.catalog,
        path=payload.path,
        file_format=payload.file_format,
        secret_store=secret_store,
    )
    return ProfileRead(
        row_count=result.row_count,
        table=result.table,
        schema_=result.schema,
        catalog=result.catalog,
        path=result.path,
        file_format=result.file_format,
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
