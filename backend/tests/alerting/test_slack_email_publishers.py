"""Tests for the Slack + email publishers and the registry composite.

Covers the two things that matter without a live send: the **gating** (each
publisher is a quiet no-op when unconfigured / below the suite's threshold) and
the **rendering** (a failing report produces a sane Slack payload / email body).
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import pytest

from backend.app.alerting import email as email_mod
from backend.app.alerting import slack as slack_mod
from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.composite import CompositePublisher
from backend.app.alerting.email import EmailPublisher, render_subject
from backend.app.alerting.routing import route_for
from backend.app.alerting.slack import SlackPublisher, render_slack_message
from backend.app.db.models import Connection, Suite, SuiteNotification, User


def _report(*, worst: str | None, run_status: str = "succeeded") -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="Orders Header",
        run_status=run_status,
        datasource_type="snowflake",
        target_label="DATAQ_DB.RETAIL.ORDERS_HEADER",
        worst_severity=worst,
        counts={"pass": 2, "fail": 1} if worst else {"pass": 3},
        checks=[
            CheckReport(
                "order_number not null",
                "expect_column_values_to_not_be_null",
                "pass",
                None,
                None,
                None,
                None,
            ),
            CheckReport(
                "order_total >= 0",
                "expect_column_values_to_be_between",
                worst or "pass",
                None,
                None,
                None,
                {"unexpected_percent": 3.2, "unexpected_count": 51},
            ),
        ],
        finished_at=datetime.now(UTC),
    )


class _Store:
    """Minimal SecretStore double returning a fixed value per name."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, name: str) -> str:
        from backend.app.core.secrets import SecretNotFoundError

        if name not in self._values:
            raise SecretNotFoundError(name)
        return self._values[name]

    def set(self, name: str, value: str) -> None:  # pragma: no cover - unused here
        self._values[name] = value


# ── rendering ────────────────────────────────────────────────────────────────


def test_slack_render_failing_run_has_header_and_failing_check() -> None:
    report = _report(worst="fail")
    body = render_slack_message(report, route_for(report, "warn"))
    assert "1/3 checks failed" in str(body["text"])
    blocks_text = str(body["blocks"])
    assert "order_total >= 0" in blocks_text  # the failing check is listed
    assert "3.2% unexpected" in blocks_text  # redacted sample summary surfaced


def test_slack_render_critical_mentions_channel() -> None:
    report = _report(worst="critical")
    body = render_slack_message(report, route_for(report, "warn"))
    assert "<!channel>" in str(body["blocks"])


def test_email_subject_reflects_verdict() -> None:
    assert "FAIL" in render_subject(_report(worst="fail"))
    assert "all 3 checks passed" in render_subject(_report(worst=None))


def test_email_html_escapes_and_lists_failures() -> None:
    html = email_mod.render_html_body(_report(worst="fail"))
    assert "order_total &gt;= 0" in html  # '>' escaped, check listed


# ── gating (quiet no-op) ─────────────────────────────────────────────────────


def test_slack_noop_when_unconfigured(db_session: Any) -> None:
    """No webhook secret name → never touches the network."""
    pub = SlackPublisher(secret_store=_Store({}), webhook_secret_name=None, allowed_hosts=())
    pub.publish(db_session, _report(worst="fail"))  # must not raise / post


def test_email_noop_when_unconfigured(db_session: Any) -> None:
    pub = EmailPublisher(
        secret_store=_Store({}),
        smtp_host="smtp.example.com",
        smtp_port=587,
        username=None,
        password_secret_name=None,
        sender=None,
        recipients=(),
    )
    pub.publish(db_session, _report(worst="fail"))  # must not raise / connect


def test_slack_noop_on_clean_run_below_threshold(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean run under the default 'warn' policy must not post."""
    posted: list[object] = []
    monkeypatch.setattr("backend.app.alerting.slack.httpx.post", lambda *a, **k: posted.append(a))
    pub = SlackPublisher(
        secret_store=_Store({"wh": "https://hooks.slack.com/services/x"}),
        webhook_secret_name="wh",
        allowed_hosts=("hooks.slack.com",),
    )
    pub.publish(db_session, _report(worst=None))
    assert posted == []


def test_composite_isolates_a_failing_child(db_session: Any) -> None:
    """One child raising must not stop the others."""
    calls: list[str] = []

    class _Boom:
        def publish(self, session, report):  # type: ignore[no-untyped-def]
            calls.append("boom")
            raise RuntimeError("channel down")

    class _Ok:
        def publish(self, session, report):  # type: ignore[no-untyped-def]
            calls.append("ok")

    CompositePublisher([_Boom(), _Ok()]).publish(db_session, _report(worst="fail"))
    assert calls == ["boom", "ok"]  # the second ran despite the first raising


# ── rendering, remaining branches (W8 coverage audit) ────────────────────────


def test_slack_render_clean_run_headline() -> None:
    report = _report(worst=None)
    body = render_slack_message(report, route_for(report, "always"))
    assert "all 3 checks passed" in str(body["text"])
    assert "<!channel>" not in str(body["blocks"])


def _report_with_many_failures(count: int) -> RunReport:
    many = [
        CheckReport(f"check {i}", "expect_x", "fail", None, None, None, {"unexpected_count": i})
        for i in range(count)
    ]
    return dataclasses.replace(
        _report(worst="fail"), checks=many, counts={"pass": 0, "fail": count}
    )


def test_slack_render_truncates_beyond_max_check_lines() -> None:
    report = _report_with_many_failures(25)
    body = render_slack_message(report, route_for(report, "warn"))
    text = str(body["blocks"])
    assert f"…and {25 - slack_mod._MAX_CHECK_LINES} more" in text
    assert "(3 unexpected)" in text  # count-only sample note branch


def test_email_text_body_lists_failures_with_pct_note() -> None:
    text = email_mod.render_text_body(_report(worst="fail"))
    assert "Failing checks:" in text
    assert "[fail] order_total >= 0 — 3.2% unexpected" in text


def test_email_text_body_truncates_beyond_max_check_lines() -> None:
    text = email_mod.render_text_body(_report_with_many_failures(25))
    assert f"…and {25 - email_mod._MAX_CHECK_LINES} more" in text
    assert "0 unexpected" in text  # count-only note; falsy-zero must still render


def test_email_text_body_clean_run_has_no_failing_section() -> None:
    text = email_mod.render_text_body(_report(worst=None))
    assert "Failing checks:" not in text


# ── publish paths (W8 coverage audit) ────────────────────────────────────────


def _disabled_config_suite(db: Any) -> Any:
    """A real suite row with alerting disabled — the gate is exercised DB-backed
    (the test_teams.py pattern), not against a hand-encoded config stand-in."""
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(
        name="Orders QA", connection_id=conn.id, created_by=owner.id, target={"table": "T"}
    )
    db.add(suite)
    db.flush()
    db.add(SuiteNotification(suite_id=suite.id, enabled=False))
    db.commit()
    return suite


class _FakeSmtp:
    """Capture-only stand-in for smtplib.SMTP (the transport boundary)."""

    instances: ClassVar[list[_FakeSmtp]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.message: Any = None
        _FakeSmtp.instances.append(self)

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("closed")

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append(f"login:{username}:{password}")

    def send_message(self, message: Any) -> None:
        self.calls.append("send")
        self.message = message


@pytest.fixture()
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> list[_FakeSmtp]:
    """Patch the SMTP transport; yield a fresh per-test instance registry."""
    _FakeSmtp.instances.clear()
    monkeypatch.setattr("backend.app.alerting.email.smtplib.SMTP", _FakeSmtp)
    return _FakeSmtp.instances


def _email_publisher(store: _Store) -> EmailPublisher:
    return EmailPublisher(
        secret_store=store,
        smtp_host="smtp.example.com",
        smtp_port=587,
        username="alerts@example.com",
        password_secret_name="smtp-pass",
        sender="alerts@example.com",
        recipients=("a@example.com", "b@example.com"),
    )


def test_email_publish_happy_path_sends_over_starttls(
    db_session: Any, fake_smtp: list[_FakeSmtp]
) -> None:
    _email_publisher(_Store({"smtp-pass": "hunter2"})).publish(db_session, _report(worst="fail"))
    (smtp,) = fake_smtp
    assert smtp.calls == ["starttls", "login:alerts@example.com:hunter2", "send", "closed"]
    assert smtp.message["To"] == "a@example.com, b@example.com"
    assert "FAIL" in smtp.message["Subject"]


def test_email_publish_noop_below_threshold(db_session: Any, fake_smtp: list[_FakeSmtp]) -> None:
    """Clean run under the default 'warn' policy must not connect at all."""
    _email_publisher(_Store({"smtp-pass": "hunter2"})).publish(db_session, _report(worst=None))
    assert fake_smtp == []


def test_email_publish_noop_when_suite_disabled_alerting(
    db_session: Any, fake_smtp: list[_FakeSmtp]
) -> None:
    suite = _disabled_config_suite(db_session)
    report = dataclasses.replace(_report(worst="fail"), suite_id=suite.id)
    _email_publisher(_Store({"smtp-pass": "hunter2"})).publish(db_session, report)
    assert fake_smtp == []


def test_email_publish_noop_when_password_secret_missing(
    db_session: Any, fake_smtp: list[_FakeSmtp]
) -> None:
    """Unresolvable password logs a warning and skips — never raises."""
    _email_publisher(_Store({})).publish(db_session, _report(worst="fail"))
    assert fake_smtp == []


class _CapturePost:
    """httpx.post stand-in returning a REAL httpx.Response, so raise_for_status
    is the genuine article (a 4xx/5xx really raises to the composite)."""

    def __init__(self, *, status_code: int = 200) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._status_code = status_code

    def __call__(self, url: str, *, json: dict[str, object], timeout: float) -> httpx.Response:
        self.calls.append((url, json))
        return httpx.Response(self._status_code, request=httpx.Request("POST", url))


def _slack_publisher(store: _Store) -> SlackPublisher:
    return SlackPublisher(
        secret_store=store,
        webhook_secret_name="wh",
        allowed_hosts=("hooks.slack.com",),
    )


def test_slack_publish_happy_path_posts_payload(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    post = _CapturePost()
    monkeypatch.setattr("backend.app.alerting.slack.httpx.post", post)
    store = _Store({"wh": "https://hooks.slack.com/services/T00/B00/xyz"})
    _slack_publisher(store).publish(db_session, _report(worst="fail"))
    ((url, payload),) = post.calls
    assert url.startswith("https://hooks.slack.com/")
    assert "1/3 checks failed" in str(payload["text"])


def test_slack_publish_raises_on_webhook_http_error(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 5xx from the webhook surfaces to the composite (which isolates it)."""
    monkeypatch.setattr("backend.app.alerting.slack.httpx.post", _CapturePost(status_code=500))
    store = _Store({"wh": "https://hooks.slack.com/services/T00/B00/xyz"})
    with pytest.raises(httpx.HTTPStatusError):
        _slack_publisher(store).publish(db_session, _report(worst="fail"))


def test_slack_publish_blocks_non_allowlisted_webhook_host(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSRF guard: a webhook secret pointing off-allowlist is never POSTed."""
    posted: list[object] = []
    monkeypatch.setattr("backend.app.alerting.slack.httpx.post", lambda *a, **k: posted.append(a))
    store = _Store({"wh": "https://evil.example.com/exfil"})
    _slack_publisher(store).publish(db_session, _report(worst="fail"))
    assert posted == []


def test_slack_publish_noop_when_webhook_secret_missing(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[object] = []
    monkeypatch.setattr("backend.app.alerting.slack.httpx.post", lambda *a, **k: posted.append(a))
    _slack_publisher(_Store({})).publish(db_session, _report(worst="fail"))
    assert posted == []


def test_slack_publish_noop_when_suite_disabled_alerting(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    post = _CapturePost()
    monkeypatch.setattr("backend.app.alerting.slack.httpx.post", post)
    suite = _disabled_config_suite(db_session)
    report = dataclasses.replace(_report(worst="fail"), suite_id=suite.id)
    _slack_publisher(_Store({"wh": "https://hooks.slack.com/services/x"})).publish(
        db_session, report
    )
    assert post.calls == []
