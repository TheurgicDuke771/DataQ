"""Auth for the MCP server — the *same* credentials the REST API accepts.

MCP clients (Claude Desktop / Claude.ai / Copilot / Cursor) present either the
Azure AD access token the web UI uses **or a DataQ PAT** (`dq_live_…`, ADR 0026
— the seam is shared, never MCP-only). Azure tokens are validated with a
fastmcp ``JWTVerifier`` configured from the same tenant / audience / scope as
``core.auth`` — issuer, signature (Azure JWKS), expiry, and the required API
scope; PATs by hashed lookup via ``api_key_service``, exactly like REST. The
two are disjoint by prefix, composed in ``_PatOrJwtVerifier``.

Modes, picked from settings in the **same order as** ``core.auth``'s
``get_current_user`` ladder — so a deployment cannot authenticate on REST in one
mode and on ``/mcp`` in another (``mcp_auth_mode``):

- **azure_ad** (`azure_auth_configured`): the composite verifier above.
- **pat_only** (`otp_auth_configured`, no Azure — ADR 0032, #1128): the same
  composite with the **JWT half absent**. An OTP deployment has no directory to
  validate a JWT against, so a PAT is the *only* MCP credential; every non-PAT
  bearer is rejected uniformly, having reached no validator at all. The
  alternative — mounting nothing — silently cost an OTP deployment the whole
  MCP surface (19 tools) even though its PATs work perfectly (#1128).
- **dev_bypass** (`ENVIRONMENT=dev` + `AUTH_DEV_BYPASS=true`, no Azure, no OTP):
  no verifier — every call resolves to the fixed dev user, for local dev only.
- **disabled**: nothing configured → the server is **not mounted** (fail-closed —
  the ``/mcp`` endpoint never goes live without auth; CLAUDE.md §10 security note).

Note what an **unconfigured JWT verifier never becomes**: an accept-anything
path. ``_PatOrJwtVerifier`` with ``_jwt is None`` returns ``None`` (fastmcp's
uniform 401) for anything that is not a valid PAT — the absence of a validator
is a rejection, not a skip.
"""

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
    _upsert_user,
    discover_jwks_uri,
    fetch_userinfo,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import User
from backend.app.services import api_key_service, session_service
from backend.app.services.otp_service import normalize_email

log = get_logger(__name__)

# Claim key carrying the PAT-resolved DataQ user id through fastmcp's token
# context into `resolve_current_user` (a DataQ-internal claim, not an OIDC one).
PAT_USER_CLAIM = "dataq_user_id"


class McpAuthError(Exception):
    """Raised inside a tool when the caller can't be resolved (defence-in-depth)."""


#: The MCP auth modes, in the order they are selected. Mirrors the
#: ``core.auth.get_current_user`` ladder exactly (Azure → generic OIDC → OTP →
#: dev bypass → nothing), because REST and ``/mcp`` sharing credentials is only
#: true if they also share the mode selection.
McpAuthMode = Literal["azure_ad", "generic_oidc", "pat_only", "dev_bypass", "disabled"]


def mcp_auth_mode(settings: Settings | None = None) -> McpAuthMode:
    """Which authenticator ``/mcp`` runs on — the single ladder everything reads.

    One function so the mount gate (``mcp_enabled``), the provider construction
    (``build_auth_provider``) and the startup log line cannot drift from each
    other: the previous shape had the gate and the log each re-deriving the mode
    from ``azure_auth_configured``, which is exactly how an OTP deployment ended
    up unmounted *and* reported as "dev_bypass".

    ``generic_oidc`` sits alongside ``azure_ad`` (mutually exclusive by
    ``Settings._validate_generic_oidc``) rather than composing with it — ``/mcp``
    has no "azure_ad_or_otp"-shaped mode either, since a session cookie is never
    an MCP credential (ADR 0032 decision 1); only PAT-vs-real-JWT varies.

    OTP outranks dev bypass for the same reason it does in ``core.auth``: an
    OTP-configured stack is a real auth configuration, and resolving it to the
    unauthenticated bypass would be a downgrade. (``_dev_bypass_allowed`` already
    excludes an Azure- or generic-OIDC-configured stack.)
    """
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
    """Whether ``/mcp`` should be mounted at all — only when auth is resolvable.

    Azure AD, an OTP deployment's PATs, or the local dev-bypass. Never an
    unauthenticated mount in a deployed (prod/staging) environment.
    """
    return mcp_auth_mode(settings) != "disabled"


class _PatOrJwtVerifier(TokenVerifier):
    """Composite verifier: DataQ PAT by prefix, else the Azure ``JWTVerifier``.

    The branches are disjoint (a ``dq_live_…`` bearer is never a valid JWT).
    A bad PAT returns ``None`` — fastmcp turns that into the standard 401 —
    and is logged prefix-only inside ``api_key_service``.

    An OTP **session** token (``dq_sess_…``) is a third, explicitly rejected
    prefix: ADR 0032 keeps sessions to the browser and PATs to headless/MCP
    clients. Rejecting by prefix (rather than letting the JWT branch fail) is what
    keeps a session token out of the JWT validator's log line.

    ``jwt_verifier`` is ``None`` in **pat_only** mode (an OTP deployment, #1128):
    there is no directory to validate a JWT against, so the JWT half is genuinely
    absent rather than misconfigured. A missing verifier **rejects** — it is never
    a fall-through — which is the whole reason it is modelled as an explicit
    ``None`` here instead of, say, a permissive stub.
    """

    def __init__(self, jwt_verifier: JWTVerifier | None) -> None:
        super().__init__()
        self._jwt = jwt_verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.startswith(session_service.TOKEN_PREFIX):
            # `/mcp` is an explicit NON-GOAL for sessions (ADR 0032 decision 1):
            # a session is a browser credential, a PAT is the headless/MCP one.
            # Rejected HERE, before the JWT verifier, for the #849 reason — a
            # `dq_sess_…` is not a JWT, and handing it to a JWT validator is how a
            # live credential ends up in a "Malformed token" log line. Returning
            # None yields fastmcp's standard 401.
            return None
        if not token.startswith(api_key_service.TOKEN_PREFIX):
            if self._jwt is None:
                # pat_only mode (#1128): PATs are the only /mcp credential here.
                # Uniform rejection, and — like the session branch above — no token
                # material reaches a validator that would log what it cannot decode.
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
    """The fastmcp auth provider for the current ``mcp_auth_mode``.

    - ``azure_ad`` → the PAT-or-Azure-JWT composite.
    - ``generic_oidc`` → the same composite, JWT half pointed at the configured
      issuer instead of Azure's hardcoded endpoints. No ``required_scopes`` —
      Azure's API-scope pattern isn't universal, and DataQ's authorization is
      per-suite sharing on the resolved user row, not token scopes (same
      reasoning as ``core.auth.OidcBearerScheme``).
    - ``pat_only`` → the same composite with the JWT half absent (PAT or 401).
    - ``dev_bypass`` → ``None``, i.e. an unauthenticated server. This is the ONE
      mode that returns ``None``, and it is only ever mounted when
      ``_dev_bypass_allowed`` is true (see ``mcp_enabled``).
    - ``disabled`` → the PAT-only verifier, deliberately **not** ``None``. Nothing
      mounts in this mode, so it is unreachable in practice; making the
      unreachable case require a credential rather than none means a future
      mounting mistake degrades to "nobody can authenticate", not "everybody can".
    """
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
    """Resolve the calling user and upsert them — the MCP twin of ``get_current_user``.

    In real mode the validated token's claims (set by the ``JWTVerifier``) supply
    the AAD object id + email + name, upserted via the shared ``_upsert_user`` so
    the ``users`` row is identical to a web-UI login. In dev bypass (no token) the
    fixed dev user is used. Reuses ``core.auth`` so claim handling can't drift.
    """
    token = get_access_token()
    if token is not None:
        claims: dict[str, Any] = token.claims or {}
        # PAT path (ADR 0026): the verifier already resolved (and last-used-
        # stamped) the owning user; load it by id — no upsert, the user exists.
        pat_user_id = claims.get(PAT_USER_CLAIM)
        if pat_user_id:
            user = session.get(User, pat_user_id)
            if user is None:  # revoked/deleted between verify and tool call
                raise McpAuthError("could not resolve the API key's user")
            return user
        settings = get_settings()
        mode = mcp_auth_mode(settings)
        if mode == "generic_oidc":
            # `sub` — the RFC 7519 REQUIRED subject claim — not Azure's `oid`.
            # No guest-user policy here: that concept is Azure B2B-specific, and
            # the generic REST validator (`core.auth.OidcBearerScheme`) applies
            # none either.
            subject = claims.get("sub")
            if subject:
                # #1346: Cognito access tokens carry no email/name — resolve
                # them from the issuer's userinfo endpoint, exactly as the REST
                # validator does (shared cache in core.auth). Fail-closed on an
                # outage: an empty-email upsert poisons the users row.
                if not claims.get("email") and settings.oidc_issuer:
                    try:
                        userinfo = fetch_userinfo(settings.oidc_issuer, token.token)
                    except (httpx.HTTPError, ValueError) as exc:
                        # ValueError: a 200 whose body is not a JSON object —
                        # same fail-closed outcome as an outage (core.auth
                        # `_json_dict`), never an unhandled tool crash.
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
                # Same access allowlist the REST resolver applies (#1386). This
                # is the THIRD generic-OIDC resolver in the codebase and the one
                # easiest to forget: /mcp does not go through
                # `_resolve_generic_oidc_user`, so without this line a token the
                # REST API 403s would authenticate here, be provisioned a users
                # row, and get all 19 tools — including `trigger_suite_run`.
                # Exactly the invariant the Azure-guest branch below states.
                if not _oidc_access_allowed(email, settings):
                    log.warning("mcp_oidc_access_denied", **_denied_identity(email))
                    raise McpAuthError("this account is not authorized for this DataQ workspace")
                name = claims.get("name")
                return _upsert_user(
                    session,
                    aad_object_id=str(subject),
                    email=email,
                    display_name=str(name) if name is not None else None,
                    oidc_issuer=settings.oidc_issuer,
                )
        else:
            # Mirror the REST validator's guest policy: reject Azure AD guests
            # (B2B / external) unless explicitly allowed, so /mcp can't accept
            # an identity the REST API would 403. The JWTVerifier already
            # validated signature / issuer / audience / scope; this is the
            # tenant-membership policy on top.
            if not settings.azure_allow_guest_users and is_guest(claims):
                raise McpAuthError("guest users are not permitted")
            # `oid` (the stable directory object id) is mandatory, exactly as
            # the REST resolver treats it — never fall back to `sub` (a per-app
            # pairwise pseudonym), which would key a divergent / duplicate
            # users row.
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
    # Auth provider rejects unauthenticated calls before reaching a tool; this is
    # defence-in-depth for the (mis)configured case.
    raise McpAuthError("could not resolve an authenticated MCP user")
