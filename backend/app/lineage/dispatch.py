"""The fail-open OpenLineage choke point the worker calls (ADR 0034, #758)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Run, Suite
from backend.app.lineage import emitter
from backend.app.services import check_service, run_service

if TYPE_CHECKING:
    from openlineage.client.event_v2 import RunEvent

log = get_logger(__name__)


def _emit(
    session: Session,
    *,
    run_id: uuid.UUID,
    event: str,
    build: Callable[[Run, Suite, Asset | None], RunEvent],
) -> bool:
    """Shared gate + load + build + emit + fail-open skeleton for both phases."""
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
        client.emit(build(run, suite, asset))
        return True
    except Exception:
        log.exception(event, run_id=str(run_id))
        return False


def emit_run_lineage_start(session: Session, *, run_id: uuid.UUID) -> bool:
    """Emit a START event for ``run_id``. Returns whether an event was emitted."""
    return _emit(
        session,
        run_id=run_id,
        event="openlineage_emit_start_failed",
        build=emitter.build_start_event,
    )


def emit_run_lineage_terminal(session: Session, *, run_id: uuid.UUID) -> bool:
    """Emit a terminal (COMPLETE / FAIL / ABORT) event for ``run_id``."""

    def _build(run: Run, suite: Suite, asset: Asset | None) -> RunEvent:
        checks = check_service.list_checks(session, suite.id)
        results = run_service.list_results(session, run.id)
        return emitter.build_terminal_event(run, suite, asset, checks, results)

    return _emit(
        session,
        run_id=run_id,
        event="openlineage_emit_terminal_failed",
        build=_build,
    )
