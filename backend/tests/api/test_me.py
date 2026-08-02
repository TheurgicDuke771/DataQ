"""/me endpoint tests — focus on the `is_workspace_admin` flag the Admin nav
gates on. Auth is dev-bypass (conftest); WORKSPACE_ADMIN_EMAILS toggles whether
the caller is an admin. Skips without TEST_DATABASE_URL.

The `PATCH /me` tests (#1139) live in this file rather than a new one — same
fixture, same auth-mode caveats as the GET tests above.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import DEV_BYPASS_EMAIL
from backend.app.core.config import get_settings
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import api_key_service, session_service


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_me_flags_non_admin_by_default(client: TestClient) -> None:
    get_settings.cache_clear()
    body = client.get("/api/v1/me").json()
    assert body["email"] == DEV_BYPASS_EMAIL
    assert body["is_workspace_admin"] is False


def test_me_flags_admin_when_allowlisted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", f"someone@else.io,{DEV_BYPASS_EMAIL}")
    get_settings.cache_clear()
    body = client.get("/api/v1/me").json()
    assert body["is_workspace_admin"] is True


def test_me_serialises_a_null_aad_object_id(client: TestClient, db_session: Any) -> None:
    """`/me` must return 200 with `aad_object_id: null` for a non-AAD identity.

    `MeResponse.aad_object_id` was non-optional and the handler uses
    `model_validate` under a `response_model`, so the moment #734 provisions an
    email-OTP user (NULL aad — ADR 0032 decision 6) every `/me` call by that user
    would have been a response-validation 500. The PAT branch of the seam is what
    lets a test reach `/me` as a user other than the dev-bypass identity.
    """
    owner = User(
        id=uuid.uuid4(), aad_object_id=None, email=f"otp-{uuid.uuid4().hex[:8]}@example.com"
    )
    db_session.add(owner)
    db_session.commit()
    _, token = api_key_service.create_key(db_session, owner, name="otp-me")

    resp = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == owner.email
    assert body["aad_object_id"] is None


# ── PATCH /me — self-service profile update (#1139) ──────────────────────────


def _cookie_header(db_session: Any, user: User) -> dict[str, str]:
    """A session cookie header for `user`, minted the same way the OTP verify
    endpoint does (`session_service.create_session`)."""
    _, token = session_service.create_session(db_session, user)
    return {"Cookie": f"{session_service.COOKIE_NAME}={token}"}


def test_patch_me_updates_display_name_and_returns_the_refreshed_profile(
    client: TestClient,
) -> None:
    resp = client.patch("/api/v1/me", json={"display_name": "  Olivia Rivera  "})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Stored/returned trimmed — the issue asks for "non-empty after strip", and
    # leaving surrounding whitespace in a name that renders in shares/admin
    # lists would be a silent cosmetic bug of its own.
    assert body["display_name"] == "Olivia Rivera"
    assert body["email"] == DEV_BYPASS_EMAIL

    # And it actually persisted — not just echoed back.
    follow_up = client.get("/api/v1/me")
    assert follow_up.json()["display_name"] == "Olivia Rivera"


@pytest.mark.parametrize(
    "payload",
    [
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "x" * 257},
    ],
    ids=["empty", "whitespace_only", "too_long"],
)
def test_patch_me_rejects_invalid_display_names(
    client: TestClient, payload: dict[str, str]
) -> None:
    resp = client.patch("/api/v1/me", json=payload)
    assert resp.status_code == 422, resp.text


def test_patch_me_accepts_exactly_the_column_limit(client: TestClient) -> None:
    name = "x" * 256
    resp = client.patch("/api/v1/me", json={"display_name": name})
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == name


def test_patch_me_works_via_pat(client: TestClient, db_session: Any) -> None:
    owner = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="pat@x.io")
    db_session.add(owner)
    db_session.commit()
    _, token = api_key_service.create_key(db_session, owner, name="patch-me")

    resp = client.patch(
        "/api/v1/me",
        json={"display_name": "PAT User"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "PAT User"
    assert resp.json()["email"] == "pat@x.io"

    db_session.refresh(owner)
    assert owner.display_name == "PAT User"


def test_patch_me_works_via_session_cookie(client: TestClient, db_session: Any) -> None:
    # A NULL-`aad_object_id` row — the shape an OTP JIT-provisioned user has
    # (ADR 0032 decision 6) — is exactly who this endpoint is for.
    owner = User(
        id=uuid.uuid4(), aad_object_id=None, email=f"otp-{uuid.uuid4().hex[:8]}@example.com"
    )
    db_session.add(owner)
    db_session.commit()

    resp = client.patch(
        "/api/v1/me",
        json={"display_name": "Cookie User"},
        headers=_cookie_header(db_session, owner),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Cookie User"
    assert resp.json()["email"] == owner.email

    db_session.refresh(owner)
    assert owner.display_name == "Cookie User"


def test_patch_me_401s_with_bad_pat_instead_of_falling_through_to_bypass(
    client: TestClient,
) -> None:
    """Mirrors `test_bad_pat_401s_instead_of_falling_through_to_bypass` (GET) —
    the dev-bypass fallback only ever applies to a request with NO credential.
    A *presented* bad credential is a hostile/expired caller, and this is the
    only "no principal" case the dev-bypass test harness can exercise honestly:
    an unauthenticated PATCH under a real (non-bypass) deployment 401s the same
    way, through the same `get_current_user` seam this endpoint depends on with
    no mode-specific branching.
    """
    resp = client.patch(
        "/api/v1/me",
        json={"display_name": "Nope"},
        headers={"Authorization": f"Bearer {api_key_service.TOKEN_PREFIX}bogus"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


def test_patch_me_with_bad_session_cookie_falls_through_to_bypass_not_401(
    client: TestClient,
) -> None:
    """A bad *cookie* is the one credential dev-bypass deliberately tolerates
    (`_get_current_user_dev_bypass`, `core/auth.py`) — unlike a bad PAT, which is
    an explicit act and 401s. This just pins that PATCH inherits the exact same
    fallback the GET handler already has, rather than special-casing auth.
    """
    resp = client.patch(
        "/api/v1/me",
        json={"display_name": "Bypass User Renamed"},
        headers={"Cookie": f"{session_service.COOKIE_NAME}={session_service.TOKEN_PREFIX}bogus"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == DEV_BYPASS_EMAIL
    assert resp.json()["display_name"] == "Bypass User Renamed"


def test_patch_me_cannot_touch_another_users_row(client: TestClient, db_session: Any) -> None:
    """No `user_id` in the body — there is nothing to point at someone else's
    row, so a caller can only ever rename themselves."""
    victim = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="v@x.io")
    db_session.add(victim)
    db_session.commit()

    client.patch("/api/v1/me", json={"display_name": "Attacker Was Here"})

    db_session.refresh(victim)
    assert victim.display_name is None
