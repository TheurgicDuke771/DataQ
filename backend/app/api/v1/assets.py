"""Read-only asset view API (ADR 0034, gap G-d phase 2, #760).

Assets are the browse/reason grain over the suite execution grain. This surface
lists the assets a caller can see and drills into one — its composing suites +
their latest run health + the lineage neighbourhood.

**Authz is derived, never granted (ADR 0027 / ADR 0034 decision 5):** an asset is
visible iff the caller can `view` ≥1 suite targeting it; the aggregation is
filtered to the caller's grants; a workspace-admin sees all; an asset wholly
outside the caller's grants is 404-no-leak. All of that lives in
`asset_view_service` (which reuses the suites/runs visibility subquery), so this
module is a thin HTTP layer.

Asset-metadata mutation (`PATCH`) is **workspace-Admin-only** (ADR 0034 §4) —
gated by `require_workspace_admin`, the same 403 the /admin surface uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ConfigDict
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
    """List-row aggregation for one visible asset. `worst_severity` /
    `checks_*` / `last_run_at` roll up the caller-visible composing suites' latest
    runs; `worst_severity` is null when all passed or nothing has run."""

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


class LineageNodeRead(ApiModel):
    """A lineage neighbour — OpenLineage identity + whether it is monitored. No
    run data (blast-radius browse only; ADR 0034 §2)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    namespace: str
    name: str
    env: str | None
    is_monitored: bool


class AssetDetailRead(ApiModel):
    """Asset detail: the summary + per-suite breakdown + upstream/downstream lineage."""

    model_config = ConfigDict(from_attributes=True)

    summary: AssetSummaryRead
    suites: list[ComposingSuiteRead]
    upstream: list[LineageNodeRead]
    downstream: list[LineageNodeRead]


class AssetMetadataUpdate(ApiModel):
    """Partial metadata update (workspace-Admin-only). Each field is optional; an
    explicit `null` clears it, an omitted field leaves it unchanged — the two are
    distinguished via `model_fields_set` at the route so `owner_user_id: null`
    means "unassign" rather than "leave as is"."""

    owner_user_id: uuid.UUID | None = None
    description: str | None = None


@router.get("/assets", response_model=list[AssetSummaryRead], summary="List visible assets")
def list_assets(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[svc.AssetSummary]:
    # Visibility derived from suite grants — a workspace-admin sees every asset
    # (ADR 0027), everyone else only assets with a suite they can view.
    return svc.list_visible_assets(
        db, user_id=current_user.id, include_all=is_workspace_admin(current_user)
    )


@router.get("/assets/{asset_id}", response_model=AssetDetailRead, summary="Get an asset")
def get_asset(
    asset_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> svc.AssetDetail:
    # Raises AssetNotFoundError (404) for an unknown asset OR one the caller can
    # see no composing suite for — existence hidden (no-leak, ADR 0027).
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
    # Return the refreshed summary (admin sees all → include_all). Never 404s on
    # an asset with no composing suites — metadata exists independently of suites.
    return svc.summarize_asset(db, asset, user_id=_admin.id, include_all=True)
