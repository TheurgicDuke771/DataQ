"""Tests for the TeamsPublisher — per-suite config, webhook resolution, policy.

DB-backed: a suite (+ optional notification config row) drives delivery. A
dict-backed fake SecretStore stands in for Key Vault. Skips without
TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.teams import TeamsPublisher
from backend.app.db.models import Connection, Suite, SuiteNotification, User

_WS_NAME = "teams-webhook"
_WS_URL = "https://contoso.webhook.office.com/workspace"
_SUITE_URL = "https://contoso.webhook.office.com/suite"


class _FakeStore:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get(self, name: str) -> str:
        from backend.app.core.secrets import SecretNotFoundError

        try:
            return self._secrets[name]
        except KeyError as exc:
            raise SecretNotFoundError(name) from exc

    def set(self, name: str, value: str) -> None:
        self._secrets[name] = value

    def delete(self, name: str) -> None:
        self._secrets.pop(name, None)


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
    return TeamsPublisher(secret_store=_FakeStore(secrets), workspace_secret_name=workspace)


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
    everything it carries — would go over the wire in cleartext (matches Slack, #639)."""
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


def test_http_error_propagates(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = _suite(db_session)
    monkeypatch.setattr(httpx, "post", _CapturePost(status_code=500))
    with pytest.raises(httpx.HTTPStatusError):
        _publisher({_WS_NAME: _WS_URL}).publish(db_session, _report(suite, worst="critical"))
