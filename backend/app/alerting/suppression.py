"""Alert suppression — honour per-check snoozes when deciding to alert.

A check can be snoozed (``checks.alert_snoozed_until``) to mute its alerts for a
window. This answers the one question the dispatch layer needs: *are all of this
run's failing checks currently snoozed?* If so, the alert is suppressed; if even
one failing check is live, the alert still fires (the operator silenced specific
checks, not the suite).

Operational run failures (no per-check result rows) aren't per-check snoozable,
so they're never suppressed here — they alert subject only to dedup.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.alerting.base import FAILING_TIERS
from backend.app.db.models import Check, Result, Run


def all_failures_snoozed(session: Session, run: Run, *, now: datetime | None = None) -> bool:
    """True when every failing check on ``run`` is currently snoozed (→ suppress).

    Returns ``False`` (don't suppress) when the run failed to execute, has no
    per-check failures (clean), or has at least one live failing check.
    """
    # An operational run failure is an *execution* failure, not a data-quality
    # result — it has no per-check result rows to snooze today (run_service rolls
    # partials back), so the query below would already return False. Guard it
    # explicitly (#387) so a future partial-failure path can never let per-check
    # snoozes silence a genuine execution failure.
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
