"""Tests for the TeamsPublisher — per-suite config, webhook resolution, policy."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.teams import TeamsPublisher
from backend.app.db.models import (
    Connection,
    NotificationChannel,
    Suite,
    SuiteNotification,
    SuiteNotificationChannel,
    User,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

_WS_NAME = "teams-webhook"
_WS_URL = "https://contoso.webhook.office.com/workspace"
_SUITE_URL = "https://contoso.webhook.office.com/suite"


class _CapturePost:
    def __init__(self, *, status_code: int = 200) -> None:
        self.calls: list[str] = []
        self._status_code = status_code

    def __call__(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        self.calls.append(url)
        return httpx.Response(self._status_code, request=httpx.Request("POST", url))


def _suite(db: Any) -> Suite:
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
    db.commit()
    return suite


def _config(db: Any, suite: Suite, **kw: Any) -> SuiteNotification:
    cfg = SuiteNotification(suite_id=suite.id, **kw)
    db.add(cfg)
    db.commit()
    return cfg


def _report(
    suite: Suite, *, worst: str | None = "fail", run_status: str = "succeeded"
) -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=suite.id,
        suite_name=suite.name,
        run_status=run_status,
        datasource_type="snowflake",
        target_label="T",
        worst_severity=worst,
        counts={worst: 1} if worst else {"pass": 1},
        checks=[CheckReport("c", "e", worst or "pass", None, None, None, None)],
        finished_at=None,
    )


def _publisher(secrets: dict[str, str], *, workspace: str | None = _WS_NAME) -> TeamsPublisher:
    return TeamsPublisher(secret_store=FakeSecretStore(secrets), workspace_secret_name=workspace)


def _link_channel(db: Any, suite: Suite, *, type: str = "teams", secret_ref: str | None) -> None:
    channel = NotificationChannel(
        id=uuid.uuid4(), name="c", type=type, webhook_secret_ref=secret_ref, created_by=None
    )
    db.add(channel)
    db.flush()
    db.add(SuiteNotificationChannel(suite_id=suite.id, channel_id=channel.id))
    db.commit()


def test_falls_back_to_workspace_webhook(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)  # no config row
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="fail"))
    assert post.calls == [_WS_URL]


def test_non_allowlisted_webhook_host_is_skipped(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An https URL on a host outside the Teams/Power-Automate allowlist is dropped
    # at the send sink (SSRF guard) rather than POSTed.
    suite = _suite(db_session)
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: "https://evil.example/exfil"}).publish(
        db_session, _report(suite, worst="critical")
    )
    assert post.calls == []


def test_cleartext_http_webhook_is_skipped(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An http:// workspace webhook must never be POSTed. The workspace value is never
    write-validated (only per-suite webhooks are), so without this check the alert — and
    everything it carries — would go over the wire in cleartext (matches Slack, #639).
    """
    suite = _suite(db_session)
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: "http://contoso.webhook.office.com/workspace"}).publish(
        db_session, _report(suite, worst="critical")
    )
    assert post.calls == []


def test_per_suite_webhook_overrides_workspace(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    _config(db_session, suite, enabled=True, alert_on="fail", webhook_secret_ref="suite-ref")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL, "suite-ref": _SUITE_URL}).publish(
        db_session, _report(suite, worst="fail")
    )
    assert post.calls == [_SUITE_URL]


def test_disabled_suite_does_not_send(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    _config(db_session, suite, enabled=False, alert_on="always")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="critical"))
    assert post.calls == []


def test_policy_fail_skips_warn_only(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    _config(db_session, suite, enabled=True, alert_on="fail")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="warn"))
    assert post.calls == []  # warn is below the 'fail' threshold


def test_policy_warn_sends_warn(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    _config(db_session, suite, enabled=True, alert_on="warn")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="warn"))
    assert post.calls == [_WS_URL]


def test_policy_always_sends_clean_run(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    _config(db_session, suite, enabled=True, alert_on="always")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    # A clean (all-pass) run is a heartbeat under 'always'.
    _publisher({_WS_NAME: _WS_URL}).publish(
        db_session, _report(suite, worst=None, run_status="succeeded")
    )
    assert post.calls == [_WS_URL]


def test_no_webhook_resolves_is_a_noop(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)  # no config, no workspace webhook configured
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({}, workspace=None).publish(db_session, _report(suite, worst="fail"))
    assert post.calls == []


def test_default_policy_when_no_config(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # No config row → default 'warn' policy: a warn run still sends.
    suite = _suite(db_session)
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="warn"))
    assert post.calls == [_WS_URL]


def test_http_error_is_isolated_not_raised(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed destination is logged, not raised (#1514): a suite can fan out to
    several Teams destinations, and one failing must never abort the whole
    publish() call — the attempt itself still happens.
    """
    suite = _suite(db_session)
    post = _CapturePost(status_code=500)
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="critical"))
    assert post.calls == [_WS_URL]


def test_channel_alone_delivers_with_no_suite_or_workspace_webhook(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A channel is a destination in its own right (#1514) — it must not need the
    legacy per-suite/workspace webhook to also be configured.
    """
    suite = _suite(db_session)
    channel_url = "https://contoso.webhook.office.com/channel"
    _link_channel(db_session, suite, secret_ref="ch-1")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({"ch-1": channel_url}, workspace=None).publish(
        db_session, _report(suite, worst="fail")
    )
    assert post.calls == [channel_url]


def test_primary_and_channel_pointing_at_the_same_url_send_once(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    _link_channel(db_session, suite, secret_ref="ch-1")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({_WS_NAME: _WS_URL, "ch-1": _WS_URL}).publish(
        db_session, _report(suite, worst="fail")
    )
    assert post.calls == [_WS_URL]


def test_a_failing_channel_does_not_block_delivery_to_another(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    bad_url = "https://contoso.webhook.office.com/bad"
    good_url = "https://contoso.webhook.office.com/good"
    _link_channel(db_session, suite, secret_ref="ch-bad")
    _link_channel(db_session, suite, secret_ref="ch-good")
    calls: list[str] = []

    def post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        calls.append(url)
        status = 500 if url == bad_url else 200
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    _publisher({"ch-bad": bad_url, "ch-good": good_url}, workspace=None).publish(
        db_session, _report(suite, worst="fail")
    )
    # Both were attempted — the bad one failing did not stop the loop before
    # reaching the good one (order isn't guaranteed, so check membership).
    assert set(calls) == {bad_url, good_url}


def test_every_destination_failing_still_logs_the_aggregate_event(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """publish() itself never raises anymore (#1514's isolation), so
    CompositePublisher's try/except can no longer observe a Teams failure —
    this aggregate warning is what replaces that signal.
    """
    from structlog.testing import capture_logs

    suite = _suite(db_session)
    monkeypatch.setattr(httpx, "post", _CapturePost(status_code=500))
    with capture_logs() as logs:
        _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="fail"))
    events = [e["event"] for e in logs]
    assert "channel_publish_failed" in events
