"""Suite-on-suite triggers (ADR 0051, #244): a run that succeeded with nothing breached fires
the `provider='suite'` bindings keyed on its suite, queuing one downstream run each."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import (
    SUITE_TRIGGER_MAX_DEPTH,
    SUITE_TRIGGER_PROVIDER,
    Connection,
    Run,
    Suite,
    TriggerBinding,
)
from backend.app.services import run_dispatch
from backend.app.services.orchestration_service import insert_triggered_run
from backend.app.services.run_service import check_outcome_counts

log = get_logger(__name__)

#: Tiers that stop a chain: the upstream said its data is bad, so the downstream is not run on it.
_BLOCKING_TIERS = frozenset({"fail", "critical"})


def marker_for(run: Run) -> str:
    return f"{SUITE_TRIGGER_PROVIDER}:{run.suite_id}:{run.id}"


def upstream_run_id(marker: str | None) -> uuid.UUID | None:
    """The upstream run a ``suite:`` marker names, or ``None`` for any other marker."""
    if not marker:
        return None
    provider, _, rest = marker.partition(":")
    if provider != SUITE_TRIGGER_PROVIDER:
        return None
    _, _, run_text = rest.partition(":")
    try:
        return uuid.UUID(run_text)
    except ValueError:
        return None


def chain_depth(session: Session, run: Run) -> int:
    """How many suite-triggered hops led to ``run`` (0 for a run nothing in DataQ triggered)."""
    depth = 0
    current: Run | None = run
    seen: set[uuid.UUID] = set()
    while current is not None and depth <= SUITE_TRIGGER_MAX_DEPTH:
        parent_id = upstream_run_id(current.triggered_by)
        if parent_id is None or parent_id in seen:
            break
        seen.add(parent_id)
        depth += 1
        current = session.get(Run, parent_id)
    return depth


def passed(session: Session, run: Run) -> bool:
    """Succeeded with no check at ``fail``/``critical``; ``warn``, ``skip`` and ``error`` pass."""
    if run.status != "succeeded":
        return False
    _total, _passed, worst = check_outcome_counts(session, [run.id]).get(run.id, (0, 0, None))
    return worst not in _BLOCKING_TIERS


def fire_for_run(session: Session, *, run_id: uuid.UUID) -> list[Run]:
    """Queue one run per enabled suite binding on ``run``'s suite, if the run passed.
    Idempotent per upstream run (the ``suite:`` marker + the dedup index). Never raises
    for a trigger problem — the run that completed must not fail because of its followers.
    """
    run = session.get(Run, run_id)
    if run is None or not passed(session, run):
        return []
    suite = session.get(Suite, run.suite_id)
    connection = session.get(Connection, suite.connection_id) if suite is not None else None
    if suite is None or connection is None:
        return []
    bindings = list(
        session.scalars(
            select(TriggerBinding).where(
                TriggerBinding.provider == SUITE_TRIGGER_PROVIDER,
                TriggerBinding.pipeline_or_dag_id == str(run.suite_id),
                TriggerBinding.env == connection.env,
                TriggerBinding.enabled.is_(True),
            )
        )
    )
    if not bindings:
        return []
    depth = chain_depth(session, run)
    if depth >= SUITE_TRIGGER_MAX_DEPTH:
        log.warning(
            "suite_trigger_depth_exceeded",
            run_id=str(run.id),
            suite_id=str(run.suite_id),
            depth=depth,
            max_depth=SUITE_TRIGGER_MAX_DEPTH,
            bindings=len(bindings),
        )
        return []
    marker = marker_for(run)
    created: list[Run] = []
    for binding in bindings:
        queued = insert_triggered_run(session, suite_id=binding.suite_id, marker=marker)
        if queued is not None:
            created.append(queued)
    if not created:
        return []
    session.commit()
    for queued in created:
        session.refresh(queued)
    log.info(
        "suite_runs_triggered",
        provider=SUITE_TRIGGER_PROVIDER,
        upstream_suite_id=str(run.suite_id),
        run_marker=marker,
        count=len(created),
        depth=depth + 1,
    )
    for queued in created:
        run_dispatch.dispatch_or_fail(
            session, queued, provider=SUITE_TRIGGER_PROVIDER, upstream_run=str(run.id)
        )
    return created


__all__ = ["chain_depth", "fire_for_run", "marker_for", "passed", "upstream_run_id"]
