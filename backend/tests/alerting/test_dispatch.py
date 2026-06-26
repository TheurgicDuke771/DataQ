"""Tests for the run-completion publish hook.

DB-backed: status gating (only succeeded/failed publish; cancelled/running
don't), the missing-run guard, the captured report shape, and — the safety
property — that a publisher exception is swallowed so a broken channel can never
fail the task. Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.alerting import dispatch, registry
from backend.app.alerting.base import RunReport
from backend.app.db.models import Check, Connection, Result, Run, Suite, User


class _SpyPublisher:
    def __init__(self, *, boom: bool = False) -> None:
        self.reports: list[RunReport] = []
        self._boom = boom

    def publish(self, report: RunReport) -> None:
        if self._boom:
            raise RuntimeError("channel down")
        self.reports.append(report)


def _run(db: Any, *, run_status: str, with_result: bool = True) -> Run:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db.add(suite)
    db.flush()
    run = Run(suite_id=suite.id, status=run_status)
    db.add(run)
    db.flush()
    if with_result:
        check = Check(suite_id=suite.id, name="c", expectation_type="e", config={})
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status="fail"))
    db.commit()
    return run


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _SpyPublisher:
    publisher = _SpyPublisher()
    monkeypatch.setattr(registry, "get_result_publisher", lambda: publisher)
    return publisher


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_publishes_terminal_executed_runs(db_session: Any, spy: _SpyPublisher, status: str) -> None:
    run = _run(db_session, run_status=status, with_result=status == "succeeded")

    assert dispatch.publish_run_outcome(db_session, run_id=run.id) is True
    assert len(spy.reports) == 1
    assert spy.reports[0].run_id == run.id
    assert spy.reports[0].run_status == status


@pytest.mark.parametrize("status", ["cancelled", "queued", "running"])
def test_does_not_publish_non_publishable_status(
    db_session: Any, spy: _SpyPublisher, status: str
) -> None:
    run = _run(db_session, run_status=status, with_result=False)

    assert dispatch.publish_run_outcome(db_session, run_id=run.id) is False
    assert spy.reports == []


def test_missing_run_is_a_noop(db_session: Any, spy: _SpyPublisher) -> None:
    assert dispatch.publish_run_outcome(db_session, run_id=uuid.uuid4()) is False
    assert spy.reports == []


def test_publisher_exception_is_swallowed(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "get_result_publisher", lambda: _SpyPublisher(boom=True))
    run = _run(db_session, run_status="failed", with_result=False)

    # Must not raise — a broken channel can't fail the run/task.
    assert dispatch.publish_run_outcome(db_session, run_id=run.id) is False


def test_dedup_suppresses_an_unchanged_repeat(db_session: Any, spy: _SpyPublisher) -> None:
    # Two consecutive runs of one suite, the same check failing both times.
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    check = Check(suite_id=suite.id, name="c", expectation_type="e", config={})
    db_session.add(check)
    db_session.flush()
    base = datetime(2026, 6, 26, tzinfo=UTC)
    r1 = Run(suite_id=suite.id, status="succeeded", created_at=base)
    r2 = Run(suite_id=suite.id, status="succeeded", created_at=base + timedelta(minutes=5))
    db_session.add_all([r1, r2])
    db_session.flush()
    db_session.add(Result(run_id=r1.id, check_id=check.id, status="fail"))
    db_session.add(Result(run_id=r2.id, check_id=check.id, status="fail"))
    db_session.commit()

    # First failure fires…
    assert dispatch.publish_run_outcome(db_session, run_id=r1.id) is True
    # …the identical repeat is deduped (no second card).
    assert dispatch.publish_run_outcome(db_session, run_id=r2.id) is False
    assert [r.run_id for r in spy.reports] == [r1.id]
