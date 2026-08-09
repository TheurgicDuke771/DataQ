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
from backend.app.services.failure_classifier import classify_failure_reason

log = get_logger(__name__)


class OrchestrationFilterInvalidError(DataQError):
    status_code = 422
    code = "orchestration_filter_invalid"


def validate_read_filters(
    provider: str | None = None, env: str | None = None, status: str | None = None
) -> None:
    """422 on a filter value outside its closed vocabulary (#306).

    An unrecognised `provider`/`env`/`status` used to flow straight into the
    `WHERE`, so a typo returned `200 []` — indistinguishable from "this provider
    genuinely has no runs", which is the confidently-empty-answer class (#828).
    `None` means "no filter" and is left alone; only a *supplied* value is checked.

    ``status`` joined the gate with `X-Total-Count` (#1108): the header made the
    silence louder, since `?status=succeded` now answers a confident
    `X-Total-Count: 0` alongside the empty page. The column stores lower-case, so
    a wrong-case `Succeeded` matches nothing too and is rejected the same way.

    Mirrors `trigger_binding_service._validate_provider_env`, which guards the write
    path against the same vocabularies. Kept separate because that one requires both
    values while a read filter may supply any subset.
    """
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


def _record_env_near_misses(
    session: Session, *, provider: str, connection: Connection, update: RunUpdate
) -> None:
    """Env-mismatch near-miss signal (#1186) — no binding fired for this run, but
    one *would have* if its env matched.

    Called only when `_trigger_suites` found zero bindings for this run's exact
    (provider, pipeline_or_dag_id, env): if an ENABLED binding exists for the same
    (provider, pipeline_or_dag_id) in a DIFFERENT env, that binding is a candidate
    victim of the #1186 ambiguity — a pipeline/DAG run genuinely succeeded and a
    binding genuinely exists for it, but the run's env (this connection's `env`,
    resolved via `_resolve_connection`) doesn't match the binding's env, so
    nothing fired and nothing said why beyond a log line. This records a
    DB-visible, deduped marker alongside the log so the mismatch survives past
    the log retention window (`workspace_health_service`, mirrors the #1100
    `POLL_STALENESS_KEY` write shape).

    Deliberately narrow: only reached when the exact-env match list is empty, so
    a pipeline that already triggers correctly never logs a near-miss just
    because an unrelated stray binding also exists in another env.

    Fail-open like every other side-channel write on this ingest path (mirrors
    `_dispatch_lineage_refresh`'s explicit try/except around `send_task`): this is
    a bonus diagnostic, not the ingestion itself, and both callers reach here
    AFTER the pipeline_run row is already durably upserted+committed. A DB error
    on the `workspace_health` write (contention, a transient connection drop) must
    never propagate — for `ingest_event` that would 500 a webhook that DataQ has
    already correctly processed (ADR 0006 says ack well-formed events, storming
    the caller's retry logic for nothing); for `ingest_polled_runs` it would abort
    the whole poll batch mid-loop, silently dropping every *other* pipeline_run
    (and any trigger) the same poll cycle would otherwise have recorded.

    The log line is throttled to the FIRST time a tuple is recorded, not every
    occurrence: a persistently misconfigured pipeline succeeds (and re-triggers
    this check) every poll cycle, and logging WARNING on every one of those is
    exactly the log-amplification shape #852 already burned this codebase on —
    the ongoing-ness is still provable from the `workspace_health` row's
    `updated_at`, which the (deduped) DB write bumps every time regardless.
    """
    mismatched_envs = sorted(
        set(
            session.scalars(
                select(TriggerBinding.env).where(
                    TriggerBinding.provider == provider,
                    TriggerBinding.pipeline_or_dag_id == update.pipeline_or_dag_id,
                    TriggerBinding.enabled.is_(True),
                    TriggerBinding.env != connection.env,
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
            # The row write failed (or the session's transaction is now aborted) —
            # roll back so the caller's session is usable again, and never let a
            # diagnostic-only failure break suite triggering itself. Always warn
            # here regardless of first-vs-repeat: a write FAILURE is not the
            # steady-state noise the throttle above guards against.
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
    if not bindings:
        _record_env_near_misses(session, provider=provider, connection=connection, update=update)
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


def _dispatch_lineage_refresh(
    *, provider_impl: OrchestrationProvider, connection: Connection, update: RunUpdate
) -> None:
    """Dispatch the async dbt-manifest lineage refresh for a succeeded run.

    Provider-agnostic (CLAUDE.md §11): probes for the OPTIONAL ``read_manifest``
    capability via ``getattr`` — only the dbt provider has it, so ADF/Airflow are a
    no-op with zero name branching. Rather than fetch+parse+refresh **inline** (the
    webhook path runs in the request threadpool — artifact download + parse + N+M
    upserts would block the ACK and exhaust the pool), it enqueues the
    ``refresh_dbt_lineage`` Celery task (own session, own single secret fetch in the
    worker); mirrors how `run_dispatch` publishes ``run_suite`` by name.

    Fail-open: a broker hiccup must NEVER affect run ingestion or suite triggering —
    it logs and returns (the 10-min poll re-dispatches on the next success).
    """
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

    A succeeded run also enqueues the async dbt-manifest lineage refresh (the
    poll-path fallback to the webhook's immediate re-read), **deduped per job** for
    this batch: a poll can surface several succeeded runs of the same job, but the
    manifest is per-job (one refresh suffices).
    """
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


# ── read model (PR-C0b: the pipeline-runs monitoring feed) ───────────────────
# `pipeline_runs` is orchestration *monitoring*, not suite-scoped data — it has
# no `suite_id` and no share rows — so the feed is gated on authentication only
# (any signed-in user), unlike the suite-scoped `runs`/`results` reads. The link
# back to a DQ run is the `triggered_by` marker on `runs`, not a column here.


def pipeline_run_order_by() -> tuple[Any, ...]:
    """The newest-first ordering for the pipeline-run feed — **total**, by design.

    Exposed as a function rather than inlined so the totality invariant can be
    asserted directly. It cannot be pinned behaviourally: with tied timestamps
    Postgres is *free* to return any order but not *required* to vary, so a test
    that pages tied rows and checks for duplicates passes on the unfixed code
    roughly whenever the planner happens to be stable — a coin flip, which is the
    #948 tie-break lesson exactly. So the test asserts the ordering contains a
    unique column instead of hoping the database misbehaves on cue.
    """
    return (PipelineRun.created_at.desc(), PipelineRun.id.desc())


def _pipeline_run_filters(*, provider: str | None, status: str | None) -> list[Any]:
    """The ONE `WHERE` chain shared by :func:`list_pipeline_runs` and
    :func:`count_pipeline_runs`. Derived once rather than hand-rolled twice, so a
    future filter cannot land on the list without the total — which would make
    `X-Total-Count` quietly disagree with the page it describes (#1108)."""
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
    """Monitored orchestrator pipeline/DAG runs, newest first, with paging (#928).

    Optionally filtered by ``provider`` and/or ``status``.

    **The `id` tie-break is load-bearing, not tidiness.** `created_at` alone is not
    a total order — the poll ingests a batch inside one transaction, so Postgres'
    transaction-scoped `now()` gives every row in that batch an identical
    timestamp. Under `LIMIT/OFFSET` a non-total order lets the database return
    tied rows in any order per query, so the same row can appear on two pages
    while another is never returned at all. Paging without this is worse than no
    paging: it looks complete and silently isn't (the same nondeterminism #889
    fixed for latest-run-per-suite).
    """
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
    :func:`list_pipeline_runs`, unaffected by its `limit`/`offset` (#1108 —
    the `/assets` `X-Total-Count` shape: a page shorter than `limit` can't by
    itself distinguish "that's everything" from "there's more"). Shares
    :func:`_pipeline_run_filters` with the list so the two cannot drift."""
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


def list_env_near_misses(
    session: Session,
    *,
    user_id: uuid.UUID,
    include_all: bool = False,
    suite_id: uuid.UUID | None = None,
) -> list[workspace_health_service.NearMissRecord]:
    """Current #1186 env-mismatch near-misses (#1199) — thin pass-through to
    `workspace_health_service.list_current_env_near_misses`, kept here so the API
    layer reaches orchestration reads through this module like every other
    orchestration read (`list_pipeline_runs`, `list_pipelines`), not by importing
    `workspace_health_service` directly.

    Unlike those two, this read is **suite-scoped**: a near-miss is derived from
    `trigger_binding` rows, which are suite-owned config (`pipeline_runs` are not),
    so it obeys the same owned-or-shared rule `GET /trigger-bindings` does.
    """
    return workspace_health_service.list_current_env_near_misses(
        session, user_id=user_id, include_all=include_all, suite_id=suite_id
    )


# ─────────────────────────── poll health (#828) ────────────────────────────
#
# A poll that fails every 10 minutes used to be visible only in the logs. These two
# functions make the outcome a fact about the connection, so the product can say "this
# integration is broken" instead of rendering a clean empty state over a dead one.


# A poll's health bookkeeping takes a ROW LOCK (#837, so two overlapping sweeps can't both
# fire the same alert). The mechanism — bounded wait, one retry, give up rather than block
# a shared beat task (#854/#855) — lives in `services/connection_lock.py`, because the
# inventory sync (#1104) needs the identical read-modify-write guard on the same table and
# a second implementation would be a second set of bugs.


def record_poll_success(session: Session, *, connection: Connection) -> int:
    """Mark a connection's poll healthy: clear the error, reset the failure streak.

    Returns the streak it just cleared, so the caller can tell a *recovery* (we had been
    failing) from a poll that was healthy all along and alert only on the transition
    (#837). The decision is the caller's — this function stays a pure state write.

    Row-locked for the same reason as `record_poll_failure`: three schedules sweep the
    same connections (the 10-min poll beat, the 30-min gap-recovery beat, and the #492
    poll-now), so two can recover the same connection concurrently. Without the lock both
    would read the pre-recovery streak and both would fire a recovery alert; with it, the
    second reads the already-cleared 0 and stays quiet.
    """
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
    """Record a failed poll against the connection and grow its failure streak.

    Returns the new streak length (0 when the connection has been deleted mid-sweep), so
    the caller can alert exactly on the threshold crossing (#837).

    Takes a ``connection_id`` rather than the ORM object on purpose: the caller has just
    rolled the session back, so its `Connection` instance is detached/stale. Re-loading
    inside a fresh transaction is the only safe way to read-modify-write here.

    Row-locked (``with_for_update``) because this is a read-modify-write on a counter that
    an alert threshold rides on, and **three** schedules sweep the same connections: the
    10-min poll beat, the 30-min gap-recovery beat, and the #492 alert-triggered poll-now.
    Two overlapping sweeps would otherwise both read N, both write N+1 (a lost update),
    and — since each then sees the streak *equal* the threshold — both fire the alert. A
    duplicate alert on the feature whose entire point is "don't storm the channel" is not
    an acceptable race, and the streak would also under-count the outage the UI reports.

    The stored reason is **classified**, never the raw exception text — a transport error
    routinely carries the thing that failed to authenticate (a SAS query string, a DSN, a
    bearer token). `classify_failure_reason` is the same redaction-safe path a failed run
    uses (#605), so a leaked credential can't reach the API through this column.
    """
    connection = _lock_connection(session, connection_id)
    if connection is None:  # deleted mid-sweep, or the row is contended — either way, move on
        return 0
    connection.last_polled_at = datetime.now(UTC)
    connection.last_poll_error = classify_failure_reason(exc)[:512]
    connection.consecutive_poll_failures = (connection.consecutive_poll_failures or 0) + 1
    session.commit()
    return connection.consecutive_poll_failures
