"""Read-only asset view API (ADR 0034, gap G-d phase 2, #760).

Assets are the browse/reason grain over the suite execution grain. This surface
lists the assets a caller can see and drills into one — its composing suites +
their latest run health + the lineage neighbourhood.

**The ADR 0037 three-layer rule:** asset identity + lineage topology (incl.
column pairs) are visible to every member; the aggregate rollup is
workspace-true (over ALL composing suites — one verdict for every viewer); only
the itemized layer — the composing-suite list — is filtered to the caller's
ADR 0027 grants, with the rest collapsing to `restricted_suite_count`. All of
that lives in `asset_view_service`; this module is a thin HTTP layer.

Asset-metadata mutation (`PATCH`) is **workspace-Admin-only** (ADR 0034 §4) —
gated by `require_workspace_admin`, the same 403 the /admin surface uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user, is_workspace_admin, require_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
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

    - *Suite health* (data quality) — `worst_severity` / `checks_*` over the
      **evaluated** checks of the composing suites' latest runs;
      `worst_severity` is null when all passed or nothing has run. Operational
      results never rank here.
    - *Connection health* (reachability) — `has_operational_error` / `has_skip`
      (plus the execution states below): could DataQ execute against the
      datasource at all? Derived from the recorded runs only — no connection-probe
      polling loop.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    description: str | None
    owner_user_id: uuid.UUID | None
    last_seen: datetime
    suite_count: int
    worst_severity: str | None
    checks_total: int
    checks_passed: int
    last_run_at: datetime | None
    # Latest-run execution states (distinct from check severity): any composing
    # suite's latest run `failed` / still `queued`/`running`.
    has_failed_run: bool
    has_active_run: bool
    # Connection health (#803): a failed run OR any `error` result → DataQ could not
    # evaluate against the datasource; `skip` → a precondition wasn't met (degraded);
    # `cancelled` → the run was killed, so it proves nothing (never rolls up green).
    has_cancelled_run: bool
    has_operational_error: bool
    has_skip: bool


class LineageNodeRead(ApiModel):
    """A lineage neighbour — OpenLineage identity + whether it is monitored. No
    run data (blast-radius browse only; ADR 0034 §2). Fully named for every
    member (ADR 0037 — lineage topology is identity).

    `depth` is the hop distance from the asset under view (1 = a direct neighbour):
    the graph view lays nodes out in hop columns rather than flattening every hop
    into one list (#805)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    is_monitored: bool
    depth: int
    # TRANSITION SHIM (#924 review — remove after one release): the pre-ADR-0037
    # SPA bundle computes `redacted = !isCenter && !is_accessible` per node; with
    # the field absent, every neighbour in a cached tab renders as an unclickable
    # "🔒 Restricted" box until a hard refresh. Constant True keeps old bundles
    # rendering correctly through the deploy window; nothing reads it server-side.
    is_accessible: bool = True


class LineageEdgeRead(ApiModel):
    """One edge of the lineage neighbourhood, `source` (upstream) → `target`
    (downstream) asset id. The UI draws exactly these — without them a graph could
    only guess which depth-2 node hangs off which depth-1 node (#805).

    `columns` is the edge's column-level refinement (#901) where a warehouse source
    recorded one — `[upstream_column, downstream_column]` pairs, shown to every
    member (ADR 0037 — column names are schema metadata). Null ⇒ the edge has no
    column grain (a table-level source recorded it)."""

    model_config = ConfigDict(from_attributes=True)

    source: uuid.UUID
    target: uuid.UUID
    columns: list[tuple[str, str]] | None = None


class LineageSourceHealthRead(ApiModel):
    """A lineage-feeding connection whose poll is currently failing (#828).

    Present so the UI can never render a clean "no lineage recorded" empty state over a
    broken integration — the failure mode that let prod lineage rot for six days behind
    an expired credential while the product reported nothing wrong.

    `last_error` is a **classified** reason, never raw exception text.
    """

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

    `tier` is the source that answered (e.g. `snowflake_object_dependencies`);
    `degraded_reason` is the "working but coarse" note (view-level only, Enterprise
    needed); `last_error` is a **classified** refresh failure. A healthy full-tier source
    is not listed.
    """

    model_config = ConfigDict(from_attributes=True)

    connection_id: uuid.UUID
    name: str
    type: str
    tier: str | None = None
    degraded_reason: str | None = None
    last_error: str | None = None
    last_refreshed_at: datetime | None = None


class AssetDetailRead(ApiModel):
    """Asset detail: the workspace-true summary + the caller's per-suite breakdown
    + upstream/downstream lineage. `suites` lists only suites the caller can view
    (ADR 0027); `restricted_suite_count` is how many more compose the asset — they
    roll into `summary` (workspace-true) but stay unnamed."""

    model_config = ConfigDict(from_attributes=True)

    summary: AssetSummaryRead
    suites: list[ComposingSuiteRead]
    restricted_suite_count: int = 0
    upstream: list[LineageNodeRead]
    downstream: list[LineageNodeRead]
    lineage_edges: list[LineageEdgeRead]
    # Non-empty ⇒ lineage may be stale/absent for reasons unrelated to this asset.
    failing_lineage_sources: list[LineageSourceHealthRead] = Field(default_factory=list)
    # Non-empty ⇒ a warehouse lineage source is coarse (degraded tier) or failing.
    warehouse_lineage_status: list[WarehouseLineageStatusRead] = Field(default_factory=list)


class AssetMetadataUpdate(ApiModel):
    """Partial metadata update (workspace-Admin-only). Each field is optional; an
    explicit `null` clears it, an omitted field leaves it unchanged — the two are
    distinguished via `model_fields_set` at the route so `owner_user_id: null`
    means "unassign" rather than "leave as is".

    `extra="forbid"`: because omitted-vs-null is semantically load-bearing here, a
    typo'd field name (`descripton`) must be a 422, not a silently-ignored no-op."""

    model_config = ConfigDict(extra="forbid")

    owner_user_id: uuid.UUID | None = None
    # Same cap as suite descriptions (SuiteCreate).
    description: str | None = Field(default=None, max_length=1024)


_LIST_LIMIT_DEFAULT = 200
_LIST_LIMIT_MAX = 200


@router.get("/assets", response_model=list[AssetSummaryRead], summary="List assets")
def list_assets(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=_LIST_LIMIT_DEFAULT, ge=1, le=_LIST_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
) -> list[svc.AssetSummary]:
    # Workspace-true (ADR 0037): identical rows for every member — the service
    # takes no user. Auth still required (the dependency), like every surface.
    return svc.list_visible_assets(db, limit=limit, offset=offset)


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
    _admin: Annotated[User, Depends(require_workspace_admin)],
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
    )
    # Return the refreshed workspace-true summary. Never 404s on an asset with no
    # composing suites — metadata exists independently of suites.
    return svc.summarize_asset(db, asset)
