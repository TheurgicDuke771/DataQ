"""Workspace-admin endpoint tests against a real Postgres via TestClient.

Auth runs in dev-bypass mode (conftest), so the caller is the fixed dev user.
`WORKSPACE_ADMIN_EMAILS` is toggled per test to flip that user between admin and
non-admin. The key property under test: an admin sees suites/users they neither
own nor are shared on — the /admin endpoints bypass the owned-or-shared scoping
`list_suites` applies. Skips without TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import DEV_BYPASS_EMAIL
from backend.app.core.config import get_settings
from backend.app.db.models import Check, Connection, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _grant_admin(monkeypatch: pytest.MonkeyPatch, *emails: str) -> None:
    """Make the given emails (default: the dev-bypass caller) workspace admins."""
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", ",".join(emails or (DEV_BYPASS_EMAIL,)))
    get_settings.cache_clear()


def _user(db_session: Any, email: str, display_name: str | None = None) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email, display_name=display_name)
    db_session.add(u)
    db_session.flush()
    return u


def _connection(db_session: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _suite(db_session: Any, owner: User, conn: Connection, name: str) -> Suite:
    suite = Suite(name=name, connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    return suite


# ── authz gate ────────────────────────────────────────────────────────────────


def test_non_admin_gets_403(client: TestClient) -> None:
    # No WORKSPACE_ADMIN_EMAILS configured → the caller is not an admin.
    get_settings.cache_clear()
    for path in ("/api/v1/admin/suites", "/api/v1/admin/users", "/api/v1/admin/access"):
        resp = client.get(path)
        assert resp.status_code == 403, path


def test_admin_email_match_is_case_insensitive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _grant_admin(monkeypatch, DEV_BYPASS_EMAIL.upper())
    assert client.get("/api/v1/admin/suites").status_code == 200


# ── all suites ────────────────────────────────────────────────────────────────


def test_admin_lists_suites_it_does_not_own(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A suite owned by someone else, with two checks and one share — the admin
    # neither owns nor is shared on it, yet must see it (scoping is bypassed).
    other = _user(db_session, "owner@x.io", "Olive Owner")
    conn = _connection(db_session, other)
    suite = _suite(db_session, other, conn, "Finance DQ")
    db_session.add_all(
        [
            Check(
                suite_id=suite.id,
                name="c1",
                expectation_type="expect_column_values_to_not_be_null",
                config={"column": "id"},
            ),
            Check(
                suite_id=suite.id,
                name="c2",
                expectation_type="expect_column_values_to_not_be_null",
                config={"column": "amt"},
            ),
        ]
    )
    viewer = _user(db_session, "viewer@x.io")
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()

    _grant_admin(monkeypatch)
    resp = client.get("/api/v1/admin/suites")
    assert resp.status_code == 200
    [row] = [r for r in resp.json() if r["id"] == str(suite.id)]
    assert row["name"] == "Finance DQ"
    assert row["owner_email"] == "owner@x.io"
    assert row["owner_name"] == "Olive Owner"
    assert row["connection_type"] == "snowflake"
    assert row["env"] == "dev"
    assert row["check_count"] == 2
    assert row["share_count"] == 1


# ── all users ─────────────────────────────────────────────────────────────────


def test_admin_lists_users_with_counts(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "alice@x.io", "Alice")
    conn = _connection(db_session, owner)
    _suite(db_session, owner, conn, "S1")
    _suite(db_session, owner, conn, "S2")
    bob = _user(db_session, "bob@x.io")
    s3 = _suite(db_session, owner, conn, "S3")
    db_session.add(Share(suite_id=s3.id, user_id=bob.id, permission="edit"))
    db_session.commit()

    _grant_admin(monkeypatch)
    rows = {r["email"]: r for r in client.get("/api/v1/admin/users").json()}
    assert rows["alice@x.io"]["owned_suite_count"] == 3
    assert rows["alice@x.io"]["shared_suite_count"] == 0
    assert rows["bob@x.io"]["owned_suite_count"] == 0
    assert rows["bob@x.io"]["shared_suite_count"] == 1


# ── access overview ───────────────────────────────────────────────────────────


def test_admin_access_overview_lists_owner_and_shares(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _user(db_session, "owner@x.io")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn, "Shared Suite")
    editor = _user(db_session, "editor@x.io")
    db_session.add(Share(suite_id=suite.id, user_id=editor.id, permission="edit"))
    db_session.commit()

    _grant_admin(monkeypatch)
    rows = [r for r in client.get("/api/v1/admin/access").json() if r["suite_id"] == str(suite.id)]
    grants = {(r["user_email"], r["permission"]) for r in rows}
    assert ("owner@x.io", "owner") in grants
    assert ("editor@x.io", "edit") in grants
