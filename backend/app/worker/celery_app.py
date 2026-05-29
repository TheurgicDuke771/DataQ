"""Celery application for DataQ's async execution backbone.

GX suite runs are dispatched from FastAPI and executed here so the request
thread returns immediately (run created as ``queued``, worker drives it to
``running`` → ``succeeded``/``failed``).

The CLAUDE.md observability rule requires ``request_id`` correlation to flow
FastAPI → Celery → GX. We carry it on the task message headers: the caller
injects the active request_id on publish, and the worker restores it into the
same ``request_id_var`` ContextVar the structlog processor chain reads from, so
worker log lines correlate with the request that triggered them.
"""

from typing import Any

from celery import Celery
from celery.signals import (
    before_task_publish,
    setup_logging,
    task_postrun,
    task_prerun,
)

from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging, request_id_var

# Message-header key carrying the originating request_id across the broker.
REQUEST_ID_HEADER = "request_id"
# Attribute on task.request where prerun stashes the ContextVar reset handle
# for postrun. Deliberately avoids the word "token" so it isn't mistaken for a
# secret by Bandit / Ruff (B105 / S105).
_REQUEST_ID_RESET_ATTR = "_dataq_request_id_reset"


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "dataq",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Surface a 'started' state so the run-status read-back can distinguish
        # queued from running without waiting for completion.
        task_track_started=True,
    )
    # Register task modules on worker boot (looks for backend.app.worker.tasks).
    app.autodiscover_tasks(["backend.app.worker"])
    return app


celery_app = create_celery_app()


@setup_logging.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _configure_celery_logging(**_kwargs: Any) -> None:
    """Disable Celery's default logging and route worker logs through structlog.

    Connecting any receiver to ``setup_logging`` tells Celery not to configure
    logging itself, so our JSON + PII-redacting processor chain stays in force
    inside the worker exactly as it is in the API.
    """
    configure_logging()


@before_task_publish.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _inject_request_id(headers: dict[str, Any] | None = None, **_kwargs: Any) -> None:
    """Caller side (FastAPI): stamp the active request_id onto task headers."""
    if headers is None:
        return
    rid = request_id_var.get()
    if rid is not None:
        headers[REQUEST_ID_HEADER] = rid


@task_prerun.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _restore_request_id(task: Any = None, **_kwargs: Any) -> None:
    """Worker side: restore request_id from the message into the ContextVar.

    Custom headers added in ``before_task_publish`` are exposed as attributes on
    ``task.request`` under the protocol-v2 message format. We stash the reset
    token on ``task.request`` so ``task_postrun`` can restore the *prior* value
    rather than blindly clearing — under ``task_always_eager`` these signals run
    in the caller's context, so a blanket reset would drop the request_id for
    the rest of the request handler.
    """
    rid = getattr(task.request, REQUEST_ID_HEADER, None) if task is not None else None
    if rid and task is not None:
        token = request_id_var.set(rid)
        setattr(task.request, _REQUEST_ID_RESET_ATTR, token)


@task_postrun.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _clear_request_id(task: Any = None, **_kwargs: Any) -> None:
    """Worker side: restore the ContextVar to its pre-task value.

    Only resets when ``task_prerun`` actually set it (token present), mirroring
    the ``reset(token)`` pattern used by ``request_id_middleware`` in main.py.
    """
    token = getattr(task.request, _REQUEST_ID_RESET_ATTR, None) if task is not None else None
    if token is not None:
        request_id_var.reset(token)
