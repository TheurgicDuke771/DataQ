"""Alert suppression — honour per-check snoozes when deciding to alert."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.alerting.base import FAILING_TIERS
from backend.app.db.models import Check, Result, Run


def all_failures_snoozed(session: Session, run: Run, *, now: datetime | None = None) -> bool:
    """True when every failing check on ``run`` is currently snoozed (→ suppress)."""
    # An operational run failure is an *execution* failure, not a data-quality result — it has no
    # per-check result rows to snooze today (run_service rolls partials back).
    if run.status == "failed":
        return False
    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(Result.check_id, Result.status).where(Result.run_id == run.id)
    ).all()
    failing = {check_id for check_id, status in rows if status in FAILING_TIERS}
    if not failing:
        return False
    snoozed = set(
        session.scalars(
            select(Check.id).where(
                Check.id.in_(failing),
                Check.alert_snoozed_until.is_not(None),
                Check.alert_snoozed_until > moment,
            )
        )
    )
    return failing <= snoozed
