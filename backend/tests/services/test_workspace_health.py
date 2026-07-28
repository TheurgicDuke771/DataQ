"""Workspace-wide poll-staleness check (#1052) — the signal that cannot lie.

The defining test here is the first one: **the worker writes nothing at all and the
alert still fires.** Every #905-class incident (#852, #854, the wedged broker
reconnect) had a worker that looked alive and wrote nothing, so these tests never
simulate a worker — they only arrange DB rows, which is exactly the information the
check is allowed to use.

Publisher delivery is faked at the seam (`get_health_publisher`), never mocked past
it: the delivered-first tests depend on the real control flow between publish and
the `workspace_health` flag write.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import select

from backend.app.alerting.base import AlertUndeliverableError, PollStalenessReport
from backend.app.core.config import get_settings
from backend.app.db.models import Connection, User, WorkspaceHealth
from backend.app.services import workspace_health_service as svc


def _user(db_session: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    return user


def _orch_connection(
    db_session: Any,
    *,
    conn_type: str = "airflow",
    last_polled_at: datetime | None = None,
    created_at: datetime | None = None,
    env: str = "dev",
) -> Connection:
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env=env,
        config={"base_url": "http://x", "project_name": "p", "factory_name": "f"},
        secret_ref="ref",
        created_by=_user(db_session).id,
        last_polled_at=last_polled_at,
    )
    db_session.add(conn)
    db_session.flush()
    if created_at is not None:
        conn.created_at = created_at
        db_session.flush()
    return conn


class _CapturingPublisher:
    def __init__(self, fail: bool = False, undeliverable: bool = False) -> None:
        self.reports: list[PollStalenessReport] = []
        self.fail = fail
        self.undeliverable = undeliverable

    def publish_poll_staleness(self, session: Any, report: PollStalenessReport) -> bool:
        if self.fail:
            raise RuntimeError("every channel failed")
        if self.undeliverable:
            raise AlertUndeliverableError("no alert channel is configured")
        self.reports.append(report)
        return True


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch) -> _CapturingPublisher:
    pub = _CapturingPublisher()
    monkeypatch.setattr(svc, "get_health_publisher", lambda: pub)
    return pub


def _flag(db_session: Any) -> WorkspaceHealth | None:
    row = db_session.execute(
        select(WorkspaceHealth).where(WorkspaceHealth.key == svc.POLL_STALENESS_KEY)
    ).scalar_one_or_none()
    return cast("WorkspaceHealth | None", row)


NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


class TestTheAlertFiresWithoutTheWorker:
    def test_worker_writes_nothing_at_all_and_the_alert_still_fires(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """The #1052 acceptance test verbatim: nothing here simulates a worker —
        two connections whose poll writes simply stopped are all it takes."""
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        _orch_connection(db_session, conn_type="adf", last_polled_at=NOW - timedelta(hours=3))

        outcome = svc.run_poll_staleness_check(db_session, now=NOW)

        assert outcome == "alerted"
        assert len(publisher.reports) == 1
        report = publisher.reports[0]
        assert report.is_failing
        assert report.connection_count == 2
        assert report.most_recent_polled_at == NOW - timedelta(hours=2)
        flag = _flag(db_session)
        assert flag is not None and flag.alerted_at is not None

    def test_never_polled_connections_go_stale_from_their_creation_moment(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """A poller that never ran at ALL (wrong image, task never registered) writes
        no `last_polled_at` anywhere — 'we have not looked yet' must not read as
        'nothing to report' (#828), so `created_at` is the fallback reference."""
        _orch_connection(db_session, last_polled_at=None, created_at=NOW - timedelta(hours=1))

        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"
        assert publisher.reports[0].most_recent_polled_at is None

    def test_fresh_polls_mean_nothing_to_say(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        _orch_connection(db_session, last_polled_at=NOW - timedelta(minutes=5))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "ok"
        assert publisher.reports == []
        flag = _flag(db_session)
        assert flag is None or flag.alerted_at is None

    def test_no_orchestration_connections_is_not_a_dead_loop(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """Nothing to poll ≠ polling is dead. A datasource connection (snowflake)
        must not be counted into the orchestration population either."""
        conn = Connection(
            name=f"snowflake-{uuid.uuid4().hex[:8]}",
            type="snowflake",
            env="dev",
            config={"account": "ACCT"},
            secret_ref="ref",
            created_by=_user(db_session).id,
        )
        db_session.add(conn)
        db_session.flush()
        conn.created_at = NOW - timedelta(days=30)
        db_session.flush()

        assert svc.run_poll_staleness_check(db_session, now=NOW) == "ok"
        assert publisher.reports == []

    def test_disabled_by_config(
        self,
        db_session: Any,
        publisher: _CapturingPublisher,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("POLL_STALENESS_ALERT_AFTER_S", "0")
        get_settings.cache_clear()
        _orch_connection(db_session, last_polled_at=NOW - timedelta(days=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "disabled"
        assert publisher.reports == []


class TestDeliveredFirst:
    """#843: the flag records what was DELIVERED, never what we intended to send."""

    def test_a_failed_publish_leaves_the_flag_unset_so_the_next_tick_retries(
        self, db_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing = _CapturingPublisher(fail=True)
        monkeypatch.setattr(svc, "get_health_publisher", lambda: failing)
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))

        with pytest.raises(RuntimeError):
            svc.run_poll_staleness_check(db_session, now=NOW)
        db_session.rollback()

        flag = _flag(db_session)
        assert flag is None or flag.alerted_at is None

    def test_no_configured_channel_leaves_the_flag_unset_and_retries(
        self, db_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fresh-install trap (review finding): every channel unconfigured must
        NOT stamp the flag — the incident stays outstanding, and the first tick
        after an operator wires a channel delivers the original edge."""
        undeliverable = _CapturingPublisher(undeliverable=True)
        monkeypatch.setattr(svc, "get_health_publisher", lambda: undeliverable)
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))

        assert svc.run_poll_staleness_check(db_session, now=NOW) == "undeliverable"
        flag = _flag(db_session)
        assert flag is None or flag.alerted_at is None

        # The undeliverable path's rollback discarded this test's UNCOMMITTED
        # fixture row (prod connections are long-committed); re-seed the same
        # still-dead state before the second tick.
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))

        # The operator wires up a channel; the loop is STILL dead — the edge
        # must now actually go out.
        configured = _CapturingPublisher()
        monkeypatch.setattr(svc, "get_health_publisher", lambda: configured)
        assert svc.run_poll_staleness_check(db_session, now=NOW + timedelta(minutes=10)) == (
            "alerted"
        )
        assert [r.state for r in configured.reports] == ["failing"]

    def test_undeliverable_recovery_keeps_the_flag_set(
        self, db_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Channels unconfigured AFTER a delivered failing edge: the operator was
        told about a failure and must not have the recovery silently dropped —
        the flag stays set until a recovery actually goes out."""
        delivered = _CapturingPublisher()
        monkeypatch.setattr(svc, "get_health_publisher", lambda: delivered)
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"

        conn = db_session.execute(select(Connection)).scalars().first()
        conn.last_polled_at = NOW + timedelta(minutes=1)
        db_session.flush()

        undeliverable = _CapturingPublisher(undeliverable=True)
        monkeypatch.setattr(svc, "get_health_publisher", lambda: undeliverable)
        outcome = svc.run_poll_staleness_check(db_session, now=NOW + timedelta(minutes=2))
        assert outcome == "undeliverable"
        flag = _flag(db_session)
        assert flag is not None and flag.alerted_at is not None

    def test_recovery_fires_only_after_a_delivered_failing_edge(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"

        # Poll writes resume (the connection got polled again).
        conn = db_session.execute(select(Connection)).scalars().first()
        conn.last_polled_at = NOW + timedelta(minutes=1)
        db_session.flush()

        outcome = svc.run_poll_staleness_check(db_session, now=NOW + timedelta(minutes=2))
        assert outcome == "recovered"
        assert [r.state for r in publisher.reports] == ["failing", "recovered"]
        flag = _flag(db_session)
        assert flag is not None and flag.alerted_at is None

    def test_no_recovery_edge_without_an_outstanding_alert(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """Fresh polls with no delivered FAILING edge → silence, not an all-clear.
        An unprompted 'recovered' message is the #837 rule violated from the other
        side: the operator's last delivered state must stay truthful."""
        _orch_connection(db_session, last_polled_at=NOW - timedelta(minutes=1))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "ok"
        assert publisher.reports == []

    def test_still_stale_with_outstanding_alert_does_not_resend(
        self, db_session: Any, publisher: _CapturingPublisher
    ) -> None:
        """The edge fires once per transition, never once per tick (#837's edge-not-
        state rule) — a dead loop checked every 10 minutes must not page every 10
        minutes."""
        _orch_connection(db_session, last_polled_at=NOW - timedelta(hours=2))
        assert svc.run_poll_staleness_check(db_session, now=NOW) == "alerted"
        assert svc.run_poll_staleness_check(db_session, now=NOW + timedelta(hours=1)) == "ok"
        assert len(publisher.reports) == 1
