"""Coverage of the inventory and the false-positive rate (ADR 0047 §8), on real Postgres."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.db.models import Asset, Check, Connection, Incident, Run, Suite, User
from backend.app.services import coverage_service as cov

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(db_session: Any) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config={},
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _asset(db: Any, name: str) -> Asset:
    asset = Asset(namespace="postgres://h/db", name=name)
    db.add(asset)
    db.flush()
    return asset


def _suite(db: Any, conn: Connection, asset: Asset, *, origin: str) -> Suite:
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:6]}",
        connection_id=conn.id,
        asset_id=asset.id,
        origin=origin,
        created_by=conn.created_by if origin == "user" else None,
    )
    db.add(suite)
    db.flush()
    return suite


def _run(db: Any, suite: Suite, *, days_ago: float, status: str = "succeeded") -> None:
    db.add(
        Run(
            suite_id=suite.id,
            status=status,
            triggered_by="manual",
            created_at=NOW - timedelta(days=days_ago),
        )
    )
    db.flush()


def _resolved(
    db: Any,
    suite: Suite,
    *,
    resolution: str | None,
    by: str = "user",
    days_ago: float = 1,
) -> None:
    check = Check(suite_id=suite.id, name=uuid.uuid4().hex[:8], expectation_type="e", config={})
    db.add(check)
    db.flush()
    db.add(
        Incident(
            asset_id=suite.asset_id,
            check_id=check.id,
            suite_id=suite.id,
            status="resolved",
            resolved_by=by,
            resolved_at=NOW - timedelta(days=days_ago),
            resolution=resolution,
        )
    )
    db.flush()


def test_coverage_counts_assets_with_a_recent_completed_run_and_splits_them(
    db_session: Any, conn: Connection
) -> None:
    authored, both, auto_only, stale, failed_only, unwatched = (
        _asset(db_session, n) for n in ("authored", "both", "auto", "stale", "failed", "none")
    )
    _run(db_session, _suite(db_session, conn, authored, origin="user"), days_ago=1)
    _run(db_session, _suite(db_session, conn, both, origin="user"), days_ago=2)
    _run(db_session, _suite(db_session, conn, both, origin="auto"), days_ago=2)
    _run(db_session, _suite(db_session, conn, auto_only, origin="auto"), days_ago=6)
    # Outside the 7-day window, and a run that never completed: neither watches anything.
    _run(db_session, _suite(db_session, conn, stale, origin="user"), days_ago=8)
    _run(
        db_session,
        _suite(db_session, conn, failed_only, origin="user"),
        days_ago=1,
        status="failed",
    )
    assert unwatched.id is not None

    figures = cov.coverage_figures(db_session, now=NOW)

    assert figures.assets_total == 6
    assert figures.assets_watched == 3
    assert (figures.assets_watched_authored, figures.assets_watched_auto_only) == (2, 1)
    assert figures.coverage_pct == 50.0
    assert figures.coverage_window_days == 7


def test_false_positive_rate_is_over_stated_resolutions_of_automatic_suites_only(
    db_session: Any, conn: Connection
) -> None:
    asset = _asset(db_session, "t")
    auto = _suite(db_session, conn, asset, origin="auto")
    authored = _suite(db_session, conn, asset, origin="user")
    _resolved(db_session, auto, resolution="false_positive")
    _resolved(db_session, auto, resolution="fixed")
    _resolved(db_session, auto, resolution="expected_change")
    _resolved(db_session, auto, resolution="fixed")
    _resolved(db_session, auto, resolution=None)  # resolved, did not say
    # None of these belong in the rate:
    _resolved(db_session, auto, resolution=None, by="auto")  # auto-resolved
    _resolved(db_session, auto, resolution="false_positive", days_ago=45)  # outside the window
    _resolved(db_session, authored, resolution="false_positive")  # a person's own suite

    figures = cov.coverage_figures(db_session, now=NOW, false_positive_window_days=30)

    assert (figures.resolved, figures.stated, figures.unstated) == (5, 4, 1)
    assert figures.false_positive == 1
    # 1 of the 4 that said — the unstated one is not counted as "not a false positive".
    assert figures.false_positive_rate == 25.0


def test_nothing_stated_is_no_rate_not_a_zero_rate(db_session: Any, conn: Connection) -> None:
    asset = _asset(db_session, "t")
    auto = _suite(db_session, conn, asset, origin="auto")
    _resolved(db_session, auto, resolution=None)

    figures = cov.coverage_figures(db_session, now=NOW)

    assert (figures.resolved, figures.stated, figures.unstated) == (1, 0, 1)
    assert figures.false_positive_rate is None


def test_an_empty_workspace_has_no_coverage_figure(db_session: Any) -> None:
    figures = cov.coverage_figures(db_session, now=NOW)

    assert (figures.assets_total, figures.assets_watched) == (0, 0)
    assert figures.coverage_pct is None
    assert figures.false_positive_rate is None


def test_the_rate_moves_when_an_incident_is_resolved_through_the_real_resolve_path(
    db_session: Any, conn: Connection
) -> None:
    """The other tests set `resolution` on the model directly. This one goes through
    `incident_service.resolve_incident`, the only way the application writes it."""
    from backend.app.services import incident_service

    asset = _asset(db_session, "t")
    auto = _suite(db_session, conn, asset, origin="auto")
    check = Check(suite_id=auto.id, name="c", expectation_type="e", config={})
    db_session.add(check)
    db_session.flush()
    incident = Incident(asset_id=asset.id, check_id=check.id, suite_id=auto.id, status="open")
    db_session.add(incident)
    db_session.commit()
    assert cov.coverage_figures(db_session).false_positive_rate is None

    incident_service.resolve_incident(
        db_session, incident, user_id=conn.created_by, resolution="false_positive"
    )

    figures = cov.coverage_figures(db_session)
    assert (figures.stated, figures.false_positive, figures.false_positive_rate) == (1, 1, 100.0)
