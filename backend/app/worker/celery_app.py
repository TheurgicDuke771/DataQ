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

import uuid
from typing import Any

from billiard.exceptions import Terminated, WorkerLostError
from celery import Celery
from celery.schedules import crontab
from celery.signals import (
    beat_init,
    before_task_publish,
    setup_logging,
    task_failure,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
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
        # Recycle a prefork child once it has grown past this resident size, so a
        # big materialisation cannot ratchet the baseline up run-over-run and drop
        # the effective ceiling for everything after it (#755 measured 956 -> 1188
        # -> 1666 MiB across three runs). The child is replaced BETWEEN tasks, so
        # this never interrupts a run in flight; it only stops memory creep.
        # 0 disables.
        worker_max_memory_per_child=settings.worker_max_memory_per_child_kb or None,
        # Deliberately NOT acks_late. With early ack (the default) a task the broker
        # has already delivered is not redelivered when the child dies — which is
        # exactly what stops an OOM-killing run from being handed straight back to a
        # fresh child and killing it too (#755's poison-pill ask; a local crash loop
        # of 25 restarts). Late ack would trade that for at-least-once delivery we
        # do not want here: a half-executed suite re-run is worse than a failed one,
        # and the run row is already driven to a terminal state by the
        # `task_failure` handler below.
        task_acks_late=False,
        # Celery-beat schedule. The orchestration polling fallback (#171) runs
        # every 10 min as the success channel for runs that produced no webhook;
        # the task looks back further than the interval so nothing slips the gap.
        # Beat runs EMBEDDED in the worker (`worker -B`) in dev AND prod alike
        # (docker-compose.yml + deploy/terraform/azure/containerapps.tf) — there
        # is no separate beat process, and beat state does not survive a restart.
        #
        # That is why every daily task below is a `crontab`, never an interval
        # (#1091): an interval restarts its countdown when beat restarts, and the
        # prod worker restarts more often than daily (ACA revision rolls, scaling,
        # the #904 watchdog), so a 24h interval NEVER fired — prod's warehouse
        # lineage sat 10 days stale with beat's every-minute tasks running fine.
        # A crontab fires at a wall-clock moment, indifferent to restarts. The
        # times are staggered (not one thundering 00:00 batch) and UTC (timezone
        # above). Trade-off, accepted: if beat happens to be DOWN at the moment,
        # that day's tick is skipped rather than made up — restarts are seconds
        # long so the window is tiny, and the staleness surface (#1052) reports
        # a miss instead of leaving it invisible. Sub-hourly liveness intervals
        # are unaffected — a reset countdown of minutes is noise.
        #
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
                "schedule": crontab(hour="1", minute="17"),  # daily, 01:17 UTC
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
                "schedule": crontab(hour="1", minute="37"),  # daily, 01:37 UTC
            },
            # Orphan-SECRET sweep (#1059): once a day, reconcile the secret store
            # against the rows that should own its entries. Daily and low-urgency for
            # the same reason as the asset sweep — and REPORTING-ONLY unless
            # SECRET_ORPHAN_PURGE is set, because the thing it would delete is a live
            # warehouse credential.
            "sweep-orphan-secrets": {
                "task": "sweep_orphan_secrets",
                "schedule": crontab(hour="1", minute="57"),  # daily, 01:57 UTC
            },
            # Catalog lineage pull (#762, ADR 0034): once a day, pull lineage from the
            # configured `LineageProvider` (Marquez) into the `lineage_edges` cache.
            # Dark by default — the task no-ops (zero queries) unless LINEAGE_PROVIDER
            # is set. Daily, not a liveness interval: a cache refresh of external truth
            # whose freshness is deliberately bounded by the catalog's own cadence.
            "refresh-lineage-pull": {
                "task": "refresh_lineage_pull",
                "schedule": crontab(hour="2", minute="17"),  # daily, 02:17 UTC
            },
            # Beat liveness heartbeat (#904): every minute, a task whose only job is
            # to prove the beat→broker→worker loop still EXECUTES. The watchdog
            # thread reads its stamp and exits the process when it goes stale, so a
            # worker that is up but consuming nothing gets restarted instead of
            # silently stopping every scheduled task for hours.
            "beat-heartbeat": {
                "task": "beat_heartbeat",
                "schedule": 60.0,  # 1 minute
            },
            # Warehouse-native lineage refresh (#858, ADR 0034): once a day, pull lineage
            # for every Snowflake / Unity Catalog connection straight from the warehouse's
            # own lineage views into `lineage_edges`. Dark by default — no-ops (zero
            # queries) unless WAREHOUSE_LINEAGE_ENABLED. Same low-urgency daily cadence as
            # the catalog pull: a cache refresh of external truth, freshness bounded by the
            # warehouse view's own latency, not a liveness interval.
            "refresh-warehouse-lineage": {
                "task": "refresh_warehouse_lineage",
                "schedule": crontab(hour="2", minute="37"),  # daily, 02:37 UTC
            },
            # Credential-expiry refresh (#838): once a day, re-read the stated
            # expiry of every stored credential that has one (an Azure SAS prints
            # `se=`) into `connections.credential_expires_at`, so the product can
            # warn BEFORE a credential dies rather than after it breaks something.
            # Daily because an expiry date moves only on a rotation, and the
            # warning window is measured in weeks — a cache refresh, not a
            # liveness interval.
            "refresh-credential-expiry": {
                "task": "refresh_credential_expiry",
                "schedule": crontab(hour="2", minute="57"),  # daily, 02:57 UTC
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


#: One-off tasks dispatched when beat starts, each because its own schedule would
#: otherwise leave a window unserved after a restart.
_ON_BEAT_START = (
    # Catches runs that completed while the system was down; the 30-min beat alone
    # would leave that window unswept until its first tick (B2).
    "recover_orchestration_gaps",
    # Populates `credential_expires_at` for credentials that state one (#1024).
    # Its schedule is DAILY, which is right for a value that only moves on a
    # rotation — but wrong for a cold start: a freshly deployed instance, or any
    # connection whose credential predates #838, shows NULL until the first tick.
    # NULL renders as "nothing expires soon", which is indistinguishable from
    # "we have not looked yet", so the warning surface silently reads as reassuring
    # for up to 24h. Prod had exactly this: every connection NULL after the
    # 2026-07-26 deploy, including SAS-bearing ones whose expiry is right there in
    # the token.
    "refresh_credential_expiry",
)


@beat_init.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _dispatch_startup_tasks(**_kwargs: Any) -> None:
    """Kick the one-off startup tasks when the beat scheduler starts.

    Tied to ``beat_init`` (one beat process per deployment) rather than worker
    boot, so each fires **once** per restart instead of once per worker — no
    thundering herd of identical sweeps on a multi-worker deploy. Enqueued by name
    (decoupled from the task module) to the broker so a ready worker runs them.

    Best-effort and **independently guarded**: a broker hiccup at startup must not
    crash beat, and one task failing to enqueue must not skip the rest (the
    schedule recovers shortly either way).
    """
    log = get_logger(__name__)
    for name in _ON_BEAT_START:
        try:
            celery_app.send_task(name)
        except Exception:  # pragma: no cover - defensive; startup must not fail on broker
            log.exception("startup_task_dispatch_failed", task=name)


@worker_ready.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _start_beat_watchdog(**_kwargs: Any) -> None:
    """Arm the beat liveness watchdog once this worker is consuming (#904).

    Hooked on ``worker_ready`` (the main process, after the pool is up) rather
    than ``worker_process_init`` (which fires in every prefork CHILD — N children
    would mean N watchdogs racing to kill the same process, and a child's exit
    would not restart the container anyway).

    The watchdog reads the heartbeat the ``beat_heartbeat`` task writes on
    execution and exits the process when it goes stale, so the platform restarts
    a worker that is up but consuming nothing (#904/#905). Best-effort: it must
    never prevent a worker from starting — a worker with no watchdog is the
    status quo ante, a worker that won't boot is an outage.
    """
    settings = get_settings()
    stale_after = settings.beat_watchdog_stale_after_s
    if stale_after <= 0:
        get_logger(__name__).info("beat_watchdog_disabled")
        return
    try:
        from backend.app.worker.beat_watchdog import build_store, start_watchdog

        # build_store, never a bare from_url: its socket timeouts are what keep
        # the watchdog thread from blocking forever on a half-open connection
        # (#854's lesson, on the thread that exists to notice hangs).
        start_watchdog(
            build_store(settings.redis_url),
            stale_after_s=float(stale_after),
            interval_s=float(settings.beat_watchdog_interval_s),
        )
    except Exception:  # pragma: no cover - defensive; startup must not fail on this
        get_logger(__name__).exception("beat_watchdog_start_failed")


@task_failure.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _fail_run_on_worker_lost(
    task_id: str | None = None,
    exception: BaseException | None = None,
    sender: Any = None,
    **_kwargs: Any,
) -> None:
    """Close a `run_suite` run whose worker child was killed mid-execution (#755).

    Runs in the **parent** process. When the OOM killer SIGKILLs a prefork child,
    nothing inside the task executes — not its `except`, not its `finally` — so the
    run row would stay `running` until the stuck-run reaper fails it up to
    `stuck_run_threshold_minutes` (default 60) later, with a reason that can only
    say "did not complete in time". Celery, however, *knows* the child was lost and
    raises `WorkerLostError` here, so the run can be failed immediately and the
    reason can name memory rather than leave the user guessing.

    Scoped narrowly on purpose:

    * only `run_suite` — other tasks have no run row to close;
    * only worker-death exceptions (`WorkerLostError` / `Terminated`). An ordinary
      exception inside the task means the task's own handlers already ran and drove
      the run to a terminal state with a properly classified reason; overwriting
      that with the generic memory text would make real failures *less* diagnosable.

    Fail-soft: this is a best-effort closer on an already-failing path, so it must
    never raise out of the signal and mask the original error.
    """
    if getattr(sender, "name", None) != "run_suite":
        return
    if not isinstance(exception, (WorkerLostError, Terminated)):
        return
    run_id = _run_id_from_task_args(sender, task_id)
    if run_id is None:
        return
    try:
        from backend.app.db.session import get_session
        from backend.app.services import run_service

        session = get_session()
        try:
            run_service.fail_run_worker_lost(session, run_id=run_id)
        finally:
            session.close()
    except Exception:  # pragma: no cover - defensive; never mask the original failure
        get_logger(__name__).exception("worker_lost_run_close_failed", task_id=task_id)


def _run_id_from_task_args(sender: Any, task_id: str | None) -> uuid.UUID | None:
    """Recover the run id from the failed task's request, tolerating both call forms.

    `run_suite` takes a single `run_id` string, dispatched positionally today — but
    reading only `args[0]` would break silently the day someone calls it by keyword,
    so both are accepted.
    """
    request = getattr(sender, "request", None)
    raw: Any = None
    args = getattr(request, "args", None) or ()
    if args:
        raw = args[0]
    else:
        kwargs = getattr(request, "kwargs", None) or {}
        raw = kwargs.get("run_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        get_logger(__name__).warning("worker_lost_unparseable_run_id", task_id=task_id)
        return None
