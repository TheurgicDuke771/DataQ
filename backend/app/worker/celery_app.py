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
    ``task.request`` under the protocol-v2 message format.
    """
    rid = getattr(task.request, REQUEST_ID_HEADER, None) if task is not None else None
    if rid:
        request_id_var.set(rid)


@task_postrun.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _clear_request_id(**_kwargs: Any) -> None:
    """Worker side: clear the ContextVar so the next task starts uncorrelated."""
    request_id_var.set(None)
