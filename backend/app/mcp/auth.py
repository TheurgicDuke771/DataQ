"""Auth for the MCP server — the *same* credentials the REST API accepts."""

from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi_azure_auth.utils import is_guest
from fastmcp.server.auth import AccessToken, AuthProvider, TokenVerifier
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from sqlalchemy.orm import Session

from backend.app.core.auth import (
    _AZURE_ISSUER,
    DEV_BYPASS_AAD_OID,
    DEV_BYPASS_DISPLAY_NAME,
    DEV_BYPASS_EMAIL,
    _denied_identity,
    _dev_bypass_allowed,
    _merge_userinfo,
    _oidc_access_allowed,
    _oidc_allowlist_grants,
    _upsert_user,
    discover_jwks_uri,
    fetch_userinfo,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import User
from backend.app.services import api_key_service, membership_service, session_service
from backend.app.services.otp_service import normalize_email

log = get_logger(__name__)

# Carries the PAT-resolved DataQ user id through fastmcp's token context
# (a DataQ-internal claim, not an OIDC one).
PAT_USER_CLAIM = "dataq_user_id"


class McpAuthError(Exception):
    """Raised inside a tool when the caller can't be resolved (defence-in-depth)."""


#: MCP auth modes in selection order — mirrors the ``core.auth`` ladder exactly;
#: shared credentials require shared mode selection.
McpAuthMode = Literal["azure_ad", "generic_oidc", "pat_only", "dev_bypass", "disabled"]


def mcp_auth_mode(settings: Settings | None = None) -> McpAuthMode:
    """Which authenticator ``/mcp`` runs on — the single ladder everything reads."""
    s = settings or get_settings()
    if s.azure_auth_configured:
        return "azure_ad"
    if s.generic_oidc_configured:
        return "generic_oidc"
    if s.otp_auth_configured:
        return "pat_only"
    if _dev_bypass_allowed(s):
        return "dev_bypass"
    return "disabled"


def mcp_enabled(settings: Settings | None = None) -> bool:
    """Whether ``/mcp`` should be mounted at all — never an unauthenticated
    mount in a deployed environment.
    """
    return mcp_auth_mode(settings) != "disabled"


class _PatOrJwtVerifier(TokenVerifier):
    """Composite verifier: DataQ PAT by prefix, else the JWT half."""

    def __init__(self, jwt_verifier: JWTVerifier | None) -> None:
        super().__init__()
        self._jwt = jwt_verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.startswith(session_service.TOKEN_PREFIX):
            return None
        if not token.startswith(api_key_service.TOKEN_PREFIX):
            if self._jwt is None:
                # pat_only (#1128): PATs are the only /mcp credential here.
                return None
            return await self._jwt.verify_token(token)
        from backend.app.db.session import SessionLocal

        session = SessionLocal()
        try:
            user = api_key_service.resolve_token(session, token)
        except DataQError:
            return None
        finally:
            session.close()
        return AccessToken(
            token=token,
            client_id="dataq-pat",
            scopes=[],
            expires_at=None,
            claims={PAT_USER_CLAIM: str(user.id)},
        )


def build_auth_provider(settings: Settings | None = None) -> AuthProvider | None:
    """The fastmcp auth provider for the current ``mcp_auth_mode``."""
    s = settings or get_settings()
    mode = mcp_auth_mode(s)
    if mode == "dev_bypass":
        return None
    if mode == "azure_ad":
        tenant = s.azure_tenant_id
        # Single-tenant v2 endpoint — same coordinates fastapi-azure-auth uses.
        jwt = JWTVerifier(
            jwks_uri=f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
            audience=s.azure_api_client_id,
            required_scopes=[s.azure_api_scope],
        )
        return _PatOrJwtVerifier(jwt)
    if mode == "generic_oidc":
        assert s.oidc_issuer is not None
        jwt = JWTVerifier(
            jwks_uri=discover_jwks_uri(s.oidc_issuer),
            issuer=s.oidc_issuer,
            audience=s.oidc_audience,
        )
        return _PatOrJwtVerifier(jwt)
    return _PatOrJwtVerifier(None)


def resolve_current_user(session: Session) -> User:
    """Resolve and upsert the calling user — the MCP twin of ``get_current_user``.
    Reuses ``core.auth`` so claim handling cannot drift from the REST path.
    """
    token = get_access_token()
    if token is not None:
        claims: dict[str, Any] = token.claims or {}
        # PAT path: the verifier already resolved (and last-used-stamped) the
        # owning user; load by id — no upsert.
        pat_user_id = claims.get(PAT_USER_CLAIM)
        if pat_user_id:
            user = session.get(User, pat_user_id)
            if user is None:  # revoked/deleted between verify and tool call
                raise McpAuthError("could not resolve the API key's user")
            return user
        settings = get_settings()
        mode = mcp_auth_mode(settings)
        if mode == "generic_oidc":
            # `sub` (RFC 7519 REQUIRED), not Azure's `oid`. No guest policy —
            # that concept is Azure B2B-specific.
            subject = claims.get("sub")
            if subject:
                # #1346: Cognito access tokens carry no email/name — resolve via userinfo (shared
                # cache in core.auth).
                if not claims.get("email") and settings.oidc_issuer:
                    try:
                        userinfo = fetch_userinfo(settings.oidc_issuer, token.token)
                    except (httpx.HTTPError, ValueError) as exc:
                        # Non-object 200 → same fail-closed outcome as an outage.
                        log.warning(
                            "mcp_oidc_userinfo_unavailable",
                            issuer=settings.oidc_issuer,
                            error=str(exc),
                        )
                        raise McpAuthError(
                            "could not resolve identity claims from the OIDC provider"
                        ) from exc
                    if userinfo is not None:
                        merged = _merge_userinfo(claims, userinfo)
                        if merged is None:
                            raise McpAuthError("userinfo subject does not match the token")
                        claims = merged
                email = normalize_email(str(claims.get("email") or ""))
                # Same allowlist the REST resolver applies (#1386), and the same
                # grant-only membership union on top of it (ADR 0043).
                env_allowed = _oidc_allowlist_grants(email, settings)
                if not membership_service.is_member(
                    session,
                    email,
                    env_allowed=env_allowed,
                    unmanaged_default=_oidc_access_allowed(email, settings),
                    settings=settings,
                ):
                    log.warning("mcp_oidc_access_denied", **_denied_identity(email))
                    raise McpAuthError("this account is not authorized for this DataQ workspace")
                name = claims.get("name")
                return _upsert_user(
                    session,
                    aad_object_id=str(subject),
                    email=email,
                    display_name=str(name) if name is not None else None,
                    oidc_issuer=settings.oidc_issuer,
                    env_allowed=env_allowed,
                )
        else:
            # Mirror the REST validator's guest policy — /mcp must not accept an
            # identity the REST API would 403.
            if not settings.azure_allow_guest_users and is_guest(claims):
                raise McpAuthError("guest users are not permitted")
            # `oid` is mandatory — never fall back to `sub` (a per-app pairwise
            # pseudonym that would key a divergent/duplicate users row).
            aad_oid = claims.get("oid")
            if aad_oid:
                email = str(
                    claims.get("preferred_username")
                    or claims.get("email")
                    or claims.get("upn")
                    or ""
                )
                name = claims.get("name")
                return _upsert_user(
                    session,
                    aad_object_id=str(aad_oid),
                    email=email,
                    display_name=str(name) if name is not None else None,
                    oidc_issuer=_AZURE_ISSUER,
                )
    if _dev_bypass_allowed(get_settings()):
        return _upsert_user(
            session,
            aad_object_id=DEV_BYPASS_AAD_OID,
            email=DEV_BYPASS_EMAIL,
            display_name=DEV_BYPASS_DISPLAY_NAME,
        )
    # The auth provider rejects unauthenticated calls before any tool runs;
    # defence-in-depth for the (mis)configured case.
    raise McpAuthError("could not resolve an authenticated MCP user")
