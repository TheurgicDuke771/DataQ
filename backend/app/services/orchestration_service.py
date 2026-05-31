"""Pipeline-run persistence for orchestration events, provider-agnostic.

Consumes the normalised `RunUpdate` (from any `OrchestrationProvider`) and lands
it in `pipeline_runs` with an idempotent upsert keyed on
(`provider`, `provider_run_id`) — the ADR 0006 replay-neutraliser: a duplicate
or replayed webhook delivery updates the same row instead of inserting a new one
(and, once triggering lands, does not re-fire a suite).

The run is attributed to an orchestrator connection by matching the event's
`resource_name` (ADF factory) against `connections.config->>'factory_name'` for
the provider's connections; that connection supplies `connection_id` (a NOT NULL
FK) and `env`. An unattributable event (no matching connection) is ignored —
the caller acknowledges it (200) per ADR 0006 rather than erroring, since a
late-arriving event for a deleted connection must not retry-storm Azure Monitor.

FastAPI-free by design (like `connection_service` / `run_service`): takes a
`Session`, returns ORM models, never raises for the ignore case.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Connection, PipelineRun
from backend.app.orchestration.base import RunUpdate

log = get_logger(__name__)


def _resolve_connection(
    session: Session, *, provider: str, resource_name: str
) -> Connection | None:
    """The orchestrator connection whose factory matches the event's resource.

    Matches on the JSONB `factory_name`. The PR-6 `(type, env)` guard makes an
    orchestrator singular per env, but factory names are globally unique in
    Azure, so this resolves the right connection across envs too.
    """
    stmt = select(Connection).where(
        Connection.type == provider,
        Connection.config["factory_name"].astext == resource_name,
    )
    matches = list(session.scalars(stmt))
    if not matches:
        return None
    if len(matches) > 1:
        log.warning(
            "orchestration_resource_ambiguous",
            provider=provider,
            resource_name=resource_name,
            match_count=len(matches),
        )
    return matches[0]


def record_pipeline_event(
    session: Session, *, provider: str, update: RunUpdate
) -> PipelineRun | None:
    """Idempotently upsert a `pipeline_runs` row from a `RunUpdate`.

    Returns the row, or ``None`` if the event could not be attributed to a known
    orchestrator connection (the caller still acknowledges it).
    """
    connection = _resolve_connection(session, provider=provider, resource_name=update.resource_name)
    if connection is None:
        log.info(
            "orchestration_event_unattributed",
            provider=provider,
            resource_name=update.resource_name,
            provider_run_id=update.provider_run_id,
        )
        return None

    now = datetime.now(UTC)
    values = {
        "provider": provider,
        "connection_id": connection.id,
        "provider_run_id": update.provider_run_id,
        "pipeline_or_dag_id": update.pipeline_or_dag_id,
        "env": connection.env,
        "status": update.status,
        "started_at": update.started_at,
        "finished_at": update.finished_at,
        "failure_reason": update.failure_reason,
        "last_updated_at": now,
    }
    stmt = (
        pg_insert(PipelineRun)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_pipeline_runs_provider_run",
            # connection_id / pipeline_or_dag_id / env are stable for a run id;
            # refresh the mutable status + timing fields a later delivery carries.
            set_={
                "status": update.status,
                "started_at": update.started_at,
                "finished_at": update.finished_at,
                "failure_reason": update.failure_reason,
                "last_updated_at": now,
            },
        )
        .returning(PipelineRun.id)
    )
    pipeline_run_id = session.execute(stmt).scalar_one()
    session.commit()

    pipeline_run = session.get(PipelineRun, pipeline_run_id)
    log.info(
        "pipeline_run_recorded",
        provider=provider,
        provider_run_id=update.provider_run_id,
        pipeline_or_dag_id=update.pipeline_or_dag_id,
        env=connection.env,
        status=update.status,
    )
    return pipeline_run
