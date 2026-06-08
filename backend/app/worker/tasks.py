"""Celery tasks for asynchronous suite execution.

``run_suite`` is the worker entry point dispatched by the probe endpoint. It
loads the run's suite / connection / checks, builds the datasource adapter, and
hands off to ``run_service.execute_run``. The DB-touching core is factored into
``_run_suite`` so it can be unit-tested with a fake session + fake runner (no
Postgres, no Snowflake); real-DB integration coverage is a Week 8 item.

The target table is passed in by the caller: the suite/dataset model does not
carry a target table until Week 3, so for now the probe endpoint supplies it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import get_secret_store
from backend.app.datasources.registry import build_check_runner
from backend.app.db.models import Check, Connection, Run, Suite
from backend.app.db.session import get_session
from backend.app.services import run_service
from backend.app.worker.celery_app import celery_app

log = get_logger(__name__)


def _run_suite(
    session: Session,
    *,
    run_id: uuid.UUID,
    table: str,
    schema: str | None,
    catalog: str | None = None,
) -> str:
    """Load the run's graph, build the runner, execute. Returns the final status.

    Dispatches by ``connection.type`` through the runner registry, so a Snowflake
    / Unity Catalog / flat-file suite each gets its correct `CheckRunner` (#146).
    ``catalog`` is required for Unity Catalog and ignored by the other types.

    Failures while loading or building the runner (missing rows, bad connection
    config, unresolved secret) drive the run to ``failed`` so it never lingers in
    ``queued``; execution failures are handled inside ``execute_run``.
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
        checks = list(session.scalars(select(Check).where(Check.suite_id == suite.id)))
        runner = build_check_runner(
            conn_type=connection.type,
            config=connection.config,
            secret_ref=connection.secret_ref,
            secret_store=get_secret_store(),
            catalog=catalog,
        )
    except Exception:
        run.status = "failed"
        run.started_at = run.started_at or datetime.now(UTC)
        run.finished_at = datetime.now(UTC)
        session.commit()
        log.exception("run_suite_setup_failed", run_id=str(run_id))
        return "failed"

    run_service.execute_run(
        session, run=run, checks=checks, runner=runner, table=table, schema=schema
    )
    return str(run.status)


@celery_app.task(name="run_suite")  # type: ignore[untyped-decorator]  # celery task decorator is unannotated
def run_suite(
    run_id: str, table: str, schema: str | None = None, catalog: str | None = None
) -> str:
    """Worker entry point. ``run_id`` is a string so it serialises over JSON.

    ``catalog`` is passed through for Unity Catalog suites; other types ignore it.
    """
    session = get_session()
    try:
        return _run_suite(
            session, run_id=uuid.UUID(run_id), table=table, schema=schema, catalog=catalog
        )
    finally:
        session.close()
