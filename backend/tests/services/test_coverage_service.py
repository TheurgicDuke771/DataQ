"""Automatic coverage (ADR 0047): the reconciler adds, pauses and resumes; a person's edits win."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from backend.app.db.models import (
    Asset,
    AuditEvent,
    Check,
    Connection,
    MonitorBaseline,
    Schedule,
    Suite,
    User,
)
from backend.app.services import check_service
from backend.app.services import coverage_service as cov
from backend.app.services.asset_identity import resolve_asset_identity

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
_CONFIG = {"host": "wh.example.com", "database": "shop", "user": "reader", "auto_coverage": True}


@pytest.fixture
def conn(db_session: Any) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config=dict(_CONFIG),
        secret_ref="kv-pg",
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.commit()
    return connection


def _asset(db_session: Any, conn: Connection, table: str, *, seen: datetime = NOW) -> Asset:
    ident = resolve_asset_identity("postgres", _CONFIG, {"schema": "public", "table": table})
    asset = Asset(
        namespace=ident.namespace,
        name=ident.name,
        env="dev",
        connection_id=conn.id,
        first_seen=seen,
        last_seen=seen,
    )
    db_session.add(asset)
    db_session.commit()
    return asset


def _auto(db_session: Any, asset: Asset) -> Suite:
    suite: Suite | None = db_session.scalar(
        select(Suite).where(Suite.asset_id == asset.id, Suite.origin == "auto")
    )
    assert suite is not None
    return suite


def _checks(db_session: Any, suite: Suite) -> dict[str, Check]:
    return {c.name: c for c in db_session.scalars(select(Check).where(Check.suite_id == suite.id))}


def _schedule(db_session: Any, suite: Suite) -> Schedule:
    schedule: Schedule | None = db_session.scalar(
        select(Schedule).where(Schedule.suite_id == suite.id)
    )
    assert schedule is not None
    return schedule


def test_a_covered_table_gets_a_system_owned_suite_of_universal_baselines(
    db_session: Any, conn: Connection
) -> None:
    orders = _asset(db_session, conn, "orders")
    audit_before = db_session.scalar(select(func.count()).select_from(AuditEvent))

    report = cov.reconcile_connection(db_session, conn, now=NOW)

    assert report.suites_created == 1 and report.checks_created == 2
    suite = _auto(db_session, orders)
    assert suite.created_by is None and suite.target == {"schema": "public", "table": "orders"}
    checks = _checks(db_session, suite)
    assert set(checks) == {cov.SCHEMA_CHECK, cov.VOLUME_CHECK}
    assert {c.origin for c in checks.values()} == {"auto"}
    volume = checks[cov.VOLUME_CHECK]
    assert (volume.warn_threshold, volume.fail_threshold, volume.critical_threshold) == (
        Decimal(3),
        Decimal(4),
        Decimal(6),
    )
    assert _schedule(db_session, suite).enabled
    # Machine writes stay out of the audit log (ADR 0041 §2.1).
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == audit_before


def test_rerunning_adds_nothing_and_keeps_a_persons_edit(db_session: Any, conn: Connection) -> None:
    orders = _asset(db_session, conn, "orders")
    cov.reconcile_connection(db_session, conn, now=NOW)
    volume = _checks(db_session, _auto(db_session, orders))[cov.VOLUME_CHECK]
    volume.fail_threshold = Decimal(5)
    db_session.commit()

    again = cov.reconcile_connection(db_session, conn, now=NOW)

    assert (again.suites_created, again.checks_created) == (0, 0)
    assert _checks(db_session, _auto(db_session, orders))[cov.VOLUME_CHECK].fail_threshold == 5


def test_a_check_a_person_deleted_is_never_recreated(db_session: Any, conn: Connection) -> None:
    orders = _asset(db_session, conn, "orders")
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = _auto(db_session, orders)
    volume = _checks(db_session, suite)[cov.VOLUME_CHECK]
    check_service.delete_check(db_session, suite.id, volume.id)

    cov.reconcile_connection(db_session, conn, now=NOW)

    assert cov.VOLUME_CHECK not in _checks(db_session, suite)


def _capture_schema(db_session: Any, suite: Suite, columns: list[dict[str, str]]) -> None:
    schema_check = _checks(db_session, suite)[cov.SCHEMA_CHECK]
    db_session.add(
        MonitorBaseline(
            check_id=schema_check.id, kind="schema_drift", baseline={"columns": columns}
        )
    )
    db_session.commit()


def test_freshness_is_added_once_the_schema_is_known(db_session: Any, conn: Connection) -> None:
    orders = _asset(db_session, conn, "orders")
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = _auto(db_session, orders)
    _capture_schema(
        db_session,
        suite,
        [
            {"name": "order_id", "type": "INTEGER"},
            {"name": "created_at", "type": "TIMESTAMP WITH TIME ZONE"},
            {"name": "loaded_at", "type": "TIMESTAMP"},
        ],
    )

    assert cov.reconcile_connection(db_session, conn, now=NOW).checks_created == 1

    freshness = _checks(db_session, suite)[cov.FRESHNESS_CHECK]
    assert freshness.config["target_metric"] == "freshness_age_hours"
    assert freshness.config["column"] == "loaded_at"  # the load time beats the creation time


def test_no_timestamp_column_is_recorded_as_a_gap_not_guessed(
    db_session: Any, conn: Connection
) -> None:
    orders = _asset(db_session, conn, "orders")
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = _auto(db_session, orders)
    _capture_schema(db_session, suite, [{"name": "order_id", "type": "INTEGER"}])

    cov.reconcile_connection(db_session, conn, now=NOW)

    assert cov.FRESHNESS_CHECK not in _checks(db_session, suite)
    assert "freshness_gap" in (_auto(db_session, orders).auto_state or {})


def test_excluded_aged_out_and_switched_off_tables_are_paused_then_resumed(
    db_session: Any, conn: Connection
) -> None:
    orders = _asset(db_session, conn, "orders")
    stale = _asset(db_session, conn, "legacy")
    cov.reconcile_connection(db_session, conn, now=NOW)

    orders.auto_coverage_excluded = True
    stale.last_seen = NOW - timedelta(days=cov.INVENTORY_FRESH_DAYS + 1)
    db_session.commit()
    assert cov.reconcile_connection(db_session, conn, now=NOW).suites_paused == 2
    assert not _schedule(db_session, _auto(db_session, orders)).enabled

    orders.auto_coverage_excluded = False
    db_session.commit()
    assert cov.reconcile_connection(db_session, conn, now=NOW).suites_resumed == 1

    conn.config = {**_CONFIG, "auto_coverage": False}
    db_session.commit()
    totals = cov.reconcile_all(db_session, now=NOW)
    assert totals["paused"] == 1
    # Paused, never deleted: the history stays.
    assert (
        db_session.scalar(select(func.count()).select_from(Suite).where(Suite.origin == "auto"))
        == 2
    )


def test_a_name_that_does_not_round_trip_is_skipped(db_session: Any, conn: Connection) -> None:
    ident = resolve_asset_identity("postgres", _CONFIG, {"schema": "public", "table": "t"})
    db_session.add(
        Asset(
            namespace=ident.namespace,
            name="not-a-dotted-name",
            env="dev",
            connection_id=conn.id,
            first_seen=NOW,
            last_seen=NOW,
        )
    )
    db_session.commit()
    report = cov.reconcile_connection(db_session, conn, now=NOW)
    assert report.skipped_assets == ["not-a-dotted-name"] and report.suites_created == 0


def test_the_cap_limits_coverage_and_says_so(
    db_session: Any, conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auto_coverage_max_assets", 1)
    _asset(db_session, conn, "a_table")
    _asset(db_session, conn, "b_table")
    report = cov.reconcile_connection(db_session, conn, now=NOW)
    assert report.truncated and report.suites_created == 1


def test_coverage_is_off_by_default(db_session: Any, conn: Connection) -> None:
    conn.config = {k: v for k, v in _CONFIG.items() if k != "auto_coverage"}
    db_session.commit()
    _asset(db_session, conn, "orders")
    assert cov.reconcile_all(db_session, now=NOW)["suites_created"] == 0


def test_daily_slots_spread_across_the_day() -> None:
    slots = {cov._cron_for(uuid.uuid4()) for _ in range(50)}
    assert len(slots) > 40
    assert all(len(s.split()) == 5 and s.endswith("* * *") for s in slots)


def test_a_renamed_check_is_not_added_again(db_session: Any, conn: Connection) -> None:
    orders = _asset(db_session, conn, "orders")
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = _auto(db_session, orders)
    _checks(db_session, suite)[cov.VOLUME_CHECK].name = "Orders volume"
    db_session.commit()

    assert cov.reconcile_connection(db_session, conn, now=NOW).checks_created == 0
    assert set(_checks(db_session, suite)) == {"Orders volume", cov.SCHEMA_CHECK}


def test_a_schedule_a_person_paused_stays_paused(db_session: Any, conn: Connection) -> None:
    orders = _asset(db_session, conn, "orders")
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = _auto(db_session, orders)
    _schedule(db_session, suite).enabled = False
    db_session.commit()

    report = cov.reconcile_connection(db_session, conn, now=NOW)

    assert report.suites_resumed == 0
    assert not _schedule(db_session, suite).enabled


def test_a_column_name_that_is_not_an_identifier_is_not_chosen(
    db_session: Any, conn: Connection
) -> None:
    orders = _asset(db_session, conn, "orders")
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = _auto(db_session, orders)
    _capture_schema(db_session, suite, [{"name": "Load Time", "type": "TIMESTAMP"}])

    report = cov.reconcile_connection(db_session, conn, now=NOW)

    assert report.skipped_assets == []
    assert cov.FRESHNESS_CHECK not in _checks(db_session, suite)
    assert "freshness_gap" in (_auto(db_session, orders).auto_state or {})


def test_one_failing_table_does_not_stop_the_rest(
    db_session: Any, conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _asset(db_session, conn, "a_broken")
    healthy = _asset(db_session, conn, "b_healthy")
    real = cov._ensure_checks

    def flaky(session: Any, suite: Suite) -> int:
        if suite.name.endswith("a_broken"):
            raise RuntimeError("boom")
        return real(session, suite)

    monkeypatch.setattr(cov, "_ensure_checks", flaky)
    report = cov.reconcile_connection(db_session, conn, now=NOW)

    assert report.skipped_assets == [f"{healthy.name[:-len('b_healthy')]}a_broken"]
    assert set(_checks(db_session, _auto(db_session, healthy))) == {
        cov.SCHEMA_CHECK,
        cov.VOLUME_CHECK,
    }
