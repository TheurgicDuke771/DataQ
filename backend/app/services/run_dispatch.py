"""Dispatch a persisted `Run` to the Celery worker — the one place that publishes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Run, Suite
from backend.app.worker.celery_app import celery_app

log = get_logger(__name__)

_RUN_SUITE_TASK = "run_suite"
_AUTO_CLASSIFY_TASK = "auto_classify_columns"
_LLM_INVOKE_TASK = "llm_invoke"


def new_queued_run(suite: Suite, *, triggered_by: str) -> Run:
    """A fresh ``queued`` `Run` for ``suite``, asset stamped at dispatch (ADR 0034)."""
    return Run(
        suite_id=suite.id,
        asset_id=suite.asset_id,
        status="queued",
        triggered_by=triggered_by,
    )


def dispatch_auto_classify(suite_id: uuid.UUID) -> None:
    """Fire-and-forget the auto-classify task for a suite that gained a target (#634)."""
    try:
        celery_app.send_task(_AUTO_CLASSIFY_TASK, args=[str(suite_id)])
    except Exception:
        log.warning("auto_classify_dispatch_failed", suite_id=str(suite_id), exc_info=True)


def dispatch_llm_invocation(invocation_id: uuid.UUID) -> None:
    """Publish the ``llm_invoke`` task (ADR 0042). Raises on broker failure — the
    caller owns marking the invocation row failed (nothing here can commit it).
    """
    celery_app.send_task(_LLM_INVOKE_TASK, args=[str(invocation_id)])


def dispatch_run(run_id: uuid.UUID) -> str:
    """Publish the ``run_suite`` task for ``run_id`` and return its Celery task id."""
    result = celery_app.send_task(_RUN_SUITE_TASK, args=[str(run_id)])
    return str(result.id)


#: Fixed, secret-free `failure_reason` strings (#605) for the non-runner failure paths — a
#: broker/dispatch failure vs the stuck-run reaper.
DISPATCH_FAILED_REASON = (
    "The run could not be dispatched to the worker — the task broker was unreachable."
)
REAPED_REASON = (
    "The run did not complete in time and was marked failed — the worker may have "
    "stopped mid-execution."
)
#: The worker process executing this run died outright (SIGKILL) — overwhelmingly the OOM killer on
#: a large materialisation (#755).
WORKER_LOST_REASON = (
    "The worker process running this suite was terminated before it finished — "
    "most often because the dataset did not fit in worker memory. Try a smaller "
    "batch or a narrower run target."
)


def mark_dispatch_failed(
    run: Run, *, at: datetime | None = None, reason: str = DISPATCH_FAILED_REASON
) -> None:
    """The canonical terminal-failed shape for a broker/dispatch failure."""
    run.status = "failed"
    run.finished_at = at or datetime.now(UTC)
    run.failure_reason = reason


def dispatch_or_fail(session: Session, run: Run, **log_context: str) -> bool:
    """Dispatch a committed queued ``run``; on broker failure record the canonical
    terminal-failed shape. Returns ``True`` if dispatched, ``False`` if the broker
    was unreachable (the run is now ``failed`` with ``finished_at`` set, committed).
    """
    try:
        run.celery_task_id = dispatch_run(run.id)
        session.commit()
        return True
    except Exception:
        log.exception("run_dispatch_failed", run_id=str(run.id), **log_context)
        mark_dispatch_failed(run)
        session.commit()
        return False


def revoke_run(task_id: str | None) -> None:
    """Best-effort revoke of a dispatched run's Celery task."""
    if not task_id:
        return
    try:
        celery_app.control.revoke(task_id)
    except Exception:
        log.warning("run_revoke_failed", celery_task_id=task_id)
