"""Suite CRUD endpoints."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.api.v1.runs import RunRead
from backend.app.core.auth import MemberUser, get_current_user
from backend.app.core.logging import get_logger
from backend.app.core.roles import is_workspace_admin
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.datasources.sampling import MAX_SAMPLE_ROWS
from backend.app.db.models import Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.services import (
    credential_health,
    live_probe,
    orchestration_service,
    run_dispatch,
    run_service,
    run_target,
)
from backend.app.services import profile_service as profile
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


class SuiteSampling(ApiRequestModel):
    """Row-cap declaration on a run target (#595) — see `datasources.sampling`."""

    # Spelled literally because `Literal[...]` needs constants, not names — a canary test asserts
    # these two stay equal to `SAMPLE_HEAD`/`SAMPLE_RANDOM`, so the duplication cannot drift
    # silently.
    strategy: Literal["head", "random"]
    rows: int = Field(ge=1, le=MAX_SAMPLE_ROWS)
    #: `random` only — a seed makes the draw reproducible.
    seed: int | None = None


class SuiteTarget(ApiRequestModel):
    """Datasource-shaped run target (#215) — which table / flat-file path / Unity
    Catalog name the suite's checks run against. Same shape as the column-profiler
    request; `run_target.resolve_target` validates the right fields per connection
    type (`table` for SQL, `path` for flat files, `catalog` for Unity Catalog).
    """

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
    # Scale-aware execution (#595): bound what a run materialises.
    sampling: SuiteSampling | None = None

    def to_storage(self) -> dict[str, Any]:
        """JSONB dict with the canonical `schema` key (not the `schema_` alias)."""
        return self.model_dump(by_alias=True, exclude_none=True)


class SuiteCreate(ApiRequestModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    connection_id: uuid.UUID
    target: SuiteTarget | None = None


class SuiteUpdate(ApiRequestModel):
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
    # The asset this suite's target resolves to (ADR 0034, #760) — the browse/reason link the Assets
    # view groups suites by.
    asset_id: uuid.UUID | None = None
    # Failing-sample redaction policy (#415): {identifier_column?, pii_columns}.
    column_policy: dict[str, Any] | None = None
    #: `None` once the creating user is erased — the row outlives its author (`ondelete=SET NULL`,
    #: #1319).
    created_by: uuid.UUID | None
    # The caller's effective level on this suite (`owner`/`admin`/`edit`/`view`) so the UI can gate
    # per-suite actions — manage shares, delete.
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
    current_user: MemberUser,
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
    # Best-effort: auto-derive the failing-sample redaction policy for the new suite's target so
    # samples have a locator without manual setup (#634).
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
        actor_id=current_user.id,
    )
    # A target-setting update on a policy-less suite gets the same best-effort auto-classify as
    # create (#634) — e.g. a suite created target-less, now given one.
    if payload.target is not None and suite.target is not None and suite.column_policy is None:
        run_dispatch.dispatch_auto_classify(suite.id)
    # Repointing a *policied* suite to a different target can strand the stored redaction policy —
    # its `identifier_column`/`pii_columns` may not exist in the new target.
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
    svc.delete_suite(db, suite_id, actor_id=current_user.id)


class SuiteDeletionImpactRead(ApiModel):
    """Exact dependent counts a suite delete would destroy (#1320) — computed via
    `COUNT(*)`, never estimated or capped. States the blast radius of `DELETE
    /suites/{id}` before the fact, since checks/runs/results cascade with no undo.
    """

    checks: int
    runs: int
    results: int
    trigger_bindings: int
    schedules: int
    notification_channel_links: int


@router.get(
    "/suites/{suite_id}/deletion_impact",
    response_model=SuiteDeletionImpactRead,
    summary="Exact dependent counts a suite delete would destroy",
)
def get_deletion_impact(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteDeletionImpactRead:
    # Same grant as the delete itself — a view/edit-only caller must not see the counts.
    require_permission(db, suite_id, current_user.id, minimum="admin")
    return SuiteDeletionImpactRead(**svc.deletion_impact(db, suite_id))


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
    """Queue a run of the suite and dispatch it to the worker."""
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None  # FK is RESTRICT; a suite always has its connection
    # Raises SuiteTargetInvalidError (422) for a targetless / wrong-datasource target.
    run_target.resolve_target(connection.type, suite.target)

    run = run_dispatch.new_queued_run(suite, triggered_by=f"manual:{current_user.id}")
    db.add(run)
    db.commit()
    db.refresh(run)

    # Shared create-adjacent dispatch+broker-failure block (#227): on a broker outage the run is
    # marked terminal-`failed` (never left stuck `queued`) and we surface 503.
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
    UUID would not.
    """

    name: str = Field(min_length=1, max_length=128)
    env: str = Field(min_length=1, max_length=16)


class CheckDocument(ApiModel):
    """One check inside a portable suite document — authoring fields only."""

    name: str = Field(min_length=1, max_length=256)
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    # DQ dimension (ADR 0038).
    dimension: str | None = None
    # Evaluating engine (ADR 0036).
    engine: str = Field(default="gx", min_length=1, max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
    # Present only on comparison checks (ADR 0015); resolved on import.
    source_connection: SourceConnectionRef | None = None
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class SuiteDocument(ApiModel):
    """Portable suite — connection-agnostic, no DB identity. The `GET /export`
    response shape; `SuiteDocumentIn` below is the identically-shaped import
    payload (#1505 — a response model stays lenient on `extra`, a request one
    doesn't).
    """

    version: int = suite_io.EXPORT_VERSION
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    checks: list[CheckDocument] = Field(default_factory=list)


class SourceConnectionRefIn(ApiRequestModel):
    """Request-side twin of `SourceConnectionRef` (see there) — the export
    response and the import payload are the same *shape*, but only the import
    side should 422 on an unknown key.
    """

    name: str = Field(min_length=1, max_length=128)
    env: str = Field(min_length=1, max_length=16)


class CheckDocumentIn(ApiRequestModel):
    """Request-side twin of `CheckDocument` — see there for field meaning."""

    name: str = Field(min_length=1, max_length=256)
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    dimension: str | None = None
    engine: str = Field(default="gx", min_length=1, max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
    source_connection: SourceConnectionRefIn | None = None
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class SuiteDocumentIn(ApiRequestModel):
    """Request-side twin of `SuiteDocument`, accepted as the `import_suite`
    payload's nested document. `SuiteDocument` itself stays on `ApiModel`
    (`GET /export`'s response model) — `export_suite` always builds it from a
    closed field set, so nothing there needs `forbid`, and response models stay
    off it per `ApiRequestModel`'s own contract.
    """

    version: int = suite_io.EXPORT_VERSION
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    checks: list[CheckDocumentIn] = Field(default_factory=list)


class SuiteImportRequest(ApiRequestModel):
    connection_id: uuid.UUID
    document: SuiteDocumentIn


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
    current_user: MemberUser,
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    # Like create_suite: Member+ (ADR 0033 — a Viewer is read-only and cannot become an owner), and
    # the new suite is owned by the importer.
    doc = payload.document
    suite = suite_io.import_suite(
        db,
        version=doc.version,
        name=doc.name,
        description=doc.description,
        # `dimension` is dropped when the payload did not SET it.
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


class ColumnProfileRequest(ApiRequestModel):
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
    `path` / `file_format`.
    """

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
        session=db,
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
    # Live probe: `top_values` / `min_value` / `max_value` are real cell contents, and this route
    # consulted no policy and wrote no audit event (#1419/#1479).
    policy = suite.column_policy
    probed_other_target = any(
        v is not None for v in (payload.table, payload.path, payload.schema_, payload.catalog)
    )
    tags = live_probe.applicable_tags(
        run_service.asset_column_tags(db, suite), probed_other_target=probed_other_target
    )
    sensitive = live_probe.sensitive_profile_columns(result.columns, policy=policy, tags=tags)
    masked = live_probe.values_are_masked(policy, destination=live_probe.Destination.INTERACTIVE)
    columns = (
        live_probe.mask_profile_columns(result.columns, sensitive=sensitive)
        if masked
        else result.columns
    )
    live_probe.record_probe_access(
        db,
        action="column.profile",
        suite_id=suite.id,
        actor=current_user,
        destination=live_probe.Destination.INTERACTIVE,
        masked=masked,
        columns=[c.column for c in result.columns],
        sensitive_columns=sensitive,
        detail={"table": result.table, "path": result.path, "row_count": result.row_count},
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
            # `columns`, NOT `result.columns` — iterating the raw list here is what
            # would make the masking above inert while looking correct.
            for c in columns
        ],
    )


class SuiteCadenceRead(ApiModel):
    """A suite's bound-pipeline cadence (#1648) — the deterministic freshness-
    threshold hint the LLM suggestion prompt also uses when one is configured,
    but computed here without any LLM at all.
    """

    bound: bool
    provider: str | None = None
    pipeline_or_dag_id: str | None = None
    env: str | None = None
    sample_count: int = 0
    insufficient_history: bool = True
    median_gap_hours: float | None = None
    max_gap_hours: float | None = None
    suggested_fail_threshold_hours: float | None = None


@router.get(
    "/suites/{suite_id}/cadence",
    response_model=SuiteCadenceRead,
    summary="A suite's bound-pipeline cadence — a freshness-threshold hint, no LLM required",
)
def get_suite_cadence(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteCadenceRead:
    # Authoring aid → 'edit', same as profile/dry-run.
    require_permission(db, suite_id, current_user.id, minimum="edit")
    binding = orchestration_service.get_enabled_binding(db, suite_id)
    if binding is None:
        return SuiteCadenceRead(bound=False)
    cadence = orchestration_service.compute_pipeline_cadence(
        db,
        provider=binding.provider,
        pipeline_or_dag_id=binding.pipeline_or_dag_id,
        env=binding.env,
    )
    return SuiteCadenceRead(
        bound=True,
        provider=binding.provider,
        pipeline_or_dag_id=binding.pipeline_or_dag_id,
        env=binding.env,
        sample_count=cadence.sample_count,
        insufficient_history=cadence.insufficient_history,
        median_gap_hours=cadence.median_gap_hours,
        max_gap_hours=cadence.max_gap_hours,
        suggested_fail_threshold_hours=cadence.suggested_fail_threshold_hours,
    )


class ColumnsRead(ApiModel):
    """The column names of a suite target — feeds the check editor's column
    dropdown (#474) so authors pick instead of recalling exact names.
    """

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
        session=db,
        table=table,
        schema=schema_,
        catalog=catalog,
        namespace=namespace,
        path=path,
        file_format=file_format,
        secret_store=secret_store,
    )
    return ColumnsRead(columns=columns)


# ── flat-file batch-target preview (#1193) ──────────────────────────


class BatchPreviewRead(ApiModel):
    """The concrete file path a batch-target spec resolves to right now — the same
    live resolution `run_target.materialize_path` performs at run time, run early
    and without persisting anything (#1193). Callers re-request on every field
    change to keep the "resolves to" hint live while authoring.
    """

    path: str


@router.get(
    "/suites/{suite_id}/batch-preview",
    response_model=BatchPreviewRead,
    summary="Resolve a flat-file batch pattern against the live listing (no persistence)",
)
def preview_batch_target(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
    pattern: Annotated[str, Query(min_length=1, max_length=1024)],
    strategy: Annotated[Literal["latest", "specific"], Query()] = "latest",
    batch: Annotated[str | None, Query(max_length=255)] = None,
    prefix: Annotated[str, Query(max_length=1024)] = "",
) -> BatchPreviewRead:
    # sync def → threadpool; the object listing is blocking.
    # Authoring aid → 'edit', same gate as the profiler/columns/policy-suggest.
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None
    # Every failure mode is already a typed `DataQError` from `run_target` (batch_preview_no_data /
    # _invalid / _failed, or suite_target_invalid), so the router stays a pass-through.
    # Credential-health seam (#1697) — `preview_batch` takes primitives, not the ORM row,
    # so the seam sits here where the row is in scope. The only door in this file that
    # does not reach the signal through a service-layer `session=` argument.
    with credential_health.credential_use(db, connection):
        path = run_target.preview_batch(
            connection.type,
            connection.config,
            prefix=prefix,
            pattern=pattern,
            strategy=strategy,
            batch=batch,
            secret_ref=connection.secret_ref,
            secret_store=secret_store,
        )
    return BatchPreviewRead(path=path)


# ── failing-sample redaction policy (#415) ──────────────────────────────────


class ColumnPolicyRead(ApiModel):
    """A suite's failing-sample redaction policy: the shown ``identifier_column``
    (a non-PII row locator) + the always-masked ``pii_columns``, plus whether the
    suite is in fail-closed mode.
    """

    identifier_column: str | None = None
    pii_columns: list[str] = Field(default_factory=list)
    #: Fail-closed mode (G3 / #433).
    require_classification: bool = False

    @classmethod
    def of(cls, policy: dict[str, Any] | None) -> ColumnPolicyRead:
        policy = policy or {}
        return cls(
            identifier_column=policy.get("identifier_column"),
            pii_columns=list(policy.get("pii_columns") or []),
            require_classification=bool(policy.get("require_classification")),
        )


class ColumnPolicyUpdate(ApiRequestModel):
    identifier_column: str | None = Field(default=None, max_length=255)
    pii_columns: list[str] = Field(default_factory=list, max_length=200)
    #: Fail-closed mode (G3 / #433).
    require_classification: bool | None = None
    """Tri-state: omit to LEAVE UNCHANGED, or send true/false to set it.

    This route is a full replacement, so a plain `False` default would let any
    client that predates the flag — including our own shipped Save button —
    silently switch fail-closed OFF while editing an unrelated field. For a
    control whose whole job is to be conservative, that is the worst available
    failure, so absence preserves. Turning it off is still possible; it just has
    to be said out loud.
    """


class ColumnPolicySuggestRequest(ApiRequestModel):
    """The suite's target to profile + classify — same shape as the profiler request,
    minus ``columns`` (all of the target's columns are classified).
    """

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
        require_classification=payload.require_classification,
        actor_id=current_user.id,
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
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None
    policy = profile.suggest_policy_for_target(
        connection,
        session=db,
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
