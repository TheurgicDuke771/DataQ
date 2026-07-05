"""Unit tests for run_dispatch — dispatch / revoke / dispatch-failure shape.

No broker: celery_app.send_task is spied. Asserts the task is published by its
registered name with the run id as the sole arg, that the captured task id is
returned, that a publish failure propagates (callers own the stuck-run policy),
and the canonical terminal-failed shape (#227).
"""

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from backend.app.db.models import Run
from backend.app.services import run_dispatch
from backend.app.worker.celery_app import celery_app

pytestmark = pytest.mark.real_dispatch


class _FakeSession:
    """Minimal stand-in: dispatch_or_fail only needs session.commit()."""

    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_dispatch_run_sends_task_by_name_and_returns_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def _send(name: str, args: list[str]) -> SimpleNamespace:
        calls.append((name, args))
        return SimpleNamespace(id="celery-task-123")

    monkeypatch.setattr(celery_app, "send_task", _send)
    run_id = uuid.uuid4()

    task_id = run_dispatch.dispatch_run(run_id)

    # Published by registered name (decoupled from worker.tasks — no import edge),
    # with the run id stringified for JSON serialisation; the AsyncResult id is
    # returned so the caller can store it on the run for later revoke.
    assert calls == [("run_suite", [str(run_id)])]
    assert task_id == "celery-task-123"


def test_mark_dispatch_failed_sets_canonical_shape() -> None:
    """#227: one definition of the dispatch-failure shape — failed + finished_at
    set, started_at left NULL (it never started)."""
    run = Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")
    run_dispatch.mark_dispatch_failed(run)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.started_at is None


def test_revoke_run_noop_without_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _revoke(_task_id: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(celery_app.control, "revoke", _revoke)
    run_dispatch.revoke_run(None)  # un-dispatched run → no broker call
    assert called is False


def test_revoke_run_swallows_broker_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_task_id: str) -> None:
        raise RuntimeError("control bus unreachable")

    monkeypatch.setattr(celery_app.control, "revoke", _boom)
    # Best-effort: the DB status is already 'cancelled' + the worker checks
    # cooperatively, so a broker error must not propagate.
    run_dispatch.revoke_run("task-1")


def test_dispatch_run_propagates_broker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str, args: Any) -> None:
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(celery_app, "send_task", _boom)

    with pytest.raises(RuntimeError):
        run_dispatch.dispatch_run(uuid.uuid4())


def test_dispatch_or_fail_success_sets_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: the run keeps its `queued` status, gets the dispatched task id,
    and the helper commits + returns True."""
    monkeypatch.setattr(run_dispatch, "dispatch_run", lambda _run_id: "task-9")
    session = _FakeSession()
    run = Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")

    ok = run_dispatch.dispatch_or_fail(cast(Session, session), run)

    assert ok is True
    assert run.celery_task_id == "task-9"
    assert run.status == "queued"
    assert session.commits == 1


def test_dispatch_or_fail_broker_failure_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Broker down: the helper records the canonical terminal-failed shape,
    commits, and returns False (caller owns the 503 / log-and-skip policy)."""

    def _boom(_run_id: uuid.UUID) -> str:
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _boom)
    session = _FakeSession()
    run = Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")

    ok = run_dispatch.dispatch_or_fail(cast(Session, session), run)

    assert ok is False
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.started_at is None
    assert run.celery_task_id is None  # never published → no stale id for revoke
    assert session.commits == 1


def test_dispatch_auto_classify_sends_task_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def _send(name: str, args: list[str]) -> SimpleNamespace:
        calls.append((name, args))
        return SimpleNamespace()

    monkeypatch.setattr(celery_app, "send_task", _send)
    sid = uuid.uuid4()
    run_dispatch.dispatch_auto_classify(sid)
    assert calls == [("auto_classify_columns", [str(sid)])]


def test_dispatch_auto_classify_is_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker blip must never fail suite create/update (#634)."""

    def _boom(name: str, args: list[str]) -> Any:
        raise RuntimeError("broker down")

    monkeypatch.setattr(celery_app, "send_task", _boom)
    run_dispatch.dispatch_auto_classify(uuid.uuid4())  # must not raise
