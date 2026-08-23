"""Schedule CRUD — cron-driven suite run schedules (A7)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Schedule
from backend.app.services import audit_service, cron, suite_service
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)


class ScheduleNotFoundError(DataQError):
    status_code = 404
    code = "schedule_not_found"


def create_schedule(
    session: Session,
    *,
    suite_id: uuid.UUID,
    cron_expr: str,
    user_id: uuid.UUID,
    timezone: str = "UTC",
    enabled: bool = True,
) -> Schedule:
    """Create a schedule. Requires `edit` on the target suite (404/403 otherwise)."""
    # Proves the suite exists (404) and the caller may automate it (403).
    require_permission(session, suite_id, user_id, minimum="edit")
    next_run_at = cron.next_fire(cron_expr, timezone)  # validates cron + tz (422)

    schedule = Schedule(
        suite_id=suite_id,
        cron=cron_expr,
        timezone=timezone,
        enabled=enabled,
        next_run_at=next_run_at,
    )
    schedule.created_by = user_id
    session.add(schedule)
    audit_service.record_entity_change(
        session,
        action="schedule.create",
        entity_type="schedule",
        entity=schedule,
        actor=user_id,
    )
    session.commit()
    session.refresh(schedule)
    log.info(
        "schedule_created",
        schedule_id=str(schedule.id),
        suite_id=str(suite_id),
        cron=cron_expr,
        timezone=timezone,
        enabled=enabled,
    )
    return schedule


def list_schedules(
    session: Session,
    *,
    user_id: uuid.UUID,
    suite_id: uuid.UUID | None = None,
    enabled: bool | None = None,
    include_all: bool = False,
) -> list[Schedule]:
    """Schedules on suites the user can access (owned or shared), newest first — or
    on *every* suite when ``include_all`` (the workspace-admin view, ADR 0027).
    """
    # Reuse the single source of truth for suite visibility (suite_service) — the same owned-OR-
    # shared subquery the suite + run reads use, so the authz rule can't silently diverge here.
    stmt = (
        select(Schedule)
        .where(
            Schedule.suite_id.in_(
                suite_service.accessible_suite_ids(user_id, include_all=include_all)
            )
        )
        .order_by(Schedule.created_at.desc())
    )
    if suite_id is not None:
        stmt = stmt.where(Schedule.suite_id == suite_id)
    if enabled is not None:
        stmt = stmt.where(Schedule.enabled.is_(enabled))
    return list(session.scalars(stmt))


def _get_owned(
    session: Session, schedule_id: uuid.UUID, user_id: uuid.UUID, *, minimum: str
) -> Schedule:
    """Load a schedule and assert the caller's permission on its suite."""
    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        raise ScheduleNotFoundError("schedule not found", detail={"schedule_id": str(schedule_id)})
    require_permission(session, schedule.suite_id, user_id, minimum=minimum)
    return schedule


def get_schedule(session: Session, schedule_id: uuid.UUID, *, user_id: uuid.UUID) -> Schedule:
    return _get_owned(session, schedule_id, user_id, minimum="view")


def update_schedule(
    session: Session,
    schedule_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    cron_expr: str | None = None,
    timezone: str | None = None,
    enabled: bool | None = None,
) -> Schedule:
    """Patch a schedule's cron / timezone / enabled flag. Requires `edit`."""
    schedule = _get_owned(session, schedule_id, user_id, minimum="edit")
    audit_before = audit_service.snapshot("schedule", schedule)

    new_cron = cron_expr if cron_expr is not None else schedule.cron
    new_tz = timezone if timezone is not None else schedule.timezone
    cadence_changed = (cron_expr is not None and cron_expr != schedule.cron) or (
        timezone is not None and timezone != schedule.timezone
    )
    re_enabling = enabled is True and not schedule.enabled

    if cron_expr is not None:
        schedule.cron = new_cron
    if timezone is not None:
        schedule.timezone = new_tz
    if enabled is not None:
        schedule.enabled = enabled
    if cadence_changed or re_enabling:
        # Validates the (possibly new) cron + tz (422) and re-bases the fire time.
        schedule.next_run_at = cron.next_fire(new_cron, new_tz)

    audit_service.record_entity_change(
        session,
        action="schedule.update",
        entity_type="schedule",
        entity=schedule,
        actor=user_id,
        before=audit_before,
        if_changed=True,
    )
    session.commit()
    session.refresh(schedule)
    log.info(
        "schedule_updated",
        schedule_id=str(schedule.id),
        cron=schedule.cron,
        timezone=schedule.timezone,
        enabled=schedule.enabled,
    )
    return schedule


def delete_schedule(session: Session, schedule_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Delete a schedule. Requires `edit` on its suite."""
    schedule = _get_owned(session, schedule_id, user_id, minimum="edit")
    # Deleting is how a schedule gets "paused" by a client with no update verb, and it discards the
    # cron expression needed to restore it.
    audit_before = audit_service.snapshot("schedule", schedule)
    session.delete(schedule)
    audit_service.record_entity_change(
        session,
        action="schedule.delete",
        entity_type="schedule",
        entity=None,
        actor=user_id,
        before=audit_before,
    )
    session.commit()
    log.info("schedule_deleted", schedule_id=str(schedule_id))
