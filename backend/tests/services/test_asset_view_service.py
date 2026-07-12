"""Service-level tests for `asset_view_service` — the branches the HTTP authz
matrix (tests/api/test_assets.py) doesn't reach: metadata partial-update
semantics, an asset with no composing suites, and the empty-input short-circuits.

Skips without TEST_DATABASE_URL (JSONB/UUID need real Postgres)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.app.db.models import Asset, Check, Connection, Result, Run, User
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


def test_list_empty_when_no_visible_suites(db_session: Any) -> None:
    user = _user(db_session)
    assert svc.list_visible_assets(db_session, user_id=user.id) == []


def test_summarize_asset_with_no_suites(db_session: Any) -> None:
    """An orphan asset (e.g. a dbt-lineage-only node) summarizes to an empty,
    no-run health — never raises, so the admin PATCH response works on it."""
    asset = Asset(namespace="snowflake://x", name="ORPHAN")
    db_session.add(asset)
    db_session.commit()
    admin = _user(db_session)
    summary = svc.summarize_asset(db_session, asset, user_id=admin.id, include_all=True)
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
    return db.get(Asset, suite.asset_id)


def test_error_result_feeds_connection_health_not_suite_health(db_session: Any) -> None:
    """A run that SUCCEEDED but whose check threw: connection health is degraded
    (operational error) while suite health stays severity-free — the exact case
    `has_failed_run` alone misses, since the run itself never failed."""
    owner = _user(db_session)
    asset = _suite_with_run(db_session, owner, run_status="succeeded", result_statuses=["error"])
    s = svc.summarize_asset(db_session, asset, user_id=owner.id, include_all=True)

    assert s.has_operational_error is True  # connection axis: could not evaluate
    assert s.has_failed_run is False  # the run itself succeeded
    assert s.worst_severity is None  # suite axis: no DQ verdict at all
    assert s.checks_total == 0  # `error` is not an evaluated check


def test_skip_result_is_degraded_not_an_error(db_session: Any) -> None:
    """`skip` = a precondition wasn't met (the batch hasn't landed). The run
    executed, so it is NOT an operational error — only a degraded connection."""
    owner = _user(db_session)
    asset = _suite_with_run(db_session, owner, run_status="succeeded", result_statuses=["skip"])
    s = svc.summarize_asset(db_session, asset, user_id=owner.id, include_all=True)

    assert s.has_skip is True
    assert s.has_operational_error is False
    assert s.worst_severity is None
    assert s.checks_total == 0


def test_failed_run_is_an_operational_error(db_session: Any) -> None:
    """A run whose execution failed wrote no results at all — connection axis."""
    owner = _user(db_session)
    asset = _suite_with_run(db_session, owner, run_status="failed", result_statuses=[])
    s = svc.summarize_asset(db_session, asset, user_id=owner.id, include_all=True)

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
    s = svc.summarize_asset(db_session, asset, user_id=owner.id, include_all=True)

    assert s.worst_severity == "critical"  # suite axis: data is bad
    assert s.checks_total == 2 and s.checks_passed == 1
    assert s.has_operational_error is False  # connection axis: nothing wrong here
    assert s.has_skip is False


def test_operational_result_flags_empty_input() -> None:
    assert run_service.operational_result_flags(None, []) == {}  # type: ignore[arg-type]
