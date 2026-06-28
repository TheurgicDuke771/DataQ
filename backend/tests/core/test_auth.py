"""Tests for the Azure auth scheme builder (offline — no token validation)."""

from __future__ import annotations

from backend.app.core.auth import _build_azure_scheme
from backend.app.core.config import Settings


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
    assert _build_azure_scheme(unconfigured) is None


def test_allow_guest_users_defaults_false() -> None:
    assert Settings().azure_allow_guest_users is False
    scheme = _build_azure_scheme(_azure_settings())
    assert scheme is not None
    # Secure default: guests are rejected unless explicitly opted in.
    assert scheme.allow_guest_users is False


def test_allow_guest_users_propagates_to_scheme() -> None:
    scheme = _build_azure_scheme(_azure_settings(allow_guest_users=True))
    assert scheme is not None
    assert scheme.allow_guest_users is True
