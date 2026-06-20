"""Celery tasks for asynchronous suite execution.

``run_suite`` is the worker entry point dispatched by the manual-run / probe /
pipeline-trigger paths. It loads the run's suite / connection / checks, resolves
the suite's datasource-shaped **target** (#215) to the runner's
``(table, schema, catalog)``, builds the datasource adapter, and hands off to
``run_service.execute_run``. The DB-touching core is factored into ``_run_suite``
so it can be unit-tested with a fake session + fake runner (no Postgres, no
Snowflake); real-DB integration coverage is a Week 8 item.

The target lives on the suite (``Suite.target``, resolved by
``run_target.resolve_target``): a targetless suite drives the run to ``failed``
with a clear log rather than running against an unknown table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.datasources.flatfile import BatchNotFoundError
from backend.app.datasources.registry import build_check_runner
from backend.app.db.models import (
    ORCHESTRATION_PROVIDERS,
    Check,
    Connection,
    Run,
    Schedule,
    Suite,
)
from backend.app.db.session import get_session
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services import cron, orchestration_service, run_dispatch, run_service, run_target
from backend.app.worker.celery_app import celery_app

# Polling fallback (#171): look back slightly further than the 10-min beat
# interval so a run can't slip through the gap between consecutive polls.
_POLL_LOOKBACK = timedelta(minutes=15)
# Gap recovery (B2): a wider window swept on startup + every 30 min to re-ingest
# runs missed while the system was down (worker/beat restart, webhook + poll both
# offline). Same provider-agnostic pipeline; only the lookback differs. Safe to
# overlap the regular poll — the upsert is idempotent and `skip_updated_since`
# drops runs already recorded inside the window.
_GAP_RECOVERY_LOOKBACK = timedelta(hours=1)

log = get_logger(__name__)


def _terminal_failed(session: Session, run: Run, *, event: str, run_id: uuid.UUID) -> str:
    """Drive ``run`` to terminal ``failed`` (never left ``queued``/``running``)."""
    run.status = "failed"
    run.started_at = run.started_at or datetime.now(UTC)
    run.finished_at = datetime.now(UTC)
    session.commit()
    log.exception(event, run_id=str(run_id))
    return "failed"


def _run_suite(session: Session, *, run_id: uuid.UUID) -> str:
    """Load the run's graph, resolve its target, build the runner, execute.

    The suite's datasource-shaped ``target`` (#215) resolves to the runner's
    ``(table, schema, catalog)`` via ``run_target.resolve_target``; dispatch by
    ``connection.type`` through the runner registry gives a Snowflake / Unity
    Catalog / flat-file suite its correct `CheckRunner` (#146). A flat-file *batch*
    target is then materialized to a concrete path by listing the store
    (`materialize_path`).

    Failures while loading, resolving the target (targetless or malformed suite),
    or building the runner (missing rows, bad connection config, unresolved
    secret) drive the run to ``failed`` so it never lingers in ``queued``;
    execution failures are handled inside ``execute_run``. A genuinely-absent
    batch (`BatchNotFoundError`) is **not** a failure — the data hasn't landed, so
    every check is ``skip``ped (#122) and the run succeeds.
    """
    run = session.get(Run, run_id)
    if run is None:
        log.error("run_suite_run_not_found", run_id=str(run_id))
        return "not_found"

    # Cooperative cancellation: a cancel that landed while the run was queued (or
    # in the dispatch→pickup window) already set 'cancelled' — don't execute it.
    # (revoke also drops a still-queued task; this is the belt-and-braces check.)
    if run.status == "cancelled":
        log.info("run_suite_already_cancelled", run_id=str(run_id))
        return "cancelled"

    try:
        suite = session.get(Suite, run.suite_id)
        connection = session.get(Connection, suite.connection_id) if suite is not None else None
        if suite is None or connection is None:
            raise RuntimeError("suite or connection not found for run")
        target = run_target.resolve_target(connection.type, suite.target)
        checks = list(session.scalars(select(Check).where(Check.suite_id == suite.id)))
        runner = build_check_runner(
            conn_type=connection.type,
            config=connection.config,
            secret_ref=connection.secret_ref,
            secret_store=get_secret_store(),
            catalog=target.catalog,
        )
    except Exception:
        return _terminal_failed(session, run, event="run_suite_setup_failed", run_id=run_id)

    # Materialize the concrete path (live for a flat-file batch target). Kept
    # separate from setup so a missing batch is a skip, not a setup failure.
    try:
        table = run_target.materialize_path(
            connection.type,
            connection.config,
            target,
            secret_ref=connection.secret_ref,
            secret_store=get_secret_store(),
        )
    except BatchNotFoundError:
        run_service.skip_run(session, run=run, checks=checks, reason="batch_not_found")
        log.info("run_suite_skipped_no_batch", run_id=str(run_id), suite_id=str(suite.id))
        return str(run.status)
    except Exception:
        return _terminal_failed(session, run, event="run_suite_materialize_failed", run_id=run_id)

    run_service.execute_run(
        session, run=run, checks=checks, runner=runner, table=table, schema=target.schema
    )
    return str(run.status)


@celery_app.task(name="run_suite")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def run_suite(run_id: str) -> str:
    """Worker entry point. ``run_id`` is a string so it serialises over JSON.

    The target is resolved from the suite (``Suite.target``), so the only
    argument the dispatcher supplies is the run id.
    """
    session = get_session()
    try:
        return _run_suite(session, run_id=uuid.UUID(run_id))
    finally:
        session.close()


def _poll_orchestration_runs(
    session: Session,
    *,
    secret_store: SecretStore,
    now: datetime | None = None,
    lookback: timedelta = _POLL_LOOKBACK,
) -> dict[str, int]:
    """Poll every orchestrator connection for recent succeeded runs (#171, ADR 0004).

    The polling fallback for runs that never produced a webhook: iterate each
    ADF / Airflow connection, ask the provider's `list_recent_runs` for runs
    updated within the ``lookback`` window, and hand them to `ingest_polled_runs`
    (upsert + trigger-on-success). Goes through the `OrchestrationProvider` seam —
    no per-provider branching. Each connection is isolated: a transport/auth
    failure logs + continues so one bad connection can't starve the rest.

    ``lookback`` widens for gap recovery (B2): the same sweep over a 1-hour window
    re-ingests runs missed during downtime. ``skip_updated_since`` rides the same
    window, so a run we already recorded inside it is skipped while a genuinely
    missed one (no row) is upserted.
    """
    since = (now or datetime.now(UTC)) - lookback
    summary = {"connections": 0, "recorded": 0, "triggered": 0, "skipped": 0, "errors": 0}
    connections = list(
        session.scalars(
            select(Connection).where(
                Connection.type.in_(ORCHESTRATION_PROVIDERS),
                Connection.secret_ref.isnot(None),
            )
        )
    )
    for connection in connections:
        if not connection.secret_ref:  # defensive; the query already filters
            continue
        try:
            provider_impl = get_orchestration_provider(connection.type)
            secret = secret_store.get(connection.secret_ref)
            updates = provider_impl.list_recent_runs(dict(connection.config), secret, since)
            result = orchestration_service.ingest_polled_runs(
                session,
                provider_impl=provider_impl,
                connection=connection,
                updates=updates,
                skip_updated_since=since,
            )
            summary["connections"] += 1
            summary["recorded"] += len(result.pipeline_runs)
            summary["triggered"] += len(result.triggered_runs)
            summary["skipped"] += result.skipped
        except Exception:
            summary["errors"] += 1
            session.rollback()
            log.exception(
                "orchestration_poll_failed",
                connection_id=str(connection.id),
                provider=connection.type,
            )
    log.info("orchestration_poll_completed", **summary)
    return summary


def _run_orchestration_poll(lookback: timedelta) -> dict[str, int]:
    """Open a session, run the poll core over ``lookback``, always close.

    Shared by both beat entry points (regular poll + gap recovery) so the session
    lifecycle lives in one place — the only thing that varies is the window.
    """
    session = get_session()
    try:
        return _poll_orchestration_runs(session, secret_store=get_secret_store(), lookback=lookback)
    finally:
        session.close()


@celery_app.task(name="poll_orchestration_runs")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def poll_orchestration_runs() -> dict[str, int]:
    """Celery-beat entry point — the 10-min orchestration polling fallback."""
    return _run_orchestration_poll(_POLL_LOOKBACK)


@celery_app.task(name="recover_orchestration_gaps")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def recover_orchestration_gaps() -> dict[str, int]:
    """Celery-beat entry point — gap recovery (B2), startup + every 30 min.

    The same poll pipeline over the wider ``_GAP_RECOVERY_LOOKBACK`` window, to
    re-ingest runs missed while the system was down. Idempotent with the regular
    poll (upsert + `skip_updated_since`).
    """
    return _run_orchestration_poll(_GAP_RECOVERY_LOOKBACK)


# ──────────────────────── scheduled run dispatch (A7) ──────────────────────


def _advance_schedule(schedule: Schedule, *, now: datetime) -> bool:
    """Roll ``schedule`` forward to its next future fire and stamp ``last_run_at``.

    **No-backfill semantics**: ``cron.next_fire`` returns the next occurrence
    strictly after ``now``, so a gap (worker/beat down across several slots) is
    collapsed to a single fire rather than backfilled. Returns True if advanced;
    False (and disables the schedule) if the stored cron/tz is somehow invalid —
    validated on write, so this only guards against direct DB tampering and stops
    an un-advanceable row from hot-looping the dispatcher every tick.
    """
    schedule.last_run_at = now
    try:
        schedule.next_run_at = cron.next_fire(schedule.cron, schedule.timezone, after=now)
    except DataQError:
        schedule.enabled = False
        log.error(
            "schedule_disabled_invalid_cron",
            schedule_id=str(schedule.id),
            cron=schedule.cron,
            timezone=schedule.timezone,
        )
        return False
    return True


def _fire_schedule(session: Session, schedule: Schedule, *, now: datetime) -> str:
    """Fire one due schedule: advance it, then queue + dispatch a suite run.

    Advancing ``next_run_at`` happens **before** the run is created and is
    committed in every branch, so the schedule leaves the due window for this
    tick whatever the run's fate — a misconfigured suite never hot-loops. The run
    is created with the canonical ``schedule:<id>`` ``triggered_by`` marker and
    handed to the worker exactly like the manual / pipeline-trigger paths; a
    targetless suite is skipped (not queued-then-failed), and a broker outage
    marks the run ``failed`` rather than leaving it stuck ``queued`` (#227).
    """
    if not _advance_schedule(schedule, now=now):
        session.commit()
        return "disabled"

    suite = session.get(Suite, schedule.suite_id)
    assert suite is not None  # schedule cascade-deletes with its suite
    connection = session.get(Connection, suite.connection_id)
    assert connection is not None  # suite.connection_id FK is RESTRICT
    try:
        run_target.resolve_target(connection.type, suite.target)
    except DataQError:
        session.commit()  # persist the advance; skip the doomed run
        log.warning(
            "schedule_skipped_invalid_target",
            schedule_id=str(schedule.id),
            suite_id=str(suite.id),
        )
        return "skipped_target"

    run = Run(suite_id=suite.id, status="queued", triggered_by=f"schedule:{schedule.id}")
    session.add(run)
    session.commit()
    session.refresh(run)
    try:
        run.celery_task_id = run_dispatch.dispatch_run(run.id)
        session.commit()
    except Exception:
        run_dispatch.mark_dispatch_failed(run)
        session.commit()
        log.exception("schedule_dispatch_failed", schedule_id=str(schedule.id), run_id=str(run.id))
        return "dispatch_failed"
    log.info("schedule_fired", schedule_id=str(schedule.id), run_id=str(run.id))
    return "dispatched"


def _dispatch_due_schedules(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Fire every enabled schedule whose ``next_run_at`` has passed (A7).

    Pulls due schedules one at a time with ``FOR UPDATE SKIP LOCKED`` so two
    overlapping dispatcher ticks can't double-fire the same schedule: the second
    skips a row the first holds, and once fired the row's ``next_run_at`` is past
    ``now`` so it drops out of the due set. ``now`` is fixed at entry, so the loop
    is finite (each iteration advances one row out of the window).
    """
    now = now or datetime.now(UTC)
    summary = {"due": 0, "dispatched": 0, "skipped_target": 0, "dispatch_failed": 0, "disabled": 0}
    while True:
        schedule = session.scalars(
            select(Schedule)
            .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
            .order_by(Schedule.next_run_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if schedule is None:
            break
        summary["due"] += 1
        outcome = _fire_schedule(session, schedule, now=now)
        summary[outcome] = summary.get(outcome, 0) + 1
    log.info("schedules_dispatch_completed", **summary)
    return summary


@celery_app.task(name="dispatch_due_schedules")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def dispatch_due_schedules() -> dict[str, int]:
    """Celery-beat entry point — fire due suite-run schedules (A7), every minute."""
    session = get_session()
    try:
        return _dispatch_due_schedules(session)
    finally:
        session.close()
