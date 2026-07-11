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
    beat_init,
    before_task_publish,
    setup_logging,
    task_postrun,
    task_prerun,
    worker_process_init,
)

from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging, get_logger, request_id_var
from backend.app.core.tracing import configure_tracing, instrument_celery

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
        # Celery-beat schedule. The orchestration polling fallback (#171) runs
        # every 10 min as the success channel for runs that produced no webhook;
        # the task looks back further than the interval so nothing slips the gap.
        # Beat runs embedded in the dev worker (`worker -B`); prod uses a separate
        # beat process.
        # Gap recovery (B2) sweeps a wider 1-hour window every 30 min (plus once
        # on beat startup, via the beat_init signal below) to re-ingest runs
        # missed while the system was down — idempotent with the 10-min poll.
        beat_schedule={
            "poll-orchestration-runs": {
                "task": "poll_orchestration_runs",
                "schedule": 600.0,  # 10 minutes
            },
            "recover-orchestration-gaps": {
                "task": "recover_orchestration_gaps",
                "schedule": 1800.0,  # 30 minutes
            },
            # Scheduled suite runs (A7): tick every minute, fire schedules whose
            # precomputed next_run_at has passed. Minute granularity matches the
            # finest standard cron resolution; the task is a cheap indexed scan
            # when nothing is due.
            "dispatch-due-schedules": {
                "task": "dispatch_due_schedules",
                "schedule": 60.0,  # 1 minute
            },
            # Result retention sweep: once a day, scrub `sample_failures` (the only
            # potentially-PII result column) from results past the configured
            # retention window. Keeps the row + `metric_value` so dashboard trends
            # survive (ADR 0012); this is PII minimisation, not a history delete.
            "purge-sample-failures": {
                "task": "purge_sample_failures",
                "schedule": 86400.0,  # 24 hours
            },
            # Stuck-run reaper (#309): every 10 min, fail runs orphaned in a
            # non-terminal state past `stuck_run_threshold_minutes` (a run committed
            # `queued` before `send_task`, or left `running` by a dead worker). The
            # detection threshold (default 60 min) far exceeds the 10-min cadence, so
            # the sweep interval only bounds detection latency, not false reaps.
            "reap-stuck-runs": {
                "task": "reap_stuck_runs",
                "schedule": 600.0,  # 10 minutes
            },
            # Orphan-asset sweep (#770, ADR 0034): once a day (same cadence as the
            # sample-failures retention sweep — this is a low-urgency accretion
            # cleanup, not a liveness janitor), delete `assets` rows whose
            # last_seen is frozen past `asset_orphan_retention_days` AND that no
            # suite/run/lineage_edge still references.
            "sweep-orphan-assets": {
                "task": "sweep_orphan_assets",
                "schedule": 86400.0,  # 24 hours
            },
        },
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
    configure_logging(service_name="dataq-worker")


@worker_process_init.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _configure_worker_tracing(**_kwargs: Any) -> None:
    """Per-task spans to App Insights (A3, consumer side). No-op without a
    connection string.

    Hooked on ``worker_process_init`` (not module import) because the prefork
    pool forks worker processes — the BatchSpanProcessor's export thread and
    the instrumentation must be set up in each child, never inherited across
    the fork. The PRODUCER side (traceparent injection on publish, which links
    task spans to the triggering request) is instrumented in main.py.
    """
    configure_tracing(service_name="dataq-worker")
    instrument_celery()


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


@beat_init.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _recover_gaps_on_beat_start(**_kwargs: Any) -> None:
    """When the beat scheduler starts, kick a one-off gap recovery (B2).

    Tied to ``beat_init`` (one beat process per deployment) rather than worker
    boot, so it fires **once** per restart instead of once per worker — no
    thundering herd of identical sweeps on a multi-worker deploy. Catches runs
    that completed while the system was down; the 30-min beat alone would leave
    that window unswept until its first tick. Enqueued by name (decoupled from
    the task module) to the broker so a ready worker runs it. Best-effort: a
    broker hiccup at startup must not crash beat (the schedule recovers shortly).
    """
    try:
        celery_app.send_task("recover_orchestration_gaps")
    except Exception:  # pragma: no cover - defensive; startup must not fail on broker
        get_logger(__name__).exception("gap_recovery_startup_dispatch_failed")
