"""The 401 body names only the credentials the deployment accepts (#1736)."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

import backend.app.core.auth as auth_mod
from backend.app.core.config import Settings
from backend.app.core.errors import DataQError
from backend.app.services import api_key_service

_AZURE: dict[str, Any] = {
    "azure_tenant_id": "11111111-1111-1111-1111-111111111111",
    "azure_api_client_id": "22222222-2222-2222-2222-222222222222",
}
_NO_AZURE: dict[str, Any] = {"azure_tenant_id": None, "azure_api_client_id": None}
_GENERIC_OIDC: dict[str, Any] = {
    **_NO_AZURE,
    "oidc_issuer": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_example",
    "oidc_audience": "spa-client-id",
}
_OTP: dict[str, Any] = {
    "auth_email_smtp_host": "smtp.example.com",
    "auth_email_username": "dataq@example.com",
    "auth_email_from": "dataq@example.com",
    "auth_email_password_secret_name": "auth-email-password",
    "auth_otp_allowed_domains": "acme.io",
}
_PAT = f"a DataQ API key ({api_key_service.TOKEN_PREFIX}…)"


@pytest.mark.parametrize(
    ("mode", "settings_kwargs", "expected"),
    [
        (
            "azure_ad",
            _AZURE,
            f"Not authenticated: a valid Azure AD sign-in token or {_PAT} is required.",
        ),
        (
            "azure_ad+otp",
            {**_AZURE, **_OTP},
            "Not authenticated: a valid Azure AD sign-in token, "
            f"a signed-in session (email code), or {_PAT} is required.",
        ),
        (
            "generic_oidc",
            _GENERIC_OIDC,
            "Not authenticated: a valid sign-in token from your identity provider "
            f"or {_PAT} is required.",
        ),
        (
            "generic_oidc+otp",
            {**_GENERIC_OIDC, **_OTP},
            "Not authenticated: a valid sign-in token from your identity provider, "
            f"a signed-in session (email code), or {_PAT} is required.",
        ),
        (
            "otp_only",
            {**_NO_AZURE, **_OTP},
            f"Not authenticated: a signed-in session (email code) or {_PAT} is required.",
        ),
        ("dev_bypass_or_unconfigured", _NO_AZURE, f"Not authenticated: {_PAT} is required."),
    ],
)
def test_message_names_only_the_credentials_this_mode_accepts(
    mode: str, settings_kwargs: dict[str, Any], expected: str
) -> None:
    message = auth_mod.unauthenticated_message(Settings(**settings_kwargs))
    assert message == expected
    # The defect: a Cognito/OTP stack telling the caller it needs an Azure AD token.
    assert ("Azure" in message) is mode.startswith("azure_ad")
    assert api_key_service.TOKEN_PREFIX in message


def test_generic_oidc_message_leaks_no_issuer_detail() -> None:
    message = auth_mod.unauthenticated_message(Settings(**_GENERIC_OIDC))
    assert _GENERIC_OIDC["oidc_issuer"] not in message
    assert _GENERIC_OIDC["oidc_audience"] not in message
    assert "cognito" not in message.lower()


def test_module_constant_is_derived_from_the_bound_settings() -> None:
    assert auth_mod._UNAUTHENTICATED_MESSAGE == auth_mod.unauthenticated_message(auth_mod._settings)


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.parametrize(
    ("dependency", "extra_args"),
    [
        (auth_mod._get_current_user_real, (None,)),
        (auth_mod._get_current_user_real_or_otp, (None,)),
        (auth_mod._get_current_user_generic_oidc, (None,)),
        (auth_mod._get_current_user_generic_oidc_or_otp, (None,)),
        (auth_mod._get_current_user_otp, ()),
    ],
    ids=["azure_ad", "azure_ad+otp", "generic_oidc", "generic_oidc+otp", "otp_only"],
)
def test_every_mode_dependency_raises_the_one_derived_message(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Any,
    dependency: Any,
    extra_args: tuple[Any, ...],
) -> None:
    """Wiring: all five per-mode dependencies read the single bound constant, so no
    mode can carry a stale hand-written variant of the message.
    """
    sentinel = "sentinel-1736"
    monkeypatch.setattr(auth_mod, "_UNAUTHENTICATED_MESSAGE", sentinel)
    with pytest.raises(DataQError) as caught:
        dependency(_request(), *extra_args, db_session)
    assert caught.value.status_code == 401
    assert caught.value.code == "unauthenticated"
    assert caught.value.message == sentinel
