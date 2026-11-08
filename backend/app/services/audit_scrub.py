"""The audited payload scrub — the one exception to append-only (ADR 0041 §9, 2026-11-08 amendment).

A scrub replaces an allow-listed payload path with what a redacted version keeps, and never
rewrites `row_hash`, so the `prev_hash` links and every anchored head still verify. The evidence is
a chained `audit_event.scrub` event recording the target's hash and the hash of its content after
the scrub; `audit_chain.verify_chain` accepts a row that no longer recomputes only against it. Each
scrub also publishes a receipt to the tamper anchor once its transaction commits, so a scrub event
forged by an actor with direct database access has no receipt.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from sqlalchemy import event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.tamper_anchor import get_tamper_anchor, scrub_receipt_payload
from backend.app.db.models import AuditEvent
from backend.app.services import audit_chain, audit_service
from backend.app.services.version_redaction import KEPT_CHECK_KEYS

log = get_logger(__name__)

#: entity_type -> payload key -> the keys a scrubbed value keeps. A new path needs an ADR amendment.
SCRUBBABLE: Final[Mapping[str, Mapping[str, tuple[str, ...]]]] = {
    "check": {"config": KEPT_CHECK_KEYS},
}
_SIDES: Final[tuple[str, ...]] = ("before", "after")

#: `session.info` slot holding receipts to publish once the scrub's transaction commits.
_RECEIPTS_KEY = "audit_scrub_receipts"

ScrubTrigger = Literal["version_redaction", "admin"]

#: The only columns the scrub writes, granted for its own statement.
_PAYLOAD_UPDATE: Final[str] = "UPDATE (before, after, scrubbed_at)"


class AuditEventNotFoundError(DataQError):
    status_code = 404
    code = "audit_event_not_found"


class AuditEventNotScrubbableError(DataQError):
    status_code = 409
    code = "audit_event_not_scrubbable"


class AuditEventUnverifiedError(DataQError):
    # Scrubbing a row that already fails verification would launder whatever was done to it.
    status_code = 409
    code = "audit_event_unverified"


@dataclass(frozen=True)
class _Scrub:
    path: str
    side: str
    key: str


def _kept(value: Any, keep: tuple[str, ...]) -> dict[str, Any]:
    return {k: value[k] for k in keep if k in value} if isinstance(value, dict) else {}


def _pending(row: AuditEvent, *, only: Any = None) -> list[_Scrub]:
    """The paths on `row` still holding more than a scrub keeps (matching `only` when given)."""
    found: list[_Scrub] = []
    for key, keep in SCRUBBABLE.get(row.entity_type, {}).items():
        for side in _SIDES:
            payload = getattr(row, side)
            if not isinstance(payload, dict) or not isinstance(payload.get(key), dict):
                continue
            value = payload[key]
            if value == _kept(value, keep) or (only is not None and value != only):
                continue
            found.append(_Scrub(f"{side}.{key}", side, key))
    return found


def _verifies(row: AuditEvent, claims: Mapping[uuid.UUID, tuple[str | None, str | None]]) -> bool:
    """The row is intact now: by its own hash, or (already scrubbed) by the claim of the newest
    scrub event the chain walk verified. A claim read straight from the table is never trusted:
    anyone who can write the table can write one. A row written before the chain shipped has no
    hash and nothing to protect.
    """
    if row.row_hash is None:
        return True
    recomputed = audit_chain.compute_row_hash(row, row.prev_hash)
    return recomputed == row.row_hash or claims.get(row.id) == (row.row_hash, recomputed)


def _write(session: Session, row: AuditEvent, values: dict[str, Any]) -> None:
    """The UPDATE, under a privilege granted for this statement only. `dataq_app` owns the table,
    so this guards against an accidental write, not against the owner (ADR 0041 decision 7).
    """
    session.execute(text(f"GRANT {_PAYLOAD_UPDATE} ON audit_events TO CURRENT_USER"))
    try:
        session.execute(update(AuditEvent).where(AuditEvent.id == row.id).values(**values))
    except BaseException:
        # A database error aborts the transaction, and its rollback takes the GRANT with it; the
        # REVOKE then fails too, and must not hide the error that caused it.
        with contextlib.suppress(DBAPIError):
            session.execute(text(f"REVOKE {_PAYLOAD_UPDATE} ON audit_events FROM CURRENT_USER"))
        raise
    session.execute(text(f"REVOKE {_PAYLOAD_UPDATE} ON audit_events FROM CURRENT_USER"))
    session.expire(row, list(values))


def _scrub(
    session: Session,
    row: AuditEvent,
    scrubs: list[_Scrub],
    *,
    actor: Any,
    reason: str,
    trigger: ScrubTrigger,
) -> AuditEvent:
    keep = SCRUBBABLE[row.entity_type]
    sides: dict[str, dict[str, Any]] = {}
    for s in scrubs:
        payload = sides.setdefault(s.side, dict(getattr(row, s.side)))
        payload[s.key] = _kept(payload[s.key], keep[s.key])
    residual = (
        None
        if row.row_hash is None
        else audit_chain.compute_row_hash(row, row.prev_hash, overrides=sides)
    )
    target_row_hash = row.row_hash
    _write(session, row, {**sides, "scrubbed_at": datetime.now(UTC)})
    scrub_event = audit_service.record(
        session,
        action=audit_chain.SCRUB_ACTION,
        entity_type=audit_chain.SCRUB_ENTITY_TYPE,
        entity_id=row.id,
        actor=actor,
        after={
            "target_event_id": str(row.id),
            "target_action": row.action,
            "target_row_hash": target_row_hash,
            "residual_hash": residual,
            "paths": [s.path for s in scrubs],
            "trigger": trigger,
            "reason": reason,
        },
    )
    session.flush([scrub_event])  # the receipt names the scrub event's id
    session.info.setdefault(_RECEIPTS_KEY, []).append(
        scrub_receipt_payload(
            scrub_event_id=str(scrub_event.id),
            target_event_id=str(row.id),
            target_row_hash=target_row_hash,
            residual_hash=residual,
            as_of=datetime.now(UTC),
        )
    )
    log.info(
        "audit_event_scrubbed",
        event_id=str(row.id),
        paths=[s.path for s in scrubs],
        trigger=trigger,
    )
    return scrub_event


def scrub_event(session: Session, event_id: uuid.UUID, *, actor: Any, reason: str) -> AuditEvent:
    """A workspace admin's scrub of every allow-listed path on one event. The caller commits."""
    row = session.get(AuditEvent, event_id, with_for_update=True)
    if row is None:
        raise AuditEventNotFoundError("audit event not found", detail={"event_id": str(event_id)})
    scrubs = _pending(row)
    if not scrubs:
        raise AuditEventNotScrubbableError(
            "this event holds nothing a scrub removes: only a check event's config can be "
            "scrubbed, and it keeps the tested column",
            detail={"event_id": str(event_id), "entity_type": row.entity_type},
        )
    if not _verifies(row, audit_chain.verify_chain(session).scrub_claims):
        raise AuditEventUnverifiedError(
            "this event no longer matches its hash; investigate it with the chain verification "
            "before scrubbing",
            detail={"event_id": str(event_id)},
        )
    _scrub(session, row, scrubs, actor=actor, reason=reason, trigger="admin")
    return row


def scrub_matching(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    key: str,
    value: dict[str, Any],
    actor: Any,
    reason: str,
) -> int:
    """Scrub `key` wherever it equals `value` on this entity's events (a version redaction).
    `value` is compared as the audit payload stores it. An event that fails verification is
    skipped and logged at ERROR, never laundered. Returns how many events were scrubbed. The
    caller commits.
    """
    stored = audit_service.payload_value(value)
    rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.occurred_at)
        .with_for_update()
    ).all()
    scrubbed = 0
    claims: Mapping[uuid.UUID, tuple[str | None, str | None]] | None = None
    for row in rows:
        scrubs = [s for s in _pending(row, only=stored) if s.key == key]
        if not scrubs:
            continue
        if claims is None:  # one chain walk, and only when something needs scrubbing
            claims = audit_chain.verify_chain(session).scrub_claims
        if not _verifies(row, claims):
            log.error("audit_scrub_skipped_unverified", event_id=str(row.id))
            continue
        _scrub(session, row, scrubs, actor=actor, reason=reason, trigger="version_redaction")
        scrubbed += 1
    return scrubbed


@event.listens_for(Session, "after_commit")
def _publish_receipts(session: Session) -> None:
    """Publish each committed scrub's receipt. A failed publish is logged by the anchor and never
    undoes the scrub: erasing a value that should not be held is the obligation.
    """
    receipts = session.info.pop(_RECEIPTS_KEY, None)
    if not receipts:
        return
    anchor = get_tamper_anchor()
    for receipt in receipts:
        anchor.publish_scrub(receipt)


@event.listens_for(Session, "after_rollback")
def _drop_receipts(session: Session) -> None:
    session.info.pop(_RECEIPTS_KEY, None)
