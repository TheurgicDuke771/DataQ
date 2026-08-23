"""The workspace-admin read surface and retention sweep for `audit_events`
— ADR [0041](../../../docs/site/adr/0041-history-audit-strategy.md) phase 1 (#1318).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.chunked_dml import CHUNK_SIZE
from backend.app.db.models import AuditEvent
from backend.app.services import audit_service

log = get_logger(__name__)


class AuditFilterInvalidError(DataQError):
    """A filter names a value that cannot exist — a 422, never an empty page."""

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="audit_filter_invalid", message=message, status_code=422, detail=detail
        )


#: Hard ceiling on a single page.
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class AuditPage:
    """One page of events plus the honesty fields a reader needs to interpret it."""

    events: Sequence[AuditEvent]
    total: int
    truncated: bool
    #: The configured retention window, and the timestamp before which events have been swept.
    retention_days: int
    retained_since: datetime | None


def list_events(
    session: Session,
    *,
    action_class: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    retention_days: int | None = None,
) -> AuditPage:
    """Query the audit log, newest first. **Workspace-admin only — gated at the
    API, not here**, matching how every other admin read in this codebase is
    arranged (`admin_service`).
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)

    # An unvalidated filter that matches nothing returns `total: 0`, and on an audit log that reads
    # as "nothing happened" rather than "you asked for a thing that does not exist".
    if entity_type is not None and entity_type not in audit_service.declared_entity_types():
        raise AuditFilterInvalidError(
            f"unknown entity_type {entity_type!r}",
            detail={
                "entity_type": entity_type,
                "known": list(audit_service.declared_entity_types()),
            },
        )

    filters = []
    if action_class is not None:
        filters.append(AuditEvent.action_class == action_class)
    if entity_type is not None:
        filters.append(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        filters.append(AuditEvent.entity_id == entity_id)
    if actor_user_id is not None:
        filters.append(AuditEvent.actor_user_id == actor_user_id)
    if action is not None:
        filters.append(AuditEvent.action == action)
    if since is not None:
        filters.append(AuditEvent.occurred_at >= since)
    if until is not None:
        filters.append(AuditEvent.occurred_at < until)

    total = int(session.scalar(select(func.count()).select_from(AuditEvent).where(*filters)) or 0)
    events = list(
        session.scalars(
            select(AuditEvent)
            .where(*filters)
            .options(selectinload(AuditEvent.actor))
            # `id` breaks the tie: `occurred_at` has a `now()` default, so events written in the
            # same transaction share a timestamp EXACTLY (Postgres freezes `now()` per transaction).
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    retention = get_settings().audit_retention_days if retention_days is None else retention_days
    return AuditPage(
        events=events,
        total=total,
        truncated=offset + len(events) < total,
        retention_days=retention,
        # None when the sweep is disabled — "nothing has been swept" is a different statement from
        # "swept back to the beginning of time".
        retained_since=(datetime.now(UTC) - timedelta(days=retention) if retention > 0 else None),
    )


def _set_delete_privilege(session: Session, *, granted: bool) -> None:
    """Grant or revoke `DELETE ON audit_events` for the connected role."""
    verb = "GRANT" if granted else "REVOKE"
    preposition = "TO" if granted else "FROM"
    session.execute(text(f"{verb} DELETE ON audit_events {preposition} CURRENT_USER"))


def purge_expired_events(session: Session, *, retention_days: int) -> int:
    """Delete `audit_events` older than `retention_days`. Returns the count."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    total = 0
    while True:
        try:
            _set_delete_privilege(session, granted=True)
            result = session.execute(
                delete(AuditEvent).where(
                    AuditEvent.occurred_at < cutoff,
                    AuditEvent.id.in_(
                        select(AuditEvent.id)
                        .where(AuditEvent.occurred_at < cutoff)
                        .order_by(AuditEvent.occurred_at)
                        .limit(CHUNK_SIZE)
                        .scalar_subquery()
                    ),
                )
            )
            # `max(..., 0)`, not a bare `or 0`: some DB-API drivers return -1 for "unknown
            # rowcount", which is truthy.
            affected = max(cast(CursorResult[Any], result).rowcount or 0, 0)
            _set_delete_privilege(session, granted=False)
            session.commit()
        except Exception:
            # Rollback FIRST.
            session.rollback()
            raise
        total += affected
        # Exit on zero rather than `affected < CHUNK_SIZE`: a chunk size that evenly divides the
        # backlog never produces a partial batch.
        if affected == 0:
            break

    if total:
        log.info("audit_events_purged", deleted=total, retention_days=retention_days)
    return total


def as_dict(event: AuditEvent) -> dict[str, Any]:
    """Serialize one event for the read API."""
    return {
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "action_class": event.action_class,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id) if event.entity_id else None,
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "actor_kind": event.actor_kind,
        "actor_label": event.actor_label,
        "actor_display": event.actor_display,
        "before": event.before,
        "after": event.after,
        "request_id": event.request_id,
    }
