"""Auth seam: DataQ PAT · email-OTP session cookie · Azure AD token, + user upsert.

Three authenticators, one `get_current_user` seam, resolved in a fixed order
(ADR 0026 decision 1, extended by ADR 0032 decision 1):

    1. `Authorization: Bearer dq_live_…`  → PAT, hashed lookup in `api_keys`
    2. `Cookie: dataq_session=dq_sess_…`  → OTP session, hashed lookup in `sessions`
    3. anything else in `Authorization`   → Azure AD token (`fastapi-azure-auth`)

The branches are **disjoint by construction**, which is the #849 lesson made
structural: a `dq_live_` bearer is never a valid JWT and must never reach a JWT
validator (which logs what it cannot decode), and a request presenting a session
cookie is decided by that cookie — it never falls through to another
authenticator on failure. Every failure is the same uniform 401.

Modes, picked once at import time from settings:

- **Real mode** — `AZURE_TENANT_ID` + `AZURE_API_CLIENT_ID` set. Azure tokens are
  validated by `fastapi-azure-auth` (issuer, audience, signature, expiry, scope;
  OpenID config loaded at startup by `init_auth()` and refreshed automatically).
- **OTP mode** — the `AUTH_EMAIL_*` block complete AND a non-empty signup
  allowlist (`Settings.otp_auth_configured`, ADR 0032). Humans sign in with an
  emailed code and carry a session cookie; PATs still work.
- **Real + OTP** — both configured; the cookie is checked before the JWT branch.
- **Dev bypass** — `ENVIRONMENT=dev` + `AUTH_DEV_BYPASS=true` + Azure vars empty.
  No credential required; every request resolves to a fixed dev user.

If nothing is configured, `init_auth` raises at startup — fail-closed.
"""

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, Request, Security
from fastapi.security import SecurityScopes
from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer
from fastapi_azure_auth.user import User as AzureUser
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import case, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import api_key_service, session_service
from backend.app.services.otp_service import normalize_email

log = get_logger(__name__)

DEV_BYPASS_AAD_OID = "00000000-0000-0000-0000-000000000001"
DEV_BYPASS_EMAIL = "dev-bypass@dataq.local"
DEV_BYPASS_DISPLAY_NAME = "Dev Bypass User"


def _dev_bypass_allowed(settings: Settings) -> bool:
    return (
        settings.environment == "dev"
        and settings.auth_dev_bypass
        and not settings.azure_auth_configured
        and not settings.generic_oidc_configured
    )


class _PatAwareAzureScheme(SingleTenantAzureAuthorizationCodeBearer):
    """The Azure scheme, taught to keep its hands off a DataQ PAT (#849).

    `Security(azure_scheme)` is a FastAPI *dependency*, so it resolves **before**
    `get_current_user`'s body runs — meaning the PAT-first ordering documented there was
    never actually first. Every `dq_live_…` bearer was handed to a JWT validator, which
    naturally failed to decode it and logged

        log.warning('Malformed token received. %s. Error: %s', access_token, error)

    …shipping the **raw PAT** — a live bearer credential — into App Insights on every
    single PAT-authenticated request, plus an exception record for good measure.

    A PAT is not a JWT and must never reach a JWT validator. Short-circuiting to ``None``
    here makes the two branches genuinely disjoint (`get_current_user` then takes the PAT
    path), removes the log line at its source, and stops the exception spam.

    The logger-level redaction in `core.logging` (`_BEARER_TOKEN_RE`) stays as the
    backstop — we do not control what a dependency logs, and the next library to echo a
    token won't announce itself either.
    """

    async def __call__(
        self, request: HTTPConnection, security_scopes: SecurityScopes
    ) -> AzureUser | None:
        # `HTTPConnection`, not `Request`, because that is what the library declares and
        # what FastAPI may hand us: a WebSocket route secured with this scheme yields a
        # `WebSocket` — also an HTTPConnection, but NOT a Request. Narrowing the type
        # would have needed a `type: ignore[override]`, which silences precisely the check
        # that would flag the mismatch (#849 review). Both carry `.headers`, which is all
        # `_pat_token` reads.
        if _pat_token(request) is not None:
            return None
        # Same short-circuit for an OTP session cookie (ADR 0032 decision 1): the
        # cookie is checked before the JWT branch, and `Security(azure_scheme)`
        # resolves before `get_current_user`'s body — so "before the JWT branch"
        # has to mean *here*, not in the function that runs afterwards.
        #
        # Gated on OTP actually being enabled. Ungated, any client could disable
        # JWT validation on an Azure-only deployment by attaching a junk
        # `dataq_session` cookie — turning a cosmetic short-circuit into an auth
        # bypass vector. `_otp_enabled` is the import-time mode flag.
        if _otp_enabled and _session_token(request) is not None:
            return None
        user: AzureUser | None = await super().__call__(request, security_scopes)
        return user


def _build_azure_scheme(
    settings: Settings,
) -> SingleTenantAzureAuthorizationCodeBearer | None:
    if not settings.azure_auth_configured:
        return None
    assert settings.azure_api_client_id is not None
    assert settings.azure_tenant_id is not None
    assert settings.azure_api_scope_uri is not None
    return _PatAwareAzureScheme(
        app_client_id=settings.azure_api_client_id,
        tenant_id=settings.azure_tenant_id,
        scopes={settings.azure_api_scope_uri: settings.azure_api_scope},
        allow_guest_users=settings.azure_allow_guest_users,
        # auto_error=False so a failed Azure validation yields None instead of
        # raising — get_current_user then rejects with the standard error
        # envelope. Required for the PAT path (ADR 0026): a `dq_live_…` bearer
        # is not a JWT and must not be force-rejected by the Azure scheme.
        auto_error=False,
    )


def _json_dict(response: httpx.Response) -> dict[str, Any]:
    """The response body as a parsed JSON object, or `ValueError`.

    A 200 carrying an HTML error page (a proxy in front of the IdP), a JSON
    scalar (`null`), or an `application/jwt` userinfo body must hit the same
    fail-closed branch as an outage — `json.JSONDecodeError` is NOT an
    `httpx.HTTPError`, so without this normalization it would escape the
    outage guards as an unhandled 500 on the auth path (the #567 class).
    `json.JSONDecodeError` subclasses `ValueError`, so callers catch
    `(httpx.HTTPError, ValueError)` and get both shapes.
    """
    body = response.json()  # raises json.JSONDecodeError (a ValueError)
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object body")
    return body


def discover_oidc_config(issuer: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch an OIDC issuer's discovery document (parsed, unvalidated).

    Synchronous and shared by every caller that needs a discovery field:
    `OidcBearerScheme.load_config` below (wrapped in `asyncio.to_thread` — it
    must not block the event loop), `mcp.auth.build_auth_provider` (genuinely
    synchronous — fastmcp's auth provider is built at **module import time**,
    before any async startup hook exists to call into), and `fetch_userinfo`
    (#1346). One implementation, not a sync/async pair that could quietly
    drift apart.
    """
    response = httpx.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration", timeout=timeout)
    response.raise_for_status()
    return _json_dict(response)


def discover_jwks_uri(issuer: str, *, timeout: float = 5.0) -> str:
    """Resolve `jwks_uri` — the one REQUIRED endpoint — from OIDC discovery."""
    return str(discover_oidc_config(issuer, timeout=timeout)["jwks_uri"])


# ── userinfo fallback (#1346) ────────────────────────────────────────────────
#
# Cognito access tokens carry no `email`/`name` (only sub/client_id/scope/…),
# so identity claims a token omits are resolved from the issuer's standard
# `userinfo` endpoint instead — provider-neutral: any token that already embeds
# `email` (Azure AD, Keycloak defaults, …) never triggers the round-trip.
#
# The cache is keyed by the token's SHA-256 (never the raw bearer — it must not
# sit in a long-lived dict a heap dump could read) and bounded: userinfo is one
# extra HTTP call per token per TTL, not per request.

_USERINFO_TTL_SECONDS = 300.0
_USERINFO_CACHE_MAX = 1024
_userinfo_cache: dict[str, tuple[float, dict[str, Any]]] = {}
#: Discovery documents are static per issuer for a process lifetime (the REST
#: scheme resolves once at startup; this is the sync/MCP path's equivalent).
_discovery_cache: dict[str, dict[str, Any]] = {}
#: One lock for both caches: the REST validator mutates them from the event-loop
#: thread while MCP tools (sync `def`s in worker threads) do the same — an
#: unguarded eviction sweep can hit "dict changed size during iteration".
_oidc_cache_lock = threading.Lock()


def _userinfo_cache_get(token: str) -> dict[str, Any] | None:
    with _oidc_cache_lock:
        entry = _userinfo_cache.get(hashlib.sha256(token.encode()).hexdigest())
    if entry is None or entry[0] < time.monotonic():
        return None
    return entry[1]


def _userinfo_cache_put(token: str, claims: dict[str, Any]) -> None:
    now = time.monotonic()
    with _oidc_cache_lock:
        if len(_userinfo_cache) >= _USERINFO_CACHE_MAX:
            for key in [k for k, (expiry, _) in _userinfo_cache.items() if expiry < now]:
                del _userinfo_cache[key]
            while len(_userinfo_cache) >= _USERINFO_CACHE_MAX:
                # Still full after dropping expired entries: evict oldest-inserted.
                del _userinfo_cache[next(iter(_userinfo_cache))]
        _userinfo_cache[hashlib.sha256(token.encode()).hexdigest()] = (
            now + _USERINFO_TTL_SECONDS,
            claims,
        )


def _cached_discovery(issuer: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """`discover_oidc_config`, memoized per issuer for the process lifetime.

    Without this, every userinfo-cache miss (and EVERY sync `fetch_userinfo`
    call against a provider that publishes no userinfo endpoint, for which
    nothing else is ever cached) paid a discovery round-trip on top.
    """
    with _oidc_cache_lock:
        cached = _discovery_cache.get(issuer)
    if cached is not None:
        return cached
    config = discover_oidc_config(issuer, timeout=timeout)
    with _oidc_cache_lock:
        _discovery_cache[issuer] = config
    return config


def _merge_userinfo(claims: dict[str, Any], userinfo: dict[str, Any]) -> dict[str, Any] | None:
    """`claims` with `email`/`name` filled from `userinfo`, or None on sub mismatch.

    OIDC Core §5.3.2: the client MUST verify the userinfo `sub` matches the
    token's — a mismatch means the response describes someone else (an IdP bug
    or a swapped response) and the sign-in must fail rather than mint a user
    row from mixed identities.
    """
    if userinfo.get("sub") != claims.get("sub"):
        return None
    for claim in ("email", "name"):
        if not claims.get(claim) and userinfo.get(claim):
            claims[claim] = userinfo[claim]
    return claims


def fetch_userinfo(issuer: str, token: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """The issuer's userinfo response for `token` — sync, for `mcp.auth` (#1346).

    Returns None when the provider publishes no `userinfo_endpoint` (it is
    RECOMMENDED, not required — the caller proceeds with whatever the token
    carried). Raises `httpx.HTTPError` on an outage and `ValueError` on a 200
    whose body is not a JSON object (`_json_dict`): both are outages to
    surface, never an empty identity (the ADR 0039 decision 6 lesson applied
    to auth). The REST path (`OidcBearerScheme._verify`) does the same fetch
    through its own async client; both share the TTL cache.
    """
    cached = _userinfo_cache_get(token)
    if cached is not None:
        return cached
    endpoint = _cached_discovery(issuer, timeout=timeout).get("userinfo_endpoint")
    if not endpoint:
        return None
    response = httpx.get(
        str(endpoint), headers={"Authorization": f"Bearer {token}"}, timeout=timeout
    )
    response.raise_for_status()
    userinfo = _json_dict(response)
    _userinfo_cache_put(token, userinfo)
    return userinfo


class OidcBearerScheme:
    """Provider-neutral OIDC bearer validator (ADR 0026 amendment).

    Same PAT/OTP short-circuit discipline as `_PatAwareAzureScheme`, and the same
    "never raise, return None on failure" contract as `auto_error=False` — but
    speaks the *standard* (OIDC discovery + JWKS) instead of a vendor SDK
    hardcoded to Microsoft's endpoints. `httpx` does the HTTP (this codebase's
    established client, e.g. `OpenBaoSecretStore`); `PyJWT` does signature
    verification (already an installed transitive of `fastapi-azure-auth`,
    promoted to a direct pin since this class now imports it directly).

    Returns the token's raw claims dict — there is no shared wrapper type to
    mirror `AzureUser` with, and none is needed; `_extract_oidc_claims` reads it
    the same way `_extract_claims` reads `AzureUser.claims`.

    **RS256 only.** Every provider this was built against (Cognito, and RS256 is
    the near-universal default for GCP Identity Platform/Okta/Auth0/Keycloak)
    signs with RSA; a provider that signs with something else fails closed
    (`_verify` returns `None`, i.e. the standard 401) rather than silently
    accepting an unverified token — extend `_signing_key` if that's ever needed.
    """

    def __init__(self, issuer: str, audience: str, *, timeout: float = 5.0) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._client = httpx.AsyncClient(timeout=timeout)
        self._jwks_uri: str | None = None
        self._jwks_cache: dict[str, Any] | None = None
        self._userinfo_endpoint: str | None = None

    async def load_config(self) -> None:
        """Resolve discovery endpoints and pre-warm the JWKS cache.

        Called once from `init_auth()` — fail-closed at startup, mirroring
        `azure_scheme.openid_config.load_config()`: a deployment must not report
        healthy while unable to validate a single token.

        `discover_oidc_config` is synchronous (shared with `mcp.auth`, which can
        only call it synchronously — see its docstring), so it runs off the
        event loop thread here rather than blocking it. `userinfo_endpoint` is
        optional (absent → tokens must carry their own identity claims).
        """
        config = await asyncio.to_thread(_cached_discovery, self._issuer)
        self._jwks_uri = str(config["jwks_uri"])
        userinfo_endpoint = config.get("userinfo_endpoint")
        self._userinfo_endpoint = str(userinfo_endpoint) if userinfo_endpoint else None
        await self._refresh_jwks()

    async def _refresh_jwks(self) -> dict[str, Any]:
        assert self._jwks_uri is not None, "load_config() must run before any token is verified"
        response = await self._client.get(self._jwks_uri)
        response.raise_for_status()
        jwks: dict[str, Any] = _json_dict(response)
        self._jwks_cache = jwks
        return jwks

    def _signing_key(self, kid: str, jwks: dict[str, Any]) -> Any:
        for key in jwks.get("keys", []):
            if key.get("kid") == kid and key.get("kty") == "RSA":
                return RSAAlgorithm.from_jwk(json.dumps(key))
        return None

    async def __call__(self, request: HTTPConnection) -> dict[str, Any] | None:
        if _pat_token(request) is not None:
            return None
        if _otp_enabled and _session_token(request) is not None:
            return None
        token = _bearer_token(request)
        if token is None:
            return None
        return await self._verify(token)

    async def _verify(self, token: str) -> dict[str, Any] | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid = header.get("kid")
        if not isinstance(kid, str):
            return None
        jwks = self._jwks_cache
        try:
            key = self._signing_key(kid, jwks) if jwks is not None else None
            if key is None:
                # Absent from the cache: either a genuinely unknown key, or the
                # provider rotated its signing keys since the last fetch. Refresh
                # once and retry before giving up — mirrors OpenBao's
                # single-retry-then-surface discipline (`_send`).
                key = self._signing_key(kid, await self._refresh_jwks())
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError: a 200 whose body is not a JSON object (`_json_dict`).
            log.warning("oidc_jwks_unavailable", issuer=self._issuer, error=str(exc))
            return None
        if key is None:
            return None
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self._issuer,
                # Audience is checked manually below, not via PyJWT's own
                # `audience=` kwarg — see the docstring's Cognito note.
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None
        aud = claims.get("aud")
        aud_values = aud if isinstance(aud, list) else [aud] if aud else []
        # Standard OIDC puts the client id in `aud`. AWS Cognito's ACCESS token —
        # what the SPA actually sends as the bearer, matching how the Azure path
        # also validates an access token — carries it as `client_id` instead;
        # its ID token does carry `aud`, but that is not what arrives here. This
        # is the one deliberately provider-aware line in an otherwise generic
        # implementation; it needs confirming against a real Cognito token once
        # one exists (this codebase's own rule for anything crossing a
        # third-party token-shape boundary — code review alone cannot settle it).
        if self._audience not in aud_values and claims.get("client_id") != self._audience:
            return None
        # #1346: a token that omits `email` (Cognito access tokens carry none)
        # gets it from the issuer's userinfo endpoint. Fail-closed on an outage:
        # provisioning a user row with an empty email is silent data poisoning
        # (the second such row can never sign in — `uq_users_email_lower`),
        # whereas a 401 is a visible, retryable failure.
        if not claims.get("email") and self._userinfo_endpoint is not None:
            userinfo = _userinfo_cache_get(token)
            if userinfo is None:
                try:
                    response = await self._client.get(
                        self._userinfo_endpoint,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    response.raise_for_status()
                    userinfo = _json_dict(response)
                except (httpx.HTTPError, ValueError) as exc:
                    # ValueError covers a 200 with a non-JSON / non-object body
                    # (proxy error page, JSON null, signed-JWT userinfo) — same
                    # fail-closed outcome as an outage, never a 500 (#567 class).
                    log.warning("oidc_userinfo_unavailable", issuer=self._issuer, error=str(exc))
                    return None
                _userinfo_cache_put(token, userinfo)
            merged = _merge_userinfo(claims, userinfo)
            if merged is None:
                log.warning("oidc_userinfo_sub_mismatch", issuer=self._issuer)
                return None
            claims = merged
        return claims


def _build_oidc_scheme(settings: Settings) -> OidcBearerScheme | None:
    if not settings.generic_oidc_configured:
        return None
    assert settings.oidc_issuer is not None
    assert settings.oidc_audience is not None
    return OidcBearerScheme(issuer=settings.oidc_issuer, audience=settings.oidc_audience)


def _bearer_token(request: HTTPConnection) -> str | None:
    """The raw bearer token from the Authorization header, if any.

    Takes `HTTPConnection` (the common base of `Request` and `WebSocket`) so the security
    scheme can call it with whatever FastAPI injects — only `.headers` is read."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def _pat_token(request: HTTPConnection) -> str | None:
    """The bearer token when it is a DataQ PAT (by prefix), else None."""
    token = _bearer_token(request)
    if token is not None and token.startswith(api_key_service.TOKEN_PREFIX):
        return token
    return None


def _session_token(request: HTTPConnection) -> str | None:
    """The OTP session token from the cookie, if present and shaped like one.

    Prefix-checked for the same reason the PAT branch is: it keeps the
    authenticators disjoint, so a cookie set by something else on the same origin
    cannot steer a request into the session branch. Reads `.cookies`, which both
    `Request` and `WebSocket` expose (see `_bearer_token` on the type choice).
    """
    token = request.cookies.get(session_service.COOKIE_NAME)
    if token and token.startswith(session_service.TOKEN_PREFIX):
        return token
    return None


_settings = get_settings()
azure_scheme: SingleTenantAzureAuthorizationCodeBearer | None = _build_azure_scheme(_settings)
#: The deterministic Azure AD v2 issuer URL, written to `users.oidc_issuer` on
#: every real-mode login (self-healing pre-existing rows — see the model
#: docstring). Same coordinates `fastapi_azure_auth` itself validates against.
_AZURE_ISSUER: str | None = (
    f"https://login.microsoftonline.com/{_settings.azure_tenant_id}/v2.0"
    if _settings.azure_tenant_id
    else None
)
#: The generic-OIDC counterpart to `azure_scheme` — mutually exclusive with it
#: (`Settings._validate_generic_oidc`), so exactly one of the two is non-None.
oidc_scheme: OidcBearerScheme | None = _build_oidc_scheme(_settings)
#: Whether email OTP sign-in is configured — read once at import, like the Azure
#: scheme, because the whole mode ladder is bound at import time (12-factor: change
#: the env and restart).
_otp_enabled: bool = _settings.otp_auth_configured


class IdentityConflictError(DataQError):
    """Two identities claim one mailbox — the sign-in cannot be resolved to a user.

    Raised when the upsert violates `uq_users_email_lower` (#735): the row is
    keyed on `aad_object_id`, so a *different* object id arriving with an email
    that case-collides with an existing row has no conflict target and hits the
    email index instead. Without this it is an unhandled `IntegrityError`, i.e. a
    500 on **every** login for that user (CONTRIBUTING rule 32 — never let a
    database exception surface as an unhandled error).

    409 rather than 401/403: the credential is valid, the workspace's identity
    state is not — an operator has to resolve it (the same duplicate-email
    resolution the 7d25617cfaf0 migration documents). ADR 0032 decision 6 is what
    makes it a conflict at all: one user row per normalized email.

    **Not** raised when the colliding row is an OTP identity (`aad_object_id IS
    NULL`) — that is one human with two authenticators, which decision 6 says to
    LINK, not to reject. See `_claim_unlinked_user`.
    """

    status_code = 409
    code = "identity_conflict"


def _claim_unlinked_user(
    db: Session,
    *,
    aad_object_id: str,
    email: str,
    display_name: str | None,
    now: datetime,
    oidc_issuer: str | None = None,
) -> User | None:
    """Attach an AAD identity to an existing OTP-provisioned row, or return None.

    The other half of ADR 0032 decision 6's linking rule. `otp_service` resolves
    an OTP sign-in onto an existing AAD row by `lower(email)`; this is the reverse
    direction, and without it the rule holds in only one direction: a person who
    signed in with an emailed code first (leaving a row with `aad_object_id IS
    NULL`) and later signed in through Azure AD would hit the email index, get an
    `IdentityConflictError`, and be **permanently 409'd on every subsequent AAD
    login** — with an error message telling them another account owns their
    address, when in fact it is their own.

    Deliberately narrow: it claims **only** a row whose `aad_object_id` is NULL.
    A row already carrying a *different* object id is the genuine conflict #1131
    exists for (two directory identities on one mailbox, which needs an operator),
    and that path is unchanged.

    The `IS NULL` predicate rides in the UPDATE, so the claim is atomic: two AAD
    sign-ins racing for the same unlinked row cannot both succeed. The loser gets
    `None` here and the caller retries the ordinary upsert, which now finds the
    winner's `aad_object_id` as a conflict target.

    `display_name` branches on `display_name_override` (#1139, migration
    6230293aea96), not a plain overwrite: the row being claimed is exactly the
    OTP-provisioned shape — email only, likely no name yet — so the incoming
    AAD claim is a good one to seed *unless* the person already set their own
    via `PATCH /me` before ever signing in through Azure AD, in which case
    linking must not silently discard it. `COALESCE(User.display_name, claim)`
    was the first cut here (and remains materially the same outcome for THIS
    function, which runs once, at the moment of linking) — it was replaced to
    use the same predicate as `_upsert_user` below, where COALESCE's actual
    defect lives (it can't tell "someone set this" from "a first login seeded
    it", so it also froze out every later legitimate claim rename). One
    predicate for both call sites is what makes "override survives, no-override
    syncs" hold as a single invariant instead of two similar-looking rules.
    """
    claimed = db.execute(
        update(User)
        .where(
            func.lower(User.email) == normalize_email(email),
            User.aad_object_id.is_(None),
        )
        .values(
            aad_object_id=aad_object_id,
            oidc_issuer=oidc_issuer,
            email=email,
            display_name=case(
                (User.display_name_override.is_(True), User.display_name),
                else_=display_name,
            ),
            last_seen_at=now,
            updated_at=now,
        )
        .returning(User)
    ).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        return None
    db.commit()
    # No email in the log line: `_PII_KEYS` redacts an `email` key, but the honest
    # move is not to hand it over. The object id is enough to correlate.
    log.info("identity_linked_otp_row_to_aad", aad_object_id=aad_object_id, user_id=str(claimed.id))
    return claimed


def _upsert_user(
    db: Session,
    *,
    aad_object_id: str,
    email: str,
    display_name: str | None,
    oidc_issuer: str | None = None,
    _retrying: bool = False,
) -> User:
    now = datetime.now(UTC)
    stmt = (
        insert(User)
        .values(
            aad_object_id=aad_object_id,
            oidc_issuer=oidc_issuer,
            email=email,
            display_name=display_name,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["aad_object_id"],
            set_={
                "email": email,
                "oidc_issuer": oidc_issuer,
                # Branches on `display_name_override` (#1139, migration
                # 6230293aea96), not a plain overwrite AND not a bare COALESCE.
                # This upsert runs on EVERY real-mode request (no session cache
                # — the JWT is re-validated and re-claimed each time), so a
                # plain overwrite would silently revert a `PATCH /me` override
                # back to the AAD token's `name` claim on the user's very next
                # request. A first cut used `COALESCE(User.display_name, claim)`
                # instead — review on #1139 caught that it over-corrects: it
                # can't distinguish "someone explicitly set this" from "a first
                # login seeded it from the very same claim", so EVERY user's
                # name froze at whatever their first login happened to claim,
                # and a genuine Entra rename never synced again for anyone,
                # override or not. The flag is the missing bit: sync the claim
                # in whenever nobody has overridden it (True → False stays
                # False → this row's whole life until a PATCH), and leave the
                # self-service value alone once they have. The INSERT branch
                # (`.values()` above) still seeds `display_name` from the claim
                # on a brand-new row — `display_name_override` defaults False
                # there (server_default, migration), so the very next login
                # still syncs it, which is correct: nobody overrode anything
                # yet.
                "display_name": case(
                    (User.display_name_override.is_(True), User.display_name),
                    else_=display_name,
                ),
                "last_seen_at": now,
                "updated_at": now,
            },
        )
        .returning(User)
    )
    try:
        user = db.execute(stmt).scalar_one()
        db.commit()
    except IntegrityError as exc:
        # The `aad_object_id` conflict is HANDLED by on_conflict_do_update above,
        # so anything reaching here is a different constraint — in practice
        # `uq_users_email_lower`. Roll back: the session is otherwise poisoned for
        # the rest of the request.
        db.rollback()
        # First, the benign reading of that collision: the colliding row is an OTP
        # identity for the same human (ADR 0032 decision 6). Link it rather than
        # reject the login.
        if not _retrying:
            linked = _claim_unlinked_user(
                db,
                aad_object_id=aad_object_id,
                email=email,
                display_name=display_name,
                now=now,
                oidc_issuer=oidc_issuer,
            )
            if linked is not None:
                return linked
            # Nothing to claim. Either the row already carries a different object
            # id (the real conflict, below), or a concurrent sign-in claimed it
            # first — in which case the ordinary upsert now HAS a conflict target
            # and succeeds. One bounded retry distinguishes the two; a second
            # IntegrityError is the genuine conflict.
            return _upsert_user(
                db,
                aad_object_id=aad_object_id,
                email=email,
                display_name=display_name,
                oidc_issuer=oidc_issuer,
                _retrying=True,
            )
        # Deliberately no email in the message or the log. The redactor covers
        # both `email` and `aad_object_id` keys (core/logging.py `_PII_KEYS`), so
        # the structured field below is safe — but the message travels in the HTTP
        # error envelope, which the redactor does not touch, and naming the
        # colliding address would tell any caller who else is in the workspace.
        log.warning("identity_conflict_on_upsert", aad_object_id=aad_object_id)
        raise IdentityConflictError(
            "This sign-in could not be resolved to a user account: another account "
            "already exists with the same email address. Contact your workspace "
            "administrator.",
        ) from exc
    return user


def _extract_claims(azure_user: AzureUser) -> tuple[str, str, str | None]:
    claims: dict[str, Any] = azure_user.claims
    aad_oid = str(claims["oid"])
    email = str(claims.get("preferred_username") or claims.get("email") or claims.get("upn") or "")
    display_name_raw = claims.get("name")
    display_name = str(display_name_raw) if display_name_raw is not None else None
    return aad_oid, email, display_name


def _extract_oidc_claims(claims: dict[str, Any]) -> tuple[str, str, str | None]:
    """The generic-OIDC counterpart to `_extract_claims`.

    `sub` — RFC 7519's REQUIRED subject claim — not Azure's non-standard `oid`;
    bare `claims["sub"]` on purpose, so a token missing it (a malformed/misissued
    token, since every compliant OIDC provider sets it) fails loudly rather than
    silently keying a row on an empty string.
    """
    subject = str(claims["sub"])
    email = str(claims.get("email") or "")
    display_name_raw = claims.get("name")
    display_name = str(display_name_raw) if display_name_raw is not None else None
    return subject, email, display_name


def _log_otp_mode_ready() -> None:
    """Announce OTP mode WITHOUT logging a single address.

    The allowlist is a workspace member list; `_PII_KEYS` redacts an `email` key,
    but the honest fix is not to hand it over — so this reports a count and the
    domains (an org identifier, not a person).
    """
    log.info(
        "auth_otp_mode_ready",
        allowed_email_count=len(_settings.auth_otp_allowed_email_set),
        allowed_domains=sorted(_settings.auth_otp_allowed_domain_set),
        session_ttl_hours=_settings.auth_session_ttl_hours,
        smtp_host=_settings.auth_email_smtp_host,
    )


async def init_auth() -> None:
    """Wire app startup: load OIDC config in real mode, announce OTP mode, or fail-closed.

    Called from the FastAPI lifespan. The fail-closed contract (ADR 0032 decision
    2) is the point: a deployment must never come up looking healthy while being
    unable to log anybody in. The *partial* OTP configurations are rejected even
    earlier, by `Settings._validate_otp_auth`, so by the time we get here OTP is
    either fully on or fully off.
    """
    if azure_scheme is not None:
        await azure_scheme.openid_config.load_config()
        log.info(
            "auth_real_mode_ready",
            provider="azure_ad",
            tenant_id=_settings.azure_tenant_id,
            client_id=_settings.azure_api_client_id,
            scope=_settings.azure_api_scope_uri,
        )
        # Both may be on at once (ADR 0032 decision 1's "real + otp"): AAD for the
        # org's own identities, OTP for the people it has no directory entry for.
        if _otp_enabled:
            _log_otp_mode_ready()
        return
    if oidc_scheme is not None:
        await oidc_scheme.load_config()
        log.info(
            "auth_real_mode_ready",
            provider="generic_oidc",
            issuer=_settings.oidc_issuer,
            audience=_settings.oidc_audience,
            signup_allowlist=_settings.oidc_allowlist_configured,
        )
        if not _settings.oidc_allowlist_configured:
            # The open state is the DEFAULT (a fail-closed default would lock a
            # whole workspace out on upgrade), so it must at least be loud: with
            # no app-side gate, DataQ admits every identity the issuer will
            # issue a token for, which makes the IdP's own registration policy
            # DataQ's access policy. That is fine for an invite-only tenant and
            # is exactly how #1385 happened on a self-signup-enabled pool.
            # Count + domains only, never the addresses themselves (PII) —
            # mirrors `_log_otp_mode_ready`.
            log.warning(
                "auth_oidc_no_signup_allowlist",
                issuer=_settings.oidc_issuer,
                hint="Set OIDC_ALLOWED_EMAILS / OIDC_ALLOWED_DOMAINS unless the "
                "issuer is invite-only; otherwise anyone who can register with "
                "the issuer gets a DataQ account.",
            )
        if _otp_enabled:
            _log_otp_mode_ready()
        return
    if _otp_enabled:
        _log_otp_mode_ready()
        return
    if _dev_bypass_allowed(_settings):
        log.warning(
            "auth_dev_bypass_active",
            environment=_settings.environment,
            note=(
                "Every request resolves to a fixed dev user. "
                "Do NOT run with this configuration outside local dev."
            ),
        )
        return
    raise RuntimeError(
        "Auth not configured. Set AZURE_TENANT_ID + AZURE_API_CLIENT_ID, "
        "or OIDC_ISSUER + OIDC_AUDIENCE for a generic OIDC provider (Cognito, "
        "GCP Identity Platform, Okta, Keycloak, ...), "
        "or configure email OTP sign-in (AUTH_EMAIL_SMTP_HOST + AUTH_EMAIL_USERNAME "
        "+ AUTH_EMAIL_FROM + AUTH_EMAIL_PASSWORD_SECRET_NAME, plus "
        "AUTH_OTP_ALLOWED_EMAILS and/or AUTH_OTP_ALLOWED_DOMAINS), "
        "or set ENVIRONMENT=dev with AUTH_DEV_BYPASS=true for local dev."
    )


_UNAUTHENTICATED_MESSAGE = "Not authenticated: a valid Azure AD token or DataQ API key is required."
_UNAUTHENTICATED_MESSAGE_OTP = (
    "Not authenticated: sign in with an email code, or present a DataQ API key."
)


def _get_current_user_real(
    request: Request,
    azure_user: Annotated[AzureUser | None, Security(azure_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    # DataQ PAT first, by prefix (ADR 0026 — second authenticator behind the
    # seam): a `dq_live_…` bearer is never a valid JWT, so the branches are
    # disjoint. api_key_service raises the uniform 401 on any bad key.
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    if azure_user is None:
        # auto_error=False left rejection to us: no/invalid Azure token.
        raise DataQError(
            code="unauthenticated",
            message=_UNAUTHENTICATED_MESSAGE,
            status_code=401,
        )
    aad_oid, email, display_name = _extract_claims(azure_user)
    user = _upsert_user(
        db, aad_object_id=aad_oid, email=email, display_name=display_name, oidc_issuer=_AZURE_ISSUER
    )
    log.info("auth_user_resolved", mode="real", aad_oid=aad_oid, user_id=str(user.id))
    return user


def _get_current_user_real_or_otp(
    request: Request,
    azure_user: Annotated[AzureUser | None, Security(azure_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Azure AD **and** email OTP both configured — PAT → session cookie → JWT.

    A separate function rather than a branch inside `_get_current_user_real`
    because the mode is bound at import time and the ladder below picks exactly
    one; keeping them separate means the Azure-only deployment executes no OTP
    code at all, and the two orderings are each independently readable.
    """
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    cookie = _session_token(request)
    if cookie is not None:
        # Decided here, and NOT falling through to the JWT branch on failure —
        # the same disjointness the PAT branch has. `_PatAwareAzureScheme` has
        # already short-circuited, so `azure_user` is None anyway; this is the
        # explicit statement of the contract rather than a reliance on that.
        return session_service.resolve_token(db, cookie)
    if azure_user is None:
        raise DataQError(
            code="unauthenticated",
            message=_UNAUTHENTICATED_MESSAGE_OTP,
            status_code=401,
        )
    aad_oid, email, display_name = _extract_claims(azure_user)
    user = _upsert_user(
        db, aad_object_id=aad_oid, email=email, display_name=display_name, oidc_issuer=_AZURE_ISSUER
    )
    log.info("auth_user_resolved", mode="real", aad_oid=aad_oid, user_id=str(user.id))
    return user


def _oidc_access_allowed(email: str) -> bool:
    """Whether `email` (already normalized) may hold a DataQ account via OIDC (#1385).

    Mirrors `otp_service.is_signup_eligible`, with one deliberate difference in the
    empty-allowlist case: OTP treats "no allowlist" as *nobody* (and refuses to
    boot), while this returns True — see the `oidc_allowed_emails` field comment
    in `core/config.py` for why a fail-closed default here would turn a routine
    image bump into a workspace-wide lockout.

    Checked on EVERY request, not only at first provisioning. Gating just the
    insert would leave an already-provisioned identity in place after it is
    removed from the allowlist, so the list would grant access but never revoke
    it — this way it doubles as the kill-switch for an account that should no
    longer be admitted.
    """
    # `_settings` (the module's import-time binding), not a fresh `get_settings()`
    # — every other read in this module goes through it, and one settings source
    # per module is what keeps a monkeypatched override from applying to half the
    # auth path.
    if not _settings.oidc_allowlist_configured:
        return True
    if email in _settings.oidc_allowed_email_set:
        return True
    _, _, domain = email.partition("@")
    return bool(domain) and domain in _settings.oidc_allowed_domain_set


def _resolve_generic_oidc_user(db: Session, oidc_claims: dict[str, Any]) -> User:
    """Claims -> allowlist gate -> upserted `User`, for both generic-OIDC deps.

    Deliberately ONE function called from both `_get_current_user_generic_oidc`
    and `_get_current_user_generic_oidc_or_otp`: the two dependencies had byte-
    identical bodies here, and a gate added to one and forgotten in the other
    would leave the mode that also has OTP enabled silently ungated.
    """
    subject, email, display_name = _extract_oidc_claims(oidc_claims)
    email = normalize_email(email)
    if not _oidc_access_allowed(email):
        # Deliberately 403, not 401: the token is VALID: re-authenticating would
        # loop the SPA forever. The address is not echoed back — the caller
        # already knows who they signed in as, and the log line below is where an
        # operator reads it.
        log.warning("auth_oidc_access_denied", mode="generic_oidc", email=email)
        raise DataQError(
            code="forbidden",
            message="This account is not authorized for this DataQ workspace.",
            status_code=403,
        )
    user = _upsert_user(
        db,
        aad_object_id=subject,
        email=email,
        display_name=display_name,
        oidc_issuer=_settings.oidc_issuer,
    )
    log.info("auth_user_resolved", mode="generic_oidc", user_id=str(user.id))
    return user


def _get_current_user_generic_oidc(
    request: Request,
    oidc_claims: Annotated[dict[str, Any] | None, Security(oidc_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """The generic-OIDC counterpart to `_get_current_user_real`."""
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    if oidc_claims is None:
        raise DataQError(
            code="unauthenticated",
            message=_UNAUTHENTICATED_MESSAGE,
            status_code=401,
        )
    return _resolve_generic_oidc_user(db, oidc_claims)


def _get_current_user_generic_oidc_or_otp(
    request: Request,
    oidc_claims: Annotated[dict[str, Any] | None, Security(oidc_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Generic OIDC **and** email OTP both configured — the counterpart to
    `_get_current_user_real_or_otp`."""
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    cookie = _session_token(request)
    if cookie is not None:
        return session_service.resolve_token(db, cookie)
    if oidc_claims is None:
        raise DataQError(
            code="unauthenticated",
            message=_UNAUTHENTICATED_MESSAGE_OTP,
            status_code=401,
        )
    return _resolve_generic_oidc_user(db, oidc_claims)


def _get_current_user_otp(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """OTP-only deployment (no Azure AD) — PAT → session cookie → uniform 401.

    Declares no `Security(azure_scheme)` dependency, which is the whole reason it
    exists: `_get_current_user_real` cannot serve this mode because that dependency
    is `None` here and FastAPI would still try to resolve it.
    """
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    cookie = _session_token(request)
    if cookie is not None:
        return session_service.resolve_token(db, cookie)
    raise DataQError(
        code="unauthenticated",
        message=_UNAUTHENTICATED_MESSAGE_OTP,
        status_code=401,
    )


def _get_current_user_dev_bypass(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    # PATs resolve in dev bypass too (same seam order as real mode), so the
    # local stack can exercise the full PAT lifecycle without Azure.
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    # A session cookie resolves too — but, unlike the PAT branch above, an
    # UNUSABLE one falls through to the bypass user instead of 401ing.
    #
    # The asymmetry is deliberate. Dev bypass is not an authenticator: its entire
    # contract is "no credential is required", so refusing a request because of a
    # credential nobody had to present is pure friction with no security value —
    # the next request without the cookie is admitted anyway. The concrete case is
    # a developer who ran an OTP-configured stack, kept the cookie, and switched
    # the env back: they would otherwise be locked out of their own machine until
    # they cleared browser storage. (The PAT branch keeps its 401 because #461's
    # tests pin it and because a presented PAT is an explicit act.)
    cookie = _session_token(request)
    if cookie is not None:
        try:
            return session_service.resolve_token(db, cookie)
        except session_service.SessionAuthError:
            log.debug("dev_bypass_ignoring_unusable_session_cookie")
    user = _upsert_user(
        db,
        aad_object_id=DEV_BYPASS_AAD_OID,
        email=DEV_BYPASS_EMAIL,
        display_name=DEV_BYPASS_DISPLAY_NAME,
    )
    log.debug("auth_user_resolved", mode="dev_bypass", user_id=str(user.id))
    return user


def _get_current_user_unconfigured() -> User:
    # init_auth will have raised at startup; this is defence-in-depth.
    raise DataQError(
        code="auth_not_configured",
        message="Authentication is not configured for this environment.",
        status_code=503,
    )


get_current_user: Callable[..., User]
if azure_scheme is not None:
    get_current_user = _get_current_user_real_or_otp if _otp_enabled else _get_current_user_real
elif oidc_scheme is not None:
    get_current_user = (
        _get_current_user_generic_oidc_or_otp if _otp_enabled else _get_current_user_generic_oidc
    )
elif _otp_enabled:
    get_current_user = _get_current_user_otp
elif _dev_bypass_allowed(_settings):
    get_current_user = _get_current_user_dev_bypass
else:
    get_current_user = _get_current_user_unconfigured


def is_workspace_admin(user: User) -> bool:
    """True iff the user is in the workspace-admin allowlist (WORKSPACE_ADMIN_EMAILS).

    Workspace admin is a single config-driven set — the whole-workspace
    administrator, distinct from the per-suite view/edit/admin/owner ladder in
    `suite_authz`. Matched case-insensitively on the IdP-supplied email, a
    generic identity attribute, so no Azure/Entra claim is read here
    (ADR 0010/0013, CLAUDE.md §11). Resolves the allowlist via `get_settings()`
    (not the import-time `_settings` singleton) so a test can vary it with
    `get_settings.cache_clear()`; in a running process settings are read once at
    startup (12-factor — change the env and restart).
    """
    return get_settings().is_admin_email(user.email)


def require_workspace_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """FastAPI dependency gating the /admin endpoints — 403 for a non-admin.

    Server-side authz (never a client toggle): a non-admin gets a real 403, which
    the frontend renders as the forbidden page.
    """
    if not is_workspace_admin(current_user):
        raise DataQError(
            code="workspace_admin_required",
            message="This action requires workspace-admin access.",
            status_code=403,
        )
    return current_user
