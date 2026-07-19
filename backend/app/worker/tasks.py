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

from backend.app.alerting import dispatch as alert_dispatch
from backend.app.alerting.base import HEALTH_FAILING, HEALTH_RECOVERED
from backend.app.core.config import get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, get_secret_store
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
    comparison_run,
    cron,
    incident_service,
    orchestration_service,
    profile_service,
    run_dispatch,
    run_service,
    run_target,
    schema_drift,
    suite_service,
)
from backend.app.services.failure_classifier import classify_failure_reason
from backend.app.worker import beat_watchdog
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


def _terminal_failed(
    session: Session, run: Run, *, event: str, run_id: uuid.UUID, reason: str | None = None
) -> str:
    """Drive ``run`` to terminal ``failed`` (never left ``queued``/``running``).

    ``reason`` is the redaction-safe, classified message (#605) — setup/materialize
    failures (bad config, unreadable secret, unreachable store) are the largest
    class of real run failures, so they carry a user-facing reason too, not just
    the runner-time path in ``execute_run``.
    """
    run.status = "failed"
    run.started_at = run.started_at or datetime.now(UTC)
    run.finished_at = datetime.now(UTC)
    run.failure_reason = reason
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
    except Exception as exc:
        return _terminal_failed(
            session,
            run,
            event="run_suite_setup_failed",
            run_id=run_id,
            reason=classify_failure_reason(exc),
        )

    # From here the runner exists — everything below runs inside `owned_runner`,
    # which releases its shared engine pool (#427) on every exit: normal return,
    # handled failure, or propagating exception.
    with owned_runner(runner):
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
        except Exception as exc:
            return _terminal_failed(
                session,
                run,
                event="run_suite_materialize_failed",
                run_id=run_id,
                reason=classify_failure_reason(exc),
            )

        # The suite's identifier column (#415) — requested from GX so failing rows
        # are captured with a locator. A `None`/absent policy keeps the scalar-only
        # sample.
        policy = suite.column_policy or {}
        identifier = policy.get("identifier_column")
        index_columns = [str(identifier)] if identifier else None

        # Comparison checks (ADR 0015, #794): bind an executor to this run's
        # resolved target side so the diff validates the exact dataset the GX
        # runner sees. Built only when the suite actually has comparison checks.
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

        # Stateful monitor kinds (#592): the baseline-diff executor owns the
        # session + baseline store, which runners never see. Built only when the
        # suite actually has a stateful check.
        stateful_monitor_executor = None
        if any(c.kind in STATEFUL_MONITOR_KINDS for c in checks):
            stateful_monitor_executor = schema_drift.build_schema_drift_executor(
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
    """Worker entry point. ``run_id`` is a string so it serialises over JSON.

    The target is resolved from the suite (``Suite.target``), so the only
    argument the dispatcher supplies is the run id.

    After the run reaches a terminal state, its outcome is published through the
    ``ResultPublisher`` seam (ADR 0011). The publish is best-effort and isolated
    (``publish_run_outcome`` never raises), so a notification failure can't
    affect the already-persisted run or the task's return value.
    """
    rid = uuid.UUID(run_id)
    session = get_session()
    try:
        # OpenLineage START/terminal emission (ADR 0034, #758) brackets the run.
        # Both calls are fail-open and dark-by-default (no-op with zero queries when
        # unconfigured), so they never fail or slow the task — the single choke
        # point covering execute/skip/early-fail. Sits next to the alert-dispatch
        # hook (same contract). Guarantee: any run that emitted a START gets exactly
        # one terminal event. `_run_suite` itself drives every failure it handles to
        # a terminal status, but should it raise before doing so (a DB hiccup, an
        # unforeseen error), the except-branch still closes the START with a terminal
        # (the run is non-terminal → mapped to FAIL) before re-raising. (A cancel
        # while still queued — or a successful revoke that drops the task — produces
        # zero lineage events by design: no START ever fired.)
        lineage_dispatch.emit_run_lineage_start(session, run_id=rid)
        try:
            outcome = _run_suite(session, run_id=rid)
        except BaseException:
            lineage_dispatch.emit_run_lineage_terminal(session, run_id=rid)
            raise
        lineage_dispatch.emit_run_lineage_terminal(session, run_id=rid)
        # Roll the run's per-check results up into incidents (open/attach/auto-
        # resolve) BEFORE alert dispatch, so the published report can reference the
        # open incident (ADR 0034 #761). Fail-soft like the two hooks around it —
        # an incident-engine bug must never fail the already-persisted run.
        incident_service.sync_incidents_for_run(session, run_id=rid)
        alert_dispatch.publish_run_outcome(session, run_id=rid)
        return outcome
    finally:
        session.close()


def _auto_classify_columns(session: Session, *, suite_id: uuid.UUID) -> str:
    """Best-effort derive + persist of a suite's failing-sample redaction policy
    (#634) — extracted for a DB-backed unit test without the Celery envelope.

    No-op (returns a reason, never raises) when the suite is gone, has no concrete
    profilable target (a targetless / batch-`pattern` suite), already has a policy
    (never clobber a user or earlier auto choice), or the datasource can't be
    introspected. The value-signal PII classifier still runs at redaction time
    regardless, so a skipped derive only costs the auto-picked identifier locator.
    """
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
        # Lock the row, then confirm nothing changed under us during the
        # (seconds-long) introspection before persisting (#642 review): a user may
        # have set their own policy (never clobber it), or the target may have been
        # repointed (making this derive stale — it would reference the old table's
        # columns). The FOR UPDATE lock closes the check→write race — a concurrent
        # `set_column_policy` blocks until our commit, then wins on its own re-read.
        session.refresh(suite, with_for_update=True)
        if suite.column_policy is not None or suite.target != target:
            session.rollback()  # release the lock; don't persist a raced/stale derive
            return "skipped_raced"
        suite_service.set_column_policy(
            session,
            suite_id,
            identifier_column=policy.get("identifier_column"),
            pii_columns=policy.get("pii_columns", []),
        )
    except Exception:
        session.rollback()
        log.warning("auto_classify_failed", suite_id=str(suite_id), exc_info=True)
        return "error"
    log.info("auto_classify_applied", suite_id=str(suite_id))
    return "classified"


@celery_app.task(name="auto_classify_columns")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def auto_classify_columns(suite_id: str) -> str:
    """Auto-derive + persist a new suite's failing-sample redaction policy (#634).

    Dispatched fire-and-forget when a suite gains a concrete target (create /
    target-set). Best-effort: never raises, never clobbers an existing policy.
    """
    session = get_session()
    try:
        return _auto_classify_columns(session, suite_id=uuid.UUID(suite_id))
    finally:
        session.close()


def _alert_poll_health(
    session: Session, *, connection_id: uuid.UUID, streak: int, recovered: bool
) -> None:
    """Push a poll-health alert on a **transition**, and only on a transition (#837).

    The failure edge fires when the streak *equals* the threshold — not when it exceeds
    it. That `==` is the whole design: a connection whose credential expired keeps
    failing every 10 minutes forever, and alerting on `>=` would send 144 notifications
    a day until someone muted the channel — which is how you end up back at #828, blind
    to a real outage because the alert that would have told you is in a mute rule.

    The recovery edge fires only when the streak we just cleared had reached the
    threshold, i.e. only when we actually alerted on the way down — so a connection that
    blipped once and recovered stays silent in both directions.

    Threshold changes take effect on the next streak: a connection already past a
    newly-lowered threshold won't retro-fire (it never lands on `==` again).

    Never raises — `publish_connection_health` swallows its own failures, and a
    notification problem must not break the polling sweep.
    """
    threshold = get_settings().orchestration_poll_failure_alert_threshold
    if threshold <= 0:  # push disabled; #828's in-app health signals still stand
        return
    if recovered:
        if streak >= threshold:
            alert_dispatch.publish_connection_health(
                session, connection_id=connection_id, state=HEALTH_RECOVERED
            )
        return
    if streak == threshold:
        alert_dispatch.publish_connection_health(
            session, connection_id=connection_id, state=HEALTH_FAILING
        )


def _poll_orchestration_runs(
    session: Session,
    *,
    secret_store: SecretStore,
    now: datetime | None = None,
    lookback: timedelta = _POLL_LOOKBACK,
    provider: str | None = None,
    resource_name: str | None = None,
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

    ``provider`` / ``resource_name`` narrow the sweep for alert-triggered
    poll-now calls (#492): an `AlertPing` names the provider (and usually the
    factory), so only the matching connection(s) are polled — an alert storm
    can't amplify into repeated full sweeps of every orchestrator. The match
    rides the provider's ``resource_config_key`` seam, no provider branching.
    """
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
            # Make the failure a fact about the CONNECTION, not just a log line (#828).
            # This is the whole point: prod lineage rotted for six days behind an
            # expired credential while the product reported nothing wrong. Runs after
            # the rollback above, on a clean session, and is itself fail-soft — a
            # bookkeeping error must never take down the sweep it is reporting on.
            try:
                streak = orchestration_service.record_poll_failure(
                    session, connection_id=connection.id, exc=exc
                )
                _alert_poll_health(
                    session, connection_id=connection.id, streak=streak, recovered=False
                )
            except Exception:
                session.rollback()
                log.exception(
                    "orchestration_poll_health_write_failed",
                    connection_id=str(connection.id),
                )
        else:
            # The recovery alert lives OUT of the try above, deliberately. Inside it, any
            # raise on the notification path would land in the `except` — which records a
            # poll FAILURE — so a connection that had just polled *successfully* would be
            # marked failing, corrupting the very streak the alert keys on. The publish is
            # already fail-soft; this makes it structurally impossible for it to matter.
            _alert_poll_health(
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
    """Open a session, run the poll core over ``lookback``, always close.

    Shared by the beat entry points (regular poll + gap recovery) and the
    alert-triggered poll-now path (#492) so the session lifecycle lives in one
    place — what varies is the window and the optional targeting.
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
    """The 10-min beat polling fallback; also the alert-triggered poll-now
    (#492), where ``provider``/``resource_name`` narrow the sweep to the
    alerting orchestrator."""
    return _run_orchestration_poll(_POLL_LOOKBACK, provider=provider, resource_name=resource_name)


@celery_app.task(name="recover_orchestration_gaps")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def recover_orchestration_gaps() -> dict[str, int]:
    """Celery-beat entry point — gap recovery (B2), startup + every 30 min.

    The same poll pipeline over the wider ``_GAP_RECOVERY_LOOKBACK`` window, to
    re-ingest runs missed while the system was down. Idempotent with the regular
    poll (upsert + `skip_updated_since`).
    """
    return _run_orchestration_poll(_GAP_RECOVERY_LOOKBACK)


def _refresh_dbt_lineage(
    session: Session, *, connection_id: uuid.UUID, job: str, secret_store: SecretStore
) -> str:
    """Fetch + parse + refresh the dbt lineage cache for one (connection, job).

    The worker-side body of `refresh_dbt_lineage`, extracted for a DB-backed unit
    test without the Celery envelope. Runs off the webhook/poll path (dispatched by
    `orchestration_service._dispatch_lineage_refresh` on a succeeded dbt run) so the
    receiver never blocks on the artifact download + parse + N+M upserts.

    Fully **fail-open**: every step returns a reason string rather than raising, and
    one consistent ``dbt_lineage_refresh_*`` log family covers each outcome — a bad
    manifest, an unreadable store, or a DB hiccup must never surface as a task error.
    """
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
    """Async dbt-manifest lineage refresh for one (connection, job) (ADR 0034, #759).

    Dispatched fire-and-forget by the orchestration ingest path when a dbt run
    succeeds (webhook immediately, poll as the fallback). Own session + own single
    secret fetch, so the artifact IO never blocks the webhook ACK / poll loop.
    Best-effort: never raises (`_refresh_dbt_lineage` fails open per step).
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

    run = run_dispatch.new_queued_run(suite, triggered_by=f"schedule:{schedule.id}")
    session.add(run)
    session.commit()
    session.refresh(run)
    # Shared dispatch+broker-failure block (#227): on failure the run is marked
    # terminal-`failed` and logged (with schedule_id kept on the event); the
    # advance is already committed, so the schedule has left the due window.
    if not run_dispatch.dispatch_or_fail(session, run, schedule_id=str(schedule.id)):
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


# ─────────────────────── result retention sweep (PII purge) ─────────────────


@celery_app.task(name="purge_sample_failures")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def purge_sample_failures() -> int:
    """Celery-beat entry point — daily PII-retention sweep.

    Scrubs `sample_failures` from results older than the configured
    ``sample_failures_retention_days`` window (keeping the row + `metric_value` so
    trends survive — ADR 0012). Returns the number of rows scrubbed.
    """
    session = get_session()
    try:
        retention_days = get_settings().sample_failures_retention_days
        return run_service.purge_expired_sample_failures(session, retention_days=retention_days)
    finally:
        session.close()


# ──────────────────────── stuck-run reaper (#309) ──────────────────────────


@celery_app.task(name="reap_stuck_runs")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def reap_stuck_runs() -> int:
    """Celery-beat entry point — fail runs orphaned in a non-terminal state (#309).

    A run committed ``queued`` before its task was published — or left ``running``
    by a worker that died mid-execution — would otherwise linger forever (gap
    recovery only covers ``pipeline_runs``). The reaper drives such runs, stuck
    past ``stuck_run_threshold_minutes``, to terminal ``failed`` so they surface in
    the runs table / dashboard and the user can re-run. No alert is published — see
    ``run_service.reap_stuck_runs`` for why (a reaped run is an infra/liveness
    event, and alerting a slow-but-alive run would be an irreversible false alarm).
    Returns the count reaped.
    """
    session = get_session()
    try:
        threshold = get_settings().stuck_run_threshold_minutes
        return len(run_service.reap_stuck_runs(session, threshold_minutes=threshold))
    finally:
        session.close()


# ──────────────────────── orphan-asset sweep (#770) ──────────────────────────


@celery_app.task(name="sweep_orphan_assets")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def sweep_orphan_assets() -> int:
    """Celery-beat entry point — delete unreferenced, stale `assets` rows (#770).

    ADR 0034's accepted cleanup posture ("asset rows accrete; last_seen + a
    sweep, not deletes"): a suite retargeting away or a dbt model dropping out of
    the manifest leaves its `assets` row behind with a frozen ``last_seen`` and
    no reference into it — see ``asset_service.sweep_orphan_assets`` for the full
    reference-guard checklist. Returns the count swept.

    Unlike its sibling janitors above, this wraps the service call in its own
    try/except: the guard list is new and hand-maintained (a future referencing
    table — #761 `incidents` — landing without its guard line is exactly the
    failure mode to be defensive about), so a DB-level surprise here is logged
    and swallowed rather than surfaced as a failed Celery task — it must never
    take down the beat tick for the janitors scheduled after it.
    """
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


# ─────────────────────── catalog lineage pull (#762) ─────────────────────────


@celery_app.task(name="refresh_lineage_pull")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def refresh_lineage_pull() -> int:
    """Celery-beat entry point — pull catalog lineage into `lineage_edges` (#762).

    **Dark by default** (ADR 0034's "pulled, never built" posture): when no
    `LineageProvider` is configured (`LINEAGE_PROVIDER` unset) the task no-ops with
    zero queries — the same gate shape the OpenLineage emitter uses. A daily cadence
    (not a liveness interval): a catalog's lineage moves on the artifact/build cadence,
    not per-run, and the pull is a *cache refresh of external truth*, so freshness is
    deliberately bounded (ADR 0034 accepted "freshness bounded by poll cadence") — same
    low-urgency daily tick as the sample-failures and orphan-asset sweeps. Callable
    on-demand too (`refresh_lineage_pull.delay()`). Returns the live pulled-edge count
    (0 when unconfigured or nothing pulled); `refresh_pulled_edges` fails open per step.
    """
    provider = lineage_pull.get_lineage_provider()
    if provider is None:
        return 0
    session = get_session()
    try:
        return lineage_pull.refresh_pulled_edges(session, provider=provider) or 0
    finally:
        session.close()


@celery_app.task(name="refresh_warehouse_lineage")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def refresh_warehouse_lineage() -> int:
    """Celery-beat entry point — refresh warehouse-native lineage for every eligible
    connection (#858, ADR 0034).

    **Dark by default**: no-ops with zero queries unless ``WAREHOUSE_LINEAGE_ENABLED``
    is set — the warehouse views it reads (Snowflake ACCOUNT_USAGE, UC system.access)
    need a grant the connection's principal may not have, so this stays opt-in like the
    catalog pull. Iterates the Snowflake / Unity Catalog connections and refreshes each
    **independently** (`refresh_connection_lineage` is fail-soft per connection: one
    unreachable warehouse records a classified error and never aborts the sweep). Daily
    cadence — a cache refresh of external truth, not a liveness path. Returns the number
    of connections successfully refreshed.
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
    """Stamp 'the beat→broker→worker loop is actually executing tasks' (#904).

    The stamp is written HERE, on execution, not by the scheduler — the outage
    this defends against is "beat keeps queueing, nothing consumes", which a
    scheduler-side heartbeat would have reported healthy right through. The
    watchdog thread (`beat_watchdog`) reads it and exits the process when it
    goes stale, so the platform restarts a worker that is up but idle.

    The client is built ONCE and reused (a fresh pool every 60s forever is pure
    churn) and carries bounded socket timeouts — without them an unresponsive
    broker would block this task forever, pinning a pool slot every minute until
    the worker is starved: the heartbeat would *become* the outage it watches
    for (#931 review).

    Fail-soft: a broker hiccup must not mark the task failed and spam the error
    channel — the watchdog's own `unknown` verdict already covers an unreadable
    store.
    """
    try:
        beat_watchdog.record_beat_tick(_heartbeat_store())
        return True
    except Exception:
        log.warning("beat_heartbeat_write_failed", exc_info=True)
        return False
