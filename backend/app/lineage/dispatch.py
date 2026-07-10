"""The fail-open OpenLineage choke point the worker calls (ADR 0034, #758).

``emit_run_lineage_start`` / ``emit_run_lineage_terminal`` load a run's graph, build
the event, and emit it. Both are **best-effort and never raise** — lineage emission
is a browse/reason convenience layered over the execution model, so a dead or slow
OpenLineage receiver must never fail or roll back an already-persisted run. When
emission is unconfigured the client is ``None`` and each returns ``False``
immediately, before touching the session (zero queries on the dark path). Precedent:
``alerting.dispatch.publish_run_outcome``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Check, Result, Run, Suite
from backend.app.lineage import emitter

log = get_logger(__name__)


def emit_run_lineage_start(session: Session, *, run_id: uuid.UUID) -> bool:
    """Emit a START event for ``run_id``. Returns whether an event was emitted.

    No-op (``False``, no queries) when emission is unconfigured or the run/suite is
    missing. Any failure — loading the graph, building, or the emit itself — is
    logged and swallowed.
    """
    client = emitter.get_openlineage_client()
    if client is None:
        return False
    try:
        run = session.get(Run, run_id)
        if run is None:
            return False
        suite = session.get(Suite, run.suite_id)
        if suite is None:
            return False
        asset = session.get(Asset, run.asset_id) if run.asset_id else None
        client.emit(emitter.build_start_event(run, suite, asset))
        return True
    except Exception:
        log.exception("openlineage_emit_start_failed", run_id=str(run_id))
        return False


def emit_run_lineage_terminal(session: Session, *, run_id: uuid.UUID) -> bool:
    """Emit a terminal (COMPLETE / FAIL / ABORT) event for ``run_id``.

    Loads the run's checks + results to populate the data-quality facets. Same
    fail-open contract as :func:`emit_run_lineage_start` — no queries on the dark
    path, never raises.
    """
    client = emitter.get_openlineage_client()
    if client is None:
        return False
    try:
        run = session.get(Run, run_id)
        if run is None:
            return False
        suite = session.get(Suite, run.suite_id)
        if suite is None:
            return False
        asset = session.get(Asset, run.asset_id) if run.asset_id else None
        checks = list(session.scalars(select(Check).where(Check.suite_id == suite.id)))
        results = list(session.scalars(select(Result).where(Result.run_id == run.id)))
        client.emit(emitter.build_terminal_event(run, suite, asset, checks, results))
        return True
    except Exception:
        log.exception("openlineage_emit_terminal_failed", run_id=str(run_id))
        return False
