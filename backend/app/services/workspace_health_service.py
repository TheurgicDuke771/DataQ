"""Workspace-wide orchestration-poll staleness — the signal that cannot lie (#1052).

Every incident in the #905 class (#852 exporter starvation, #854 unbounded row-lock
wait, the 2026-07-18 wedged broker reconnect) had the same shape: **the worker looked
alive and wrote nothing**. A per-connection health edge (#837/#996) is computed from
state the worker itself writes, so it structurally cannot fire when the worker is the
thing that died. The DB is the only party that can tell: if ``max(last_polled_at)``
across ALL orchestration connections is older than a few poll intervals, the polling
loop is dead regardless of cause.

This module therefore runs from the **API process** (a lifespan loop in ``main.py``),
never the worker — a check that lives in the process it monitors inherits the failure
it exists to detect. Delivery reuses the ``HealthPublisher`` seam and the #843
delivered-first rule via a ``workspace_health`` row (no parallel mechanism): the
FAILING edge is recorded only after a publish actually succeeded, the RECOVERED edge
only fires when a FAILING one was delivered, and the row is claimed
``FOR UPDATE SKIP LOCKED`` so two API replicas never double-send.
"""

from __future__ import annotations

import hashlib
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

log = get_logger(__name__)

#: The `workspace_health.key` this signal owns.
POLL_STALENESS_KEY = "orchestration_poll_staleness"

#: Prefix for the #1186 env-mismatch near-miss dedupe keys (see
#: `record_trigger_binding_env_near_miss` below).
_NEAR_MISS_KEY_PREFIX = "trigger_env_near_miss"


def evaluate_poll_staleness(
    session: Session, *, now: datetime | None = None
) -> tuple[bool, PollStalenessReport]:
    """Pure decision: is the workspace's polling loop stale, and the report to say so.

    Returns ``(stale, report)`` — the report carries the FAILING state; the caller
    flips it to RECOVERED for the recovery edge. The workspace reference moment is
    the **MAX** over connections of ``last_polled_at`` (falling back to
    ``created_at`` for a connection never polled at all — wrong image, task never
    registered — so "we have not looked yet" cannot read as "nothing to report",
    #828). MAX is deliberate: this signal fires when the *whole loop* is dead —
    i.e. when even the freshest activity across the fleet has aged out — not when
    one connection lags (that is the per-connection #837 edge's job). One recently
    created connection therefore masks the fleet for at most one threshold window.

    No orchestration connections ⇒ not stale (nothing to poll is not a dead loop).
    """
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
    """One tick of the API-side staleness check; returns the outcome for logs/tests.

    Outcomes: ``disabled`` · ``skipped`` (another replica holds the claim) ·
    ``ok`` (nothing to say) · ``alerted`` · ``recovered`` · ``undeliverable``
    (an edge was due but reached no channel — flag untouched, retried next tick).

    #843 delivered-first, both edges: ``alerted_at`` is written only **after**
    ``publish_poll_staleness`` returned (the composite raises when every channel
    failed, so a total delivery failure leaves the flag unset and the next tick
    retries); the RECOVERED edge fires only when a FAILING edge was actually
    delivered, and clears the flag the same way.
    """
    if get_settings().poll_staleness_alert_after_s <= 0:
        return "disabled"

    # Claim the signal row (creating it on first use). SKIP LOCKED: with N API
    # replicas each running this loop, one claims, the rest skip the tick — the
    # cadence is minutes, so a skipped tick costs nothing and can never double-send.
    #
    # The lock is then deliberately held ACROSS the synchronous publish below —
    # the #842 shape that the per-connection path eliminated by handing the send
    # to a Celery task. That hand-off is structurally unavailable here: the
    # worker is the process whose deadness this signal reports, so a
    # worker-dispatched alert never fires during the exact incident it exists
    # for. Holding the lock is safe in THIS one spot because the row is
    # dedicated (no other query touches workspace_health), contenders skip
    # rather than queue, the loop runs off the request path, and the hold is
    # bounded by the channels' own HTTP/SMTP timeouts.
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
            # No channel configured — nothing was sent, so the flag stays unset
            # and every later tick retries; the moment an operator wires a
            # channel, the still-outstanding edge goes out (review finding: a
            # fresh install stamping the flag silently would bury the incident).
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
            # Channels got UNconfigured while an alert was outstanding. Leave the
            # flag set: the operator was told about a failure and has not been
            # told it recovered — clearing silently would strand a stale alarm
            # as the last delivered word.
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
    """Deterministic, length-bounded `workspace_health.key` for one near-miss tuple.

    `pipeline_or_dag_id` runs up to 256 chars (Airflow DAG ids), but
    `workspace_health.key` is capped at 64 — so the identifying tuple is hashed
    rather than concatenated raw. The row exists purely as a DB-visible
    dedupe/last-seen marker; the human-readable detail (provider/dag/envs) lives
    on the paired `trigger_binding_env_near_miss` log line emitted alongside it.
    """
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
    """Upsert the dedupe marker for one (provider, dag, run_env, binding_env) near-miss.

    One row per distinct tuple: a repeated near-miss (e.g. the 10-min poll
    re-observing the same stuck env mismatch) bumps `updated_at` in place rather
    than growing the table — mirroring `_ensure_row`'s upsert shape for
    `POLL_STALENESS_KEY`, just with an `ON CONFLICT DO UPDATE` instead of
    `DO NOTHING` so "last seen" stays current. Commits its own transaction (the
    caller — mid-ingest — has already committed the pipeline_run write, so this
    is a small, isolated write, same discipline as `_upsert_pipeline_run`).

    Returns whether this call was the FIRST time this tuple was recorded (a
    genuine ``INSERT``) as opposed to a repeat (``UPDATE`` via the conflict arm)
    — the `(xmax = 0)` idiom on the returned row is the standard Postgres way to
    tell the two apart from an `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`.
    The DB row itself already dedupes via the upsert; this return value additionally
    lets the caller dedupe ITS OWN log line, so a persistently misconfigured
    pipeline that succeeds every 10 minutes doesn't warn every 10 minutes forever
    (the #852 log-amplification lesson) — the row's `updated_at` still proves the
    mismatch is ongoing without a matching log line on every occurrence.
    """
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
    session: Session, *, since_hours: int | None = None
) -> list[NearMissRecord]:
    """Decode current near-miss rows back to `(provider, pipeline_or_dag_id, run_env,
    binding_env, updated_at)`, newest first.

    Since the stored key is an opaque hash, this re-derives CANDIDATE tuples the
    same way `record_trigger_binding_env_near_miss`'s caller
    (`orchestration_service._record_env_near_misses`) would have produced them:
    every ENABLED `trigger_binding`'s `(provider, pipeline_or_dag_id, env)`,
    crossed with every OTHER value in the closed `ENVS` vocabulary as a candidate
    `run_env` (a pipeline run is always attributed to *some* orchestration
    connection's env, and `ENVS` is the closed set every connection's `env` is
    drawn from). Each candidate tuple is hashed with the exact same `_near_miss_key`
    the write side uses, and only the hashes that actually exist as a row — i.e.
    were actually recorded by a real mismatched ingest event, not merely
    hypothesised here — come back. `ENVS` has 4 members, so this is
    O(enabled bindings x 3) hash computations, not a table scan.

    A binding that has since been deleted or re-pointed to the correct env no
    longer contributes candidates, so its old near-miss row (if any) simply can't
    be found here — it ages out of view without needing its own cleanup.

    `since_hours` bounds "current": a row whose `updated_at` has aged past the
    window is excluded — the mismatch may have gone quiet (fixed, or the pipeline
    stopped running) since it was last recorded, so it should read as resolved
    rather than an ongoing incident. Defaults to
    `settings.trigger_env_near_miss_recent_hours`.
    """
    window_hours = (
        get_settings().trigger_env_near_miss_recent_hours if since_hours is None else since_hours
    )
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)

    enabled_bindings = session.execute(
        select(TriggerBinding.provider, TriggerBinding.pipeline_or_dag_id, TriggerBinding.env)
        .where(TriggerBinding.enabled.is_(True))
        .distinct()
    ).all()

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
    records.sort(key=lambda r: r.updated_at, reverse=True)
    return records
