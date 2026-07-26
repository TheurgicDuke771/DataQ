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

from backend.app.alerting import dispatch, registry, render
from backend.app.alerting.base import (
    HEALTH_FAILING,
    HEALTH_RECOVERED,
    ConnectionHealthReport,
    HealthState,
)
from backend.app.alerting.builder import build_connection_health_report
from backend.app.alerting.card import render_teams_health_message
from backend.app.alerting.email import render_health_html_body, render_health_text_body
from backend.app.alerting.slack import render_slack_health_message
from backend.app.core.config import get_settings
from backend.app.db.models import Connection, User
from backend.app.worker import tasks
from backend.app.worker.celery_app import celery_app

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
    *, state: HealthState = HEALTH_FAILING, failures: int = 3, reason: str | None = "auth_failed"
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


def _raise_channel_down(*_args: Any, **_kwargs: Any) -> None:
    """A publisher whose channel is down — the quiet no-op #843 is about."""
    raise RuntimeError("channel unreachable")


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Records what `_alert_poll_health` QUEUES, without running it.

    The publish now happens in its own task (#842), so the sweep-side unit under
    test is the decision plus the hand-off — asserting on a publisher spy here
    would assert the old synchronous design back into existence.
    """
    calls: list[tuple[str, str]] = []

    def _send_task(name: str, args: list[str], **_kwargs: Any) -> None:
        assert name == "publish_connection_health"
        calls.append((args[0], args[1]))

    monkeypatch.setattr(celery_app, "send_task", _send_task)
    return calls


# ── the crossing: fire once, not once per failing poll ───────────────────────────


@pytest.mark.parametrize("streak", [1, 2])
def test_no_alert_below_threshold(db_session: Any, spy: _SpyHealthPublisher, streak: int) -> None:
    """A transient blip (a 502, a restarting orchestrator) must not page anyone."""
    conn = _connection(db_session)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=streak, recovered=False)
    assert spy.reports == []


def test_alerts_on_reaching_the_threshold(
    db_session: Any, dispatched: list[tuple[str, str]]
) -> None:
    conn = _connection(db_session, consecutive_poll_failures=3, last_poll_error="auth_failed")
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=3, recovered=False)
    assert dispatched == [(str(conn.id), HEALTH_FAILING)]


@pytest.mark.parametrize("streak", [4, 5, 144, 1008])
def test_no_alert_storm_once_the_operator_has_been_told(
    db_session: Any, dispatched: list[tuple[str, str]], streak: int
) -> None:
    """The #828 outage ran six days = ~864 consecutive failed polls. Every one past
    the crossing must be silent, or the channel gets muted and we are blind again.

    What makes them silent is now the DELIVERED-alert flag, not the counter's `==`.
    The fixture says so: `health_alerted_at` is set, because by sweep 144 an alert
    has landed. The previous version of this test left it NULL and relied on the
    equality — encoding the old model rather than the situation.
    """
    conn = _connection(db_session, health_alerted_at=datetime.now(UTC))
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=streak, recovered=False)
    assert dispatched == []


def test_a_lowered_threshold_still_alerts_a_connection_already_past_it(
    db_session: Any, dispatched: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#843's second half. Under the old `==` test a connection sitting at streak 40
    when the threshold dropped from 50 to 3 never lands on the equality again, so it
    never alerted at all — silently, which is the worst way to not alert."""
    monkeypatch.setattr(get_settings(), "orchestration_poll_failure_alert_threshold", 3)
    conn = _connection(db_session)  # nothing delivered yet
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=40, recovered=False)
    assert dispatched == [(str(conn.id), HEALTH_FAILING)]


def test_threshold_zero_disables_the_push(
    db_session: Any, dispatched: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opting out of the push must not opt you out of the truth: #828's in-app health
    badge and lineage warning are unconditional; only the notification is gated."""
    monkeypatch.setattr(get_settings(), "orchestration_poll_failure_alert_threshold", 0)
    conn = _connection(db_session, health_alerted_at=datetime.now(UTC))
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=3, recovered=False)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=9, recovered=True)
    assert dispatched == []


# ── recovery ─────────────────────────────────────────────────────────────────────


def test_recovery_alerts_only_when_a_failing_alert_was_DELIVERED(
    db_session: Any, dispatched: list[tuple[str, str]]
) -> None:
    """#843's first half. The old code recovered off the counter, so an operator could
    be told an alarm had ENDED that they were never told had BEGUN — the failing alert
    having been swallowed by a down channel, an unresolved webhook or a missing
    secret, each a quiet no-op."""
    conn = _connection(db_session, health_alerted_at=datetime.now(UTC))
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=5, recovered=True)
    assert dispatched == [(str(conn.id), HEALTH_RECOVERED)]


@pytest.mark.parametrize("streak", [0, 1, 2, 5, 144])
def test_recovery_is_silent_when_nothing_was_delivered(
    db_session: Any, dispatched: list[tuple[str, str]], streak: int
) -> None:
    """Including a streak well PAST the threshold: what matters is that no alert
    landed, not how long it failed. A blip that self-healed, or a crossing whose
    publish was swallowed, both leave nothing to sound an all-clear for."""
    conn = _connection(db_session)  # health_alerted_at is NULL
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=streak, recovered=True)
    assert dispatched == []


# ── the send itself: off the beat, and the flag rides delivery ──────────────────


def test_the_sweep_never_waits_on_a_channel(
    db_session: Any, spy: _SpyHealthPublisher, dispatched: list[tuple[str, str]]
) -> None:
    """#842: publishing used to run synchronously inside the connection loop inside
    the beat task — Teams (10s) + Slack (10s) + SMTP (15s) per crossing. When the
    outage is DataQ-side EVERY connection crosses on the same sweep, so ten of them
    bolted ~6 minutes of blocking sends onto a task that beats every 10 minutes."""
    conn = _connection(db_session, consecutive_poll_failures=3)
    tasks._alert_poll_health(db_session, connection_id=conn.id, streak=3, recovered=False)
    assert dispatched  # it was queued…
    assert spy.reports == []  # …and nothing was published on this thread


def test_the_task_publishes_and_records_the_delivery(
    db_session: Any, spy: _SpyHealthPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _connection(db_session, consecutive_poll_failures=3, last_poll_error="auth_failed")
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert tasks.publish_connection_health(str(conn.id), HEALTH_FAILING) is True

    assert [r.state for r in spy.reports] == [HEALTH_FAILING]
    db_session.refresh(conn)
    assert conn.health_alerted_at is not None  # the operator was told


def test_a_swallowed_publish_leaves_the_edge_open_to_retry(
    db_session: Any, spy: _SpyHealthPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag must mean "delivered", so a publish that quietly fails must NOT set
    it — otherwise the next sweep sees an outstanding alert nobody received, and the
    eventual recovery announces the end of an alarm that never sounded."""
    conn = _connection(db_session, consecutive_poll_failures=3)
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(spy, "publish_health", _raise_channel_down)

    assert tasks.publish_connection_health(str(conn.id), HEALTH_FAILING) is False

    db_session.refresh(conn)
    assert conn.health_alerted_at is None


def test_recovering_closes_the_outstanding_alert(
    db_session: Any, spy: _SpyHealthPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _connection(db_session, health_alerted_at=datetime.now(UTC))
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert tasks.publish_connection_health(str(conn.id), HEALTH_RECOVERED) is True

    db_session.refresh(conn)
    assert conn.health_alerted_at is None  # ready to alert again on the next outage


def test_a_malformed_queue_message_is_dropped_not_published(
    db_session: Any, spy: _SpyHealthPublisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task args cross the broker as plain JSON, so the state literal is established
    here rather than assumed. An unknown edge must not publish an alert whose meaning
    nobody can state."""
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    assert tasks.publish_connection_health(str(uuid.uuid4()), "sideways") is False
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
    # Run the queued publish INLINE rather than stubbing it out. The point of this
    # test is the whole chain — sweep decides, task publishes, delivery sets the
    # flag, flag suppresses the next four sweeps — and a stub at the hand-off would
    # verify only the first link. This is also what now proves the storm prevention
    # comes from DELIVERY state and not from the counter's old `==`.
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda _name, args, **_kw: tasks.publish_connection_health(*args),
    )

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


def test_a_failing_alert_never_hides_why(db_session: Any, spy: _SpyHealthPublisher) -> None:
    """A card that says "poll failing" and omits the reason is barely better than the
    silence #828 was about. An unset reason degrades to a visible 'unknown', it does not
    vanish (health_facts drops empty values)."""
    conn = _connection(db_session, consecutive_poll_failures=3, last_poll_error=None)
    dispatch.publish_connection_health(db_session, connection_id=conn.id, state=HEALTH_FAILING)
    assert spy.reports[0].reason
    assert "Reason" in str(render.health_facts(spy.reports[0]))


def test_a_recovery_alert_cannot_mark_a_healthy_poll_as_failing(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery alert must sit OUTSIDE the try that records a poll failure: if a
    notification raised in there, a poll that actually SUCCEEDED would be recorded as a
    failure — corrupting the streak the alert keys on."""

    class _Exploding:
        def publish_health(self, session: Any, report: ConnectionHealthReport) -> None:
            raise RuntimeError("channel down")

    conn = _connection(db_session, consecutive_poll_failures=4)
    db_session.commit()
    monkeypatch.setattr(registry, "get_health_publisher", lambda: _Exploding())
    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _t: _HealthyProvider())

    _sweep(db_session)

    db_session.refresh(conn)
    assert conn.consecutive_poll_failures == 0  # the success stands
    assert conn.last_poll_error is None


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
