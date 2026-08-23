"""Workspace-wide orchestration-poll staleness — the signal that cannot lie (#1052)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
from backend.app.services.suite_service import accessible_suite_ids

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
        for run_env in ENVS:
            if run_env == binding_env:
                continue  # not a mismatch — the binding's own env
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
