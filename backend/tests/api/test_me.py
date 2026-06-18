"""/me endpoint tests — focus on the `is_workspace_admin` flag the Admin nav
gates on. Auth is dev-bypass (conftest); WORKSPACE_ADMIN_EMAILS toggles whether
the caller is an admin. Skips without TEST_DATABASE_URL.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import DEV_BYPASS_EMAIL
from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.main import app


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
