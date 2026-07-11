"""Incident read + lifecycle API (ADR 0034 decision 4, gap G-d phase 3, #761).

Incidents are the stateful, deduped, evidence-carrying roll-up of the per-result
alert signal. This surface lists the incidents a caller can see, drills into one
(with its deterministic evidence card), and lets them acknowledge / resolve.

**Authz mirrors the asset-view matrix exactly (ADR 0027 / #760):** visibility is
derived from suite grants — an incident is visible iff the caller can ``view`` its
suite; a workspace-admin sees all; an incident wholly outside the caller's grants
is **404-no-leak** (indistinguishable from a truly unknown id). Acknowledge /
resolve are *operational* actions on the suite, so they require ``edit`` on it
(owner/edit/admin/workspace-admin act; a view-share reads but 403s on mutate) —
the same gate as triggering a run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user, is_workspace_admin
from backend.app.core.errors import DataQError
from backend.app.db.models import INCIDENT_STATUSES, Incident, Suite, User
from backend.app.db.session import get_db
from backend.app.services import incident_service
from backend.app.services.incident_service import IncidentNotFoundError
from backend.app.services.suite_authz import SuiteForbiddenError, effective_permission

router = APIRouter(tags=["incidents"])

# Levels that may act on (ack/resolve) an incident — edit and above, mirroring the
# suite_authz ladder (view reads; edit/admin/owner act).
_ACTING_LEVELS = frozenset({"edit", "admin", "owner"})
_NOTE_MAX_LEN = 2000


# ── response models ───────────────────────────────────────────────────────────


class IncidentRead(ApiModel):
    """List-row / summary view of an incident. ``check_name`` / ``asset_*`` are
    lifted from the snapshotted evidence card (fallbacks when absent) so the list
    renders without a join; ``latest_status`` is the breaching tier of the most
    recent occurrence."""

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
    actors/notes + the reopen link."""

    acknowledged_by: uuid.UUID | None
    resolved_by_user_id: uuid.UUID | None
    prior_incident_id: uuid.UUID | None
    acknowledge_note: str | None
    resolution_note: str | None
    evidence: dict[str, Any] | None


class IncidentActionRequest(ApiModel):
    """Optional note on an ack / resolve. NUL bytes are rejected by ``ApiModel``;
    the length cap keeps a hostile note off the unbounded Text column."""

    model_config = ConfigDict(extra="forbid")

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


# ── authz helper (404-no-leak; edit-gated actions) ────────────────────────────


def _load_visible_incident(
    db: Session, incident_id: uuid.UUID, user: User, *, for_action: bool
) -> Incident:
    """Load an incident the caller may see, or 404-no-leak. When ``for_action`` the
    caller must have ``edit`` on the incident's suite (else 403).

    An unknown id and an id whose suite the caller can't view return the SAME 404
    (existence hidden) — the asset-view no-leak rule, one object over.
    """
    incident = incident_service.get_incident(db, incident_id)
    suite = db.get(Suite, incident.suite_id) if incident is not None else None
    # Workspace-admin sees every suite (implicit admin — effective_permission
    # resolves that); a normal user resolves to their grant or None.
    level = effective_permission(db, suite, user.id) if suite is not None else None
    if incident is None or level is None:
        raise IncidentNotFoundError("incident not found", detail={"incident_id": str(incident_id)})
    if for_action and level not in _ACTING_LEVELS:
        raise SuiteForbiddenError(
            "acknowledging or resolving an incident requires 'edit' on its suite",
            detail={"incident_id": str(incident_id), "have": level, "need": "edit"},
        )
    return incident


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.get("/incidents", response_model=list[IncidentRead], summary="List visible incidents")
def list_incidents(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    asset_id: uuid.UUID | None = None,
    suite_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, description="Filter by lifecycle status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[IncidentRead]:
    if state is not None and state not in INCIDENT_STATUSES:
        # A bogus state is a 422, not a silent empty list (the #570 clean-input rule).
        raise DataQError(
            code="incident_state_invalid",
            message="invalid incident state filter",
            status_code=422,
            detail={"state": state, "allowed": list(INCIDENT_STATUSES)},
        )
    incidents = incident_service.list_incidents(
        db,
        user_id=current_user.id,
        include_all=is_workspace_admin(current_user),
        asset_id=asset_id,
        suite_id=suite_id,
        state=state,
        limit=limit,
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
    incident = _load_visible_incident(db, incident_id, current_user, for_action=False)
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
    incident = _load_visible_incident(db, incident_id, current_user, for_action=True)
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
    incident = _load_visible_incident(db, incident_id, current_user, for_action=True)
    incident = incident_service.resolve_incident(
        db, incident, user_id=current_user.id, note=payload.note
    )
    return _to_detail(incident)
