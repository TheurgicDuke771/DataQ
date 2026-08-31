"""Tests for the WebhookPublisher — generic HMAC-signed outbound webhook (#1662)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx
import pytest

from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.webhook import WebhookPublisher, render_webhook_payload
from backend.app.db.models import (
    Connection,
    NotificationChannel,
    Suite,
    SuiteNotification,
    SuiteNotificationChannel,
    User,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

# 8.8.8.8 / 1.1.1.1 — stable, unambiguously public IP literals (SSRF-guard-safe,
# DNS-free), matching the notification_service SSRF-guard test convention.
_URL_A = "https://8.8.8.8/hook-a"
_URL_B = "https://1.1.1.1/hook-b"
_UNSAFE_URL = "https://127.0.0.1/hook"


class _CapturePost:
    def __init__(self, *, status_code: int = 200) -> None:
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []
        self._status_code = status_code

    def __call__(
        self, url: str, *, content: bytes, headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        self.calls.append((url, content, headers))
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


def _publisher(secrets: dict[str, str]) -> WebhookPublisher:
    return WebhookPublisher(secret_store=FakeSecretStore(secrets))


def _link_channel(
    db: Any, suite: Suite, *, webhook_url: str | None, hmac_secret_ref: str | None
) -> None:
    channel = NotificationChannel(
        id=uuid.uuid4(),
        name="c",
        type="webhook",
        webhook_url=webhook_url,
        hmac_secret_ref=hmac_secret_ref,
        created_by=None,
    )
    db.add(channel)
    db.flush()
    db.add(SuiteNotificationChannel(suite_id=suite.id, channel_id=channel.id))
    db.commit()


def test_no_linked_channel_is_a_noop(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({}).publish(db_session, _report(suite, worst="fail"))
    assert post.calls == []


def test_publish_signs_the_body_and_posts_it(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    secret = "shh-signing-key"
    _link_channel(db_session, suite, webhook_url=_URL_A, hmac_secret_ref="hmac-1")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)

    report = _report(suite, worst="fail")
    _publisher({"hmac-1": secret}).publish(db_session, report)

    assert len(post.calls) == 1
    url, body, headers = post.calls[0]
    assert url == _URL_A
    assert headers["Content-Type"] == "application/json"
    expected_signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert headers["X-DataQ-Signature"] == expected_signature
    payload = json.loads(body)
    assert payload["run_id"] == str(report.run_id)
    assert payload["suite_name"] == "Orders QA"
    assert payload["event"] == "run.completed"


def test_disabled_suite_does_not_send(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    _config(db_session, suite, enabled=False, alert_on="always")
    _link_channel(db_session, suite, webhook_url=_URL_A, hmac_secret_ref="hmac-1")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({"hmac-1": "s"}).publish(db_session, _report(suite, worst="critical"))
    assert post.calls == []


def test_policy_fail_skips_warn_only(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    _config(db_session, suite, enabled=True, alert_on="fail")
    _link_channel(db_session, suite, webhook_url=_URL_A, hmac_secret_ref="hmac-1")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({"hmac-1": "s"}).publish(db_session, _report(suite, worst="warn"))
    assert post.calls == []


def test_duplicate_url_across_channels_sends_once(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    _link_channel(db_session, suite, webhook_url=_URL_A, hmac_secret_ref="hmac-1")
    _link_channel(db_session, suite, webhook_url=_URL_A, hmac_secret_ref="hmac-2")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({"hmac-1": "s1", "hmac-2": "s2"}).publish(db_session, _report(suite, worst="fail"))
    assert len(post.calls) == 1


def test_a_failing_destination_does_not_block_delivery_to_another(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    _link_channel(db_session, suite, webhook_url=_URL_A, hmac_secret_ref="hmac-1")
    _link_channel(db_session, suite, webhook_url=_URL_B, hmac_secret_ref="hmac-2")
    calls: list[str] = []

    def post(
        url: str, *, content: bytes, headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        calls.append(url)
        status = 500 if url == _URL_A else 200
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    _publisher({"hmac-1": "s1", "hmac-2": "s2"}).publish(db_session, _report(suite, worst="fail"))
    assert set(calls) == {_URL_A, _URL_B}


def test_every_destination_failing_logs_the_aggregate_event(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from structlog.testing import capture_logs

    suite = _suite(db_session)
    _link_channel(db_session, suite, webhook_url=_URL_A, hmac_secret_ref="hmac-1")
    monkeypatch.setattr(httpx, "post", _CapturePost(status_code=500))
    with capture_logs() as logs:
        _publisher({"hmac-1": "s"}).publish(db_session, _report(suite, worst="fail"))
    events = [e["event"] for e in logs]
    assert "channel_publish_failed" in events


def test_a_channel_with_an_unsafe_url_is_skipped_at_send_time(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SSRF guard runs at channel-config time too, but this proves the
    send-time re-check is load-bearing on its own — a row that somehow carries
    an unsafe URL (e.g. written directly, bypassing the service layer) must
    still never be POSTed to.
    """
    suite = _suite(db_session)
    _link_channel(db_session, suite, webhook_url=_UNSAFE_URL, hmac_secret_ref="hmac-1")
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({"hmac-1": "s"}).publish(db_session, _report(suite, worst="fail"))
    assert post.calls == []


def test_a_channel_with_no_url_or_secret_is_not_a_destination(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    _link_channel(db_session, suite, webhook_url=None, hmac_secret_ref=None)
    post = _CapturePost()
    monkeypatch.setattr(httpx, "post", post)
    _publisher({}).publish(db_session, _report(suite, worst="fail"))
    assert post.calls == []


def test_publish_health_is_an_honest_noop(db_session: Any) -> None:
    """No workspace-level generic webhook exists — a skip must read as a skip,
    never as a silent delivered=True.
    """
    from backend.app.alerting.base import HEALTH_FAILING, ConnectionHealthReport

    report = ConnectionHealthReport(
        connection_id=uuid.uuid4(),
        connection_name="c",
        connection_type="snowflake",
        state=HEALTH_FAILING,
        consecutive_failures=3,
        reason="boom",
        last_polled_at=None,
    )
    assert _publisher({}).publish_health(db_session, report) is False


def test_publish_poll_staleness_is_an_honest_noop(db_session: Any) -> None:
    from backend.app.alerting.base import HEALTH_FAILING, PollStalenessReport

    report = PollStalenessReport(
        state=HEALTH_FAILING, connection_count=2, most_recent_polled_at=None, threshold_seconds=600
    )
    assert _publisher({}).publish_poll_staleness(db_session, report) is False


def test_render_webhook_payload_includes_owner_and_incidents(db_session: Any) -> None:
    suite = _suite(db_session)
    report = _report(suite, worst="fail")
    payload = render_webhook_payload(report)
    assert payload["owner"] is None
    assert payload["incidents"] == []
    assert payload["counts"] == {"fail": 1}
    assert payload["failed_checks"] == 1
