"""Tests for the llm_invocations reaper (`llm_service.reap_stuck_invocations`, #1644)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.core.config import get_settings
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


def test_default_thresholds_carry_margin_for_the_shared_queue_and_warehouse_profiling(
    monkeypatch: Any,
) -> None:
    """#1726: 15m pending / 10m running were measured against dispatch-loss and the
    120s provider timeout alone. Neither accounted for `llm_invoke` sharing its
    Celery queue with long `run_suite` backlogs (pending), or for the
    check-suggestion builder's live warehouse profiling running INSIDE the
    `running` window alongside the provider's own worst case (up to two 120s
    attempts, `max_retries=1`). A default that silently drops back toward the
    tight originals would reintroduce the false-kill this issue exists to fix,
    with no test anywhere else in the suite positioned to catch it — the
    threshold-behavior tests above all pass explicit values.
    """
    monkeypatch.delenv("LLM_INVOCATION_PENDING_THRESHOLD_MINUTES", raising=False)
    monkeypatch.delenv("LLM_INVOCATION_RUNNING_THRESHOLD_MINUTES", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.llm_invocation_pending_threshold_minutes >= 30
        assert settings.llm_invocation_running_threshold_minutes >= 20
    finally:
        get_settings.cache_clear()


def test_reap_reasons_admit_ambiguity_rather_than_assert_a_cause_neither_can_verify(
    db_session: Any,
) -> None:
    """#1726: the pre-fix wording stated "the dispatch was likely lost" and "the
    worker never finished" as the cause — confident and, for a merely-queued or
    merely-slow call, false. Neither reap can distinguish "genuinely dead" from
    "still alive and just slow" (no celery task id is stored to check the broker),
    so the reason a caller reads back must say so instead of asserting a verdict.
    """
    pending = _invocation(db_session, status="pending", created_min_ago=999)
    running = _invocation(db_session, status="running", created_min_ago=999, started_min_ago=998)

    llm_service.reap_stuck_invocations(
        db_session, pending_threshold_minutes=15, running_threshold_minutes=10, now=NOW
    )

    db_session.refresh(pending)
    db_session.refresh(running)
    pending_error = pending.error
    running_error = running.error
    assert pending_error is not None
    assert running_error is not None
    assert "may have been lost" in pending_error
    assert "nothing is currently consuming" in pending_error
    assert "was lost" not in pending_error  # no unqualified claim
    assert "may have been killed" in running_error
    assert "never finished" not in running_error  # no unqualified claim


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
