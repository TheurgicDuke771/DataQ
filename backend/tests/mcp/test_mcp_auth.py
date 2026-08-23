"""Unit tests for the MCP auth module (no DB, no network)."""

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier

from backend.app.core import auth as core_auth
from backend.app.core.auth import DEV_BYPASS_AAD_OID, DEV_BYPASS_EMAIL
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.mcp import auth


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


#: A complete OTP mailer block + allowlist — the minimum `otp_auth_configured`
#: accepts (`Settings._validate_otp_auth` refuses every partial state).
_OTP: dict[str, Any] = {
    "auth_email_smtp_host": "smtp.example.com",
    "auth_email_username": "dataq@example.com",
    "auth_email_from": "dataq@example.com",
    "auth_email_password_secret_name": "auth-email-password",
    "auth_otp_allowed_domains": "acme.io",
}

_AZURE: dict[str, Any] = {"azure_tenant_id": "tenant-1", "azure_api_client_id": "api-client"}
_OIDC: dict[str, Any] = {
    "oidc_issuer": "https://example-idp.test",
    "oidc_audience": "dataq-client-id",
}

#: Nothing configured at all.
_NOTHING: dict[str, Any] = {"environment": "prod", "auth_dev_bypass": False}


def test_build_auth_provider_real_mode_is_pat_or_jwt_composite() -> None:
    s = _settings(**_AZURE, environment="prod")
    provider = auth.build_auth_provider(s)
    # Composite (ADR 0026, #461): PAT by prefix, else the Azure JWTVerifier
    # built from the same tenant/audience/scope as the REST API.
    assert isinstance(provider, auth._PatOrJwtVerifier)
    assert isinstance(provider._jwt, JWTVerifier)
    assert provider._jwt.audience == "api-client"


def test_build_auth_provider_is_none_only_in_dev_bypass() -> None:
    """`None` means "mount without auth" — the one mode allowed to say it."""
    assert auth.build_auth_provider(_settings(environment="dev", auth_dev_bypass=True)) is None


# ── the mode ladder (#1128) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param({**_AZURE, "environment": "prod"}, "azure_ad", id="azure-only"),
        pytest.param({**_OIDC, "environment": "prod"}, "generic_oidc", id="generic-oidc-only"),
        pytest.param({**_OTP, **_NOTHING}, "pat_only", id="otp-only"),
        pytest.param({**_AZURE, **_OTP, "environment": "prod"}, "azure_ad", id="both"),
        pytest.param({**_OIDC, **_OTP, "environment": "prod"}, "generic_oidc", id="oidc-and-otp"),
        pytest.param({"environment": "dev", "auth_dev_bypass": True}, "dev_bypass", id="bypass"),
        pytest.param(_NOTHING, "disabled", id="nothing"),
        # OTP outranks dev-bypass, exactly as it does in `core.auth`'s ladder: resolving a real auth
        # configuration to the unauthenticated bypass would be a downgrade.
        pytest.param(
            {**_OTP, "environment": "dev", "auth_dev_bypass": True}, "pat_only", id="otp+bypass"
        ),
    ],
)
def test_mcp_auth_mode_matrix(config: dict[str, Any], expected: str) -> None:
    assert auth.mcp_auth_mode(_settings(**config)) == expected


def test_mcp_enabled_covers_otp_only_and_still_fails_closed() -> None:
    assert auth.mcp_enabled(_settings(**_AZURE)) is True
    assert auth.mcp_enabled(_settings(environment="dev", auth_dev_bypass=True)) is True
    # The #1128 gap: an OTP deployment's PATs work perfectly, yet /mcp was unmounted.
    assert auth.mcp_enabled(_settings(**_OTP, **_NOTHING)) is True
    # Nothing configured → not enabled (fail-closed, never unauthenticated).
    assert auth.mcp_enabled(_settings(**_NOTHING)) is False


def test_otp_only_builds_a_verifier_with_NO_jwt_half() -> None:
    """The JWT half is absent, not unconfigured-but-present."""
    provider = auth.build_auth_provider(_settings(**_OTP, **_NOTHING))
    assert isinstance(provider, auth._PatOrJwtVerifier)
    assert provider._jwt is None


def test_azure_and_otp_together_keep_the_jwt_half() -> None:
    """A deployment running both must still accept its Azure tokens on /mcp."""
    provider = auth.build_auth_provider(_settings(**_AZURE, **_OTP, environment="prod"))
    assert isinstance(provider, auth._PatOrJwtVerifier)
    assert isinstance(provider._jwt, JWTVerifier)


def test_an_unconfigured_deployment_gets_a_credential_requiring_verifier_not_none() -> None:
    """The unreachable case degrades safely."""
    provider = auth.build_auth_provider(_settings(**_NOTHING))
    assert isinstance(provider, auth._PatOrJwtVerifier)
    assert provider._jwt is None


def test_build_auth_provider_generic_oidc_mode_is_pat_or_jwt_composite(monkeypatch: Any) -> None:
    """The MCP counterpart to `core.auth.OidcBearerScheme` — reuses fastmcp's
    already-generic `JWTVerifier`, just pointed at the configured issuer instead
    of Azure's hardcoded endpoints (see `build_auth_provider`'s docstring).
    """
    monkeypatch.setattr(auth, "discover_jwks_uri", lambda issuer: f"{issuer}/jwks.json")
    s = _settings(**_OIDC, environment="prod")
    provider = auth.build_auth_provider(s)
    assert isinstance(provider, auth._PatOrJwtVerifier)
    assert isinstance(provider._jwt, JWTVerifier)
    assert provider._jwt.audience == "dataq-client-id"
    assert provider._jwt.issuer == "https://example-idp.test"
    # No required_scopes — Azure's API-scope pattern isn't universal (docstring).
    assert not provider._jwt.required_scopes


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


def test_resolve_user_generic_oidc_reads_sub(db_session: Any, monkeypatch: Any) -> None:
    """In `generic_oidc` mode, `sub` IS the right key — unlike Azure's pairwise
    `sub`, generic OIDC has no `oid`-shaped stable-directory alternative; `sub`
    is the RFC 7519 REQUIRED subject claim.
    """
    token = SimpleNamespace(claims={"sub": "cognito-sub-1", "email": "u@example.com"})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(**_OIDC, environment="prod"))
    user = auth.resolve_current_user(db_session)
    assert user.aad_object_id == "cognito-sub-1"
    assert user.email == "u@example.com"
    assert user.oidc_issuer == "https://example-idp.test"


def test_resolve_user_generic_oidc_has_no_guest_policy(db_session: Any, monkeypatch: Any) -> None:
    """The Azure B2B-guest concept doesn't apply to a generic provider — an
    `acct` claim (Azure's guest signal) must not be inspected in this mode.
    """
    token = SimpleNamespace(claims={"sub": "cognito-sub-2", "acct": 1}, token="raw-jwt")
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    # No email in the claims → the #1346 userinfo path fires; neutralize it here
    # (its own behavior is covered by the dedicated tests below).
    monkeypatch.setattr(auth, "fetch_userinfo", lambda issuer, tok: None)
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: _settings(**_OIDC, environment="prod", azure_allow_guest_users=False),
    )
    assert auth.resolve_current_user(db_session).aad_object_id == "cognito-sub-2"


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


def _pat_only_verifier() -> auth._PatOrJwtVerifier:
    provider = auth.build_auth_provider(_settings(**_OTP, **_NOTHING))
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


# ── pat_only mode: a PAT is the ONLY /mcp credential (#1128) ─────────────────


async def test_pat_only_verifier_accepts_a_valid_pat(db_session: Any, monkeypatch: Any) -> None:
    """The point of the change: an OTP deployment's PATs authenticate on /mcp."""
    user, token = _pat_owner(db_session)
    _use_test_session(monkeypatch, db_session)
    access = await _pat_only_verifier().verify_token(token)
    assert access is not None
    assert access.claims[auth.PAT_USER_CLAIM] == str(user.id)


async def test_pat_only_verifier_rejects_a_bad_pat(db_session: Any, monkeypatch: Any) -> None:
    from backend.app.services import api_key_service

    _use_test_session(monkeypatch, db_session)
    assert await _pat_only_verifier().verify_token(api_key_service.TOKEN_PREFIX + "nope") is None


@pytest.mark.parametrize(
    "bearer",
    [
        # Deliberately NOT a decodable JWT: the branch under test never parses the token.
        pytest.param("eyJhbGciOi.some.jwt", id="jwt-shaped"),
        pytest.param("opaque-bearer-that-is-not-a-dataq-token", id="opaque"),
        pytest.param("", id="empty"),
    ],
)
async def test_pat_only_verifier_rejects_every_non_pat_bearer_uniformly(bearer: str) -> None:
    """No JWT branch exists here, so "rejected" must not mean "crashed"."""
    assert await _pat_only_verifier().verify_token(bearer) is None


async def test_pat_only_verifier_rejects_a_session_token() -> None:
    """`dq_sess_` stays a browser-only credential in this mode too (ADR 0032)."""
    from backend.app.services import session_service

    verifier = _pat_only_verifier()
    assert await verifier.verify_token(session_service.TOKEN_PREFIX + "abcdef123456") is None


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


async def test_verifier_rejects_a_SESSION_token_before_the_jwt_branch(monkeypatch: Any) -> None:
    """`/mcp` is an explicit non-goal for sessions (ADR 0032 decision 1): a session
    is a browser credential, a PAT is the headless/MCP one.
    """
    from backend.app.services import session_service

    verifier = _composite_verifier()
    seen: list[str] = []

    async def _fake_jwt_verify(token: str) -> None:
        seen.append(token)
        return None

    monkeypatch.setattr(verifier._jwt, "verify_token", _fake_jwt_verify)
    assert await verifier.verify_token(session_service.TOKEN_PREFIX + "abcdef123456") is None
    assert seen == [], "a session token was handed to the JWT validator — it will be logged"


def test_the_mcp_layer_never_reads_a_cookie() -> None:
    """The premise the `allowed_origins=["*"]` justification rests on (#734)."""
    import inspect

    from backend.app.mcp import auth as mcp_auth

    source = inspect.getsource(mcp_auth)
    assert "cookies" not in source
    assert "COOKIE_NAME" not in source


def test_a_request_carrying_ONLY_the_session_cookie_is_rejected_over_http(
    monkeypatch: Any,
) -> None:
    """The end-to-end statement of the `allowed_origins=["*"]` premise (#734)."""
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.core.config import get_settings
    from backend.app.mcp import server as mcp_server
    from backend.app.services import session_service

    monkeypatch.setenv("AZURE_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("AZURE_API_CLIENT_ID", "22222222-2222-2222-2222-222222222222")
    get_settings.cache_clear()

    try:
        reloaded = importlib.reload(mcp_server)
        assert reloaded.mcp.auth is not None, (
            "the MCP server rebuilt without an auth provider — a 401 here would " "prove nothing"
        )
        mcp_app = reloaded.build_mcp_app()
        assert mcp_app is not None
        app = FastAPI(lifespan=mcp_app.lifespan)
        app.mount("/mcp", mcp_app)

        with TestClient(app) as client:
            client.cookies.set(session_service.COOKIE_NAME, session_service.TOKEN_PREFIX + "0" * 40)
            response = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
        assert response.status_code == 401, response.text
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        importlib.reload(mcp_server)


def _reload_server_in_otp_only_mode(monkeypatch: Any) -> Any:
    """Reload `mcp.server` with ONLY the OTP block configured, and return it."""
    from backend.app.core.config import get_settings

    for var in ("AZURE_TENANT_ID", "AZURE_API_CLIENT_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("AUTH_DEV_BYPASS", "false")
    monkeypatch.setenv("AUTH_EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AUTH_EMAIL_USERNAME", "dataq@example.com")
    monkeypatch.setenv("AUTH_EMAIL_FROM", "dataq@example.com")
    monkeypatch.setenv("AUTH_EMAIL_PASSWORD_SECRET_NAME", "auth-email-password")
    monkeypatch.setenv("AUTH_OTP_ALLOWED_DOMAINS", "acme.io")
    get_settings.cache_clear()

    import importlib

    from backend.app.mcp import server as mcp_server

    return importlib.reload(mcp_server)


def _restore_server(monkeypatch: Any) -> None:
    import importlib

    from backend.app.core.config import get_settings
    from backend.app.mcp import server as mcp_server

    monkeypatch.undo()
    get_settings.cache_clear()
    importlib.reload(mcp_server)


def test_otp_only_deployment_MOUNTS_mcp_and_still_serves_the_WHOLE_tool_surface(
    monkeypatch: Any,
) -> None:
    """The headline of #1128: before this, an OTP deployment had no `/mcp` at all."""
    import asyncio

    from backend.app.mcp.server import mcp as default_mode_mcp

    expected = {t.name for t in asyncio.run(default_mode_mcp.list_tools(run_middleware=False))}
    try:
        reloaded = _reload_server_in_otp_only_mode(monkeypatch)
        assert reloaded.build_mcp_app() is not None, "/mcp is still unmounted in otp-only mode"
        tools = {t.name for t in asyncio.run(reloaded.mcp.list_tools(run_middleware=False))}
        assert tools == expected
        assert tools, "the registry is empty — the comparison above would pass vacuously"
    finally:
        _restore_server(monkeypatch)


def test_otp_only_mcp_accepts_a_PAT_and_rejects_every_other_credential_over_http(
    db_session: Any, monkeypatch: Any
) -> None:
    """The credential matrix for the new mode, end to end over real HTTP."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.services import session_service

    _, pat = _pat_owner(db_session)
    _use_test_session(monkeypatch, db_session)
    session_token = session_service.TOKEN_PREFIX + "0" * 40

    try:
        reloaded = _reload_server_in_otp_only_mode(monkeypatch)
        assert isinstance(reloaded.mcp.auth, auth._PatOrJwtVerifier)
        assert reloaded.mcp.auth._jwt is None, "an OTP deployment has no JWT verifier"
        mcp_app = reloaded.build_mcp_app()
        assert mcp_app is not None
        app = FastAPI(lifespan=mcp_app.lifespan)
        app.mount("/mcp", mcp_app)

        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        accept = {"Accept": "application/json, text/event-stream"}
        with TestClient(app) as client:
            with_pat = client.post(
                "/mcp/", json=body, headers={**accept, "Authorization": f"Bearer {pat}"}
            )
            with_session_bearer = client.post(
                "/mcp/", json=body, headers={**accept, "Authorization": f"Bearer {session_token}"}
            )
            with_jwt = client.post(
                "/mcp/",
                json=body,
                headers={**accept, "Authorization": "Bearer eyJhbGciOi.some.jwt"},
            )
            client.cookies.set(session_service.COOKIE_NAME, session_token)
            with_cookie = client.post("/mcp/", json=body, headers=accept)

        assert with_pat.status_code != 401, with_pat.text
        # …and it got past auth into the PROTOCOL: the body is a JSON-RPC error from the transport
        # (no MCP session handshake in this bare POST), not an auth challenge.
        assert "jsonrpc" in with_pat.json(), with_pat.text
        assert with_session_bearer.status_code == 401, with_session_bearer.text
        assert with_jwt.status_code == 401, with_jwt.text
        assert with_cookie.status_code == 401, with_cookie.text
    finally:
        _restore_server(monkeypatch)


def test_an_otp_signed_in_user_can_mint_the_pat_that_mcp_then_accepts(
    db_session: Any, monkeypatch: Any
) -> None:
    """The whole #1128 journey, joined up: sign in with a code → mint → call /mcp."""
    import asyncio
    import uuid

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.app.db.session as db_session_mod
    from backend.app.api.v1 import api_keys as api_keys_api
    from backend.app.core.auth import _get_current_user_otp, get_current_user
    from backend.app.core.errors import register_exception_handlers
    from backend.app.db.models import User
    from backend.app.services import session_service

    user = User(id=uuid.uuid4(), aad_object_id=None, email=f"otp-{uuid.uuid4().hex[:8]}@acme.io")
    db_session.add(user)
    db_session.commit()
    _, cookie = session_service.create_session(db_session, user)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(api_keys_api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = _get_current_user_otp
    app.dependency_overrides[db_session_mod.get_db] = lambda: db_session

    with TestClient(app) as client:
        client.cookies.set(session_service.COOKIE_NAME, cookie)
        minted = client.post("/api/v1/me/api-keys", json={"name": "mcp"})
        assert minted.status_code == 201, minted.text
        listed = client.get("/api/v1/me/api-keys")
    assert listed.status_code == 200, listed.text

    token = minted.json()["token"]
    _use_test_session(monkeypatch, db_session)
    access = asyncio.run(_pat_only_verifier().verify_token(token))
    assert access is not None, "the PAT an OTP user just minted is rejected by /mcp"
    assert access.claims[auth.PAT_USER_CLAIM] == str(user.id)


# ── generic_oidc userinfo fallback (#1346) ───────────────────────────────────


def test_resolve_user_generic_oidc_fetches_userinfo_when_email_absent(
    db_session: Any, monkeypatch: Any
) -> None:
    """A real Cognito access token has no email claim — it comes from userinfo,
    fetched with the SAME raw bearer the client presented.
    """
    token = SimpleNamespace(claims={"sub": "cognito-sub-3", "token_use": "access"}, token="raw-jwt")
    seen: list[tuple[str, str]] = []

    def fake_fetch(issuer: str, tok: str) -> dict[str, Any]:
        seen.append((issuer, tok))
        return {"sub": "cognito-sub-3", "email": "mcp-user@example.com", "name": "MCP User"}

    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(auth, "fetch_userinfo", fake_fetch)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(**_OIDC, environment="prod"))
    user = auth.resolve_current_user(db_session)
    assert user.email == "mcp-user@example.com"
    assert user.display_name == "MCP User"
    assert seen == [("https://example-idp.test", "raw-jwt")]


def test_resolve_user_generic_oidc_userinfo_outage_raises(
    db_session: Any, monkeypatch: Any
) -> None:
    """Outage → a visible auth error, never an empty-email upsert."""
    import httpx

    token = SimpleNamespace(claims={"sub": "cognito-sub-4"}, token="raw-jwt")

    def fake_fetch(issuer: str, tok: str) -> dict[str, Any]:
        raise httpx.ConnectError("userinfo down")

    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(auth, "fetch_userinfo", fake_fetch)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(**_OIDC, environment="prod"))
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)


def test_resolve_user_generic_oidc_userinfo_sub_mismatch_raises(
    db_session: Any, monkeypatch: Any
) -> None:
    token = SimpleNamespace(claims={"sub": "cognito-sub-5"}, token="raw-jwt")
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(
        auth, "fetch_userinfo", lambda issuer, tok: {"sub": "someone-else", "email": "x@y.z"}
    )
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(**_OIDC, environment="prod"))
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)


# ── OIDC access allowlist on /mcp (#1386) ──────────────────────────────────── Caught in review:
# `_resolve_generic_oidc_user` unified the two REST dependencies.


def test_mcp_generic_oidc_denies_an_address_off_the_allowlist(
    db_session: Any, monkeypatch: Any
) -> None:
    token = SimpleNamespace(claims={"sub": "stranger-sub", "email": "stranger@evil.test"})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: _settings(**_OIDC, environment="prod", oidc_allowed_emails="invited@example.com"),
    )
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)


def test_mcp_generic_oidc_denial_provisions_no_user_row(db_session: Any, monkeypatch: Any) -> None:
    """The whole point: /mcp must not be the path that creates the account the
    REST API refuses to create.
    """
    token = SimpleNamespace(claims={"sub": "mcp-no-row-sub", "email": "nobody@evil.test"})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: _settings(**_OIDC, environment="prod", oidc_allowed_domains="example.com"),
    )
    with pytest.raises(auth.McpAuthError):
        auth.resolve_current_user(db_session)
    assert (
        db_session.query(User).filter(User.aad_object_id == "mcp-no-row-sub").one_or_none() is None
    )


def test_mcp_generic_oidc_admits_a_listed_address(db_session: Any, monkeypatch: Any) -> None:
    token = SimpleNamespace(claims={"sub": "mcp-invited-sub", "email": "invited@example.com"})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: _settings(**_OIDC, environment="prod", oidc_allowed_emails="invited@example.com"),
    )
    user = auth.resolve_current_user(db_session)
    assert user.email == "invited@example.com"


def test_mcp_generic_oidc_ungated_when_no_allowlist_is_set(
    db_session: Any, monkeypatch: Any
) -> None:
    """Same documented default as the REST path — pinned on both surfaces so they
    cannot drift apart in either direction.
    """
    token = SimpleNamespace(claims={"sub": "mcp-ungated-sub", "email": "anyone@anywhere.test"})
    monkeypatch.setattr(auth, "get_access_token", lambda: token)
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(**_OIDC, environment="prod"))
    user = auth.resolve_current_user(db_session)
    assert user.email == "anyone@anywhere.test"


def test_mcp_generic_oidc_gate_matches_the_rest_gate_for_the_same_inputs(
    db_session: Any, monkeypatch: Any
) -> None:
    """The invariant itself, asserted directly rather than inferred from the two
    suites agreeing by coincidence: for one settings object and one address, /mcp
    and /api must reach the same verdict.
    """
    settings = _settings(**_OIDC, environment="prod", oidc_allowed_domains="example.com")
    for email, expected in [("ok@example.com", True), ("no@evil.test", False)]:
        assert core_auth._oidc_access_allowed(email, settings) is expected
