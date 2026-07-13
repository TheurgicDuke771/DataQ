"""Tests for the connection poll-health alert (#837) — the *push* half of #828.

The behaviours worth defending are the ones that made the original outage possible,
so they are tested as failure modes, not happy paths:

- **it fires on the crossing, not on every failing poll** — a connection whose
  credential expired keeps failing every 10 minutes forever, and a `>=` here would send
  144 alerts a day until the channel was muted, putting us right back in the dark;
- **it signals recovery, but only if it ever alerted** — a single blip stays silent;
- **the alert carries the CLASSIFIED reason, never the raw exception** — the real #828
  exception carried the SAS query string, and an alert is the one place a credential
  would leave DataQ's trust boundary;
- **a broken channel can't take down the polling sweep** it is reporting on.

DB-backed where the dispatch path needs a real `Connection` row; the render + crossing
tests are pure. Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.alerting import dispatch, registry
from backend.app.alerting.base import (
    HEALTH_FAILING,
    HEALTH_RECOVERED,
    ConnectionHealthReport,
)
from backend.app.alerting.builder import build_connection_health_report
from backend.app.alerting.card import render_teams_health_message
from backend.app.alerting.email import render_health_html_body, render_health_text_body
from backend.app.alerting.slack import render_slack_health_message
from backend.app.core.config import get_settings
from backend.app.db.models import Connection, User
from backend.app.worker import tasks

# The exact shape of the credential that leaked in #828: an ADLS SAS whose query string
# rides in the exception message. If any renderer ever interpolates a raw exception, this
# string is what shows up in the Teams card.
_SAS = "sig=abc%2Fdef%3D&se=2027-01-01&sp=rl"


class _SpyHealthPublisher:
    def __init__(self, *, boom: bool = False) -> None:
        self.reports: list[ConnectionHealthReport] = []
        self._boom = boom

    def publish_health(self, session: Any, report: ConnectionHealthReport) -> None:
        if self._boom:
            raise RuntimeError("channel down")
        self.reports.append(report)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _SpyHealthPublisher:
    publisher = _SpyHealthPublisher()
    monkeypatch.setattr(registry, "get_health_publisher", lambda: publisher)
    return publisher


def _report(
    *, state: str = HEALTH_FAILING, failures: int = 3, reason: str | None = "auth_failed"
) -> ConnectionHealthReport:
    return ConnectionHealthReport(
        connection_id=uuid.uuid4(),
        connection_name="dbt-prod",
        connection_type="dbt",
        state=state,
        consecutive_failures=failures,
        reason=reason,
        last_polled_at=datetime(2026, 7, 13, 4, 0, tzinfo=UTC),
        connection_url="https://dataq.example/connections",
    )


def _connection(db: Any, **kwargs: Any) -> Connection:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="dbt",
        env="prod",
        config={"artifact_uri": "abfss://x"},
        secret_ref="kv",
        created_by=owner.id,
        **kwargs,
    )
    db.add(conn)
    db.commit()
    return conn


# ── the crossing: fire once, not once per failing poll ───────────────────────────


@pytest.mark.parametrize("streak", [1, 2])
def test_no_alert_below_threshold(db_session: Any, spy: _SpyHealthPublisher, streak: int) -> None:
    """A transient blip (a 502, a restarting orchestrator) must not page anyone."""
    conn = _connection(db_session)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=streak, recovered=False)
    assert spy.reports == []


def test_alerts_exactly_on_the_threshold(db_session: Any, spy: _SpyHealthPublisher) -> None:
    conn = _connection(db_session, consecutive_poll_failures=3, last_poll_error="auth_failed")
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=3, recovered=False)
    assert [r.state for r in spy.reports] == [HEALTH_FAILING]
    assert spy.reports[0].consecutive_failures == 3


@pytest.mark.parametrize("streak", [4, 5, 144, 1008])
def test_no_alert_storm_from_a_persistently_dead_connection(
    db_session: Any, spy: _SpyHealthPublisher, streak: int
) -> None:
    """The #828 outage ran for six days = ~864 consecutive failed polls. Every one of
    them past the crossing must be silent, or the channel gets muted and we are blind
    again — the exact failure this feature exists to prevent."""
    conn = _connection(db_session)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=streak, recovered=False)
    assert spy.reports == []


def test_threshold_zero_disables_the_push(
    db_session: Any, spy: _SpyHealthPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opting out of the push must not opt you out of the truth: #828's in-app health
    badge and lineage warning are unconditional; only the notification is gated."""
    monkeypatch.setattr(get_settings(), "orchestration_poll_failure_alert_threshold", 0)
    conn = _connection(db_session)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=3, recovered=False)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=9, recovered=True)
    assert spy.reports == []


# ── recovery ─────────────────────────────────────────────────────────────────────


def test_recovery_alerts_when_we_had_alerted(db_session: Any, spy: _SpyHealthPublisher) -> None:
    conn = _connection(db_session)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=5, recovered=True)
    assert [r.state for r in spy.reports] == [HEALTH_RECOVERED]


@pytest.mark.parametrize("streak", [0, 1, 2])
def test_recovery_is_silent_when_we_never_alerted(
    db_session: Any, spy: _SpyHealthPublisher, streak: int
) -> None:
    """A blip that self-heals under the threshold produced no failure alert, so its
    'recovery' would be an all-clear for an alarm nobody heard."""
    conn = _connection(db_session)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=streak, recovered=True)
    assert spy.reports == []


# ── the dispatch path ────────────────────────────────────────────────────────────


def test_dispatch_builds_from_persisted_health(db_session: Any, spy: _SpyHealthPublisher) -> None:
    conn = _connection(
        db_session,
        consecutive_poll_failures=3,
        last_poll_error="auth_failed",
        last_polled_at=datetime.now(UTC),
    )
    assert dispatch.publish_connection_health(
        db_session, connection_id=conn.id, state=HEALTH_FAILING
    )
    report = spy.reports[0]
    assert report.connection_name == conn.name
    assert report.connection_type == "dbt"
    assert report.reason == "auth_failed"
    assert report.consecutive_failures == 3


def test_dispatch_tolerates_a_deleted_connection(db_session: Any, spy: _SpyHealthPublisher) -> None:
    """The connection can be deleted between the poll and the alert."""
    assert not dispatch.publish_connection_health(
        db_session, connection_id=uuid.uuid4(), state=HEALTH_FAILING
    )
    assert spy.reports == []


def test_a_broken_channel_never_breaks_the_poll(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safety property: notification failure is contained. A dead Slack webhook must
    not raise out of the polling sweep it is reporting on."""
    monkeypatch.setattr(registry, "get_health_publisher", lambda: _SpyHealthPublisher(boom=True))
    conn = _connection(db_session)
    assert not dispatch.publish_connection_health(
        db_session, connection_id=conn.id, state=HEALTH_FAILING
    )


def test_recovery_report_carries_no_reason(db_session: Any, spy: _SpyHealthPublisher) -> None:
    """A recovered connection still has last_poll_error set from the failure that
    preceded it; the recovery alert must not present a stale error as current."""
    conn = _connection(db_session, last_poll_error="auth_failed")
    dispatch.publish_connection_health(db_session, connection_id=conn.id, state=HEALTH_RECOVERED)
    assert spy.reports[0].reason is None


# ── the wiring: drive the real poll sweep, not just the helper ───────────────────


class _Store:
    def get(self, name: str) -> str:
        return "secret"

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class _RaisingProvider:
    provider = "dbt"
    resource_config_key = "project_name"

    def list_recent_runs(self, config: Any, secret: str, since: Any) -> Any:
        raise PermissionError(f"AuthenticationFailed: SAS expired {_SAS}")


class _HealthyProvider:
    provider = "dbt"
    resource_config_key = "project_name"

    def list_recent_runs(self, config: Any, secret: str, since: Any) -> Any:
        return []


def _sweep(db: Any) -> None:
    tasks._poll_orchestration_runs(
        db, secret_store=_Store(), lookback=timedelta(minutes=15), now=datetime.now(UTC)
    )


def test_five_failing_sweeps_produce_exactly_one_alert(
    db_session: Any, spy: _SpyHealthPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the real beat task: the poll fails five times in a row and the
    operator is told once, at the 3rd — the crossing — with a classified reason."""
    conn = _connection(db_session)
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: _RaisingProvider())

    for _ in range(5):
        _sweep(db_session)

    assert [r.state for r in spy.reports] == [HEALTH_FAILING]
    assert spy.reports[0].consecutive_failures == 3
    assert spy.reports[0].connection_id == conn.id
    assert _SAS not in str(spy.reports[0])

    # …and when the credential is fixed, the recovery closes the loop — once.
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: _HealthyProvider())
    _sweep(db_session)
    _sweep(db_session)

    assert [r.state for r in spy.reports] == [HEALTH_FAILING, HEALTH_RECOVERED]


# ── the redaction property: no raw exception text in any channel ─────────────────


def test_builder_never_derives_the_reason_from_an_exception(db_session: Any) -> None:
    """The reason is read from the CLASSIFIED column, so whatever the transport raised
    (here: a SAS query string) is structurally unable to reach the report."""
    conn = _connection(db_session, consecutive_poll_failures=3, last_poll_error="auth_failed")
    report = build_connection_health_report(conn, state=HEALTH_FAILING)
    assert report.reason == "auth_failed"
    assert _SAS not in str(report)


@pytest.mark.parametrize(
    "render",
    [
        lambda r: str(render_teams_health_message(r)),
        lambda r: str(render_slack_health_message(r)),
        render_health_text_body,
        render_health_html_body,
    ],
)
def test_no_channel_can_leak_a_credential(render: Any) -> None:
    """Belt-and-braces: even handed a report whose reason somehow contained a SAS, a
    renderer must not be the thing that ships it — the assertion is that the reason we
    pass through is the only field they read, so a classified reason renders and this
    test would fail loudly the day someone interpolates an exception instead."""
    body = render(_report(reason="auth_failed"))
    assert "auth_failed" in body
    assert _SAS not in body


# ── rendering ────────────────────────────────────────────────────────────────────


def test_teams_card_titles_the_failure_with_its_streak() -> None:
    card = render_teams_health_message(_report(failures=3))
    body = card["attachments"][0]["content"]["body"]
    assert "3 consecutive failures" in body[0]["text"]
    assert body[0]["color"] == "attention"


def test_teams_card_reads_positive_on_recovery() -> None:
    card = render_teams_health_message(_report(state=HEALTH_RECOVERED, reason=None))
    body = card["attachments"][0]["content"]["body"]
    assert "recovered" in body[0]["text"]
    assert body[0]["color"] == "good"


def test_slack_message_carries_the_facts_and_a_deep_link() -> None:
    message = render_slack_health_message(_report())
    blocks: list[Any] = list(message["blocks"])  # type: ignore[call-overload]  # Block Kit blocks are dicts
    assert "poll failing" in str(message["text"])
    fields = str(blocks[1]["fields"])
    assert "dbt-prod" in fields and "auth_failed" in fields
    assert blocks[-1]["elements"][0]["url"] == "https://dataq.example/connections"


def test_recovery_omits_the_failure_count_from_the_facts() -> None:
    """'0 consecutive failures' on a recovery card is noise at best, confusing at worst."""
    text = render_health_text_body(_report(state=HEALTH_RECOVERED, failures=0, reason=None))
    assert "Consecutive failures" not in text
    assert "recovered" in text


def test_html_body_escapes_the_connection_name() -> None:
    report = ConnectionHealthReport(
        connection_id=uuid.uuid4(),
        connection_name="<script>alert(1)</script>",
        connection_type="dbt",
        state=HEALTH_FAILING,
        consecutive_failures=3,
        reason="auth_failed",
        last_polled_at=None,
        connection_url=None,
    )
    html = render_health_html_body(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
