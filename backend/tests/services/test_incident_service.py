"""Incident lifecycle engine tests against a real Postgres (db_session).

The **state machine + dedup guarantee is the point** (ADR 0034 decision 4, #761):
open / acknowledge / resolve / auto-resolve / reopen, occurrence attach (no
duplicate active incident per (asset, check)), per-suite auto-resolve config, and
the upsert-race no-duplicate proof (deterministic ON CONFLICT fallback + a genuine
two-connection concurrent race).

Skips without TEST_DATABASE_URL (JSONB/UUID/partial-index need real Postgres)."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from backend.app.db.models import (
    Asset,
    Check,
    Connection,
    Incident,
    Result,
    Run,
    SuiteNotification,
    User,
)
from backend.app.services import incident_service, suite_service

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}


# ── seeding helpers ───────────────────────────────────────────────────────────


def _user(db: Any, email: str = "owner@example.com") -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email)
    db.add(u)
    db.flush()
    return u


def _connection(db: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=_SF_CONFIG,
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    return conn


def _suite(db: Any, owner: User, conn: Connection, *, table: str = "ORDERS") -> Any:
    return suite_service.create_suite(
        db,
        name=f"suite-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": table},
    )


def _check(db: Any, suite: Any, *, name: str = "orders_not_null") -> Check:
    check = Check(
        suite_id=suite.id,
        name=name,
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.flush()
    return check


def _run_with_result(
    db: Any,
    suite: Any,
    check: Check,
    *,
    status: str,
    metric: float | None = None,
    sample: dict[str, Any] | None = None,
    triggered_by: str = "manual",
    run_status: str = "succeeded",
) -> Run:
    run = Run(
        suite_id=suite.id,
        status=run_status,
        triggered_by=triggered_by,
        asset_id=suite.asset_id,
    )
    db.add(run)
    db.flush()
    db.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status=status,
            metric_value=metric,
            sample_failures=sample,
        )
    )
    db.commit()
    return run


def _active(db: Any, asset_id: uuid.UUID, check_id: uuid.UUID) -> list[Incident]:
    return list(
        db.scalars(
            select(Incident).where(
                Incident.asset_id == asset_id,
                Incident.check_id == check_id,
                Incident.status.in_(("open", "acknowledged")),
            )
        )
    )


@pytest.fixture
def world(db_session: Any) -> dict[str, Any]:
    owner = _user(db_session)
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    assert suite.asset_id is not None
    check = _check(db_session, suite)
    return {"owner": owner, "conn": conn, "suite": suite, "check": check}


# ── open / attach (dedup) ─────────────────────────────────────────────────────


def test_failing_run_opens_one_incident(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail", metric=0.4)
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    active = _active(db_session, world["suite"].asset_id, world["check"].id)
    assert len(active) == 1
    inc = active[0]
    assert inc.status == "open"
    assert inc.occurrence_count == 1
    assert inc.suite_id == world["suite"].id
    assert inc.prior_incident_id is None
    assert inc.evidence is not None  # card snapshotted at open


def test_repeat_failure_attaches_occurrence_not_duplicate(
    db_session: Any, world: dict[str, Any]
) -> None:
    for _ in range(3):
        run = _run_with_result(db_session, world["suite"], world["check"], status="critical")
        incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    active = _active(db_session, world["suite"].asset_id, world["check"].id)
    assert len(active) == 1  # never a second active incident for the pair
    assert active[0].occurrence_count == 3
    # Every occurrence refreshes last_seen_at (>= created_at).
    assert active[0].last_seen_at >= active[0].created_at


def test_warn_tier_also_opens_incident(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="warn")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    assert len(_active(db_session, world["suite"].asset_id, world["check"].id)) == 1


def test_operational_error_and_skip_do_not_open(db_session: Any, world: dict[str, Any]) -> None:
    for status in ("error", "skip"):
        run = _run_with_result(db_session, world["suite"], world["check"], status=status)
        incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    assert _active(db_session, world["suite"].asset_id, world["check"].id) == []


def test_run_without_asset_opens_nothing(db_session: Any, world: dict[str, Any]) -> None:
    """A run whose asset never resolved (asset_id NULL) can't anchor an incident."""
    run = Run(suite_id=world["suite"].id, status="succeeded", triggered_by="manual", asset_id=None)
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=world["check"].id, status="fail"))
    db_session.commit()
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    assert db_session.scalars(select(Incident)).all() == []


def test_operational_run_failure_no_results_opens_nothing(
    db_session: Any, world: dict[str, Any]
) -> None:
    """A run that failed to execute (no result rows) has no check-level anchor."""
    run = Run(
        suite_id=world["suite"].id,
        status="failed",
        triggered_by="manual",
        asset_id=world["suite"].asset_id,
    )
    db_session.add(run)
    db_session.commit()
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    assert db_session.scalars(select(Incident)).all() == []


# ── auto-resolve ──────────────────────────────────────────────────────────────


def test_passing_run_auto_resolves(db_session: Any, world: dict[str, Any]) -> None:
    fail_run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=fail_run.id)
    pass_run = _run_with_result(db_session, world["suite"], world["check"], status="pass")
    incident_service.sync_incidents_for_run(db_session, run_id=pass_run.id)

    assert _active(db_session, world["suite"].asset_id, world["check"].id) == []
    resolved = db_session.scalars(select(Incident)).all()
    assert len(resolved) == 1
    assert resolved[0].status == "resolved"
    assert resolved[0].resolved_by == "auto"
    assert resolved[0].resolved_by_user_id is None


def test_auto_resolve_disabled_per_suite(db_session: Any, world: dict[str, Any]) -> None:
    db_session.add(SuiteNotification(suite_id=world["suite"].id, auto_resolve_incidents=False))
    db_session.commit()
    fail_run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=fail_run.id)
    pass_run = _run_with_result(db_session, world["suite"], world["check"], status="pass")
    incident_service.sync_incidents_for_run(db_session, run_id=pass_run.id)
    # Config off → the incident stays open through the passing run.
    active = _active(db_session, world["suite"].asset_id, world["check"].id)
    assert len(active) == 1 and active[0].status == "open"


def test_auto_resolve_default_on_without_config(db_session: Any, world: dict[str, Any]) -> None:
    assert incident_service.auto_resolve_enabled(db_session, world["suite"].id) is True


# ── reopen chain ──────────────────────────────────────────────────────────────


def test_reopen_after_resolve_links_prior(db_session: Any, world: dict[str, Any]) -> None:
    fail1 = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=fail1.id)
    first = _active(db_session, world["suite"].asset_id, world["check"].id)[0]
    passr = _run_with_result(db_session, world["suite"], world["check"], status="pass")
    incident_service.sync_incidents_for_run(db_session, run_id=passr.id)
    fail2 = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=fail2.id)

    active = _active(db_session, world["suite"].asset_id, world["check"].id)
    assert len(active) == 1  # exactly one active again
    reopened = active[0]
    assert reopened.id != first.id  # a NEW incident, not the resolved one mutated
    assert reopened.prior_incident_id == first.id  # linked to the prior


# ── manual ack / resolve (manual wins) ────────────────────────────────────────


def test_acknowledge_then_resolve(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    inc = _active(db_session, world["suite"].asset_id, world["check"].id)[0]

    inc = incident_service.acknowledge_incident(
        db_session, inc, user_id=world["owner"].id, note="on it"
    )
    assert inc.status == "acknowledged"
    assert inc.acknowledged_by == world["owner"].id
    assert inc.acknowledge_note == "on it"

    inc = incident_service.resolve_incident(
        db_session, inc, user_id=world["owner"].id, note="fixed"
    )
    assert inc.status == "resolved"
    assert inc.resolved_by == "user"
    assert inc.resolved_by_user_id == world["owner"].id
    assert inc.resolution_note == "fixed"


def test_acknowledged_incident_still_dedups_new_failure(
    db_session: Any, world: dict[str, Any]
) -> None:
    """An acknowledged incident is still 'active' — a repeat failure attaches to it
    rather than opening a second (the partial index covers acknowledged too)."""
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    inc = _active(db_session, world["suite"].asset_id, world["check"].id)[0]
    incident_service.acknowledge_incident(db_session, inc, user_id=world["owner"].id)

    run2 = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run2.id)
    active = _active(db_session, world["suite"].asset_id, world["check"].id)
    assert len(active) == 1
    assert active[0].id == inc.id
    assert active[0].status == "acknowledged"  # attach doesn't revert the ack
    assert active[0].occurrence_count == 2


def test_double_resolve_conflicts(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    inc = _active(db_session, world["suite"].asset_id, world["check"].id)[0]
    incident_service.resolve_incident(db_session, inc, user_id=world["owner"].id)
    with pytest.raises(incident_service.IncidentNotActiveError):
        incident_service.resolve_incident(db_session, inc, user_id=world["owner"].id)


def test_acknowledge_resolved_conflicts(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    inc = _active(db_session, world["suite"].asset_id, world["check"].id)[0]
    incident_service.resolve_incident(db_session, inc, user_id=world["owner"].id)
    with pytest.raises(incident_service.IncidentNotActiveError):
        incident_service.acknowledge_incident(db_session, inc, user_id=world["owner"].id)


# ── read model ────────────────────────────────────────────────────────────────


def test_list_incidents_filters(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    owner_id = world["owner"].id
    assert len(incident_service.list_incidents(db_session, user_id=owner_id)) == 1
    assert len(incident_service.list_incidents(db_session, user_id=owner_id, state="open")) == 1
    assert len(incident_service.list_incidents(db_session, user_id=owner_id, state="resolved")) == 0
    assert (
        len(
            incident_service.list_incidents(
                db_session, user_id=owner_id, asset_id=world["suite"].asset_id
            )
        )
        == 1
    )
    # A different asset filter → empty.
    assert (
        len(incident_service.list_incidents(db_session, user_id=owner_id, asset_id=uuid.uuid4()))
        == 0
    )


def test_active_incidents_for_run_map(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    fresh = db_session.get(Run, run.id)
    mapping = incident_service.active_incidents_for_run(db_session, fresh)
    assert set(mapping) == {world["check"].id}


# ── upsert-race no-duplicate ──────────────────────────────────────────────────


def test_on_conflict_fallback_is_deterministic(db_session: Any, world: dict[str, Any]) -> None:
    """The loser's path proven deterministically: a second open on the SAME pair
    (the winner already committed) hits ON CONFLICT DO NOTHING → attaches. This is
    exactly what protects concurrent failing results from racing in a duplicate."""
    run1 = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    run2 = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    check = world["check"]
    asset = db_session.get(Asset, world["suite"].asset_id)

    _, action1 = incident_service.open_or_attach_incident(
        db_session,
        run=db_session.get(Run, run1.id),
        result=_result(db_session, run1),
        check=check,
        asset=asset,
    )
    db_session.commit()
    _, action2 = incident_service.open_or_attach_incident(
        db_session,
        run=db_session.get(Run, run2.id),
        result=_result(db_session, run2),
        check=check,
        asset=asset,
    )
    db_session.commit()
    assert action1 == "opened"
    assert action2 == "attached"  # conflict → fallback
    active = _active(db_session, world["suite"].asset_id, check.id)
    assert len(active) == 1 and active[0].occurrence_count == 2


def _result(db: Any, run: Run) -> Any:
    return db.scalars(select(Result).where(Result.run_id == run.id)).one()


def test_concurrent_failing_syncs_no_duplicate(_db_engine: Any) -> None:
    """A genuine two-connection race: two failing runs of the SAME (asset, check)
    sync concurrently. The partial unique index + ON CONFLICT guarantees exactly
    one active incident with the occurrences counted — no duplicate, no
    IntegrityError. Committed rows are cleaned up so other tests aren't polluted."""
    seed = SASession(bind=_db_engine)
    try:
        owner = _user(seed, email=f"race-{uuid.uuid4().hex[:6]}@ex.com")
        conn = _connection(seed, owner)
        suite = _suite(seed, owner, conn, table=f"T{uuid.uuid4().hex[:6].upper()}")
        check = _check(seed, suite)
        run1 = _run_with_result(seed, suite, check, status="fail", triggered_by="r1")
        run2 = _run_with_result(seed, suite, check, status="fail", triggered_by="r2")
        asset_id = suite.asset_id
        seed.commit()

        barrier = threading.Barrier(2)

        def worker(run_id: uuid.UUID) -> None:
            s = SASession(bind=_db_engine)
            try:
                barrier.wait(timeout=5)
                incident_service.sync_incidents_for_run(s, run_id=run_id)
            finally:
                s.close()

        threads = [threading.Thread(target=worker, args=(r.id,)) for r in (run1, run2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        check_sess = SASession(bind=_db_engine)
        try:
            active = list(
                check_sess.scalars(
                    select(Incident).where(
                        Incident.asset_id == asset_id,
                        Incident.status.in_(("open", "acknowledged")),
                    )
                )
            )
            assert len(active) == 1
            assert active[0].occurrence_count == 2
        finally:
            check_sess.close()
    finally:
        _cleanup(
            _db_engine,
            suite_id=suite.id,
            asset_id=suite.asset_id,
            conn_id=conn.id,
            user_id=owner.id,
        )


def _cleanup(
    engine: Any, *, suite_id: uuid.UUID, asset_id: uuid.UUID, conn_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Delete the race test's committed rows (suite cascades checks/runs/results/
    incidents), then the now-orphaned asset, connection and user."""
    from backend.app.db.models import Suite

    s = SASession(bind=engine)
    try:
        suite = s.get(Suite, suite_id)
        if suite is not None:
            s.delete(suite)  # cascades checks → runs → results → incidents
            s.flush()
        asset = s.get(Asset, asset_id)
        if asset is not None:
            s.delete(asset)
        conn = s.get(Connection, conn_id)
        if conn is not None:
            s.delete(conn)
        user = s.get(User, user_id)
        if user is not None:
            s.delete(user)
        s.commit()
    finally:
        s.close()
