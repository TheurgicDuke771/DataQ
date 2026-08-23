"""Email OTP sign-in endpoints — ADR 0032 (#734)."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.api.v1.me import MeResponse
from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.core.roles import is_workspace_admin, resolve_role
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.session import get_db
from backend.app.services import otp_service, session_service
from backend.app.services.otp_mailer import OtpMailer

router = APIRouter(tags=["auth"])

log = get_logger(__name__)

#: The one body every non-error `otp/request` returns.
_UNIFORM_REQUEST_RESPONSE = {"status": "ok"}


class OtpRequest(ApiModel):
    # 320 = the RFC-bounded maximum (64 local + @ + 255 domain).
    email: str = Field(min_length=3, max_length=320, description="Your email address")


class OtpVerify(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    # Not `pattern=r"^\d{6}$"`.
    code: str = Field(min_length=1, max_length=32, description="The 6-digit code from your email")


class OtpRequestAck(ApiModel):
    status: str = Field(description="Always 'ok' — never reveals whether mail was sent")


def _require_otp_enabled(settings: Settings) -> None:
    if not settings.otp_auth_configured:
        raise otp_service.OtpNotConfiguredError()


def _floor_remainder(started: float, floor_seconds: float, *, now: float) -> float:
    """How much longer this response must be held to reach the floor."""
    return max(0.0, floor_seconds - (now - started))


def _hold_until_floor(started: float, floor_seconds: float) -> None:
    """Pad a uniform response out to `floor_seconds` measured from `started`."""
    remaining = _floor_remainder(started, floor_seconds, now=time.monotonic())
    if remaining > 0:
        time.sleep(remaining)


def _cookie_secure(request: Request, settings: Settings) -> bool:
    """Whether to mark the session cookie `Secure`."""
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
    """Email a one-time sign-in code, if the address is eligible."""
    # Started FIRST, before any branch-dependent work, so the floor covers the
    # whole handler rather than whatever is left after the expensive part.
    started = time.monotonic()
    _require_otp_enabled(settings)
    mailer = OtpMailer(secret_store, settings)
    outcome = otp_service.request_code(db, payload.email, mailer=mailer, settings=settings)
    # Logged, never returned. The endpoint's whole job is to be uninformative;
    # the operator still needs to know which branch ran.
    log.info("otp_request_handled", outcome=outcome.reason)
    # Every branch that reaches here — sent, ineligible, throttled — is padded to the same floor
    # (#1137).
    _hold_until_floor(started, settings.auth_otp_request_min_seconds)
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
    """Verify the code, mint a session, and set the `dataq_session` cookie."""
    # Started before the eligibility-dependent work, same as `request_otp`.
    started = time.monotonic()
    _require_otp_enabled(settings)
    try:
        user = otp_service.verify_code(db, payload.email, payload.code, settings=settings)
    except otp_service.OtpVerifyError:
        # THE uniform response of this endpoint, and therefore the one that has to be floored
        # (#1141) — the mirror image of `request_otp`.
        _hold_until_floor(started, settings.auth_otp_verify_min_seconds)
        raise
    _, token = session_service.create_session(db, user, settings=settings)
    response.set_cookie(
        session_service.COOKIE_NAME,
        token,
        max_age=settings.auth_session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request, settings),
        # Path=/ explicitly: nginx passes Set-Cookie through unmodified (no `proxy_cookie_path`),
        # and the API lives under /api while the SPA's routes do not.
        path="/",
    )
    resp = MeResponse.model_validate(user)
    resp.role = resolve_role(user)
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
    """Revoke the presented session and clear the cookie. Idempotent."""
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
