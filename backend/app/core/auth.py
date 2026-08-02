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

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Request, Security
from fastapi.security import SecurityScopes
from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer
from fastapi_azure_auth.user import User as AzureUser
from sqlalchemy import func, update
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

    `display_name` is a `COALESCE`, not a plain overwrite (#1139): the row being
    claimed is exactly the OTP-provisioned shape — email only, so the incoming
    AAD claim is a good first name to seed. But if the person already set one
    via `PATCH /me` before ever signing in through Azure AD, linking must not
    silently discard it.
    """
    claimed = db.execute(
        update(User)
        .where(
            func.lower(User.email) == normalize_email(email),
            User.aad_object_id.is_(None),
        )
        .values(
            aad_object_id=aad_object_id,
            email=email,
            display_name=func.coalesce(User.display_name, display_name),
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
    _retrying: bool = False,
) -> User:
    now = datetime.now(UTC)
    stmt = (
        insert(User)
        .values(
            aad_object_id=aad_object_id,
            email=email,
            display_name=display_name,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["aad_object_id"],
            set_={
                "email": email,
                # COALESCE, not a plain overwrite (#1139): this upsert runs on
                # EVERY real-mode request (there is no session cache — the JWT
                # is re-validated and re-claimed each time), so a bare overwrite
                # would silently revert a `PATCH /me` display-name override back
                # to the AAD token's `name` claim on the user's very next
                # request. The claim still seeds the field the first time a row
                # is created (`.values()` above, the INSERT branch) — this only
                # protects an already-populated value from being re-synced away.
                "display_name": func.coalesce(User.display_name, display_name),
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
            tenant_id=_settings.azure_tenant_id,
            client_id=_settings.azure_api_client_id,
            scope=_settings.azure_api_scope_uri,
        )
        # Both may be on at once (ADR 0032 decision 1's "real + otp"): AAD for the
        # org's own identities, OTP for the people it has no directory entry for.
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
    user = _upsert_user(db, aad_object_id=aad_oid, email=email, display_name=display_name)
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
    user = _upsert_user(db, aad_object_id=aad_oid, email=email, display_name=display_name)
    log.info("auth_user_resolved", mode="real", aad_oid=aad_oid, user_id=str(user.id))
    return user


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
