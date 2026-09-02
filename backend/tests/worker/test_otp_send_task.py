"""The `send_otp_code` worker task (#1731) — the OTP mailer, off the request path.

Real `OtpMailer`; only `smtplib.SMTP` is replaced, at the transport boundary.
"""

from __future__ import annotations

import smtplib

import pytest

from backend.app.core.config import Settings
from backend.app.core.secrets import SecretNotFoundError
from backend.app.worker import tasks
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.smtp_stubs import BrokenSMTP, CapturingSMTP


def _settings() -> Settings:
    return Settings(
        auth_email_smtp_host="smtp.example.com",
        auth_email_username="dataq@example.com",
        auth_email_from="dataq@example.com",
        auth_email_password_secret_name="auth-email-password",
        auth_otp_allowed_domains="acme.io",
    )


@pytest.fixture(autouse=True)
def _worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    CapturingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", CapturingSMTP)
    monkeypatch.setattr(tasks, "get_settings", _settings)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: FakeSecretStore(default="app-pw"))


def test_the_task_delivers_the_code_over_smtp() -> None:
    assert tasks.send_otp_code(to="ada@acme.io", code="042917", expires_in_minutes=10) is True
    to, body = CapturingSMTP.sent[0]
    assert to == "ada@acme.io"
    assert "042917" in body
    assert "10 minutes" in body


def test_a_transport_failure_returns_false_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing upstream can act on a raise (the request already answered `ok`),
    and a raised task would put the address + code into the failure traceback
    Celery stores and logs.
    """
    monkeypatch.setattr(smtplib, "SMTP", BrokenSMTP)
    assert tasks.send_otp_code(to="ada@acme.io", code="042917", expires_in_minutes=10) is False


def test_a_missing_password_secret_returns_false_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The secret lookup moved here with the send — the not-configured class
    (ADR 0039 decision 6) is now a worker log line, not a 503.
    """
    monkeypatch.setattr(
        tasks,
        "get_secret_store",
        lambda: FakeSecretStore(raise_on_get=SecretNotFoundError("not set")),
    )
    assert tasks.send_otp_code(to="ada@acme.io", code="042917", expires_in_minutes=10) is False
    assert CapturingSMTP.sent == []


def test_the_task_stores_no_result() -> None:
    """The message carries a live sign-in code; nothing about it belongs in the
    result backend.
    """
    assert tasks.send_otp_code.ignore_result is True
