"""Tests for the Azure auth scheme builder (offline — no token validation)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi_azure_auth.user import User as AzureUser
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
