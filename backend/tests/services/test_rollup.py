"""Shared rollup primitives (#889) — the one histogram, score, and latest-run query.

The score math itself is pinned in `test_dashboard_service.py` (its long-standing
home, including the ADR-0005 worked examples); these tests cover what the shared
module adds: the histogram query, the latest-run statement's semantics, and the
invariants a future consumer could quietly break.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

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
    evaluated_total,
    health_score,
    latest_runs_per_suite_stmt,
    pass_rate,
    status_histograms,
)

# ── vocabulary invariants ──


def test_severity_statuses_come_from_the_model_vocabulary() -> None:
    """Not re-derived from the penalty map's keys. The two were the same tuple by
    coincidence, so a weight added without a matching tier would have silently
    widened N — and N is the health score's denominator."""
    assert SEVERITY_STATUSES == RESULT_SEVERITY_TIERS


def test_operational_statuses_are_excluded_from_the_denominator() -> None:
    """#122 / ADR 0005: `skip` and `error` did not evaluate a severity, so they
    must never be counted as a pass NOR inflate N."""
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


def test_status_histograms_empty_input_does_no_query(db_session: Any) -> None:
    assert status_histograms(db_session, []) == {}


def test_a_run_with_no_results_is_absent_not_empty(db_session: Any) -> None:
    """Callers treat a missing entry as "nothing evaluated"; returning an empty
    dict instead would look identical to a run whose results were all filtered."""
    suite = _suite(db_session)
    run = _seed_run(db_session, suite=suite, created_at=datetime.now(UTC), statuses=[])
    assert run.id not in status_histograms(db_session, [run.id])


def test_the_histogram_feeds_the_score_directly(db_session: Any) -> None:
    """The point of sharing: the shape one query produces is the shape the score
    consumes, with no adapter in between."""
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


# Explicit, ordered ids for the tie-break test. Server-generated UUIDs would make
# it a COIN FLIP: with no tie-break Postgres returns whichever row its sort emits
# first, which is the max about half the time — so a future removal of `id DESC`
# would merge green on roughly every other run. Inserting the LOWER id first means
# heap order (what an untied sort falls back to) yields the wrong answer
# deterministically.
_LOW_RUN_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
_HIGH_RUN_ID = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")


def test_ties_on_created_at_resolve_deterministically(db_session: Any) -> None:
    """Both previous copies ordered only by `created_at DESC`, so two runs sharing
    a timestamp resolved nondeterministically — the same page could show different
    numbers on refresh. The `id DESC` tie-break makes it stable."""
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
    either choice in the shared query would silently change the other."""
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
