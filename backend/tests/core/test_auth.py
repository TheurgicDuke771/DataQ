"""Tests for the Azure auth scheme builder (offline — no token validation)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi_azure_auth.user import User as AzureUser

import backend.app.core.auth as auth_mod
from backend.app.core.config import Settings
from backend.app.core.errors import DataQError


def _azure_settings(*, allow_guest_users: bool = False) -> Settings:
    """Settings with the two fields that make azure_auth_configured() true."""
    return Settings(
        azure_tenant_id="11111111-1111-1111-1111-111111111111",
        azure_api_client_id="22222222-2222-2222-2222-222222222222",
        azure_allow_guest_users=allow_guest_users,
    )


def test_scheme_is_none_when_auth_unconfigured() -> None:
    # Force the azure fields empty so the assertion holds regardless of any
    # ambient AZURE_* env vars on the dev/CI machine (hermetic).
    unconfigured = Settings(azure_tenant_id=None, azure_api_client_id=None)
    assert auth_mod._build_azure_scheme(unconfigured) is None


def test_allow_guest_users_defaults_false() -> None:
    assert Settings().azure_allow_guest_users is False
    scheme = auth_mod._build_azure_scheme(_azure_settings())
    assert scheme is not None
    # Secure default: guests are rejected unless explicitly opted in.
    assert scheme.allow_guest_users is False


def test_allow_guest_users_propagates_to_scheme() -> None:
    scheme = auth_mod._build_azure_scheme(_azure_settings(allow_guest_users=True))
    assert scheme is not None
    assert scheme.allow_guest_users is True


# ── claim extraction + mode wiring (W8 coverage audit) ───────────────────────


def _azure_user(claims: dict[str, Any]) -> AzureUser:
    """A stand-in carrying only what `_extract_claims` reads."""
    return cast(AzureUser, SimpleNamespace(claims=claims))


def test_extract_claims_prefers_preferred_username() -> None:
    oid, email, name = auth_mod._extract_claims(
        _azure_user(
            {
                "oid": "abc-123",
                "preferred_username": "olivia@example.com",
                "email": "ignored@example.com",
                "name": "Olivia",
            }
        )
    )
    assert (oid, email, name) == ("abc-123", "olivia@example.com", "Olivia")


def test_extract_claims_falls_back_email_then_upn_then_empty() -> None:
    assert (
        auth_mod._extract_claims(_azure_user({"oid": "x", "email": "e@example.com"}))[1]
        == "e@example.com"
    )
    assert auth_mod._extract_claims(_azure_user({"oid": "x", "upn": "u@example.com"}))[1] == (
        "u@example.com"
    )
    _oid, email, name = auth_mod._extract_claims(_azure_user({"oid": "x"}))
    assert (email, name) == ("", None)


async def test_init_auth_real_mode_loads_openid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[bool] = []

    class _OpenIdConfig:
        async def load_config(self) -> None:
            loaded.append(True)

    monkeypatch.setattr(auth_mod, "azure_scheme", SimpleNamespace(openid_config=_OpenIdConfig()))
    await auth_mod.init_auth()
    assert loaded == [True]


async def test_init_auth_fails_closed_when_nothing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Azure config and no dev bypass → startup must raise, not limp open."""
    monkeypatch.setattr(auth_mod, "azure_scheme", None)
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
    with pytest.raises(RuntimeError, match="Auth not configured"):
        await auth_mod.init_auth()


def test_get_current_user_real_upserts_from_claims(db_session: Any) -> None:
    user = auth_mod._get_current_user_real(
        _azure_user({"oid": "11111111-2222-3333-4444-555555555555", "upn": "real@example.com"}),
        db_session,
    )
    assert user.email == "real@example.com"
    assert user.aad_object_id == "11111111-2222-3333-4444-555555555555"


def test_get_current_user_unconfigured_raises_503() -> None:
    with pytest.raises(DataQError) as excinfo:
        auth_mod._get_current_user_unconfigured()
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "auth_not_configured"
