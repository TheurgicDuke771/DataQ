"""Celery tasks for asynchronous suite execution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.alerting import dispatch as alert_dispatch
from backend.app.alerting.base import HEALTH_FAILING, HEALTH_RECOVERED, HealthState
from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.core.tamper_anchor import get_tamper_anchor
from backend.app.datasources.flatfile import BatchNotFoundError
from backend.app.datasources.monitors import STATEFUL_MONITOR_KINDS
from backend.app.datasources.registry import build_check_runner, owned_runner
from backend.app.db.models import (
    ORCHESTRATION_PROVIDERS,
    Check,
    Connection,
    Run,
    Schedule,
    Suite,
)
from backend.app.db.session import get_session
from backend.app.lineage import dbt_manifest, warehouse_refresh
from backend.app.lineage import dispatch as lineage_dispatch
from backend.app.lineage import edges as lineage_edges
from backend.app.lineage import pull as lineage_pull
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services import (
    asset_service,
    audit_chain,
    audit_read_service,
    column_tags,
    comparison_run,
    connection_service,
    credential_health,
    cron,
    incident_service,
    llm_kinds,  # noqa: F401 — registers every LLM feature kind in the worker
    llm_service,
    orchestration_service,
    otp_service,
    profile_service,
    run_dispatch,
    run_service,
    run_target,
    secret_sweep_service,
    stateful_monitors,
    suite_service,
    workspace_health_service,
)
from backend.app.services.failure_classifier import classify_failure_reason
from backend.app.services.otp_mailer import OtpMailer
from backend.app.worker import beat_watchdog
from backend.app.worker.celery_app import LLM_INVOKE_TASK_NAME, OTP_SEND_TASK_NAME, celery_app

# Poll lookback exceeds the 10-min beat interval so runs can't slip the gap (#171).
_POLL_LOOKBACK = timedelta(minutes=15)
# Gap recovery (B2): wider window, startup + every 30 min; idempotent with the
# regular poll (upsert + `skip_updated_since`).
_GAP_RECOVERY_LOOKBACK = timedelta(hours=1)

log = get_logger(__name__)


def _terminal_failed(
    session: Session, run: Run, *, event: str, run_id: uuid.UUID, reason: str | None = None
) -> str:
    """Drive ``run`` to terminal ``failed`` — never left ``queued``/``running``.
    ``reason`` is the redaction-safe classified message (#605).
    """
    run.status = "failed"
    run.started_at = run.started_at or datetime.now(UTC)
    run.finished_at = datetime.now(UTC)
    run.failure_reason = reason
    session.commit()
    log.exception(event, run_id=str(run_id))
    return "failed"


def _run_suite(session: Session, *, run_id: uuid.UUID) -> str:
    """Load the run's graph, resolve its target, build the runner, execute."""
    run = session.get(Run, run_id)
    if run is None:
        log.error("run_suite_run_not_found", run_id=str(run_id))
        return "not_found"

    # A cancel during the queue/dispatch window already set 'cancelled' — don't
    # execute it (revoke is best-effort; this is the belt-and-braces check).
    if run.status == "cancelled":
        log.info("run_suite_already_cancelled", run_id=str(run_id))
        return "cancelled"

    suite = session.get(Suite, run.suite_id)
    connection = session.get(Connection, suite.connection_id) if suite is not None else None
    # The ONE datasource credential-health seam for the run path (#1697): everything below
    # — runner build, batch materialisation, column tags, comparison and stateful-monitor
    # executors, and the run itself — uses this connection's stored credential.
    with credential_health.credential_use(session, connection) as credential:
        try:
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
                # Suite row cap (#595); `resolve_target` already refused it on
                # pushdown datasources, so it is never silently dropped here.
                sampling=target.sampling,
            )
        except Exception as exc:
            credential.failed(exc)
            return _terminal_failed(
                session,
                run,
                event="run_suite_setup_failed",
                run_id=run_id,
                reason=classify_failure_reason(exc),
            )

        # Refresh warehouse column classifications (G3/#433) here — the read path must not open a
        # warehouse connection, and this is the moment we're connected.
        try:
            column_tags.refresh_asset_column_tags(
                session,
                suite=suite,
                connection=connection,
                target=target,
                secret_store=get_secret_store(),
            )
        except Exception:  # pragma: no cover - the callee already swallows
            log.warning("column_tags_refresh_skipped", run_id=str(run_id), exc_info=True)

        # Everything below runs inside `owned_runner`, which releases the shared
        # engine pool (#427) on every exit.
        with owned_runner(runner):
            # Kept separate from setup so a missing batch is a skip, not a setup failure.
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
            except Exception as exc:
                credential.failed(exc)
                return _terminal_failed(
                    session,
                    run,
                    event="run_suite_materialize_failed",
                    run_id=run_id,
                    reason=classify_failure_reason(exc),
                )

            # The suite's identifier column (#415) — requested from GX so failing
            # rows carry a locator; absent policy keeps the scalar-only sample.
            policy = suite.column_policy or {}
            identifier = policy.get("identifier_column")
            index_columns = [str(identifier)] if identifier else None

            # Comparison executor (ADR 0015, #794): bound to this run's resolved target
            # so the diff validates the exact dataset the GX runner sees.
            comparison_executor = None
            if comparison_run.has_comparison_checks(checks):
                comparison_executor = comparison_run.build_comparison_executor(
                    session,
                    suite_connection=connection,
                    target_table=table,
                    target_schema=target.schema,
                    target_catalog=target.catalog,
                    secret_store=get_secret_store(),
                )

            # Stateful monitor executors (#592/#593) own the session + baseline
            # store, which runners must never see.
            stateful_monitor_executor = None
            if any(c.kind in STATEFUL_MONITOR_KINDS for c in checks):
                stateful_monitor_executor = stateful_monitors.build_stateful_monitor_executor(
                    session,
                    connection=connection,
                    target_table=table,
                    target_schema=target.schema,
                    target_catalog=target.catalog,
                    secret_store=get_secret_store(),
                )

            run_service.execute_run(
                session,
                run=run,
                checks=checks,
                runner=runner,
                table=table,
                schema=target.schema,
                index_columns=index_columns,
                comparison_executor=comparison_executor,
                stateful_monitor_executor=stateful_monitor_executor,
            )
            return str(run.status)


@celery_app.task(name="run_suite")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def run_suite(run_id: str) -> str:
    """Worker entry point. ``run_id`` is a string so it serialises over JSON."""
    rid = uuid.UUID(run_id)
    session = get_session()
    try:
        # OpenLineage START/terminal brackets the run (ADR 0034, #758) — fail-open, dark by default.
        lineage_dispatch.emit_run_lineage_start(session, run_id=rid)
        try:
            outcome = _run_suite(session, run_id=rid)
        except BaseException:
            lineage_dispatch.emit_run_lineage_terminal(session, run_id=rid)
            raise
        lineage_dispatch.emit_run_lineage_terminal(session, run_id=rid)
        # Incident rollup BEFORE alert dispatch so the report can reference the
        # open incident (#761); fail-soft like the hooks around it.
        incident_service.sync_incidents_for_run(session, run_id=rid)
        alert_dispatch.publish_run_outcome(session, run_id=rid)
        _alert_datasource_health_for_run(session, run_id=rid)
        return outcome
    finally:
        session.close()


def _alert_datasource_health_for_run(session: Session, *, run_id: uuid.UUID) -> None:
    """Drive the health edges for the datasource this run used (#996)."""
    try:
        run = session.get(Run, run_id)
        suite = session.get(Suite, run.suite_id) if run is not None else None
        connection_id = suite.connection_id if suite is not None else None
        if connection_id is None:
            return
        health = connection_service.datasource_health(session, [connection_id]).get(connection_id)
        if health is None:  # no runs in the window — nothing to say either way
            return
        _alert_connection_health(
            session,
            connection_id=connection_id,
            streak=health.consecutive_failures,
            recovered=health.consecutive_failures == 0,
        )
    except Exception:
        session.rollback()
        log.exception("datasource_health_alert_failed", run_id=str(run_id))


def _auto_classify_columns(session: Session, *, suite_id: uuid.UUID) -> str:
    """Best-effort derive + persist of a suite's redaction policy (#634)."""
    suite = session.get(Suite, suite_id)
    if suite is None or suite.target is None or suite.column_policy is not None:
        return "skipped"
    target = suite.target
    table, path = target.get("table"), target.get("path")
    if not table and not path:  # targetless / batch-pattern → nothing to profile
        return "skipped"
    connection = session.get(Connection, suite.connection_id)
    if connection is None:
        return "skipped"

    try:
        policy = profile_service.suggest_policy_for_target(
            connection,
            session=session,
            table=table,
            schema=target.get("schema"),
            catalog=target.get("catalog"),
            namespace=target.get("namespace"),
            path=path,
            file_format=target.get("file_format"),
            secret_store=get_secret_store(),
        )
        if not policy.get("identifier_column") and not policy.get("pii_columns"):
            return "empty"
        # Re-check under FOR UPDATE (#642): a user-set policy or a retargeted suite during the
        # seconds-long introspection must win — the lock closes the check→write race.
        session.refresh(suite, with_for_update=True)
        if suite.column_policy is not None or suite.target != target:
            session.rollback()  # release the lock; don't persist a raced/stale derive
            return "skipped_raced"
        suite_service.set_column_policy(
            session,
            suite_id,
            identifier_column=policy.get("identifier_column"),
            pii_columns=policy.get("pii_columns", []),
            # ADR 0041 §2.1 keeps machine writes out of the audit log so they
            # cannot bury actor-attributable events.
            machine_write=True,
        )
    except Exception:
        session.rollback()
        log.warning("auto_classify_failed", suite_id=str(suite_id), exc_info=True)
        return "error"
    log.info("auto_classify_applied", suite_id=str(suite_id))
    return "classified"


@celery_app.task(name="auto_classify_columns")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def auto_classify_columns(suite_id: str) -> str:
    """Auto-derive a new suite's redaction policy (#634) — fire-and-forget, never
    raises, never clobbers an existing policy.
    """
    session = get_session()
    try:
        return _auto_classify_columns(session, suite_id=uuid.UUID(suite_id))
    finally:
        session.close()


def _alert_connection_health(
    session: Session, *, connection_id: uuid.UUID, streak: int, recovered: bool
) -> None:
    """Decide whether a connection-health edge is due; hand the send to its own task."""
    threshold = get_settings().orchestration_poll_failure_alert_threshold
    if threshold <= 0:  # push disabled; #828's in-app health signals still stand
        return
    try:
        connection = session.get(Connection, connection_id)
        if connection is None:  # deleted between the poll and the alert
            return
        outstanding = connection.health_alerted_at is not None
    except Exception:
        # This read also runs on the SUCCESS path, outside the caller's try — a
        # transient DB error must not abort the sweep (#842).
        session.rollback()
        log.exception("connection_health_alert_decision_failed", connection_id=str(connection_id))
        return

    if recovered:
        # Only if a failing alert was delivered — otherwise nothing to recover FROM.
        if outstanding:
            _dispatch_health_alert(connection_id, HEALTH_RECOVERED)
        return
    if streak >= threshold and not outstanding:
        _dispatch_health_alert(connection_id, HEALTH_FAILING)


def _dispatch_health_alert(connection_id: uuid.UUID, state: str) -> None:
    """Queue the health publish, swallowing a broker failure — an unreachable Redis
    is exactly the incident that makes every connection cross at once.
    """
    try:
        celery_app.send_task("publish_connection_health", args=[str(connection_id), state])
    except Exception:
        log.exception(
            "connection_health_alert_dispatch_failed",
            connection_id=str(connection_id),
            state=state,
        )


@celery_app.task(name="publish_connection_health")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def publish_connection_health(connection_id: str, state: str) -> bool:
    """Publish one poll-health edge and record the delivery (#842/#843)."""
    if state not in (HEALTH_FAILING, HEALTH_RECOVERED):
        # Args cross the broker as plain JSON — establish the literal at the
        # boundary; drop a malformed message loudly.
        log.error("connection_health_alert_bad_state", connection_id=connection_id, state=state)
        return False
    edge: HealthState = HEALTH_FAILING if state == HEALTH_FAILING else HEALTH_RECOVERED
    session = get_session()
    try:
        cid = uuid.UUID(connection_id)
        # CLAIM the edge with one atomic conditional UPDATE: overlapping sweeps (poll, gap recovery,
        # poll-now) can both read NULL and queue.
        claimed_at = datetime.now(UTC)
        previous: datetime | None = None
        if edge == HEALTH_FAILING:
            claim = update(Connection).where(
                Connection.id == cid, Connection.health_alerted_at.is_(None)
            )
            won = session.execute(claim.values(health_alerted_at=claimed_at)).rowcount  # type: ignore[attr-defined]  # UPDATE always yields a CursorResult
        else:
            previous = session.scalar(
                select(Connection.health_alerted_at).where(Connection.id == cid)
            )
            claim = update(Connection).where(
                Connection.id == cid, Connection.health_alerted_at.is_not(None)
            )
            won = session.execute(claim.values(health_alerted_at=None)).rowcount  # type: ignore[attr-defined]  # UPDATE always yields a CursorResult
        session.commit()
        if not won:
            # A racing task already owns this edge, or the connection is gone.
            return False

        if alert_dispatch.publish_connection_health(session, connection_id=cid, state=edge):
            return True

        # Nothing was delivered, so the claim must not stand (#843) — release it
        # for the next sweep's retry.
        session.execute(
            update(Connection)
            .where(Connection.id == cid)
            .values(health_alerted_at=None if edge == HEALTH_FAILING else previous)
        )
        session.commit()
        return False
    except Exception:
        session.rollback()
        log.exception("connection_health_alert_failed", connection_id=connection_id, state=state)
        return False
    finally:
        session.close()


def _poll_orchestration_runs(
    session: Session,
    *,
    secret_store: SecretStore,
    now: datetime | None = None,
    lookback: timedelta = _POLL_LOOKBACK,
    provider: str | None = None,
    resource_name: str | None = None,
) -> dict[str, int]:
    """Poll every orchestrator connection for recent succeeded runs (#171, ADR 0004)."""
    since = (now or datetime.now(UTC)) - lookback
    summary = {"connections": 0, "recorded": 0, "triggered": 0, "skipped": 0, "errors": 0}
    provider_filter = (
        [provider] if provider in ORCHESTRATION_PROVIDERS else list(ORCHESTRATION_PROVIDERS)
    )
    connections = list(
        session.scalars(
            select(Connection).where(
                Connection.type.in_(provider_filter),
                Connection.secret_ref.isnot(None),
            )
        )
    )
    for connection in connections:
        if not connection.secret_ref:  # defensive; the query already filters
            continue
        try:
            provider_impl = get_orchestration_provider(connection.type)
            if resource_name is not None and (
                connection.config.get(provider_impl.resource_config_key) != resource_name
            ):
                continue
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
            recovered_from = orchestration_service.record_poll_success(
                session, connection=connection
            )
        except Exception as exc:
            summary["errors"] += 1
            session.rollback()
            log.exception(
                "orchestration_poll_failed",
                connection_id=str(connection.id),
                provider=connection.type,
            )
            # Record the failure as a fact about the CONNECTION, not just a log line (#828).
            try:
                streak = orchestration_service.record_poll_failure(
                    session, connection_id=connection.id, exc=exc
                )
                _alert_connection_health(
                    session, connection_id=connection.id, streak=streak, recovered=False
                )
            except Exception:
                session.rollback()
                log.exception(
                    "orchestration_poll_health_write_failed",
                    connection_id=str(connection.id),
                )
        else:
            # Deliberately OUTSIDE the try: a raise on the notification path would land in the
            # except and mark a SUCCESSFUL poll as failing, corrupting the streak the alert keys on.
            _alert_connection_health(
                session, connection_id=connection.id, streak=recovered_from, recovered=True
            )
    log.info("orchestration_poll_completed", **summary)
    return summary


def _run_orchestration_poll(
    lookback: timedelta,
    *,
    provider: str | None = None,
    resource_name: str | None = None,
) -> dict[str, int]:
    """Open a session, run the poll core over ``lookback``, always close — shared
    by the beat entry points and the poll-now path (#492).
    """
    session = get_session()
    try:
        return _poll_orchestration_runs(
            session,
            secret_store=get_secret_store(),
            lookback=lookback,
            provider=provider,
            resource_name=resource_name,
        )
    finally:
        session.close()


@celery_app.task(name="poll_orchestration_runs")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def poll_orchestration_runs(
    provider: str | None = None, resource_name: str | None = None
) -> dict[str, int]:
    """The 10-min beat polling fallback; ``provider``/``resource_name`` narrow the
    alert-triggered poll-now sweep (#492).
    """
    return _run_orchestration_poll(_POLL_LOOKBACK, provider=provider, resource_name=resource_name)


@celery_app.task(name="recover_orchestration_gaps")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def recover_orchestration_gaps() -> dict[str, int]:
    """Gap recovery (B2), startup + every 30 min — the same pipeline over the wider
    window, idempotent with the regular poll.
    """
    return _run_orchestration_poll(_GAP_RECOVERY_LOOKBACK)


def _refresh_dbt_lineage(
    session: Session, *, connection_id: uuid.UUID, job: str, secret_store: SecretStore
) -> str:
    """Fetch + parse + refresh the dbt lineage cache for one (connection, job)."""
    connection = session.get(Connection, connection_id)
    if connection is None:
        log.warning("dbt_lineage_refresh_no_connection", connection_id=str(connection_id))
        return "no_connection"
    provider_impl = get_orchestration_provider(connection.type)
    reader = getattr(provider_impl, "read_manifest", None)
    if reader is None:
        log.warning(
            "dbt_lineage_refresh_no_capability",
            connection_id=str(connection_id),
            provider=connection.type,
        )
        return "no_capability"
    if not connection.secret_ref:
        log.warning("dbt_lineage_refresh_no_secret", connection_id=str(connection_id))
        return "no_secret"
    try:
        secret = secret_store.get(connection.secret_ref)
        raw = reader(dict(connection.config), secret, job)
        if raw is None:
            log.info("dbt_lineage_refresh_no_manifest", connection_id=str(connection_id), job=job)
            return "no_manifest"
        graph = dbt_manifest.parse_manifest(raw)
        lineage_edges.refresh_dbt_edges(session, connection=connection, graph=graph)
    except Exception:
        session.rollback()
        log.warning(
            "dbt_lineage_refresh_failed",
            connection_id=str(connection_id),
            job=job,
            exc_info=True,
        )
        return "error"
    log.info("dbt_lineage_refresh_done", connection_id=str(connection_id), job=job)
    return "refreshed"


@celery_app.task(name="refresh_dbt_lineage")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def refresh_dbt_lineage(connection_id: str, job: str) -> str:
    """Async dbt-manifest lineage refresh (ADR 0034, #759) — fire-and-forget off
    the ingest path so artifact IO never blocks the webhook ACK / poll loop;
    never raises.
    """
    session = get_session()
    try:
        return _refresh_dbt_lineage(
            session,
            connection_id=uuid.UUID(connection_id),
            job=job,
            secret_store=get_secret_store(),
        )
    finally:
        session.close()


# ──────────────────────── scheduled run dispatch (A7) ──────────────────────


def _advance_schedule(schedule: Schedule, *, now: datetime) -> bool:
    """Roll ``schedule`` forward to its next future fire; stamp ``last_run_at``."""
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
    """Fire one due schedule: advance it, then queue + dispatch a suite run."""
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

    run = run_dispatch.new_queued_run(suite, triggered_by=f"schedule:{schedule.id}")
    session.add(run)
    session.commit()
    session.refresh(run)
    # Shared dispatch+broker-failure handling (#227); the advance is already committed.
    if not run_dispatch.dispatch_or_fail(session, run, schedule_id=str(schedule.id)):
        return "dispatch_failed"
    log.info("schedule_fired", schedule_id=str(schedule.id), run_id=str(run.id))
    return "dispatched"


def _dispatch_due_schedules(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Fire every enabled schedule whose ``next_run_at`` has passed (A7)."""
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


# ─────────────────────── result retention sweep (PII purge) ─────────────────


@celery_app.task(name="purge_sample_failures")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def purge_sample_failures() -> int:
    """Daily PII-retention sweep — scrubs `sample_failures` + list-shaped
    `observed_value` (#1253) past retention, keeping the row + `metric_value`
    (ADR 0012). Returns column values scrubbed.
    """
    session = get_session()
    try:
        retention_days = get_settings().sample_failures_retention_days
        return run_service.purge_expired_sample_failures(session, retention_days=retention_days)
    finally:
        session.close()


# ─────────────────────── Audit-log retention sweep (#1318) ──────────────────


@celery_app.task(name="purge_audit_events")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def purge_audit_events() -> int:
    """Daily audit-log retention sweep (ADR 0041 §2.7), on its own setting —
    decoupled from the PII sweep (opposite retention pressures).
    """
    session = get_session()
    try:
        return audit_read_service.purge_expired_events(
            session, retention_days=get_settings().audit_retention_days
        )
    finally:
        session.close()


# ───────────────── Audit hash-chain anchor + verify (ADR 0041 §9 / #1460) ────


@celery_app.task(name="anchor_audit_chain_head")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def anchor_audit_chain_head() -> bool:
    """Daily periodic anchor of the chain's current head — dark by default
    (TAMPER_ANCHOR unset), same posture as `refresh_lineage_pull`. Runs even on
    a day with no retention purge, so the anchor is not solely purge-triggered.
    """
    session = get_session()
    try:
        result = audit_chain.verify_chain(session)
        if result.chain_head_hash is None:
            return False  # nothing written yet — nothing to anchor
        return get_tamper_anchor().publish(
            label="audit_chain_daily",
            head_hash=result.chain_head_hash,
            event_count=result.verified_count,
            as_of=datetime.now(UTC),
        )
    finally:
        session.close()


@celery_app.task(name="verify_audit_chain")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def verify_audit_chain() -> bool:
    """Daily chain-integrity check. Logs loudly on a break; does not (and
    cannot) auto-remediate. Returns whether the chain verified clean.
    """
    session = get_session()
    try:
        result = audit_chain.verify_chain(session)
        if result.first_break is not None:
            log.error(
                "audit_chain_broken",
                event_id=str(result.first_break.event_id),
                occurred_at=(
                    result.first_break.occurred_at.isoformat()
                    if result.first_break.occurred_at is not None
                    else None
                ),
                expected_prev_hash=result.first_break.expected_prev_hash,
                actual_prev_hash=result.first_break.actual_prev_hash,
            )
        return result.ok
    finally:
        session.close()


# ─────────────────────── OTP-code retention sweep (#1136) ───────────────────


@celery_app.task(name="purge_otp_codes")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def purge_otp_codes() -> int:
    """Daily OTP-code retention sweep (#1136) — `otp_codes.email` is plaintext PII,
    so an unswept table is an unbounded sign-in log. Returns the count deleted,
    never an address. ``<= 0`` no-ops inside `otp_service` (the shared floor).
    """
    session = get_session()
    try:
        retention_hours = get_settings().otp_codes_retention_hours
        return otp_service.purge_expired_codes(session, older_than_hours=retention_hours)
    finally:
        session.close()


# ─────────────────────── OTP sign-in mail delivery (#1731) ──────────────────


@celery_app.task(name=OTP_SEND_TASK_NAME, ignore_result=True)  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def send_otp_code(*, to: str, code: str, expires_in_minutes: int) -> bool:
    """Deliver one sign-in code over SMTP, off the request path (#1731). Returns
    whether it was sent. NEVER raises: a failure here is an operator signal
    (`otp_send_task_failed` + the mailer's own staged log line), and there is no
    retry — the code has a 10-minute life and the user's re-request supersedes
    it. `ignore_result=True`: nothing about this message belongs in the backend.
    """
    try:
        OtpMailer(get_secret_store(), get_settings()).send_code(
            to=to, code=code, expires_in_minutes=expires_in_minutes
        )
    except Exception as exc:
        # No exc_info: the frames hold the address and the code.
        log.error(
            "otp_send_task_failed",
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
        )
        return False
    return True


# ──────────────────────── stuck-run reaper (#309) ──────────────────────────


@celery_app.task(name="reap_stuck_runs")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def reap_stuck_runs() -> int:
    """Fail runs orphaned non-terminal past ``stuck_run_threshold_minutes`` (#309).
    No alert — see ``run_service.reap_stuck_runs``. Returns the count reaped.
    """
    session = get_session()
    try:
        threshold = get_settings().stuck_run_threshold_minutes
        return len(run_service.reap_stuck_runs(session, threshold_minutes=threshold))
    finally:
        session.close()


# ─────────────────────── llm_invocations reaper (#1644) ────────────────────


@celery_app.task(name="reap_stuck_llm_invocations")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def reap_stuck_llm_invocations() -> int:
    """Fail `llm_invocations` stranded in `pending`/`running` (#1644).
    No alert — see `llm_service.reap_stuck_invocations`. Returns the count reaped.
    """
    session = get_session()
    try:
        settings = get_settings()
        reaped = llm_service.reap_stuck_invocations(
            session,
            pending_threshold_minutes=settings.llm_invocation_pending_threshold_minutes,
            running_threshold_minutes=settings.llm_invocation_running_threshold_minutes,
        )
        return len(reaped)
    finally:
        session.close()


# ──────────────────────── orphan-asset sweep (#770) ──────────────────────────


@celery_app.task(name="sync_asset_inventory")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def sync_asset_inventory() -> int:
    """Daily warehouse inventory sync (#919, ADR 0040) — dark by default at the
    connection grain (``inventory_sync: true``); no warehouse query without opt-in.
    Its `last_seen` advancement also keeps discovered assets out of the orphan sweep.
    """
    from backend.app.services import inventory_service

    session = get_session()
    try:
        return inventory_service.sync_asset_inventory(session, secret_store=get_secret_store())
    finally:
        session.close()


@celery_app.task(name="sweep_orphan_assets")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def sweep_orphan_assets() -> int:
    """Delete unreferenced, stale `assets` rows (#770, ADR 0034). Returns the count."""
    session = get_session()
    try:
        retention_days = get_settings().asset_orphan_retention_days
        return asset_service.sweep_orphan_assets(session, retention_days=retention_days)
    except Exception:
        session.rollback()
        log.warning("orphan_asset_sweep_failed", exc_info=True)
        return 0
    finally:
        session.close()


# ─────────────────────── orphan-secret sweep (#1059) ─────────────────────────


@celery_app.task(name="sweep_orphan_secrets")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def sweep_orphan_secrets() -> int:
    """Reconcile the secret store against its owners (#1059) — reports by default,
    purges only under `SECRET_ORPHAN_PURGE` (what it deletes is a live credential).
    """
    session = get_session()
    try:
        settings = get_settings()
        result = secret_sweep_service.sweep_orphan_secrets(
            session,
            store=get_secret_store(),
            grace_days=settings.secret_orphan_grace_days,
            purge=settings.secret_orphan_purge,
        )
        return len(result.orphans)
    except Exception:
        session.rollback()
        log.warning("orphan_secret_sweep_failed", exc_info=True)
        return 0
    finally:
        session.close()


# ─────────────────────── catalog lineage pull (#762) ─────────────────────────


@celery_app.task(name="refresh_lineage_pull")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def refresh_lineage_pull() -> int:
    """Daily catalog lineage pull into `lineage_edges` (#762, ADR 0034) — dark by
    default (no-op without `LINEAGE_PROVIDER`); a cache refresh of external truth,
    not a liveness interval. Returns the pulled-edge count; fails open per step.
    """
    provider = lineage_pull.get_lineage_provider()
    if provider is None:
        # #1090: UNSET (catalog removed → sweep orphaned cached edges) is distinct from configured-
        # but-broken (keep the cache — a purge would turn a typo into data loss).
        if lineage_pull.lineage_provider_unset():
            session = get_session()
            try:
                lineage_pull.purge_orphaned_pulled_edges(session)
            finally:
                session.close()
        return 0
    session = get_session()
    try:
        return lineage_pull.refresh_pulled_edges(session, provider=provider) or 0
    finally:
        session.close()


@celery_app.task(name="refresh_warehouse_lineage")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def refresh_warehouse_lineage() -> int:
    """Daily warehouse-native lineage refresh (#858, ADR 0034) — dark by default
    (``WAREHOUSE_LINEAGE_ENABLED``; the views need grants the principal may lack).
    Per-connection fail-soft: one unreachable warehouse never aborts the sweep.
    """
    if not get_settings().warehouse_lineage_enabled:
        return 0
    session = get_session()
    try:
        connections = list(
            session.scalars(
                select(Connection).where(Connection.type.in_(("snowflake", "unity_catalog")))
            )
        )
        refreshed = 0
        for connection in connections:
            outcome = warehouse_refresh.refresh_connection_lineage(
                session, connection=connection, secret_store=get_secret_store()
            )
            if outcome is not None:
                refreshed += 1
        return refreshed
    finally:
        session.close()


# ─────────────────────── beat liveness heartbeat (#904) ─────────────────────

_HEARTBEAT_STORE: beat_watchdog._TickStore | None = None


def _heartbeat_store() -> beat_watchdog._TickStore:
    """One timeout-bounded Redis client for the heartbeat, built on first use."""
    global _HEARTBEAT_STORE
    if _HEARTBEAT_STORE is None:
        _HEARTBEAT_STORE = beat_watchdog.build_store(get_settings().redis_url)
    return _HEARTBEAT_STORE


@celery_app.task(name="beat_heartbeat")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def beat_heartbeat() -> bool:
    """Stamp 'the beat→broker→worker loop is actually executing tasks' (#904), in Redis (the
    in-process watchdog) AND `workspace_health` (the admin health read API, #1885). Either
    write failing does not skip the other.
    """
    ok = True
    try:
        beat_watchdog.record_beat_tick(_heartbeat_store())
    except Exception:
        log.warning("beat_heartbeat_write_failed", exc_info=True)
        ok = False
    session = get_session()
    try:
        workspace_health_service.record_beat_heartbeat(session)
    except Exception:
        log.warning("beat_heartbeat_db_write_failed", exc_info=True)
        ok = False
    finally:
        session.close()
    return ok


# ─────────────────── credential-expiry refresh (#838) ────────────────────────


@celery_app.task(name="refresh_credential_expiry")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def refresh_credential_expiry() -> int:
    """Daily re-read of every credential's stated expiry into
    `connections.credential_expires_at` (#838) — the warn-before-it-dies signal.
    Fail-soft: a vault outage must not fail the beat tick or read as a credential
    problem. Returns the count of changed expiries.
    """
    session = get_session()
    try:
        return connection_service.refresh_credential_expiry(
            session, secret_store=get_secret_store()
        )
    except Exception:
        session.rollback()
        log.warning("credential_expiry_refresh_failed", exc_info=True)
        return 0
    finally:
        session.close()


@celery_app.task(name=LLM_INVOKE_TASK_NAME)  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def llm_invoke(invocation_id: str) -> str:
    """Execute one queued LLM round-trip (ADR 0042, #1511). All failure handling
    lives in `execute_invocation`, which always lands the row terminal.
    """
    session = get_session()
    try:
        return llm_service.execute_invocation(
            session, uuid.UUID(invocation_id), secret_store=get_secret_store()
        )
    finally:
        session.close()
