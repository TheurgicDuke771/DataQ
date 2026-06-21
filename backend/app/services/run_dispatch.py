"""Dispatch a persisted `Run` to the Celery worker — the one place that publishes.

Publishes ``run_suite`` **by name** via ``celery_app.send_task`` rather than
importing the task object. That decoupling is deliberate: ``worker.tasks``
imports service modules (e.g. ``orchestration_service``), so a service importing
``worker.tasks`` back would be a cyclic import (CodeQL `py/cyclic-import`). By
name there is no import edge service → worker, and the broker resolves the task
on the worker side. The ``before_task_publish`` signal still fires, so the
``request_id`` correlation header is carried exactly as for ``.delay``.

Raises on a broker/publish failure; the caller owns the policy for a stuck run
(the probe endpoint surfaces 503; the pipeline-trigger path marks the run
``failed`` so it isn't left ``queued``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Run
from backend.app.worker.celery_app import celery_app

log = get_logger(__name__)

_RUN_SUITE_TASK = "run_suite"


def dispatch_run(run_id: uuid.UUID) -> str:
    """Publish the ``run_suite`` task for ``run_id`` and return its Celery task id.

    The task id is stored on the `Run` (``celery_task_id``) so a later cancel can
    revoke a still-queued task. Raises if the broker is down — the caller owns the
    policy for the stuck run (`mark_dispatch_failed` + 503 / log).

    No 2-phase commit spans the broker and the DB: if the publish succeeds but the
    caller's follow-up commit of ``celery_task_id`` fails (a rare DB blip in that
    window), the task still runs — the worker just can't be revoked by id and falls
    back to the cooperative ``cancelled``-status check. Self-correcting and benign.
    """
    result = celery_app.send_task(_RUN_SUITE_TASK, args=[str(run_id)])
    return str(result.id)


def mark_dispatch_failed(run: Run) -> None:
    """The canonical terminal-failed shape for a broker/dispatch failure.

    One definition shared by every trigger path (probe, manual run, pipeline
    success) so a never-dispatched run is recorded identically everywhere:
    ``failed`` with ``finished_at`` set and ``started_at`` left as-is (NULL — it
    never started), keeping run-history / duration views consistent (#227).
    """
    run.status = "failed"
    run.finished_at = datetime.now(UTC)


def dispatch_or_fail(session: Session, run: Run, **log_context: str) -> bool:
    """Dispatch a committed queued ``run``; on broker failure record the canonical
    terminal-failed shape. Returns ``True`` if dispatched, ``False`` if the broker
    was unreachable (the run is now ``failed`` with ``finished_at`` set, committed).

    The one copy of the dispatch + broker-failure block every trigger path shares
    (probe, manual run, pipeline-success batch, scheduled run) so a never-dispatched
    run is recorded — and its traceback logged — identically everywhere (#227). The
    caller owns the *policy* for a ``False`` return: the HTTP paths surface 503; the
    batch / scheduled paths skip the run and carry on. ``log_context`` is merged into
    the failure log so a caller can keep its correlation keys (``schedule_id``, the
    triggering ``provider``/``pipeline``) on the one ``run_dispatch_failed`` event.
    Mirrors ``run_suite``'s own no-2-phase-commit contract (see ``dispatch_run``):
    the publish and the ``celery_task_id`` commit aren't atomic, which is benign
    and self-correcting.
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
    """Best-effort revoke of a dispatched run's Celery task.

    Drops the task if it's still **queued** (not yet picked up). Deliberately no
    ``terminate`` — we don't SIGKILL a worker mid-GX (it would take out sibling
    tasks); an already-running task is stopped **cooperatively** (the worker
    checks for a ``cancelled`` run status). A no-op for an un-dispatched run
    (``task_id is None``); broker errors are swallowed (the DB status is already
    ``cancelled`` and the worker's cooperative check still applies).
    """
    if not task_id:
        return
    try:
        celery_app.control.revoke(task_id)
    except Exception:
        log.warning("run_revoke_failed", celery_task_id=task_id)
