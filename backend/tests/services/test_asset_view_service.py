"""Service-level tests for `asset_view_service` — the branches the HTTP authz
matrix (tests/api/test_assets.py) doesn't reach: metadata partial-update
semantics, an asset with no composing suites, and the empty-input short-circuits.

Skips without TEST_DATABASE_URL (JSONB/UUID need real Postgres)."""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from backend.app.db.models import DQ_DIMENSIONS, Asset, Check, Connection, Result, Run, User
from backend.app.services import asset_view_service as svc
from backend.app.services import run_service, suite_service


def _user(db: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db.add(u)
    db.flush()
    return u


def _conn(db: Any, owner: User) -> Connection:
    c = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(c)
    db.commit()
    return c


def test_list_empty_when_no_assets_exist(db_session: Any) -> None:
    assert svc.list_visible_assets(db_session) == []


def test_summarize_asset_with_no_suites(db_session: Any) -> None:
    """An orphan asset (e.g. a dbt-lineage-only node) summarizes to an empty,
    no-run health — never raises, so the admin PATCH response works on it."""
    asset = Asset(namespace="snowflake://x", name="ORPHAN")
    db_session.add(asset)
    db_session.commit()
    summary = svc.summarize_asset(db_session, asset)
    assert summary.suite_count == 0
    assert summary.worst_severity is None
    assert summary.last_run_at is None
    assert summary.checks_total == 0


def test_update_metadata_partial_leaves_untouched(db_session: Any) -> None:
    owner = _user(db_session)
    conn = _conn(db_session, owner)
    suite = suite_service.create_suite(
        db_session,
        name="S",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )
    asset_id = suite.asset_id
    assert asset_id is not None

    # Set description only — owner stays NULL (set_owner=False).
    svc.update_asset_metadata(db_session, asset_id, description="v1", set_description=True)
    asset = db_session.get(Asset, asset_id)
    assert asset.description == "v1"
    assert asset.owner_user_id is None

    # Set owner only — description untouched (still v1).
    svc.update_asset_metadata(db_session, asset_id, owner_user_id=owner.id, set_owner=True)
    db_session.refresh(asset)
    assert asset.owner_user_id == owner.id
    assert asset.description == "v1"

    # Explicit clear of description to None (set_description=True, value None).
    svc.update_asset_metadata(db_session, asset_id, description=None, set_description=True)
    db_session.refresh(asset)
    assert asset.description is None


def test_update_metadata_unknown_raises(db_session: Any) -> None:
    with pytest.raises(svc.AssetNotFoundError):
        svc.update_asset_metadata(db_session, uuid.uuid4(), description="x", set_description=True)


def test_get_unknown_asset_raises(db_session: Any) -> None:
    user = _user(db_session)
    with pytest.raises(svc.AssetNotFoundError):
        svc.get_visible_asset(db_session, uuid.uuid4(), user_id=user.id)


# ── connection health vs suite health (#803) ─────────────────────────────────
#
# The two axes must not bleed into each other: operational `error`/`skip` results
# (#122) feed *connection* health (could DataQ reach the datasource?) and are
# invisible to *suite* health (is the data good?), which is severity-only.


def _suite_with_run(db: Any, owner: User, *, run_status: str, result_statuses: list[str]) -> Asset:
    """A suite on a fresh asset with one run carrying `result_statuses` results."""
    conn = _conn(db, owner)
    suite = suite_service.create_suite(
        db,
        name=f"S-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": f"T{uuid.uuid4().hex[:6]}"},
    )
    run = Run(suite_id=suite.id, status=run_status, triggered_by="manual")
    db.add(run)
    db.flush()
    for status in result_statuses:
        check = Check(
            suite_id=suite.id,
            name=f"c-{uuid.uuid4().hex[:6]}",
            expectation_type="expect_column_to_exist",
            config={"column": "X"},
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=status))
    db.commit()
    assert suite.asset_id is not None
    return cast(Asset, db.get(Asset, suite.asset_id))


def test_error_result_feeds_connection_health_not_suite_health(db_session: Any) -> None:
    """A run that SUCCEEDED but whose check threw: connection health is degraded
    (operational error) while suite health stays severity-free — the exact case
    `has_failed_run` alone misses, since the run itself never failed."""
    owner = _user(db_session)
    asset = _suite_with_run(db_session, owner, run_status="succeeded", result_statuses=["error"])
    s = svc.summarize_asset(db_session, asset)

    assert s.has_operational_error is True  # connection axis: could not evaluate
    assert s.has_failed_run is False  # the run itself succeeded
    assert s.worst_severity is None  # suite axis: no DQ verdict at all
    assert s.checks_total == 0  # `error` is not an evaluated check


def test_skip_result_is_degraded_not_an_error(db_session: Any) -> None:
    """`skip` = a precondition wasn't met (the batch hasn't landed). The run
    executed, so it is NOT an operational error — only a degraded connection."""
    owner = _user(db_session)
    asset = _suite_with_run(db_session, owner, run_status="succeeded", result_statuses=["skip"])
    s = svc.summarize_asset(db_session, asset)

    assert s.has_skip is True
    assert s.has_operational_error is False
    assert s.worst_severity is None
    assert s.checks_total == 0


def test_failed_run_is_an_operational_error(db_session: Any) -> None:
    """A run whose execution failed wrote no results at all — connection axis."""
    owner = _user(db_session)
    asset = _suite_with_run(db_session, owner, run_status="failed", result_statuses=[])
    s = svc.summarize_asset(db_session, asset)

    assert s.has_failed_run is True
    assert s.has_operational_error is True
    assert s.worst_severity is None


def test_failing_data_does_not_touch_connection_health(db_session: Any) -> None:
    """The mirror case: the datasource was perfectly reachable, the DATA is bad.
    Suite health goes red; connection health stays clean."""
    owner = _user(db_session)
    asset = _suite_with_run(
        db_session, owner, run_status="succeeded", result_statuses=["pass", "critical"]
    )
    s = svc.summarize_asset(db_session, asset)

    assert s.worst_severity == "critical"  # suite axis: data is bad
    assert s.checks_total == 2 and s.checks_passed == 1
    assert s.has_operational_error is False  # connection axis: nothing wrong here
    assert s.has_skip is False


def test_cancelled_run_is_flagged_so_it_never_rolls_up_green(db_session: Any) -> None:
    """A cancelled run proves nothing: killed before a check ran, we may never have
    reached the datasource. It is neither a failure nor an active run, so without
    its own flag it would look identical to a clean success and read green on both
    axes. (The UI keys `connectionHealth`/`suiteHealth` off this.)"""
    owner = _user(db_session)
    asset = _suite_with_run(db_session, owner, run_status="cancelled", result_statuses=[])
    s = svc.summarize_asset(db_session, asset)

    assert s.has_cancelled_run is True
    # Explicitly NOT any of the other execution states — this is the whole point:
    # nothing else in the summary distinguishes it from a healthy run.
    assert s.has_failed_run is False
    assert s.has_active_run is False
    assert s.has_operational_error is False
    assert s.checks_total == 0


def test_operational_result_flags_empty_input(db_session: Any) -> None:
    assert run_service.operational_result_flags(db_session, []) == {}


# ── DQ scorecard (#889, ADR 0038) ────────────────────────────────────────────


def _suite_with_dimensioned_results(
    db: Any, owner: User, *, results: list[tuple[str | None, str]]
) -> Asset:
    """A suite whose latest run carries `(dimension, result_status)` pairs."""
    conn = _conn(db, owner)
    suite = suite_service.create_suite(
        db,
        name=f"S-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": f"T{uuid.uuid4().hex[:6]}"},
    )
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="manual")
    db.add(run)
    db.flush()
    for dimension, status in results:
        check = Check(
            suite_id=suite.id,
            name=f"c-{uuid.uuid4().hex[:6]}",
            expectation_type="expect_column_to_exist",
            config={"column": "X"},
            dimension=dimension,
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=status))
    db.commit()
    assert suite.asset_id is not None
    return cast(Asset, db.get(Asset, suite.asset_id))


def _scorecard_for(db: Any, asset: Asset, user_id: uuid.UUID) -> Any:
    detail = svc.get_visible_asset(db, asset.id, user_id=user_id, include_all=True)
    assert detail.scorecard is not None
    return detail.scorecard


def test_scorecard_scores_each_dimension_independently(db_session: Any) -> None:
    owner = _user(db_session)
    asset = _suite_with_dimensioned_results(
        db_session,
        owner,
        results=[
            ("completeness", "pass"),
            ("completeness", "pass"),
            ("uniqueness", "fail"),
            ("uniqueness", "pass"),
        ],
    )
    card = _scorecard_for(db_session, asset, owner.id)
    by_dim = {d.dimension: d for d in card.covered}

    assert by_dim["completeness"].score == 100.0
    assert (by_dim["completeness"].checks_total, by_dim["completeness"].checks_passing) == (2, 2)
    # ADR 0005: one fail of two → penalty 1.0 over N=2, W_MAX=2 → 75.0
    assert by_dim["uniqueness"].score == 75.0
    assert (by_dim["uniqueness"].checks_total, by_dim["uniqueness"].checks_passing) == (2, 1)


def test_uncovered_lists_every_dimension_with_no_checks(db_session: Any) -> None:
    """The actionable half. "This asset has no Timeliness checks" is what a lead
    acts on; a pass-rate never says that."""
    owner = _user(db_session)
    asset = _suite_with_dimensioned_results(db_session, owner, results=[("completeness", "pass")])
    card = _scorecard_for(db_session, asset, owner.id)

    assert [d.dimension for d in card.covered] == ["completeness"]
    assert "timeliness" in card.uncovered
    assert "completeness" not in card.uncovered
    assert len(card.uncovered) == len(DQ_DIMENSIONS) - 1


def test_an_asset_with_no_checks_is_all_uncovered_not_a_perfect_score(db_session: Any) -> None:
    """No coverage ≠ 100%. Rendering a green tick over an asset nobody checks is
    the single most dangerous thing this feature could do."""
    owner = _user(db_session)
    asset = _suite_with_dimensioned_results(db_session, owner, results=[])
    card = _scorecard_for(db_session, asset, owner.id)

    assert card.covered == []
    assert sorted(card.uncovered) == sorted(DQ_DIMENSIONS)


def test_unclassified_checks_are_counted_but_never_bucketed(db_session: Any) -> None:
    """ADR 0038: a NULL dimension is a real state (custom SQL is unclassifiable).
    Filing those under some dimension would corrupt that bucket's score AND make
    `uncovered` a lie — so they are reported separately."""
    owner = _user(db_session)
    asset = _suite_with_dimensioned_results(
        db_session,
        owner,
        results=[(None, "fail"), (None, "pass"), ("validity", "pass")],
    )
    card = _scorecard_for(db_session, asset, owner.id)

    assert card.unclassified_checks == 2
    assert [d.dimension for d in card.covered] == ["validity"]
    assert card.covered[0].checks_total == 1  # the two NULL checks did not leak in
    assert card.covered[0].score == 100.0


def test_skip_and_error_are_excluded_from_the_denominator(db_session: Any) -> None:
    """#122 / ADR 0005: they did not evaluate a severity, so they must not count
    as passes NOR inflate the total."""
    owner = _user(db_session)
    asset = _suite_with_dimensioned_results(
        db_session,
        owner,
        results=[("validity", "pass"), ("validity", "skip"), ("validity", "error")],
    )
    card = _scorecard_for(db_session, asset, owner.id)
    row = card.covered[0]

    # Three checks EXIST, so coverage is 3 — but only one evaluated, and the score
    # is over that one. The two numbers are deliberately different.
    assert (row.checks_total, row.checks_passing, row.checks_evaluated) == (3, 1, 1)
    assert row.score == 100.0


def test_a_dimension_whose_checks_all_skipped_is_covered_with_no_score(
    db_session: Any,
) -> None:
    """Distinct from uncovered: checks EXIST, they just didn't evaluate. Merging
    the two states would tell a user to write checks they already have."""
    owner = _user(db_session)
    asset = _suite_with_dimensioned_results(
        db_session, owner, results=[("timeliness", "skip"), ("timeliness", "error")]
    )
    card = _scorecard_for(db_session, asset, owner.id)

    assert [d.dimension for d in card.covered] == ["timeliness"]
    assert card.covered[0].score is None  # no signal — not 0, not 100
    assert card.covered[0].checks_total == 2  # the checks exist...
    assert card.covered[0].checks_evaluated == 0  # ...but measured nothing
    assert "timeliness" not in card.uncovered


def test_a_check_that_has_never_run_still_counts_as_covered(db_session: Any) -> None:
    """THE bug this feature could most easily have shipped.

    Coverage must come from checks, not results. Derived from results, a
    `timeliness` check authored today on a nightly suite reads as *missing* until
    tomorrow's run — and the panel would tell the user to write a check they
    already wrote. It would also regress every time a run hard-failed and rolled
    its results back.
    """
    owner = _user(db_session)
    conn = _conn(db_session, owner)
    suite = suite_service.create_suite(
        db_session,
        name=f"S-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": f"T{uuid.uuid4().hex[:6]}"},
    )
    db_session.add(
        Check(
            suite_id=suite.id,
            name="never run",
            expectation_type="expect_column_to_exist",
            config={"column": "X"},
            dimension="timeliness",
        )
    )
    db_session.commit()
    assert suite.asset_id is not None
    asset = cast(Asset, db_session.get(Asset, suite.asset_id))

    card = _scorecard_for(db_session, asset, owner.id)
    assert [d.dimension for d in card.covered] == ["timeliness"]
    assert card.covered[0].checks_total == 1
    assert card.covered[0].checks_evaluated == 0
    assert card.covered[0].score is None  # no run yet — no signal, not a failure
    assert "timeliness" not in card.uncovered  # NOT "write a timeliness check"


def test_an_unclassified_check_that_only_errored_is_still_counted(db_session: Any) -> None:
    """The asymmetry review caught: a DIMENSIONED all-skip dimension stays visible
    with "no signal", but an unclassified one used to vanish entirely — so an asset
    whose only checks were five erroring custom-SQL checks reported "no checks at
    all". Counting checks rather than severity-bearing results fixes it."""
    owner = _user(db_session)
    asset = _suite_with_dimensioned_results(
        db_session, owner, results=[(None, "error"), (None, "skip")]
    )
    card = _scorecard_for(db_session, asset, owner.id)
    assert card.unclassified_checks == 2
