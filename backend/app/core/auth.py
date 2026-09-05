"""Auth seam: DataQ PAT · email-OTP session cookie · Azure AD / generic OIDC token."""

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
from backend.app.core.identity import identity_log_fields, normalize_email
from backend.app.core.logging import get_logger
from backend.app.core.roles import (
    ADMIN_ROLE,
    DEFAULT_WORKSPACE_ROLE,
    ROLE_RANK,
    admin_promotion_values,
    bootstrap_role,
    is_workspace_admin,
    resolve_role,
)
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import api_key_service, membership_service, session_service

log = get_logger(__name__)

DEV_BYPASS_AAD_OID = "00000000-0000-0000-0000-000000000001"
DEV_BYPASS_EMAIL = "dev-bypass@dataq.local"
DEV_BYPASS_DISPLAY_NAME = "Dev Bypass User"


def _dev_bypass_allowed(settings: Settings) -> bool:
    return settings.dev_bypass_allowed


class _PatAwareAzureScheme(SingleTenantAzureAuthorizationCodeBearer):
    """Azure scheme that short-circuits DataQ PATs and OTP session cookies (#849)."""

    async def __call__(
        self, request: HTTPConnection, security_scopes: SecurityScopes
    ) -> AzureUser | None:
        # `HTTPConnection`, not `Request` — a WebSocket route hands a WebSocket.
        if _pat_token(request) is not None:
            return None
        # OTP short-circuit, gated on `_otp_enabled`: ungated, a junk `dataq_session` cookie would
        # disable JWT validation on an Azure-only deployment — an auth bypass vector.
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
        # auto_error=False: failed validation yields None and get_current_user
        # rejects — required so a PAT bearer isn't force-rejected here (ADR 0026).
        auto_error=False,
    )


def _json_dict(response: httpx.Response) -> dict[str, Any]:
    """The body as a parsed JSON object, or `ValueError`."""
    body = response.json()  # raises json.JSONDecodeError (a ValueError)
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object body")
    return body


def discover_oidc_config(issuer: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch an OIDC issuer's discovery document (parsed, unvalidated)."""
    response = httpx.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration", timeout=timeout)
    response.raise_for_status()
    return _json_dict(response)


def discover_jwks_uri(issuer: str, *, timeout: float = 5.0) -> str:
    """Resolve `jwks_uri` — the one REQUIRED endpoint — from OIDC discovery."""
    return str(discover_oidc_config(issuer, timeout=timeout)["jwks_uri"])


# ── userinfo fallback (#1346) ──────────────────────────────────────────────── Cognito access
# tokens carry no `email`/`name`.

_USERINFO_TTL_SECONDS = 300.0
_USERINFO_CACHE_MAX = 1024
_userinfo_cache: dict[str, tuple[float, dict[str, Any]]] = {}
#: Discovery documents are static per issuer for a process lifetime.
_discovery_cache: dict[str, dict[str, Any]] = {}
#: One lock for both caches — the REST validator (event-loop thread) and MCP
#: tools (worker threads) both mutate them.
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
    """`discover_oidc_config`, memoized per issuer for the process lifetime."""
    with _oidc_cache_lock:
        cached = _discovery_cache.get(issuer)
    if cached is not None:
        return cached
    config = discover_oidc_config(issuer, timeout=timeout)
    with _oidc_cache_lock:
        _discovery_cache[issuer] = config
    return config


def _merge_userinfo(claims: dict[str, Any], userinfo: dict[str, Any]) -> dict[str, Any] | None:
    """`claims` with `email`/`name` filled from `userinfo`, or None on sub mismatch
    (OIDC Core §5.3.2 — a mismatched `sub` describes someone else; the sign-in
    must fail rather than mint a user row from mixed identities).
    """
    if userinfo.get("sub") != claims.get("sub"):
        return None
    for claim in ("email", "name"):
        if not claims.get(claim) and userinfo.get(claim):
            claims[claim] = userinfo[claim]
    return claims


def fetch_userinfo(issuer: str, token: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """The issuer's userinfo response for `token` — sync, for `mcp.auth` (#1346)."""
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
    """Provider-neutral OIDC bearer validator (ADR 0026 amendment)."""

    def __init__(self, issuer: str, audience: str, *, timeout: float = 5.0) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._client = httpx.AsyncClient(timeout=timeout)
        self._jwks_uri: str | None = None
        self._jwks_cache: dict[str, Any] | None = None
        self._userinfo_endpoint: str | None = None

    async def load_config(self) -> None:
        """Resolve discovery endpoints and pre-warm the JWKS cache."""
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
                # Unknown kid — the provider may have rotated its signing keys.
                # Refresh once and retry before giving up.
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
                # Audience is checked manually below — see the Cognito note.
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None
        aud = claims.get("aud")
        aud_values = aud if isinstance(aud, list) else [aud] if aud else []
        # Standard OIDC puts the client id in `aud`; a Cognito ACCESS token (what the SPA sends)
        # carries it as `client_id` instead.
        if self._audience not in aud_values and claims.get("client_id") != self._audience:
            return None
        # #1346: fill a missing `email` from userinfo.
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
                    # Non-object 200 → same fail-closed outcome as an outage,
                    # never a 500 (#567 class).
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
    """Raw bearer from the Authorization header, if any. Takes `HTTPConnection`
    (the common base of `Request` and `WebSocket`); only `.headers` is read.
    """
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
    """The OTP session token from the cookie, if shaped like one. Prefix-checked
    to keep the authenticators disjoint — a cookie set by something else on the
    same origin must not steer a request into the session branch.
    """
    token = request.cookies.get(session_service.COOKIE_NAME)
    if token and token.startswith(session_service.TOKEN_PREFIX):
        return token
    return None


_settings = get_settings()
azure_scheme: SingleTenantAzureAuthorizationCodeBearer | None = _build_azure_scheme(_settings)
#: Deterministic Azure AD v2 issuer, written to `users.oidc_issuer` on every
#: real-mode login — the same coordinates fastapi_azure_auth validates against.
_AZURE_ISSUER: str | None = (
    f"https://login.microsoftonline.com/{_settings.azure_tenant_id}/v2.0"
    if _settings.azure_tenant_id
    else None
)
#: Mutually exclusive with `azure_scheme` — exactly one of the two is non-None.
oidc_scheme: OidcBearerScheme | None = _build_oidc_scheme(_settings)
#: Read once at import — the whole mode ladder is bound at import time
#: (12-factor: change the env and restart).
_otp_enabled: bool = _settings.otp_auth_configured


class IdentityConflictError(DataQError):
    """Two identities claim one mailbox (`uq_users_email_lower`, #735)."""

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
    role: str | None = None,
) -> User | None:
    """Attach an AAD identity to an existing OTP-provisioned row, or None."""
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
            # Promote-only: the stored role is authoritative; only the allowlist may raise it.
            **({"role": role} if role is not None else admin_promotion_values(email)),
        )
        .returning(User)
    ).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        return None
    db.commit()
    # No email in the log line — the object id is enough to correlate.
    log.info("identity_linked_otp_row_to_aad", aad_object_id=aad_object_id, user_id=str(claimed.id))
    return claimed


def _upsert_user(
    db: Session,
    *,
    aad_object_id: str,
    email: str,
    display_name: str | None,
    oidc_issuer: str | None = None,
    role: str | None = None,
    env_allowed: bool = False,
    _retrying: bool = False,
) -> User:
    """Upsert the user this sign-in identifies, keyed on `aad_object_id`.

    Choke point 1 of ADR 0043 decision 4: Azure AD and generic OIDC, REST and
    /mcp, plus the dev-bypass mint (exempt inside the check).
    """
    # `_settings`, not `get_settings()`: the gate must read the same Settings the
    # auth ladder itself bound, or the two can disagree about the mode.
    membership_service.require_member(
        db, email, door="upsert_user", env_allowed=env_allowed, settings=_settings
    )
    now = datetime.now(UTC)
    stmt = (
        insert(User)
        .values(
            aad_object_id=aad_object_id,
            oidc_issuer=oidc_issuer,
            email=email,
            display_name=display_name,
            # Seeds a NEW row only (the conflict branch is promote-only); the
            # allowlist write-through still wins inside `bootstrap_role`.
            role=role
            or bootstrap_role(
                email,
                # A pre-provisioned `initial_role` (ADR 0043 decision 9) seeds the
                # signup default HERE, in `values()`, and nowhere else: the
                # `on_conflict_do_update` branch below stays promote-only, so a
                # later in-app role change is never overwritten by a sign-in.
                default=membership_service.initial_role_for(db, email)
                or get_settings().auth_oidc_default_role,
            ),
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["aad_object_id"],
            set_={
                "email": email,
                "oidc_issuer": oidc_issuer,
                # Promote-only, and OMITTED entirely when not allowlisted — this
                # per-request upsert must never race an in-app role change.
                **({"role": role} if role is not None else admin_promotion_values(email)),
                # Branches on `display_name_override` (#1139): this upsert runs on EVERY real-mode
                # request.
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
        # `aad_object_id` conflicts are handled above, so this is a different constraint — in
        # practice `uq_users_email_lower`.
        db.rollback()
        # Benign reading first: the colliding row is an OTP identity for the
        # same human (ADR 0032 decision 6) — link it, don't reject.
        if not _retrying:
            linked = _claim_unlinked_user(
                db,
                aad_object_id=aad_object_id,
                email=email,
                display_name=display_name,
                now=now,
                oidc_issuer=oidc_issuer,
                role=role,
            )
            if linked is not None:
                return linked
            # Nothing to claim: a different oid (real conflict) or a concurrent
            # sign-in won the claim — one bounded retry distinguishes the two.
            return _upsert_user(
                db,
                aad_object_id=aad_object_id,
                email=email,
                display_name=display_name,
                oidc_issuer=oidc_issuer,
                role=role,
                env_allowed=env_allowed,
                _retrying=True,
            )
        # No email in the message or the log: the message travels in the HTTP error envelope
        # (untouched by the redactor).
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
    """Generic-OIDC counterpart to `_extract_claims`. Bare `claims["sub"]` on
    purpose: a token missing the REQUIRED subject claim must fail loudly, not
    silently key a row on an empty string.
    """
    subject = str(claims["sub"])
    email = str(claims.get("email") or "")
    display_name_raw = claims.get("name")
    display_name = str(display_name_raw) if display_name_raw is not None else None
    return subject, email, display_name


def _log_otp_mode_ready() -> None:
    """Announce OTP mode WITHOUT logging a single address — count + domains only
    (the allowlist is a workspace member list).
    """
    log.info(
        "auth_otp_mode_ready",
        allowed_email_count=len(_settings.auth_otp_allowed_email_set),
        allowed_domains=sorted(_settings.auth_otp_allowed_domain_set),
        session_ttl_hours=_settings.auth_session_ttl_hours,
        smtp_host=_settings.auth_email_smtp_host,
    )


async def init_auth() -> None:
    """App-startup wiring (FastAPI lifespan). Fail-closed (ADR 0032 decision 2):
    a deployment must never come up healthy while unable to log anybody in.
    Partial OTP configs are already rejected by `Settings._validate_otp_auth`.
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
        # Real + OTP may both be on at once (ADR 0032 decision 1).
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
            # The open state is the deliberate default (fail-closed would lock a workspace out on
            # upgrade), so it must be LOUD: with no app-side gate.
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


def unauthenticated_message(settings: Settings | None = None) -> str:
    """The 401 body for the configured auth ladder (#1736): names only the credential
    kinds THIS deployment accepts, derived from the same `Settings` properties the
    ladder binds `get_current_user` on — so a Cognito or OTP stack never steers a
    caller toward an Azure AD token it cannot mint.
    """
    s = settings or _settings
    accepted: list[str] = []
    if s.azure_auth_configured:
        accepted.append("a valid Azure AD sign-in token")
    elif s.generic_oidc_configured:
        accepted.append("a valid sign-in token from your identity provider")
    if s.otp_auth_configured:
        accepted.append("a signed-in session (email code)")
    accepted.append(f"a DataQ API key ({api_key_service.TOKEN_PREFIX}…)")
    if len(accepted) == 1:
        listed = accepted[0]
    elif len(accepted) == 2:
        listed = f"{accepted[0]} or {accepted[1]}"
    else:
        listed = ", ".join(accepted[:-1]) + f", or {accepted[-1]}"
    return f"Not authenticated: {listed} is required."


#: Bound once at import like the rest of the ladder; every 401 below reads it.
_UNAUTHENTICATED_MESSAGE = unauthenticated_message(_settings)


def _get_current_user_real(
    request: Request,
    azure_user: Annotated[AzureUser | None, Security(azure_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    # PAT first, by prefix (ADR 0026): a `dq_live_` bearer is never a valid JWT,
    # so the branches are disjoint; api_key_service raises the uniform 401.
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
    """Azure AD and email OTP both configured — PAT → session cookie → JWT.
    A separate function so the Azure-only deployment executes no OTP code.
    """
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    cookie = _session_token(request)
    if cookie is not None:
        # Decided here; never falls through to the JWT branch on failure — the
        # same disjointness the PAT branch has.
        return session_service.resolve_token(db, cookie)
    if azure_user is None:
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


def _oidc_access_allowed(email: str, settings: Settings | None = None) -> bool:
    """Whether `email` (already normalized) may hold a DataQ account via OIDC (#1386)."""
    s = settings or _settings
    if not s.oidc_allowlist_configured:
        return True
    if email in s.oidc_allowed_email_set:
        return True
    _, _, domain = email.partition("@")
    return bool(domain) and domain in s.oidc_allowed_domain_set


def _oidc_allowlist_grants(email: str, settings: Settings | None = None) -> bool:
    """Whether an EXPLICIT allowlist entry names `email`.

    Distinct from `_oidc_access_allowed`, which is also True when no allowlist is
    configured at all. An open door is today's behaviour, not a grant — treating
    it as one would make membership enforcement a no-op on exactly the
    deployments that have no app-side gate today.
    """
    s = settings or _settings
    return s.oidc_allowlist_configured and _oidc_access_allowed(email, s)


def _denied_identity(email: str) -> dict[str, str]:
    """Log fields naming a REJECTED identity without logging the address."""
    return identity_log_fields(email)


def _resolve_generic_oidc_user(db: Session, oidc_claims: dict[str, Any]) -> User:
    """Claims → allowlist gate → upserted `User` — ONE function for both
    generic-OIDC deps, so a gate added to one cannot be forgotten in the other.
    """
    subject, email, display_name = _extract_oidc_claims(oidc_claims)
    email = normalize_email(email)
    # The allowlist is grant-only (ADR 0043 decision 7): while `workspace_members`
    # is empty this is byte for byte the old rule, and once it is populated the
    # table can admit somebody the allowlist does not name.
    env_allowed = _oidc_allowlist_grants(email)
    if not membership_service.is_member(
        db,
        email,
        env_allowed=env_allowed,
        unmanaged_default=_oidc_access_allowed(email),
        settings=_settings,
    ):
        # 403, not 401: the token is VALID, so re-authenticating would loop the SPA forever.
        log.warning("auth_oidc_access_denied", mode="generic_oidc", **_denied_identity(email))
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
        env_allowed=env_allowed,
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
    """Generic OIDC and email OTP both configured — the counterpart to
    `_get_current_user_real_or_otp`.
    """
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    cookie = _session_token(request)
    if cookie is not None:
        return session_service.resolve_token(db, cookie)
    if oidc_claims is None:
        raise DataQError(
            code="unauthenticated",
            message=_UNAUTHENTICATED_MESSAGE,
            status_code=401,
        )
    return _resolve_generic_oidc_user(db, oidc_claims)


def _get_current_user_otp(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """OTP-only deployment (no Azure AD) — PAT → session cookie → uniform 401.
    Declares no `Security(azure_scheme)` dependency: the scheme is None here and
    FastAPI would still try to resolve it.
    """
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    cookie = _session_token(request)
    if cookie is not None:
        return session_service.resolve_token(db, cookie)
    raise DataQError(
        code="unauthenticated",
        message=_UNAUTHENTICATED_MESSAGE,
        status_code=401,
    )


def _get_current_user_dev_bypass(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    # PATs resolve in dev bypass too — same seam order as real mode, so the
    # local stack can exercise the full PAT lifecycle without Azure.
    pat = _pat_token(request)
    if pat is not None:
        return api_key_service.resolve_token(db, pat)
    # Unlike the PAT branch, an UNUSABLE cookie falls through to the bypass user instead of 401ing:
    # dev bypass's contract is "no credential required".
    cookie = _session_token(request)
    if cookie is not None:
        try:
            return session_service.resolve_token(db, cookie)
        except session_service.SessionAuthError:
            log.debug("dev_bypass_ignoring_unusable_session_cookie")
    # The bypass identity is the workspace ADMIN (ADR 0033 / #741): a single-operator mode, and
    # `member` would leave the local/eval stacks unable to create a connection at all.
    user = _upsert_user(
        db,
        aad_object_id=DEV_BYPASS_AAD_OID,
        email=DEV_BYPASS_EMAIL,
        display_name=DEV_BYPASS_DISPLAY_NAME,
        role=ADMIN_ROLE,
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


def require_workspace_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Gate the /admin endpoints — 403 for a non-admin. Server-side authz,
    never a client toggle.
    """
    if not is_workspace_admin(current_user):
        raise DataQError(
            code="workspace_admin_required",
            message="This action requires workspace-admin access.",
            status_code=403,
        )
    return current_user


def require_role(minimum: str) -> Callable[..., User]:
    """Build a FastAPI dependency requiring at least `minimum` workspace role."""
    if minimum not in ROLE_RANK:
        raise ValueError(
            f"unknown workspace role: {minimum!r}"
        )  # pragma: no cover — programmer error

    def dependency(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        role = resolve_role(current_user)
        if ROLE_RANK[role] < ROLE_RANK[minimum]:
            raise DataQError(
                code="workspace_role_required",
                message=f"This action requires the '{minimum}' workspace role or higher.",
                status_code=403,
                detail={"have": role, "need": minimum},
            )
        return current_user

    return dependency


#: The two role gates the API layer uses (ADR 0033, #741) — aliases so a gate reads at the
#: signature.
AdminUser = Annotated[User, Depends(require_role(ADMIN_ROLE))]
MemberUser = Annotated[User, Depends(require_role(DEFAULT_WORKSPACE_ROLE))]
