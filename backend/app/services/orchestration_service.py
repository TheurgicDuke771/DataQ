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

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Connection, PipelineRun, Run, TriggerBinding
from backend.app.orchestration.base import OrchestrationProvider, RunUpdate
from backend.app.orchestration.registry import get_orchestration_provider

log = get_logger(__name__)


def _resolve_connection(
    session: Session, *, provider_impl: OrchestrationProvider, resource_name: str
) -> Connection | None:
    """The orchestrator connection whose resource matches the event.

    Matches on the provider's own resource key (`factory_name` for ADF,
    `base_url` for Airflow) — the provider owns that knowledge, so this stays
    provider-agnostic. The PR-6 `(type, env)` guard makes an orchestrator
    singular per env; resource names are unique across envs too, so this resolves
    the right connection regardless.
    """
    stmt = select(Connection).where(
        Connection.type == provider_impl.provider,
        Connection.config[provider_impl.resource_config_key].astext == resource_name,
    )
    matches = list(session.scalars(stmt))
    if not matches:
        return None
    if len(matches) > 1:
        log.warning(
            "orchestration_resource_ambiguous",
            provider=provider_impl.provider,
            resource_name=resource_name,
            match_count=len(matches),
        )
    return matches[0]


def _upsert_pipeline_run(
    session: Session, *, provider: str, connection: Connection, update: RunUpdate
) -> PipelineRun:
    """Idempotent `pipeline_runs` upsert keyed on (provider, provider_run_id).

    A replayed / re-delivered event lands on the same row and refreshes the
    mutable status + timing fields (ADR 0006 replay-neutraliser).
    """
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
    if pipeline_run is None:  # pragma: no cover - the row was just upserted
        raise RuntimeError(f"pipeline_run {pipeline_run_id} missing immediately after upsert")
    log.info(
        "pipeline_run_recorded",
        provider=provider,
        provider_run_id=update.provider_run_id,
        pipeline_or_dag_id=update.pipeline_or_dag_id,
        env=connection.env,
        status=update.status,
    )
    return pipeline_run


def record_pipeline_event(
    session: Session, *, provider: str, update: RunUpdate
) -> PipelineRun | None:
    """Resolve + upsert only (the monitor primitive — no enrichment, no trigger).

    Returns the row, or ``None`` if the event could not be attributed to a known
    orchestrator connection. Used directly where triggering isn't wanted.
    """
    provider_impl = get_orchestration_provider(provider)
    connection = _resolve_connection(
        session, provider_impl=provider_impl, resource_name=update.resource_name
    )
    if connection is None:
        log.info(
            "orchestration_event_unattributed",
            provider=provider,
            resource_name=update.resource_name,
            provider_run_id=update.provider_run_id,
        )
        return None
    return _upsert_pipeline_run(session, provider=provider, connection=connection, update=update)


def _maybe_enrich(
    provider_impl: OrchestrationProvider,
    connection: Connection,
    update: RunUpdate,
    secret_store: SecretStore,
) -> RunUpdate:
    """Best-effort authoritative enrichment via the provider's REST API.

    Returns the enriched `RunUpdate` on success; on any failure (no stored
    credential, transport/auth error) falls back to the parsed ``update`` so a
    thin-but-valid webhook is never dropped just because the follow-up call
    failed (ADR 0006: ack well-formed events).
    """
    if not connection.secret_ref:
        return update
    try:
        secret = secret_store.get(connection.secret_ref)
        detailed = provider_impl.fetch_run_detail(
            dict(connection.config), secret, update.provider_run_id
        )
    except NotImplementedError:
        # Provider has no REST enrichment (e.g. Airflow — its signed callback is
        # already authoritative). Not an error; use the parsed update as-is.
        return update
    except Exception as exc:
        log.warning(
            "orchestration_enrich_failed",
            provider=provider_impl.provider,
            provider_run_id=update.provider_run_id,
            error_type=type(exc).__name__,
        )
        return update
    log.info(
        "orchestration_event_enriched",
        provider=provider_impl.provider,
        provider_run_id=update.provider_run_id,
        status=detailed.status,
    )
    return detailed


def _trigger_suites(
    session: Session, *, provider: str, connection: Connection, update: RunUpdate
) -> list[Run]:
    """Create one queued `Run` per enabled `trigger_binding` for a succeeded run.

    Idempotent on the ``triggered_by`` marker ``<provider>:<pipeline>:<run_id>``:
    a replayed event (or a webhook + poll double-delivery) does not spawn a
    second run for the same (suite, pipeline-run).

    Each created run is handed to Celery (``run_suite``) once committed; the
    worker resolves the suite's target (#215) and fails the run cleanly if the
    suite is targetless. A broker failure marks that run ``failed`` rather than
    leaving it stuck ``queued`` (the 10-min poll won't re-dispatch a stale row).
    """
    marker = f"{provider}:{update.pipeline_or_dag_id}:{update.provider_run_id}"
    bindings = list(
        session.scalars(
            select(TriggerBinding).where(
                TriggerBinding.provider == provider,
                TriggerBinding.pipeline_or_dag_id == update.pipeline_or_dag_id,
                TriggerBinding.env == connection.env,
                TriggerBinding.enabled.is_(True),
            )
        )
    )
    created: list[Run] = []
    for binding in bindings:
        already = session.scalar(
            select(Run.id).where(Run.suite_id == binding.suite_id, Run.triggered_by == marker)
        )
        if already is not None:
            continue
        run = Run(suite_id=binding.suite_id, status="queued", triggered_by=marker)
        session.add(run)
        created.append(run)

    if created:
        session.commit()
        for run in created:
            session.refresh(run)
        log.info(
            "suite_runs_triggered",
            provider=provider,
            pipeline=update.pipeline_or_dag_id,
            run_marker=marker,
            count=len(created),
        )
        # Lazy import: `worker.tasks` imports this module, so a module-level
        # import would be circular. The worker resolves each suite's target.
        from backend.app.worker.tasks import run_suite

        for run in created:
            try:
                run_suite.delay(str(run.id))
            except Exception:
                run.status = "failed"
                session.commit()
                log.exception("suite_dispatch_failed", run_id=str(run.id))
    return created


@dataclass(frozen=True)
class IngestResult:
    pipeline_run: PipelineRun | None
    triggered_runs: list[Run] = field(default_factory=list)


def ingest_event(
    session: Session,
    *,
    provider_impl: OrchestrationProvider,
    update: RunUpdate,
    secret_store: SecretStore,
) -> IngestResult:
    """Full webhook ingestion: resolve → enrich (best-effort) → upsert → trigger.

    Triggering fires only for a ``succeeded`` run (failures alert but never
    trigger, ADR 0004). Unattributable events are ignored — the caller still
    acknowledges them (ADR 0006).
    """
    provider = provider_impl.provider
    connection = _resolve_connection(
        session, provider_impl=provider_impl, resource_name=update.resource_name
    )
    if connection is None:
        log.info(
            "orchestration_event_unattributed",
            provider=provider,
            resource_name=update.resource_name,
            provider_run_id=update.provider_run_id,
        )
        return IngestResult(pipeline_run=None)

    update = _maybe_enrich(provider_impl, connection, update, secret_store)
    pipeline_run = _upsert_pipeline_run(
        session, provider=provider, connection=connection, update=update
    )
    triggered = (
        _trigger_suites(session, provider=provider, connection=connection, update=update)
        if update.status == "succeeded"
        else []
    )
    return IngestResult(pipeline_run=pipeline_run, triggered_runs=triggered)


@dataclass(frozen=True)
class PollIngestResult:
    pipeline_runs: list[PipelineRun] = field(default_factory=list)
    triggered_runs: list[Run] = field(default_factory=list)
    skipped: int = 0


def ingest_polled_runs(
    session: Session,
    *,
    provider_impl: OrchestrationProvider,
    connection: Connection,
    updates: list[RunUpdate],
    skip_updated_since: datetime,
) -> PollIngestResult:
    """Persist the runs a poll returned for one orchestrator connection.

    Polling is the **trigger-on-success** fallback (ADR 0004): only ``succeeded``
    runs are persisted + triggered (failures arrive on the webhook). Poll data is
    already authoritative, so there is no REST enrichment. A run whose
    `pipeline_runs` row was already updated at/after ``skip_updated_since`` (e.g.
    by a webhook within this poll window) is skipped to avoid redundant churn —
    triggering is idempotent regardless, so this is an optimisation, not a
    correctness guard. The connection is known (we polled it), so no resolve.
    """
    provider = provider_impl.provider
    pipeline_runs: list[PipelineRun] = []
    triggered: list[Run] = []
    skipped = 0
    for update in updates:
        if update.status != "succeeded":
            continue
        existing = session.scalar(
            select(PipelineRun.last_updated_at).where(
                PipelineRun.provider == provider,
                PipelineRun.provider_run_id == update.provider_run_id,
            )
        )
        if existing is not None and existing >= skip_updated_since:
            skipped += 1
            continue
        pipeline_runs.append(
            _upsert_pipeline_run(session, provider=provider, connection=connection, update=update)
        )
        triggered.extend(
            _trigger_suites(session, provider=provider, connection=connection, update=update)
        )
    return PollIngestResult(pipeline_runs=pipeline_runs, triggered_runs=triggered, skipped=skipped)
