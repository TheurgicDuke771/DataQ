"""The run-completion hook: build a run's report and hand it to the publisher.

Called from the worker right after a run reaches a terminal state. It is
**best-effort and never raises** — the run is already persisted, so a broken /
slow notification channel must not fail the task or roll anything back. Only
runs that actually executed (``succeeded``/``failed``) are published; a
``cancelled`` run is user-initiated and not alert-worthy.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.app.alerting import dedup, registry, suppression
from backend.app.alerting.builder import build_run_report
from backend.app.alerting.routing import ALWAYS
from backend.app.core.logging import get_logger
from backend.app.db.models import Run
from backend.app.services import notification_service

log = get_logger(__name__)

# Terminal statuses worth notifying on. `cancelled` is excluded (user-initiated);
# `queued`/`running` are non-terminal so they never reach here.
_PUBLISHABLE_STATUSES = frozenset({"succeeded", "failed"})


def publish_run_outcome(session: Session, *, run_id: uuid.UUID) -> bool:
    """Publish ``run_id``'s outcome through the configured publisher.

    Returns whether a report was dispatched (``False`` when the run is missing,
    not in a publishable state, or a failure was swallowed). Any exception —
    building the report or the publisher itself — is logged and contained.
    """
    try:
        run = session.get(Run, run_id)
        if run is None or run.status not in _PUBLISHABLE_STATUSES:
            return False
        # Suppress when every failing check is snoozed (the operator silenced
        # them); a partial snooze still alerts on the live checks. Snooze is an
        # explicit silence, so it wins even under the 'always' heartbeat policy.
        if suppression.all_failures_snoozed(session, run):
            log.info("alert_suppressed_snoozed", run_id=str(run_id), suite_id=str(run.suite_id))
            return False
        # Dedup before building/publishing: an ongoing, unchanged failure on a
        # scheduled suite shouldn't re-alert every run (a clean run is never a
        # "duplicate", so this is a no-op for the passing path). The 'always'
        # (heartbeat) policy opts out — it wants every run, deduped or not.
        config = notification_service.get_config(session, run.suite_id)
        policy = config.alert_on if (config is not None and config.enabled) else None
        if policy != ALWAYS and dedup.is_duplicate_alert(session, run):
            log.info("alert_deduped", run_id=str(run_id), suite_id=str(run.suite_id))
            return False
        report = build_run_report(session, run)
        registry.get_result_publisher().publish(session, report)
        return True
    except Exception:
        log.exception("result_publish_failed", run_id=str(run_id))
        return False
