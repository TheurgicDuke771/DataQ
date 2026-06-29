"""Unit tests for the MCP auth module (no DB, no network).

Covers the two operating modes and user resolution: real Azure mode builds a
JWTVerifier from the same tenant/audience/scope as the REST API; dev-bypass and
unconfigured modes build none; and `resolve_current_user` reads the validated
token's claims (or the dev-bypass user).
"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier

from backend.app.core.auth import DEV_BYPASS_AAD_OID, DEV_BYPASS_EMAIL
from backend.app.core.config import Settings
from backend.app.mcp import auth


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def test_build_auth_provider_real_mode_is_jwt_verifier_for_the_api_app() -> None:
    s = _settings(azure_tenant_id="tenant-1", azure_api_client_id="api-client", environment="prod")
    provider = auth.build_auth_provider(s)
    assert isinstance(provider, JWTVerifier)


def test_build_auth_provider_none_without_azure() -> None:
    assert auth.build_auth_provider(_settings()) is None


def test_mcp_enabled_real_or_dev_bypass_only() -> None:
    assert auth.mcp_enabled(_settings(azure_tenant_id="t", azure_api_client_id="c")) is True
    assert auth.mcp_enabled(_settings(environment="dev", auth_dev_bypass=True)) is True
    # Neither real auth nor dev bypass → not enabled (fail-closed, never unauthenticated).
    assert auth.mcp_enabled(_settings(environment="prod")) is False


def test_resolve_user_from_token_claims(db_session: Any, monkeypatch: Any) -> None:
    token = SimpleNamespace(
        claims={"oid": "aad-oid-123", "preferred_username": "ada@acme.io", "name": "Ada"},
        subject="aad-oid-123",
    )
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    user = auth.resolve_current_user(db_session)
    assert user.aad_object_id == "aad-oid-123"
    assert user.email == "ada@acme.io"


def test_resolve_user_rejects_guest_by_default(db_session: Any, monkeypatch: Any) -> None:
    """A guest token (acct=1) is rejected unless azure_allow_guest_users — same as REST."""
    token = SimpleNamespace(claims={"oid": "g1", "preferred_username": "g@ext", "acct": 1})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(azure_allow_guest_users=False))
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)


def test_resolve_user_allows_guest_when_enabled(db_session: Any, monkeypatch: Any) -> None:
    token = SimpleNamespace(claims={"oid": "g1", "preferred_username": "g@ext", "acct": 1})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(azure_allow_guest_users=True))
    assert auth.resolve_current_user(db_session).aad_object_id == "g1"


def test_resolve_user_requires_oid_no_subject_fallback(db_session: Any, monkeypatch: Any) -> None:
    """A token without `oid` is not silently keyed on the pairwise `sub`."""
    token = SimpleNamespace(claims={"preferred_username": "x@acme.io"}, subject="pairwise-sub")
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(environment="prod"))
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)


def test_resolve_user_dev_bypass_when_no_token(db_session: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    monkeypatch.setattr(
        auth, "get_settings", lambda: _settings(environment="dev", auth_dev_bypass=True)
    )
    user = auth.resolve_current_user(db_session)
    assert user.aad_object_id == DEV_BYPASS_AAD_OID
    assert user.email == DEV_BYPASS_EMAIL


def test_resolve_user_raises_when_unauthenticated_and_no_bypass(
    db_session: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(environment="prod"))
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)
