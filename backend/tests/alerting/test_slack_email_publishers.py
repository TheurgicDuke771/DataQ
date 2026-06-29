"""Tests for the Slack + email publishers and the registry composite.

Covers the two things that matter without a live send: the **gating** (each
publisher is a quiet no-op when unconfigured / below the suite's threshold) and
the **rendering** (a failing report produces a sane Slack payload / email body).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from backend.app.alerting import email as email_mod
from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.composite import CompositePublisher
from backend.app.alerting.email import EmailPublisher, render_subject
from backend.app.alerting.routing import route_for
from backend.app.alerting.slack import SlackPublisher, render_slack_message


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


def test_slack_noop_when_unconfigured(db_session) -> None:
    """No webhook secret name → never touches the network."""
    pub = SlackPublisher(secret_store=_Store({}), webhook_secret_name=None, allowed_hosts=())
    pub.publish(db_session, _report(worst="fail"))  # must not raise / post


def test_email_noop_when_unconfigured(db_session) -> None:
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
    db_session, monkeypatch: pytest.MonkeyPatch
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


def test_composite_isolates_a_failing_child(db_session) -> None:
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
