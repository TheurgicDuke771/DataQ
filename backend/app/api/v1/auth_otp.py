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
ADR 0032 decision 4's anti-enumeration property scoped to what a caller sees *in
the response body/status*, and it is why the endpoint does not report whether mail
was sent.

Two things the content-level uniformity does NOT cover, both deliberate and both
kept honest here rather than overclaimed:

- **Response *latency* is floored, not equalized (#1137).** The eligible path does a
  Redis counter check, two DB writes and a synchronous SMTP handshake that the
  ineligible path (one in-memory set lookup) skips, so raw timing used to separate
  members at a glance. Every uniform branch is now padded to
  `AUTH_OTP_REQUEST_MIN_SECONDS` (default 1s), measured from handler entry on a
  monotonic clock, sleeping the REMAINDER — which collapses the microseconds-vs-
  hundreds-of-milliseconds gulf into one indistinguishable floor.
  What this does **not** claim: a send SLOWER than the floor still overruns it, so
  a degraded relay (up to `AUTH_EMAIL_TIMEOUT_SECONDS`, default 5s) re-exposes a
  narrower version of the same channel, and `AUTH_OTP_REQUEST_MIN_SECONDS=0` turns
  it off entirely. The floor raises the sample count an attacker needs from a
  handful to a statistical exercise against network jitter; it is not a
  constant-time guarantee.
- **A real SMTP failure on an eligible address gets a 502/503** where a working
  send would have been `{"status": "ok"}`. That leaks eligibility while the mail
  server is broken — but issue #734's acceptance criteria are explicit that a send
  failure must surface a real error and a structured log rather than a quiet no-op.
  Weighed: a user told "check your email" when nothing was sent cannot tell a slow
  relay from a dead one and retries forever, and a mail outage is an
  operator-visible, deployment-wide condition, not a per-address secret. With a
  working mailer the three outcomes are indistinguishable at the body/status level.
- **The floor covers `otp/request` only.** `otp/verify` answers a uniform 401 for
  every failure mode, but an address with a live code costs an `UPDATE … RETURNING`
  plus a commit that an address with none never pays — the same channel, a few
  milliseconds wide instead of hundreds. Tracked in
  [#1141](https://github.com/TheurgicDuke771/DataQ/issues/1141) with the options and
  the trade; do not read the `request` floor as covering it.

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

import time
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


def _floor_remainder(started: float, floor_seconds: float, *, now: float) -> float:
    """How much longer this response must be held to reach the floor.

    The REMAINDER, never a fixed pad: sleeping a constant after variable work just
    shifts the distribution and leaves the difference intact — an eligible request
    that spent 400ms would still answer 400ms later than an ineligible one that
    spent none. Clamped at zero, so work that already overran the floor (a slow
    relay) is never "sped up" and never sleeps a negative.
    """
    return max(0.0, floor_seconds - (now - started))


def _hold_until_floor(started: float, settings: Settings) -> None:
    """Pad a uniform `otp/request` response out to `AUTH_OTP_REQUEST_MIN_SECONDS`.

    `time.sleep` is correct HERE and would be a bug one line up the stack: the
    endpoint is a **sync `def`**, so Starlette runs it in the threadpool and this
    blocks one worker thread, not the event loop. If this route is ever made
    `async def`, this must become `await asyncio.sleep(...)` — a `time.sleep` on the
    loop would stall every other request in the process. (The threadpool cost is
    bounded by the middleware's strict `auth` per-IP class, #1127.)

    `time.monotonic`, not `time.time`: an NTP step mid-request would otherwise
    compute a negative or wildly long remainder from a wall clock that moved.
    """
    remaining = _floor_remainder(
        started, settings.auth_otp_request_min_seconds, now=time.monotonic()
    )
    if remaining > 0:
        time.sleep(remaining)


def _cookie_secure(request: Request, settings: Settings) -> bool:
    """Whether to mark the session cookie `Secure`.

    `AUTH_SESSION_COOKIE_SECURE` wins when set. Otherwise infer from
    `X-Forwarded-Proto` — the only HTTPS signal that reaches the api, which sits on
    internal ingress behind the frontend nginx proxy (ADR 0028 §5) — falling back to
    the request's own scheme for a direct connection.

    Inference is exactly as trustworthy as the proxy chain, which is not theoretical:
    the reference nginx originally sent `X-Forwarded-Proto: $scheme`, and since TLS
    terminates at the platform edge rather than at nginx, `$scheme` is
    deterministically `http` there — so `proxy_set_header` REPLACED the edge's
    correct `https` and this function returned False on a live HTTPS deployment
    (#1138, fixed by forwarding the edge's header). Behind a *different* proxy,
    verify the same or set `AUTH_SESSION_COOKIE_SECURE=true`, which skips the header.

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
    allow-listed, or over its request quota all answer `{"status": "ok"}` after the
    same minimum elapsed time. Only a real mail-transport failure produces a
    different status.
    """
    # Started FIRST, before any branch-dependent work, so the floor covers the
    # whole handler rather than whatever is left after the expensive part.
    started = time.monotonic()
    _require_otp_enabled(settings)
    mailer = OtpMailer(secret_store, settings)
    outcome = otp_service.request_code(db, payload.email, mailer=mailer, settings=settings)
    # Logged, never returned. The endpoint's whole job is to be uninformative;
    # the operator still needs to know which branch ran.
    log.info("otp_request_handled", outcome=outcome.reason)
    # Every branch that reaches here — sent, ineligible, throttled — is padded to
    # the same floor (#1137). ERROR responses deliberately are NOT: a raise from
    # `_require_otp_enabled` (503) or from the mailer (502/503) exits above this
    # line, and those responses are already non-uniform by design — a mail outage
    # is a deployment-wide, operator-visible condition, not a per-address secret
    # (see this module's docstring). Padding them would buy nothing and would hold
    # a worker thread through an outage.
    _hold_until_floor(started, settings)
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
