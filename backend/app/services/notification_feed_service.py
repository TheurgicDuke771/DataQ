"""The in-app notification feed (#1687): who receives a run's alert, how the entry is
written, and each user's own read state over it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from backend.app.alerting.base import FAILING_TIERS, RunReport
from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.db.models import ADMIN_ROLE, Incident, Notification, Share, Suite, User

#: Failing check names listed in a body before "… and N more".
_BODY_CHECK_NAMES = 5
_TITLE_MAX = 256


class NotificationNotFoundError(DataQError):
    """The entry does not exist *or* belongs to another user — indistinguishable by
    design (404-no-leak): a feed is private to its owner.
    """

    status_code = 404
    code = "notification_not_found"


def recipients_for_suite(session: Session, suite: Suite) -> set[uuid.UUID]:
    """Every user who can see ``suite`` right now: its creator, each grantee (any level —
    a Viewer's clamp still leaves `view`), the stored-role workspace admins and the
    allowlisted ones (ADR 0033 — an allowlist admin may hold a `member` row).
    """
    emails = get_settings().workspace_admin_email_set
    admin_filter = [User.role == ADMIN_ROLE]
    if emails:
        admin_filter.append(func.lower(User.email).in_(sorted(emails)))
    recipients = set(session.scalars(select(User.id).where(or_(*admin_filter))))
    recipients.update(session.scalars(select(Share.user_id).where(Share.suite_id == suite.id)))
    if suite.created_by is not None:
        recipients.add(suite.created_by)
    return recipients


@dataclass(frozen=True)
class FeedEntry:
    """What every recipient's row says about one run — identical across users."""

    severity: str
    title: str
    body: str
    deep_link: str
    incident_id: uuid.UUID | None


def _severity(report: RunReport) -> str:
    if report.run_status == "failed":
        return "error"
    return report.worst_severity or "pass"


def compose_entry(report: RunReport) -> FeedEntry:
    """The feed text for a run, from the report the other channels already send — check
    names and counts only, so nothing an observed value or sample row would carry can
    reach the feed (the #1772 rule holds at the build step, not by later filtering).
    """
    severity = _severity(report)
    if severity == "error":
        title = f"{report.suite_name}: run failed to execute"
        body = f"The run on {report.target_label} did not complete. Open it for the reason."
    elif severity == "pass":
        title = f"{report.suite_name}: all {report.total_checks} checks passed"
        body = f"{report.target_label} — nothing breached."
    else:
        breaching = [c for c in report.checks if c.status in FAILING_TIERS]
        title = (
            f"{report.suite_name}: {len(breaching)} of {report.total_checks} checks "
            f"breached (worst: {severity})"
        )
        names = [f"{c.check_name} ({c.status})" for c in breaching[:_BODY_CHECK_NAMES]]
        more = len(breaching) - len(names)
        listed = ", ".join(names) + (f" and {more} more" if more > 0 else "")
        body = f"{report.target_label}" + (f" ({report.env})" if report.env else "") + f": {listed}"
    incident_id = report.incidents[0].incident_id if report.incidents else None
    return FeedEntry(
        severity=severity,
        title=title[:_TITLE_MAX],
        body=body,
        deep_link=f"/results/{report.run_id}",
        incident_id=incident_id,
    )


def record_run_alert(session: Session, report: RunReport) -> int:
    """Write ``report`` into every recipient's feed; return how many rows were inserted.
    Idempotent per (user, run): a re-published outcome adds nothing. Returns 0 when the
    suite is gone or nobody can see it.
    """
    suite = session.get(Suite, report.suite_id)
    if suite is None:
        return 0
    recipients = recipients_for_suite(session, suite)
    if not recipients:
        return 0
    entry = compose_entry(report)
    # The incident may have been deleted between the report build and this write; a dangling
    # FK would fail every recipient's row, so check it exists once.
    incident_id = entry.incident_id
    if incident_id is not None and session.get(Incident, incident_id) is None:
        incident_id = None
    rows = [
        {
            "user_id": user_id,
            "suite_id": report.suite_id,
            "run_id": report.run_id,
            "incident_id": incident_id,
            "severity": entry.severity,
            "title": entry.title,
            "body": entry.body,
            "deep_link": entry.deep_link,
        }
        for user_id in sorted(recipients)
    ]
    stmt = (
        pg_insert(Notification)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["user_id", "run_id"], index_where=Notification.run_id.is_not(None)
        )
    )
    result = cast(CursorResult[Any], session.execute(stmt))
    return max(result.rowcount or 0, 0)


# ── the owner's view ──────────────────────────────────────────────────────────


def _own(user_id: uuid.UUID, *, unread_only: bool) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [Notification.user_id == user_id]
    if unread_only:
        clauses.append(Notification.read_at.is_(None))
    return clauses


def list_feed(
    session: Session, *, user_id: uuid.UUID, unread_only: bool, limit: int, offset: int
) -> list[tuple[Notification, uuid.UUID | None]]:
    """Newest-first page of the caller's own feed, each row paired with its incident's
    asset id (the incident deep link needs it) or ``None``.
    """
    stmt = (
        select(Notification, Incident.asset_id)
        .outerjoin(Incident, Incident.id == Notification.incident_id)
        .where(*_own(user_id, unread_only=unread_only))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def count_feed(session: Session, *, user_id: uuid.UUID, unread_only: bool) -> int:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(*_own(user_id, unread_only=unread_only))
    )
    return int(session.execute(stmt).scalar_one())


def unread_count(session: Session, *, user_id: uuid.UUID) -> int:
    return count_feed(session, user_id=user_id, unread_only=True)


def mark_read(
    session: Session, *, user_id: uuid.UUID, notification_id: uuid.UUID
) -> tuple[Notification, uuid.UUID | None]:
    """Mark one of the caller's own entries read (idempotent — a second call keeps the
    first `read_at`). Another user's entry is a 404, never a 403.
    """
    row = session.execute(
        select(Notification, Incident.asset_id)
        .outerjoin(Incident, Incident.id == Notification.incident_id)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
    ).first()
    if row is None:
        raise NotificationNotFoundError(
            "notification not found", detail={"notification_id": str(notification_id)}
        )
    notification, asset_id = row[0], row[1]
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        session.commit()
    return notification, asset_id


def mark_all_read(session: Session, *, user_id: uuid.UUID) -> int:
    """Mark every unread entry of the caller's own feed read; return how many changed."""
    result = cast(
        CursorResult[Any],
        session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=datetime.now(UTC))
        ),
    )
    session.commit()
    return max(result.rowcount or 0, 0)


__all__ = [
    "FeedEntry",
    "NotificationNotFoundError",
    "compose_entry",
    "count_feed",
    "list_feed",
    "mark_all_read",
    "mark_read",
    "recipients_for_suite",
    "record_run_alert",
    "unread_count",
]
