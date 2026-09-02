"""Incident lifecycle engine + read model (ADR 0034 decision 4, #761)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
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
    Suite,
    SuiteNotification,
)
from backend.app.services import audit_service, run_service, suite_service
from backend.app.services.incident_evidence import (
    RedactionContext,
    build_evidence,
    resolve_redaction_contexts,
)
from backend.app.services.run_service import list_results
from backend.app.services.suite_authz import (
    SuiteForbiddenError,
    effective_permission,
    effective_permissions,
)

log = get_logger(__name__)

# Mirrors the partial unique index predicate `uq_incidents_active_asset_check` (keep in sync with
# the model / migration).
_ACTIVE_INCIDENT_PREDICATE = text(
    "status IN (" + ", ".join(f"'{s}'" for s in INCIDENT_ACTIVE_STATUSES) + ")"
)
# The clean/passing result status that auto-resolves an active incident.
_PASSING_RESULT = "pass"
# Bounded open→attach retry (open loses the insert AND the winner resolves in the
# gap → the pair is free again → re-open). 3 attempts is already paranoid.
_OPEN_ATTACH_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(UTC)


class IncidentFilterInvalidError(DataQError):
    status_code = 422
    code = "incident_filter_invalid"


class IncidentNotFoundError(DataQError):
    """The incident does not exist *or* is wholly outside the caller's grants — the
    two are indistinguishable by design (404-no-leak, same as the asset view).
    """

    status_code = 404
    code = "incident_not_found"


class IncidentNotActiveError(DataQError):
    """A manual transition was requested on a resolved incident (already closed) —
    a 409, not a silent no-op (the caller is acting on stale state).
    """

    status_code = 409
    code = "incident_not_active"


# ── the fail-soft run hook ────────────────────────────────────────────────────


def sync_incidents_for_run(session: Session, *, run_id: uuid.UUID) -> None:
    """Reconcile incidents from a terminal run's results — **never raises**."""
    try:
        _sync_incidents_for_run(session, run_id=run_id)
    except Exception:
        session.rollback()
        log.exception("incident_sync_failed", run_id=str(run_id))


def _sync_incidents_for_run(session: Session, *, run_id: uuid.UUID) -> None:
    run = session.get(Run, run_id)
    # Only executed runs carry per-check results.
    if run is None or run.status not in ("succeeded", "failed") or run.asset_id is None:
        return

    results = list_results(session, run.id)
    if not results:
        return
    asset = session.get(Asset, run.asset_id)
    auto_resolve = auto_resolve_enabled(session, run.suite_id)

    # Resolved ONCE per run, not per failing result (#1792): the checks, and each
    # result's as-of-write (tested_column, expectation_type) + policy + tags.
    failing = [r for r in results if r.status in FAILING_TIERS]
    checks = {
        c.id: c
        for c in session.scalars(select(Check).where(Check.id.in_({r.check_id for r in failing})))
    }
    contexts = resolve_redaction_contexts(
        session, run=run, results=failing, checks=checks, asset=asset
    )

    opened = attached = resolved = 0
    for result in results:
        if result.status in FAILING_TIERS:
            _, action = open_or_attach_incident(
                session,
                run=run,
                result=result,
                check=checks.get(result.check_id),
                asset=asset,
                context=contexts[result.id],
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


def redact_stale_evidence(session: Session) -> int:
    """One-time backfill (#1772): re-derive ``evidence.failing_result.observed_value``
    for every incident whose evidence was stored before the build-time G3 fix landed.

    `Incident.evidence` is written once per occurrence — a new failing result on an
    ACTIVE incident overwrites it (see `open_or_attach_incident` below), but an
    incident that stays open (or gets acked/resolved) with no further failure keeps
    its ORIGINAL snapshot forever. Before this fix, that snapshot's `observed_value`
    was never masked, so any incident opened pre-fix serves the raw value through
    `get_incident` (REST/MCP) and the RCA prompt indefinitely unless corrected here.

    Uses the check's CURRENT `config.column` as the best available `tested_column`
    (the original `Result` row isn't retained on `Incident`, so the point-in-time
    resolution `historical_check_context` gives on a live run isn't available for a
    stored snapshot — a check's tested column changing after the fact is rare and
    this backfill runs once). Idempotent: re-running it after itself is a no-op,
    since redacting an already-redacted value returns the same value.

    Returns the number of incidents actually rewritten.
    """
    incidents = list(session.scalars(select(Incident).where(Incident.evidence.is_not(None))))
    if not incidents:
        return 0

    checks = {
        c.id: c
        for c in session.scalars(select(Check).where(Check.id.in_({i.check_id for i in incidents})))
    }
    suites = {
        s.id: s
        for s in session.scalars(select(Suite).where(Suite.id.in_({i.suite_id for i in incidents})))
    }
    assets = {
        a.id: a
        for a in session.scalars(select(Asset).where(Asset.id.in_({i.asset_id for i in incidents})))
    }

    updated = 0
    for incident in incidents:
        evidence = incident.evidence
        if not evidence:
            continue
        failing = evidence.get("failing_result")
        if not isinstance(failing, dict) or "observed_value" not in failing:
            continue
        observed = failing["observed_value"]
        check = checks.get(incident.check_id)
        suite = suites.get(incident.suite_id)
        asset = assets.get(incident.asset_id)
        tested_column = check.config.get("column") if check and check.config else None
        redacted = run_service.redact_observed_value(
            observed,
            tested_column=tested_column,
            expectation_type=check.expectation_type if check is not None else None,
            policy=suite.column_policy if suite is not None else None,
            tags=asset.column_tags if asset is not None else None,
        )
        if redacted == observed:
            continue
        incident.evidence = {
            **evidence,
            "failing_result": {**failing, "observed_value": redacted},
        }
        updated += 1

    if updated:
        session.commit()
        log.info("incident_evidence_redaction_backfill", updated=updated)
    return updated


# ── lifecycle primitives ──────────────────────────────────────────────────────


def open_or_attach_incident(
    session: Session,
    *,
    run: Run,
    result: Result,
    check: Check | None,
    asset: Asset | None,
    context: RedactionContext | None = None,
) -> tuple[Incident, str]:
    """Open a new incident for ``(run.asset_id, result.check_id)`` or attach an
    occurrence to the active one. Returns ``(incident, "opened"|"attached")``.
    """
    assert run.asset_id is not None  # guarded by the caller (_sync / anchor rule)
    evidence = build_evidence(
        session, run=run, result=result, check=check, asset=asset, context=context
    )

    for _ in range(_OPEN_ATTACH_ATTEMPTS):
        # Recomputed per attempt: an incident that resolved in the gap is now the
        # reopen link for the fresh open.
        prior_id = _most_recent_resolved_id(
            session, asset_id=run.asset_id, check_id=result.check_id
        )
        new_id = session.execute(
            pg_insert(Incident)
            .values(
                asset_id=run.asset_id,
                check_id=result.check_id,
                suite_id=run.suite_id,
                status="open",
                occurrence_count=1,
                last_seen_at=func.now(),  # == created_at (see timestamp contract)
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

        # Conflict → an active incident already exists (the winner's row, now visible under READ
        # COMMITTED since our insert blocked on its commit).
        active = _active_incident(
            session, asset_id=run.asset_id, check_id=result.check_id, for_update=True
        )
        if active is None:
            # Resolved in the insert→attach gap — the pair is free again; retry the open instead of
            # raising (a raise would roll back the WHOLE run's sync, not just this pair).
            continue
        active.occurrence_count += 1
        active.last_seen_at = func.clock_timestamp()  # breaks created_at equality
        active.evidence = evidence
        return active, "attached"

    # Only reachable if the pair flip-flopped open↔resolved on every attempt —
    # practically impossible; surfaced (and swallowed fail-soft) by the caller.
    raise IncidentNotActiveError(  # pragma: no cover
        "incident open/attach did not converge",
        detail={"asset_id": str(run.asset_id), "check_id": str(result.check_id)},
    )


def _auto_resolve_active(session: Session, *, asset_id: uuid.UUID, check_id: uuid.UUID) -> bool:
    """Auto-resolve the active incident for the pair on a passing result. Returns
    whether one was resolved (``False`` when none is active — the common clean
    case). ``resolved_by='auto'``; no actor user. Row-locked (``FOR UPDATE``) so it
    serializes with a concurrent manual ack/resolve instead of clobbering it.
    """
    active = _active_incident(session, asset_id=asset_id, check_id=check_id, for_update=True)
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
    """Acknowledge an incident (``open → acknowledged``), stamping actor + time."""
    session.refresh(incident, with_for_update=True)
    if incident.status == "resolved":
        session.rollback()  # release the lock; nothing to write
        raise IncidentNotActiveError(
            "cannot acknowledge a resolved incident", detail={"incident_id": str(incident.id)}
        )
    audit_before = audit_service.snapshot("incident", incident)
    incident.status = "acknowledged"
    incident.acknowledged_at = _now()
    incident.acknowledged_by = user_id
    if note is not None:
        incident.acknowledge_note = note
    audit_service.record_entity_change(
        session,
        action="incident.acknowledge",
        entity_type="incident",
        entity=incident,
        actor=user_id,
        before=audit_before,
    )
    session.commit()
    session.refresh(incident)
    log.info("incident_acknowledged", incident_id=str(incident.id), user_id=str(user_id))
    return incident


def resolve_incident(
    session: Session, incident: Incident, *, user_id: uuid.UUID, note: str | None = None
) -> Incident:
    """Manually resolve an incident (``open|acknowledged → resolved``, ``resolved_by
    ='user'``). Manual wins over auto. A double-resolve is a 409.
    """
    session.refresh(incident, with_for_update=True)
    if incident.status == "resolved":
        session.rollback()  # release the lock; nothing to write
        raise IncidentNotActiveError(
            "incident is already resolved", detail={"incident_id": str(incident.id)}
        )
    audit_before = audit_service.snapshot("incident", incident)
    incident.status = "resolved"
    incident.resolved_by = "user"
    incident.resolved_by_user_id = user_id
    incident.resolved_at = _now()
    if note is not None:
        incident.resolution_note = note
    # Only the MANUAL resolve is audited.
    audit_service.record_entity_change(
        session,
        action="incident.resolve",
        entity_type="incident",
        entity=incident,
        actor=user_id,
        before=audit_before,
    )
    session.commit()
    session.refresh(incident)
    log.info("incident_resolved", incident_id=str(incident.id), user_id=str(user_id))
    return incident


# ── config ────────────────────────────────────────────────────────────────────


def auto_resolve_enabled(session: Session, suite_id: uuid.UUID) -> bool:
    """Whether the suite auto-resolves incidents on a passing result. Default
    **on** for a suite with no notification config row (matches the no-config
    alerting default).
    """
    config = session.scalars(
        select(SuiteNotification).where(SuiteNotification.suite_id == suite_id)
    ).first()
    return config.auto_resolve_incidents if config is not None else True


# ── read model ────────────────────────────────────────────────────────────────


def _active_incident(
    session: Session, *, asset_id: uuid.UUID, check_id: uuid.UUID, for_update: bool = False
) -> Incident | None:
    """The single active (open|acknowledged) incident for the pair, if any — the
    partial unique index guarantees at most one. ``for_update`` row-locks it for
    the mutating callers (attach, auto-resolve) so they serialize with the
    manual ack/resolve lock instead of losing updates.
    """
    stmt = select(Incident).where(
        Incident.asset_id == asset_id,
        Incident.check_id == check_id,
        Incident.status.in_(INCIDENT_ACTIVE_STATUSES),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def _most_recent_resolved_id(
    session: Session, *, asset_id: uuid.UUID, check_id: uuid.UUID
) -> uuid.UUID | None:
    """The most-recently-resolved incident id for the pair (the reopen link), or
    ``None`` for a first-ever incident.
    """
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
    the open incident. Empty when the run has no resolved asset.
    """
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


#: Suite levels that may act on (ack / resolve) an incident — `edit` and above,
#: mirroring the suite_authz ladder (a `view` share reads but never acts).
ACTING_LEVELS = frozenset({"edit", "admin", "owner"})


def load_visible_incident(
    session: Session, incident_id: uuid.UUID, *, user_id: uuid.UUID, for_action: bool
) -> Incident:
    """Load an incident the caller may see, or raise 404-no-leak. When
    ``for_action`` the caller must additionally hold ``edit`` on the incident's
    suite (else `SuiteForbiddenError`).
    """
    incident = get_incident(session, incident_id)
    suite = session.get(Suite, incident.suite_id) if incident is not None else None
    # Workspace-admin resolves to an implicit `admin` on every suite inside
    # `effective_permission`; a normal user resolves to their grant or None.
    level = effective_permission(session, suite, user_id) if suite is not None else None
    if incident is None or level is None:
        raise IncidentNotFoundError("incident not found", detail={"incident_id": str(incident_id)})
    if for_action and level not in ACTING_LEVELS:
        raise SuiteForbiddenError(
            "acknowledging or resolving an incident requires 'edit' on its suite",
            detail={"incident_id": str(incident_id), "have": level, "need": "edit"},
        )
    return incident


def get_incident(session: Session, incident_id: uuid.UUID) -> Incident | None:
    """Fetch an incident by id (no authz — the API layer gates on its suite)."""
    return session.get(Incident, incident_id)


def _parse_sibling_suite_ids(
    siblings: list[Any],
) -> list[tuple[Any, uuid.UUID | None]]:
    """Pair each ``same_asset_siblings`` entry with its parsed ``suite_id``, or
    ``None`` for anything malformed (not a dict, no ``suite_id``, or not a valid
    UUID string) — a single fail-closed pass so a bad entry is withheld rather
    than crashing the two comprehensions that used to walk this list separately
    with different guards (#1635 review).
    """
    parsed: list[tuple[Any, uuid.UUID | None]] = []
    for entry in siblings:
        suite_id = entry.get("suite_id") if isinstance(entry, dict) else None
        try:
            parsed.append((entry, uuid.UUID(suite_id) if isinstance(suite_id, str) else None))
        except ValueError:
            parsed.append((entry, None))
    return parsed


def evidence_for_caller(
    session: Session, incident: Incident, *, user_id: uuid.UUID
) -> dict[str, Any] | None:
    """The incident's stored evidence card, with the ``same_asset_siblings``
    layer (#1635) trimmed to entries whose suite this caller can view.

    That layer is built **workspace-true** (ADR 0037), the same way the asset
    rollup is — it has no caller in scope, since it's assembled once at sync
    time. Holding `view` on THIS incident's suite is not a grant on a sibling's
    suite just because both target the same asset, so the itemized rows are
    filtered here, at read time — mirroring how ``get_asset`` names only the
    caller's own suites and folds the rest into a count. A suite that no longer
    exists reads the same as one the caller cannot see, and so does a malformed
    entry; all three collapse into the restricted count rather than being
    distinguished or raising.

    ``same_asset_siblings_restricted_count`` is present (possibly ``0``)
    whenever the ``same_asset_siblings`` layer was built at all — i.e. whenever
    it isn't ``None`` — the same null-vs-empty-list convention every other list
    layer on this card already uses; it is absent only alongside a ``None``
    layer, never alongside an empty one.
    """
    evidence = incident.evidence
    if not isinstance(evidence, dict):
        return evidence
    siblings = evidence.get("same_asset_siblings")
    if not isinstance(siblings, list):
        return evidence
    parsed = _parse_sibling_suite_ids(siblings)
    suite_ids = {sid for _entry, sid in parsed if sid is not None}
    suites = session.scalars(select(Suite).where(Suite.id.in_(suite_ids))).all()
    levels = effective_permissions(session, suites, user_id)
    visible = [entry for entry, sid in parsed if sid is not None and levels.get(sid) is not None]
    return {
        **evidence,
        "same_asset_siblings": visible,
        "same_asset_siblings_restricted_count": len(siblings) - len(visible),
    }


def evidence_for_alert(incident: Incident) -> dict[str, Any] | None:
    """The incident's evidence card for outbound alert delivery (#1635 review) —
    ``same_asset_siblings`` restricted to entries in the incident's OWN suite.

    An alert channel has no per-user grant to redact against the way
    ``evidence_for_caller`` does — it's configured per suite (``SuiteNotification``),
    not per viewer — so a cross-suite sibling has no principal it's safe to name.
    The incident's own suite is different: it's exactly what that channel's
    configured recipients already administer, so same-suite entries are kept.
    """
    evidence = incident.evidence
    if not isinstance(evidence, dict):
        return evidence
    siblings = evidence.get("same_asset_siblings")
    if not isinstance(siblings, list):
        return evidence
    own_suite = incident.suite_id
    parsed = _parse_sibling_suite_ids(siblings)
    same_suite = [entry for entry, sid in parsed if sid == own_suite]
    return {
        **evidence,
        "same_asset_siblings": same_suite,
        "same_asset_siblings_restricted_count": len(siblings) - len(same_suite),
    }


def _incident_filters(
    *,
    user_id: uuid.UUID,
    include_all: bool,
    asset_id: uuid.UUID | None,
    suite_id: uuid.UUID | None,
    state: str | None,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> list[Any]:
    """The ONE `WHERE` chain shared by :func:`list_incidents` and
    :func:`count_incidents`. Derived once rather than hand-rolled twice, so a
    future filter cannot land on the list without the total — which would make
    `X-Total-Count` quietly disagree with the page it describes (#1108).
    """
    conditions: list[Any] = [
        Incident.suite_id.in_(suite_service.accessible_suite_ids(user_id, include_all=include_all))
    ]
    if asset_id is not None:
        conditions.append(Incident.asset_id == asset_id)
    if suite_id is not None:
        conditions.append(Incident.suite_id == suite_id)
    if state is not None:
        conditions.append(Incident.status == state)
    if since_hours is not None:
        conditions.append(Incident.last_seen_at >= _now() - timedelta(hours=since_hours))
    if until_hours is not None:
        conditions.append(Incident.last_seen_at <= _now() - timedelta(hours=until_hours))
    return conditions


def validate_read_filters(
    *,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> None:
    """422 on an incident time-window filter outside its valid shape."""
    if since_hours is not None and since_hours <= 0:
        raise IncidentFilterInvalidError(f"since_hours must be positive, got {since_hours!r}")
    if until_hours is not None and until_hours < 0:
        raise IncidentFilterInvalidError(f"until_hours must not be negative, got {until_hours!r}")
    if since_hours is not None and until_hours is not None and until_hours >= since_hours:
        raise IncidentFilterInvalidError(
            f"until_hours ({until_hours!r}) must be less than since_hours ({since_hours!r})"
        )


def list_incidents(
    session: Session,
    *,
    user_id: uuid.UUID,
    include_all: bool = False,
    asset_id: uuid.UUID | None = None,
    suite_id: uuid.UUID | None = None,
    state: str | None = None,
    limit: int = 100,
    offset: int = 0,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> list[Incident]:
    """Incidents on suites the caller can view, most-recently-active first
    (``last_seen_at`` desc), paginated with ``limit``/``offset`` (the #772
    /assets pagination shape).

    Ordered, filtered (``since_hours``/``until_hours``) and windowed
    (``_page_window`` in the MCP tool) all on the SAME field —
    ``last_seen_at``, the most recent breach, not ``created_at`` — on purpose:
    an incident opened last week that breached again an hour ago should
    surface ahead of one that opened yesterday and has been quiet since, and a
    truncated page must be a contiguous slice of whatever field the caller is
    told to page by. Ordering by a different field than the filter/window
    would make "page with offset until oldest_in_page precedes the window you
    asked about" silently wrong.
    """
    stmt = (
        select(Incident)
        .where(
            *_incident_filters(
                user_id=user_id,
                include_all=include_all,
                asset_id=asset_id,
                suite_id=suite_id,
                state=state,
                since_hours=since_hours,
                until_hours=until_hours,
            )
        )
        .order_by(Incident.last_seen_at.desc(), Incident.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt))


def count_incidents(
    session: Session,
    *,
    user_id: uuid.UUID,
    include_all: bool = False,
    asset_id: uuid.UUID | None = None,
    suite_id: uuid.UUID | None = None,
    state: str | None = None,
    since_hours: float | None = None,
    until_hours: float | None = None,
) -> int:
    """Total incidents matching the SAME visibility + filters as :func:`list_incidents`, unaffected
    by its `limit`/`offset` (#1108 — the `/assets` `X-Total-Count` shape). Grant-scoped like the
    list — this is a per-caller total, not a workspace-wide one (unlike `/assets`, incidents
    stay behind suite grants per ADR 0037).
    """
    stmt = (
        select(func.count())
        .select_from(Incident)
        .where(
            *_incident_filters(
                user_id=user_id,
                include_all=include_all,
                asset_id=asset_id,
                suite_id=suite_id,
                state=state,
                since_hours=since_hours,
                until_hours=until_hours,
            )
        )
    )
    return session.scalar(stmt) or 0
