"""The developer bypass is an explicit opt-in, never a default or a fallback (#1901)."""

from __future__ import annotations

from typing import Any

import pytest

import backend.app.core.auth as auth_mod
from backend.app.core.config import Settings, dev_bypass_conflicts

_OTP: dict[str, Any] = {
    "auth_email_smtp_host": "smtp.example.com",
    "auth_email_username": "dataq@example.com",
    "auth_email_from": "dataq@example.com",
    "auth_email_password_secret_name": "auth-email-password",
    "auth_otp_allowed_domains": "acme.io",
}


def test_bypass_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    assert Settings(_env_file=None).auth_dev_bypass is False


def test_bypass_alone_in_dev_has_no_conflict() -> None:
    assert dev_bypass_conflicts(Settings(auth_dev_bypass=True, environment="dev")) == []


def test_bypass_off_never_conflicts() -> None:
    assert dev_bypass_conflicts(Settings(auth_dev_bypass=False, environment="prod", **_OTP)) == []


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_bypass_outside_dev_conflicts(environment: str) -> None:
    (problem,) = dev_bypass_conflicts(Settings(auth_dev_bypass=True, environment=environment))
    assert "only honoured with ENVIRONMENT=dev" in problem


@pytest.mark.parametrize(
    "mode,overrides",
    [
        ("email OTP", _OTP),
        ("Azure AD", {"azure_tenant_id": "t", "azure_api_client_id": "c"}),
        ("generic OIDC", {"oidc_issuer": "https://issuer.example.com", "oidc_audience": "aud"}),
    ],
)
def test_bypass_beside_a_real_mode_conflicts(mode: str, overrides: dict[str, Any]) -> None:
    (problem,) = dev_bypass_conflicts(
        Settings(auth_dev_bypass=True, environment="dev", **overrides)
    )
    assert "never a fallback" in problem and mode in problem


async def test_init_auth_refuses_bypass_beside_otp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth_mod, "_settings", Settings(auth_dev_bypass=True, environment="dev", **_OTP)
    )
    with pytest.raises(RuntimeError, match="never a fallback"):
        await auth_mod.init_auth()


async def test_unconfigured_error_names_the_compose_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_mod, "azure_scheme", None)
    monkeypatch.setattr(auth_mod, "oidc_scheme", None)
    monkeypatch.setattr(auth_mod, "_otp_enabled", False)
    monkeypatch.setattr(auth_mod, "_settings", Settings(auth_dev_bypass=False, environment="prod"))
    with pytest.raises(RuntimeError, match="DATAQ_DEV_BYPASS=true"):
        await auth_mod.init_auth()


async def test_init_auth_refuses_bypass_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_mod, "_settings", Settings(auth_dev_bypass=True, environment="prod"))
    with pytest.raises(RuntimeError, match="only honoured with ENVIRONMENT=dev"):
        await auth_mod.init_auth()
