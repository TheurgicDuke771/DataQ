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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.api.v1.runs import RunRead
from backend.app.core.auth import get_current_user, is_workspace_admin
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import Connection, Suite, User
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

log = get_logger(__name__)


class SuiteTarget(ApiModel):
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
    # Iceberg addresses a table by ``namespace.table``; run_target folds it in.
    namespace: str | None = Field(default=None, max_length=255)
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


class SuiteCreate(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    connection_id: uuid.UUID
    target: SuiteTarget | None = None


class SuiteUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    target: SuiteTarget | None = None


class SuiteRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    connection_id: uuid.UUID
    target: dict[str, Any] | None
    # The asset this suite's target resolves to (ADR 0034, #760) — the browse/reason
    # link the Assets view groups suites by. NULL for a targetless/unresolvable
    # suite (resolution is fail-soft). Deferred to #760 by the #764 review.
    asset_id: uuid.UUID | None = None
    # Failing-sample redaction policy (#415): {identifier_column?, pii_columns}. NULL
    # until set — the classifier still auto-classifies incidental columns at redaction
    # time; this stored policy pins the shown identifier + the always-masked columns.
    column_policy: dict[str, Any] | None = None
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
    # Best-effort: auto-derive the failing-sample redaction policy for the new
    # suite's target so samples have a locator without manual setup (#634). A fresh
    # suite never has a policy; fire only when it has a concrete target.
    if suite.target is not None:
        run_dispatch.dispatch_auto_classify(suite.id)
    # The creator is, by definition, the owner.
    return SuiteRead.of(suite, OWNER)


@router.get("/suites", response_model=list[SuiteRead], summary="List suites")
def list_suites(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    connection_id: uuid.UUID | None = None,
) -> list[SuiteRead]:
    # Scoped to suites the user owns or has a share on — or every suite for a
    # workspace-admin (ADR 0027). effective_permissions then stamps each as admin.
    suites = svc.list_suites(
        db,
        user_id=current_user.id,
        connection_id=connection_id,
        include_all=is_workspace_admin(current_user),
    )
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
    before = require_permission(db, suite_id, current_user.id, minimum="edit")
    # Snapshot the pre-update state — `update_suite` mutates `before` in place, so
    # capture the values (a copy of the target dict) before the call (#634/#643).
    had_policy = before.column_policy is not None
    old_target = dict(before.target) if before.target else None
    new_target = payload.target.to_storage() if payload.target is not None else None
    suite = svc.update_suite(
        db,
        suite_id,
        name=payload.name,
        description=payload.description,
        target=new_target,
    )
    # A target-setting update on a policy-less suite gets the same best-effort
    # auto-classify as create (#634) — e.g. a suite created target-less, now given
    # one. Never re-derives once a policy exists (the task also re-checks).
    if payload.target is not None and suite.target is not None and suite.column_policy is None:
        run_dispatch.dispatch_auto_classify(suite.id)
    # Repointing a *policied* suite to a different target can strand the stored
    # redaction policy — its `identifier_column`/`pii_columns` may not exist in the
    # new target. We deliberately don't auto-re-derive (don't clobber a
    # user/derived policy, #642), but the staleness was previously invisible. Emit
    # an observable event so an operator (or a future UI hint) can prompt a
    # re-run of Auto-detect (#643).
    elif had_policy and new_target is not None and new_target != old_target:
        log.warning(
            "suite_policy_possibly_stale",
            suite_id=str(suite.id),
            reason="target_changed_on_policied_suite",
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

    run = run_dispatch.new_queued_run(suite, triggered_by=f"manual:{current_user.id}")
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


class SourceConnectionRef(ApiModel):
    """A comparison check's portable source ref (ADR 0015) — `(name, env)` is the
    workspace-unique connection key, so it survives an export/import while a raw
    UUID would not."""

    name: str = Field(min_length=1, max_length=128)
    env: str = Field(min_length=1, max_length=16)


class CheckDocument(ApiModel):
    """One check inside a portable suite document — authoring fields only."""

    name: str = Field(min_length=1, max_length=256)
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    # DQ dimension (ADR 0038). Absent on an older export → derived on import,
    # exactly as if freshly authored. Optional in BOTH directions, so
    # EXPORT_VERSION does not bump (it bumps only on an incompatible shape).
    dimension: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    # Present only on comparison checks (ADR 0015); resolved on import.
    source_connection: SourceConnectionRef | None = None
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class SuiteDocument(ApiModel):
    """Portable suite — connection-agnostic, no DB identity. Both the export
    response and the import payload (a round-trippable document)."""

    version: int = suite_io.EXPORT_VERSION
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    checks: list[CheckDocument] = Field(default_factory=list)


class SuiteImportRequest(ApiModel):
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
    return SuiteDocument.model_validate(suite_io.export_suite(db, suite))


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
        # `dimension` is dropped when the payload did not SET it, so the service
        # can tell "an older document omits the field" (→ derive) from "this
        # document says the check is unclassified" (→ keep NULL). model_dump()
        # alone emits the key either way, which would classify every pre-ADR-0038
        # check on import — exactly the backfill ADR 0038 §5 forbids.
        checks=[
            {
                k: v
                for k, v in c.model_dump().items()
                if k != "dimension" or "dimension" in c.model_fields_set
            }
            for c in doc.checks
        ],
        connection_id=payload.connection_id,
        created_by=current_user.id,
    )
    return SuiteRead.model_validate(suite)


# ───────────────────────── column profiler (no persistence) ─────────


class ColumnProfileRequest(ApiModel):
    columns: list[str] = Field(min_length=1, max_length=50)
    top_n: int = Field(default=10, ge=1, le=100, description="Most-frequent values per column")
    # SQL datasources: the target is a table (+ schema; Unity Catalog also catalog).
    table: str | None = Field(
        default=None, max_length=255, description="SQL/Iceberg table to profile"
    )
    schema_: str | None = Field(default=None, alias="schema")
    catalog: str | None = Field(default=None, max_length=255, description="Unity Catalog catalog")
    # Iceberg: the table is addressed by an optional namespace (namespace.table).
    namespace: str | None = Field(default=None, max_length=255, description="Iceberg namespace")
    # Flat-file datasources (ADLS Gen2 / S3): the target is a file path.
    path: str | None = Field(default=None, max_length=1024, description="Flat-file path to profile")
    file_format: Literal["csv", "parquet"] | None = None


class TopValue(ApiModel):
    value: Any | None
    count: int


class ColumnProfileRead(ApiModel):
    column: str
    null_count: int
    null_fraction: float
    distinct_count: int | None
    min_value: Any | None
    max_value: Any | None
    top_values: list[TopValue]


class ProfileRead(ApiModel):
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
        namespace=payload.namespace,
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


class ColumnsRead(ApiModel):
    """The column names of a suite target — feeds the check editor's column
    dropdown (#474) so authors pick instead of recalling exact names."""

    columns: list[str]


@router.get(
    "/suites/{suite_id}/columns",
    response_model=ColumnsRead,
    summary="List the column names of a table/file on the suite's connection",
)
def list_columns(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
    table: Annotated[str | None, Query(max_length=255)] = None,
    schema_: Annotated[str | None, Query(alias="schema", max_length=255)] = None,
    catalog: Annotated[str | None, Query(max_length=255)] = None,
    namespace: Annotated[str | None, Query(max_length=255)] = None,
    path: Annotated[str | None, Query(max_length=1024)] = None,
    file_format: Annotated[Literal["csv", "parquet"] | None, Query()] = None,
) -> ColumnsRead:
    # sync def → threadpool; the datasource connect/introspect is blocking.
    # Authoring aid → 'edit', same gate as the profiler/dry-run.
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None
    columns = profile.list_columns(
        connection,
        table=table,
        schema=schema_,
        catalog=catalog,
        namespace=namespace,
        path=path,
        file_format=file_format,
        secret_store=secret_store,
    )
    return ColumnsRead(columns=columns)


# ── failing-sample redaction policy (#415) ──────────────────────────────────


class ColumnPolicyRead(ApiModel):
    """A suite's failing-sample redaction policy: the shown ``identifier_column``
    (a non-PII row locator) + the always-masked ``pii_columns``."""

    identifier_column: str | None = None
    pii_columns: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, policy: dict[str, Any] | None) -> ColumnPolicyRead:
        policy = policy or {}
        return cls(
            identifier_column=policy.get("identifier_column"),
            pii_columns=list(policy.get("pii_columns") or []),
        )


class ColumnPolicyUpdate(ApiModel):
    identifier_column: str | None = Field(default=None, max_length=255)
    pii_columns: list[str] = Field(default_factory=list, max_length=200)


class ColumnPolicySuggestRequest(ApiModel):
    """The suite's target to profile + classify — same shape as the profiler request,
    minus ``columns`` (all of the target's columns are classified)."""

    top_n: int = Field(default=20, ge=1, le=100)
    table: str | None = Field(default=None, max_length=255)
    schema_: str | None = Field(default=None, alias="schema")
    catalog: str | None = Field(default=None, max_length=255)
    namespace: str | None = Field(default=None, max_length=255)
    path: str | None = Field(default=None, max_length=1024)
    file_format: Literal["csv", "parquet"] | None = None


@router.get(
    "/suites/{suite_id}/column-policy",
    response_model=ColumnPolicyRead,
    summary="Get the suite's failing-sample redaction policy (#415)",
)
def get_column_policy(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ColumnPolicyRead:
    suite = require_permission(db, suite_id, current_user.id, minimum="view")
    return ColumnPolicyRead.of(suite.column_policy)


@router.put(
    "/suites/{suite_id}/column-policy",
    response_model=ColumnPolicyRead,
    summary="Set the suite's failing-sample redaction policy (#415)",
)
def set_column_policy(
    suite_id: uuid.UUID,
    payload: ColumnPolicyUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ColumnPolicyRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    suite = svc.set_column_policy(
        db,
        suite_id,
        identifier_column=payload.identifier_column,
        pii_columns=payload.pii_columns,
    )
    return ColumnPolicyRead.of(suite.column_policy)


@router.post(
    "/suites/{suite_id}/column-policy/suggest",
    response_model=ColumnPolicyRead,
    summary="Suggest a redaction policy by profiling + classifying the target (no save)",
)
def suggest_column_policy(
    suite_id: uuid.UUID,
    payload: ColumnPolicySuggestRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ColumnPolicyRead:
    # sync def → threadpool; the datasource connect + column list/profile are blocking.
    # Authoring aid → 'edit'. Lists the target's columns, profiles them for sample
    # values, then classifies name+values into an {identifier, pii} suggestion the
    # author reviews and PUTs. Not persisted here.
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None
    policy = profile.suggest_policy_for_target(
        connection,
        table=payload.table,
        schema=payload.schema_,
        catalog=payload.catalog,
        namespace=payload.namespace,
        path=payload.path,
        file_format=payload.file_format,
        top_n=payload.top_n,
        secret_store=secret_store,
    )
    return ColumnPolicyRead.of(policy)
