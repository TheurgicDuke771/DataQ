"""Unit test for run_dispatch.dispatch_run — publishes run_suite by name.

No broker: celery_app.send_task is spied. Asserts the task is published by its
registered name with the run id as the sole arg, and that a publish failure
propagates (callers own the stuck-run policy).
"""

import uuid
from typing import Any

import pytest

from backend.app.services import run_dispatch

pytestmark = pytest.mark.real_dispatch


def test_dispatch_run_sends_task_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        run_dispatch.celery_app,
        "send_task",
        lambda name, args: calls.append((name, args)),
    )
    run_id = uuid.uuid4()

    run_dispatch.dispatch_run(run_id)

    # Published by registered name (decoupled from worker.tasks — no import edge),
    # with the run id stringified for JSON serialisation.
    assert calls == [("run_suite", [str(run_id)])]


def test_dispatch_run_propagates_broker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str, args: Any) -> None:
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(run_dispatch.celery_app, "send_task", _boom)

    with pytest.raises(RuntimeError):
        run_dispatch.dispatch_run(uuid.uuid4())
