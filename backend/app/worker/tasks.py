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

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.datasources.registry import build_check_runner
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Check, Connection, Run, Suite
from backend.app.db.session import get_session
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services import orchestration_service, run_service, run_target
from backend.app.worker.celery_app import celery_app

# Polling fallback (#171): look back slightly further than the 10-min beat
# interval so a run can't slip through the gap between consecutive polls.
_POLL_LOOKBACK = timedelta(minutes=15)

log = get_logger(__name__)


def _run_suite(session: Session, *, run_id: uuid.UUID) -> str:
    """Load the run's graph, resolve its target, build the runner, execute.

    The suite's datasource-shaped ``target`` (#215) resolves to the runner's
    ``(table, schema, catalog)`` via ``run_target.resolve_target``; dispatch by
    ``connection.type`` through the runner registry gives a Snowflake / Unity
    Catalog / flat-file suite its correct `CheckRunner` (#146).

    Failures while loading, resolving the target (targetless or malformed suite),
    or building the runner (missing rows, bad connection config, unresolved
    secret) drive the run to ``failed`` so it never lingers in ``queued``;
    execution failures are handled inside ``execute_run``.
    """
    run = session.get(Run, run_id)
    if run is None:
        log.error("run_suite_run_not_found", run_id=str(run_id))
        return "not_found"

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
        run.status = "failed"
        run.started_at = run.started_at or datetime.now(UTC)
        run.finished_at = datetime.now(UTC)
        session.commit()
        log.exception("run_suite_setup_failed", run_id=str(run_id))
        return "failed"

    run_service.execute_run(
        session, run=run, checks=checks, runner=runner, table=target.table, schema=target.schema
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
    session: Session, *, secret_store: SecretStore, now: datetime | None = None
) -> dict[str, int]:
    """Poll every orchestrator connection for recent succeeded runs (#171, ADR 0004).

    The polling fallback for runs that never produced a webhook: iterate each
    ADF / Airflow connection, ask the provider's `list_recent_runs` for runs
    updated within the lookback window, and hand them to `ingest_polled_runs`
    (upsert + trigger-on-success). Goes through the `OrchestrationProvider` seam —
    no per-provider branching. Each connection is isolated: a transport/auth
    failure logs + continues so one bad connection can't starve the rest.
    """
    since = (now or datetime.now(UTC)) - _POLL_LOOKBACK
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


@celery_app.task(name="poll_orchestration_runs")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def poll_orchestration_runs() -> dict[str, int]:
    """Celery-beat entry point — the 10-min orchestration polling fallback."""
    session = get_session()
    try:
        return _poll_orchestration_runs(session, secret_store=get_secret_store())
    finally:
        session.close()
