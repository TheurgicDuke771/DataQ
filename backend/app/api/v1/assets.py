"""Read-only asset view API (ADR 0034, gap G-d phase 2, #760)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import (
    TOTAL_COUNT_HEADER,
    ApiModel,
    ApiRequestModel,
    total_count_responses,
)
from backend.app.core.auth import get_current_user, require_workspace_admin
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.lineage.columns import DEFAULT_TRACE_DEPTH, MAX_TRACE_DEPTH, TraceDirection
from backend.app.services import asset_view_service as svc

router = APIRouter(tags=["assets"])


class RunOutcomeRead(ApiModel):
    """A suite's latest run outcome — execution status + the DQ summary."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID | None
    status: str | None
    worst_severity: str | None
    checks_total: int
    checks_passed: int
    finished_at: datetime | None
    created_at: datetime | None


class ComposingSuiteRead(ApiModel):
    """One suite the caller can see that targets the asset, with its latest run."""

    model_config = ConfigDict(from_attributes=True)

    suite_id: uuid.UUID
    name: str
    my_permission: str
    latest_run: RunOutcomeRead


class AssetSummaryRead(ApiModel):
    """List-row aggregation for one asset — **workspace-true** (ADR 0037): every
    field is identical for every viewer, aggregated over ALL composing suites.
    Carries **two orthogonal health axes** (#803) the UI renders separately:
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    description: str | None
    owner_user_id: uuid.UUID | None
    #: Left out of its connection's automatic coverage (ADR 0047).
    auto_coverage_excluded: bool = False
    last_seen: datetime
    suite_count: int
    worst_severity: str | None
    checks_total: int
    checks_passed: int
    last_run_at: datetime | None
    # 0-100 over every evaluated result of the composing suites' latest complete runs,
    # with the workspace's severity weights. NULL when nothing evaluated (never run, or
    # only skip/error) — not 0, which means it ran and everything was critical.
    health_score: float | None = None
    # Latest-run execution states (distinct from check severity): any composing
    # suite's latest run `failed` / still `queued`/`running`.
    has_failed_run: bool
    has_active_run: bool
    # Connection health (#803): a failed run OR any `error` result → DataQ could not evaluate
    # against the datasource; `skip` → a precondition wasn't met (degraded).
    has_cancelled_run: bool
    has_operational_error: bool
    has_skip: bool


class LineageNodeRead(ApiModel):
    """A lineage neighbour — OpenLineage identity + whether it is monitored. No
    run data (blast-radius browse only; ADR 0034 §2). Fully named for every
    member (ADR 0037 — lineage topology is identity).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    is_monitored: bool
    depth: int
    # TRANSITION SHIM (#924 review — remove after one release): the pre-ADR-0037 SPA bundle
    # computes `redacted = !isCenter && !is_accessible` per node; absent the field, every
    # neighbour in a cached tab renders as an unclickable Restricted box until hard refresh.
    is_accessible: bool = True


class LineageEdgeRead(ApiModel):
    """One edge of the lineage neighbourhood, `source` (upstream) → `target`
    (downstream) asset id. The UI draws exactly these — without them a graph could
    only guess which depth-2 node hangs off which depth-1 node (#805).
    """

    model_config = ConfigDict(from_attributes=True)

    source: uuid.UUID
    target: uuid.UUID
    columns: list[tuple[str, str]] | None = None
    # #1710: why `columns` is null when it is — `recorded` · `none_recorded` (the source reads
    # column lineage and recorded none here) · `unavailable` (its column read failed) · `unknown`
    # (not refreshed since this was tracked) · `not_captured` (dbt/catalog: never column-grain).
    column_coverage: str = "unknown"


class LineageSourceHealthRead(ApiModel):
    """A lineage-feeding connection whose poll is currently failing (#828)."""

    model_config = ConfigDict(from_attributes=True)

    connection_id: uuid.UUID
    name: str
    type: str
    consecutive_failures: int
    last_error: str | None = None
    last_polled_at: datetime | None = None


class WarehouseLineageStatusRead(ApiModel):
    """A warehouse-native lineage source (Snowflake / UC) that is degraded or failing —
    so the graph can be qualified rather than shown as complete + current (#828, #858).
    """

    model_config = ConfigDict(from_attributes=True)

    connection_id: uuid.UUID
    name: str
    type: str
    tier: str | None = None
    degraded_reason: str | None = None
    last_error: str | None = None
    last_refreshed_at: datetime | None = None
    # #1091: the refresh loop silently stopped — no error, no degradation, just no refresh within
    # LINEAGE_STALE_AFTER_HOURS.
    stale: bool = False
    # #1236: the last refresh left stale edges unpruned, so the graph from this source can only
    # grow. Snapshot sources only (an incremental source never prunes).
    prune_suspended: bool = False
    # When this source last pruned. `null` means NEVER — not "recently", and not unknown-because-
    # the-field-is-new.
    prune_suspended_since: datetime | None = None


class DimensionScoreRead(ApiModel):
    """One scorecard row (#889, ADR 0038)."""

    model_config = ConfigDict(from_attributes=True)

    dimension: str
    # Checks that EXIST — coverage. A check authored today counts before it runs.
    checks_total: int
    # Of those, how many passed in the latest run.
    checks_passing: int
    # How many evaluated a severity (the score's denominator): excludes skip/error
    # AND checks that have not run yet.
    checks_evaluated: int
    score: float | None


class ScorecardRead(ApiModel):
    """Per-dimension coverage + score, **workspace-true** (ADR 0037) — identical
    for every viewer who can see the asset.
    """

    model_config = ConfigDict(from_attributes=True)

    covered: list[DimensionScoreRead]
    uncovered: list[str]
    unclassified_checks: int


class InheritedSourceRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: uuid.UUID
    asset_name: str
    column: str


class InheritedClassificationRead(ApiModel):
    """A column masked as sensitive only because recorded lineage traces it to a sensitive
    upstream column. Tagging the column `public` in the warehouse overrides the inheritance."""

    model_config = ConfigDict(from_attributes=True)

    column: str
    sources: list[InheritedSourceRead]


class AssetDetailRead(ApiModel):
    """Asset detail: the workspace-true summary + the caller's per-suite breakdown
    + upstream/downstream lineage. `suites` lists only suites the caller can view
    (ADR 0027); `restricted_suite_count` is how many more compose the asset — they
    roll into `summary` (workspace-true) but stay unnamed.
    """

    model_config = ConfigDict(from_attributes=True)

    summary: AssetSummaryRead
    suites: list[ComposingSuiteRead]
    scorecard: ScorecardRead | None = None
    restricted_suite_count: int = 0
    upstream: list[LineageNodeRead]
    downstream: list[LineageNodeRead]
    lineage_edges: list[LineageEdgeRead]
    # Non-empty ⇒ lineage may be stale/absent for reasons unrelated to this asset.
    failing_lineage_sources: list[LineageSourceHealthRead] = Field(default_factory=list)
    # Non-empty ⇒ a warehouse lineage source is coarse (degraded tier) or failing.
    warehouse_lineage_status: list[WarehouseLineageStatusRead] = Field(default_factory=list)
    # null = could not be determined (a lineage read failed), not "nothing inherited".
    inherited_classifications: list[InheritedClassificationRead] | None = Field(
        default_factory=list
    )
    inherited_classifications_truncated: bool = False


class ColumnTraceAssetRead(ApiModel):
    """The identity of an asset a column trace names."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    is_monitored: bool


class ColumnNodeRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: uuid.UUID
    column: str
    depth: int


class ColumnHopRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    upstream_asset_id: uuid.UUID
    upstream_column: str
    downstream_asset_id: uuid.UUID
    downstream_column: str


class CoverageGapRead(ApiModel):
    """A table edge on the walk that carries no column pairs — the column MAY cross it."""

    model_config = ConfigDict(from_attributes=True)

    upstream_asset_id: uuid.UUID
    downstream_asset_id: uuid.UUID
    coverage: str


class ColumnOriginRead(ApiModel):
    """An upstream-most column the walk reached (the traced column itself is never listed).
    `confirmed: false` ⇒ a gap or the depth cap means something further upstream may still
    feed it."""

    model_config = ConfigDict(from_attributes=True)

    asset_id: uuid.UUID
    column: str
    depth: int
    confirmed: bool


class ColumnTraceRead(ApiModel):
    """Column-grain provenance (`upstream`, `origins`) and impact (`downstream`) for one column.

    `*_status` per direction: `traced` · `no_table_lineage` · `none_recorded` · `incomplete`
    (null when that direction was not requested). `complete` is false whenever `gaps` is
    non-empty or `truncated` — then an absent column is NOT evidence of no dependency (#828).
    The column's existence on the asset is not verified: a misspelling traces to nothing.
    """

    asset_id: uuid.UUID
    column: str
    upstream: list[ColumnNodeRead]
    downstream: list[ColumnNodeRead]
    hops: list[ColumnHopRead]
    gaps: list[CoverageGapRead]
    origins: list[ColumnOriginRead]
    upstream_status: str | None
    downstream_status: str | None
    truncated: bool
    complete: bool
    assets: list[ColumnTraceAssetRead]
    # Table-level lineage qualifiers — the trace inherits every one of them.
    qualified_by: list[str]


class AssetMetadataUpdate(ApiRequestModel):
    """Partial metadata update (workspace-Admin-only). Each field is optional; an
    explicit `null` clears it, an omitted field leaves it unchanged — the two are
    distinguished via `model_fields_set` at the route so `owner_user_id: null`
    means "unassign" rather than "leave as is".
    """

    owner_user_id: uuid.UUID | None = None
    # Same cap as suite descriptions (SuiteCreate).
    description: str | None = Field(default=None, max_length=1024)
    # Leave this table out of its connection's automatic coverage (ADR 0047).
    auto_coverage_excluded: bool | None = None


_LIST_LIMIT_DEFAULT = 200
_LIST_LIMIT_MAX = 200


@router.get(
    "/assets",
    response_model=list[AssetSummaryRead],
    summary="List assets",
    responses=total_count_responses(
        "Total assets in the workspace (#925) — the same "
        "unfiltered population this page's limit/offset slice "
        "into. A page shorter than `limit` doesn't by itself "
        "prove there's no more; compare against this header."
    ),
)
def list_assets(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    limit: int = Query(default=_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    sort: Annotated[
        Literal["name", "health_score"],
        Query(
            description=(
                "`name` orders by namespace then name. `health_score` orders the whole "
                "population lowest score first, assets with no score last."
            )
        ),
    ] = "name",
) -> list[svc.AssetSummary]:
    # Workspace-true (ADR 0037): identical rows for every member — the service takes no user.
    response.headers[TOTAL_COUNT_HEADER] = str(svc.count_assets(db))
    return svc.list_visible_assets(db, limit=limit, offset=offset, sort=sort)


@router.get("/assets/{asset_id}", response_model=AssetDetailRead, summary="Get an asset")
def get_asset(
    asset_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> svc.AssetDetail:
    # Opens for every member (ADR 0037) — only a truly unknown id 404s. The caller
    # shapes the composing-suite LIST only (their ADR 0027 grants; admins see all).
    return svc.get_visible_asset(
        db, asset_id, user_id=current_user.id, include_all=is_workspace_admin(current_user)
    )


@router.patch(
    "/assets/{asset_id}",
    response_model=AssetSummaryRead,
    summary="Update asset metadata (workspace-admin only)",
)
def update_asset(
    asset_id: uuid.UUID,
    payload: AssetMetadataUpdate,
    # Workspace-Admin-only (ADR 0034 §4) — a non-admin gets a real 403.
    admin: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> svc.AssetSummary:
    fields = payload.model_fields_set
    asset = svc.update_asset_metadata(
        db,
        asset_id,
        owner_user_id=payload.owner_user_id,
        description=payload.description,
        set_owner="owner_user_id" in fields,
        set_description="description" in fields,
        auto_coverage_excluded=payload.auto_coverage_excluded,
        actor_id=admin.id,
    )
    # Return the refreshed workspace-true summary. Never 404s on an asset with no
    # composing suites — metadata exists independently of suites.
    return svc.summarize_asset(db, asset)


@router.get(
    "/assets/{asset_id}/column-lineage",
    response_model=ColumnTraceRead,
    summary="Trace one column's lineage",
)
def trace_asset_column(
    asset_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    column: str = Query(min_length=1, max_length=255),
    direction: TraceDirection = TraceDirection.BOTH,
    max_depth: int = Query(default=DEFAULT_TRACE_DEPTH, ge=1, le=MAX_TRACE_DEPTH),
) -> ColumnTraceRead:
    # Workspace-visible topology (ADR 0037): opens for every member, 404 only on an unknown id.
    view = svc.trace_asset_column(db, asset_id, column, direction=direction, max_depth=max_depth)
    trace = view.trace
    return ColumnTraceRead(
        asset_id=trace.asset_id,
        column=trace.column,
        upstream=[ColumnNodeRead.model_validate(n) for n in trace.upstream],
        downstream=[ColumnNodeRead.model_validate(n) for n in trace.downstream],
        hops=[ColumnHopRead.model_validate(h) for h in trace.hops],
        gaps=[CoverageGapRead.model_validate(g) for g in trace.gaps],
        origins=[ColumnOriginRead.model_validate(o) for o in trace.origins],
        upstream_status=trace.upstream_status,
        downstream_status=trace.downstream_status,
        truncated=trace.truncated,
        complete=trace.complete,
        assets=[ColumnTraceAssetRead.model_validate(a) for a in view.assets],
        qualified_by=view.qualified_by,
    )
