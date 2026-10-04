"""Shared rollup primitives (#889) — the one histogram, score, and latest-run query."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event

from backend.app.db.models import (
    RESULT_OPERATIONAL_STATUSES,
    RESULT_SEVERITY_TIERS,
    Check,
    Connection,
    Result,
    Run,
    Suite,
    User,
)
from backend.app.services.rollup import (
    SEVERITY_STATUSES,
    connection_scores,
    evaluated_total,
    health_score,
    latest_runs_per_suite_stmt,
    pass_rate,
    status_histograms,
)
from backend.app.services.scoring_settings_service import DEFAULT_WEIGHTS

_WEIGHTS = DEFAULT_WEIGHTS

# ── vocabulary invariants ──


def test_severity_statuses_come_from_the_model_vocabulary() -> None:
    """Not re-derived from the penalty map's keys. The two were the same tuple by
    coincidence, so a weight added without a matching tier would have silently
    widened N — and N is the health score's denominator.
    """
    assert SEVERITY_STATUSES == RESULT_SEVERITY_TIERS


def test_operational_statuses_are_excluded_from_the_denominator() -> None:
    """#122 / ADR 0005: `skip` and `error` did not evaluate a severity, so they
    must never be counted as a pass NOR inflate N.
    """
    counts = {"pass": 2, **dict.fromkeys(RESULT_OPERATIONAL_STATUSES, 5)}
    assert evaluated_total(counts) == 2
    assert pass_rate(counts) == 100.0
    assert health_score(counts) == 100.0


def test_a_run_of_only_operational_results_has_no_score() -> None:
    counts = dict.fromkeys(RESULT_OPERATIONAL_STATUSES, 3)
    assert evaluated_total(counts) == 0
    assert health_score(counts) is None
    assert pass_rate(counts) is None


# ── status_histograms ──


def _seed_run(
    db: Any,
    *,
    suite: Suite,
    created_at: datetime,
    statuses: list[str],
    run_id: uuid.UUID | None = None,
) -> Run:
    run = Run(suite_id=suite.id, status="succeeded", created_at=created_at)
    if run_id is not None:
        run.id = run_id
    db.add(run)
    db.flush()
    for i, status in enumerate(statuses):
        check = Check(
            suite_id=suite.id,
            name=f"c{i}-{uuid.uuid4().hex[:6]}",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "x"},
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=status))
    db.flush()
    return run


def _suite(db: Any, name: str = "s") -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={},
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name=f"{name}-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.flush()
    return suite


def test_status_histograms_groups_by_run_and_status(db_session: Any) -> None:
    suite = _suite(db_session)
    now = datetime.now(UTC)
    run = _seed_run(
        db_session, suite=suite, created_at=now, statuses=["pass", "pass", "fail", "skip"]
    )
    assert status_histograms(db_session, [run.id]) == {run.id: {"pass": 2, "fail": 1, "skip": 1}}


def test_a_partly_skipped_run_rolls_up_as_evaluated_only(db_session: Any) -> None:
    """A per-check `skip` (#593 anomaly cold start) among normal siblings must not corrupt the
    run's outcome: `checks_total` counts only evaluated checks, so the run reads 2/2 rather than
    a misleading 2/3, and the skip surfaces separately as an operational flag.
    """
    from backend.app.services.run_service import check_outcome_counts, operational_result_flags

    suite = _suite(db_session)
    run = _seed_run(
        db_session, suite=suite, created_at=datetime.now(UTC), statuses=["pass", "pass", "skip"]
    )
    assert check_outcome_counts(db_session, [run.id]) == {run.id: (2, 2, None)}
    assert operational_result_flags(db_session, [run.id]) == {run.id: (False, True)}


def test_a_skipped_check_never_ranks_as_a_severity(db_session: Any) -> None:
    """The worst-severity fold must ignore `skip`, or a cold-start anomaly would
    look like the run's worst outcome.
    """
    from backend.app.services.run_service import check_outcome_counts

    suite = _suite(db_session)
    run = _seed_run(
        db_session, suite=suite, created_at=datetime.now(UTC), statuses=["skip", "warn"]
    )
    assert check_outcome_counts(db_session, [run.id]) == {run.id: (1, 0, "warn")}


def test_status_histograms_empty_input_does_no_query(db_session: Any) -> None:
    assert status_histograms(db_session, []) == {}


def test_a_run_with_no_results_is_absent_not_empty(db_session: Any) -> None:
    """Callers treat a missing entry as "nothing evaluated"; returning an empty
    dict instead would look identical to a run whose results were all filtered.
    """
    suite = _suite(db_session)
    run = _seed_run(db_session, suite=suite, created_at=datetime.now(UTC), statuses=[])
    assert run.id not in status_histograms(db_session, [run.id])


def test_the_histogram_feeds_the_score_directly(db_session: Any) -> None:
    """The point of sharing: the shape one query produces is the shape the score
    consumes, with no adapter in between.
    """
    suite = _suite(db_session)
    run = _seed_run(
        db_session,
        suite=suite,
        created_at=datetime.now(UTC),
        statuses=["pass", "pass", "fail", "fail"],
    )
    counts = status_histograms(db_session, [run.id])[run.id]
    assert health_score(counts) == 75.0  # the ADR-0005 worked example


# ── latest_runs_per_suite_stmt ──


def test_latest_run_is_the_newest_per_suite(db_session: Any) -> None:
    suite_a, suite_b = _suite(db_session, "a"), _suite(db_session, "b")
    now = datetime.now(UTC)
    _seed_run(db_session, suite=suite_a, created_at=now - timedelta(hours=2), statuses=["pass"])
    newest_a = _seed_run(db_session, suite=suite_a, created_at=now, statuses=["fail"])
    only_b = _seed_run(db_session, suite=suite_b, created_at=now - timedelta(days=1), statuses=[])

    runs = list(db_session.scalars(latest_runs_per_suite_stmt([suite_a.id, suite_b.id])))
    assert {r.suite_id: r.id for r in runs} == {suite_a.id: newest_a.id, suite_b.id: only_b.id}


# Explicit, ordered ids for the tie-break test.
_LOW_RUN_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
_HIGH_RUN_ID = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")


def test_ties_on_created_at_resolve_deterministically(db_session: Any) -> None:
    """Both previous copies ordered only by `created_at DESC`, so two runs sharing
    a timestamp resolved nondeterministically — the same page could show different
    numbers on refresh. The `id DESC` tie-break makes it stable.
    """
    suite = _suite(db_session)
    same = datetime.now(UTC)
    _seed_run(db_session, suite=suite, created_at=same, statuses=["pass"], run_id=_LOW_RUN_ID)
    _seed_run(db_session, suite=suite, created_at=same, statuses=["fail"], run_id=_HIGH_RUN_ID)

    for _ in range(3):  # stable across repeated evaluation, not merely once
        runs = list(db_session.scalars(latest_runs_per_suite_stmt([suite.id])))
        assert [r.id for r in runs] == [_HIGH_RUN_ID]


@pytest.mark.parametrize("status", ["failed", "cancelled", "queued", "running"])
def test_the_latest_run_counts_whatever_its_status(db_session: Any, status: str) -> None:
    """No status filter here on purpose: the dashboard drops a resultless run with
    an inner join, the asset view keeps it to report an operational error. Encoding
    either choice in the shared query would silently change the other.
    """
    suite = _suite(db_session)
    now = datetime.now(UTC)
    _seed_run(db_session, suite=suite, created_at=now - timedelta(hours=1), statuses=["pass"])
    latest = Run(suite_id=suite.id, status=status, created_at=now)
    db_session.add(latest)
    db_session.flush()

    runs = list(db_session.scalars(latest_runs_per_suite_stmt([suite.id])))
    assert [r.id for r in runs] == [latest.id]


def test_empty_scope_returns_nothing(db_session: Any) -> None:
    assert list(db_session.scalars(latest_runs_per_suite_stmt([]))) == []


# ── connection_scores (#1557) ──


def _owner(db: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db.add(user)
    db.flush()
    return user


def _conn(db: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    return conn


def _suite_run(
    db: Any, conn: Connection, owner: User, statuses: list[str], *, run_status: str = "succeeded"
) -> Suite:
    suite = Suite(name=f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.flush()
    _run_on(db, suite, statuses, run_status=run_status)
    return suite


def _run_on(
    db: Any, suite: Suite, statuses: list[str], *, run_status: str = "succeeded", age_days: int = 0
) -> None:
    run = Run(
        suite_id=suite.id,
        status=run_status,
        triggered_by="manual",
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db.add(run)
    db.flush()
    for status in statuses:
        check = Check(
            suite_id=suite.id, name=f"k-{uuid.uuid4().hex[:6]}", expectation_type="e", config={}
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=status))
    db.flush()


def test_connection_score_pools_every_suite_on_the_connection(db_session: Any) -> None:
    owner, other = _owner(db_session), _owner(db_session)
    conn, quiet = _conn(db_session, owner), _conn(db_session, owner)
    _suite_run(db_session, conn, owner, ["pass", "pass", "pass"])
    # Another user's suite on the same connection counts too — the score is workspace-true.
    _suite_run(db_session, conn, other, ["fail", "skip", "error"])

    scores = connection_scores(db_session, [conn.id, quiet.id], weights=_WEIGHTS)

    # 1 fail of 4 evaluated (skip/error are outside the denominator) → 87.5.
    assert scores == {conn.id: 87.5}
    # Nothing evaluated on `quiet`: absent, never 0 or 100.
    assert quiet.id not in scores
    # Only skip/error is also "nothing evaluated".
    _suite_run(db_session, quiet, owner, ["skip", "error"])
    assert quiet.id not in connection_scores(db_session, [quiet.id], weights=_WEIGHTS)


def test_connection_score_uses_only_each_suites_latest_complete_run(db_session: Any) -> None:
    owner = _owner(db_session)
    conn = _conn(db_session, owner)
    suite = _suite_run(db_session, conn, owner, ["pass"])
    _run_on(db_session, suite, ["critical"], age_days=5)  # superseded
    stalled = _suite_run(db_session, conn, owner, ["critical"], run_status="running")

    assert connection_scores(db_session, [conn.id], weights=_WEIGHTS) == {conn.id: 100.0}
    # A suite whose latest run failed outright contributes nothing, even though an older
    # run of it completed.
    _run_on(db_session, stalled, ["critical"], age_days=5)
    assert connection_scores(db_session, [conn.id], weights=_WEIGHTS) == {conn.id: 100.0}


def test_connection_scores_cost_one_query_for_any_number_of_connections(db_session: Any) -> None:
    owner = _owner(db_session)
    conns = [_conn(db_session, owner) for _ in range(6)]
    for conn in conns:
        _suite_run(db_session, conn, owner, ["pass", "warn"])
    statements: list[str] = []
    bind = db_session.get_bind()

    def _count(*args: Any) -> None:
        statements.append(args[2])

    event.listen(bind, "before_cursor_execute", _count)
    try:
        scores = connection_scores(db_session, [c.id for c in conns], weights=_WEIGHTS)
    finally:
        event.remove(bind, "before_cursor_execute", _count)

    assert len(scores) == 6
    assert len(statements) == 1


def test_connection_scores_of_no_connections_runs_no_query(db_session: Any) -> None:
    assert connection_scores(db_session, [], weights=_WEIGHTS) == {}
