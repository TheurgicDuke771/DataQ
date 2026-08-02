"""Email OTP sign-in endpoints — ADR 0032 (#734).

    POST /api/v1/auth/otp/request   unauthenticated — mint + mail a code
    POST /api/v1/auth/otp/verify    unauthenticated — code → session cookie
    POST /api/v1/auth/logout        cookie-authenticated — revoke + clear

All three sit under `/api/v1/auth/`, which the rate limiter's `auth` class covers
with a strict per-IP cap checked *before* the bearer branch (#1127); the per-email
counters live in `otp_service` because the middleware cannot see a request body.

## Two security decisions worth reading before changing anything here

**1. The uniform response, and the one place it is not uniform.** `otp/request`
answers `{"status": "ok"}` for an eligible address, an ineligible one, and a
throttled one — byte-identical bodies and status codes, asserted by tests. That is
ADR 0032 decision 4's anti-enumeration property, and it is why the endpoint does
not report whether mail was sent.

The exception: an eligible address whose SMTP submission genuinely FAILS gets a
502/503. That does leak eligibility — but only while the mail server is broken,
and issue #734's acceptance criteria are explicit that a send failure must surface
a real error and a structured log rather than a quiet no-op. Weighed: a user told
"check your email" when nothing was sent has no way to distinguish a slow relay
from a dead one and will retry forever, and a mail outage is an operator-visible,
deployment-wide condition, not a per-address secret. The steady-state property —
which is what the ADR argues for — is unaffected: with a working mailer the three
outcomes are indistinguishable.

**2. Throttled returns success, not 429.** When an address exceeds its per-email
quota the response is the same `{"status": "ok"}`. A 429 here would be a perfect
enumeration oracle: an attacker who wants to know whether `ada@acme.io` is
allow-listed needs only to send four requests and watch for the throttle, because
an ineligible address is never counted at all. (The middleware's per-IP 429 is a
different thing and stays — it is keyed on the caller, not on the address, so it
reveals nothing about who is in the workspace.)

## CSRF

The cookie is `SameSite=Lax`, so a cross-site form POST cannot carry it, and every
cookie-authenticated mutation in DataQ is POST/PATCH/DELETE — an invariant a test
pins over the whole route table, because Lax's protection evaporates the moment a
GET mutates. Login-CSRF on `verify` (an attacker completing a sign-in *as
themselves* in the victim's browser) is bounded by the same Lax rule plus the
same-origin nginx proxy: the SPA and the API share an origin, so a cross-site POST
to `/api/v1/auth/verify` neither carries nor can read the resulting cookie.
`logout` is POST-only for the same reason, and is idempotent so a forced logout is
a nuisance, not a state corruption.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.api.v1.me import MeResponse
from backend.app.core.auth import is_workspace_admin
from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.session import get_db
from backend.app.services import otp_service, session_service
from backend.app.services.otp_mailer import OtpMailer

router = APIRouter(tags=["auth"])

log = get_logger(__name__)

#: The one body every non-error `otp/request` returns. A module constant so the
#: "byte-identical across eligibility branches" property is structural rather than
#: three literals that could drift apart.
_UNIFORM_REQUEST_RESPONSE = {"status": "ok"}


class OtpRequest(ApiModel):
    # 320 = the RFC-bounded maximum (64 local + @ + 255 domain). A cap, not
    # validation: the point is that an oversized payload is a 422 from the
    # framework rather than something the service has to hash and store.
    email: str = Field(min_length=3, max_length=320, description="Your email address")


class OtpVerify(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    # Not `pattern=r"^\d{6}$"`. A 422 for a non-numeric code and a 401 for a wrong
    # one would tell a caller the code's SHAPE, and more importantly the two
    # responses differ in a way that has nothing to do with whether they guessed
    # right. Constant-time comparison against the stored hash handles any string;
    # the cap is only there to bound what we hash.
    code: str = Field(min_length=1, max_length=32, description="The 6-digit code from your email")


class OtpRequestAck(ApiModel):
    status: str = Field(description="Always 'ok' — never reveals whether mail was sent")


def _require_otp_enabled(settings: Settings) -> None:
    if not settings.otp_auth_configured:
        raise otp_service.OtpNotConfiguredError()


def _cookie_secure(request: Request, settings: Settings) -> bool:
    """Whether to mark the session cookie `Secure`.

    `AUTH_SESSION_COOKIE_SECURE` wins when set. Otherwise infer from
    `X-Forwarded-Proto` — the only HTTPS signal that survives DataQ's nginx proxy
    (ADR 0028 §5), which terminates TLS and speaks plain HTTP upstream — falling
    back to the request's own scheme for a direct connection.

    Hard-coding `Secure=True` is the single most likely dev-vs-prod footgun in this
    feature: the browser accepts the `Set-Cookie` silently and then never sends the
    cookie back over plain HTTP, so local dev sees a successful sign-in followed by
    a 401 on the very next request, with nothing in any log to explain it.
    """
    if settings.auth_session_cookie_secure is not None:
        return settings.auth_session_cookie_secure
    forwarded = request.headers.get("x-forwarded-proto", "")
    # A proxy chain can append: `https, http`. The CLIENT-facing hop is the first.
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


@router.post(
    "/auth/otp/request",
    response_model=OtpRequestAck,
    status_code=status.HTTP_200_OK,
    summary="Request an email sign-in code",
)
def request_otp(
    payload: OtpRequest,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OtpRequestAck:
    """Email a one-time sign-in code, if the address is eligible.

    **The response is identical whether or not it is** — eligible, not
    allow-listed, or over its request quota all answer `{"status": "ok"}` and
    nothing else. Only a real mail-transport failure produces a different status.
    """
    _require_otp_enabled(settings)
    mailer = OtpMailer(secret_store, settings)
    outcome = otp_service.request_code(db, payload.email, mailer=mailer, settings=settings)
    # Logged, never returned. The endpoint's whole job is to be uninformative;
    # the operator still needs to know which branch ran.
    log.info("otp_request_handled", outcome=outcome.reason)
    return OtpRequestAck(**_UNIFORM_REQUEST_RESPONSE)


@router.post(
    "/auth/otp/verify",
    response_model=MeResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange an email code for a session cookie",
)
def verify_otp(
    payload: OtpVerify,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MeResponse:
    """Verify the code, mint a session, and set the `dataq_session` cookie.

    Returns the same shape as `GET /me`, so the SPA can render immediately without
    a second round trip. The token itself is never in the body — only in the
    HttpOnly cookie, which is what keeps it out of JS-readable storage.
    """
    _require_otp_enabled(settings)
    user = otp_service.verify_code(db, payload.email, payload.code, settings=settings)
    _, token = session_service.create_session(db, user, settings=settings)
    response.set_cookie(
        session_service.COOKIE_NAME,
        token,
        max_age=settings.auth_session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request, settings),
        # Path=/ explicitly: nginx passes Set-Cookie through unmodified (no
        # `proxy_cookie_path`), and the API lives under /api while the SPA's
        # routes do not — a path-scoped cookie would be sent to one and not the
        # other. Domain is deliberately UNSET: the proxy forwards the upstream
        # Host, so deriving a Domain from it would scope the cookie to an
        # internal hostname the browser never saw.
        path="/",
    )
    resp = MeResponse.model_validate(user)
    resp.is_workspace_admin = is_workspace_admin(user)
    return resp


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out — revoke the session and clear the cookie",
)
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Revoke the presented session and clear the cookie. Idempotent.

    Deliberately NOT behind `get_current_user`: logging out must work when the
    session is already expired or unknown, and a 401 there would leave a stale
    cookie in the browser forever. Revocation is enforced at the seam
    (`session_service.resolve_token`), so the very next request with this cookie
    is a uniform 401 even if the browser kept it.
    """
    token = request.cookies.get(session_service.COOKIE_NAME)
    if token and token.startswith(session_service.TOKEN_PREFIX):
        session_service.revoke(db, token)
    # Cleared unconditionally — including for a token we did not recognise, so a
    # malformed cookie cannot wedge a browser into permanent 401s.
    response.delete_cookie(
        session_service.COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request, settings),
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
