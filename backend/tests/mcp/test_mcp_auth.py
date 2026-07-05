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


def test_build_auth_provider_real_mode_is_pat_or_jwt_composite() -> None:
    s = _settings(azure_tenant_id="tenant-1", azure_api_client_id="api-client", environment="prod")
    provider = auth.build_auth_provider(s)
    # Composite (ADR 0026, #461): PAT by prefix, else the Azure JWTVerifier
    # built from the same tenant/audience/scope as the REST API.
    assert isinstance(provider, auth._PatOrJwtVerifier)
    assert isinstance(provider._jwt, JWTVerifier)
    assert provider._jwt.audience == "api-client"


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


# ── PAT branch (ADR 0026, #461) ───────────────────────────────────────────────


def _pat_owner(db_session: Any) -> tuple[Any, str]:
    import uuid

    from backend.app.db.models import User
    from backend.app.services import api_key_service

    user = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="pat@mcp.io")
    db_session.add(user)
    db_session.commit()
    _, token = api_key_service.create_key(db_session, user, name="mcp")
    return user, token


def _composite_verifier() -> auth._PatOrJwtVerifier:
    provider = auth.build_auth_provider(
        _settings(azure_tenant_id="t1", azure_api_client_id="c1", environment="prod")
    )
    assert isinstance(provider, auth._PatOrJwtVerifier)
    return provider


def _use_test_session(monkeypatch: Any, db_session: Any) -> None:
    """Route the verifier's own SessionLocal to the test's savepoint session."""
    import backend.app.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)


async def test_verifier_valid_pat_yields_access_token_with_user_claim(
    db_session: Any, monkeypatch: Any
) -> None:
    user, token = _pat_owner(db_session)
    _use_test_session(monkeypatch, db_session)
    access = await _composite_verifier().verify_token(token)
    assert access is not None
    assert access.claims[auth.PAT_USER_CLAIM] == str(user.id)
    assert access.client_id == "dataq-pat"


async def test_verifier_bad_pat_returns_none_fail_closed(db_session: Any, monkeypatch: Any) -> None:
    from backend.app.services import api_key_service

    _use_test_session(monkeypatch, db_session)
    assert await _composite_verifier().verify_token(api_key_service.TOKEN_PREFIX + "nope") is None


async def test_verifier_non_pat_bearer_delegates_to_jwt(monkeypatch: Any) -> None:
    verifier = _composite_verifier()
    seen: list[str] = []

    async def _fake_jwt_verify(token: str) -> None:
        seen.append(token)
        return None

    monkeypatch.setattr(verifier._jwt, "verify_token", _fake_jwt_verify)
    assert await verifier.verify_token("eyJhbGciOi.some.jwt") is None
    assert seen == ["eyJhbGciOi.some.jwt"]


def test_resolve_user_pat_claim_loads_owner(db_session: Any, monkeypatch: Any) -> None:
    user, _ = _pat_owner(db_session)
    token = SimpleNamespace(claims={auth.PAT_USER_CLAIM: str(user.id)})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    assert auth.resolve_current_user(db_session).id == user.id


def test_resolve_user_pat_claim_missing_user_fails_closed(
    db_session: Any, monkeypatch: Any
) -> None:
    import uuid

    token = SimpleNamespace(claims={auth.PAT_USER_CLAIM: str(uuid.uuid4())})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)
