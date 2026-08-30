"""Tests for the llm_invocations reaper (`llm_service.reap_stuck_invocations`, #1644)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.db.models import LlmInvocation
from backend.app.services import llm_service

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def _invocation(
    db_session: Any,
    *,
    status: str,
    created_min_ago: int,
    started_min_ago: int | None = None,
) -> LlmInvocation:
    invocation = LlmInvocation(
        kind="sql_generation",
        status=status,
        created_at=NOW - timedelta(minutes=created_min_ago),
        started_at=(
            (NOW - timedelta(minutes=started_min_ago)) if started_min_ago is not None else None
        ),
    )
    db_session.add(invocation)
    db_session.commit()
    return invocation


def _reap(db_session: Any) -> list[LlmInvocation]:
    return llm_service.reap_stuck_invocations(
        db_session, pending_threshold_minutes=15, running_threshold_minutes=10, now=NOW
    )


def test_reaps_pending_row_past_threshold(db_session: Any) -> None:
    stuck = _invocation(db_session, status="pending", created_min_ago=20)

    reaped = _reap(db_session)

    assert [i.id for i in reaped] == [stuck.id]
    db_session.refresh(stuck)
    assert stuck.status == "failed"
    assert stuck.finished_at == NOW
    assert stuck.error == llm_service._PENDING_REAP_REASON


def test_reaps_running_row_past_threshold(db_session: Any) -> None:
    stuck = _invocation(db_session, status="running", created_min_ago=30, started_min_ago=15)

    reaped = _reap(db_session)

    assert [i.id for i in reaped] == [stuck.id]
    db_session.refresh(stuck)
    assert stuck.status == "failed"
    assert stuck.finished_at == NOW
    assert stuck.error == llm_service._RUNNING_REAP_REASON


def test_does_not_reap_fresh_pending_row(db_session: Any) -> None:
    fresh = _invocation(db_session, status="pending", created_min_ago=2)

    assert _reap(db_session) == []
    db_session.refresh(fresh)
    assert fresh.status == "pending"


def test_does_not_reap_recently_started_running_row(db_session: Any) -> None:
    alive = _invocation(db_session, status="running", created_min_ago=30, started_min_ago=2)

    assert _reap(db_session) == []
    db_session.refresh(alive)
    assert alive.status == "running"


def test_does_not_reap_terminal_rows(db_session: Any) -> None:
    for status in ("succeeded", "failed"):
        row = _invocation(db_session, status=status, created_min_ago=999, started_min_ago=998)
        assert _reap(db_session) == []
        db_session.refresh(row)
        assert row.status == status


def test_reap_updates_are_conditioned_on_status_at_the_sql_level(db_session: Any) -> None:
    """#1716 review: a plain select-then-mutate-then-commit races a row a worker
    legitimately finishes in between (ORM autoflush emits `WHERE id = :id` only, no
    re-check) — proven by clobbering the row in that scenario. The fix must issue
    the UPDATE itself with a `status =` guard, re-checked by Postgres at UPDATE
    time, so this asserts the guard is actually on the wire rather than relying on
    a timing-dependent thread interleaving to (maybe) reproduce it.
    """
    from sqlalchemy import event

    _invocation(db_session, status="pending", created_min_ago=20)
    statements: list[str] = []

    def _capture(_conn: Any, _cursor: Any, statement: str, *_a: Any) -> None:
        if statement.strip().upper().startswith("UPDATE") and "llm_invocations" in statement:
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        _reap(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert statements, "the reap call issued no UPDATE — nothing was exercised"
    for statement in statements:
        where_clause = statement.upper().split("WHERE", 1)[1]
        assert "STATUS" in where_clause, (
            f"UPDATE has no status guard in its WHERE clause, only: {where_clause!r} "
            "— a concurrently-finished row can be clobbered back to `failed`"
        )


def test_disabled_when_threshold_non_positive(db_session: Any) -> None:
    pending = _invocation(db_session, status="pending", created_min_ago=999)
    running = _invocation(db_session, status="running", created_min_ago=999, started_min_ago=998)

    reaped = llm_service.reap_stuck_invocations(
        db_session, pending_threshold_minutes=0, running_threshold_minutes=0, now=NOW
    )

    assert reaped == []
    db_session.refresh(pending)
    db_session.refresh(running)
    assert pending.status == "pending"
    assert running.status == "running"
