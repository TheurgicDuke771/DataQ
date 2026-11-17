"""/me endpoint tests — focus on the `is_workspace_admin` flag the Admin nav
gates on. Auth is dev-bypass (conftest); WORKSPACE_ADMIN_EMAILS toggles whether
the caller is an admin. Skips without TEST_DATABASE_URL.
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


def test_me_flags_non_admin_for_an_ordinary_member(client: TestClient, as_role: Any) -> None:
    """A MEMBER, not the ambient dev-bypass identity — which since #741 IS the
    workspace admin (dev bypass is a single-operator mode), so it can no longer
    demonstrate the non-admin case.
    """
    get_settings.cache_clear()
    user, headers = as_role("member")
    body = client.get("/api/v1/me", headers=headers).json()
    assert body["email"] == user.email
    assert body["is_workspace_admin"] is False
    assert body["role"] == "member"


def test_me_flags_the_dev_bypass_identity_as_admin(client: TestClient) -> None:
    """Dev bypass resolves to a workspace admin (#741) — pinned, because the
    local and published-eval stacks depend on it: a `member` here could not
    create a connection, i.e. could not do the first thing DataQ is for.
    """
    get_settings.cache_clear()
    body = client.get("/api/v1/me").json()
    assert body["email"] == DEV_BYPASS_EMAIL
    assert body["is_workspace_admin"] is True
    assert body["role"] == "admin"


def test_me_flags_admin_when_allowlisted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", f"someone@else.io,{DEV_BYPASS_EMAIL}")
    get_settings.cache_clear()
    body = client.get("/api/v1/me").json()
    assert body["is_workspace_admin"] is True


def test_me_serialises_a_null_aad_object_id(client: TestClient, db_session: Any) -> None:
    """`/me` must return 200 with `aad_object_id: null` for a non-AAD identity."""
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
    endpoint does (`session_service.create_session`).
    """
    _, token = session_service.create_session(db_session, user)
    return {"Cookie": f"{session_service.COOKIE_NAME}={token}"}


def test_patch_me_updates_display_name_and_returns_the_refreshed_profile(
    client: TestClient,
) -> None:
    resp = client.patch("/api/v1/me", json={"display_name": "  Olivia Rivera  "})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Stored/returned trimmed — the issue asks for "non-empty after strip".
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
    """Mirrors `test_bad_pat_401s_instead_of_falling_through_to_bypass` (GET) — the dev-bypass
    fallback only ever applies to a request with NO credential.
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
    row, so a caller can only ever rename themselves.
    """
    victim = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="v@x.io")
    db_session.add(victim)
    db_session.commit()

    client.patch("/api/v1/me", json={"display_name": "Attacker Was Here"})

    db_session.refresh(victim)
    assert victim.display_name is None


# ── GET /me/access-summary (#2537) ───────────────────────────────────────────


def _suite_for(db: Any, creator: User, name: str) -> Any:
    from backend.app.db.models import Connection, Suite

    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=creator.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name=name, connection_id=conn.id, created_by=creator.id)
    db.add(suite)
    db.flush()
    return suite


def _from_the_suite_list(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    """What the Profile page used to compute: the same figures from every suite's payload."""
    suites = client.get("/api/v1/suites", headers=headers).json()
    counts = dict.fromkeys(("owner", "admin", "edit", "view"), 0)
    for s in suites:
        if s["my_permission"]:
            counts[s["my_permission"]] += 1
    owned = sorted(
        ({"id": s["id"], "name": s["name"]} for s in suites if s["my_permission"] == "owner"),
        key=lambda s: s["name"],
    )
    return {"total": len(suites), "counts": counts, "owned": owned}


def _summary(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    resp = client.get("/api/v1/me/access-summary", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {
        "total": body["total_visible"],
        "counts": body["counts_by_level"],
        "owned": [{"id": o["id"], "name": o["name"]} for o in body["owned"]],
    }


def _world(db_session: Any, as_role: Any) -> dict[str, tuple[User, dict[str, str]]]:
    """Two owners and three kinds of caller over suites owned by each, shared at every level."""
    from backend.app.db.models import Share

    other, _ = as_role("member")
    member, member_headers = as_role("member")
    admin, admin_headers = as_role("admin")
    viewer, viewer_headers = as_role("viewer")
    mine_b = _suite_for(db_session, member, "b mine")
    _suite_for(db_session, member, "a mine")
    theirs = [_suite_for(db_session, other, f"theirs {i}") for i in range(5)]
    _suite_for(db_session, admin, "admins own")
    for suite, who, level in (
        (theirs[0], member, "admin"),
        (theirs[1], member, "edit"),
        (theirs[2], member, "view"),
        (theirs[2], viewer, "edit"),  # a viewer's share is capped to view
        (theirs[3], viewer, "view"),
        (mine_b, viewer, "view"),
        (theirs[0], admin, "view"),  # an admin's share changes nothing
    ):
        db_session.add(Share(suite_id=suite.id, user_id=who.id, permission=level))
    db_session.commit()
    return {
        "member": (member, member_headers),
        "admin": (admin, admin_headers),
        "viewer": (viewer, viewer_headers),
    }


@pytest.mark.parametrize("who", ["member", "admin", "viewer"])
def test_the_access_summary_equals_what_the_suite_list_says(
    client: TestClient, db_session: Any, as_role: Any, who: str
) -> None:
    get_settings.cache_clear()
    _, headers = _world(db_session, as_role)[who]

    summary = _summary(client, headers)

    assert summary == _from_the_suite_list(client, headers)
    assert summary["total"] > 0


def test_the_access_summary_figures_for_each_role(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    get_settings.cache_clear()
    world = _world(db_session, as_role)

    member = _summary(client, world["member"][1])
    assert member["counts"] == {"owner": 2, "admin": 1, "edit": 1, "view": 1}
    assert [o["name"] for o in member["owned"]] == ["a mine", "b mine"]

    admin = _summary(client, world["admin"][1])
    assert admin["counts"]["owner"] == 1 and admin["counts"]["edit"] == admin["counts"]["view"] == 0
    assert admin["total"] == 1 + admin["counts"]["admin"]

    viewer = _summary(client, world["viewer"][1])
    assert viewer["counts"] == {"owner": 0, "admin": 0, "edit": 0, "view": 3}
    assert viewer["owned"] == []


def test_a_suite_owned_and_shared_to_its_owner_counts_once(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    from backend.app.db.models import Share

    get_settings.cache_clear()
    member, headers = as_role("member")
    suite = _suite_for(db_session, member, "mine")
    db_session.add(Share(suite_id=suite.id, user_id=member.id, permission="view"))
    db_session.commit()

    summary = _summary(client, headers)

    assert summary["counts"] == {"owner": 1, "admin": 0, "edit": 0, "view": 0}
    assert summary == _from_the_suite_list(client, headers)


def test_the_access_summary_of_a_user_with_no_suites_is_empty(
    client: TestClient, as_role: Any
) -> None:
    get_settings.cache_clear()
    _, headers = as_role("member")

    assert _summary(client, headers) == {
        "total": 0,
        "counts": {"owner": 0, "admin": 0, "edit": 0, "view": 0},
        "owned": [],
    }


def test_a_share_on_a_suite_whose_creator_was_deleted_still_counts(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    """`suites.created_by` is NULL once its creator is deleted; the suite list still shows it."""
    from backend.app.db.models import Share

    get_settings.cache_clear()
    creator, _ = as_role("member")
    member, headers = as_role("member")
    suite = _suite_for(db_session, creator, "orphaned")
    db_session.add(Share(suite_id=suite.id, user_id=member.id, permission="view"))
    suite.created_by = None
    db_session.commit()

    summary = _summary(client, headers)

    assert summary["counts"]["view"] == 1 and summary["total"] == 1
    assert summary == _from_the_suite_list(client, headers)
