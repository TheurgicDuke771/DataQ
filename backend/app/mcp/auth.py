"""Auth for the MCP server — validate the *same* Azure AD bearer token as the REST API.

MCP clients (Claude Desktop / Claude.ai / Copilot / Cursor) present the same
Azure AD access token the web UI uses. We validate it with a fastmcp
``JWTVerifier`` configured from the same tenant / audience / scope as
``core.auth`` — issuer, signature (Azure JWKS), expiry, and the required API
scope. This mirrors the REST validator without depending on fastapi-azure-auth
internals (which are Starlette-request-bound and can't verify a raw token).

Two modes, picked from settings exactly like ``core.auth``:

- **Real mode** (`azure_auth_configured`): the ``JWTVerifier`` above.
- **Dev bypass** (`ENVIRONMENT=dev` + `AUTH_DEV_BYPASS=true`, no Azure vars): no
  verifier — every call resolves to the fixed dev user, for local dev only.

If neither is configured the server is **not mounted** (fail-closed — the
``/mcp`` endpoint never goes live without auth; CLAUDE.md §10 security note).
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from sqlalchemy.orm import Session

from backend.app.core.auth import (
    DEV_BYPASS_AAD_OID,
    DEV_BYPASS_DISPLAY_NAME,
    DEV_BYPASS_EMAIL,
    _dev_bypass_allowed,
    _upsert_user,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import User

log = get_logger(__name__)


class McpAuthError(Exception):
    """Raised inside a tool when the caller can't be resolved (defence-in-depth)."""


def mcp_enabled(settings: Settings | None = None) -> bool:
    """Whether ``/mcp`` should be mounted at all — only when auth is resolvable.

    Real Azure auth, or the local dev-bypass. Never an unauthenticated mount in a
    deployed (prod/staging) environment.
    """
    s = settings or get_settings()
    return s.azure_auth_configured or _dev_bypass_allowed(s)


def build_auth_provider(settings: Settings | None = None) -> AuthProvider | None:
    """The fastmcp auth provider — a JWTVerifier in real mode, ``None`` in dev bypass.

    Returning ``None`` leaves the mounted server unauthenticated; callers must only
    mount in that case when ``_dev_bypass_allowed`` is true (see ``mcp_enabled``).
    """
    s = settings or get_settings()
    if not s.azure_auth_configured:
        return None
    tenant = s.azure_tenant_id
    # Single-tenant v2 endpoint — same coordinates fastapi-azure-auth uses.
    return JWTVerifier(
        jwks_uri=f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
        audience=s.azure_api_client_id,
        required_scopes=[s.azure_api_scope],
    )


def resolve_current_user(session: Session) -> User:
    """Resolve the calling user and upsert them — the MCP twin of ``get_current_user``.

    In real mode the validated token's claims (set by the ``JWTVerifier``) supply
    the AAD object id + email + name, upserted via the shared ``_upsert_user`` so
    the ``users`` row is identical to a web-UI login. In dev bypass (no token) the
    fixed dev user is used. Reuses ``core.auth`` so claim handling can't drift.
    """
    token = get_access_token()
    if token is not None:
        claims: dict[str, Any] = token.claims or {}
        aad_oid = str(claims.get("oid") or token.subject or "")
        email = str(
            claims.get("preferred_username") or claims.get("email") or claims.get("upn") or ""
        )
        name = claims.get("name")
        if aad_oid:
            return _upsert_user(
                session,
                aad_object_id=aad_oid,
                email=email,
                display_name=str(name) if name is not None else None,
            )
    if _dev_bypass_allowed(get_settings()):
        return _upsert_user(
            session,
            aad_object_id=DEV_BYPASS_AAD_OID,
            email=DEV_BYPASS_EMAIL,
            display_name=DEV_BYPASS_DISPLAY_NAME,
        )
    # Auth provider rejects unauthenticated calls before reaching a tool; this is
    # defence-in-depth for the (mis)configured case.
    raise McpAuthError("could not resolve an authenticated MCP user")
