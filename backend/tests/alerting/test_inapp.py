"""The in-app feed publisher (#1687): recipients, entry text, idempotency, gating, isolation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select, text
from structlog.testing import capture_logs

from backend.app.alerting import registry
from backend.app.alerting.base import CheckReport, IncidentCard, RunReport
from backend.app.alerting.composite import CompositePublisher
from backend.app.alerting.inapp import InAppPublisher
from backend.app.core import usage_telemetry
from backend.app.db.models import (
    Connection,
    Notification,
    Run,
    Share,
    Suite,
    SuiteNotification,
    User,
)
from backend.app.services import notification_feed_service as svc


def _user(db: Any, *, role: str = "member", email: str | None = None) -> User:
    user = User(
        aad_object_id=uuid.uuid4().hex,
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def _suite(db: Any, owner: User | None) -> Suite:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id if owner else None,
    )
    db.add(conn)
    db.flush()
    suite = Suite(
        name="orders", connection_id=conn.id, created_by=owner.id if owner else None, target={}
    )
    db.add(suite)
    db.commit()
    return suite


def _check(name: str, status: str) -> CheckReport:
    return CheckReport(
        check_name=name,
        expectation_type="expect_column_values_to_not_be_null",
        status=status,
        metric_value=0.4,
        observed_value={"unexpected_list": ["SECRET-VALUE-1"]},
        expected_value=None,
        sample_summary={"rows": [{"email": "SECRET-VALUE-2"}]},
    )


def _run(db: Any, suite: Suite, *, status: str = "succeeded") -> uuid.UUID:
    run = Run(suite_id=suite.id, status=status)
    db.add(run)
    db.commit()
    return run.id


def _report(
    suite: Suite,
    *,
    db: Any,
    checks: list[CheckReport] | None = None,
    run_status: str = "succeeded",
    run_id: uuid.UUID | None = None,
    incidents: list[IncidentCard] | None = None,
) -> RunReport:
    rows = checks if checks is not None else [_check("id not null", "fail"), _check("ts", "pass")]
    counts: dict[str, int] = {}
    for c in rows:
        counts[c.status] = counts.get(c.status, 0) + 1
    worst = None
    for tier in ("warn", "fail", "critical"):
        if counts.get(tier):
            worst = tier
    return RunReport(
        run_id=run_id or _run(db, suite, status=run_status),
        suite_id=suite.id,
        suite_name=suite.name,
        run_status=run_status,
        datasource_type="snowflake",
        target_label="RETAIL.ORDERS",
        worst_severity=worst,
        counts=counts,
        checks=rows,
        finished_at=None,
        env="dev",
        incidents=incidents or [],
    )


def _feed(db: Any, user: User) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.user_id == user.id)))


# ── recipients ────────────────────────────────────────────────────────────────


def test_recipients_are_owner_grantees_and_admins(
    db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    owner = _user(db_session)
    viewer = _user(db_session, role="viewer")
    editor = _user(db_session)
    stored_admin = _user(db_session, role="admin")
    allowlisted = _user(db_session, email="boot@example.com")
    outsider = _user(db_session)
    make_workspace_admin("Boot@Example.com")
    suite = _suite(db_session, owner)
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.add(Share(suite_id=suite.id, user_id=editor.id, permission="edit"))
    db_session.commit()

    got = svc.recipients_for_suite(db_session, suite)

    assert got == {owner.id, viewer.id, editor.id, stored_admin.id, allowlisted.id}
    assert outsider.id not in got


def test_suite_with_no_creator_still_reaches_admins(db_session: Any) -> None:
    admin = _user(db_session, role="admin")
    suite = _suite(db_session, None)
    assert svc.recipients_for_suite(db_session, suite) == {admin.id}


# ── entry text ────────────────────────────────────────────────────────────────


def test_entry_names_breaching_checks_and_never_an_observed_value(db_session: Any) -> None:
    suite = _suite(db_session, _user(db_session))
    report = _report(suite, db=db_session)
    entry = svc.compose_entry(report)
    assert entry.severity == "fail"
    assert entry.title == "orders: 1 of 2 checks breached (worst: fail)"
    assert entry.body == "RETAIL.ORDERS (dev): id not null (fail)"
    assert entry.deep_link == f"/results/{report.run_id}"
    for leaked in ("SECRET-VALUE-1", "SECRET-VALUE-2", "unexpected_list"):
        assert leaked not in entry.title + entry.body


def test_entry_truncates_a_long_breach_list(db_session: Any) -> None:
    suite = _suite(db_session, _user(db_session))
    checks = [_check(f"check {i}", "warn") for i in range(7)]
    entry = svc.compose_entry(_report(suite, db=db_session, checks=checks))
    assert entry.severity == "warn"
    assert entry.body.endswith("check 4 (warn) and 2 more")
    assert "check 5" not in entry.body


def test_entry_for_an_operational_failure_and_a_clean_run(db_session: Any) -> None:
    suite = _suite(db_session, _user(db_session))
    failed = svc.compose_entry(_report(suite, db=db_session, checks=[], run_status="failed"))
    assert failed.severity == "error"
    assert failed.title == "orders: run failed to execute"
    clean = svc.compose_entry(
        _report(suite, db=db_session, checks=[_check("a", "pass"), _check("b", "pass")])
    )
    assert clean.severity == "pass"
    assert clean.title == "orders: all 2 checks passed"


def test_entry_carries_the_first_incident(db_session: Any) -> None:
    suite = _suite(db_session, _user(db_session))
    card = IncidentCard(
        incident_id=uuid.uuid4(),
        check_id=uuid.uuid4(),
        check_name="id not null",
        status="fail",
        occurrence_count=1,
        is_new=True,
        evidence=None,
    )
    assert (
        svc.compose_entry(_report(suite, db=db_session, incidents=[card])).incident_id
        == card.incident_id
    )


# ── writing the feed ──────────────────────────────────────────────────────────


def test_record_writes_one_row_per_recipient_and_is_idempotent(db_session: Any) -> None:
    owner = _user(db_session)
    viewer = _user(db_session, role="viewer")
    suite = _suite(db_session, owner)
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()
    report = _report(suite, db=db_session)

    assert svc.record_run_alert(db_session, report) == 2
    # A worker retry re-publishes the same run: nothing is added.
    assert svc.record_run_alert(db_session, report) == 0
    assert len(_feed(db_session, owner)) == 1
    assert len(_feed(db_session, viewer)) == 1
    row = _feed(db_session, owner)[0]
    assert (row.run_id, row.suite_id, row.read_at) == (report.run_id, suite.id, None)


def test_record_drops_an_incident_that_no_longer_exists(db_session: Any) -> None:
    suite = _suite(db_session, _user(db_session))
    card = IncidentCard(
        incident_id=uuid.uuid4(),
        check_id=uuid.uuid4(),
        check_name="c",
        status="fail",
        occurrence_count=1,
        is_new=True,
        evidence=None,
    )
    assert svc.record_run_alert(db_session, _report(suite, db=db_session, incidents=[card])) == 1
    assert db_session.scalars(select(Notification)).one().incident_id is None


def test_record_is_a_noop_without_a_suite_or_recipients(db_session: Any) -> None:
    suite = _suite(db_session, None)  # no creator, no shares, no admins
    assert svc.record_run_alert(db_session, _report(suite, db=db_session)) == 0
    gone = _report(suite, db=db_session)
    gone = RunReport(**{**gone.__dict__, "suite_id": uuid.uuid4()})
    assert svc.record_run_alert(db_session, gone) == 0


# ── the publisher ─────────────────────────────────────────────────────────────


def test_publisher_honours_the_suite_policy(db_session: Any) -> None:
    owner = _user(db_session)
    suite = _suite(db_session, owner)
    db_session.add(SuiteNotification(suite_id=suite.id, enabled=True, alert_on="fail"))
    db_session.commit()
    warn_only = _report(suite, db=db_session, checks=[_check("c", "warn")])

    InAppPublisher().publish(db_session, warn_only)
    assert _feed(db_session, owner) == []

    InAppPublisher().publish(db_session, _report(suite, db=db_session))
    assert len(_feed(db_session, owner)) == 1


def test_publisher_respects_a_disabled_config_and_the_default_warn_policy(db_session: Any) -> None:
    owner = _user(db_session)
    suite = _suite(db_session, owner)
    # Default policy: warn and worse alert; a clean run does not.
    InAppPublisher().publish(
        db_session, _report(suite, db=db_session, checks=[_check("c", "pass")])
    )
    assert _feed(db_session, owner) == []
    InAppPublisher().publish(
        db_session, _report(suite, db=db_session, checks=[_check("c", "warn")])
    )
    assert len(_feed(db_session, owner)) == 1

    db_session.add(SuiteNotification(suite_id=suite.id, enabled=False, alert_on="always"))
    db_session.commit()
    InAppPublisher().publish(db_session, _report(suite, db=db_session))
    assert len(_feed(db_session, owner)) == 1


def test_publisher_emits_one_telemetry_event_per_run(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session)
    viewer = _user(db_session, role="viewer")
    suite = _suite(db_session, owner)
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        usage_telemetry, "emit", lambda event, **kw: emitted.append({"event": event, **kw})
    )
    report = _report(suite, db=db_session)
    InAppPublisher().publish(db_session, report)
    assert [e["channel"] for e in emitted] == ["in_app"]
    # The retry writes nothing, so it is not a delivery either.
    InAppPublisher().publish(db_session, report)
    assert len(emitted) == 1


def test_publisher_returns_false_for_health_edges(db_session: Any) -> None:
    pub = InAppPublisher()
    assert pub.publish_health(db_session, None) is False  # type: ignore[arg-type]
    assert pub.publish_workspace_signal(db_session, None) is False  # type: ignore[arg-type]


class _Spy:
    def __init__(self) -> None:
        self.reports: list[RunReport] = []

    def publish(self, session: Any, report: RunReport) -> None:
        # A later channel must still be able to USE the session.
        session.execute(text("SELECT 1")).scalar_one()
        self.reports.append(report)

    def publish_health(self, session: Any, report: Any) -> bool:
        return False

    def publish_workspace_signal(self, session: Any, report: Any) -> bool:
        return False


def test_a_failed_feed_write_does_not_block_the_next_channel(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session)
    suite = _suite(db_session, owner)

    def boom(session: Any, report: RunReport) -> int:
        # Poison the transaction the way a real constraint failure would, then raise.
        session.execute(text("INSERT INTO notifications (severity) VALUES ('nope')"))
        return 1  # pragma: no cover — the INSERT raises first

    monkeypatch.setattr(svc, "record_run_alert", boom)
    spy = _Spy()
    composite = CompositePublisher([InAppPublisher(), spy])  # type: ignore[list-item]

    with capture_logs() as logs:
        composite.publish(db_session, _report(suite, db=db_session))

    assert len(spy.reports) == 1
    assert _feed(db_session, owner) == []
    events = [entry["event"] for entry in logs]
    assert "inapp_feed_write_failed" in events
    assert "channel_publish_failed" in events


def test_registry_composite_includes_the_in_app_publisher() -> None:
    publisher = registry.get_result_publisher()
    assert isinstance(publisher, CompositePublisher)
    assert any(isinstance(p, InAppPublisher) for p in publisher._publishers)
