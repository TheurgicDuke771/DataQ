"""Tests for the stuck-run reaper (`run_service.reap_stuck_runs`, #309).

DB-backed (real Postgres): the reaper is a time-windowed scan over the `runs`
lifecycle keyed on status + COALESCE(started_at, created_at), so it's exercised
against the real engine. Verifies it fails only non-terminal runs past the
threshold, measures staleness from the most-recent lifecycle timestamp (so a
recently-*started* run isn't reaped on an old created_at), leaves terminal runs
and fresh runs alone, preserves the canonical terminal-failed shape, and honours
the disable sentinel. Skips without TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.db.models import Connection, Run, Suite, User
from backend.app.services import run_dispatch, run_service

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def _suite(db_session: Any) -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    return suite


def _run(
    db_session: Any,
    *,
    status: str,
    created_min_ago: int,
    started_min_ago: int | None = None,
) -> Run:
    run = Run(
        suite_id=_suite(db_session).id,
        status=status,
        created_at=NOW - timedelta(minutes=created_min_ago),
        started_at=(
            (NOW - timedelta(minutes=started_min_ago)) if started_min_ago is not None else None
        ),
    )
    db_session.add(run)
    db_session.commit()
    return run


def _reap(db_session: Any) -> list[Run]:
    return run_service.reap_stuck_runs(db_session, threshold_minutes=60, now=NOW)


def test_reaps_queued_run_past_threshold(db_session: Any) -> None:
    """The orphan window: queued, never dispatched, older than the threshold."""
    stuck = _run(db_session, status="queued", created_min_ago=90)

    reaped = _reap(db_session)

    assert [r.id for r in reaped] == [stuck.id]
    db_session.refresh(stuck)
    assert stuck.status == "failed"
    assert stuck.finished_at == NOW
    assert stuck.started_at is None  # never started → left NULL (canonical shape)
    assert stuck.failure_reason == run_dispatch.REAPED_REASON  # #605


def test_reaps_running_run_stuck_past_threshold(db_session: Any) -> None:
    """A worker died mid-execution: running, started long ago."""
    stuck = _run(db_session, status="running", created_min_ago=120, started_min_ago=90)

    reaped = _reap(db_session)

    assert [r.id for r in reaped] == [stuck.id]
    db_session.refresh(stuck)
    assert stuck.status == "failed"
    assert stuck.finished_at == NOW
    # started_at is preserved (it really did start) for duration/history views
    assert stuck.started_at == NOW - timedelta(minutes=90)


def test_does_not_reap_recently_started_running_run(db_session: Any) -> None:
    """Staleness is COALESCE(started_at, created_at): an actively-running run that
    *started* recently is safe even if it was created long ago (sat queued)."""
    alive = _run(db_session, status="running", created_min_ago=120, started_min_ago=5)

    assert _reap(db_session) == []
    db_session.refresh(alive)
    assert alive.status == "running"


def test_does_not_reap_fresh_queued_run(db_session: Any) -> None:
    """A just-created queued run is mid-dispatch, not stuck."""
    fresh = _run(db_session, status="queued", created_min_ago=2)

    assert _reap(db_session) == []
    db_session.refresh(fresh)
    assert fresh.status == "queued"


def test_does_not_reap_terminal_runs(db_session: Any) -> None:
    """succeeded / failed / cancelled are terminal — never reaped, however old."""
    for status in ("succeeded", "failed", "cancelled"):
        run = _run(db_session, status=status, created_min_ago=999, started_min_ago=998)
        reaped = _reap(db_session)
        assert reaped == []
        db_session.refresh(run)
        assert run.status == status


def test_disabled_when_threshold_non_positive(db_session: Any) -> None:
    stuck = _run(db_session, status="queued", created_min_ago=999)

    assert run_service.reap_stuck_runs(db_session, threshold_minutes=0, now=NOW) == []
    assert run_service.reap_stuck_runs(db_session, threshold_minutes=-1, now=NOW) == []
    db_session.refresh(stuck)
    assert stuck.status == "queued"  # untouched


def test_reap_emits_terminal_lineage_only_for_started_runs(
    db_session: Any, monkeypatch: Any
) -> None:
    """Review finding on #765: a reaped `running` run emitted an OpenLineage START
    (the worker got that far), so the reaper must close it with a terminal event —
    while a reaped `queued` run never emitted a START and must get none."""
    from backend.app.lineage import dispatch as lineage_dispatch

    emitted: list[uuid.UUID] = []
    monkeypatch.setattr(
        lineage_dispatch,
        "emit_run_lineage_terminal",
        lambda _s, *, run_id: emitted.append(run_id),
    )
    was_running = _run(db_session, status="running", created_min_ago=120, started_min_ago=90)
    was_queued = _run(db_session, status="queued", created_min_ago=90)

    reaped = _reap(db_session)

    assert {r.id for r in reaped} == {was_running.id, was_queued.id}
    # Only the run that had actually started (→ had a START) gets the closing FAIL.
    assert emitted == [was_running.id]
