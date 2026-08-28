"""Incident read + lifecycle API (ADR 0034 decision 4, gap G-d phase 3, #761)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import (
    TOTAL_COUNT_HEADER,
    ApiModel,
    ApiRequestModel,
    total_count_responses,
)
from backend.app.core.auth import get_current_user
from backend.app.core.errors import DataQError
from backend.app.core.roles import is_workspace_admin
from backend.app.db.models import INCIDENT_STATUSES, Incident, User
from backend.app.db.session import get_db
from backend.app.services import incident_service

router = APIRouter(tags=["incidents"])

_NOTE_MAX_LEN = 2000


# ── response models ───────────────────────────────────────────────────────────


class IncidentRead(ApiModel):
    """List-row / summary view of an incident. ``check_name`` / ``asset_*`` are
    lifted from the snapshotted evidence card (fallbacks when absent) so the list
    renders without a join; ``latest_status`` is the breaching tier of the most
    recent occurrence.
    """

    id: uuid.UUID
    asset_id: uuid.UUID
    check_id: uuid.UUID
    suite_id: uuid.UUID
    status: str
    resolved_by: str | None
    occurrence_count: int
    created_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    check_name: str | None
    asset_namespace: str | None
    asset_name: str | None
    latest_status: str | None


class IncidentDetailRead(IncidentRead):
    """Incident detail — the summary plus the full evidence card + transition
    actors/notes + the reopen link.
    """

    acknowledged_by: uuid.UUID | None
    resolved_by_user_id: uuid.UUID | None
    prior_incident_id: uuid.UUID | None
    acknowledge_note: str | None
    resolution_note: str | None
    evidence: dict[str, Any] | None


class IncidentActionRequest(ApiRequestModel):
    """Optional note on an ack / resolve. NUL bytes are rejected by ``ApiModel``;
    the length cap keeps a hostile note off the unbounded Text column.
    """

    note: str | None = Field(default=None, max_length=_NOTE_MAX_LEN)


# ── serialization ─────────────────────────────────────────────────────────────


def _evidence_get(evidence: dict[str, Any] | None, *path: str) -> Any:
    """Safely walk a snapshotted-evidence path (any missing layer → ``None``)."""
    node: Any = evidence
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _summary_fields(incident: Incident) -> dict[str, Any]:
    ev = incident.evidence
    return {
        "id": incident.id,
        "asset_id": incident.asset_id,
        "check_id": incident.check_id,
        "suite_id": incident.suite_id,
        "status": incident.status,
        "resolved_by": incident.resolved_by,
        "occurrence_count": incident.occurrence_count,
        "created_at": incident.created_at,
        "last_seen_at": incident.last_seen_at,
        "acknowledged_at": incident.acknowledged_at,
        "resolved_at": incident.resolved_at,
        "check_name": _evidence_get(ev, "check", "name"),
        "asset_namespace": _evidence_get(ev, "asset", "namespace"),
        "asset_name": _evidence_get(ev, "asset", "name"),
        "latest_status": _evidence_get(ev, "failing_result", "status"),
    }


def _to_summary(incident: Incident) -> IncidentRead:
    return IncidentRead(**_summary_fields(incident))


def _to_detail(incident: Incident) -> IncidentDetailRead:
    return IncidentDetailRead(
        **_summary_fields(incident),
        acknowledged_by=incident.acknowledged_by,
        resolved_by_user_id=incident.resolved_by_user_id,
        prior_incident_id=incident.prior_incident_id,
        acknowledge_note=incident.acknowledge_note,
        resolution_note=incident.resolution_note,
        evidence=incident.evidence,
    )


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/incidents",
    response_model=list[IncidentRead],
    summary="List visible incidents",
    responses=total_count_responses(
        "Total incidents visible to the caller matching the `asset_id`/`suite_id`/"
        "`state` filters (#1108) — the same accessible-suite-scoped population "
        "this page's limit/offset slice into. #772 added `offset` but shipped only "
        "that half of the /assets paging shape; a page shorter than `limit` "
        "doesn't by itself prove there's no more — compare against this header."
    ),
)
def list_incidents(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    asset_id: uuid.UUID | None = None,
    suite_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, description="Filter by lifecycle status"),
    limit: int = Query(default=100, ge=1, le=500),
    # Pagination offset — the #772 /assets shape; limit alone truncated silently.
    offset: int = Query(default=0, ge=0),
) -> list[IncidentRead]:
    if state is not None and state not in INCIDENT_STATUSES:
        # A bogus state is a 422, not a silent empty list (the #570 clean-input rule).
        raise DataQError(
            code="incident_state_invalid",
            message="invalid incident state filter",
            status_code=422,
            detail={"state": state, "allowed": list(INCIDENT_STATUSES)},
        )
    include_all = is_workspace_admin(current_user)
    response.headers[TOTAL_COUNT_HEADER] = str(
        incident_service.count_incidents(
            db,
            user_id=current_user.id,
            include_all=include_all,
            asset_id=asset_id,
            suite_id=suite_id,
            state=state,
        )
    )
    incidents = incident_service.list_incidents(
        db,
        user_id=current_user.id,
        include_all=include_all,
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
        limit=limit,
        offset=offset,
    )
    return [_to_summary(i) for i in incidents]


@router.get(
    "/incidents/{incident_id}", response_model=IncidentDetailRead, summary="Get an incident"
)
def get_incident(
    incident_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> IncidentDetailRead:
    incident = incident_service.load_visible_incident(
        db, incident_id, user_id=current_user.id, for_action=False
    )
    return _to_detail(incident)


@router.post(
    "/incidents/{incident_id}/ack",
    response_model=IncidentDetailRead,
    summary="Acknowledge an incident (requires edit on its suite)",
)
def acknowledge_incident(
    incident_id: uuid.UUID,
    payload: IncidentActionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> IncidentDetailRead:
    incident = incident_service.load_visible_incident(
        db, incident_id, user_id=current_user.id, for_action=True
    )
    incident = incident_service.acknowledge_incident(
        db, incident, user_id=current_user.id, note=payload.note
    )
    return _to_detail(incident)


@router.post(
    "/incidents/{incident_id}/resolve",
    response_model=IncidentDetailRead,
    summary="Resolve an incident (requires edit on its suite)",
)
def resolve_incident(
    incident_id: uuid.UUID,
    payload: IncidentActionRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> IncidentDetailRead:
    incident = incident_service.load_visible_incident(
        db, incident_id, user_id=current_user.id, for_action=True
    )
    incident = incident_service.resolve_incident(
        db, incident, user_id=current_user.id, note=payload.note
    )
    return _to_detail(incident)
