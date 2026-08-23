"""User-directory search endpoint tests against a real Postgres via TestClient."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, email: str, display_name: str | None = None) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email, display_name=display_name)
    db_session.add(u)
    db_session.flush()
    return u


def test_matches_email_substring(client: TestClient, db_session: Any) -> None:
    _user(db_session, "alice@acme.io")
    _user(db_session, "bob@acme.io")
    _user(db_session, "carol@other.io")
    db_session.commit()
    resp = client.get("/api/v1/users/search", params={"q": "acme"})
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert emails == {"alice@acme.io", "bob@acme.io"}


def test_matches_display_name_case_insensitively(client: TestClient, db_session: Any) -> None:
    target = _user(db_session, "x@ex", display_name="Deepak Sharma")
    _user(db_session, "y@ex", display_name="Other Person")
    db_session.commit()
    resp = client.get("/api/v1/users/search", params={"q": "deepak"})
    assert resp.status_code == 200
    assert [u["id"] for u in resp.json()] == [str(target.id)]


def test_short_query_returns_empty(client: TestClient, db_session: Any) -> None:
    _user(db_session, "alice@acme.io")
    db_session.commit()
    resp = client.get("/api/v1/users/search", params={"q": "a"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_blank_query_returns_empty(client: TestClient, db_session: Any) -> None:
    _user(db_session, "alice@acme.io")
    db_session.commit()
    resp = client.get("/api/v1/users/search")
    assert resp.status_code == 200
    assert resp.json() == []


def test_summary_omits_sensitive_fields(client: TestClient, db_session: Any) -> None:
    """An EXHAUSTIVE allowlist, deliberately — this directory is readable by every
    authenticated user, so a field must never arrive here by accident. Equality
    (not a subset check) is what makes a new column a failing test rather than a
    silent widening.
    """
    _user(db_session, "alice@acme.io", display_name="Alice")
    db_session.commit()
    resp = client.get("/api/v1/users/search", params={"q": "alice"})
    [row] = resp.json()
    assert set(row) == {"id", "email", "display_name", "role"}
    assert "aad_object_id" not in row


def test_summary_reports_the_effective_role(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    """The EFFECTIVE role, not the stored column — because that is what
    `share_service` checks when it rejects an `edit` grant. A break-glass
    allowlist admin whose row still reads `member` can hold `edit`, and a picker
    reading the stored value would wrongly refuse them.
    """
    from backend.app.core.config import get_settings

    user = _user(db_session, "ada@acme.io", display_name="Ada")
    user.role = "member"
    db_session.commit()
    monkeypatch.setenv("WORKSPACE_ADMIN_EMAILS", "ada@acme.io")
    get_settings.cache_clear()

    [row] = client.get("/api/v1/users/search", params={"q": "ada"}).json()
    assert row["role"] == "admin"


def test_limit_is_capped(client: TestClient) -> None:
    resp = client.get("/api/v1/users/search", params={"q": "acme", "limit": 999})
    # Over-cap limit is rejected by the query-param bound (le=MAX_LIMIT).
    assert resp.status_code == 422


def test_limit_caps_result_count(client: TestClient, db_session: Any) -> None:
    for i in range(5):
        _user(db_session, f"user{i}@acme.io")
    db_session.commit()
    resp = client.get("/api/v1/users/search", params={"q": "acme", "limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_like_wildcards_are_literal(client: TestClient, db_session: Any) -> None:
    # A bare "%" must not match every user — wildcards in the query are escaped.
    _user(db_session, "alice@acme.io")
    _user(db_session, "bob@acme.io")
    db_session.commit()
    resp = client.get("/api/v1/users/search", params={"q": "a%c"})
    assert resp.status_code == 200
    assert resp.json() == []
