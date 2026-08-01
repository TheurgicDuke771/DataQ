"""Stateful monitor-kind dispatch (#593) — one executor callable, N engines.

`run_service` takes a single `stateful_monitor_executor`; there are now two
stateful kinds with different engines, so this is the routing between them. The
failure this exists to prevent is a kind being silently handed to the WRONG
engine — the half-finished-kind shape the registry's own module comment warns
about.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import STATEFUL_MONITOR_KINDS
from backend.app.db.models import Check, Connection
from backend.app.services import stateful_monitors


class _FakeStore:
    def get(self, name: str) -> str:
        return "secret"

    def set(self, name: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


def _check(kind: str) -> Check:
    return Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name=kind,
        kind=kind,
        expectation_type=f"monitor:{kind}",
        config={},
    )


def _build(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> Any:
    """Replace both engines with recorders, so the assertion is about ROUTING."""

    def fake_builder(name: str) -> Any:
        def builder(session: Any, **kwargs: Any) -> Any:
            calls.append(f"built:{name}")

            def executor(check: Check) -> CheckOutcome:
                calls.append(f"ran:{name}")
                return CheckOutcome(check.expectation_type, success=True)

            return executor

        return builder

    monkeypatch.setattr(
        stateful_monitors,
        "_BUILDERS",
        {"schema_drift": fake_builder("drift"), "anomaly": fake_builder("anomaly")},
    )
    # The session/connection are opaque to the dispatcher — it only forwards them
    # — so the recorders above stand in for the engines that would use them.
    return stateful_monitors.build_stateful_monitor_executor(
        cast(Session, None),
        connection=cast(Connection, None),
        target_table="T",
        target_schema=None,
        target_catalog=None,
        secret_store=_FakeStore(),
    )


def test_every_stateful_kind_has_an_engine() -> None:
    """The registry decides which kinds are stateful; this module decides how each
    one runs. A kind in the first set and missing from the second is dispatchable
    but unrunnable — exactly the half-finished state the seam warns about."""
    assert set(STATEFUL_MONITOR_KINDS) == set(stateful_monitors._BUILDERS)


def test_each_kind_reaches_its_own_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    executor = _build(monkeypatch, calls)
    executor(_check("schema_drift"))
    executor(_check("anomaly"))
    assert calls == ["built:drift", "ran:drift", "built:anomaly", "ran:anomaly"]


def test_an_engine_is_built_once_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    executor = _build(monkeypatch, calls)
    for _ in range(3):
        executor(_check("anomaly"))
    assert calls.count("built:anomaly") == 1
    assert calls.count("ran:anomaly") == 3


def test_an_unwired_stateful_kind_errors_that_check_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a crash and not a silent fall-through to the other engine: the check
    reports its own operational error (#122) and its siblings still run."""
    calls: list[str] = []
    executor = _build(monkeypatch, calls)
    outcome = executor(_check("some_future_kind"))
    assert outcome.errored is True
    assert "some_future_kind" in (outcome.error_message or "")
    assert calls == []
    # a sibling still routes normally afterwards
    assert executor(_check("anomaly")).errored is False
