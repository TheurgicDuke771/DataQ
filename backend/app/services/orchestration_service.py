"""Pipeline-run persistence for orchestration events, provider-agnostic."""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import (
    ENVS,
    ORCHESTRATION_PROVIDERS,
    PIPELINE_RUN_STATUSES,
    Connection,
    PipelineRun,
    Run,
    Suite,
    TriggerBinding,
)
from backend.app.orchestration.base import OrchestrationProvider, RunUpdate
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services import run_dispatch, workspace_health_service
from backend.app.services.connection_lock import lock_connection as _lock_connection
from backend.app.services.failure_classifier import classify_orchestration_poll_reason

log = get_logger(__name__)


class OrchestrationFilterInvalidError(DataQError):
    status_code = 422
    code = "orchestration_filter_invalid"


def validate_read_filters(
    provider: str | None = None, env: str | None = None, status: str | None = None
) -> None:
    """422 on a filter value outside its closed vocabulary (#306)."""
    if provider is not None and provider not in ORCHESTRATION_PROVIDERS:
        raise OrchestrationFilterInvalidError(
            f"invalid provider {provider!r}",
            detail={"allowed": list(ORCHESTRATION_PROVIDERS)},
        )
    if env is not None and env not in ENVS:
        raise OrchestrationFilterInvalidError(
            f"invalid env {env!r}", detail={"allowed": list(ENVS)}
        )
    if status is not None and status not in PIPELINE_RUN_STATUSES:
        raise OrchestrationFilterInvalidError(
            f"invalid pipeline run status {status!r}",
            detail={"allowed": list(PIPELINE_RUN_STATUSES)},
        )


# Predicate of the partial unique index `uq_runs_suite_triggered_by` (#308) — kept identical to the
# migration and the model's `postgresql_where`.
_ORCH_TRIGGER_PREDICATE = text(
    "triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%' OR triggered_by LIKE 'dbt:%'"
)

# Terminal pipeline-run statuses — a run in one of these won't transition again, so the poll's
# `skip_updated_since` churn-optimisation may skip re-recording it.
_TERMINAL_PIPELINE_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def _resolve_connection(
    session: Session, *, provider_impl: OrchestrationProvider, resource_name: str
) -> Connection | None:
    """The orchestrator connection whose resource matches the event."""
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
    """Idempotent `pipeline_runs` upsert keyed on (provider, provider_run_id)."""
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
    """Resolve + upsert only (the monitor primitive — no enrichment, no trigger)."""
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
    """Best-effort authoritative enrichment via the provider's REST API."""
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


def _record_env_near_misses(
    session: Session, *, provider: str, connection: Connection, update: RunUpdate
) -> None:
    """Env-mismatch near-miss signal (#1186) — no binding fired for this run, but
    one *would have* if its env matched.
    """
    mismatched_envs = sorted(
        set(
            session.scalars(
                select(TriggerBinding.env).where(
                    TriggerBinding.provider == provider,
                    TriggerBinding.pipeline_or_dag_id == update.pipeline_or_dag_id,
                    TriggerBinding.enabled.is_(True),
                    TriggerBinding.env.in_(
                        workspace_health_service.near_miss_partner_envs(connection.env)
                    ),
                )
            )
        )
    )
    for binding_env in mismatched_envs:
        try:
            first_occurrence = workspace_health_service.record_trigger_binding_env_near_miss(
                session,
                provider=provider,
                pipeline_or_dag_id=update.pipeline_or_dag_id,
                run_env=connection.env,
                binding_env=binding_env,
            )
        except Exception:
            # The row write failed (or the session's transaction is now aborted) — roll back so the
            # caller's session is usable again.
            session.rollback()
            log.warning(
                "trigger_binding_env_near_miss_record_failed",
                provider=provider,
                pipeline_or_dag_id=update.pipeline_or_dag_id,
                run_env=connection.env,
                binding_env=binding_env,
            )
            continue
        if first_occurrence:
            log.warning(
                "trigger_binding_env_near_miss",
                provider=provider,
                pipeline_or_dag_id=update.pipeline_or_dag_id,
                run_env=connection.env,
                binding_env=binding_env,
            )


def _trigger_suites(
    session: Session, *, provider: str, connection: Connection, update: RunUpdate
) -> list[Run]:
    """Create one queued `Run` per enabled `trigger_binding` for a succeeded run."""
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
    if not bindings:
        _record_env_near_misses(session, provider=provider, connection=connection, update=update)
    created: list[Run] = []
    for binding in bindings:
        # Atomic dedup: the partial unique index `uq_runs_suite_triggered_by` (#308) + ON CONFLICT
        # DO NOTHING makes a concurrent second ingestion of the same pipeline-run event (webhook +
        # poll, or poll + gap-recovery) a graceful no-op instead of a double-trigger or an
        # IntegrityError.
        run = session.scalars(
            pg_insert(Run)
            .values(
                suite_id=binding.suite_id,
                # Bespoke Run construction (atomic dedup needs pg_insert) — the ORM sibling is
                # `run_dispatch.new_queued_run`; a new stamped run field must land in BOTH.
                asset_id=select(Suite.asset_id)
                .where(Suite.id == binding.suite_id)
                .scalar_subquery(),
                status="queued",
                triggered_by=marker,
            )
            .on_conflict_do_nothing(
                index_elements=["suite_id", "triggered_by"],
                index_where=_ORCH_TRIGGER_PREDICATE,
            )
            .returning(Run)
        ).one_or_none()
        if run is not None:
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
        for run in created:
            # Broker down: the shared helper marks the run terminal-`failed` and logs (with the
            # pipeline correlation kept on the event).
            run_dispatch.dispatch_or_fail(
                session, run, provider=provider, pipeline=update.pipeline_or_dag_id
            )
    return created


def _dispatch_lineage_refresh(
    *, provider_impl: OrchestrationProvider, connection: Connection, update: RunUpdate
) -> None:
    """Dispatch the async dbt-manifest lineage refresh for a succeeded run."""
    if getattr(provider_impl, "read_manifest", None) is None:
        return
    # Lazy import to avoid a service→worker import cycle (mirrors request_immediate_poll).
    from backend.app.worker.celery_app import celery_app

    try:
        celery_app.send_task(
            "refresh_dbt_lineage",
            args=[str(connection.id), update.pipeline_or_dag_id],
        )
    except Exception:
        log.warning(
            "dbt_lineage_dispatch_failed",
            provider=provider_impl.provider,
            connection_id=str(connection.id),
            pipeline=update.pipeline_or_dag_id,
        )


@dataclass(frozen=True)
class IngestResult:
    pipeline_run: PipelineRun | None
    triggered_runs: list[Run] = field(default_factory=list)


def request_immediate_poll(provider: str, resource_name: str | None) -> bool:
    """Poll-now for run-anonymous alert webhooks (`AlertPing`, #492)."""
    from backend.app.worker.celery_app import celery_app

    try:
        celery_app.send_task(
            "poll_orchestration_runs",
            kwargs={"provider": provider, "resource_name": resource_name},
        )
    except Exception:
        log.exception("orchestration_immediate_poll_dispatch_failed", provider=provider)
        return False
    return True


def ingest_event(
    session: Session,
    *,
    provider_impl: OrchestrationProvider,
    update: RunUpdate,
    secret_store: SecretStore,
) -> IngestResult:
    """Full webhook ingestion: resolve → enrich (best-effort) → upsert → trigger."""
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
    triggered: list[Run] = []
    if update.status == "succeeded":
        triggered = _trigger_suites(
            session, provider=provider, connection=connection, update=update
        )
        # Immediate manifest re-read on the webhook path (the AC's convergence
        # channel) — enqueued async; fail-open, never affects the result above.
        _dispatch_lineage_refresh(provider_impl=provider_impl, connection=connection, update=update)
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
    """Persist the runs a poll returned for one orchestrator connection."""
    provider = provider_impl.provider
    pipeline_runs: list[PipelineRun] = []
    triggered: list[Run] = []
    dispatched_jobs: set[str] = set()
    skipped = 0
    for update in updates:
        existing = session.execute(
            select(PipelineRun.status, PipelineRun.last_updated_at).where(
                PipelineRun.provider == provider,
                PipelineRun.provider_run_id == update.provider_run_id,
            )
        ).first()
        if (
            existing is not None
            and existing.status in _TERMINAL_PIPELINE_STATUSES
            and existing.last_updated_at >= skip_updated_since
        ):
            skipped += 1
            continue
        pipeline_runs.append(
            _upsert_pipeline_run(session, provider=provider, connection=connection, update=update)
        )
        if update.status == "succeeded":
            triggered.extend(
                _trigger_suites(session, provider=provider, connection=connection, update=update)
            )
            # Poll-path lineage refresh (the fallback to the webhook's immediate
            # re-read); async + fail-open, deduped per job for this batch.
            job = update.pipeline_or_dag_id
            if job not in dispatched_jobs:
                dispatched_jobs.add(job)
                _dispatch_lineage_refresh(
                    provider_impl=provider_impl, connection=connection, update=update
                )
    return PollIngestResult(pipeline_runs=pipeline_runs, triggered_runs=triggered, skipped=skipped)


# ── read model (PR-C0b: the pipeline-runs monitoring feed) ─────────────────── `pipeline_runs` is
# orchestration *monitoring*, not suite-scoped data — it has no `suite_id` and no share rows.


def pipeline_run_order_by() -> tuple[Any, ...]:
    """The newest-first ordering for the pipeline-run feed — **total**, by design."""
    return (PipelineRun.created_at.desc(), PipelineRun.id.desc())


def _pipeline_run_filters(*, provider: str | None, status: str | None) -> list[Any]:
    """The ONE `WHERE` chain shared by :func:`list_pipeline_runs` and
    :func:`count_pipeline_runs`. Derived once rather than hand-rolled twice, so a
    future filter cannot land on the list without the total — which would make
    `X-Total-Count` quietly disagree with the page it describes (#1108).
    """
    conditions: list[Any] = []
    if provider is not None:
        conditions.append(PipelineRun.provider == provider)
    if status is not None:
        conditions.append(PipelineRun.status == status)
    return conditions


def list_pipeline_runs(
    session: Session,
    *,
    provider: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[PipelineRun]:
    """Monitored orchestrator pipeline/DAG runs, newest first, with paging (#928)."""
    stmt = (
        select(PipelineRun)
        .where(*_pipeline_run_filters(provider=provider, status=status))
        .order_by(*pipeline_run_order_by())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt))


def count_pipeline_runs(
    session: Session,
    *,
    provider: str | None = None,
    status: str | None = None,
) -> int:
    """Total pipeline runs matching the SAME `provider`/`status` filters as
    :func:`list_pipeline_runs`, unaffected by its `limit`/`offset` (#1108 — the `/assets`
    `X-Total-Count` shape: a page shorter than `limit` can't by itself distinguish "that's
    everything" from "there's more"). Shares :func:`_pipeline_run_filters` with the list so the
    two cannot drift.
    """
    stmt = (
        select(func.count())
        .select_from(PipelineRun)
        .where(*_pipeline_run_filters(provider=provider, status=status))
    )
    return session.scalar(stmt) or 0


def list_pipelines(
    session: Session,
    *,
    provider: str | None = None,
    env: str | None = None,
    limit: int = 50,
) -> list[PipelineRun]:
    """Latest run per distinct pipeline (provider, pipeline_or_dag_id, env)."""
    # "Recency" = COALESCE(started_at, created_at): started_at is the truth, but it is nullable (a
    # failure event can land before — or without — a start time).
    recency = func.coalesce(PipelineRun.started_at, PipelineRun.created_at)
    # Inner DISTINCT ON picks each pipeline's most-recent run.
    latest = (
        select(PipelineRun)
        .distinct(
            PipelineRun.provider,
            PipelineRun.pipeline_or_dag_id,
            PipelineRun.env,
        )
        .order_by(
            PipelineRun.provider,
            PipelineRun.pipeline_or_dag_id,
            PipelineRun.env,
            recency.desc(),
            PipelineRun.created_at.desc(),  # deterministic tie-break
        )
    )
    if provider is not None:
        latest = latest.where(PipelineRun.provider == provider)
    if env is not None:
        latest = latest.where(PipelineRun.env == env)
    # …so wrap it and order by recency in the outer query, where LIMIT then caps
    # to the N most-recently-active pipelines (symmetry with list_pipeline_runs).
    sub = latest.subquery()
    pr = aliased(PipelineRun, sub)
    stmt = select(pr).order_by(func.coalesce(pr.started_at, pr.created_at).desc()).limit(limit)
    return list(session.scalars(stmt))


def list_env_near_misses(
    session: Session,
    *,
    user_id: uuid.UUID,
    include_all: bool = False,
    suite_id: uuid.UUID | None = None,
) -> list[workspace_health_service.NearMissRecord]:
    """Current #1186 env-mismatch near-misses (#1199) — thin pass-through to
    `workspace_health_service.list_current_env_near_misses`, kept here so the API layer reaches
    orchestration reads through this module like every other orchestration read
    (`list_pipeline_runs`, `list_pipelines`), not by importing `workspace_health_service`
    directly.
    """
    return workspace_health_service.list_current_env_near_misses(
        session, user_id=user_id, include_all=include_all, suite_id=suite_id
    )


# ─────────────────────── pipeline cadence (#1648) ──────────────────────────

#: Below this many succeeded runs, a median/max gap is noise, not a signal.
_CADENCE_MIN_RUNS = 3
_CADENCE_HISTORY_LIMIT = 10


@dataclass(frozen=True)
class PipelineCadence:
    """How often a bound pipeline actually produces data, from its own run
    history — the grounding for a freshness check's threshold, never asserted
    over too little history (#1648).
    """

    sample_count: int
    insufficient_history: bool
    median_gap_hours: float | None = None
    max_gap_hours: float | None = None
    #: A deterministic default threshold, for when no LLM is configured — the
    #: largest observed gap plus margin, not the median: a threshold tighter
    #: than the slowest still-healthy cycle would false-positive on it.
    suggested_fail_threshold_hours: float | None = None


def compute_pipeline_cadence(
    session: Session, *, provider: str, pipeline_or_dag_id: str, env: str
) -> PipelineCadence:
    rows = session.execute(
        select(func.coalesce(PipelineRun.started_at, PipelineRun.created_at))
        .where(
            PipelineRun.provider == provider,
            PipelineRun.pipeline_or_dag_id == pipeline_or_dag_id,
            PipelineRun.env == env,
            PipelineRun.status == "succeeded",
        )
        .order_by(func.coalesce(PipelineRun.started_at, PipelineRun.created_at).desc())
        .limit(_CADENCE_HISTORY_LIMIT)
    ).all()
    timestamps = sorted(r[0] for r in rows)
    if len(timestamps) < _CADENCE_MIN_RUNS:
        return PipelineCadence(sample_count=len(timestamps), insufficient_history=True)
    gaps_hours = sorted(
        (later - earlier).total_seconds() / 3600
        for earlier, later in itertools.pairwise(timestamps)
    )
    mid = len(gaps_hours) // 2
    median = gaps_hours[mid] if len(gaps_hours) % 2 else (gaps_hours[mid - 1] + gaps_hours[mid]) / 2
    max_gap = gaps_hours[-1]
    return PipelineCadence(
        sample_count=len(timestamps),
        insufficient_history=False,
        median_gap_hours=round(median, 2),
        max_gap_hours=round(max_gap, 2),
        suggested_fail_threshold_hours=round(max_gap * 1.25, 1),
    )


# ─────────────────────────── poll health (#828) ────────────────────────────
#
# A poll that fails every 10 minutes used to be visible only in the logs. These two
# functions make the outcome a fact about the connection, so the product can say "this
# integration is broken" instead of rendering a clean empty state over a dead one.


# A poll's health bookkeeping takes a ROW LOCK (#837, so two overlapping sweeps can't both fire the
# same alert).


def record_poll_success(session: Session, *, connection: Connection) -> int:
    """Mark a connection's poll healthy: clear the error, reset the failure streak."""
    locked = _lock_connection(session, connection.id)
    if locked is None:  # contended or deleted — never block the sweep on bookkeeping
        return 0
    previous_failures = locked.consecutive_poll_failures or 0
    locked.last_polled_at = datetime.now(UTC)
    locked.last_poll_error = None
    locked.consecutive_poll_failures = 0
    session.commit()
    return previous_failures


def record_poll_failure(session: Session, *, connection_id: uuid.UUID, exc: BaseException) -> int:
    """Record a failed poll against the connection and grow its failure streak."""
    connection = _lock_connection(session, connection_id)
    if connection is None:  # deleted mid-sweep, or the row is contended — either way, move on
        return 0
    connection.last_polled_at = datetime.now(UTC)
    connection.last_poll_error = classify_orchestration_poll_reason(exc)[:512]
    connection.consecutive_poll_failures = (connection.consecutive_poll_failures or 0) + 1
    session.commit()
    return connection.consecutive_poll_failures
