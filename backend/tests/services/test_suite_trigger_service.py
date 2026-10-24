"""Suite-on-suite triggers (ADR 0051, #244): what fires, what never does, and the guards."""

from __future__ import annotations

import uuid
from itertools import pairwise
from typing import Any

import pytest
from sqlalchemy import func, select

from backend.app.db.models import (
    SUITE_TRIGGER_MAX_DEPTH,
    Check,
    Connection,
    Result,
    Run,
    Suite,
    TriggerBinding,
    User,
)
from backend.app.services import run_dispatch
from backend.app.services import suite_trigger_service as svc


def _user(db: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"t-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    db.flush()
    return user


def _connection(db: Any, owner: User, *, env: str = "dev") -> Connection:
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}", type="postgres", env=env, config={}, created_by=owner.id
    )
    db.add(conn)
    db.flush()
    return conn


def _suite(db: Any, owner: User, conn: Connection) -> Suite:
    suite = Suite(name=f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.flush()
    return suite


def _bind(
    db: Any, *, upstream: Suite, downstream: Suite, env: str = "dev", enabled: bool = True
) -> None:
    db.add(
        TriggerBinding(
            provider="suite",
            pipeline_or_dag_id=str(upstream.id),
            env=env,
            suite_id=downstream.id,
            enabled=enabled,
        )
    )
    db.flush()


def _run(
    db: Any, suite: Suite, *statuses: str, status: str = "succeeded", marker: str | None = None
) -> Run:
    run = Run(suite_id=suite.id, status=status, triggered_by=marker)
    db.add(run)
    db.flush()
    for s in statuses:
        check = Check(
            suite_id=suite.id,
            name=f"c-{uuid.uuid4().hex[:6]}",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "id"},
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=s))
    db.commit()
    return run


def _downstream_runs(db: Any, suite: Suite) -> list[Run]:
    return list(db.scalars(select(Run).where(Run.suite_id == suite.id)))


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    dispatched: list[uuid.UUID] = []

    def _fake(session: Any, run: Run, **_kw: str) -> bool:
        dispatched.append(run.id)
        return True

    monkeypatch.setattr(run_dispatch, "dispatch_or_fail", _fake)
    return dispatched


@pytest.fixture
def chain(db_session: Any) -> tuple[Suite, Suite]:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    upstream, downstream = _suite(db_session, owner, conn), _suite(db_session, owner, conn)
    _bind(db_session, upstream=upstream, downstream=downstream)
    db_session.commit()
    return upstream, downstream


def test_a_passed_run_queues_the_downstream_once(
    db_session: Any, chain: tuple[Suite, Suite], _no_broker: list[uuid.UUID]
) -> None:
    upstream, downstream = chain
    run = _run(db_session, upstream, "pass", "warn")  # warn passes
    created = svc.fire_for_run(db_session, run_id=run.id)
    assert [r.suite_id for r in created] == [downstream.id]
    assert created[0].status == "queued"
    assert created[0].triggered_by == f"suite:{upstream.id}:{run.id}"
    assert _no_broker == [created[0].id]
    # A second completion event for the same upstream run (worker retry) adds nothing.
    assert svc.fire_for_run(db_session, run_id=run.id) == []
    assert len(_downstream_runs(db_session, downstream)) == 1


@pytest.mark.parametrize(
    ("statuses", "run_status"),
    [
        (("pass", "fail"), "succeeded"),
        (("critical",), "succeeded"),
        (("pass",), "failed"),
        (("pass",), "cancelled"),
        (("pass",), "running"),
    ],
)
def test_a_breach_or_an_unfinished_run_fires_nothing(
    db_session: Any, chain: tuple[Suite, Suite], statuses: tuple[str, ...], run_status: str
) -> None:
    upstream, downstream = chain
    run = _run(db_session, upstream, *statuses, status=run_status)
    assert svc.fire_for_run(db_session, run_id=run.id) == []
    assert _downstream_runs(db_session, downstream) == []


def test_errored_and_skipped_checks_do_not_block(
    db_session: Any, chain: tuple[Suite, Suite]
) -> None:
    # The run verified what it could; an operational error is not "the data is bad".
    upstream, downstream = chain
    run = _run(db_session, upstream, "pass", "error", "skip")
    assert len(svc.fire_for_run(db_session, run_id=run.id)) == 1
    assert len(_downstream_runs(db_session, downstream)) == 1


def test_disabled_binding_and_env_mismatch_fire_nothing(db_session: Any) -> None:
    owner = _user(db_session)
    upstream = _suite(db_session, owner, _connection(db_session, owner, env="qa"))
    off = _suite(db_session, owner, _connection(db_session, owner, env="qa"))
    wrong_env = _suite(db_session, owner, _connection(db_session, owner, env="qa"))
    _bind(db_session, upstream=upstream, downstream=off, env="qa", enabled=False)
    _bind(db_session, upstream=upstream, downstream=wrong_env, env="dev")
    db_session.commit()
    run = _run(db_session, upstream, "pass")
    assert svc.fire_for_run(db_session, run_id=run.id) == []


def test_chain_depth_is_read_back_through_the_markers(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    suites = [_suite(db_session, owner, conn) for _ in range(3)]
    manual = _run(db_session, suites[0], "pass", marker=f"manual:{owner.id}")
    hop1 = _run(db_session, suites[1], "pass", marker=f"suite:{suites[0].id}:{manual.id}")
    hop2 = _run(db_session, suites[2], "pass", marker=f"suite:{suites[1].id}:{hop1.id}")
    assert svc.chain_depth(db_session, manual) == 0
    assert svc.chain_depth(db_session, hop1) == 1
    assert svc.chain_depth(db_session, hop2) == 2
    assert svc.upstream_run_id("adf:p:r") is None
    assert svc.upstream_run_id(f"suite:{suites[0].id}:not-a-uuid") is None


def test_a_chain_at_the_depth_cap_stops(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    suites = [_suite(db_session, owner, conn) for _ in range(SUITE_TRIGGER_MAX_DEPTH + 2)]
    for upstream, downstream in pairwise(suites):
        _bind(db_session, upstream=upstream, downstream=downstream)
    db_session.commit()
    # Runs 0..MAX already exist as a chain; run MAX is the one whose followers would be hop MAX+1.
    previous = _run(db_session, suites[0], "pass", marker=f"manual:{owner.id}")
    for i in range(1, SUITE_TRIGGER_MAX_DEPTH + 1):
        previous = _run(
            db_session, suites[i], "pass", marker=f"suite:{suites[i - 1].id}:{previous.id}"
        )
    assert svc.chain_depth(db_session, previous) == SUITE_TRIGGER_MAX_DEPTH
    assert svc.fire_for_run(db_session, run_id=previous.id) == []
    assert _downstream_runs(db_session, suites[-1]) == []
    # One hop below the cap still fires.
    below = db_session.get(Run, previous.id)
    assert below is not None
    hop_before = db_session.scalars(
        select(Run).where(Run.suite_id == suites[SUITE_TRIGGER_MAX_DEPTH - 1].id)
    ).one()
    assert svc.fire_for_run(db_session, run_id=hop_before.id) == []  # its follower already exists
    assert (
        int(db_session.scalar(select(func.count()).select_from(Run))) == SUITE_TRIGGER_MAX_DEPTH + 1
    )
