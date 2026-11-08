"""Read the object-lock anchors back and match them against the local chain (#2310).

The anchor holds every head DataQ ever published. A head the local chain no longer
knows — neither a live row's hash nor a purge checkpoint's last-deleted hash — and
that no retention purge accounts for is the signature of a database restored to an
earlier point, or of a rewritten chain. The webhook anchor cannot be read back, so
this check exists only for anchors that can.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.tamper_anchor import (
    AnchorRecord,
    ObjectLockAnchor,
    ScrubReceiptReadResult,
    TamperAnchor,
    TamperAnchorUnavailableError,
    get_tamper_anchor,
)
from backend.app.db.models import AuditChainCheckpoint, AuditEvent
from backend.app.services.audit_chain import (
    SCRUB_ACTION,
    ChainVerification,
    VerifiedScrub,
    verify_chain,
)

AnchorMatch = Literal["present", "purged", "missing", "malformed"]
AnchorStatus = Literal["ok", "mismatch", "empty", "unavailable"]

#: How many non-matching anchors a report itemises; the counts are always complete.
MAX_REPORTED = 20
#: Receipts are one per scrub, so far rarer than heads: read them all, up to this bound.
MAX_RECEIPTS_READ = 5000


@dataclass(frozen=True)
class AnchorCheck:
    key: str
    label: str | None
    head_hash: str | None
    as_of: datetime | None
    match: AnchorMatch


@dataclass(frozen=True)
class AnchorVerification:
    status: AnchorStatus
    checked_count: int = 0
    present_count: int = 0
    purged_count: int = 0
    missing_count: int = 0
    malformed_count: int = 0
    #: The non-matching anchors, newest first, at most `MAX_REPORTED`.
    findings: list[AnchorCheck] = field(default_factory=list)
    #: More anchors exist than were read; only the newest were checked.
    truncated: bool = False
    #: Why the store could not be read — set only with status "unavailable".
    error: str | None = None
    #: Scrub receipts read back (ADR 0041 §9, 2026-11-08 amendment).
    scrub_receipt_count: int = 0
    #: Scrub events with no receipt: not written by the app, or a publish that failed. Each one
    #: makes the status "mismatch". Only scrubs since the anchor began publishing are judged.
    unanchored_scrub_count: int = 0
    #: Receipts naming no scrub event that retention explains, or objects under the receipt
    #: prefix DataQ did not write.
    orphan_receipt_count: int = 0
    #: The unanchored scrub events' ids, at most `MAX_REPORTED`.
    unanchored_scrub_event_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ScrubCheck:
    receipt_count: int = 0
    unanchored: list[str] = field(default_factory=list)
    orphan_count: int = 0


def _match(record: AnchorRecord, known: set[str], purge_cutoff: datetime | None) -> AnchorMatch:
    if record.malformed or record.head_hash is None:
        return "malformed"
    if record.head_hash in known:
        return "present"
    if purge_cutoff is not None and record.as_of is not None and record.as_of < purge_cutoff:
        return "purged"
    return "missing"


def _legitimate_purge_cutoff(session: Session) -> datetime | None:
    """The latest checkpoint cutoff a real retention sweep could have written.

    A sweep's cutoff is ``now - AUDIT_RETENTION_DAYS`` at the time it ran, so it can
    never be later than that today. A checkpoint row claiming a later cutoff is not a
    purge but a forgery, and trusting it would reclassify every missing anchor as
    purged — hiding exactly what this check exists to find.
    """
    retention = get_settings().audit_retention_days
    if retention <= 0:
        return None
    ceiling = datetime.now(UTC) - timedelta(days=retention)
    return session.scalar(
        select(func.max(AuditChainCheckpoint.cutoff)).where(AuditChainCheckpoint.cutoff <= ceiling)
    )


def verify_anchors(
    session: Session,
    anchor: TamperAnchor | None = None,
    chain: ChainVerification | None = None,
) -> AnchorVerification | None:
    """``None`` when the configured anchor cannot be read back (none / webhook). ``chain`` is the
    caller's `verify_chain` result, when it already has one.
    """
    anchor = anchor if anchor is not None else get_tamper_anchor()
    if not isinstance(anchor, ObjectLockAnchor):
        return None
    try:
        read = anchor.read_anchors()
        receipts = anchor.read_scrub_receipts(limit=MAX_RECEIPTS_READ)
    except TamperAnchorUnavailableError as exc:
        return AnchorVerification(status="unavailable", error=str(exc))
    purge_cutoff = _legitimate_purge_cutoff(session)
    chain = chain if chain is not None else verify_chain(session)
    scrubs = _check_scrubs(session, chain, read.records, receipts, purge_cutoff)
    if not read.records and not scrubs.unanchored:
        return _with_scrubs(AnchorVerification(status="empty", truncated=read.truncated), scrubs)

    heads = {r.head_hash for r in read.records if r.head_hash is not None}
    known: set[str] = set()
    known.update(
        h
        for h in session.scalars(select(AuditEvent.row_hash).where(AuditEvent.row_hash.in_(heads)))
        if h is not None
    )
    known.update(
        h
        for h in session.scalars(
            select(AuditChainCheckpoint.last_deleted_row_hash).where(
                AuditChainCheckpoint.last_deleted_row_hash.in_(heads)
            )
        )
        if h is not None
    )
    checks = [
        AnchorCheck(r.key, r.label, r.head_hash, r.as_of, _match(r, known, purge_cutoff))
        for r in read.records
    ]
    counts = {
        m: sum(1 for c in checks if c.match == m)
        for m in ("present", "purged", "missing", "malformed")
    }
    findings = [c for c in checks if c.match in ("missing", "malformed")]
    verification = AnchorVerification(
        status="mismatch" if findings or scrubs.unanchored else "ok",
        checked_count=len(checks),
        present_count=counts["present"],
        purged_count=counts["purged"],
        missing_count=counts["missing"],
        malformed_count=counts["malformed"],
        findings=findings[:MAX_REPORTED],
        truncated=read.truncated,
    )
    return _with_scrubs(verification, scrubs)


def _with_scrubs(verification: AnchorVerification, scrubs: _ScrubCheck) -> AnchorVerification:
    return replace(
        verification,
        scrub_receipt_count=scrubs.receipt_count,
        unanchored_scrub_count=len(scrubs.unanchored),
        orphan_receipt_count=scrubs.orphan_count,
        unanchored_scrub_event_ids=scrubs.unanchored[:MAX_REPORTED],
    )


def _check_scrubs(
    session: Session,
    chain: ChainVerification,
    heads: list[AnchorRecord],
    receipts: ScrubReceiptReadResult,
    purge_cutoff: datetime | None,
) -> _ScrubCheck:
    """Match the scrub events the chain verified against their receipts.

    A receipt counts only if its target and both hashes equal the scrub event's own: a scrub's
    residual edited after its receipt was published does not pass on the id alone. Which scrubs
    are judged is decided by chain position, never by `occurred_at`, which whoever writes the row
    sets: every verified scrub chained after the oldest published head, or at or after the oldest
    scrub with a receipt, must have a matching receipt. Older scrubs predate the anchor.
    """
    valid = [r for r in receipts.records if not r.malformed]
    by_id = {r.scrub_event_id: r for r in valid}
    scrub_positions = {str(v.event_id): v.position for v in chain.verified_scrubs}
    # The oldest anchored head was the chain's head when anchoring began, so only scrubs chained
    # after it are judged. Any receipt, matching or not, proves anchoring was live at its scrub.
    head_points = [
        chain.positions[r.head_hash]
        for r in heads
        if r.head_hash is not None and r.head_hash in chain.positions
    ]
    receipt_points = [
        scrub_positions[r.scrub_event_id] for r in valid if r.scrub_event_id in scrub_positions
    ]
    oldest_head = max(head_points) if head_points else None
    oldest_receipt = max(receipt_points) if receipt_points else None

    def _judged(scrub: VerifiedScrub) -> bool:
        return (oldest_head is not None and scrub.position < oldest_head) or (
            oldest_receipt is not None and scrub.position <= oldest_receipt
        )

    def _matches(scrub: VerifiedScrub) -> bool:
        receipt = by_id.get(str(scrub.event_id))
        return receipt is not None and (
            receipt.target_event_id,
            receipt.target_row_hash,
            receipt.residual_hash,
        ) == (str(scrub.target_event_id), scrub.target_row_hash, scrub.residual_hash)

    unanchored = [str(v.event_id) for v in chain.verified_scrubs if _judged(v) and not _matches(v)]
    live = {
        str(event_id)
        for event_id in session.scalars(
            select(AuditEvent.id).where(AuditEvent.action == SCRUB_ACTION)
        )
    }
    orphans = sum(1 for r in receipts.records if r.malformed) + sum(
        1
        for r in valid
        if r.scrub_event_id not in live
        and not (purge_cutoff is not None and r.as_of is not None and r.as_of < purge_cutoff)
    )
    return _ScrubCheck(
        receipt_count=len(receipts.records), unanchored=unanchored, orphan_count=orphans
    )
