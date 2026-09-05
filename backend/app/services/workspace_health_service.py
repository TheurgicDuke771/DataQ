"""Workspace-wide orchestration-poll staleness — the signal that cannot lie (#1052)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    HEALTH_FAILING,
    HEALTH_RECOVERED,
    AlertUndeliverableError,
    PollStalenessReport,
)
from backend.app.alerting.registry import get_health_publisher
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    ENVS,
    ORCHESTRATION_PROVIDERS,
    Connection,
    TriggerBinding,
    WorkspaceHealth,
)
from backend.app.services import credential_health
from backend.app.services.failure_classifier import classify_broker_reason
from backend.app.services.suite_service import accessible_suite_ids
from backend.app.worker.celery_app import POLL_ORCHESTRATION_INTERVAL_S

log = get_logger(__name__)

#: The `workspace_health.key` this signal owns.
POLL_STALENESS_KEY = "orchestration_poll_staleness"

#: Prefix for the #1186 env-mismatch near-miss dedupe keys (see
#: `record_trigger_binding_env_near_miss` below).
_NEAR_MISS_KEY_PREFIX = "trigger_env_near_miss"


def evaluate_poll_staleness(
    session: Session, *, now: datetime | None = None
) -> tuple[bool, PollStalenessReport]:
    """Pure decision: is the workspace's polling loop stale, and the report to say so."""
    settings = get_settings()
    threshold = settings.poll_staleness_alert_after_s
    moment = now or datetime.now(UTC)
    count, most_recent_poll, reference = session.execute(
        select(
            func.count(),
            func.max(Connection.last_polled_at),
            func.max(func.coalesce(Connection.last_polled_at, Connection.created_at)),
        ).where(Connection.type.in_(ORCHESTRATION_PROVIDERS))
    ).one()
    stale = bool(
        threshold > 0
        and count
        and reference is not None
        and reference < moment - timedelta(seconds=threshold)
    )
    report = PollStalenessReport(
        state=HEALTH_FAILING,
        connection_count=int(count or 0),
        most_recent_polled_at=most_recent_poll,
        threshold_seconds=threshold,
    )
    return stale, report


def run_poll_staleness_check(session: Session, *, now: datetime | None = None) -> str:
    """One tick of the API-side staleness check; returns the outcome for logs/tests."""
    if get_settings().poll_staleness_alert_after_s <= 0:
        return "disabled"

    # Claim the signal row (creating it on first use).
    _ensure_row(session)
    flag = session.execute(
        select(WorkspaceHealth)
        .where(WorkspaceHealth.key == POLL_STALENESS_KEY)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if flag is None:
        session.rollback()
        return "skipped"

    stale, report = evaluate_poll_staleness(session, now=now)
    outstanding = flag.alerted_at is not None

    if stale and not outstanding:
        try:
            get_health_publisher().publish_poll_staleness(session, report)
        except AlertUndeliverableError:
            # No channel configured — nothing was sent, so the flag stays unset and every later tick
            # retries; the moment an operator wires a channel.
            session.rollback()
            log.warning(
                "workspace_poll_staleness_undeliverable",
                connection_count=report.connection_count,
                threshold_s=report.threshold_seconds,
            )
            return "undeliverable"
        flag.alerted_at = now or datetime.now(UTC)
        session.commit()
        log.warning(
            "workspace_poll_staleness_alerted",
            connection_count=report.connection_count,
            most_recent_polled_at=(
                report.most_recent_polled_at.isoformat() if report.most_recent_polled_at else None
            ),
            threshold_s=report.threshold_seconds,
        )
        return "alerted"

    if not stale and outstanding:
        recovery = PollStalenessReport(
            state=HEALTH_RECOVERED,
            connection_count=report.connection_count,
            most_recent_polled_at=report.most_recent_polled_at,
            threshold_seconds=report.threshold_seconds,
        )
        try:
            get_health_publisher().publish_poll_staleness(session, recovery)
        except AlertUndeliverableError:
            # Channels got UNconfigured while an alert was outstanding.
            session.rollback()
            log.warning("workspace_poll_staleness_recovery_undeliverable")
            return "undeliverable"
        flag.alerted_at = None
        session.commit()
        log.info("workspace_poll_staleness_recovered", connection_count=report.connection_count)
        return "recovered"

    session.rollback()  # release the row lock; nothing to record
    return "ok"


def _ensure_row(session: Session) -> None:
    """Create the signal row if absent (idempotent, race-safe via ON CONFLICT)."""
    session.execute(
        pg_insert(WorkspaceHealth)
        .values(key=POLL_STALENESS_KEY)
        .on_conflict_do_nothing(index_elements=[WorkspaceHealth.key])
    )


# ─────────────── trigger-binding env near-miss (#1186) ───────────────
#
# A sibling signal to the poll-staleness one above, on the same `workspace_health`
# table but a DIFFERENT shape: this is not a delivered-alert flag (no publisher, no
# #843 delivered-first bookkeeping) — it is a lightweight, DB-visible "this
# mismatch is still happening" marker, upserted every time the ingest path
# (`orchestration_service._trigger_suites`) observes a succeeded pipeline/DAG run
# whose (provider, pipeline_or_dag_id) matches an ENABLED binding but whose env
# does not. The live incident (#1186): two Airflow connections shared one
# `base_url` across envs, so runs kept attributing to "qa" while the binding was
# scoped to "dev" — the binding was silently dead on arrival and nothing but a
# structlog line said so.


def near_miss_partner_envs(known_env: str) -> list[str]:
    """The `ENVS` values that pair with `known_env` to form a near-miss (#1247).

    This is the ONE definition of tuple eligibility: the write side
    (`orchestration_service._record_env_near_misses`) calls it with the run's actual
    env to find which binding envs mismatch it, and `list_current_env_near_misses`
    below calls it with a binding's configured env to find which run envs would. A
    single function means the two sides cannot independently drift on "which envs
    count as a mismatch" the way two hand-written loops could.
    """
    return [env for env in ENVS if env != known_env]


def _near_miss_key(
    *, provider: str, pipeline_or_dag_id: str, run_env: str, binding_env: str
) -> str:
    """Deterministic, length-bounded `workspace_health.key` for one near-miss tuple."""
    digest = hashlib.sha256(
        f"{provider}|{pipeline_or_dag_id}|{run_env}|{binding_env}".encode()
    ).hexdigest()[:16]
    return f"{_NEAR_MISS_KEY_PREFIX}:{digest}"


def record_trigger_binding_env_near_miss(
    session: Session,
    *,
    provider: str,
    pipeline_or_dag_id: str,
    run_env: str,
    binding_env: str,
) -> bool:
    """Upsert the dedupe marker for one (provider, dag, run_env, binding_env) near-miss."""
    key = _near_miss_key(
        provider=provider,
        pipeline_or_dag_id=pipeline_or_dag_id,
        run_env=run_env,
        binding_env=binding_env,
    )
    result = session.execute(
        pg_insert(WorkspaceHealth)
        .values(key=key)
        .on_conflict_do_update(
            index_elements=[WorkspaceHealth.key],
            set_={"updated_at": func.now()},
        )
        .returning(text("xmax = 0"))
    )
    was_first_insert = bool(result.scalar_one())
    session.commit()
    return was_first_insert


# ─────────────── trigger-binding env near-miss — read side (#1199) ───────────────
#
# `record_trigger_binding_env_near_miss` above is write-only by design: the row's
# `key` is a hash (the identifying tuple can run to 256 chars — Airflow DAG ids —
# against a 64-char column) and there is no sibling detail column, so nothing can
# SELECT a tuple back out of the table directly. #1199 is exactly that gap: the
# signal was recorded but nothing could read it back except `psql`.


@dataclass(frozen=True)
class NearMissRecord:
    """One decoded, currently-active #1186 env-mismatch tuple."""

    provider: str
    pipeline_or_dag_id: str
    run_env: str
    binding_env: str
    updated_at: datetime


def list_current_env_near_misses(
    session: Session,
    *,
    user_id: uuid.UUID,
    include_all: bool = False,
    suite_id: uuid.UUID | None = None,
    since_hours: int | None = None,
) -> list[NearMissRecord]:
    """Decode current near-miss rows back to `(provider, pipeline_or_dag_id, run_env,
    binding_env, updated_at)`, newest first.
    """
    window_hours = (
        get_settings().trigger_env_near_miss_recent_hours if since_hours is None else since_hours
    )
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

    binding_stmt = (
        select(TriggerBinding.provider, TriggerBinding.pipeline_or_dag_id, TriggerBinding.env)
        .where(
            TriggerBinding.enabled.is_(True),
            TriggerBinding.suite_id.in_(accessible_suite_ids(user_id, include_all=include_all)),
        )
        .distinct()
    )
    if suite_id is not None:
        binding_stmt = binding_stmt.where(TriggerBinding.suite_id == suite_id)
    enabled_bindings = session.execute(binding_stmt).all()

    # key -> the tuple it was derived from, so a hit can be decoded back.
    candidates: dict[str, tuple[str, str, str, str]] = {}
    for provider, pipeline_or_dag_id, binding_env in enabled_bindings:
        for run_env in near_miss_partner_envs(binding_env):
            key = _near_miss_key(
                provider=provider,
                pipeline_or_dag_id=pipeline_or_dag_id,
                run_env=run_env,
                binding_env=binding_env,
            )
            candidates[key] = (provider, pipeline_or_dag_id, run_env, binding_env)

    if not candidates:
        return []

    rows = session.execute(
        select(WorkspaceHealth.key, WorkspaceHealth.updated_at).where(
            WorkspaceHealth.key.in_(candidates.keys()),
            WorkspaceHealth.updated_at >= cutoff,
        )
    ).all()

    records = [
        NearMissRecord(
            provider=candidates[key][0],
            pipeline_or_dag_id=candidates[key][1],
            run_env=candidates[key][2],
            binding_env=candidates[key][3],
            updated_at=updated_at,
        )
        for key, updated_at in rows
    ]
    # Ordered in Python, not SQL: the fetched rows are keyed by an opaque hash, so the fields worth
    # ordering on only exist after the decode above.
    records.sort(
        key=lambda r: (
            -r.updated_at.timestamp(),
            r.provider,
            r.pipeline_or_dag_id,
            r.binding_env,
            r.run_env,
        )
    )
    return records


# ─────────────── admin health read API (#1885) ───────────────
#
# `GET /admin/health` reads three independent signals: per-connection poll staleness (below,
# from the same `last_polled_at` #828 already maintains), the beat heartbeat (a second,
# DB-visible echo of the Redis tick `beat_watchdog` records for the in-process watchdog, #904),
# and broker (Redis) queue depth. Honesty rule throughout: a connection/beat never observed
# reads `unknown`/`not_monitored`, never healthy.

#: The `workspace_health.key` the beat-heartbeat task upserts.
BEAT_HEARTBEAT_KEY = "beat_heartbeat"


def record_beat_heartbeat(session: Session) -> None:
    """Upsert the `workspace_health` row the admin health API reads as the beat tick."""
    session.execute(
        pg_insert(WorkspaceHealth)
        .values(key=BEAT_HEARTBEAT_KEY)
        .on_conflict_do_update(
            index_elements=[WorkspaceHealth.key],
            set_={"updated_at": func.now()},
        )
    )
    session.commit()


def read_beat_heartbeat(session: Session) -> datetime | None:
    """The last time the beat-heartbeat task ran, or `None` if it never has."""
    return session.execute(
        select(WorkspaceHealth.updated_at).where(WorkspaceHealth.key == BEAT_HEARTBEAT_KEY)
    ).scalar_one_or_none()


def beat_health_status(
    last_tick_at: datetime | None, *, now: datetime, stale_after_s: int
) -> Literal["alive", "stale", "not_monitored"]:
    """Pure decision: `not_monitored` when the heartbeat has never run, never `alive`."""
    if last_tick_at is None:
        return "not_monitored"
    if stale_after_s > 0 and last_tick_at < now - timedelta(seconds=stale_after_s):
        return "stale"
    return "alive"


def poll_health_status(
    *,
    last_polled_at: datetime | None,
    consecutive_poll_failures: int,
    now: datetime,
    threshold_seconds: int,
) -> Literal["on_cadence", "stalled", "failing", "unknown"]:
    """Pure decision, mirroring `evaluate_poll_staleness`'s honesty rule (#828): a connection
    never polled reads `unknown`, never healthy. `last_polled_at` is stamped by BOTH
    `record_poll_success` and `record_poll_failure` — it is the last ATTEMPT, not the last
    success — so a connection failing every attempt would otherwise read `on_cadence` with a
    fresh timestamp; `consecutive_poll_failures` is what actually says the polling loop is
    unhealthy, and it takes priority over the time-based `stalled` check.
    """
    if last_polled_at is None:
        return "unknown"
    if consecutive_poll_failures > 0:
        return "failing"
    if threshold_seconds > 0 and last_polled_at < now - timedelta(seconds=threshold_seconds):
        return "stalled"
    return "on_cadence"


@dataclass(frozen=True)
class PollHealthRow:
    """One orchestration connection's poll staleness, for the admin health read API.

    `last_polled_at` is the last ATTEMPT (success or failure) — the model has no separate
    last-success timestamp, so none is fabricated here. `last_error` is the already-classified,
    secret-free reason (`Connection.last_poll_error`), present only while `status="failing"`.
    """

    connection_id: uuid.UUID
    name: str
    provider: str
    last_polled_at: datetime | None
    cadence_seconds: int
    next_expected_at: datetime | None
    status: Literal["on_cadence", "stalled", "failing", "unknown"]
    last_error: str | None


def list_poll_health(session: Session, *, now: datetime | None = None) -> list[PollHealthRow]:
    """Per-connection poll staleness, at the fixed workspace-wide poll cadence
    (`POLL_ORCHESTRATION_INTERVAL_S`) — every orchestration connection is polled on the same
    beat schedule, so there is no per-connection cadence to read.
    """
    moment = now or datetime.now(UTC)
    threshold = get_settings().poll_staleness_alert_after_s
    cadence = int(POLL_ORCHESTRATION_INTERVAL_S)
    rows = session.execute(
        select(
            Connection.id,
            Connection.name,
            Connection.type,
            Connection.last_polled_at,
            Connection.consecutive_poll_failures,
            Connection.last_poll_error,
        )
        .where(Connection.type.in_(ORCHESTRATION_PROVIDERS))
        .order_by(Connection.name)
    ).all()
    result: list[PollHealthRow] = []
    for connection_id, name, provider, last_polled_at, failures, last_poll_error in rows:
        failures = failures or 0
        next_expected = (
            last_polled_at + timedelta(seconds=cadence) if last_polled_at is not None else None
        )
        status = poll_health_status(
            last_polled_at=last_polled_at,
            consecutive_poll_failures=failures,
            now=moment,
            threshold_seconds=threshold,
        )
        result.append(
            PollHealthRow(
                connection_id=connection_id,
                name=name,
                provider=provider,
                last_polled_at=last_polled_at,
                cadence_seconds=cadence,
                next_expected_at=next_expected,
                status=status,
                last_error=last_poll_error if status == "failing" else None,
            )
        )
    return result


@dataclass(frozen=True)
class QueueDepthRow:
    """One broker queue's current length."""

    name: str
    depth: int


#: Bounded so an unreachable broker fails fast into the "unavailable" branch rather than
#: hanging the request (same rationale as `beat_watchdog.build_store`, #854).
_BROKER_TIMEOUT_S = 2.0


def broker_queue_depths(
    redis_url: str, queue_names: Sequence[str]
) -> tuple[list[QueueDepthRow] | None, str | None]:
    """`LLEN` per queue — `None` + a classified reason on ANY broker failure, never a fake `0`."""
    try:
        import redis

        # Context manager: closes the pool's connection(s) back to the OS on exit rather than
        # relying on GC, since this is a fresh client built per request.
        with redis.from_url(
            redis_url,
            socket_connect_timeout=_BROKER_TIMEOUT_S,
            socket_timeout=_BROKER_TIMEOUT_S,
        ) as client:
            depths = [QueueDepthRow(name=q, depth=int(client.llen(q))) for q in queue_names]
        return depths, None
    except Exception as exc:  # broad: any broker/library failure must read as "unavailable"
        log.warning("admin_health_queue_depth_failed", exc_info=True)
        return None, classify_broker_reason(exc)


# ─────────────── datasource credential health, workspace-wide (#1697) ───────────────


@dataclass(frozen=True)
class CredentialHealthRow:
    """One datasource connection's credential health for the admin Overview feed."""

    connection_id: uuid.UUID
    name: str
    type: str
    env: str
    status: credential_health.CredentialStatus
    consecutive_auth_failures: int
    last_auth_failure_at: datetime | None
    last_auth_success_at: datetime | None
    last_error: str | None


def list_credential_health(session: Session) -> list[CredentialHealthRow]:
    """Every DATASOURCE connection's credential health, worst first (#1697).

    Admin-wide and unscoped, like the sibling poll rows: the question this answers is
    "is anything in the workspace broken", and a connection is workspace-level state.
    Orchestration providers are excluded — theirs is the poll signal (#828).
    """
    rows = session.scalars(
        select(Connection)
        .where(Connection.type.not_in(ORCHESTRATION_PROVIDERS))
        .order_by(Connection.name, Connection.env)
    ).all()
    out = [
        CredentialHealthRow(
            connection_id=conn.id,
            name=conn.name,
            type=conn.type,
            env=conn.env,
            status=credential_health.credential_status(conn),
            consecutive_auth_failures=conn.consecutive_auth_failures or 0,
            last_auth_failure_at=conn.last_auth_failure_at,
            last_auth_success_at=conn.last_auth_success_at,
            last_error=conn.last_auth_error,
        )
        for conn in rows
    ]
    # Failing first, then unknown, then healthy — an admin page is read top-down, and the
    # rows that need action must not sit below the ones that do not.
    order = {"failing": 0, "unknown": 1, "healthy": 2}
    out.sort(key=lambda r: (order.get(r.status, 3), r.name, r.env))
    return out
