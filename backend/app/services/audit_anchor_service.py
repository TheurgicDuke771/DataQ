"""Read the object-lock anchors back and match them against the local chain (#2310).

The anchor holds every head DataQ ever published. A head the local chain no longer
knows — neither a live row's hash nor a purge checkpoint's last-deleted hash — and
that no retention purge accounts for is the signature of a database restored to an
earlier point, or of a rewritten chain. The webhook anchor cannot be read back, so
this check exists only for anchors that can.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.tamper_anchor import (
    AnchorRecord,
    ObjectLockAnchor,
    TamperAnchor,
    TamperAnchorUnavailableError,
    get_tamper_anchor,
)
from backend.app.db.models import AuditChainCheckpoint, AuditEvent

AnchorMatch = Literal["present", "purged", "missing", "malformed"]
AnchorStatus = Literal["ok", "mismatch", "empty", "unavailable"]

#: How many non-matching anchors a report itemises; the counts are always complete.
MAX_REPORTED = 20


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


def _match(record: AnchorRecord, known: set[str], purge_cutoff: datetime | None) -> AnchorMatch:
    if record.malformed or record.head_hash is None:
        return "malformed"
    if record.head_hash in known:
        return "present"
    if purge_cutoff is not None and record.as_of is not None and record.as_of < purge_cutoff:
        return "purged"
    return "missing"


def verify_anchors(
    session: Session, anchor: TamperAnchor | None = None
) -> AnchorVerification | None:
    """``None`` when the configured anchor cannot be read back (none / webhook)."""
    anchor = anchor if anchor is not None else get_tamper_anchor()
    if not isinstance(anchor, ObjectLockAnchor):
        return None
    try:
        read = anchor.read_anchors()
    except TamperAnchorUnavailableError as exc:
        return AnchorVerification(status="unavailable", error=str(exc))
    if not read.records:
        return AnchorVerification(status="empty", truncated=read.truncated)

    known: set[str] = set()
    known.update(
        h
        for h in session.scalars(select(AuditEvent.row_hash).where(AuditEvent.row_hash.isnot(None)))
        if h is not None
    )
    known.update(
        h
        for h in session.scalars(
            select(AuditChainCheckpoint.last_deleted_row_hash).where(
                AuditChainCheckpoint.last_deleted_row_hash.isnot(None)
            )
        )
        if h is not None
    )
    purge_cutoff = session.scalar(select(func.max(AuditChainCheckpoint.cutoff)))

    checks = [
        AnchorCheck(r.key, r.label, r.head_hash, r.as_of, _match(r, known, purge_cutoff))
        for r in read.records
    ]
    counts = {
        m: sum(1 for c in checks if c.match == m)
        for m in ("present", "purged", "missing", "malformed")
    }
    findings = [c for c in checks if c.match in ("missing", "malformed")]
    return AnchorVerification(
        status="mismatch" if findings else "ok",
        checked_count=len(checks),
        present_count=counts["present"],
        purged_count=counts["purged"],
        missing_count=counts["missing"],
        malformed_count=counts["malformed"],
        findings=findings[:MAX_REPORTED],
        truncated=read.truncated,
    )
