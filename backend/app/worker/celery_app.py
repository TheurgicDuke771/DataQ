"""Celery application for DataQ's async execution backbone."""

import os
import tempfile
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
#: Single source of truth for the task name — also used by run_dispatch.py's
#: send_task and tasks.py's @celery_app.task registration (#1789 review).
LLM_INVOKE_TASK_NAME = "llm_invoke"
#: llm_invoke's dedicated queue (#1726/#1777) — a worker not told to consume it
#: too (`-Q celery,llm`) silently never processes it; deploy/README.md has the
#: rollout note.
LLM_QUEUE_NAME = "llm"
# Where prerun stashes the ContextVar reset handle for postrun. Deliberately
# avoids the word "token" so Bandit/Ruff (B105/S105) don't flag it as a secret.
_REQUEST_ID_RESET_ATTR = "_dataq_request_id_reset"


def _rediss_safe_url(url: str) -> str:
    """A ``rediss://`` URL celery will accept: default ``ssl_cert_reqs=required``."""
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url)
    if parts.scheme != "rediss" or "ssl_cert_reqs" in parse_qs(parts.query):
        return url
    separator = "&" if parts.query else "?"
    return f"{url}{separator}ssl_cert_reqs=required"


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "dataq",
        broker=_rediss_safe_url(settings.redis_url),
        backend=_rediss_safe_url(settings.redis_url),
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Surface 'started' so read-back can distinguish queued from running.
        task_track_started=True,
        # Fair dispatch-time interleaving, not a concurrency fix: a worker
        # consuming both queues fetches from "llm" and "celery" in round-robin
        # rather than draining "celery" strictly FIFO, so a QUEUED run_suite
        # backlog no longer blocks llm_invoke from being pulled at all. It
        # does NOT add an execution slot — one already-RUNNING long suite can
        # still occupy the worker's only active slot for its full duration
        # under the deployed (unset, so prefork-default) concurrency; #1790
        # tracks verifying/fixing that separately (#1789 review).
        task_routes={LLM_INVOKE_TASK_NAME: {"queue": LLM_QUEUE_NAME}},
        # Recycle a prefork child past this resident size, BETWEEN tasks — stops
        # memory creep (#755) without interrupting a run in flight. 0 disables.
        worker_max_memory_per_child=settings.worker_max_memory_per_child_kb or None,
        # Deliberately NOT acks_late: early ack stops an OOM-killing run from being redelivered to a
        # fresh child (#755 poison pill).
        task_acks_late=False,
        # Beat runs EMBEDDED in the worker (`worker -B`) in dev AND prod; beat state does not
        # survive a restart.
        beat_schedule_filename=os.path.join(tempfile.gettempdir(), "dataq-celerybeat-schedule"),
        beat_schedule={
            "poll-orchestration-runs": {
                "task": "poll_orchestration_runs",
                "schedule": 600.0,  # 10 minutes
            },
            "recover-orchestration-gaps": {
                "task": "recover_orchestration_gaps",
                "schedule": 1800.0,  # 30 minutes
            },
            # Scheduled suite runs (A7): minute granularity matches cron's finest
            # standard resolution; a no-due tick is a cheap indexed scan.
            "dispatch-due-schedules": {
                "task": "dispatch_due_schedules",
                "schedule": 60.0,  # 1 minute
            },
            # Daily PII scrub of `sample_failures` + list-shaped `observed_value`
            # (#1253); keeps the row + `metric_value` (ADR 0012).
            "purge-sample-failures": {
                "task": "purge_sample_failures",
                "schedule": crontab(hour="1", minute="17"),  # daily, 01:17 UTC
            },
            # Stuck-run reaper (#309): the 60-min default threshold far exceeds
            # the cadence, so the interval bounds latency, not false reaps.
            "reap-stuck-runs": {
                "task": "reap_stuck_runs",
                "schedule": 600.0,  # 10 minutes
            },
            # llm_invocations reaper (#1644): mirrors reap-stuck-runs.
            "reap-stuck-llm-invocations": {
                "task": "reap_stuck_llm_invocations",
                "schedule": 300.0,  # 5 minutes — the running threshold is tighter (20 min, #1726)
            },
            # Orphan-asset sweep (#770, ADR 0034): daily low-urgency accretion cleanup.
            "sweep-orphan-assets": {
                "task": "sweep_orphan_assets",
                "schedule": crontab(hour="1", minute="37"),  # daily, 01:37 UTC
            },
            # Orphan-secret sweep (#1059): REPORTING-ONLY unless SECRET_ORPHAN_PURGE
            # is set — what it would delete is a live warehouse credential.
            "sweep-orphan-secrets": {
                "task": "sweep_orphan_secrets",
                "schedule": crontab(hour="1", minute="57"),  # daily, 01:57 UTC
            },
            # Catalog lineage pull (#762, ADR 0034): dark by default — no-ops
            # unless LINEAGE_PROVIDER is set.
            "refresh-lineage-pull": {
                "task": "refresh_lineage_pull",
                "schedule": crontab(hour="2", minute="17"),  # daily, 02:17 UTC
            },
            # Beat liveness heartbeat (#904): proves the beat→broker→worker loop
            # still EXECUTES; the watchdog thread reads its stamp.
            "beat-heartbeat": {
                "task": "beat_heartbeat",
                "schedule": 60.0,  # 1 minute
            },
            # Warehouse-native lineage refresh (#858, ADR 0034): dark by default —
            # no-ops unless WAREHOUSE_LINEAGE_ENABLED.
            "refresh-warehouse-lineage": {
                "task": "refresh_warehouse_lineage",
                "schedule": crontab(hour="2", minute="37"),  # daily, 02:37 UTC
            },
            # Credential-expiry refresh (#838): warn BEFORE a credential dies;
            # daily is enough — an expiry only moves on rotation.
            "refresh-credential-expiry": {
                "task": "refresh_credential_expiry",
                "schedule": crontab(hour="2", minute="57"),  # daily, 02:57 UTC
            },
            # Warehouse inventory sync (#919, ADR 0040): per-connection opt-in, no global gate.
            "sync-asset-inventory": {
                "task": "sync_asset_inventory",
                "schedule": crontab(hour="3", minute="17"),  # daily, 03:17 UTC
            },
            # Audit-log retention (#1318, ADR 0041 §2.7): own clock and setting,
            # decoupled from the PII sweep — their windows point opposite ways.
            "purge-audit-events": {
                "task": "purge_audit_events",
                "schedule": crontab(hour="4", minute="17"),  # daily, 04:17 UTC
            },
            # Audit hash-chain external anchor (ADR 0041 §9 / #1460): dark by
            # default — no-ops unless TAMPER_ANCHOR is set. AFTER the purge above
            # so the same day's checkpoint (if any) is already committed.
            "anchor-audit-chain-head": {
                "task": "anchor_audit_chain_head",
                "schedule": crontab(hour="4", minute="27"),  # daily, 04:27 UTC
            },
            # Audit hash-chain integrity check (#1460): logs loudly on a break;
            # never auto-remediates.
            "verify-audit-chain": {
                "task": "verify_audit_chain",
                "schedule": crontab(hour="4", minute="37"),  # daily, 04:37 UTC
            },
            # OTP-code retention (#1136): the table is PII (plaintext address +
            # sign-in timestamp); hygiene, not a security control.
            "purge-otp-codes": {
                "task": "purge_otp_codes",
                "schedule": crontab(hour="3", minute="37"),  # daily, 03:37 UTC
            },
        },
    )
    app.autodiscover_tasks(["backend.app.worker"])
    return app


celery_app = create_celery_app()


@setup_logging.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _configure_celery_logging(**_kwargs: Any) -> None:
    """Route worker logs through structlog — connecting any receiver to
    ``setup_logging`` stops Celery configuring logging itself, keeping the
    JSON + PII-redacting chain in force.
    """
    configure_logging(service_name="dataq-worker")


@worker_process_init.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _configure_worker_tracing(**_kwargs: Any) -> None:
    """Per-task spans to App Insights; no-op without a connection string."""
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
    """Worker side: restore request_id from the message into the ContextVar."""
    rid = getattr(task.request, REQUEST_ID_HEADER, None) if task is not None else None
    if rid and task is not None:
        token = request_id_var.set(rid)
        setattr(task.request, _REQUEST_ID_RESET_ATTR, token)


@task_postrun.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _clear_request_id(task: Any = None, **_kwargs: Any) -> None:
    """Restore the ContextVar to its pre-task value — only when prerun set it."""
    token = getattr(task.request, _REQUEST_ID_RESET_ATTR, None) if task is not None else None
    if token is not None:
        request_id_var.reset(token)


#: One-off tasks dispatched when beat starts — each covers a window its own
#: schedule would leave unserved after a restart.
_ON_BEAT_START = (
    # Sweeps runs that completed while the system was down (B2).
    "recover_orchestration_gaps",
    # Cold start: a NULL expiry reads as "nothing expires soon" for up to 24h (#1024).
    "refresh_credential_expiry",
)


@beat_init.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _dispatch_startup_tasks(**_kwargs: Any) -> None:
    """Kick the one-off startup tasks when the beat scheduler starts."""
    log = get_logger(__name__)
    for name in _ON_BEAT_START:
        try:
            celery_app.send_task(name)
        except Exception:  # pragma: no cover - defensive; startup must not fail on broker
            log.exception("startup_task_dispatch_failed", task=name)


@worker_ready.connect  # type: ignore[untyped-decorator]  # celery signal .connect is unannotated
def _start_beat_watchdog(**_kwargs: Any) -> None:
    """Arm the beat liveness watchdog once this worker is consuming (#904)."""
    settings = get_settings()
    stale_after = settings.beat_watchdog_stale_after_s
    if stale_after <= 0:
        get_logger(__name__).info("beat_watchdog_disabled")
        return
    try:
        from backend.app.worker.beat_watchdog import build_store, start_watchdog

        # build_store, never bare from_url: its socket timeouts keep the watchdog
        # thread from blocking forever on a half-open connection (#854).
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
    """Close a `run_suite` run whose worker child was killed mid-execution (#755)."""
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
    """Recover the run id from the failed task's request — positional today, but a
    keyword call must not break silently, so both forms are accepted.
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
