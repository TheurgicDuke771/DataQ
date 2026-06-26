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

from backend.app.alerting import registry
from backend.app.alerting.builder import build_run_report
from backend.app.core.logging import get_logger
from backend.app.db.models import Run

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
        report = build_run_report(session, run)
        registry.get_result_publisher().publish(report)
        return True
    except Exception:
        log.exception("result_publish_failed", run_id=str(run_id))
        return False
