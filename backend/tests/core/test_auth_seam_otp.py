"""The three-authenticator seam order — PAT → session cookie → Azure JWT (#734).

ADR 0032 decision 1 extends ADR 0026's seam with a browser credential. The
properties that matter, and that these tests pin:

* the order is fixed, and the branches are **disjoint** — a request decided by one
  authenticator never falls through to another on failure (the #849 lesson made
  structural: a credential of type A must never reach a validator for type B);
* an expired or revoked session is a **uniform 401 on the next request**, checked
  at the seam rather than merely stored;
* the cookie short-circuit is gated on OTP actually being enabled, so it cannot
  become an auth-bypass vector on an Azure-only deployment.

The TestClient-level half runs against a purpose-built app mounting the real
resolver, because the mode is bound at import time in `core.auth` and reloading it
would not rebind the `get_current_user` name every router already captured. The
resolver, the cookie parsing, the DataQError handler and the 401 envelope are all
the production ones.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, cast

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fastapi_azure_auth.user import User as AzureUser
from starlette.requests import Request

import backend.app.core.auth as auth_mod
from backend.app.core.errors import DataQError, register_exception_handlers
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import api_key_service, session_service


def _user(db: Any) -> User:
    user = User(
        id=uuid.uuid4(),
        aad_object_id=uuid.uuid4().hex,
        email=f"{uuid.uuid4().hex[:10]}@seam.io",
    )
    db.add(user)
    db.commit()
    return user


def _request(*, authorization: str | None = None, cookie: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    if cookie is not None:
        headers.append((b"cookie", f"{session_service.COOKIE_NAME}={cookie}".encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _azure_user(claims: dict[str, Any]) -> AzureUser:
    return cast(AzureUser, SimpleNamespace(claims=claims))


# ── cookie extraction ────────────────────────────────────────────────────────


def test_only_a_correctly_prefixed_cookie_is_treated_as_a_session() -> None:
    """Prefix-checked like the PAT branch, so a cookie set by something else on
    the same origin cannot steer a request into the session branch."""
    assert auth_mod._session_token(_request()) is None
    assert auth_mod._session_token(_request(cookie="")) is None
    assert auth_mod._session_token(_request(cookie="some-other-value")) is None
    assert auth_mod._session_token(_request(cookie="dq_live_a-pat-in-a-cookie")) is None
    token = session_service.TOKEN_PREFIX + "abc"
    assert auth_mod._session_token(_request(cookie=token)) == token


# ── OTP-only mode: PAT → cookie → 401 ────────────────────────────────────────


def test_otp_only_resolves_a_valid_session_cookie(db_session: Any) -> None:
    user = _user(db_session)
    _, token = session_service.create_session(db_session, user)
    assert auth_mod._get_current_user_otp(_request(cookie=token), db_session).id == user.id


def test_otp_only_prefers_a_PAT_over_the_cookie(db_session: Any) -> None:
    """Seam order, asserted with BOTH credentials present and pointing at
    DIFFERENT users — the only arrangement that can tell precedence from
    coincidence."""
    pat_owner, cookie_owner = _user(db_session), _user(db_session)
    _, pat = api_key_service.create_key(db_session, pat_owner, name="seam")
    _, cookie = session_service.create_session(db_session, cookie_owner)

    resolved = auth_mod._get_current_user_otp(
        _request(authorization=f"Bearer {pat}", cookie=cookie), db_session
    )
    assert resolved.id == pat_owner.id


def test_otp_only_401s_with_no_credential_at_all(db_session: Any) -> None:
    with pytest.raises(DataQError) as caught:
        auth_mod._get_current_user_otp(_request(), db_session)
    assert caught.value.status_code == 401
    assert caught.value.code == "unauthenticated"


def test_a_bad_cookie_never_falls_through_to_the_bypass_identity(db_session: Any) -> None:
    """Disjointness: a request that presented a session cookie is decided by it."""
    with pytest.raises(DataQError) as caught:
        auth_mod._get_current_user_otp(
            _request(cookie=session_service.TOKEN_PREFIX + "forged"), db_session
        )
    assert caught.value.status_code == 401
    assert caught.value.code == "invalid_session"


# ── real + OTP: PAT → cookie → JWT ───────────────────────────────────────────


def test_the_cookie_wins_over_a_valid_azure_token(db_session: Any) -> None:
    user = _user(db_session)
    _, cookie = session_service.create_session(db_session, user)
    resolved = auth_mod._get_current_user_real_or_otp(
        _request(cookie=cookie),
        _azure_user({"oid": uuid.uuid4().hex, "upn": "someone.else@corp.io"}),
        db_session,
    )
    assert resolved.id == user.id


def test_a_BAD_cookie_does_not_fall_through_to_the_azure_branch(db_session: Any) -> None:
    """The PAT branch's contract, applied to the cookie: presenting a credential
    and having it rejected is a 401, not an invitation to try the next
    authenticator. Falling through would mean a stale cookie silently changed
    WHICH identity a request authenticated as."""
    with pytest.raises(DataQError) as caught:
        auth_mod._get_current_user_real_or_otp(
            _request(cookie=session_service.TOKEN_PREFIX + "stale"),
            _azure_user({"oid": uuid.uuid4().hex, "upn": "real@corp.io"}),
            db_session,
        )
    assert caught.value.code == "invalid_session"


def test_azure_still_works_when_no_cookie_is_presented(db_session: Any) -> None:
    oid = uuid.uuid4().hex
    resolved = auth_mod._get_current_user_real_or_otp(
        _request(), _azure_user({"oid": oid, "upn": f"aad-{oid[:8]}@corp.io"}), db_session
    )
    assert resolved.aad_object_id == oid


def test_a_PAT_still_beats_everything(db_session: Any) -> None:
    owner = _user(db_session)
    _, pat = api_key_service.create_key(db_session, owner, name="seam")
    _, cookie = session_service.create_session(db_session, _user(db_session))
    resolved = auth_mod._get_current_user_real_or_otp(
        _request(authorization=f"Bearer {pat}", cookie=cookie),
        _azure_user({"oid": uuid.uuid4().hex, "upn": "third@corp.io"}),
        db_session,
    )
    assert resolved.id == owner.id


# ── the scheme wrapper's cookie short-circuit ────────────────────────────────


@pytest.mark.asyncio
async def test_the_azure_scheme_short_circuits_on_a_cookie_ONLY_when_otp_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Security(azure_scheme)` resolves BEFORE the resolver body, so "the cookie is
    checked before the JWT branch" has to be enforced inside the scheme.

    And it must be gated: ungated, any client could switch off JWT validation on an
    Azure-only deployment by attaching a junk `dataq_session` cookie — a cosmetic
    short-circuit turned into an auth-bypass vector.
    """
    from fastapi.security import SecurityScopes
    from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer

    seen: list[str] = []

    async def _spy(self: Any, request: Any, security_scopes: Any) -> None:
        seen.append("validator entered")
        return None

    monkeypatch.setattr(SingleTenantAzureAuthorizationCodeBearer, "__call__", _spy)
    scheme = auth_mod._PatAwareAzureScheme.__new__(auth_mod._PatAwareAzureScheme)

    class _Req:
        def __init__(self, cookie: str) -> None:
            self.headers: dict[str, str] = {}
            self.cookies = {session_service.COOKIE_NAME: cookie}

    cookie_request = _Req(session_service.TOKEN_PREFIX + "whatever")

    monkeypatch.setattr(auth_mod, "_otp_enabled", True)
    assert await scheme(cookie_request, SecurityScopes()) is None  # type: ignore[arg-type]
    assert seen == [], "the JWT validator ran even though a session cookie decides this request"

    monkeypatch.setattr(auth_mod, "_otp_enabled", False)
    await scheme(cookie_request, SecurityScopes())  # type: ignore[arg-type]
    assert seen == [
        "validator entered"
    ], "a junk cookie disabled JWT validation on an OTP-less deployment"


# ── dev bypass interplay ─────────────────────────────────────────────────────


def test_dev_bypass_resolves_a_VALID_session_cookie(db_session: Any) -> None:
    user = _user(db_session)
    _, token = session_service.create_session(db_session, user)
    assert auth_mod._get_current_user_dev_bypass(_request(cookie=token), db_session).id == user.id


def test_dev_bypass_ignores_an_unusable_cookie(db_session: Any) -> None:
    """A DELIBERATE asymmetry with the PAT branch (which 401s).

    Dev bypass is not an authenticator — its entire contract is "no credential is
    required" — so refusing a request over a credential nobody had to present is
    friction with no security value: the next request without the cookie is
    admitted anyway. The concrete case is a developer who ran an OTP-configured
    stack, kept the cookie, and switched the env back.
    """
    resolved = auth_mod._get_current_user_dev_bypass(
        _request(cookie=session_service.TOKEN_PREFIX + "leftover-from-another-stack"), db_session
    )
    assert resolved.email == auth_mod.DEV_BYPASS_EMAIL


def test_dev_bypass_still_401s_a_bad_PAT(db_session: Any) -> None:
    """The asymmetry is scoped to cookies — the #461 contract is untouched."""
    with pytest.raises(DataQError):
        auth_mod._get_current_user_dev_bypass(
            _request(authorization=f"Bearer {api_key_service.TOKEN_PREFIX}bogus"), db_session
        )


# ── TestClient level: expiry + revocation on the NEXT request ────────────────


@pytest.fixture
def otp_client(db_session: Any) -> Iterator[TestClient]:
    """An app whose one route depends on the REAL OTP resolver."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe")
    def probe(
        current_user: Annotated[User, Depends(auth_mod._get_current_user_otp)],
    ) -> dict[str, str]:
        return {"id": str(current_user.id), "email": current_user.email}

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as client:
        yield client


def test_a_live_session_authenticates_over_http(otp_client: TestClient, db_session: Any) -> None:
    user = _user(db_session)
    _, token = session_service.create_session(db_session, user)
    otp_client.cookies.set(session_service.COOKIE_NAME, token)

    response = otp_client.get("/probe")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(user.id)


def test_an_EXPIRED_session_is_a_uniform_401_on_the_next_request(
    otp_client: TestClient, db_session: Any
) -> None:
    """ADR 0032's testable obligation, end to end."""
    user = _user(db_session)
    row, token = session_service.create_session(db_session, user)
    otp_client.cookies.set(session_service.COOKIE_NAME, token)
    before = otp_client.get("/probe")
    assert before.status_code == 200

    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    response = otp_client.get("/probe")
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "invalid_session"


def test_a_REVOKED_session_is_a_uniform_401_on_the_next_request(
    otp_client: TestClient, db_session: Any
) -> None:
    user = _user(db_session)
    _, token = session_service.create_session(db_session, user)
    otp_client.cookies.set(session_service.COOKIE_NAME, token)
    before = otp_client.get("/probe")
    assert before.status_code == 200

    session_service.revoke(db_session, token)

    response = otp_client.get("/probe")
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "invalid_session"


def test_the_401_bodies_are_byte_identical_for_expired_and_revoked(
    otp_client: TestClient, db_session: Any
) -> None:
    """One message for every failure mode — telling them apart would confirm to a
    probing caller that the session was once real, or that somebody logged out."""
    expired_user = _user(db_session)
    expired_row, expired = session_service.create_session(db_session, expired_user)
    expired_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    revoked_user = _user(db_session)
    _, revoked = session_service.create_session(db_session, revoked_user)
    session_service.revoke(db_session, revoked)

    bodies = []
    for token in (expired, revoked, session_service.TOKEN_PREFIX + "never-existed"):
        otp_client.cookies.set(session_service.COOKIE_NAME, token)
        response = otp_client.get("/probe")
        assert response.status_code == 401
        bodies.append(response.content)
    assert bodies[0] == bodies[1] == bodies[2]


# ── CSRF invariant ───────────────────────────────────────────────────────────


def _api_routes() -> list[tuple[str, set[str]]]:
    """Every (path, methods) pair the app serves, flattened.

    FastAPI does not keep included routers' routes in `app.routes` — it stores a
    `_IncludedRouter` wrapper holding the original router plus the prefix it was
    mounted under. A naive flat scan therefore finds four routes and every "no
    offenders" assertion below passes trivially. It did, on the first draft; the
    `test_the_route_scan_actually_sees_the_api` guard below exists because of it.

    Walked from the router objects rather than read out of `app.openapi()` so a
    route marked `include_in_schema=False` — invisible in the schema, perfectly
    reachable over HTTP — cannot slip past the CSRF audit.
    """
    from backend.app.main import app

    found: list[tuple[str, set[str]]] = []

    def walk(routes: Any, prefix: str) -> None:
        for route in routes:
            methods = set(getattr(route, "methods", None) or set())
            if methods:
                found.append((prefix + str(getattr(route, "path", "")), methods))
            context = getattr(route, "include_context", None)
            included = getattr(route, "original_router", None)
            if included is not None:
                walk(included.routes, prefix + str(getattr(context, "prefix", "") or ""))

    walk(app.routes, "")
    return found


def test_the_route_scan_actually_sees_the_api() -> None:
    """Guards the two audits below from becoming vacuous.

    FastAPI wraps included routers, so a naive `app.routes` walk finds four routes
    and every "no offenders" assertion passes trivially.
    """
    paths = {path for path, _ in _api_routes()}
    assert len([p for p in paths if p.startswith("/api/v1/")]) > 50
    assert "/api/v1/me" in paths
    assert "/api/v1/auth/otp/request" in paths


def test_no_GET_route_in_the_api_mutates_state() -> None:
    """`SameSite=Lax` blocks cross-site POSTs but NOT cross-site GETs — so the
    cookie is only safe while every mutation is a POST/PATCH/PUT/DELETE.

    Audited over the whole route table rather than by inspection, because the
    invariant is broken by ADDING a route, not by editing an existing one — and a
    review checklist does not run in CI.
    """
    mutating_verbs = {"POST", "PUT", "PATCH", "DELETE"}
    offenders = [
        (path, sorted(methods))
        for path, methods in _api_routes()
        # A route answering GET *and* a mutating verb shares one handler, so the
        # GET can reach the mutation.
        if path.startswith("/api/") and "GET" in methods and methods & mutating_verbs
    ]
    assert (
        offenders == []
    ), f"GET-reachable mutating routes break the SameSite=Lax CSRF stance: {offenders}"


def test_the_logout_route_is_POST_only() -> None:
    """Login-CSRF's other half: a cross-site <img src> must not be able to sign a
    user out, and a GET logout is exactly that."""
    logout = [methods for path, methods in _api_routes() if path == "/api/v1/auth/logout"]
    assert logout, "the logout route is not mounted"
    assert logout[0] == {"POST"}
