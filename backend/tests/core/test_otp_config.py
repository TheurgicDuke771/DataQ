"""Fail-closed OTP configuration — ADR 0032 decision 2 (#734).

The failure being prevented is specific: a deployment that boots, serves
`/healthz`, renders a sign-in screen, and cannot log anybody in. Because
`otp/request` answers identically for an ineligible address (anti-enumeration),
an empty allowlist is *indistinguishable from working* to the person trying to
sign in — nobody would see an error, they would just never receive a code. So the
misconfiguration has to be caught at startup, naming the missing variables.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import backend.app.core.auth as auth_mod
from backend.app.core.config import Settings

_COMPLETE_EMAIL: dict[str, Any] = {
    "auth_email_smtp_host": "smtp.example.com",
    "auth_email_username": "dataq@example.com",
    "auth_email_from": "dataq@example.com",
    "auth_email_password_secret_name": "auth-email-password",
}


def _otp_settings(**overrides: Any) -> Settings:
    base = dict(_COMPLETE_EMAIL)
    base["auth_otp_allowed_domains"] = "acme.io"
    base.update(overrides)
    return Settings(**base)


# ── the validator ────────────────────────────────────────────────────────────


def test_a_deployment_that_configures_none_of_it_boots_normally() -> None:
    """No OTP fields touched → the whole block is inert. An Azure-only or
    dev-bypass deployment must not have to carry any of these vars."""
    s = Settings(azure_tenant_id=None, azure_api_client_id=None)
    assert s.otp_auth_configured is False
    assert s.auth_email_configured is False


@pytest.mark.parametrize(
    ("omit", "named"),
    [
        ("auth_email_smtp_host", "AUTH_EMAIL_SMTP_HOST"),
        ("auth_email_username", "AUTH_EMAIL_USERNAME"),
        ("auth_email_from", "AUTH_EMAIL_FROM"),
        ("auth_email_password_secret_name", "AUTH_EMAIL_PASSWORD_SECRET_NAME"),
    ],
)
def test_a_partial_email_block_refuses_to_boot_and_NAMES_the_missing_var(
    omit: str, named: str
) -> None:
    kwargs = dict(_COMPLETE_EMAIL)
    kwargs[omit] = None
    kwargs["auth_otp_allowed_domains"] = "acme.io"
    with pytest.raises(ValueError) as caught:
        Settings(**kwargs)
    message = str(caught.value)
    assert named in message, "the operator is not told which variable is missing"


def test_all_the_missing_vars_are_named_at_once() -> None:
    """Collected, not short-circuited — the `_validate_secret_store` precedent. An
    operator missing three should learn three, not fix one and redeploy to find
    the next."""
    with pytest.raises(ValueError) as caught:
        Settings(auth_email_smtp_host="smtp.example.com", auth_otp_allowed_domains="acme.io")
    message = str(caught.value)
    for named in ("AUTH_EMAIL_USERNAME", "AUTH_EMAIL_FROM", "AUTH_EMAIL_PASSWORD_SECRET_NAME"):
        assert named in message


def test_a_complete_mailer_with_an_EMPTY_allowlist_refuses_to_boot() -> None:
    """ADR 0032 decision 5: no open registration, and no silently-closed one either."""
    with pytest.raises(ValueError) as caught:
        Settings(**_COMPLETE_EMAIL)
    message = str(caught.value)
    assert "AUTH_OTP_ALLOWED_EMAILS" in message
    assert "AUTH_OTP_ALLOWED_DOMAINS" in message


def test_an_allowlist_with_no_mailer_refuses_to_boot() -> None:
    """The other half of the same trap: eligible users, no way to reach them."""
    with pytest.raises(ValueError) as caught:
        Settings(auth_otp_allowed_emails="ada@acme.io")
    assert "AUTH_EMAIL_SMTP_HOST" in str(caught.value)


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_whitespace_only_value_counts_as_missing(blank: str) -> None:
    """Bare truthiness would accept `"  "` as an SMTP host, and the deployment
    would fail much later as a DNS error — pointing the operator at their network
    instead of their env file."""
    kwargs = dict(_COMPLETE_EMAIL)
    kwargs["auth_email_smtp_host"] = blank
    kwargs["auth_otp_allowed_domains"] = "acme.io"
    with pytest.raises(ValueError) as caught:
        Settings(**kwargs)
    assert "AUTH_EMAIL_SMTP_HOST" in str(caught.value)


def test_a_whitespace_only_allowlist_is_an_empty_allowlist() -> None:
    kwargs = dict(_COMPLETE_EMAIL)
    kwargs["auth_otp_allowed_domains"] = " , , "
    with pytest.raises(ValueError) as caught:
        Settings(**kwargs)
    assert "AUTH_OTP_ALLOWED_EMAILS" in str(caught.value)


def test_a_fully_configured_block_is_accepted_and_reports_itself_on() -> None:
    s = _otp_settings()
    assert s.auth_email_configured is True
    assert s.otp_auth_configured is True


def test_either_allowlist_alone_is_enough() -> None:
    assert Settings(**_COMPLETE_EMAIL, auth_otp_allowed_emails="ada@acme.io").otp_auth_configured
    assert Settings(**_COMPLETE_EMAIL, auth_otp_allowed_domains="acme.io").otp_auth_configured


# ── allowlist parsing ────────────────────────────────────────────────────────


def test_the_allowlists_normalize_the_same_way_emails_do() -> None:
    s = _otp_settings(
        auth_otp_allowed_emails=" Ada@Acme.IO , grace@acme.io ,, ",
        auth_otp_allowed_domains=" @Acme.IO , Other.Org ",
    )
    assert s.auth_otp_allowed_email_set == frozenset({"ada@acme.io", "grace@acme.io"})
    # A leading `@` is tolerated: `@acme.io` is what an operator naturally writes,
    # and it would otherwise match NOTHING while looking configured — a failure the
    # uniform response hides completely.
    assert s.auth_otp_allowed_domain_set == frozenset({"acme.io", "other.org"})


def test_the_session_ttl_is_bounded() -> None:
    """A deployment must not be able to configure a de-facto immortal cookie."""
    with pytest.raises(ValueError):
        Settings(auth_session_ttl_hours=0)
    with pytest.raises(ValueError):
        Settings(auth_session_ttl_hours=100_000)
    assert Settings(auth_session_ttl_hours=24).auth_session_ttl_hours == 24


@pytest.mark.parametrize("blank", ["", "  "])
def test_a_blank_cookie_secure_means_infer_not_a_boot_failure(blank: str) -> None:
    """`.env.app.example` ships every optional key blank, and blank is the
    RECOMMENDED value here (infer from `X-Forwarded-Proto`). Without this, the
    shipped template would refuse to boot — pydantic parses `""` for `bool | None`
    as an invalid boolean, not as absent."""
    assert Settings(auth_session_cookie_secure=blank).auth_session_cookie_secure is None


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("false", False)])
def test_an_explicit_cookie_secure_still_parses(raw: str, expected: bool) -> None:
    assert Settings(auth_session_cookie_secure=raw).auth_session_cookie_secure is expected


def test_the_smtp_timeout_is_bounded() -> None:
    """It runs inside a sign-in request; an unbounded value would hold a worker
    thread for the connect default."""
    with pytest.raises(ValueError):
        Settings(auth_email_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(auth_email_timeout_seconds=600)


# ── init_auth's fourth branch ────────────────────────────────────────────────


async def test_init_auth_accepts_otp_only_and_does_not_require_azure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OTP alone is a complete auth configuration — that is the gap ADR 0032
    exists to close (a non-Azure / fully-local deployment with no way to log a
    human in)."""
    monkeypatch.setattr(auth_mod, "azure_scheme", None)
    monkeypatch.setattr(auth_mod, "_otp_enabled", True)
    monkeypatch.setattr(auth_mod, "_settings", _otp_settings())
    await auth_mod.init_auth()  # must not raise


async def test_init_auth_still_fails_closed_when_nothing_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_mod, "azure_scheme", None)
    monkeypatch.setattr(auth_mod, "_otp_enabled", False)
    monkeypatch.setattr(
        auth_mod,
        "_settings",
        Settings(
            environment="prod",
            auth_dev_bypass=False,
            azure_tenant_id=None,
            azure_api_client_id=None,
        ),
    )
    with pytest.raises(RuntimeError) as caught:
        await auth_mod.init_auth()
    message = str(caught.value)
    assert "Auth not configured" in message
    # The new branch has to be discoverable from the error, or an operator who
    # WANTED OTP is told only about Azure and dev-bypass.
    assert "AUTH_EMAIL_SMTP_HOST" in message
    assert "AUTH_OTP_ALLOWED_EMAILS" in message


async def test_init_auth_announces_both_modes_when_azure_and_otp_are_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _OpenIdConfig:
        async def load_config(self) -> None:
            return None

    monkeypatch.setattr(auth_mod, "azure_scheme", SimpleNamespace(openid_config=_OpenIdConfig()))
    monkeypatch.setattr(auth_mod, "_otp_enabled", True)
    monkeypatch.setattr(auth_mod, "_settings", _otp_settings())
    monkeypatch.setattr(auth_mod.log, "info", lambda event, **kwargs: events.append(event))
    await auth_mod.init_auth()
    assert events == ["auth_real_mode_ready", "auth_otp_mode_ready"]


def test_the_otp_ready_log_never_carries_an_address(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The allowlist IS the workspace member list. `_PII_KEYS` redacts an `email`
    key, but the honest fix is not to hand it over — so the log reports a count."""
    from backend.app.core.logging import configure_logging

    monkeypatch.setattr(auth_mod, "_settings", _otp_settings(auth_otp_allowed_emails="ada@acme.io"))
    configure_logging()
    auth_mod._log_otp_mode_ready()

    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    assert "auth_otp_mode_ready" in emitted, "nothing was emitted — the rest would be vacuous"
    assert "ada@acme.io" not in emitted
    assert "allowed_email_count" in emitted
