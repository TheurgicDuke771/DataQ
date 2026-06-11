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

from backend.app.worker.celery_app import celery_app

_RUN_SUITE_TASK = "run_suite"


def dispatch_run(run_id: uuid.UUID) -> None:
    """Publish the ``run_suite`` task for ``run_id``. Raises if the broker is down."""
    celery_app.send_task(_RUN_SUITE_TASK, args=[str(run_id)])
