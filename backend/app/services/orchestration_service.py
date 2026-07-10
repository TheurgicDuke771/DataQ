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

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Connection, PipelineRun, Run, Suite, TriggerBinding
from backend.app.lineage import dbt_manifest, edges
from backend.app.orchestration.base import OrchestrationProvider, RunUpdate
from backend.app.orchestration.registry import get_orchestration_provider
from backend.app.services import run_dispatch

log = get_logger(__name__)

# Predicate of the partial unique index `uq_runs_suite_triggered_by` (#308) —
# kept identical to the migration and the model's `postgresql_where`. Scopes the
# dedup guard to orchestration markers (`<provider>:<pipeline>:<run_id>`) so the
# repeatable manual/probe/schedule markers are unaffected.
_ORCH_TRIGGER_PREDICATE = text(
    "triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%' OR triggered_by LIKE 'dbt:%'"
)

# Terminal pipeline-run statuses — a run in one of these won't transition again,
# so the poll's `skip_updated_since` churn-optimisation may skip re-recording it.
# A non-terminal row (queued/running) must always be re-processed so a later
# transition (e.g. running → succeeded) isn't dropped (#490).
_TERMINAL_PIPELINE_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


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
        # Atomic dedup: the partial unique index `uq_runs_suite_triggered_by`
        # (#308) + ON CONFLICT DO NOTHING makes a concurrent second ingestion of
        # the same pipeline-run event (webhook + poll, or poll + gap-recovery) a
        # graceful no-op instead of a double-trigger or an IntegrityError. A
        # row comes back only for the winner; the loser/replay returns nothing.
        run = session.scalars(
            pg_insert(Run)
            .values(
                suite_id=binding.suite_id,
                # Bespoke Run construction (atomic dedup needs pg_insert) — the
                # ORM sibling is `run_dispatch.new_queued_run`; a new stamped run
                # field must land in BOTH. Stamp the suite's asset at dispatch (ADR
                # 0034) inline, so an orchestration-triggered run records its asset
                # like every other run path. Scalar subquery keeps it a single
                # INSERT; NULL when the suite never resolved an asset (fail-soft).
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
            # Broker down: the shared helper marks the run terminal-`failed` and
            # logs (with the pipeline correlation kept on the event); the batch
            # carries on so one stuck broker can't drop the rest (#227). The run
            # stays in `created` either way (it was created).
            run_dispatch.dispatch_or_fail(
                session, run, provider=provider, pipeline=update.pipeline_or_dag_id
            )
    return created


def _refresh_lineage(
    session: Session,
    *,
    provider_impl: OrchestrationProvider,
    connection: Connection,
    update: RunUpdate,
    secret_store: SecretStore,
) -> None:
    """Best-effort dbt-manifest lineage refresh on a succeeded orchestration run.

    Provider-agnostic (CLAUDE.md §11): probes for the OPTIONAL ``read_manifest``
    capability via ``getattr`` — only the dbt provider has it, so ADF/Airflow are a
    no-op with zero name branching. Fetches the manifest, parses it, and refreshes
    the `lineage_edges` cache. The webhook path gives the immediate refresh the AC
    demands; the 10-min poll is the fallback.

    Wrapped fail-open: a lineage failure (unreadable manifest, parse error, DB
    hiccup) must NEVER affect run ingestion or suite triggering — it logs and
    returns. `edges.refresh_dbt_edges` is itself fail-open too (belt and braces).
    """
    reader = getattr(provider_impl, "read_manifest", None)
    if reader is None:
        return
    if not connection.secret_ref:
        return
    try:
        secret = secret_store.get(connection.secret_ref)
        raw = reader(dict(connection.config), secret, update.pipeline_or_dag_id)
        if raw is None:
            return
        graph = dbt_manifest.parse_manifest(raw)
        edges.refresh_dbt_edges(session, connection=connection, graph=graph)
    except Exception as exc:
        log.warning(
            "lineage_refresh_failed",
            provider=provider_impl.provider,
            connection_id=str(connection.id),
            pipeline=update.pipeline_or_dag_id,
            error=str(exc),
        )


@dataclass(frozen=True)
class IngestResult:
    pipeline_run: PipelineRun | None
    triggered_runs: list[Run] = field(default_factory=list)


def request_immediate_poll(provider: str, resource_name: str | None) -> bool:
    """Poll-now for run-anonymous alert webhooks (`AlertPing`, #492).

    A Common-Alert-Schema alert names the factory/pipeline but no runId, so it
    can't be upserted directly — instead the receiver trades the 10-min poll
    cadence for *now*: enqueue one **targeted** poll (this provider, and when
    the alert named its resource, just that connection), which ingests the real
    run(s) through the normal idempotent path. Targeting keeps an alert storm
    (one fired webhook per pipeline dimension) from amplifying into repeated
    full sweeps of every orchestrator. Best-effort — a broker hiccup must not
    fail the webhook ack (the 10-min beat recovers on its own); returns whether
    the poll was actually enqueued so the ack can be honest about it.
    """
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
    triggered: list[Run] = []
    if update.status == "succeeded":
        triggered = _trigger_suites(
            session, provider=provider, connection=connection, update=update
        )
        # Immediate manifest re-read on the webhook path (the AC's convergence
        # channel); fail-open, never affects the ingest/trigger result above.
        _refresh_lineage(
            session,
            provider_impl=provider_impl,
            connection=connection,
            update=update,
            secret_store=secret_store,
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
    secret_store: SecretStore | None = None,
) -> PollIngestResult:
    """Persist the runs a poll returned for one orchestrator connection.

    Records **every status** for the monitor view (#490), but stays the
    **trigger-on-success** channel (ADR 0004): only a ``succeeded`` run triggers a
    suite — failures/running are recorded, never triggered (mirrors `ingest_event`).
    Poll data is already authoritative, so there is no REST enrichment.

    The ``skip_updated_since`` churn-optimisation skips a run we already recorded
    inside this window — but **only when the existing row is already terminal**
    (succeeded/failed/cancelled). A non-terminal row (queued/running) must always
    be re-processed: now that the poll records non-terminal states (#490), skipping
    it on time alone would drop a later ``running → succeeded`` transition — losing
    both the monitor update *and* the trigger. The connection is known (we polled
    it), so no resolve.

    ``secret_store``, when supplied (the worker passes it), enables the fail-open
    dbt-manifest lineage refresh on succeeded runs — the poll-path fallback to the
    webhook's immediate re-read. It's optional so pure poll-ingestion tests need
    not thread a store.
    """
    provider = provider_impl.provider
    pipeline_runs: list[PipelineRun] = []
    triggered: list[Run] = []
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
            # re-read); only when a secret store was supplied. Fail-open.
            if secret_store is not None:
                _refresh_lineage(
                    session,
                    provider_impl=provider_impl,
                    connection=connection,
                    update=update,
                    secret_store=secret_store,
                )
    return PollIngestResult(pipeline_runs=pipeline_runs, triggered_runs=triggered, skipped=skipped)


# ── read model (PR-C0b: the pipeline-runs monitoring feed) ───────────────────
# `pipeline_runs` is orchestration *monitoring*, not suite-scoped data — it has
# no `suite_id` and no share rows — so the feed is gated on authentication only
# (any signed-in user), unlike the suite-scoped `runs`/`results` reads. The link
# back to a DQ run is the `triggered_by` marker on `runs`, not a column here.


def list_pipeline_runs(
    session: Session,
    *,
    provider: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[PipelineRun]:
    """Monitored orchestrator pipeline/DAG runs, newest first (`created_at` desc).

    Optionally filtered by ``provider`` (adf / airflow) and/or ``status``.
    """
    stmt = select(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(limit)
    if provider is not None:
        stmt = stmt.where(PipelineRun.provider == provider)
    if status is not None:
        stmt = stmt.where(PipelineRun.status == status)
    return list(session.scalars(stmt))


def list_pipelines(
    session: Session,
    *,
    provider: str | None = None,
    env: str | None = None,
    limit: int = 50,
) -> list[PipelineRun]:
    """Latest run per distinct pipeline (provider, pipeline_or_dag_id, env).

    The orchestration "pipeline status" view (one row per monitored pipeline,
    carrying its most-recent run's status/timing), as opposed to the flat
    per-run feed in :func:`list_pipeline_runs`. Provider-agnostic — ADF and
    Airflow share the shape — and optionally narrowed by ``provider`` and/or
    ``env``. Same auth-only gating: monitoring data, not suite-scoped.
    """
    # "Recency" = COALESCE(started_at, created_at): started_at is the truth, but
    # it is nullable (a failure event can land before — or without — a start
    # time), so fall back to created_at (NOT NULL) rather than ordering those
    # runs last. Ordering them last would let an older, fully-timed run mask the
    # freshest run inside its partition — the opposite of a "latest status" view.
    recency = func.coalesce(PipelineRun.started_at, PipelineRun.created_at)
    # Inner DISTINCT ON picks each pipeline's most-recent run. Postgres requires
    # the ORDER BY to lead with the partition keys, so the recency ordering can't
    # also drive the cross-pipeline display order here…
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
