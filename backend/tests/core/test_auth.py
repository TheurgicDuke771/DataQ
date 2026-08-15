"""Tests for the Azure auth scheme builder (offline — no token validation)."""

from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace
from typing import Any, cast

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi_azure_auth.user import User as AzureUser
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError
from starlette.requests import Request

import backend.app.core.auth as auth_mod
from backend.app.core.config import Settings
from backend.app.core.errors import DataQError
from backend.app.db.models import User
from backend.app.services import api_key_service, user_service


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


def _request(authorization: str | None = None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_get_current_user_real_upserts_from_claims(db_session: Any) -> None:
    user = auth_mod._get_current_user_real(
        _request(),
        _azure_user({"oid": "11111111-2222-3333-4444-555555555555", "upn": "real@example.com"}),
        db_session,
    )
    assert user.email == "real@example.com"
    assert user.aad_object_id == "11111111-2222-3333-4444-555555555555"


def test_upsert_seeds_display_name_on_first_login(db_session: Any) -> None:
    """A brand-new row has no override yet, so the AAD claim seeds a real name
    instead of leaving a bare email to render in shares/admin lists."""
    claims = {
        "oid": "44444444-5555-6666-7777-888888888888",
        "upn": "named@example.com",
        "name": "AAD Claim Name",
    }
    first = auth_mod._get_current_user_real(_request(), _azure_user(claims), db_session)
    assert first.display_name == "AAD Claim Name"
    assert first.display_name_override is False


def test_upsert_preserves_a_patch_me_override_across_relogin(db_session: Any) -> None:
    """#1139: `_upsert_user` runs on EVERY real-mode request (no session cache —
    the JWT is re-validated and re-claimed each time), so a `PATCH /me` override
    must survive the user's very next request rather than being silently
    re-synced back to the AAD token's `name` claim.

    The override is set through the real service call (`user_service.
    update_display_name`, what the PATCH handler calls) — not by hand-setting
    the column — so this pins the actual mechanism (`display_name_override`,
    migration 6230293aea96), not an incidental side effect of some other
    field's nullability.
    """
    claims = {
        "oid": "55555555-6666-7777-8888-999999999999",
        "upn": "overrider@example.com",
        "name": "AAD Claim Name",
    }
    first = auth_mod._get_current_user_real(_request(), _azure_user(claims), db_session)
    user_service.update_display_name(db_session, first, "Self-Service Override")
    assert first.display_name_override is True

    second = auth_mod._get_current_user_real(_request(), _azure_user(claims), db_session)
    # Same claim as before, re-presented on a second "request" — the override
    # must stick, not merely tolerate an unchanged claim.
    assert second.display_name == "Self-Service Override"
    assert second.display_name_override is True


def test_upsert_syncs_an_aad_rename_when_there_is_no_override(db_session: Any) -> None:
    """The other direction of the same invariant — the one a bare COALESCE
    could never satisfy (#1139 review): nobody has ever explicitly set this
    person's name, so a legitimate rename in the directory (a new `name` claim
    on a later login) must still land."""
    claims = {
        "oid": "66666666-7777-8888-9999-aaaaaaaaaaaa",
        "upn": "renamed@example.com",
        "name": "Old Claim Name",
    }
    first = auth_mod._get_current_user_real(_request(), _azure_user(claims), db_session)
    assert first.display_name == "Old Claim Name"
    assert first.display_name_override is False

    renamed_claims = {**claims, "name": "New Claim Name"}
    second = auth_mod._get_current_user_real(_request(), _azure_user(renamed_claims), db_session)
    assert second.display_name == "New Claim Name"
    assert second.display_name_override is False


# ── PAT branch on the seam (ADR 0026, #461) ──────────────────────────────────


def _user_with_pat(db_session: Any) -> tuple[User, str]:
    user = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="pat@seam.io")
    db_session.add(user)
    db_session.commit()
    _, token = api_key_service.create_key(db_session, user, name="seam")
    return user, token


def test_bearer_and_pat_token_parsing() -> None:
    assert auth_mod._bearer_token(_request()) is None
    assert auth_mod._bearer_token(_request("Basic dXNlcg==")) is None
    assert auth_mod._bearer_token(_request("Bearer  ")) is None
    assert auth_mod._bearer_token(_request("Bearer abc")) == "abc"
    # Only the dq_live_ prefix is a PAT; a JWT-ish bearer is not.
    assert auth_mod._pat_token(_request("Bearer eyJhbGciOi.xxx.yyy")) is None
    pat = api_key_service.TOKEN_PREFIX + "abc"
    assert auth_mod._pat_token(_request(f"Bearer {pat}")) == pat


def test_get_current_user_real_pat_resolves_without_azure(db_session: Any) -> None:
    """A valid PAT authenticates on its own — azure_user None (no JWT at all)."""
    user, token = _user_with_pat(db_session)
    resolved = auth_mod._get_current_user_real(_request(f"Bearer {token}"), None, db_session)
    assert resolved.id == user.id


def test_get_current_user_real_401_without_any_credential(db_session: Any) -> None:
    with pytest.raises(DataQError) as excinfo:
        auth_mod._get_current_user_real(_request(), None, db_session)
    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "unauthenticated"


def test_get_current_user_real_bad_pat_never_falls_through_to_azure(db_session: Any) -> None:
    """A dq_live_ bearer is decided by the PAT branch alone — even alongside a
    (hypothetically) valid Azure identity, a bad PAT is a uniform 401."""
    azure_user = _azure_user({"oid": "33333333-4444-5555-6666-777777777777", "upn": "a@b.io"})
    with pytest.raises(DataQError) as excinfo:
        auth_mod._get_current_user_real(
            _request(f"Bearer {api_key_service.TOKEN_PREFIX}bogus"), azure_user, db_session
        )
    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "invalid_api_key"


def test_get_current_user_dev_bypass_pat_first_and_fail_closed(db_session: Any) -> None:
    user, token = _user_with_pat(db_session)
    # PAT wins over the bypass identity.
    resolved = auth_mod._get_current_user_dev_bypass(_request(f"Bearer {token}"), db_session)
    assert resolved.id == user.id
    # A bad PAT 401s — it must not fall through to the bypass user.
    with pytest.raises(DataQError) as excinfo:
        auth_mod._get_current_user_dev_bypass(
            _request(f"Bearer {api_key_service.TOKEN_PREFIX}bogus"), db_session
        )
    assert excinfo.value.status_code == 401


def test_get_current_user_unconfigured_raises_503() -> None:
    with pytest.raises(DataQError) as excinfo:
        auth_mod._get_current_user_unconfigured()
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "auth_not_configured"


# ── Generic OIDC (ADR 0026 amendment) ────────────────────────────────────────


def _oidc_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "oidc_issuer": "https://example-idp.test",
        "oidc_audience": "dataq-client-id",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_generic_oidc_configured_requires_both_fields() -> None:
    assert Settings(oidc_issuer=None, oidc_audience=None).generic_oidc_configured is False
    assert _oidc_settings().generic_oidc_configured is True


def test_settings_rejects_oidc_issuer_without_audience() -> None:
    with pytest.raises(ValidationError, match="OIDC_AUDIENCE"):
        Settings(oidc_issuer="https://example-idp.test", oidc_audience=None)


def test_settings_rejects_oidc_audience_without_issuer() -> None:
    with pytest.raises(ValidationError, match="OIDC_ISSUER"):
        Settings(oidc_issuer=None, oidc_audience="dataq-client-id")


def test_settings_rejects_oidc_issuer_without_a_scheme() -> None:
    with pytest.raises(ValidationError, match="must start with http"):
        Settings(oidc_issuer="example-idp.test", oidc_audience="dataq-client-id")


def test_settings_rejects_oidc_and_azure_together() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        Settings(
            oidc_issuer="https://example-idp.test",
            oidc_audience="dataq-client-id",
            azure_tenant_id="11111111-1111-1111-1111-111111111111",
            azure_api_client_id="22222222-2222-2222-2222-222222222222",
        )


def test_build_oidc_scheme_is_none_when_unconfigured() -> None:
    assert auth_mod._build_oidc_scheme(Settings(oidc_issuer=None, oidc_audience=None)) is None


def test_build_oidc_scheme_configured() -> None:
    scheme = auth_mod._build_oidc_scheme(_oidc_settings())
    assert scheme is not None
    assert scheme._issuer == "https://example-idp.test"
    assert scheme._audience == "dataq-client-id"


def test_discover_jwks_uri_reads_the_oidc_discovery_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared by both `OidcBearerScheme.load_config` (async, via `asyncio.to_thread`)
    and `mcp.auth.build_auth_provider` (genuinely sync, import-time) — one
    implementation, tested once, here."""

    def fake_get(url: str, timeout: float) -> httpx.Response:
        assert url == "https://example-idp.test/.well-known/openid-configuration"
        return httpx.Response(
            200,
            json={"jwks_uri": "https://example-idp.test/jwks.json"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    assert (
        auth_mod.discover_jwks_uri("https://example-idp.test")
        == "https://example-idp.test/jwks.json"
    )


def test_extract_oidc_claims_reads_sub_email_name() -> None:
    subject, email, name = auth_mod._extract_oidc_claims(
        {"sub": "user-123", "email": "olivia@example.com", "name": "Olivia"}
    )
    assert (subject, email, name) == ("user-123", "olivia@example.com", "Olivia")


def test_extract_oidc_claims_requires_sub() -> None:
    with pytest.raises(KeyError):
        auth_mod._extract_oidc_claims({"email": "olivia@example.com"})


def test_extract_oidc_claims_defaults_email_and_name_when_absent() -> None:
    subject, email, name = auth_mod._extract_oidc_claims({"sub": "user-123"})
    assert (subject, email, name) == ("user-123", "", None)


def _oidc_claims(claims: dict[str, Any]) -> dict[str, Any]:
    return claims


def test_get_current_user_generic_oidc_upserts_from_claims(db_session: Any) -> None:
    user = auth_mod._get_current_user_generic_oidc(
        _request(),
        _oidc_claims({"sub": "cognito-sub-1", "email": "cognito-user@example.com"}),
        db_session,
    )
    assert user.email == "cognito-user@example.com"
    assert user.aad_object_id == "cognito-sub-1"


def test_get_current_user_generic_oidc_writes_the_configured_issuer(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_mod, "_settings", _oidc_settings())
    user = auth_mod._get_current_user_generic_oidc(
        _request(),
        _oidc_claims({"sub": "cognito-sub-2", "email": "issuer-check@example.com"}),
        db_session,
    )
    assert user.oidc_issuer == "https://example-idp.test"


def test_get_current_user_generic_oidc_pat_resolves_without_a_token(db_session: Any) -> None:
    user, token = _user_with_pat(db_session)
    resolved = auth_mod._get_current_user_generic_oidc(
        _request(f"Bearer {token}"), None, db_session
    )
    assert resolved.id == user.id


def test_get_current_user_generic_oidc_401_without_any_credential(db_session: Any) -> None:
    with pytest.raises(DataQError) as excinfo:
        auth_mod._get_current_user_generic_oidc(_request(), None, db_session)
    assert excinfo.value.status_code == 401


def test_azure_login_writes_the_deterministic_azure_issuer(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-healing (model docstring): every real-mode login — Azure included —
    now writes `oidc_issuer`, so pre-existing rows pick up an accurate value on
    their next sign-in instead of needing a backfill."""
    monkeypatch.setattr(auth_mod, "_AZURE_ISSUER", "https://login.microsoftonline.com/tenant/v2.0")
    user = auth_mod._get_current_user_real(
        _request(),
        _azure_user({"oid": "azure-self-heal-oid", "upn": "azure-heal@example.com"}),
        db_session,
    )
    assert user.oidc_issuer == "https://login.microsoftonline.com/tenant/v2.0"


async def test_init_auth_generic_oidc_mode_loads_config(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[bool] = []

    class _FakeOidcScheme:
        async def load_config(self) -> None:
            loaded.append(True)

    monkeypatch.setattr(auth_mod, "azure_scheme", None)
    monkeypatch.setattr(auth_mod, "oidc_scheme", _FakeOidcScheme())
    await auth_mod.init_auth()
    assert loaded == [True]


# ── OidcBearerScheme: real crypto path (closes the 0%-covered branch) ────────


def _rsa_keypair() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """A throwaway RSA key + its public half as a JWKS entry, `kid="test-key"`."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(private_key.public_key()))
    public_jwk["kid"] = "test-key"
    public_jwk["use"] = "sig"
    return private_key, public_jwk


def _sign(private_key: rsa.RSAPrivateKey, claims: dict[str, Any]) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def _mock_oidc_transport(jwks: dict[str, Any], issuer: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks.json":
            return httpx.Response(200, json=jwks)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _loaded_scheme(
    monkeypatch: pytest.MonkeyPatch, issuer: str, jwks: dict[str, Any]
) -> auth_mod.OidcBearerScheme:
    """An `OidcBearerScheme` with `load_config()` already run against mocked HTTP.

    Discovery (`discover_jwks_uri`, shared with `mcp.auth`) is a synchronous,
    module-level `httpx.get` call — patched here — separately from the JWKS
    fetch itself, which goes through the scheme's own async `_client`.
    """
    scheme = auth_mod.OidcBearerScheme(issuer=issuer, audience="dataq-client-id")
    # `httpx` here is the SAME module object `core.auth` imported (module identity,
    # not a copy), so patching `.get` on it is visible to `discover_jwks_uri`
    # inside `core.auth` too.
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, timeout: httpx.Response(
            200, json={"jwks_uri": f"{issuer}/jwks.json"}, request=httpx.Request("GET", url)
        ),
    )
    monkeypatch.setattr(
        scheme, "_client", httpx.AsyncClient(transport=_mock_oidc_transport(jwks, issuer))
    )
    await scheme.load_config()
    return scheme


async def test_oidc_scheme_verifies_a_real_signed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer = "https://example-idp.test"
    private_key, public_jwk = _rsa_keypair()
    scheme = await _loaded_scheme(monkeypatch, issuer, {"keys": [public_jwk]})

    token = _sign(
        private_key,
        {
            "sub": "user-1",
            "email": "verified@example.com",
            "iss": issuer,
            "aud": "dataq-client-id",
            "exp": int(time.time()) + 3600,
        },
    )
    claims = await scheme._verify(token)
    assert claims is not None
    assert claims["sub"] == "user-1"


async def test_oidc_scheme_rejects_a_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer = "https://example-idp.test"
    private_key, public_jwk = _rsa_keypair()
    scheme = await _loaded_scheme(monkeypatch, issuer, {"keys": [public_jwk]})

    token = _sign(
        private_key,
        {
            "sub": "user-1",
            "iss": issuer,
            "aud": "someone-elses-client-id",
            "exp": int(time.time()) + 3600,
        },
    )
    assert await scheme._verify(token) is None


async def test_oidc_scheme_accepts_cognito_style_client_id_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AWS Cognito access tokens carry `client_id`, not the standard `aud` — the
    one deliberately provider-aware accommodation (class docstring)."""
    issuer = "https://example-idp.test"
    private_key, public_jwk = _rsa_keypair()
    scheme = await _loaded_scheme(monkeypatch, issuer, {"keys": [public_jwk]})

    token = _sign(
        private_key,
        {
            "sub": "user-1",
            "iss": issuer,
            "client_id": "dataq-client-id",
            "exp": int(time.time()) + 3600,
        },
    )
    claims = await scheme._verify(token)
    assert claims is not None
    assert claims["sub"] == "user-1"


async def test_oidc_scheme_rejects_a_token_signed_by_the_wrong_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual signature-verification path, not just claim shape — a token
    signed by a DIFFERENT key than the one published in the JWKS must fail."""
    issuer = "https://example-idp.test"
    _legit_key, public_jwk = _rsa_keypair()
    forged_key, _forged_jwk = _rsa_keypair()
    scheme = await _loaded_scheme(monkeypatch, issuer, {"keys": [public_jwk]})

    forged_token = _sign(
        forged_key,
        {
            "sub": "attacker",
            "iss": issuer,
            "aud": "dataq-client-id",
            "exp": int(time.time()) + 3600,
        },
    )
    assert await scheme._verify(forged_token) is None


async def test_oidc_scheme_rejects_an_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer = "https://example-idp.test"
    private_key, public_jwk = _rsa_keypair()
    scheme = await _loaded_scheme(monkeypatch, issuer, {"keys": [public_jwk]})

    expired = _sign(
        private_key,
        {
            "sub": "user-1",
            "iss": issuer,
            "aud": "dataq-client-id",
            "exp": int(time.time()) - 3600,
        },
    )
    assert await scheme._verify(expired) is None


async def test_oidc_scheme_refreshes_jwks_once_on_an_unknown_kid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that rotated its signing keys since the last fetch: one
    refresh-and-retry, mirroring OpenBao's single-retry discipline."""
    issuer = "https://example-idp.test"
    private_key, public_jwk = _rsa_keypair()
    scheme = auth_mod.OidcBearerScheme(issuer=issuer, audience="dataq-client-id")
    # Start with an EMPTY JWKS cached (simulating a fetch that predates rotation).
    monkeypatch.setattr(
        scheme,
        "_client",
        httpx.AsyncClient(transport=_mock_oidc_transport({"keys": [public_jwk]}, issuer)),
    )
    scheme._jwks_uri = f"{issuer}/jwks.json"
    scheme._jwks_cache = {"keys": []}

    token = _sign(
        private_key,
        {
            "sub": "user-1",
            "iss": issuer,
            "aud": "dataq-client-id",
            "exp": int(time.time()) + 3600,
        },
    )
    claims = await scheme._verify(token)
    assert claims is not None
    assert scheme._jwks_cache == {"keys": [public_jwk]}


async def test_oidc_scheme_returns_none_on_a_malformed_token() -> None:
    scheme = auth_mod.OidcBearerScheme(issuer="https://example-idp.test", audience="x")
    assert await scheme._verify("not-a-jwt") is None


async def test_oidc_scheme_short_circuits_for_a_pat(db_session: Any) -> None:
    scheme = auth_mod.OidcBearerScheme(issuer="https://example-idp.test", audience="x")
    _user, token = _user_with_pat(db_session)
    assert await scheme(_request(f"Bearer {token}")) is None


async def test_oidc_scheme_short_circuits_for_an_otp_session_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_mod, "_otp_enabled", True)
    scheme = auth_mod.OidcBearerScheme(issuer="https://example-idp.test", audience="x")
    from backend.app.services import session_service

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", f"{session_service.COOKIE_NAME}=dq_sess_x".encode())],
        }
    )
    assert await scheme(request) is None


async def test_oidc_scheme_call_delegates_a_bearer_to_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer = "https://example-idp.test"
    private_key, public_jwk = _rsa_keypair()
    scheme = await _loaded_scheme(monkeypatch, issuer, {"keys": [public_jwk]})
    token = _sign(
        private_key,
        {"sub": "u1", "iss": issuer, "aud": "dataq-client-id", "exp": int(time.time()) + 3600},
    )
    claims = await scheme(_request(f"Bearer {token}"))
    assert claims is not None
    assert claims["sub"] == "u1"


async def test_oidc_scheme_returns_none_when_no_bearer_present() -> None:
    scheme = auth_mod.OidcBearerScheme(issuer="https://example-idp.test", audience="x")
    assert await scheme(_request()) is None


async def test_oidc_scheme_rejects_a_token_with_no_kid_header() -> None:
    scheme = auth_mod.OidcBearerScheme(issuer="https://example-idp.test", audience="x")
    private_key, _ = _rsa_keypair()
    token = jwt.encode(
        {"sub": "u1", "exp": int(time.time()) + 3600}, private_key, algorithm="RS256"
    )
    assert await scheme._verify(token) is None


async def test_oidc_scheme_returns_none_when_the_refreshed_jwks_still_lacks_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `kid` that genuinely doesn't exist anywhere — not a rotation, a bad
    token — must fail after the one refresh-and-retry, not loop or raise."""
    issuer = "https://example-idp.test"
    _private_key, public_jwk = _rsa_keypair()
    other_key, _other_jwk = _rsa_keypair()
    scheme = await _loaded_scheme(monkeypatch, issuer, {"keys": [public_jwk]})

    unknown_kid_token = jwt.encode(
        {"sub": "u1", "iss": issuer, "aud": "dataq-client-id", "exp": int(time.time()) + 3600},
        other_key,
        algorithm="RS256",
        headers={"kid": "never-published"},
    )
    assert await scheme._verify(unknown_kid_token) is None


async def test_oidc_scheme_verify_returns_none_when_jwks_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network failure mid-refresh must degrade to the standard 401, not an
    unhandled exception out of a FastAPI dependency."""
    issuer = "https://example-idp.test"
    scheme = auth_mod.OidcBearerScheme(issuer=issuer, audience="dataq-client-id")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        scheme, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    scheme._jwks_uri = f"{issuer}/jwks.json"
    scheme._jwks_cache = None

    private_key, _ = _rsa_keypair()
    token = jwt.encode(
        {"sub": "u1", "exp": int(time.time()) + 3600},
        private_key,
        algorithm="RS256",
        headers={"kid": "whatever"},
    )
    assert await scheme._verify(token) is None
