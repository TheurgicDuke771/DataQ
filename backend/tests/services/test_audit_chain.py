"""Hash-chain tamper-evidence — ADR 0041 §9 / #1460."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.db.models import AuditChainCheckpoint, AuditChainState, AuditEvent
from backend.app.services import audit_chain, audit_service


def _record(session: Any, *, suffix: str = "") -> AuditEvent:
    return audit_service.record(
        session,
        action=f"check.update{suffix}",
        entity_type="check",
        entity_id=uuid.uuid4(),
        actor=None,
        actor_kind="webhook",
    )


def test_two_events_in_one_commit_chain_onto_each_other(db_session: Any) -> None:
    """`record()` itself never flushes — the chain is built by the `before_commit`
    hook, right before this session's own commit.
    """
    first = _record(db_session, suffix=".a")
    second = _record(db_session, suffix=".b")
    assert first.row_hash is None and second.row_hash is None  # not yet — pre-commit

    db_session.commit()

    assert first.row_hash is not None
    assert second.row_hash is not None
    assert second.prev_hash == first.row_hash
    state = db_session.get(AuditChainState, 1)
    assert state is not None
    assert state.head_hash == second.row_hash
    assert state.head_event_id == second.id


def test_a_later_commit_chains_onto_the_earlier_ones_head(db_session: Any) -> None:
    _record(db_session)
    db_session.commit()
    state_after_first = db_session.get(AuditChainState, 1)
    assert state_after_first is not None
    head_after_first = state_after_first.head_hash

    third = _record(db_session)
    db_session.commit()

    assert third.prev_hash == head_after_first


def test_verify_chain_reports_ok_on_an_untampered_chain(db_session: Any) -> None:
    """Counts are deltas, not absolutes: `audit_events` is shared, whole-process
    state (ADR 0041 §9's own design — one chain over the whole table), and other
    tests in the same run legitimately commit real rows outside this test's
    transaction. `result.ok` is the property actually under test here.
    """
    baseline = audit_chain.verify_chain(db_session)
    assert baseline.ok, baseline.first_break

    _record(db_session)
    _record(db_session)
    db_session.commit()

    result = audit_chain.verify_chain(db_session)

    assert result.ok
    assert result.first_break is None
    assert result.verified_count == baseline.verified_count + 2


def test_verify_chain_catches_a_direct_row_mutation(db_session: Any) -> None:
    """The scenario the whole feature exists for: someone edits a row directly,
    bypassing the app entirely.
    """
    event = _record(db_session)
    db_session.commit()

    event.after = {"tampered": True}
    db_session.commit()

    result = audit_chain.verify_chain(db_session)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.event_id == event.id


def test_a_dangling_head_pointer_blames_the_row_it_named(db_session: Any) -> None:
    """The chain head points at a hash no live row carries — a row was deleted
    outside the documented retention path (no checkpoint), or the state row
    itself was edited directly. The blamed row (`head_event_id`) still exists,
    just under a different `row_hash` than the head claims.
    """
    event = _record(db_session)
    db_session.commit()

    state = db_session.get(AuditChainState, 1)
    assert state is not None
    state.head_hash = "0" * 64  # a hash no row carries
    db_session.commit()

    result = audit_chain.verify_chain(db_session)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.event_id == event.id
    assert result.first_break.actual_prev_hash is None


def test_a_dangling_head_pointer_with_no_row_left_to_blame(db_session: Any) -> None:
    """Both the head hash AND the row it names are gone — still a break, just
    one with no row left to read `occurred_at` from.
    """
    state = db_session.get(AuditChainState, 1)
    if state is None:
        state = AuditChainState(id=1)
        db_session.add(state)
    state.head_hash = "0" * 64
    state.head_event_id = uuid.uuid4()  # names no real row
    db_session.commit()

    result = audit_chain.verify_chain(db_session)

    assert not result.ok
    assert result.first_break is not None
    assert result.first_break.event_id == state.head_event_id
    assert result.first_break.occurred_at is None


def test_a_row_with_no_hash_is_reported_as_legacy_not_a_break(db_session: Any) -> None:
    """A row written before the chain shipped (`row_hash IS NULL`, never
    backfilled) must never be silently counted as verified OR flagged as tampered
    — it is a third, honestly-reported state.
    """
    baseline = audit_chain.verify_chain(db_session)
    assert baseline.ok, baseline.first_break

    legacy = AuditEvent(
        action_class="config",
        action="check.update",
        entity_type="check",
        entity_id=uuid.uuid4(),
        actor_kind="webhook",
    )
    db_session.add(legacy)
    db_session.flush()  # bypasses the before_commit hook's `row_hash is None` pending scan
    legacy.row_hash = None
    db_session.commit()

    result = audit_chain.verify_chain(db_session)

    assert result.unverifiable_legacy_count == baseline.unverifiable_legacy_count + 1
    assert result.verified_count == baseline.verified_count  # not counted as verified either
    assert result.ok  # no hashed rows to break


def test_verify_chain_accepts_a_documented_checkpoint_boundary(db_session: Any) -> None:
    """`verify_chain`'s side of the retention-purge contract, tested in
    isolation from `write_purge_checkpoint`'s row-selection query: a checkpoint
    naming this row as `first_surviving_event_id`, with `last_deleted_row_hash`
    equal to whatever this row's REAL `prev_hash` already is, must let the walk
    treat that link as legitimate rather than a break — regardless of what that
    `prev_hash` value actually is, since `audit_events` is shared, whole-process
    state (ADR 0041 §9) and this row's true predecessor may be another test's
    real commit, not one this test controls.

    `write_purge_checkpoint`'s OWN row-selection (picking the right
    `last_deleted`/`first_surviving` pair by `occurred_at`) is covered
    precisely, and safely, on an isolated throwaway database by
    `test_the_sweep_writes_a_checkpoint_before_deleting` in
    `tests/db/test_audit_retention_privileges.py` — constructing that pairing
    by hand here, from a synthetic `occurred_at` (see that function's own
    accepted-limitation note), previously made THIS test flaky under ambient
    pollution rather than testing what it says it tests.
    """
    survivor = _record(db_session, suffix=".survivor")
    db_session.commit()
    # The REAL predecessor this session sees right now — whatever committed
    # before it, from any test.
    real_predecessor_hash = survivor.prev_hash

    checkpoint = AuditChainCheckpoint(
        cutoff=datetime.now(UTC),
        deleted_count=1,
        last_deleted_event_id=uuid.uuid4(),
        last_deleted_row_hash=real_predecessor_hash,
        first_surviving_event_id=survivor.id,
        anchored=False,
    )
    db_session.add(checkpoint)
    db_session.commit()

    result = audit_chain.verify_chain(db_session)

    assert result.ok, result.first_break


def test_purge_checkpoint_resets_the_head_when_nothing_survives(db_session: Any) -> None:
    """A cutoff past every existing row's `occurred_at` deletes the current
    chain head too — a real scenario (an idle workspace, or an admin lowering
    `AUDIT_RETENTION_DAYS`). Left unhandled, `AuditChainState` would keep
    pointing at a row about to be deleted, and every later `verify_chain` call
    would report a dangling-pointer break for an ordinary, documented purge
    (the review finding this test locks in). A cutoff in the FUTURE guarantees
    "nothing survives" regardless of ambient data from other tests.
    """
    _record(db_session, suffix=".about-to-be-the-head")
    db_session.commit()

    future_cutoff = datetime.now(UTC) + timedelta(days=1)
    checkpoint = audit_chain.write_purge_checkpoint(db_session, cutoff=future_cutoff)
    assert checkpoint is not None
    assert checkpoint.first_surviving_event_id is None
    db_session.commit()

    state = db_session.get(AuditChainState, 1)
    assert state is not None
    assert state.head_hash is None
    assert state.head_event_id is None

    result = audit_chain.verify_chain(db_session)
    assert result.ok, result.first_break
    assert result.chain_head_hash is None


def test_purge_checkpoint_is_none_when_nothing_is_older_than_cutoff(db_session: Any) -> None:
    _record(db_session)
    db_session.commit()

    checkpoint = audit_chain.write_purge_checkpoint(
        db_session, cutoff=datetime.now(UTC) - timedelta(days=365)
    )

    assert checkpoint is None
    assert db_session.query(AuditChainCheckpoint).count() == 0
