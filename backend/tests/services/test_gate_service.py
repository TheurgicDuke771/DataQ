"""The pipeline DQ gate (ADR 0046) against a real Postgres."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from backend.app.core.errors import DataQError
from backend.app.db.models import Check, Connection, Result, Run, Share, Suite, TriggerBinding, User
from backend.app.orchestration.adf import AdfProvider
from backend.app.orchestration.base import RunUpdate
from backend.app.services import gate_service
from backend.app.services.gate_service import (
    GateBindingNotFoundError,
    GateForbiddenError,
    evaluate_gate,
)
from backend.app.services.orchestration_service import ingest_event
from backend.tests.support.fake_secret_store import FakeSecretStore

_PIPELINE = "load_finance"


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"g-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def _connection(db_session: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"adf-{uuid.uuid4().hex[:6]}",
        type="adf",
        env="dev",
        config={
            "subscription_id": "00000000-0000-0000-0000-000000000001",
            "resource_group": "rg-data",
            "factory_name": "example-adf-preprod",
            "tenant_id": "00000000-0000-0000-0000-0000000000aa",
            "client_id": "00000000-0000-0000-0000-0000000000bb",
        },
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _bound_suite(db_session: Any, owner: User, conn: Connection, *, enabled: bool = True) -> Suite:
    suite = Suite(name=f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    db_session.add(
        TriggerBinding(
            provider="adf",
            pipeline_or_dag_id=_PIPELINE,
            env="dev",
            suite_id=suite.id,
            enabled=enabled,
        )
    )
    db_session.commit()
    return suite


def _gate(db_session: Any, user: User, **kw: Any) -> gate_service.GateOutcome:
    args: dict[str, Any] = {
        "provider": "adf",
        "pipeline_or_dag_id": _PIPELINE,
        "env": "dev",
        "provider_run_id": "run-1",
    }
    args.update(kw)
    return evaluate_gate(db_session, user_id=user.id, **args)


def _finish(
    db_session: Any, run_id: uuid.UUID | None, *statuses: str, run_status: str = "succeeded"
) -> None:
    assert run_id is not None
    run = db_session.get(Run, run_id)
    run.status = run_status
    for status in statuses:
        check = Check(
            suite_id=run.suite_id,
            name=f"c-{uuid.uuid4().hex[:6]}",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "id"},
        )
        db_session.add(check)
        db_session.flush()
        db_session.add(Result(run_id=run.id, check_id=check.id, status=status))
    db_session.commit()


def _runs(db_session: Any) -> int:
    return int(db_session.scalar(select(func.count()).select_from(Run)))


@pytest.fixture
def owner_and_suite(db_session: Any) -> tuple[User, Suite]:
    owner = _user(db_session)
    return owner, _bound_suite(db_session, owner, _connection(db_session, owner))


def test_trigger_mode_starts_one_run_and_reports_it_running(
    db_session: Any, owner_and_suite: tuple[User, Suite], stub_run_dispatch: list[str]
) -> None:
    owner, suite = owner_and_suite

    outcome = _gate(db_session, owner)

    assert outcome.state == "running"
    assert outcome.created_runs == 1
    assert outcome.retry_after_seconds is not None
    run = db_session.scalars(select(Run)).one()
    assert (run.suite_id, run.triggered_by) == (suite.id, "adf:load_finance:run-1")
    assert stub_run_dispatch == [str(run.id)]


def test_polling_the_gate_never_starts_a_second_run(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    owner, _ = owner_and_suite

    _gate(db_session, owner)
    again = _gate(db_session, owner)

    assert again.created_runs == 0
    assert _runs(db_session) == 1


def test_the_pipelines_own_success_event_reuses_the_gates_run(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    # The gate and the success-event trigger share one marker, so the suite runs once.
    owner, _ = owner_and_suite
    _gate(db_session, owner)

    ingest_event(
        db_session,
        provider_impl=AdfProvider(),
        update=RunUpdate(
            provider_run_id="run-1",
            pipeline_or_dag_id=_PIPELINE,
            resource_name="example-adf-preprod",
            status="succeeded",
        ),
        secret_store=FakeSecretStore(),
    )

    assert _runs(db_session) == 1


@pytest.mark.parametrize(
    ("statuses", "fail_on", "expected"),
    [
        (("pass", "pass"), "fail", "passed"),
        (("pass", "warn"), "fail", "passed"),
        (("pass", "warn"), "warn", "failed"),
        (("fail",), "fail", "failed"),
        (("fail",), "critical", "passed"),
        (("critical",), "critical", "failed"),
        (("pass", "error"), "fail", "error"),
        (("pass", "skip"), "fail", "passed"),
    ],
)
def test_a_finished_run_is_judged_against_fail_on(
    db_session: Any,
    owner_and_suite: tuple[User, Suite],
    statuses: tuple[str, ...],
    fail_on: str,
    expected: str,
) -> None:
    owner, _ = owner_and_suite
    started = _gate(db_session, owner)
    _finish(db_session, started.suites[0].run_id, *statuses)

    outcome = _gate(db_session, owner, fail_on=fail_on)

    assert outcome.state == expected
    assert outcome.retry_after_seconds is None


@pytest.mark.parametrize("run_status", ["failed", "cancelled"])
def test_a_run_that_did_not_complete_is_an_error_not_a_pass(
    db_session: Any, owner_and_suite: tuple[User, Suite], run_status: str
) -> None:
    owner, _ = owner_and_suite
    started = _gate(db_session, owner)
    _finish(db_session, started.suites[0].run_id, run_status=run_status)

    assert _gate(db_session, owner).state == "error"


def test_one_failing_suite_fails_the_gate_while_another_still_runs(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    owner, first = owner_and_suite
    _bound_suite(db_session, owner, db_session.get(Connection, first.connection_id))
    started = _gate(db_session, owner)
    failing = next(s for s in started.suites if s.suite_id == first.id)
    _finish(db_session, failing.run_id, "fail")

    outcome = _gate(db_session, owner)

    assert outcome.state == "failed"
    assert {s.state for s in outcome.suites} == {"failed", "running"}


def test_status_only_mode_creates_nothing_and_waits_for_the_trigger(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    owner, _ = owner_and_suite

    outcome = _gate(db_session, owner, trigger=False)

    assert outcome.state == "awaiting_trigger"
    assert _runs(db_session) == 0


def test_status_only_needs_view_and_trigger_needs_edit(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    _, suite = owner_and_suite
    viewer = _user(db_session)
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()

    assert _gate(db_session, viewer, trigger=False).state == "awaiting_trigger"
    with pytest.raises(GateForbiddenError):
        _gate(db_session, viewer)
    assert _runs(db_session) == 0


def test_a_bound_suite_the_caller_cannot_see_refuses_the_gate(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    # Skipping it would pass a pipeline that suite may say to stop.
    owner, first = owner_and_suite
    other = _user(db_session)
    _bound_suite(db_session, other, db_session.get(Connection, first.connection_id))

    with pytest.raises(GateForbiddenError):
        _gate(db_session, owner)
    assert _runs(db_session) == 0


def test_a_caller_who_sees_no_bound_suite_gets_not_found(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    with pytest.raises(GateBindingNotFoundError):
        _gate(db_session, _user(db_session))


def test_disabled_and_unrelated_bindings_are_not_gated(db_session: Any) -> None:
    owner = _user(db_session)
    _bound_suite(db_session, owner, _connection(db_session, owner), enabled=False)

    with pytest.raises(GateBindingNotFoundError):
        _gate(db_session, owner)
    with pytest.raises(GateBindingNotFoundError):
        _gate(db_session, owner, pipeline_or_dag_id="other_pipeline")


def test_an_unknown_fail_on_is_refused(
    db_session: Any, owner_and_suite: tuple[User, Suite]
) -> None:
    owner, _ = owner_and_suite
    with pytest.raises(DataQError, match="fail_on"):
        _gate(db_session, owner, fail_on="pass")
