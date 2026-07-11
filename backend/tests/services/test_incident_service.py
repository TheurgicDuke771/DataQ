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
        # Capture plain ids BEFORE commit and close the seed session: touching an
        # expired ORM attribute after commit would open a NEW transaction on this
        # session that nothing ever closes — an idle-in-transaction backend whose
        # locks deadlock the engine fixture's drop_all teardown.
        run_ids = (run1.id, run2.id)
        ids = {
            "suite_id": suite.id,
            "asset_id": suite.asset_id,
            "conn_id": conn.id,
            "user_id": owner.id,
        }
        asset_id = ids["asset_id"]
        seed.commit()
        seed.close()

        barrier = threading.Barrier(2)

        def worker(run_id: uuid.UUID) -> None:
            s = SASession(bind=_db_engine)
            try:
                barrier.wait(timeout=5)
                incident_service.sync_incidents_for_run(s, run_id=run_id)
            finally:
                s.close()

        threads = [threading.Thread(target=worker, args=(rid,)) for rid in run_ids]
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
        seed.close()  # idempotent; ensures no idle-in-transaction leak on failure
        _cleanup(_db_engine, **ids)


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


# ── fix batch (PR #775 review): lock/recheck, retry, fail-soft, cascade ───────


def test_ack_with_stale_object_after_resolve_conflicts_and_never_reopens(
    db_session: Any, world: dict[str, Any]
) -> None:
    """The lost-update race, deterministically: a caller holding a STALE incident
    (read while open) acks after a resolve committed. The FOR-UPDATE re-read must
    surface the resolved state → 409, and the row must stay resolved (a terminal
    row is never reopened — which could also double-match the active partial
    unique index once a successor incident exists)."""
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    stale = _active(db_session, world["suite"].asset_id, world["check"].id)[0]
    assert stale.status == "open"  # the stale read

    # A passing run auto-resolves the incident out from under the stale holder.
    pass_run = _run_with_result(db_session, world["suite"], world["check"], status="pass")
    incident_service.sync_incidents_for_run(db_session, run_id=pass_run.id)

    with pytest.raises(incident_service.IncidentNotActiveError):
        incident_service.acknowledge_incident(db_session, stale, user_id=world["owner"].id)
    refreshed = db_session.get(Incident, stale.id)
    assert refreshed.status == "resolved"  # never reopened
    assert refreshed.resolved_by == "auto"  # the winner's outcome intact


def test_concurrent_manual_resolves_exactly_one_wins(_db_engine: Any) -> None:
    """Two sessions resolve the SAME incident concurrently: the FOR-UPDATE lock
    serializes them — exactly one wins, the loser gets a clean 409 (not a silent
    actor/note overwrite), and the row ends resolved-by-user exactly once."""
    seed = SASession(bind=_db_engine)
    try:
        owner = _user(seed, email=f"race2-{uuid.uuid4().hex[:6]}@ex.com")
        conn = _connection(seed, owner)
        suite = _suite(seed, owner, conn, table=f"R{uuid.uuid4().hex[:6].upper()}")
        check = _check(seed, suite)
        run = _run_with_result(seed, suite, check, status="fail", triggered_by="race2")
        incident_service.sync_incidents_for_run(seed, run_id=run.id)
        incident_id = seed.scalars(select(Incident.id).where(Incident.suite_id == suite.id)).one()
        owner_id = owner.id
        # Same idle-in-transaction discipline as the sync race test: capture plain
        # ids pre-commit, then close the seed session before the threads run.
        ids = {
            "suite_id": suite.id,
            "asset_id": suite.asset_id,
            "conn_id": conn.id,
            "user_id": owner.id,
        }
        seed.commit()
        seed.close()

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker(tag: str) -> None:
            s = SASession(bind=_db_engine)
            try:
                incident = s.get(Incident, incident_id)
                assert incident is not None
                barrier.wait(timeout=5)
                try:
                    incident_service.resolve_incident(s, incident, user_id=owner_id, note=tag)
                    with lock:
                        outcomes.append(f"{tag}:won")
                except incident_service.IncidentNotActiveError:
                    with lock:
                        outcomes.append(f"{tag}:409")
            finally:
                s.close()

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert sorted(o.split(":")[1] for o in outcomes) == ["409", "won"]
        verify = SASession(bind=_db_engine)
        try:
            final = verify.get(Incident, incident_id)
            assert final is not None
            assert final.status == "resolved"
            assert final.resolved_by == "user"
            # The winner's note survived (the loser 409'd before writing).
            winner_tag = next(o.split(":")[0] for o in outcomes if o.endswith("won"))
            assert final.resolution_note == winner_tag
        finally:
            verify.close()
    finally:
        seed.close()  # idempotent; ensures no idle-in-transaction leak on failure
        _cleanup(_db_engine, **ids)


def test_open_retries_when_active_vanishes_mid_attach(
    db_session: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ON-CONFLICT fallback gap: the insert conflicts but the active incident
    resolves before the attach lookup. The bounded retry must converge (here: the
    lookup 'misses' once, the retry re-attempts and attaches) instead of raising
    and rolling back the whole run's sync."""
    run1 = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run1.id)

    real = incident_service._active_incident
    calls = {"n": 0}

    def flaky(session: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # simulate: resolved in the insert→attach gap
        return real(session, **kwargs)

    monkeypatch.setattr(incident_service, "_active_incident", flaky)
    run2 = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run2.id)

    active = _active(db_session, world["suite"].asset_id, world["check"].id)
    assert len(active) == 1  # converged — attached on the retry, no duplicate
    assert active[0].occurrence_count == 2
    assert calls["n"] >= 2  # the retry actually looped


def test_sync_engine_failure_swallowed_and_alerts_still_dispatch(
    db_session: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-soft proof (mirrors alerting's test_publisher_exception_is_swallowed):
    an injected engine crash must not raise, the run stays persisted, and the
    alert dispatch that follows in the worker still publishes."""
    from backend.app.alerting import dispatch as alert_dispatch

    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("incident engine exploded")

    monkeypatch.setattr(incident_service, "_sync_incidents_for_run", boom)
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)  # must not raise

    persisted = db_session.get(Run, run.id)
    assert persisted is not None and persisted.status == "succeeded"
    # The worker calls alert dispatch right after the (failed) sync — still fires.
    assert alert_dispatch.publish_run_outcome(db_session, run_id=run.id) is True


def test_suite_delete_cascades_incidents(db_session: Any, world: dict[str, Any]) -> None:
    """#540 lesson: a suite that produced incidents must delete cleanly — the
    check/suite CASCADEs take the incident rows with them, no FK 500."""
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    assert len(_active(db_session, world["suite"].asset_id, world["check"].id)) == 1

    suite_service.delete_suite(db_session, world["suite"].id)
    assert db_session.scalars(select(Incident)).all() == []


def test_check_delete_cascades_incident(db_session: Any, world: dict[str, Any]) -> None:
    run = _run_with_result(db_session, world["suite"], world["check"], status="fail")
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    db_session.delete(db_session.get(Check, world["check"].id))
    db_session.commit()
    assert db_session.scalars(select(Incident)).all() == []


def test_cancelled_run_opens_nothing(db_session: Any, world: dict[str, Any]) -> None:
    """(11a) A cancelled run is excluded by the terminal-status guard — even if a
    result row somehow survived the cancel rollback, the sync must not anchor an
    incident to a run the user aborted."""
    run = _run_with_result(
        db_session, world["suite"], world["check"], status="fail", run_status="cancelled"
    )
    incident_service.sync_incidents_for_run(db_session, run_id=run.id)
    assert db_session.scalars(select(Incident)).all() == []
