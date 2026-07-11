"""Incident lifecycle engine + read model (ADR 0034 decision 4, #761).

An **alert** is a per-result notification (fire-and-forget; severity routing,
dedup, snooze — already shipped). An **incident** is the stateful object those
signals roll up into, anchored to ``(asset_id, check_id)``:

* a **breaching** result (warn/fail/critical) *opens* an incident if none is
  active for the pair, else *attaches an occurrence* (``occurrence_count`` +
  ``last_seen_at`` + refreshed evidence) — never a duplicate;
* the first **passing** result *auto-resolves* the active incident (per-suite
  configurable, default on); a manual ack/resolve via the API always wins;
* a resolved pair's next breach opens a **new** incident linked to the prior one
  (``prior_incident_id``) — a resolved row is never mutated back to open.

**Dedup is upsert-race-safe (the #420 discipline, one level up from alert dedup):**
opening is an ``INSERT … ON CONFLICT DO NOTHING`` against the partial unique index
``uq_incidents_active_asset_check`` — a concurrent second breaching result loses
the insert and falls through to the occurrence-attach path instead of racing in a
duplicate or raising an IntegrityError.

**The run hook is fail-soft** (``sync_incidents_for_run``): an incident-engine bug
must never fail an already-persisted run — the same contract as the sibling
``alerting.dispatch`` / ``lineage.dispatch`` hooks the worker calls next to it.

Visibility derives from suite grants (same rule as the asset view, #760 / ADR
0027): an incident is visible iff the caller can ``view`` its suite; a
workspace-admin sees all; anything outside the caller's grants is 404-no-leak.

The orphan-asset sweep (#770) never retires an asset with incident history:
``incidents.asset_id`` is registered in ``asset_service._SWEEP_REFERENCE_GUARDS``
(schema-introspection-enforced), because the FK is ``ON DELETE CASCADE`` and an
un-guarded sweep would silently drop incidents.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    FAILING_TIERS,
    INCIDENT_ACTIVE_STATUSES,
    Asset,
    Check,
    Incident,
    Result,
    Run,
    SuiteNotification,
)
from backend.app.services import suite_service
from backend.app.services.incident_evidence import build_evidence
from backend.app.services.run_service import list_results

log = get_logger(__name__)

# Mirrors the partial unique index predicate `uq_incidents_active_asset_check`
# (keep in sync with the model / migration). The ON CONFLICT target below points
# at that index via (index_elements, index_where).
_ACTIVE_INCIDENT_PREDICATE = text(
    "status IN (" + ", ".join(f"'{s}'" for s in INCIDENT_ACTIVE_STATUSES) + ")"
)
# The clean/passing result status that auto-resolves an active incident.
_PASSING_RESULT = "pass"


def _now() -> datetime:
    return datetime.now(UTC)


class IncidentNotFoundError(DataQError):
    """The incident does not exist *or* is wholly outside the caller's grants — the
    two are indistinguishable by design (404-no-leak, same as the asset view)."""

    status_code = 404
    code = "incident_not_found"


class IncidentNotActiveError(DataQError):
    """A manual transition was requested on a resolved incident (already closed) —
    a 409, not a silent no-op (the caller is acting on stale state)."""

    status_code = 409
    code = "incident_not_active"


# ── the fail-soft run hook ────────────────────────────────────────────────────


def sync_incidents_for_run(session: Session, *, run_id: uuid.UUID) -> None:
    """Reconcile incidents from a terminal run's results — **never raises**.

    Called from the worker right after the run reaches a terminal state and
    before the alert dispatch (so the published report can reference the open
    incidents). Any failure is logged and swallowed: the run is already persisted,
    so an incident-engine hiccup must not fail the task or roll anything back.
    """
    try:
        _sync_incidents_for_run(session, run_id=run_id)
    except Exception:
        session.rollback()
        log.exception("incident_sync_failed", run_id=str(run_id))


def _sync_incidents_for_run(session: Session, *, run_id: uuid.UUID) -> None:
    run = session.get(Run, run_id)
    # Only executed runs carry per-check results. A run with no resolved asset
    # can't anchor an incident (fail-soft, mirrors Suite/Run.asset_id) → skip. An
    # *operationally-failed* run (status='failed', no result rows) has no
    # check-level signal to anchor to either — it still alerts (the always-alert
    # path) but opens no incident (the (asset, check) anchor needs a check).
    if run is None or run.status not in ("succeeded", "failed") or run.asset_id is None:
        return

    results = list_results(session, run.id)
    if not results:
        return
    asset = session.get(Asset, run.asset_id)
    auto_resolve = auto_resolve_enabled(session, run.suite_id)

    opened = attached = resolved = 0
    for result in results:
        if result.status in FAILING_TIERS:
            check = session.get(Check, result.check_id)
            _, action = open_or_attach_incident(
                session, run=run, result=result, check=check, asset=asset
            )
            opened += action == "opened"
            attached += action == "attached"
        elif result.status == _PASSING_RESULT and auto_resolve:
            # skip/error are operational (not a pass) — they neither open nor
            # resolve; only a genuine pass clears the pair.
            if _auto_resolve_active(session, asset_id=run.asset_id, check_id=result.check_id):
                resolved += 1

    session.commit()
    if opened or attached or resolved:
        log.info(
            "incidents_synced",
            run_id=str(run_id),
            suite_id=str(run.suite_id),
            opened=opened,
            attached=attached,
            auto_resolved=resolved,
        )


# ── lifecycle primitives ──────────────────────────────────────────────────────


def open_or_attach_incident(
    session: Session,
    *,
    run: Run,
    result: Result,
    check: Check | None,
    asset: Asset | None,
) -> tuple[Incident, str]:
    """Open a new incident for ``(run.asset_id, result.check_id)`` or attach an
    occurrence to the active one. Returns ``(incident, "opened"|"attached")``.

    Upsert-race-safe: the ``INSERT … ON CONFLICT DO NOTHING`` against the partial
    unique index means a concurrent second breaching result loses the insert and
    falls through to the attach path — exactly one active incident, occurrences
    counted. Does **not** commit (the caller batches the run's results into one
    transaction); the evidence card is (re)snapshotted here either way.
    """
    assert run.asset_id is not None  # guarded by the caller (_sync / anchor rule)
    evidence = build_evidence(session, run=run, result=result, check=check, asset=asset)
    now = _now()
    prior_id = _most_recent_resolved_id(session, asset_id=run.asset_id, check_id=result.check_id)
    new_id = session.execute(
        pg_insert(Incident)
        .values(
            asset_id=run.asset_id,
            check_id=result.check_id,
            suite_id=run.suite_id,
            status="open",
            occurrence_count=1,
            last_seen_at=now,
            evidence=evidence,
            prior_incident_id=prior_id,
        )
        .on_conflict_do_nothing(
            index_elements=["asset_id", "check_id"],
            index_where=_ACTIVE_INCIDENT_PREDICATE,
        )
        .returning(Incident.id)
    ).scalar_one_or_none()

    if new_id is not None:
        incident = session.get(Incident, new_id)
        assert incident is not None  # just inserted in this transaction
        return incident, "opened"

    # Conflict → an active incident already exists (the winner's row, now visible
    # under READ COMMITTED since our insert blocked on its commit). Attach.
    active = _active_incident(session, asset_id=run.asset_id, check_id=result.check_id)
    if active is None:  # pragma: no cover — the conflicting row vanished mid-tx
        raise IncidentNotActiveError("incident vanished during attach")
    active.occurrence_count += 1
    active.last_seen_at = now
    active.evidence = evidence
    return active, "attached"


def _auto_resolve_active(session: Session, *, asset_id: uuid.UUID, check_id: uuid.UUID) -> bool:
    """Auto-resolve the active incident for the pair on a passing result. Returns
    whether one was resolved (``False`` when none is active — the common clean
    case). ``resolved_by='auto'``; no actor user."""
    active = _active_incident(session, asset_id=asset_id, check_id=check_id)
    if active is None:
        return False
    now = _now()
    active.status = "resolved"
    active.resolved_by = "auto"
    active.resolved_at = now
    return True


def acknowledge_incident(
    session: Session, incident: Incident, *, user_id: uuid.UUID, note: str | None = None
) -> Incident:
    """Acknowledge an incident (``open → acknowledged``), stamping actor + time.

    Idempotent on an already-acknowledged incident (records the newer actor/note);
    a **resolved** incident is a 409 (`IncidentNotActiveError`) — it is closed, and
    reopening is only ever a new incident (never a mutation back to active).
    """
    if incident.status == "resolved":
        raise IncidentNotActiveError(
            "cannot acknowledge a resolved incident", detail={"incident_id": str(incident.id)}
        )
    incident.status = "acknowledged"
    incident.acknowledged_at = _now()
    incident.acknowledged_by = user_id
    if note is not None:
        incident.acknowledge_note = note
    session.commit()
    session.refresh(incident)
    log.info("incident_acknowledged", incident_id=str(incident.id), user_id=str(user_id))
    return incident


def resolve_incident(
    session: Session, incident: Incident, *, user_id: uuid.UUID, note: str | None = None
) -> Incident:
    """Manually resolve an incident (``open|acknowledged → resolved``, ``resolved_by
    ='user'``). Manual wins over auto. A double-resolve is a 409."""
    if incident.status == "resolved":
        raise IncidentNotActiveError(
            "incident is already resolved", detail={"incident_id": str(incident.id)}
        )
    incident.status = "resolved"
    incident.resolved_by = "user"
    incident.resolved_by_user_id = user_id
    incident.resolved_at = _now()
    if note is not None:
        incident.resolution_note = note
    session.commit()
    session.refresh(incident)
    log.info("incident_resolved", incident_id=str(incident.id), user_id=str(user_id))
    return incident


# ── config ────────────────────────────────────────────────────────────────────


def auto_resolve_enabled(session: Session, suite_id: uuid.UUID) -> bool:
    """Whether the suite auto-resolves incidents on a passing result. Default
    **on** for a suite with no notification config row (matches the no-config
    alerting default)."""
    config = session.scalars(
        select(SuiteNotification).where(SuiteNotification.suite_id == suite_id)
    ).first()
    return config.auto_resolve_incidents if config is not None else True


# ── read model ────────────────────────────────────────────────────────────────


def _active_incident(
    session: Session, *, asset_id: uuid.UUID, check_id: uuid.UUID
) -> Incident | None:
    """The single active (open|acknowledged) incident for the pair, if any — the
    partial unique index guarantees at most one."""
    return session.scalars(
        select(Incident).where(
            Incident.asset_id == asset_id,
            Incident.check_id == check_id,
            Incident.status.in_(INCIDENT_ACTIVE_STATUSES),
        )
    ).first()


def _most_recent_resolved_id(
    session: Session, *, asset_id: uuid.UUID, check_id: uuid.UUID
) -> uuid.UUID | None:
    """The most-recently-resolved incident id for the pair (the reopen link), or
    ``None`` for a first-ever incident."""
    return session.scalars(
        select(Incident.id)
        .where(
            Incident.asset_id == asset_id,
            Incident.check_id == check_id,
            Incident.status == "resolved",
        )
        .order_by(Incident.resolved_at.desc().nullslast(), Incident.created_at.desc())
        .limit(1)
    ).first()


def active_incidents_for_run(session: Session, run: Run) -> dict[uuid.UUID, Incident]:
    """The active incidents on this run's asset keyed by ``check_id`` — the map the
    alert builder joins its failing checks against so a published report references
    the open incident. Empty when the run has no resolved asset."""
    if run.asset_id is None:
        return {}
    rows = session.scalars(
        select(Incident).where(
            Incident.asset_id == run.asset_id,
            Incident.suite_id == run.suite_id,
            Incident.status.in_(INCIDENT_ACTIVE_STATUSES),
        )
    )
    return {inc.check_id: inc for inc in rows}


def get_incident(session: Session, incident_id: uuid.UUID) -> Incident | None:
    """Fetch an incident by id (no authz — the API layer gates on its suite)."""
    return session.get(Incident, incident_id)


def list_incidents(
    session: Session,
    *,
    user_id: uuid.UUID,
    include_all: bool = False,
    asset_id: uuid.UUID | None = None,
    suite_id: uuid.UUID | None = None,
    state: str | None = None,
    limit: int = 100,
) -> list[Incident]:
    """Incidents on suites the caller can view, newest first (``created_at`` desc).

    Visibility derives from suite grants (ADR 0027 / #760): the accessible-suite
    subquery is always applied, so an ``asset_id``/``suite_id`` filter the caller
    can't see yields an empty list. ``include_all`` spans every suite (workspace
    admin). ``state`` narrows by lifecycle status.
    """
    accessible = suite_service.accessible_suite_ids(user_id, include_all=include_all)
    stmt = (
        select(Incident)
        .where(Incident.suite_id.in_(accessible))
        .order_by(Incident.created_at.desc())
        .limit(limit)
    )
    if asset_id is not None:
        stmt = stmt.where(Incident.asset_id == asset_id)
    if suite_id is not None:
        stmt = stmt.where(Incident.suite_id == suite_id)
    if state is not None:
        stmt = stmt.where(Incident.status == state)
    return list(session.scalars(stmt))
