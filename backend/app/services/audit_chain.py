"""Hash-chain tamper-evidence over `audit_events` — ADR 0041 §9 / #1460.

The chain proves a row was not silently rewritten *within* what it covers; the
external anchor (`core/tamper_anchor.py`) is the load-bearing half, because a
chain alone proves nothing to an attacker with write access to the whole table —
they can recompute it forward from wherever they edited. See ADR 0041 §9 for the
stated threat model.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from backend.app.db.models import AuditChainCheckpoint, AuditChainState, AuditEvent

#: The singleton `audit_chain_state` row id. Seeded by the migration; `_ensure_chain_state_row`
#: is defense-in-depth for a session that only ran `Base.metadata.create_all` (tests).
_STATE_ROW_ID = 1

#: Fields hashed alongside `prev_hash` — every column on `AuditEvent` that is
#: genuinely immutable once written.
#:
#: `actor_user_id` and `actor_label` are DELIBERATELY EXCLUDED, and this is a
#: correctness requirement, not a stylistic choice: ADR 0041 §2.6.5 documents
#: both as mutable by design. `actor_user_id` is `ON DELETE SET NULL` — a real
#: user deletion (offboarding, not tampering) nulls it out via a Postgres FK
#: cascade that never goes through `record()`/this module at all. And the same
#: section names pseudonymizing `actor_label` in place as the exact mechanism
#: G2 erasure needs. A first version of this hash included both fields; a real
#: (test-triggered) user deletion then flipped `actor_user_id` to NULL on an
#: already-hashed row, and `verify_chain` reported every event downstream as
#: tampered — a false positive on ordinary, expected system behavior, which is
#: worse for a compliance feature than under-covering: an admin who sees
#: "broken" here has no way to tell a real attack from routine offboarding.
_HASH_FIELDS: tuple[str, ...] = (
    "id",
    "occurred_at",
    "action_class",
    "action",
    "entity_type",
    "entity_id",
    "actor_kind",
    "before",
    "after",
    "request_id",
)


def _coerce(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _canonical(event: AuditEvent) -> dict[str, Any]:
    return {name: _coerce(getattr(event, name)) for name in _HASH_FIELDS}


def compute_row_hash(event: AuditEvent, prev_hash: str | None) -> str:
    """Deterministic sha256 over `event`'s content and its chain position.

    Called both when a row is written (with the real `prev_hash`) and when
    `verify_chain` recomputes it from what is stored — the two MUST agree on
    exactly what goes in, which is why this is the only place either does it.
    """
    payload = {"prev_hash": prev_hash, **_canonical(event)}
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ensure_chain_state_row(session: Session) -> None:
    session.execute(
        text(
            "INSERT INTO audit_chain_state (id, head_hash, head_event_id, updated_at) "
            "VALUES (:id, NULL, NULL, now()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": _STATE_ROW_ID},
    )


def _lock_chain_state(session: Session) -> AuditChainState:
    """Lock the singleton head row for the rest of this transaction.

    No contention-timeout tolerance here (contrast `connection_lock.py`'s
    best-effort retry): a phase-1 audit write is fail-closed by ADR 0041 §2.1,
    and silently proceeding without a lock would risk two concurrent writers
    computing the same `prev_hash` — a lock we swallow the failure of would
    itself defeat the chain it exists to protect.
    """
    _ensure_chain_state_row(session)
    state = session.get(AuditChainState, _STATE_ROW_ID, with_for_update=True)
    if state is None:  # pragma: no cover — the INSERT above guarantees existence
        raise RuntimeError("audit_chain_state singleton row is missing after ensure-insert")
    return state


@event.listens_for(Session, "before_commit")
def _chain_pending_audit_events(session: Session) -> None:
    """Extend the chain onto any newly-added, not-yet-hashed `AuditEvent` rows,
    right before this session's transaction commits.

    Deliberately NOT done inside `audit_service.record()` itself: `record()`'s
    own contract (ADR 0041 §2.1, tested by
    `test_record_adds_to_the_caller_transaction_and_never_commits`) is that it
    adds to the caller's transaction WITHOUT flushing or committing — a
    premature flush could hit FK/ordering issues against entities the caller
    hasn't finished constructing yet. Hooking `before_commit` gets the same
    fail-closed guarantee (a hashing failure here aborts the whole `commit()`,
    exactly as if the audit write itself had failed) without record() knowing
    anything about hashing.

    Registered globally on `Session` at import time (this module is imported by
    `audit_service`, which every audit-writing call site already imports) —
    fires on every session commit, so the `if not pending` fast path below
    matters: it must cost nothing for the overwhelming majority of commits that
    touch no audit event at all.
    """
    # `session.new` iterates in `add()` order (it is dict-backed) — which IS the
    # correct chain order for events from one commit, since Postgres `now()`
    # returns the TRANSACTION's start time: two events in the same transaction
    # get the IDENTICAL `occurred_at`, so sorting by `(occurred_at, id)` would
    # tie-break on a random UUID instead of preserving call order. Sorting by
    # `occurred_at` ALONE and relying on Python's stable sort keeps that
    # relative order for genuine ties, while still ordering events correctly
    # across separate, sequential commits.
    pending = [obj for obj in session.new if isinstance(obj, AuditEvent) and obj.row_hash is None]
    if not pending:
        return
    session.flush(pending)  # materializes server-generated id/occurred_at
    pending.sort(key=lambda e: e.occurred_at)
    state = _lock_chain_state(session)
    for ev in pending:
        ev.prev_hash = state.head_hash
        ev.row_hash = compute_row_hash(ev, state.head_hash)
        state.head_hash = ev.row_hash
        state.head_event_id = ev.id
    session.flush(pending)


@dataclass(frozen=True)
class ChainBreak:
    """The first row whose stored chain position could not be reproduced.

    `occurred_at` is `None` in the one case where the blamed row could not be
    loaded at all — its hash is referenced by a pointer (the chain head, or
    another row's `prev_hash`) but the row itself is gone with no checkpoint
    explaining why. That is itself the finding: a deletion outside the
    documented retention path.
    """

    event_id: uuid.UUID
    occurred_at: datetime | None
    expected_prev_hash: str | None
    actual_prev_hash: str | None


@dataclass(frozen=True)
class ChainVerification:
    """The chain's state as of this call — every field an admin needs to answer
    "has this been tampered with", not just a boolean.
    """

    verified_count: int
    #: Rows with no `row_hash` — written before the chain shipped. Reported, never
    #: silently folded into either "verified" or "broken".
    unverifiable_legacy_count: int
    chain_head_hash: str | None
    first_break: ChainBreak | None

    @property
    def ok(self) -> bool:
        return self.first_break is None


def verify_chain(session: Session) -> ChainVerification:
    """Walk the chain by FOLLOWING its `prev_hash` links backward from the
    current head — not by sorting rows on `occurred_at`.

    `occurred_at` is `server_default=func.now()`, which in Postgres reflects a
    transaction's START time, not its commit time. Two transactions can begin in
    one order and commit — and therefore get chained by `_chain_pending_audit_events`
    — in the other. The chain LINKS are always correct (`_lock_chain_state`
    serializes their assignment to true commit order), so verification must
    trust the links, not a timestamp column that can disagree with them under
    overlapping transactions. An earlier version of this function sorted by
    `(occurred_at, id)` and reported false breaks under exactly that scenario.

    A retention-purge checkpoint's `first_surviving_event_id` is the ONE row
    allowed to have a `prev_hash` that no longer names a live row (its
    `last_deleted_row_hash`) — matched by exact event id, so a tampered row
    cannot forge continuity by copying an unrelated checkpoint's hash.

    Loads every hashed row into memory rather than walking the chain via
    targeted per-hop queries — simpler to get correct, and not obviously worse
    at typical volumes (a hop-by-hop walk trades one query for O(chain length)
    of them). Tracked as a scaling concern, not a correctness one: #1597.
    """
    legacy_count = (
        session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.row_hash.is_(None))
        )
        or 0
    )
    rows = session.scalars(select(AuditEvent).where(AuditEvent.row_hash.isnot(None))).unique().all()
    by_hash: dict[str, AuditEvent] = {row.row_hash: row for row in rows if row.row_hash is not None}
    boundary: dict[uuid.UUID, str] = {
        cp.first_surviving_event_id: cp.last_deleted_row_hash
        for cp in session.scalars(select(AuditChainCheckpoint))
        if cp.first_surviving_event_id is not None and cp.last_deleted_row_hash is not None
    }

    state = session.get(AuditChainState, _STATE_ROW_ID)
    head_hash = state.head_hash if state else None

    verified = 0
    first_break: ChainBreak | None = None
    # What a dangling pointer should be blamed on: initially the head itself.
    blame_id = state.head_event_id if state else None
    current_hash = head_hash
    while current_hash is not None:
        row = by_hash.get(current_hash)
        if row is None:
            blamed = session.get(AuditEvent, blame_id) if blame_id is not None else None
            if blamed is not None:
                first_break = ChainBreak(blamed.id, blamed.occurred_at, current_hash, None)
            elif blame_id is not None:
                # The blamed row is gone too, with no checkpoint explaining it —
                # still a break, just one with no row left to read details from.
                first_break = ChainBreak(blame_id, None, current_hash, None)
            break
        if compute_row_hash(row, row.prev_hash) != row.row_hash:
            first_break = ChainBreak(row.id, row.occurred_at, row.prev_hash, row.row_hash)
            break
        verified += 1
        blame_id = row.id
        prev = row.prev_hash
        if prev is None or boundary.get(row.id) == prev:
            current_hash = None  # genuine genesis, or a documented purge boundary
        else:
            current_hash = prev

    return ChainVerification(
        verified_count=verified,
        unverifiable_legacy_count=legacy_count,
        chain_head_hash=head_hash,
        first_break=first_break,
    )


def write_purge_checkpoint(session: Session, *, cutoff: datetime) -> AuditChainCheckpoint | None:
    """Snapshot the chain boundary a retention purge is about to create.

    MUST be called (and its transaction committed) before any row older than
    `cutoff` is deleted. Returns `None` — writing nothing — when there is
    nothing to purge; a no-op sweep gets no checkpoint. Aggregates rather than
    loading every about-to-be-deleted row, since a large backlog is exactly the
    case `purge_expired_events` already chunks to avoid.

    Selects `last_deleted`/`first_surviving` by `occurred_at`, which is what
    retention is actually about (wall-clock age) — NOT by walking the chain's
    real `prev_hash` links (`verify_chain`'s approach, needed because it must
    detect tampering regardless of clock skew). Accepted residual risk: under
    genuinely overlapping transactions near the exact cutoff moment, chain
    (commit) order and `occurred_at` order can disagree by the width of that
    overlap (milliseconds), which could make this checkpoint name the wrong
    row pair. The failure mode is a false-positive `verify_chain` break at that
    one boundary — never a missed real tamper elsewhere in the chain — and the
    window is negligible next to a retention period measured in days.

    When NOTHING survives the cutoff (an idle workspace, or a lowered
    `AUDIT_RETENTION_DAYS`), the purge is about to delete the current chain
    head too — resets `AuditChainState` so the chain cleanly restarts at
    genesis on the next write, instead of leaving the head pointing at a row
    that no longer exists (a review-caught defect: `verify_chain` reported
    every such ordinary purge as tampering).
    """
    last_deleted = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.occurred_at < cutoff)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).first()
    if last_deleted is None:
        return None
    deleted_count = (
        session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.occurred_at < cutoff)
        )
        or 0
    )
    first_surviving = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.occurred_at >= cutoff)
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
        .limit(1)
    ).first()
    if first_surviving is None:
        # Nothing survives the cutoff — the purge is about to delete the current
        # chain head too. Left alone, `AuditChainState` would keep pointing at a
        # row that no longer exists, and every `verify_chain` call afterward
        # would report a dangling-pointer break for an ordinary, documented
        # purge. Reset the head so the chain cleanly restarts at genesis on the
        # next write — the honest state, since nothing hashed survives to link
        # onto.
        state = _lock_chain_state(session)
        state.head_hash = None
        state.head_event_id = None
    checkpoint = AuditChainCheckpoint(
        cutoff=cutoff,
        deleted_count=deleted_count,
        last_deleted_event_id=last_deleted.id,
        last_deleted_row_hash=last_deleted.row_hash,
        first_surviving_event_id=first_surviving.id if first_surviving else None,
        anchored=False,
    )
    session.add(checkpoint)
    session.flush([checkpoint])
    return checkpoint
